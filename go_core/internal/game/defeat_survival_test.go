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

// A cultivator who loses a fight and lives (v1.0.3).
//
// `fatalChance` is `min(75, 8+gap*3)`, so against a same-realm opponent a
// defeat is fatal eight times in a hundred. The other ninety-two ended with
// `characters.vitality` on whatever `MAX(0, vitality-dmg)` had reached - which
// at the end of a losing fight is zero - while the two *fate-rescue* branches,
// the rarer and strictly worse outcome, each wrote `vitality=1` outright.
//
// Nothing in this tree regenerates vitality with time, so zero was not a state
// anybody waited their way out of, and `/battle` printed "You survive but are
// incapacitated" over it - a word the engine never enforced.

func defeatSurvivalSchema() string {
	return `
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY, name TEXT, path TEXT, location TEXT, life_status TEXT DEFAULT 'alive',
	attributes_json TEXT, qi INTEGER, qi_max INTEGER, vitality INTEGER, vitality_max INTEGER, updated_at REAL
);
CREATE TABLE character_conditions(
	condition_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES characters(user_id),
	condition_key TEXT, category TEXT, name TEXT, severity INTEGER, state TEXT,
	source_type TEXT, source_id TEXT, effect_json TEXT,
	created_game_minute INTEGER, updated_game_minute INTEGER, resolved_game_minute INTEGER,
	created_at REAL, updated_at REAL
);
CREATE TABLE active_effects(
	user_id INTEGER NOT NULL REFERENCES characters(user_id), effect_key TEXT, name TEXT,
	source_type TEXT, source_id TEXT, effect_json TEXT, stacks INTEGER,
	starts_game_minute INTEGER, ends_game_minute INTEGER, created_at REAL,
	PRIMARY KEY(user_id,effect_key,source_type,source_id)
);
CREATE TABLE inventory(
	user_id INTEGER NOT NULL REFERENCES characters(user_id), item_id TEXT, quantity INTEGER,
	PRIMARY KEY(user_id,item_id)
);
CREATE TABLE deployed_location_arrays(location TEXT, effect_json TEXT, starts_game_minute INTEGER, ends_game_minute INTEGER);
CREATE TABLE character_spiritual_roots(user_id INTEGER NOT NULL REFERENCES characters(user_id), grade TEXT, mutation TEXT);
CREATE TABLE character_physiques(user_id INTEGER NOT NULL REFERENCES characters(user_id), physique_id TEXT, state TEXT, evolution_stage INTEGER, instability INTEGER);
CREATE TABLE character_bloodlines(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES characters(user_id), bloodline_id TEXT, state TEXT, evolution_stage INTEGER, rejection INTEGER, primary_lineage INTEGER);
`
}

// setupDefeatSurvivalDB seeds a cultivator who has just been beaten to zero.
// The foreign keys are production's, so a fixture cannot accept a row the real
// schema refuses - `storage.Open` sets `foreign_keys=ON`.
func setupDefeatSurvivalDB(t *testing.T, vitality int64) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "defeat_survival.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	script := defeatSurvivalSchema() + `
INSERT INTO characters(user_id,name,path,location,life_status,attributes_json,qi,qi_max,vitality,vitality_max,updated_at)
	VALUES(77,'Rhymere','Sword Cultivator','Greenriver Town','alive','{"body":3,"agility":2,"spirit":2,"insight":3,"will":3,"presence":1}',14,14,` + itoa(vitality) + `,12,0);
INSERT INTO character_spiritual_roots(user_id,grade,mutation) VALUES(77,'Mortal','');
`
	if err := conn.ExecScript(script); err != nil {
		t.Fatal(err)
	}
	return path
}

func vitalityOf(t *testing.T, path string, userID int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	r, err := conn.Execute(`SELECT vitality FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Rows) == 0 {
		t.Fatalf("no character %d", userID)
	}
	return storage.ParseInt(r.Rows[0][0])
}

