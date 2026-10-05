package game

import (
	"fmt"
	"sort"

	"xianxia/core/internal/storage"
)

// kinGrudge is what a killing costs a player with the dead's people: their
// spouse, their children and their disciples each hold this much more grudge
// against the killer (v1.29.0), clamped where `npc.relationship` clamps it.
//
// A player's kill wrote a `victim_lineage` grudge (combat.resolve) and a
// clan blood feud when the dead headed a household, and nothing about the
// people who were actually theirs: a widow met in the next town greeted her
// husband's killer exactly as she greeted a stranger, while `npc_relationships`
// carried a `grudge` column the narrator and `/talk` already read.
const kinGrudge = 40

// kinOfTx is everybody who was the dead's own - the spouse, the children born
// to them and the disciples they were teaching - alive, sorted, once each. It
// is read before ReleaseNPCBondsTx, which is what widows the spouse and so
// takes the name off the row this reads. Best-effort about the tables: a
// database without them has no kin to find.
func kinOfTx(conn *storage.Conn, deceased string) []string {
	seen := map[string]bool{}
	add := func(sql string, args []any) {
		res, err := conn.Execute(sql, args)
		if err != nil {
			return
		}
		for _, row := range res.Rows {
			name := fmt.Sprint(row[0])
			if name != "" && name != deceased && name != "<nil>" {
				seen[name] = true
			}
		}
	}
	if tableExistsTx(conn, "npc_life_state") {
		add(`SELECT npc_name FROM npc_life_state WHERE spouse_name=? AND relationship_status='married' AND health>0`, []any{deceased})
	}
	if tableExistsTx(conn, "npc_descendants") {
		add(`SELECT child_name FROM npc_descendants WHERE (parent_a=? OR parent_b=?) AND status='alive'`, []any{deceased, deceased})
	}
	if tableExistsTx(conn, "npc_disciple_bonds") {
		add(`SELECT disciple_name FROM npc_disciple_bonds WHERE master_name=? AND status='active'`, []any{deceased})
	}
	out := make([]string, 0, len(seen))
	for name := range seen {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

// RememberTheKillerTx raises the grudge each of the dead's kin holds against
// the player who killed them, and answers who now remembers.
func RememberTheKillerTx(conn *storage.Conn, userID int64, kin []string, deceased string, now float64) ([]string, error) {
	if len(kin) == 0 || !tableExistsTx(conn, "npc_relationships") {
		return nil, nil
	}
	summary := "Killed " + deceased + ", who was theirs."
	for _, name := range kin {
		if _, err := conn.Execute(`INSERT INTO npc_relationships(user_id,npc_name,grudge,encounter_count,last_summary,updated_at)
VALUES(?,?,?,0,?,?) ON CONFLICT(user_id,npc_name) DO UPDATE SET grudge=MIN(100,npc_relationships.grudge+excluded.grudge),last_summary=excluded.last_summary,updated_at=excluded.updated_at`,
			[]any{userID, name, kinGrudge, summary, now}); err != nil {
			return nil, err
		}
	}
	return kin, nil
}
