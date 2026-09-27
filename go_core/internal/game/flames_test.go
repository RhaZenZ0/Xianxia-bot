package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// Flames (v1.10.0). The table carries production's DDL, foreign key included:
// a fixture that cannot fail the way production fails is not testing it.
const characterFlamesDDL = `CREATE TABLE character_flames (
	user_id INTEGER NOT NULL, flame_id TEXT NOT NULL, refinement INTEGER NOT NULL DEFAULT 0,
	bound INTEGER NOT NULL DEFAULT 0, captured_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
	PRIMARY KEY(user_id,flame_id),
	FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`

func setupFlameDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, characterFlamesDDL)
	batch4Exec(t, path, `UPDATE characters SET location='Emberforge Forge Terraces', realm_index=2 WHERE user_id=42`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)`)
	return path
}

func flameAct(t *testing.T, path, op, flameID string) (map[string]any, error) {
	t.Helper()
	catalog := crossingCatalog(t)
	raw, _ := json.Marshal(map[string]any{"flame_id": flameID})
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		var m authoritativeMutation
		var err error
		switch op {
		case "capture":
			m, err = flameCaptureAction(conn, catalog, 42, raw)
		case "refine":
			m, err = flameRefineAction(conn, catalog, 42, raw)
		case "bind":
			m, err = flameBindAction(conn, catalog, 42, raw)
		}
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

func TestAFlameIsCapturedWhereItBurnsAndBound(t *testing.T) {
	path := setupFlameDB(t)
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	out, err := flameAct(t, path, "capture", "")
	if err != nil {
		t.Fatalf("a capture at Emberforge's terraces was refused: %v", err)
	}
	if out["flame_id"] != "earth_heart_fire" || out["success"] != true || out["bound"] != true {
		t.Fatalf("the capture did not take and bind the Earth-Heart Fire: %+v", out)
	}
	if _, err := flameAct(t, path, "capture", ""); err == nil || !strings.Contains(err.Error(), "already hold") {
		t.Fatalf("the same flame was captured twice: %v", err)
	}
}

func TestAFlameIsNotCapturedElsewhereOrTooEarly(t *testing.T) {
	path := setupFlameDB(t)
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	if _, err := flameAct(t, path, "capture", ""); err == nil || !strings.Contains(err.Error(), "no flame burns here") {
		t.Fatalf("a flame was captured away from its terraces: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Emberforge Forge Terraces', realm_index=1 WHERE user_id=42`)
	if _, err := flameAct(t, path, "capture", ""); err == nil || !strings.Contains(err.Error(), "below realm") {
		t.Fatalf("a realm-0 cultivator captured a flame: %v", err)
	}
}

