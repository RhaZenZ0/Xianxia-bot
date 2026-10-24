package game

// Erasing one person from the world.
//
// A player can ask for their data back out of this bot, and until now the only
// honest answer was "the operator will go through the database by hand". That
// is not an answer at schema 49: a Discord id appears in 104 tables under 28
// different column names, and a hand-written list of them is a list that goes
// stale the first time somebody adds a table.
//
// So the list is not written down. `erasureTargets` asks the live schema which
// columns identify a person, which is the only way this can still be correct in
// a year. tests/python/contracts/test_privacy_erasure.py is the other half of
// that bargain - it lives in Python because the fixtures in this package build
// a schema by hand, and could only ever prove the fixture is classified. It
// bootstraps the real database and fails if a column that looks like a person is in
// neither the subject set below nor one of the two exception maps, so a new
// table carrying a Discord id cannot be added without someone deciding what
// erasure means for it.
//
// Three dispositions, because "delete the row" is wrong for some of them:
//
//   - **delete** - the row is the person's. Their character, their inventory,
//     their scene history, their RAG memories. Most of the 114 column pairs.
//   - **anonymise** - the row is shared world state that merely *names* a
//     person: the sect other cultivators belong to, the family that outlives
//     its founder, the history of a battle that happened. Deleting those would
//     take other players' world away to satisfy one person's request, so the
//     link goes and the row stays.
//   - **keep** - `admin_audit_log`, which is the record that this erasure was
//     performed at all.
//
// The FTS5 indexes need no special handling and deliberately get none: every
// mirror (`rag_memories_fts`, `world_history_fts`, the npc memory index) is an
// external-content table with `_ad`/`_au` triggers on its base table, so an
// ordinary DELETE or UPDATE re-syncs the index on the way through. Reaching
// into a shadow table by hand is how those indexes get corrupted.

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// erasedUserSentinel replaces a Discord id on an anonymised column that cannot
// hold NULL. Real ids are positive snowflakes, so zero is unambiguously "this
// person was erased" and no lookup will ever match it.
const erasedUserSentinel int64 = 0

// erasureSubjectColumns are the column names that hold a Discord user id. Taken
// from the live schema rather than invented: every one of these exists at
// schema 49, and the classification test fails if the schema grows another.
var erasureSubjectColumns = map[string]bool{
	"user_id": true, "actor_id": true, "admin_user_id": true,
	"bidder_user_id": true, "buyer_user_id": true, "challenger_user_id": true, "claimed_by_user_id": true,
	"current_bidder_user_id": true,
	"disciple_user_id":       true, "founded_by_user_id": true, "founder_user_id": true,
	"from_user_id": true, "guest_user_id": true, "invitee_user_id": true,
	"inviter_user_id": true, "leader_user_id": true, "master_user_id": true,
	"opened_by_user_id": true,
	"owner_user_id":     true, "parent_user_id": true, "player1_user_id": true,
	"player2_user_id": true, "related_user_id": true, "seller_user_id": true,
	"target_user_id": true, "to_user_id": true, "turn_user_id": true,
	"user_a": true, "user_b": true, "winner_user_id": true,
}

// erasureKeep is the one place a person's id survives an erasure, and it is the
// record of the erasure. Keyed "table.column"; the value is the reason, which
// the classification test prints when it fails.
var erasureKeep = map[string]string{
	"admin_audit_log.admin_user_id": "the audit trail, including the row written by this erasure - " +
		"the record that a request was honoured cannot be the thing the request deletes",
}

