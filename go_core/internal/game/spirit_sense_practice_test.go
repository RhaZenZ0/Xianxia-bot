package game

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// What builds the spirit sense, and when (v1.12.3).
//
// The sense is the Formation and Inscription twin of a flame, and v1.10.0 gave
// it three ways to be built. Three of the rules around them were off:
//
//   - a Forging or Alchemy craft built it, because the call in
//     `craftResolveAction` was unconditional while `craftSpiritSenseTx` (what
//     the sense gives a craft) already said "nothing for a trade it does not
//     serve";
//   - a scene action at a full stage wrote the day's scene slot *before* it
//     found the stage full, so a practice that built nothing spent one of the
//     day's few slots;
//   - a meditation that gathered nothing (a full stage) still built it, where
//     the method's own practice, two lines above, says a session that gathered
//     nothing practises nothing.

func craftOnce(t *testing.T, recipe string, materials map[string]int64, profession string) (string, map[string]any) {
	t.Helper()
	path := setupSpiritSenseDB(t)
	setupCraftAuthorityTables(t, path)
	batch4TeachRecipe(t, path, 42, recipe)
	for item, qty := range materials {
		batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,?,?)`, item, qty)
	}
	batch4Exec(t, path, `INSERT OR REPLACE INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,?,1,0,0)`, profession)
	// The highest dice: a craft that lands, so the gain is the full one and a
	// failed roll cannot be why nothing was built.
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	raw, _ := json.Marshal(map[string]any{"recipe": recipe})
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("sense-craft-%s", recipe), Operation: "craft.resolve", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatalf("%s was refused: %v", recipe, err)
	}
	return path, out.Result.(map[string]any)
}

func TestAForgingCraftBuildsNoSpiritSense(t *testing.T) {
	path, out := craftOnce(t, "Spirit-Iron Sword", map[string]int64{"spirit_iron": 3, "beast_core": 1}, "Forging")
	if out["success"] != true {
		t.Fatalf("the craft did not land: %v", out)
	}
	if gain, present := out["spirit_sense_gain"]; present {
		t.Fatalf("a Forging craft reported building the spirit sense: %v", gain)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_spirit_sense WHERE user_id=42 AND progress>0`)); n != 0 {
		t.Fatalf("a Forging craft wrote spirit-sense progress (%d row(s)); it is not a trade the sense serves", n)
	}
}

func TestAnInscriptionCraftStillBuildsIt(t *testing.T) {
	path, out := craftOnce(t, "Swift-Wind Talisman", map[string]int64{"talisman_paper": 1, "spirit_ink": 1}, "Inscription")
	gain, _ := out["spirit_sense_gain"].(map[string]any)
	if gain == nil || i64(gain["gain"]) < 1 {
		t.Fatalf("an Inscription craft built nothing: %v", out["spirit_sense_gain"])
	}
	if progress := storage.ParseInt(actionScalar(t, path, `SELECT progress FROM character_spirit_sense WHERE user_id=42`)); progress != i64(gain["gain"]) {
		t.Fatalf("the craft said it built %v and the sense holds %d", gain["gain"], progress)
	}
}

func sceneSlotsSpent(t *testing.T, path string) int64 {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT COUNT(*) FROM event_log WHERE user_id=42 AND event_type=?`, spiritSenseSceneLogType))
}

// A scene action on a full stage builds nothing, and so spends no slot of the
// day's few. The slot used to be written before the stage was found full.
func TestAFullStageSpendsNoSceneSlot(t *testing.T) {
	path := setupSpiritSenseDB(t)
	rules := spiritSenseRules(crossingCatalog(t))
	batch4Exec(t, path, `INSERT INTO character_spirit_sense(user_id,stage,progress,updated_at) VALUES(42,0,?,0)`, spiritSenseNeed(rules, 0))
	for i := int64(0); i < rules.SceneGainsPerDay+2; i++ {
		if out := senseGain(t, path, "scene", false, 100); out == nil || out["full"] != true {
			t.Fatalf("a scene action on a full stage said %v, want the stage reported full", out)
		}
	}
	if n := sceneSlotsSpent(t, path); n != 0 {
		t.Fatalf("scene actions at a full stage spent %d of the day's slots on a practice that built nothing", n)
	}
	// Once the stage has room the day's slots are all still there.
	batch4Exec(t, path, `UPDATE character_spirit_sense SET progress=0 WHERE user_id=42`)
	built := int64(0)
	for i := int64(0); i < rules.SceneGainsPerDay+2; i++ {
		if senseGain(t, path, "scene", false, 100) != nil {
			built++
		}
	}
	if built != rules.SceneGainsPerDay {
		t.Fatalf("after settling, the day's scene actions built the sense %d times, want %d", built, rules.SceneGainsPerDay)
	}
	if n := sceneSlotsSpent(t, path); n != rules.SceneGainsPerDay {
		t.Fatalf("%d scene slots spent for %d gains", n, rules.SceneGainsPerDay)
	}
}

// A meditation that gathered nothing builds nothing: the rule the method's
// practice states a few lines above it in cultivationTrain.
func TestAMeditationThatGatheredNothingBuildsNoSense(t *testing.T) {
	// A session draws a variance and may roll a clash or a deviation; the
	// highest dice make the one that has room gather and leave nothing to
	// chance in what this asserts.
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	path := setupCultivationDB(t)
	batch4Exec(t, path, characterSpiritSenseDDL)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	cost, err := phaseCost(catalog.Realms, 1, 3)
	if err != nil {
		t.Fatal(err)
	}
	// The stage is full: there is no room, so the session gathers nothing.
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=? WHERE user_id=42`, cost)
	trained := batch4Result(t, batch4Apply(t, path, world, "cultivation.train", 1, map[string]any{"game_minute": 600}))
	if trained["stage_full"] != true || i64(trained["gain"]) != 0 {
		t.Fatalf("the fixture's stage is not full (gain %v, stage_full %v); the test cannot say what it means to", trained["gain"], trained["stage_full"])
	}
	if gain, present := trained["spirit_sense_gain"]; present {
		t.Fatalf("a session that gathered nothing reported building the sense: %v", gain)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_spirit_sense WHERE user_id=42 AND progress>0`)); n != 0 {
		t.Fatalf("a session that gathered nothing built spirit-sense progress (%d row(s))", n)
	}
	// And a session with room still builds it.
	batch4Exec(t, path, `UPDATE characters SET cultivation=0 WHERE user_id=42`)
	clearCooldowns(t, path, 42)
	trained = batch4Result(t, batch4Apply(t, path, world, "cultivation.train", 2, map[string]any{"game_minute": 700}))
	if i64(trained["gain"]) < 1 {
		t.Fatalf("a session with room gathered nothing: %v", trained["gain"])
	}
	if built, _ := trained["spirit_sense_gain"].(map[string]any); built == nil || i64(built["gain"]) < 1 {
		t.Fatalf("a session that gathered %v built no spirit sense: %v", trained["gain"], trained["spirit_sense_gain"])
	}
}