// TestSurvivingADefeatLeavesAHeartbeat is the finding itself. Drill: put the
// bare `applyCombatCondition` call back in place of the door and this fails
// with "a survivor was left on 0".
func TestSurvivingADefeatLeavesAHeartbeat(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	inj, err := survivedDefeatTx(conn, 77, "flesh_wound", 1, "battle", "9", 500)
	if err != nil {
		conn.Close()
		t.Fatalf("unexpected error: %v", err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	conn.Close()

	if got := vitalityOf(t, path, 77); got < survivorVitality {
		t.Fatalf("a survivor was left on %d; losing a fight and living must leave a heartbeat, and nothing in this tree heals with time", got)
	}
	if inj["condition_key"] != "flesh_wound" {
		t.Fatalf("the injury is still the point: %v", inj)
	}
}

// TestTheDoorNeverLowersAnybody: it is a floor under a survivor, not a number
// to be set. A fate rescue on somebody who somehow still had vitality must not
// cost them any.
func TestTheDoorNeverLowersAnybody(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 7)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := survivedDefeatTx(conn, 77, "bone_fracture", 2, "fate_rescue", "9", 500); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	conn.Close()
	if got := vitalityOf(t, path, 77); got != 7 {
		t.Fatalf("the door moved a survivor from 7 to %d; MAX is what keeps it a floor", got)
	}
}

// TestTheSurviveBranchesAgree holds the shape that produced the fault: four
// copies of "you lost and lived", two of which wrote a heartbeat and two of
// which wrote nothing. Read by AST rather than by substring, because the
// literal `vitality=1` also appears inside `survivedDefeatTx`'s own comment.
//
// Drill: restore a bare `UPDATE characters SET vitality=1` to either rescue
// branch and this names the function it is in.
func TestTheSurviveBranchesAgree(t *testing.T) {
	src, err := os.ReadFile("combat_actions.go")
	if err != nil {
		t.Fatalf("the sweep cannot read combat_actions.go, so it proves nothing: %v", err)
	}
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "combat_actions.go", src, 0)
	if err != nil {
		t.Fatalf("the sweep cannot parse combat_actions.go, so it proves nothing: %v", err)
	}

	sawDoor, offenders := false, []string{}
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Body == nil {
			continue
		}
		name := fn.Name.Name
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			lit, ok := n.(*ast.BasicLit)
			if !ok || lit.Kind != token.STRING {
				return true
			}
			sql := strings.ReplaceAll(lit.Value, " ", "")
			if !strings.Contains(sql, "UPDATEcharactersSETvitality") {
				return true
			}
			switch {
			case name == "survivedDefeatTx":
				sawDoor = true
			case strings.Contains(sql, "vitality=1"):
				offenders = append(offenders, name)
			}
			return true
		})
	}
	if !sawDoor {
		t.Fatal(`the sweep did not find survivedDefeatTx writing vitality at all; it is broken, not the tree`)
	}
	if len(offenders) > 0 {
		t.Fatalf("%v set a survivor's vitality directly; there is one door and it is survivedDefeatTx, "+
			"because four copies of this rule is how two of them came to disagree", offenders)
	}
}

// TestEveryDefeatBranchGoesThroughTheDoor is the half the behavioural test
// above cannot see, and its own drill is what said so: that test calls
// `survivedDefeatTx` directly, so putting the bare `applyCombatCondition` back
// at a call site left it green. It proves the helper works and says nothing
// about whether the four branches use it - which is the whole fault, and is
// v1.0.1's own lesson about `test_release_notes.py` arriving one release later
// in Go.
//
// A defeat-survival site is an `applyCombatCondition` call whose `sourceType`
// is "battle" or "fate_rescue": those are the two ways a cultivator who lost a
// fight is marked, and both must leave a heartbeat.
//
// Drill: swap either call back to `applyCombatCondition` and this names it.
func TestEveryDefeatBranchGoesThroughTheDoor(t *testing.T) {
	src, err := os.ReadFile("combat_actions.go")
	if err != nil {
		t.Fatalf("the sweep cannot read combat_actions.go, so it proves nothing: %v", err)
	}
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "combat_actions.go", src, 0)
	if err != nil {
		t.Fatalf("the sweep cannot parse combat_actions.go, so it proves nothing: %v", err)
	}

	defeatSources := map[string]bool{strconv.Quote("battle"): true, strconv.Quote("fate_rescue"): true}
	through, bare := 0, []string{}
	ast.Inspect(file, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok || len(call.Args) < 5 {
			return true
		}
		fn, ok := call.Fun.(*ast.Ident)
		if !ok {
			return true
		}
		lit, ok := call.Args[4].(*ast.BasicLit)
		if !ok || lit.Kind != token.STRING || !defeatSources[lit.Value] {
			return true
		}
		switch fn.Name {
		case "survivedDefeatTx":
			through++
		case "applyCombatCondition":
			bare = append(bare, fset.Position(call.Pos()).String()+" "+lit.Value)
		}
		return true
	})

	if through+len(bare) != 4 {
		t.Fatalf("the sweep found %d defeat-survival sites, not the four this file has; it is broken, not the tree", through+len(bare))
	}
	if len(bare) > 0 {
		t.Fatalf("%v mark a cultivator who lost a fight without leaving them a heartbeat; "+
			"every defeat branch goes through survivedDefeatTx", bare)
	}
}