// erasureAnonymise is shared world state. The row belongs to everybody; only
// the link to one person is personal, so the link is what goes.
var erasureAnonymise = map[string]string{
	"world_history_events.related_user_id": "world canon - the battle happened, and other cultivators remember it",
	"quest_definitions.owner_user_id":      "authored content other players are mid-way through",
	"sect_manors.founded_by_user_id":       "a sect its remaining disciples still belong to",
	"player_families.founder_user_id":      "a family that outlives whoever founded it (NOT NULL, so it takes the sentinel)",
	"world_crossings.opened_by_user_id": "world canon - somebody tore the sky open over a named " +
		"place and the road is still standing there, used by other players and by the world's own " +
		"people. Only which cultivator did it is personal, so that is the part that goes; the gate " +
		"itself is keyed on `location_key`, which is why an erasure cannot unmake one",
	"stall_sales.buyer_user_id": "the seller's own ledger (v1.5.0) - the sale happened and the " +
		"seller was paid for it; only who bought is personal, so that is the part that goes, and the " +
		"row goes with the seller when the seller is erased",
	"auctions.current_bidder_user_id": "another seller's lot - the lot is the seller's, and the " +
		"schema's own ON DELETE says what becomes of the high bid when its bidder goes: the link " +
		"clears. It was classified a delete, which took the whole lot, and the item its seller had " +
		"put in escrow, with it. The bid itself is refunded first (resolveLeavingEscrowTx), so only " +
		"the link is left to clear",
	"npc_graves.claimed_by_user_id": "world canon - somebody was lost, died out there and was " +
		"eventually found, and the grave goes on saying so. Only which cultivator reached it first " +
		"is personal, so that is the part that goes; the grave stays emptied, because it was - " +
		"`claimed_game_minute` is what carries that, and nothing here touches it",
	// The three columns the schema itself clears for a leaving character (ON
	// DELETE SET NULL) and the sweep deleted anyway (v1.35.0): each is a shared
	// record whose row outlives the person - a war's field, a sect's building
	// work, a formation still standing at a place - and a column the schema
	// says to clear is shared state with a link in it, never a reason to
	// delete the row. The gate in test_privacy_erasure.py holds every SET NULL
	// column to this map.
	"territory_war_actions.user_id": "a war's record - the field was taken or held, and the war's " +
		"scorers, its allies and its history read the row; who struck the blow is the personal part",
	"sect_manor_projects.user_id": "a sect's building work, which the sect keeps; who laid the stone " +
		"is the personal part",
	"deployed_location_arrays.owner_user_id": "a formation still standing at a place and working on " +
		"everybody there; who set it is the personal part",
}

type erasureDisposition int

const (
	erasureDelete erasureDisposition = iota
	erasureAnonymiseRow
)

type erasureTarget struct {
	Table       string
	Column      string
	Disposition erasureDisposition
	NotNull     bool
}

func erasureKey(table, column string) string { return table + "." + column }

// erasureTargets asks the schema who holds a Discord id, and what to do about
// each. Virtual tables are skipped: the FTS mirrors carry no user column, and
// their shadow tables are maintained by triggers rather than by hand.
func erasureTargets(conn *storage.Conn) ([]erasureTarget, error) {
	res, err := conn.Execute(
		`SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'`, nil)
	if err != nil {
		return nil, err
	}
	var targets []erasureTarget
	for _, row := range res.Rows {
		if len(row) == 0 {
			continue
		}
		table := strings.TrimSpace(fmt.Sprint(row[0]))
		if table == "" {
			continue
		}
		if len(row) > 1 && row[1] != nil {
			if strings.HasPrefix(strings.ToUpper(strings.TrimSpace(fmt.Sprint(row[1]))), "CREATE VIRTUAL") {
				continue
			}
		}
		info, ierr := conn.Execute(fmt.Sprintf("PRAGMA table_info(%s)", quoteIdentifier(table)), nil)
		if ierr != nil {
			return nil, ierr
		}
		for _, col := range info.Rows {
			if len(col) < 4 {
				continue
			}
			column := strings.TrimSpace(fmt.Sprint(col[1]))
			if !erasureSubjectColumns[column] {
				continue
			}
			key := erasureKey(table, column)
			if _, kept := erasureKeep[key]; kept {
				continue
			}
			disposition := erasureDelete
			if _, anon := erasureAnonymise[key]; anon {
				disposition = erasureAnonymiseRow
			}
			targets = append(targets, erasureTarget{
				Table:       table,
				Column:      column,
				Disposition: disposition,
				// Read from the schema rather than configured beside the
				// anonymise map: a column that gains or loses NOT NULL in a
				// later migration must not make the erasure fail halfway.
				NotNull: storage.ParseInt(col[3]) == 1,
			})
		}
	}
	sort.Slice(targets, func(i, j int) bool {
		if targets[i].Table != targets[j].Table {
			return targets[i].Table < targets[j].Table
		}
		return targets[i].Column < targets[j].Column
	})
	return targets, nil
}

// quoteIdentifier makes a table name safe to interpolate. Every name here comes
// from sqlite_master rather than from a caller, so this is belt-and-braces
// against a table someone names awkwardly - not a trust boundary.
func quoteIdentifier(name string) string {
	return `"` + strings.ReplaceAll(name, `"`, `""`) + `"`
}

// erasureSweep is what one pass over the targets moved, reported per
// "table.column" so the caller's receipt can name it.
type erasureSweep struct {
	Deleted        map[string]any
	Anonymised     map[string]any
	RowsDeleted    int64
	RowsAnonymised int64
}

