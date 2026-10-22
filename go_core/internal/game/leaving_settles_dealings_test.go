package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// What a body leaves open with other people is settled when it leaves
// (v1.37.0). The samsara table gate (samsara_tables_test.go) found a reborn
// cultivator still holding the old body's open trade offers, its Dao
// partnership, the duel it was fighting and the stall it kept: an offer the
// other party could accept against an empty inventory, a bond paying resonance
// to a child, a duel whose turn never came, a stall the town went on buying
// from with nobody to pay. The owner's word was: settle them at death, through
// the leaving door every way out of a body shares (resolveLeavingEscrowTx), so
// a reset and an erasure close them the same way.
//
// These tests drive the death through the engine's own door
// (lifecycle.true_death, with the world file) on production's DDL for every
// table the settlement touches, because a fixture that cannot fail the way
// production fails is not testing production.

// coreDDL is app/database/core.py, the production DDL.
func coreDDL(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	raw, err := os.ReadFile(filepath.Clean(filepath.Join(filepath.Dir(file), "../../../app/database/core.py")))
	if err != nil {
		t.Fatalf("production DDL: %v", err)
	}
	return string(raw)
}

// productionTableDDL is one table as production builds it: its CREATE
// statement, foreign keys included, then every column a migration added to it
// - the literal `ALTER TABLE t ADD COLUMN ...` statements and the
// `for name, ddl in (...)` loops that write the same statement from tuples.
// A table built from the CREATE alone is the table as first shipped, which is
// how reincarnation_state came up without partner_echo.
func productionTableDDL(t *testing.T, table string) []string {
	t.Helper()
	raw := coreDDL(t)
	body := tableBody(raw, table)
	if strings.TrimSpace(body) == "" {
		t.Fatalf("production DDL has no table %q; the reader is broken, not the schema", table)
	}
	out := []string{fmt.Sprintf("CREATE TABLE IF NOT EXISTS %s (%s)", table, body)}
	literal := regexp.MustCompile(`ALTER TABLE ` + table + ` ADD COLUMN ([^"\n{]+)`)
	for _, m := range literal.FindAllStringSubmatch(raw, -1) {
		out = append(out, fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s", table, strings.TrimSpace(m[1])))
	}
	marker := fmt.Sprintf("ALTER TABLE %s ADD COLUMN {name} {ddl}", table)
	tuple := regexp.MustCompile(`\("([a-z_]+)",\s*"([^"]*)"\)`)
	for idx := strings.Index(raw, marker); idx >= 0; {
		start := strings.LastIndex(raw[:idx], "for name, ddl in (")
		if start < 0 {
			t.Fatalf("%s: a tuple loop writes columns and the reader cannot find its tuples", table)
		}
		for _, m := range tuple.FindAllStringSubmatch(raw[start:idx], -1) {
			out = append(out, fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", table, m[1], m[2]))
		}
		next := strings.Index(raw[idx+len(marker):], marker)
		if next < 0 {
			break
		}
		idx += len(marker) + next
	}
	return out
}

// createProductionTable builds one table on the fixture as production builds
// it. A column already present (in the fixture's own copy of the table, or in
// a CREATE that a later release folded the migration into) is not an error.
func createProductionTable(t *testing.T, path, table string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for i, ddl := range productionTableDDL(t, table) {
		if _, err := conn.Execute(ddl, nil); err != nil && i == 0 {
			t.Fatalf("%s: %v", table, err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

var leavingTables = []string{
	"battles", "reincarnation_state", "trade_offers", "dao_partnerships",
	"pvp_challenges", "pvp_matches", "player_stalls", "stall_listings", "stall_sales",
}

// setupLeavingDB is a world in which 42 can die: the batch-5 fixture, the
// samsara and dealing tables on production's DDL, a birth family, and 42 and
// 43 in the middle of every kind of dealing with each other.
func setupLeavingDB(t *testing.T) string {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	for _, table := range leavingTables {
		createProductionTable(t, path, table)
	}
	seedBirthFamily(t, path, 42)
	// Trades: one each way, open, and one already struck, which is history.
	batch4Exec(t, path, `INSERT INTO trade_offers(from_user_id,to_user_id,location,give_json,want_stones,status,created_at,updated_at) VALUES
		(42,43,'Greenriver Inn','{"spirit_herb":1}',3,'open',0,0),
		(43,42,'Greenriver Inn','{"recovery_pill":1}',0,'open',0,0),
		(43,42,'Greenriver Inn','{}',1,'accepted',0,0)`)
	// A Dao partnership at resonance 40, which the death reads an echo of.
	batch4Exec(t, path, `INSERT INTO dao_partnerships(user_a,user_b,requested_by,status,resonance,dual_sessions,created_at,updated_at) VALUES(42,43,42,'active',40,3,0,0)`)
	// A duel in progress, 42 to move.
	batch4Exec(t, path, `INSERT INTO pvp_challenges(challenge_id,challenger_user_id,target_user_id,stakes,status,created_at,expires_at) VALUES(7,42,43,'honor','accepted',0,9e9)`)
	batch4Exec(t, path, `INSERT INTO pvp_matches(challenge_id,player1_user_id,player2_user_id,player1_hp,player2_hp,turn_user_id,status,created_at,updated_at) VALUES(7,42,43,10,10,42,'active',0,0)`)
	// Two stalls: the dead keeper's, with two listings and a sale behind it,
	// and the survivor's, which is nobody's business.
	batch4Exec(t, path, `INSERT INTO player_stalls(user_id,city,name,currency_id,created_at,updated_at) VALUES(42,'Greenriver Town','Lin''s Table','low_spirit_stone',0,0),(43,'Greenriver Town','The Other Table','low_spirit_stone',0,0)`)
	batch4Exec(t, path, `INSERT INTO stall_listings(user_id,city,item_id,quantity,unit_price,currency_id,created_at,updated_at) VALUES
		(42,'Greenriver Town','spirit_herb',3,2,'low_spirit_stone',0,0),
		(42,'Greenriver Town','recovery_pill',2,5,'low_spirit_stone',0,0),
		(43,'Greenriver Town','spirit_herb',1,2,'low_spirit_stone',0,0)`)
	batch4Exec(t, path, `INSERT INTO stall_sales(user_id,item_id,quantity,unit_price,currency_id,buyer_npc_name,created_at) VALUES(42,'spirit_herb',1,2,'low_spirit_stone','Old Wen',0)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=1`)
	return path
}

// seedBirthFamily gives a character the family a death requires; the fixture's
// character_birth_family predates the generation column the death reads.
func seedBirthFamily(t *testing.T, path string, userID int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// Each tolerated when the fixture already has the column: the death reads
	// the family's generation and writes the body's death minute, the wheel's
	// ready minute and the count of deaths, all columns a migration added.
	for _, ddl := range []string{
		`ALTER TABLE character_birth_family ADD COLUMN generation INTEGER NOT NULL DEFAULT 1`,
		`ALTER TABLE characters ADD COLUMN death_game_minute INTEGER`,
		`ALTER TABLE characters ADD COLUMN reincarnation_ready_game_minute INTEGER`,
		`ALTER TABLE characters ADD COLUMN true_death_count INTEGER NOT NULL DEFAULT 0`,
	} {
		_, _ = conn.Execute(ddl, nil)
	}
	if _, err := conn.Execute(`INSERT INTO birth_families(archetype) VALUES('alchemy_family')`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO character_birth_family(user_id,family_id) VALUES(?,1)`, []any{userID}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func trueDeathOf(t *testing.T, path string, userID int64) map[string]any {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"reason": "the test"})
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{Operation: "lifecycle.true_death", ActorID: userID, ActionID: fmt.Sprintf("test:death:%d", userID), Payload: raw})
	if err != nil {
		t.Fatalf("lifecycle.true_death: %v", err)
	}
	result, _ := out.Result.(map[string]any)
	return result
}

func TestADeathClosesTheDealingsTheBodyHadOpen(t *testing.T) {
	path := setupLeavingDB(t)
	result := trueDeathOf(t, path, 42)

	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM trade_offers WHERE status='open' AND (from_user_id=42 OR to_user_id=42)`)); n != 0 {
		t.Errorf("%d open trade offer(s) survive the death: the other party can accept against a corpse's inventory", n)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM trade_offers WHERE status='voided'`)); n != 2 {
		t.Errorf("%d offer(s) voided, want the two that were open; the struck one is history and stays", n)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT status FROM trade_offers WHERE status<>'voided'`)); got != "accepted" {
		t.Errorf("the struck trade reads %q after the death, want accepted: a settlement never rewrites history", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT status FROM dao_partnerships WHERE user_a=42`)); got != "severed" {
		t.Errorf("the Dao partnership reads %q after the death, want severed: a bond with the dead goes on paying resonance to a child", got)
	}
	if echo := i64(result["partner_echo"]); echo != 10 {
		t.Errorf("partner_echo=%d, want 10 (resonance 40 / 4): the death severed the bond before it read the echo off it", echo)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT status||':'||COALESCE(winner_user_id,0) FROM pvp_matches WHERE challenge_id=7`)); got != "finished:43" {
		t.Errorf("the duel reads %q after the death, want finished:43 - a forfeit to the survivor, whose turn otherwise never comes", got)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM player_stalls WHERE user_id=42`)); n != 0 {
		t.Errorf("the dead keeper's stall is still open (%d row); the town goes on buying from it and pays a purse nobody holds", n)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM stall_listings WHERE user_id=42`)); n != 0 {
		t.Errorf("%d listing(s) of the dead keeper still laid out", n)
	}
	herbs := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`))
	pills := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`))
	if herbs != 4 || pills != 2 {
		t.Errorf("the corpse holds %d herbs and %d pills, want 4 and 2: the stall's goods go back to its keeper's rows for the rebirth to wipe", herbs, pills)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM stall_sales WHERE user_id=42`)); n != 1 {
		t.Errorf("the keeper's ledger has %d row(s) after the death, want 1: the sales happened", n)
	}
	// The survivor's dealings with the world are untouched.
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM player_stalls WHERE user_id=43`)); n != 1 {
		t.Errorf("the survivor's stall went with the dead keeper's (%d row)", n)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM stall_listings WHERE user_id=43`)); n != 1 {
		t.Errorf("the survivor's listing went with the dead keeper's (%d row)", n)
	}
	// And the death says what it settled, beside the auction and caravan counts.
	escrow, _ := result["escrow"].(map[string]any)
	if escrow == nil {
		t.Fatalf("the death reports no escrow: %v", result)
	}
	for key, want := range map[string]int64{"trades_voided": 2, "dao_bonds_severed": 1, "duels_forfeited": 1, "stall_entries_returned": 2} {
		if got := i64(escrow[key]); got != want {
			t.Errorf("escrow.%s=%d, want %d", key, got, want)
		}
	}
	if closed, _ := escrow["stall_closed"].(bool); !closed {
		t.Errorf("escrow.stall_closed=%v, want true", escrow["stall_closed"])
	}
}

// A body with nothing open dies with nothing to settle, and a world without the
// dealing tables yet (the compose stack's migration window) still lets it die.
func TestADeathWithNothingOpenSettlesNothing(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	for _, table := range []string{"battles", "reincarnation_state"} {
		createProductionTable(t, path, table)
	}
	seedBirthFamily(t, path, 42)
	result := trueDeathOf(t, path, 42)
	escrow, _ := result["escrow"].(map[string]any)
	if escrow == nil {
		t.Fatalf("the death reports no escrow: %v", result)
	}
	for _, key := range []string{"trades_voided", "dao_bonds_severed", "duels_forfeited"} {
		if got := i64(escrow[key]); got != 0 {
			t.Errorf("escrow.%s=%d on a world without the table", key, got)
		}
	}
	if closed, _ := escrow["stall_closed"].(bool); closed {
		t.Error("escrow.stall_closed=true with no stall tables")
	}
}

// The settlement has one door and the leaving door reaches it: the behavioural
// test above would pass against a tree where the death settled the dealings on
// its own and a reset or an erasure did not.
func TestTheLeavingDoorSettlesTheDealings(t *testing.T) {
	parsed, err := parser.ParseFile(token.NewFileSet(), "death_escrow.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	callers := map[string]bool{}
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok {
			continue
		}
		ast.Inspect(fn, func(n ast.Node) bool {
			if call, ok := n.(*ast.CallExpr); ok {
				if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "settleLeavingDealingsTx" {
					callers[fn.Name.Name] = true
				}
			}
			return true
		})
	}
	if !callers["resolveLeavingEscrowTx"] {
		t.Fatal("resolveLeavingEscrowTx no longer calls settleLeavingDealingsTx: a reset and an erasure leave the dealings open again")
	}
	delete(callers, "resolveLeavingEscrowTx")
	if len(callers) != 0 {
		t.Fatalf("settleLeavingDealingsTx is called outside the leaving door by %v: two doors to one settlement drift apart", callers)
	}
}

// The reset and the erasure go through the same door; this pins their call
// sites so the three ways out of a body cannot quietly become two.
func TestEveryWayOutOfABodyLeavesThroughTheOneDoor(t *testing.T) {
	want := map[string]string{
		"death_escrow.go":    "resolveIncarnationEscrowTx",
		"character_reset.go": "characterResetAction",
		"privacy_actions.go": "adminErasePlayer",
	}
	for file, fnName := range want {
		parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		found := false
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Name.Name != fnName {
				continue
			}
			ast.Inspect(fn, func(n ast.Node) bool {
				if call, ok := n.(*ast.CallExpr); ok {
					if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "resolveLeavingEscrowTx" {
						found = true
					}
				}
				return true
			})
		}
		if !found {
			t.Errorf("%s: %s does not call resolveLeavingEscrowTx; that way out of a body leaves its dealings open", file, fnName)
		}
	}
}