func TestAFailedCaptureBurnsTheMeridians(t *testing.T) {
	path := setupFlameDB(t)
	// The fixture cultivator is strong enough that no roll could miss.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"spirit":1,"will":1,"body":1,"insight":1}' WHERE user_id=42`)
	defer gamerng.UseRoller(func(int) int { return 0 })()
	out, err := flameAct(t, path, "capture", "")
	if err != nil {
		t.Fatalf("a failed capture errored rather than burning: %v", err)
	}
	if out["success"] != false || out["scorched"] == nil {
		t.Fatalf("a failed capture left the meridians whole: %+v", out)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_flames`)); n != 0 {
		t.Fatalf("a failed capture still wrote the flame (%d rows)", n)
	}
}

func TestRefiningRaisesTheFlameForItsMaterialsAndStopsAtTheTop(t *testing.T) {
	path := setupFlameDB(t)
	batch4Exec(t, path, `INSERT INTO character_flames(user_id,flame_id,refinement,bound,captured_game_minute,updated_at) VALUES(42,'earth_heart_fire',0,1,0,0)`)
	if _, err := flameAct(t, path, "refine", "earth_heart_fire"); err == nil || !strings.Contains(err.Error(), "missing materials") {
		t.Fatalf("a refinement with an empty bag was not refused on its materials: %v", err)
	}
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'beast_core',100),(42,'spirit_iron',100)
		ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=100`)
	out, err := flameAct(t, path, "refine", "earth_heart_fire")
	if err != nil {
		t.Fatalf("a refinement with the materials was refused: %v", err)
	}
	if i64(out["refinement"]) != 1 {
		t.Fatalf("refining left the flame at %v, want 1", out["refinement"])
	}
	if left := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='beast_core'`)); left != 99 {
		t.Fatalf("the first refinement spent %d beast cores, want 1", 100-left)
	}
	batch4Exec(t, path, `UPDATE character_flames SET refinement=9`)
	if _, err := flameAct(t, path, "refine", "earth_heart_fire"); err == nil || !strings.Contains(err.Error(), "fully refined") {
		t.Fatalf("a fully refined flame was refined again: %v", err)
	}
}

func TestBindingMakesOneFlameTheOneACraftReads(t *testing.T) {
	path := setupFlameDB(t)
	batch4Exec(t, path, `INSERT INTO character_flames(user_id,flame_id,refinement,bound,captured_game_minute,updated_at) VALUES
		(42,'earth_heart_fire',0,1,0,0),(42,'crimson_lotus_flame',9,0,0,0)`)
	if _, err := flameAct(t, path, "bind", "crimson_lotus_flame"); err != nil {
		t.Fatal(err)
	}
	var bonus int64
	var name string
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		bonus, _, name = craftFlameTx(conn, crossingCatalog(t), 42, "Forging")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if name != "Crimson Lotus Flame" || bonus != 3 {
		t.Fatalf("the craft read %q at +%d, want the Crimson Lotus Flame at its full +3", name, bonus)
	}
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		bonus, _, _ = craftFlameTx(conn, crossingCatalog(t), 42, "Formation")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if bonus != 0 {
		t.Fatalf("a flame steadied a Formation roll (+%d); flames serve Alchemy and Forging only", bonus)
	}
}

func TestAFlameNeverBreaksACraftBeforeTheTableExists(t *testing.T) {
	path := setupBatch4AuthorityDB(t) // no character_flames at all
	var bonus int64
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		bonus, _, _ = craftFlameTx(conn, crossingCatalog(t), 42, "Alchemy")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if bonus != 0 {
		t.Fatalf("a missing table gave a bonus of %d", bonus)
	}
}

// The owner's call: a flame is what opens Transcendent. Without one a rank-6
// crafter's flawless margin-12 craft stops at Superior; with a fully refined
// heavenly flame bound it is Transcendent. The shipped ladder is read, not a
// fixture's, because the rung's rank and flame rank are content.
func TestAFlameIsWhatOpensTranscendent(t *testing.T) {
	catalog := crossingCatalog(t)
	top := len(catalog.ItemGrades.Grades) - 1
	if catalog.ItemGrades.Grades[top].FlameMinRank == nil {
		t.Fatalf("the top rung carries no flame_min_rank; the content is broken, not the rule")
	}
	without, reached := craftGradeIndex(catalog, "flawless", 12, 6, false)
	if reached != top || without == top {
		t.Fatalf("without a flame a rank-6 margin-12 craft made rung %d (reached %d); Transcendent should need a flame", without, reached)
	}
	with, _ := craftGradeIndex(catalog, "flawless", 12, 6, true)
	if with != top {
		t.Fatalf("a flame that opens the top grade still capped a rank-6 craft at rung %d", with)
	}
	if lower, _ := craftGradeIndex(catalog, "flawless", 12, 5, true); lower == top {
		t.Fatalf("a flame opened Transcendent below its flame_min_rank")
	}
}

func TestOnlyAFullyRefinedHeavenlyFlameOpensIt(t *testing.T) {
	rules := flameRules(crossingCatalog(t))
	opens := 0
	for id, def := range rules.Flames {
		if flameOpensTopGrade(def, rules.MaxRefinement-1, rules.MaxRefinement) {
			t.Errorf("%s opens the top grade before it is fully refined", id)
		}
		if flameOpensTopGrade(def, rules.MaxRefinement, rules.MaxRefinement) {
			opens++
		}
		if got := flameBonusAt(def, 0, rules.MaxRefinement); got != def.BaseBonus {
			t.Errorf("%s at refinement 0 gives %d, want its base %d", id, got, def.BaseBonus)
		}
		if got := flameBonusAt(def, rules.MaxRefinement, rules.MaxRefinement); got != def.MaxBonus {
			t.Errorf("%s fully refined gives %d, want its max %d", id, got, def.MaxBonus)
		}
	}
	if opens == 0 {
		t.Fatalf("no flame in the roster opens the top grade, so Transcendent is unreachable again")
	}
}

// Every flame is captured somewhere a cultivator can stand, in the world it
// belongs to, and refined with items the catalogue carries.
func TestEveryFlameCanBeReachedAndRefined(t *testing.T) {
	catalog := crossingCatalog(t)
	rules := flameRules(catalog)
	if len(rules.Flames) == 0 {
		t.Fatalf("the roster is empty; the content parse is broken, not the tree")
	}
	for id, def := range rules.Flames {
		loc, ok := catalog.Locations[def.Location]
		if !ok {
			t.Errorf("%s is captured at %q, which is not a location", id, def.Location)
			continue
		}
		if loc.World != def.World {
			t.Errorf("%s belongs to %s but burns at %s in %s", id, def.World, def.Location, loc.World)
		}
		for item := range def.RefineItems {
			if _, _, ok := itemDef(catalog, item); !ok {
				t.Errorf("%s is refined with %q, which the catalogue does not carry", id, item)
			}
		}
	}
}

// A GM's grant is held to the roster and the range, and audited (rule 6).
func TestTheGrantFlameLeverIsHeldAndAudited(t *testing.T) {
	path := setupFlameDB(t)
	if _, err := applyAdminRaw(t, path, "admin.player.grant_flame", 1, map[string]any{"user_id": 42, "flame_id": "no_such_fire", "refinement": 0}); err == nil {
		t.Fatalf("a flame the roster does not carry was granted")
	}
	if _, err := applyAdminRaw(t, path, "admin.player.grant_flame", 1, map[string]any{"user_id": 42, "flame_id": "earth_heart_fire", "refinement": 10}); err == nil {
		t.Fatalf("a refinement past the top was granted")
	}
	if _, err := applyAdminRaw(t, path, "admin.player.grant_flame", 1, map[string]any{"user_id": 42, "flame_id": "nine_heavens_sun_flame", "refinement": 9, "reason": "test"}); err != nil {
		t.Fatalf("a valid grant was refused: %v", err)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.player.grant_flame'`)); n != 1 {
		t.Fatalf("the grant wrote %d audit rows, want 1", n)
	}
	if bound := i64(actionScalar(t, path, `SELECT bound FROM character_flames WHERE user_id=42`)); bound != 1 {
		t.Fatalf("a first flame granted was not bound")
	}
}
