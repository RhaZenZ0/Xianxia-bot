package game

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A sect's doors open by rank (v1.24.0). v1.19.4 opened every sect leaf to any
// member and the engine refused nothing by rank outside the manor, so an Outer
// Disciple an hour into a sect could start a territorial war. `rank_floors` is
// the owner's ladder, keyed by operation, and these tests hold the engine to
// it - against the shipped catalogue and a copy with the floor moved, because
// a test against the shipped number alone passes against a literal (v1.0.11).

func withSectSystemRule(catalog worlddata.Catalog, key string, value any) worlddata.Catalog {
	out := catalog
	out.SectSystem = map[string]any{}
	for k, v := range catalog.SectSystem {
		out.SectSystem[k] = v
	}
	out.SectSystem[key] = value
	return out
}

func rankFloorTerritoryFixture(t *testing.T, rankName string, rankLevel int) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS territory_state(territory_key TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50, defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS territory_wars(war_id INTEGER PRIMARY KEY AUTOINCREMENT, attacker_key TEXT NOT NULL, defender_key TEXT NOT NULL, territory_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', started_game_minute INTEGER NOT NULL DEFAULT 0, operations_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,'Azure Cloud Sect',?,?,0)`, rankName, rankLevel)
	batch4Exec(t, path, `INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES('riverguard','Riverguard Reach','Riverguard City',0)`)
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	return path
}

func TestAnOuterDiscipleCannotClaimTerritory(t *testing.T) {
	catalog := crossingCatalog(t)
	floor := sectRankFloorGo(catalog, "territory.claim")
	if floor <= 10 {
		t.Fatalf("the shipped territory.claim floor is %d; this test stands an Outer Disciple (10) below it", floor)
	}
	world := batch4WorldPath(t)
	path := rankFloorTerritoryFixture(t, "Outer Disciple", 10)
	_, err := batch4ApplyErr(path, world, "territory.claim", 42, 1, map[string]any{"territory_key": "riverguard"})
	want := "claiming territory for your sect asks for " + sectRankName(catalog, floor)
	if err == nil || !strings.Contains(err.Error(), want) || !strings.Contains(err.Error(), "you hold Outer Disciple") {
		t.Fatalf("an Outer Disciple claimed territory: want a refusal naming %q, got %v", want, err)
	}
	core := rankFloorTerritoryFixture(t, sectRankName(catalog, floor), int(floor))
	if _, err := batch4ApplyErr(core, world, "territory.claim", 42, 1, map[string]any{"territory_key": "riverguard"}); err != nil {
		t.Fatalf("a %s standing on the ground was refused: %v", sectRankName(catalog, floor), err)
	}
}

func TestTheTerritoryFloorIsTheContents(t *testing.T) {
	catalog := crossingCatalog(t)
	path := rankFloorTerritoryFixture(t, "Outer Disciple", 10)
	raw, _ := json.Marshal(map[string]any{"territory_key": "riverguard"})
	claim := func(c worlddata.Catalog) error {
		return crossingApply(t, path, func(conn *storage.Conn) error {
			_, err := territoryClaimActionGo(conn, c, 42, raw)
			return err
		})
	}
	floors, _ := catalog.SectSystem["rank_floors"].(map[string]any)
	lowered := map[string]any{}
	for k, v := range floors {
		lowered[k] = v
	}
	lowered["territory.claim"] = float64(10)
	if err := claim(withSectSystemRule(catalog, "rank_floors", lowered)); err != nil {
		t.Fatalf("with territory.claim lowered to 10 an Outer Disciple was refused: %v - the floor is a literal again", err)
	}
}

func TestNoFloorMeansNoGate(t *testing.T) {
	catalog := withSectSystemRule(crossingCatalog(t), "rank_floors", map[string]any{})
	if err := requireSectRankTx(catalog, map[string]any{"rank_level": int64(0), "rank_name": "Outer Disciple"}, "war.act", "fighting"); err != nil {
		t.Fatalf("an unauthored floor refused: %v", err)
	}
	shipped := crossingCatalog(t)
	if err := requireSectRankTx(shipped, map[string]any{"rank_level": int64(10), "rank_name": "Outer Disciple"}, "war.act", "fighting in your sect's war"); err == nil {
		t.Fatal("an Outer Disciple was let into the war")
	}
}

// Every floor the content authors is read where its operation runs: a key
// nobody reads is a door the panel padlocks and the engine opens.
func TestEveryRankFloorIsReadAtItsOperation(t *testing.T) {
	catalog := crossingCatalog(t)
	floors, _ := catalog.SectSystem["rank_floors"].(map[string]any)
	if len(floors) == 0 {
		t.Fatal("sect_system.rank_floors is empty; the gate is broken, not the tree")
	}
	read := map[string]bool{}
	fset := token.NewFileSet()
	files, _ := filepath.Glob("*.go")
	for _, file := range files {
		if strings.HasSuffix(file, "_test.go") {
			continue
		}
		src, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := parser.ParseFile(fset, file, src, 0)
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		ast.Inspect(parsed, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			ident, ok := call.Fun.(*ast.Ident)
			if !ok || (ident.Name != "requireSectRankTx" && ident.Name != "sectRankFloorGo") {
				return true
			}
			for _, arg := range call.Args {
				if lit, ok := arg.(*ast.BasicLit); ok && lit.Kind == token.STRING {
					op, _ := strconv.Unquote(lit.Value)
					read[op] = true
				}
			}
			return true
		})
	}
	if !read["territory.claim"] {
		t.Fatal("the walk did not find territory.claim read anywhere; the sweep is broken, not the tree")
	}
	for op := range floors {
		if !read[op] {
			t.Errorf("sect_system.rank_floors names %q and no production call reads it", op)
		}
	}
}