// erasedOwnerKey is what caravans.owner_key becomes for an erased owner: the
// column is TEXT NOT NULL and outside erasureSubjectColumns, so the schema walk
// never sees it, and the settlement (payCaravanOwner) and the collect already
// read an empty key as nobody's.
const erasedOwnerKey = ""

// eraseUnwalkedLinksTx anonymises the two places a person's id sits where no
// column walk can find it (v1.35.0): caravans.owner_key, a TEXT copy of the id
// on every caravan they ever sent (a death marks the travelling ones lost and
// keeps the key), and `requested_by` inside the JSON of the update_request and
// update_result rows, which only an administrator can have written. Each
// answers the rows it changed under the key the receipt reports it by.
func eraseUnwalkedLinksTx(conn *storage.Conn, userID int64, now float64) (map[string]int64, error) {
	out := map[string]int64{}
	if tableExistsTx(conn, "caravans") {
		r, err := conn.Execute(`UPDATE caravans SET owner_key=?,updated_at=? WHERE owner_type='player' AND owner_key=?`,
			[]any{erasedOwnerKey, now, fmt.Sprint(userID)})
		if err != nil {
			return nil, fmt.Errorf("erasing caravans.owner_key: %w", err)
		}
		if r.RowsAffected > 0 {
			out["caravans.owner_key"] = r.RowsAffected
		}
	}
	if tableExistsTx(conn, "world_state") {
		r, err := conn.Execute(`UPDATE world_state SET value_json=json_set(value_json,'$.requested_by',?),updated_at=? WHERE key IN (?,?) AND json_extract(value_json,'$.requested_by')=?`,
			[]any{erasedUserSentinel, now, updateRequestKey, updateResultKey, userID})
		if err != nil {
			return nil, fmt.Errorf("erasing world_state.requested_by: %w", err)
		}
		if r.RowsAffected > 0 {
			out["world_state.requested_by"] = r.RowsAffected
		}
	}
	return out, nil
}

// applyErasureTargets is the one statement of how a person's rows are removed.
// Both callers walk the same targets in the same order: `admin.player.erase`,
// which honours a data-protection request, and `character.reset`, which is a
// player abandoning a life they have only just begun (v1.0.1). They must not
// drift - the whole reason `erasureTargets` reads the live schema is that a
// hand-written list of 104 tables goes stale, and two hand-written loops over
// it would go stale against each other as well.
//
// `keep` is the one thing that varies: the reset keeps the engine's own record
// of a request, and its own record that it happened. It is an extra SQL
// predicate per "table.column", ANDed onto the DELETE. Erasure passes nil,
// because a data-protection request keeps nothing.
func applyErasureTargets(conn *storage.Conn, userID int64, targets []erasureTarget, keep map[string]string) (erasureSweep, error) {
	sweep := erasureSweep{Deleted: map[string]any{}, Anonymised: map[string]any{}}
	for _, target := range targets {
		key := erasureKey(target.Table, target.Column)
		var statement string
		if target.Disposition == erasureDelete {
			statement = fmt.Sprintf("DELETE FROM %s WHERE %s=?",
				quoteIdentifier(target.Table), quoteIdentifier(target.Column))
			if extra := strings.TrimSpace(keep[key]); extra != "" {
				statement += " AND " + extra
			}
		} else {
			replacement := "NULL"
			if target.NotNull {
				replacement = fmt.Sprint(erasedUserSentinel)
			}
			statement = fmt.Sprintf("UPDATE %s SET %s=%s WHERE %s=?",
				quoteIdentifier(target.Table), quoteIdentifier(target.Column),
				replacement, quoteIdentifier(target.Column))
		}
		res, execErr := conn.Execute(statement, []any{userID})
		if execErr != nil {
			return erasureSweep{}, fmt.Errorf("erasing %s: %w", key, execErr)
		}
		affected := res.RowsAffected
		if affected <= 0 {
			continue
		}
		if target.Disposition == erasureDelete {
			sweep.Deleted[key] = affected
			sweep.RowsDeleted += affected
		} else {
			sweep.Anonymised[key] = affected
			sweep.RowsAnonymised += affected
		}
	}
	// The world_state rows keyed on the person (v1.34.0) are in no column the
	// schema walk above can see - the id is inside the key - so they are swept
	// here, in the one function both levers share, and not in either lever.
	shelf, err := clearPlayerWorldStateTx(conn, userID)
	if err != nil {
		return erasureSweep{}, fmt.Errorf("erasing world_state: %w", err)
	}
	if shelf > 0 {
		sweep.Deleted["world_state.key"] = shelf
		sweep.RowsDeleted += shelf
	}
	return sweep, nil
}

