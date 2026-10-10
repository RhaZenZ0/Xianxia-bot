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
//
// The same list was missing from the character row (v1.33.0): a rebirth
// rewrites it in place, the rewrite names the columns that existed when it was
// written, and the sword intent a Sword Cultivator had banked (schema 72) and
// the anchor the body's mending counts from (schema 59) passed into the next
// life. The bond with a master among the sect's people is a row of its own that
// neither wipe named.

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
// rebirth can run on a world that has not got the two later tables - or the two
// later columns of the character row - yet. It is not refused over a table or a
// column a later schema owns.
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

// A new life starts the columns of the character row that belong to the life
// where the soul's own are kept. Production DDL for both columns, added the way
// the migrations add them, and a second soul to hold the wipe to one.
func TestARebirthTakesTheLifesColumnsWithIt(t *testing.T) {
	path := setupIncarnationDB(t, true)
	batch4Exec(t, path, `ALTER TABLE characters ADD COLUMN vitality_recovered_game_minute INTEGER`)
	batch4Exec(t, path, `ALTER TABLE characters ADD COLUMN path_resource INTEGER NOT NULL DEFAULT 0`)
	batch4Exec(t, path, `UPDATE characters SET path_resource=3,vitality_recovered_game_minute=500 WHERE user_id IN (42,43)`)
	karma := actionScalar(t, path, `SELECT karma_score FROM characters WHERE user_id=42`)
	if err := wipeFor(t, path, 42); err != nil {
		t.Fatal(err)
	}
	if n := i64(actionScalar(t, path, `SELECT path_resource FROM characters WHERE user_id=42`)); n != 0 {
		t.Errorf("a reincarnation left %d sword intent banked in the new body: the intent was earned by a body that is gone", n)
	}
	if v := actionScalar(t, path, `SELECT vitality_recovered_game_minute FROM characters WHERE user_id=42`); v != nil {
		t.Errorf("a reincarnation kept the mending anchor: %v; the new body has not been hurt, and an old anchor banks time it never spent", v)
	}
	if n := i64(actionScalar(t, path, `SELECT path_resource FROM characters WHERE user_id=43`)); n != 3 {
		t.Errorf("somebody else holds %d sword intent after the wipe, want 3: it is scoped to one soul", n)
	}
	if v := i64(actionScalar(t, path, `SELECT vitality_recovered_game_minute FROM characters WHERE user_id=43`)); v != 500 {
		t.Errorf("somebody else's mending anchor is %d after the wipe, want 500", v)
	}
	// The soul's own columns are not the wipe's: karma is what the wheel reads.
	if got := actionScalar(t, path, `SELECT karma_score FROM characters WHERE user_id=42`); fmt.Sprint(got) != fmt.Sprint(karma) {
		t.Errorf("the wipe moved the karma from %v to %v; karma is the soul's, and the wheel reads it", karma, got)
	}
}

// The bond with a master among the sect's people is the old life's. It is a
// table of its own (schema 79) and names the disciple by account, so a rebirth
// that left it would go on paying its terms to a new body that never knelt to
// anyone. Production DDL, foreign key included.
func TestARebirthEndsTheBondWithTheSectsPeople(t *testing.T) {
	path := setupIncarnationDB(t, true)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS npc_mentorships(disciple_user_id INTEGER PRIMARY KEY,master_npc_name TEXT NOT NULL,sect_name TEXT NOT NULL,accepted_game_minute INTEGER NOT NULL DEFAULT 0,attention INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,FOREIGN KEY(disciple_user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,created_at) VALUES(42,'Elder Test Qiu','Azure Cloud Sect',0),(43,'Elder Test Qiu','Azure Cloud Sect',0)`)
	if err := wipeFor(t, path, 42); err != nil {
		t.Fatal(err)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM npc_mentorships WHERE disciple_user_id=42`)); n != 0 {
		t.Errorf("a reincarnation left %d bond(s) with the sect's people for the reborn soul: the master taught a body that is gone", n)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM npc_mentorships WHERE disciple_user_id=43`)); n != 1 {
		t.Errorf("somebody else holds %d bond(s) after the wipe, want 1: it is scoped to one soul", n)
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