// --- the treatment half -------------------------------------------------

func defeatSurvivalCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		Items: map[string]worlddata.Item{
			// The shipped `recovery_pill`: it is both the named treatment for a
			// flesh wound and the cheapest thing in the game that restores
			// vitality, which is the whole of the finding.
			"recovery_pill": {
				Name: "Recovery Pill",
				Use:  worlddata.ItemUse{Instant: worlddata.ItemInstantUse{VitalityRestore: 8}},
			},
			// The herb that treats a cultivation injury carries no instant
			// restore, and must stay untouched by this.
			"jade_life_herb": {Name: "Jade Life Herb"},
		},
	}
}

func treatCondition(t *testing.T, path, condition string) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"condition": condition, "game_minute": 500})
	mut, err := conditionTreatAction(conn, defeatSurvivalCatalog(), 77, raw)
	if err != nil {
		t.Fatalf("unexpected error treating %s: %v", condition, err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any)
}

// TestATreatmentMendsWhatItTreated. `recovery_pill` is the named treatment for
// a flesh wound *and* carries `use.instant.vitality_restore: 8`, so before this
// the pill was spent on the roll and restored nothing: one pill did one of two
// jobs and a player needed two to get back where they started - at the end of
// the losing fight that had just put them on zero.
//
// Drill: delete the restore block from `conditionTreatAction` and this fails
// with "the pill was swallowed and restored nothing".
func TestATreatmentMendsWhatItTreated(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 1)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := survivedDefeatTx(conn, 77, "flesh_wound", 1, "battle", "9", 500); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(77,'recovery_pill',1)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	conn.Close()

	before := vitalityOf(t, path, 77)
	out := treatCondition(t, path, "flesh_wound")
	after := vitalityOf(t, path, 77)

	if after <= before {
		t.Fatalf("the pill was swallowed and restored nothing: vitality %d -> %d, "+
			"while `item.use` on the same pill restores 8", before, after)
	}
	restored, _ := out["restored"].(map[string]any)
	if restored["vitality"] != int64(8) {
		t.Fatalf("the treatment must restore what the treatment item restores, not a number of its own: %v", out["restored"])
	}
}

// TestATreatmentWithNoMedicineInItRestoresNothing: the rule is that the
// treatment does what the treatment *item* does, so an item carrying no
// instant restore must move nothing. `jade_life_herb` is the real case.
func TestATreatmentWithNoMedicineInItRestoresNothing(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 4)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := applyCombatCondition(conn, 77, "meridian_damage", 1, "tribulation", "1", 500); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(77,'jade_life_herb',1)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	conn.Close()

	before := vitalityOf(t, path, 77)
	out := treatCondition(t, path, "meridian_damage")
	if after := vitalityOf(t, path, 77); after != before {
		t.Fatalf("a herb with no instant restore moved vitality %d -> %d; the item decides, not the action", before, after)
	}
	if restored, _ := out["restored"].(map[string]any); len(restored) != 0 {
		t.Fatalf("nothing was restored, so the result must say so plainly: %v", out["restored"])
	}
}