type erasePlayerPayload struct {
	UserID int64  `json:"user_id"`
	Reason string `json:"reason"`
}

// adminErasePlayer removes one Discord user from the world and reports what it
// touched. Idempotent by construction: a second run finds nothing and succeeds,
// which matters because the operator answering a request has no way to know
// whether the first attempt got halfway.
func adminErasePlayer(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, raw json.RawMessage) (any, error) {
	var p erasePlayerPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if p.UserID <= 0 {
		return nil, errors.New("invalid user_id")
	}
	if p.UserID == adminUserID {
		// Erasing the GM would take the audit trail's author with it and leave
		// nobody able to run the console. A deliberate refusal, not an
		// oversight: an operator who really means it can stop the bot and use
		// reset_database.sh.
		return nil, errors.New("an administrator cannot erase themselves through the console")
	}
	targets, err := erasureTargets(conn)
	if err != nil {
		return nil, err
	}
	if len(targets) == 0 {
		return nil, errors.New("no erasure targets discovered: the schema looks wrong")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	// The character's name is read before anything is deleted, purely so the
	// operator's receipt says who this was. It is not written to the audit row.
	nameRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{p.UserID})
	if err != nil {
		return nil, err
	}
	hadCharacter := len(nameRes.Rows) > 0
	// A player family is succeeded before the sweep, never after it (v1.0.14).
	// The sweep walks tables alphabetically, so `characters` goes first, and
	// under foreign_keys=ON that delete fires `player_families`' ON DELETE
	// CASCADE: erasing a founder deleted the whole house and every other
	// member's place in it, while erasureAnonymise said the family outlives
	// whoever founded it. `playerFamilyDepartTx` is the rule a founder walking
	// out already had - the most senior who stays takes the house, and a house
	// with nobody left in it is dissolved - and a character reset uses it too.
	if tableExistsTx(conn, "player_family_members") {
		if _, err := playerFamilyDepartTx(conn, p.UserID); err != nil {
			return nil, err
		}
	}
	// What the person had in escrow comes back before the sweep takes the rows
	// it would come back from: the lots they were selling are closed and their
	// high bidder refunded, a lot they were winning loses only the bid, and a
	// caravan still on the road is lost with its owner. The resolver a death
	// runs, and a reset.
	if _, err := resolveLeavingEscrowTx(conn, catalog, p.UserID, float64(time.Now().UnixNano())/1e9); err != nil {
		return nil, err
	}
	sweep, err := applyErasureTargets(conn, p.UserID, targets, nil)
	if err != nil {
		return nil, err
	}
	deleted, anonymised := sweep.Deleted, sweep.Anonymised
	rowsDeleted, rowsAnonymised := sweep.RowsDeleted, sweep.RowsAnonymised
	// The two links the schema walk cannot see (v1.35.0), swept here and not
	// in applyErasureTargets: a reset shares that function and refuses any
	// anonymised row it has not released, and these are an erasure's alone -
	// a reset keeps the account, so its caravans and its requests stay its own.
	unwalked, err := eraseUnwalkedLinksTx(conn, p.UserID, float64(time.Now().UnixNano())/1e9)
	if err != nil {
		return nil, err
	}
	for key, n := range unwalked {
		anonymised[key] = n
		rowsAnonymised += n
	}
	// The audit row records that the request was honoured and how much it
	// moved. It deliberately carries no character name, no location and no
	// content - a record of an erasure that quotes the erased data is not an
	// erasure. The Discord id stays, because a record nobody can tie to a
	// request cannot demonstrate the request was met.
	if err := auditAdmin(conn, adminUserID, "admin.player.erase", fmt.Sprintf("user:%d", p.UserID),
		map[string]any{"had_character": hadCharacter},
		map[string]any{
			"rows_deleted": rowsDeleted, "rows_anonymised": rowsAnonymised,
			"tables_touched": len(deleted) + len(anonymised),
		}, p.Reason); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{
		"user_id":         p.UserID,
		"had_character":   hadCharacter,
		"rows_deleted":    rowsDeleted,
		"rows_anonymised": rowsAnonymised,
		"tables_touched":  len(deleted) + len(anonymised),
		"deleted":         deleted,
		"anonymised":      anonymised,
	}, nil
}
