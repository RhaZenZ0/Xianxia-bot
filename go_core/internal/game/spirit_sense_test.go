package game

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The spirit sense (v1.10.0): built, never captured. The table carries
// production's DDL, foreign key included.
const characterSpiritSenseDDL = `CREATE TABLE character_spirit_sense (
	user_id INTEGER PRIMARY KEY, stage INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
	updated_at REAL NOT NULL,
	FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`

func setupSpiritSenseDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, characterSpiritSenseDDL)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS event_log (
		id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT NOT NULL,
		payload_json TEXT NOT NULL, created_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)`)
	// Spirit 8: every gain is its roster value plus 8/4 = 2.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"spirit":8,"will":3,"body":3,"insight":3}' WHERE user_id=42`)
	return path
}

func senseGain(t *testing.T, path, source string, half bool, minute int64) map[string]any {
	t.Helper()
	var out map[string]any
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		out = spiritSenseGainTx(conn, crossingCatalog(t), 42, source, half, minute, 1)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	return out
}

func senseRow(t *testing.T, path string) (int64, int64) {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT stage FROM character_spirit_sense WHERE user_id=42`)),
		i64(actionScalar(t, path, `SELECT progress FROM character_spirit_sense WHERE user_id=42`))
}

func TestTheSpiritSenseRisesFromOneToFive(t *testing.T) {
	rules := spiritSenseRules(crossingCatalog(t))
	if rules.MaxStage == 0 || rules.StageProgress == 0 {
		t.Fatalf("the roster did not parse; the content is broken, not the rule")
	}
	if got := spiritSenseBonusAt(rules, 0); got != 0 {
		t.Errorf("an unbuilt sense gives %d, want 0", got)
	}
	if got := spiritSenseBonusAt(rules, 1); got != rules.MinBonus {
		t.Errorf("the first stage gives %d, want %d", got, rules.MinBonus)
	}
	if got := spiritSenseBonusAt(rules, rules.MaxStage); got != rules.MaxBonus {
		t.Errorf("the last stage gives %d, want %d", got, rules.MaxBonus)
	}
	if spiritSenseOpensTopGrade(rules, rules.MaxStage-1) || !spiritSenseOpensTopGrade(rules, rules.MaxStage) {
		t.Errorf("only a fully built sense opens the top grade")
	}
}

func TestPracticeBuildsTheSenseAndSpiritQuickensIt(t *testing.T) {
	path := setupSpiritSenseDB(t)
	rules := spiritSenseRules(crossingCatalog(t))
	if out := senseGain(t, path, "craft", false, 0); out == nil || i64(out["gain"]) != rules.Gains["craft"]+2 {
		t.Fatalf("a craft built %v, want %d (the roster's gain plus spirit 8/4)", out, rules.Gains["craft"]+2)
	}
	if out := senseGain(t, path, "craft", true, 0); out == nil || i64(out["gain"]) != (rules.Gains["craft"]+2)/2 {
		t.Fatalf("a failed craft built %v, want half", out)
	}
	if out := senseGain(t, path, "meditation", false, 0); out == nil || i64(out["gain"]) != rules.Gains["meditation"]+2 {
		t.Fatalf("a meditation built %v", out)
	}
	for i := 0; i < 20; i++ {
		senseGain(t, path, "craft", false, 0)
	}
	if _, progress := senseRow(t, path); progress != spiritSenseNeed(rules, 0) {
		t.Fatalf("practice banked %d past a full stage; it stops at %d until the stage is settled", progress, spiritSenseNeed(rules, 0))
	}
}

func TestSceneActionsBuildItOnlyAFewTimesAWorldDay(t *testing.T) {
	path := setupSpiritSenseDB(t)
	rules := spiritSenseRules(crossingCatalog(t))
	gains := 0
	for i := int64(0); i < rules.SceneGainsPerDay+3; i++ {
		if senseGain(t, path, "scene", false, 100) != nil {
			gains++
		}
	}
	if int64(gains) != rules.SceneGainsPerDay {
		t.Fatalf("scene actions built the sense %d times in one world day, want %d", gains, rules.SceneGainsPerDay)
	}
	if senseGain(t, path, "scene", false, 100+1440) == nil {
		t.Fatalf("a scene action on the next world day built nothing")
	}
}

func TestSettlingAStageTakesAFullStageAndQi(t *testing.T) {
	path := setupSpiritSenseDB(t)
	rules := spiritSenseRules(crossingCatalog(t))
	settle := func() (map[string]any, error) {
		var out map[string]any
		err := crossingApply(t, path, func(conn *storage.Conn) error {
			m, err := spiritSenseSettleAction(conn, crossingCatalog(t), 42, json.RawMessage(`{}`))
			out, _ = m.Result.(map[string]any)
			return err
		})
		return out, err
	}
	if _, err := settle(); err == nil || !strings.Contains(err.Error(), "toward its next stage") {
		t.Fatalf("an empty sense settled: %v", err)
	}
	batch4Exec(t, path, `INSERT INTO character_spirit_sense(user_id,stage,progress,updated_at) VALUES(42,0,40,0)
		ON CONFLICT(user_id) DO UPDATE SET progress=40`)
	out, err := settle()
	if err != nil {
		t.Fatalf("a full stage would not settle: %v", err)
	}
	if i64(out["stage"]) != 1 || i64(out["bonus"]) != rules.MinBonus {
		t.Fatalf("settling gave %+v, want stage 1 at +%d", out, rules.MinBonus)
	}
	batch4Exec(t, path, `UPDATE character_spirit_sense SET stage=9,progress=0`)
	if _, err := settle(); err == nil || !strings.Contains(err.Error(), "fully built") {
		t.Fatalf("a fully built sense settled again: %v", err)
	}
}

func TestAFullyBuiltSenseOpensTranscendentForItsTradesOnly(t *testing.T) {
	path := setupSpiritSenseDB(t)
	batch4Exec(t, path, `INSERT INTO character_spirit_sense(user_id,stage,progress,updated_at) VALUES(42,9,0,0)`)
	var formBonus, alchemyBonus int64
	var formOpens, alchemyOpens bool
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		formBonus, formOpens = craftSpiritSenseTx(conn, crossingCatalog(t), 42, "Formation")
		alchemyBonus, alchemyOpens = craftSpiritSenseTx(conn, crossingCatalog(t), 42, "Alchemy")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if !formOpens || formBonus != 5 {
		t.Fatalf("a fully built sense gave Formation +%d, opens=%v; want +5 and the top grade", formBonus, formOpens)
	}
	if alchemyOpens || alchemyBonus != 0 {
		t.Fatalf("the spirit sense reached Alchemy (+%d, opens=%v); that is a flame's trade", alchemyBonus, alchemyOpens)
	}
}

func TestTheSenseNeverBreaksAnActionBeforeItsTableExists(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	if out := senseGain(t, path, "craft", false, 0); out != nil {
		t.Fatalf("a missing table still built progress: %v", out)
	}
}

func TestTheSpiritSenseLeverIsHeldAndAudited(t *testing.T) {
	path := setupSpiritSenseDB(t)
	if _, err := applyAdminRaw(t, path, "admin.player.set_spirit_sense", 1, map[string]any{"user_id": 42, "stage": 10, "progress": 0}); err == nil {
		t.Fatalf("a stage past the top was set")
	}
	if _, err := applyAdminRaw(t, path, "admin.player.set_spirit_sense", 1, map[string]any{"user_id": 42, "stage": 0, "progress": 999}); err == nil {
		t.Fatalf("progress past a stage's need was set")
	}
	if _, err := applyAdminRaw(t, path, "admin.player.set_spirit_sense", 1, map[string]any{"user_id": 42, "stage": 3, "progress": 10, "reason": "test"}); err != nil {
		t.Fatalf("a valid set was refused: %v", err)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.player.set_spirit_sense'`)); n != 1 {
		t.Fatalf("the lever wrote %d audit rows, want 1", n)
	}
}

// The three ways the sense is built are paid where they happen, read by AST:
// the helper's own tests pass against a tree nothing calls it from.
func TestEveryPracticeBuildsTheSenseWhereItHappens(t *testing.T) {
	want := map[string]string{"craftResolveAction": `"craft"`, "cultivationTrain": `"meditation"`, "resolveSceneAction": `"scene"`}
	found := map[string]bool{}
	fset := token.NewFileSet()
	for _, file := range []string{"crafting_actions.go", "cultivation_actions.go", "check_scene_actions.go"} {
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
				if ident, ok := call.Fun.(*ast.Ident); ok && ident.Name == "spiritSenseGainTx" && len(call.Args) > 3 {
					if lit, ok := call.Args[3].(*ast.BasicLit); ok && lit.Value == want[fn.Name.Name] {
						found[fn.Name.Name] = true
					}
				}
				return true
			})
		}
	}
	for fn, source := range want {
		if !found[fn] {
			t.Errorf("%s no longer builds the spirit sense (%s)", fn, source)
		}
	}
}
