package game

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A rebirth starts a life without what the last one made (v1.12.3).
//
// `reincarnateAction` wipes the incarnation-scoped tables before it installs
// the new body, from a list of table names written when the tables were. A
// captured flame (schema 70) and a built spirit sense (schema 71) each came
// after the list and neither was added to it, so a soul reborn kept the flame it
// had captured in a body it no longer had and the sense it had built - and both
// are what open the top grade of a craft, which the rest of a new life does not
// carry (craft echo, the one thing samsara does remember of a trade, is a
// scaled echo and not a flame). The fixture gives every listed table a row for
// the reborn soul and another for somebody else.

func setupIncarnationDB(t *testing.T, withLater bool) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	tables := append([]string{}, incarnationScopedTables...)
	if withLater {
		// Named here, not read off incarnationScopedLaterTables: a fixture that
		// took its tables from the list under test would pass, silently,
		// against a list that had forgotten them.
		tables = append(tables, "character_flames", "character_spirit_sense")
	}
	for _, table := range tables {
		// Only user_id matters to the wipe. The tables batch4 already made keep
		// their own DDL, so a real one is never replaced by this stand-in.
		batch4Exec(t, path, fmt.Sprintf(`CREATE TABLE IF NOT EXISTS %s(user_id INTEGER NOT NULL, marker TEXT NOT NULL DEFAULT 'row')`, table))
	}
	return path
}

func rowsFor(t *testing.T, path, table string, userID int64) int64 {
	t.Helper()
	return i64(actionScalar(t, path, fmt.Sprintf(`SELECT COUNT(*) FROM %s WHERE user_id=?`, table), userID))
}

func wipeFor(t *testing.T, path string, userID int64) error {
	t.Helper()
	return crossingApply(t, path, func(conn *storage.Conn) error {
		return clearIncarnationStateTx(conn, userID)
	})
}

func TestARebirthLeavesNeitherFlameNorSpiritSense(t *testing.T) {
	path := setupIncarnationDB(t, true)
	seeded := map[string]bool{}
	for _, table := range append(append([]string{}, incarnationScopedTables...), "character_flames", "character_spirit_sense") {
		// A table batch4 built with its own columns takes no marker row; every
		// one of the two later tables - and most of the rest - is ours.
		if n := i64(actionScalar(t, path, fmt.Sprintf(`SELECT COUNT(*) FROM pragma_table_info('%s') WHERE name='marker'`, table))); n == 0 {
			continue
		}
		batch4Exec(t, path, fmt.Sprintf(`INSERT INTO %s(user_id) VALUES(42),(43)`, table))
		seeded[table] = true
	}
	for _, table := range []string{"character_flames", "character_spirit_sense"} {
		if !seeded[table] {
			t.Fatalf("%s was not seeded; the fixture cannot say what a rebirth does to it", table)
		}
	}
	if err := wipeFor(t, path, 42); err != nil {
		t.Fatal(err)
	}
	for table := range seeded {
		if n := rowsFor(t, path, table, 42); n != 0 {
			t.Errorf("a reincarnation left %d row(s) in %s for the reborn soul: a flame and a spirit sense open the top grade and must not outlive the body that made them", n, table)
		}
		if n := rowsFor(t, path, table, 43); n != 1 {
			t.Errorf("%s holds %d row(s) for somebody else after the wipe, want 1: it is scoped to one soul", table, n)
		}
	}
}

// In the compose stack the engine is healthy before db-init migrates, so a
// rebirth can run on a world that has not got the two later tables yet. It is
// not refused over a table a later schema owns.
func TestARebirthOnAWorldWithoutTheLaterTablesStillWipesTheRest(t *testing.T) {
	path := setupIncarnationDB(t, false)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',3)`)
	if err := wipeFor(t, path, 42); err != nil {
		t.Fatalf("a rebirth was refused over tables that a later schema owns: %v", err)
	}
	if n := rowsFor(t, path, "inventory", 42); n != 0 {
		t.Fatalf("the wipe stopped at the missing table and left %d inventory row(s)", n)
	}
}

// The wipe has one door, and the rebirth goes through it: the behavioural tests
// above pass against a tree where reincarnateAction has gone back to keeping a
// list of its own.
func TestReincarnationClearsThroughTheOneWipe(t *testing.T) {
	parsed, err := parser.ParseFile(token.NewFileSet(), "lifecycle_actions.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	called, ownList := false, false
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "reincarnateAction" {
			continue
		}
		ast.Inspect(fn, func(n ast.Node) bool {
			switch x := n.(type) {
			case *ast.CallExpr:
				if id, ok := x.Fun.(*ast.Ident); ok && id.Name == "clearIncarnationStateTx" {
					called = true
				}
			case *ast.BasicLit:
				if strings.Contains(x.Value, "DELETE FROM %s") {
					ownList = true
				}
			}
			return true
		})
	}
	if !called {
		t.Error("reincarnateAction no longer wipes through clearIncarnationStateTx")
	}
	if ownList {
		t.Error("reincarnateAction keeps its own wipe loop; a table added to one list and not the other is how a rebirth kept a flame")
	}
}
