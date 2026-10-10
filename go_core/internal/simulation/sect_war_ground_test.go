package simulation

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The war step moves on a whole place, never a street of one. The war door
// refuses a declaration over a part of a city (game.DeclareWarTx), and one
// system's error ends the whole tick - so a war step that picked a held street
// as its target would not merely fail, it would take every batch ordered after
// it with it, on every tick, for as long as the street was held.

const (
	groundEast = "Cloudblade City East Gate"
	groundCity = "Cloudblade City"
)

// groundWarSchema is sectWarSchema with the two keys production declares:
// territory_wars on territory_state, and the operation row on the war.
func groundWarSchema(t *testing.T) string {
	t.Helper()
	schema := strings.Replace(sectWarSchema,
		"created_at REAL NOT NULL, updated_at REAL NOT NULL);\nCREATE TABLE territory_war_operations(",
		"created_at REAL NOT NULL, updated_at REAL NOT NULL,\n    FOREIGN KEY(territory_key) REFERENCES territory_state(territory_key) ON DELETE CASCADE);\nCREATE TABLE territory_war_operations(", 1)
	schema = strings.Replace(schema,
		"occupation_until_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);\nCREATE TABLE world_history_events(",
		"occupation_until_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,\n    FOREIGN KEY(war_id) REFERENCES territory_wars(war_id) ON DELETE CASCADE);\nCREATE TABLE world_history_events(", 1)
	if strings.Count(schema, "FOREIGN KEY") != 2 {
		t.Fatal("the fixture's war tables still lack the keys production declares")
	}
	return schema
}

// groundWarWorld is the shipped catalogue with a neutral territory row for
// every place, one sect looking outward (Crimson Furnace) and the sect it
// would look at holding exactly the ground the test names, weakly.
func groundWarWorld(t *testing.T, held string) (string, *Runner) {
	t.Helper()
	catalog, err := worlddata.Load(filepath.Join("..", "..", "..", "content", "world.json"))
	if err != nil {
		t.Fatalf("the content file is in the repository; the read is broken, not the tree: %v", err)
	}
	path := setupSimulationDB(t, groundWarSchema(t))
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for name := range catalog.Locations {
		if _, err := conn.Execute(`INSERT INTO territory_state(territory_key,name,region) VALUES(?,?,?)`, []any{name, name, name}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key='Azure Cloud Sect',defense=30 WHERE territory_key=?`, []any{held}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,influence,resources) VALUES('Crimson Furnace Sect',86,70),('Azure Cloud Sect',20,20)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path, &Runner{World: catalog}
}

func TestTheWarStepNeverMovesOnAStreet(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	step := func(t *testing.T, path string, r *Runner) int64 {
		t.Helper()
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		n, err := r.npcSectWars(conn, 1, 10000)
		if err != nil {
			t.Fatalf("the war step ended the tick: %v", err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		return n
	}
	// The control, so the quiet is not a fixture that never moves: the same
	// ground held at the city is a target.
	t.Run("a held city is a target", func(t *testing.T) {
		path, r := groundWarWorld(t, groundCity)
		if declared := step(t, path, r); declared != 1 {
			t.Fatalf("an ambitious sect beside a weakly held city declared %d war(s); the fixture never moves", declared)
		}
		if got := simScalar(t, path, `SELECT territory_key FROM territory_wars`); got != groundCity {
			t.Fatalf("the war is over %v", got)
		}
	})
	t.Run("a held street is not", func(t *testing.T) {
		path, r := groundWarWorld(t, groundEast)
		if declared := step(t, path, r); declared != 0 {
			t.Fatalf("the war step declared %d war(s) over a street", declared)
		}
		if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM territory_wars`)); n != 0 {
			t.Fatalf("%d war(s) stand over a street", n)
		}
	})
}

// The repair sweeps before the siege tick looks at a war, outside the
// automation flags: whether the world's sects fight is the flag's to say,
// whether a street can be held is not, and a tick that fought a war over a
// street first and set it aside after would hand the street to a sect in
// between.
func TestTheSweepRunsBeforeTheSiegeTick(t *testing.T) {
	file, err := parser.ParseFile(token.NewFileSet(), "advanced_maintenance.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	callName := func(stmt ast.Stmt) string {
		assign, ok := stmt.(*ast.AssignStmt)
		if !ok || len(assign.Rhs) != 1 {
			return ""
		}
		call, ok := assign.Rhs[0].(*ast.CallExpr)
		if !ok {
			return ""
		}
		if sel, ok := call.Fun.(*ast.SelectorExpr); ok {
			return sel.Sel.Name
		}
		return ""
	}
	order := map[string]int{}
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "advancedMaintenance" {
			continue
		}
		// Top-level statements only: a call inside an `if` is a call behind a flag.
		for i, stmt := range fn.Body.List {
			if name := callName(stmt); name != "" {
				order[name] = i
			}
		}
	}
	sweep, okSweep := order["SetAsidePartialHoldingsTx"]
	wars, okWars := order["advanceWars"]
	if !okWars {
		t.Fatal("the walk found no advanceWars call among advancedMaintenance's own statements; the gate is broken, not the tree")
	}
	if !okSweep {
		t.Fatal("advancedMaintenance does not run game.SetAsidePartialHoldingsTx as a statement of its own: a world holding banners over streets is never put right, or only while a flag is on")
	}
	if sweep >= wars {
		t.Fatalf("the repair runs at statement %d, after the siege tick at %d: a war over a street would be fought before it is set aside", sweep, wars)
	}
}
