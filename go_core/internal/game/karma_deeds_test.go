package game

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// Good deeds are worth karma, capped per deed (v1.9.1). The event_log table is
// production's DDL, so the count the cap reads is the one production reads.
func setupDeedKarmaDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS event_log (
		id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT NOT NULL,
		payload_json TEXT NOT NULL, created_at REAL NOT NULL)`)
	batch4Exec(t, path, worldEventNodesDDL)
	batch4Exec(t, path, `UPDATE characters SET karma_score=0 WHERE user_id=42`)
	return path
}

func karmaOf(t *testing.T, path string) int64 {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT karma_score FROM characters WHERE user_id=42`))
}

func TestADeedPaysUpToItsCapAndNoFurther(t *testing.T) {
	path := setupDeedKarmaDB(t)
	for i := 0; i < 5; i++ {
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			grantDeedKarmaTx(conn, 42, "world_event_good_deed", "world_event:flood", worldEventDeedKarma, worldEventDeedKarmaCap, 1)
			return nil
		}); err != nil {
			t.Fatal(err)
		}
	}
	if got := karmaOf(t, path); got != worldEventDeedKarmaCap*worldEventDeedKarma {
		t.Fatalf("five good deeds in one event paid %d karma, want the cap %d", got, worldEventDeedKarmaCap)
	}
	// A different event is a different deed.
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		grantDeedKarmaTx(conn, 42, "world_event_good_deed", "world_event:fire", worldEventDeedKarma, worldEventDeedKarmaCap, 1)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if got := karmaOf(t, path); got != worldEventDeedKarmaCap+1 {
		t.Fatalf("a deed in a second event paid nothing: karma %d", got)
	}
}

func TestADeedNeverRefusesWhatItRidesOn(t *testing.T) {
	path := setupBatch4AuthorityDB(t) // no event_log at all
	var paid int64 = -1
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		paid, _ = grantDeedKarmaTx(conn, 42, "scene_resolve", "scene_resolve:day:0", 1, 1, 1)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if paid != 0 {
		t.Fatalf("a deed with no ledger to count against paid %d", paid)
	}
}

func TestTheLastOfASiteIsWorthKarmaOnce(t *testing.T) {
	path := setupDeedKarmaDB(t)
	batch4Exec(t, path, `INSERT INTO world_event_nodes(event_key,node_key,node_type,name,total,remaining,created_at,updated_at) VALUES('ev','boar','beast','Boar',2,1,0,0),('ev','herb','herb','Herb',1,0,0,0)`)
	clear := func() map[string]any {
		var deed map[string]any
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			deed = eventSiteClearedKarmaTx(conn, 42, "ev", 1)
			return nil
		}); err != nil {
			t.Fatal(err)
		}
		return deed
	}
	if deed := clear(); deed != nil {
		t.Fatalf("a site with a boar left paid for clearing it: %v", deed)
	}
	batch4Exec(t, path, `UPDATE world_event_nodes SET remaining=0`)
	if deed := clear(); deed == nil || i64(deed["karma_delta"]) != eventSiteClearedKarma {
		t.Fatalf("taking the last of the site paid %v, want +%d", deed, eventSiteClearedKarma)
	}
	if deed := clear(); deed != nil {
		t.Fatalf("a cleared site paid twice: %v", deed)
	}
}

func TestAResolveAgainstTheWorldIsADeedAndAgainstYourselfIsNot(t *testing.T) {
	path := setupDeedKarmaDB(t)
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	act := func(target string, minute int64) map[string]any {
		raw, _ := json.Marshal(map[string]any{"action_key": "resolve", "target": target, "detail": "I hold my ground.", "game_minute": minute})
		var out map[string]any
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			m, err := resolveSceneAction(conn, crossingCatalog(t), 42, raw)
			out, _ = m.Result.(map[string]any)
			return err
		}); err != nil {
			t.Fatal(err)
		}
		return out
	}
	if out := act("Self", 10); out["deed_karma"] != nil || karmaOf(t, path) != 0 {
		t.Fatalf("a Resolve against oneself, which always succeeds, paid karma: %v", out)
	}
	if out := act("Environment", 10); out["deed_karma"] == nil || karmaOf(t, path) != 1 {
		t.Fatalf("a successful Resolve paid nothing: %v", out)
	}
	if act("Environment", 20); karmaOf(t, path) != 1 {
		t.Fatalf("a second Resolve on the same world day paid again: karma %d", karmaOf(t, path))
	}
	if act("Environment", 20+deedKarmaDayGameMinutes); karmaOf(t, path) != 2 {
		t.Fatalf("a Resolve on the next world day paid nothing: karma %d", karmaOf(t, path))
	}
}

// Every deed is paid where it happens. The four doors are read by AST, because
// the helpers' own tests pass just as well against a tree nothing calls them
// from - which is the shape a missing wire always has.
func TestEveryGoodDeedIsPaidWhereItHappens(t *testing.T) {
	want := map[string]string{
		"worldEventActAction":       "grantDeedKarmaTx",
		"resolveSceneAction":        "grantDeedKarmaTx",
		"explorationEventActAction": "grantDeedKarmaTx",
		"worldEventEngageAction":    "eventSiteClearedKarmaTx",
	}
	found := map[string]bool{}
	sitePayers := 0
	fset := token.NewFileSet()
	for _, file := range []string{"world_event_actions.go", "check_scene_actions.go", "exploration_event_actions.go", "world_event_sites.go", "combat_actions.go"} {
		parsed, err := parser.ParseFile(fset, file, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			ast.Inspect(fn, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				ident, ok := call.Fun.(*ast.Ident)
				if !ok {
					return true
				}
				if want[fn.Name.Name] == ident.Name {
					found[fn.Name.Name] = true
				}
				if ident.Name == "eventSiteClearedKarmaTx" {
					sitePayers++
				}
				return true
			})
		}
	}
	for fn, helper := range want {
		if !found[fn] {
			t.Errorf("%s no longer calls %s, so that good deed pays no karma", fn, helper)
		}
	}
	if sitePayers != 2 {
		t.Errorf("a site's last unit is taken by an engage and by an event battle; %d of them pay for clearing it, want 2", sitePayers)
	}
}
