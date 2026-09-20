package game

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The capstone that named an effect nobody wrote (v1.0.0-rc.58).
//
// `law_system.techniques.world_collapse` declared `"effect": "world_collapse"`
// at requires_stage 5 and min_realm_index 30 - the deepest thing on the game's
// ladder - and `special_effects` carried seven entries, none of them that one.
// `lawTechniqueAction` refuses an effect the catalogue does not carry, quite
// correctly, so a realm-30 Dao Saint with Space Law at Essence/Origin and a
// stabilized personal world pressed the capstone and was told the engine did
// not know what it does.
//
// Nothing caught it because the test that exists to prove the capstone works
// declared its own `world_collapse` in its own fixture, with an effect the
// fixture provides, at RequiresStage 2 and MinRealmIndex 3. It asserted the
// capstone *succeeds*; production hard-errored. A fixture that cannot fail the
// way production fails is not testing production - the rule this repo already
// states, arriving here through the front door.
//
// So every test in this file drives the *shipped* catalogue. The synthetic
// fixture next door keeps the mechanism tests, which is right: pinning a stage
// gate to production content would break it on every content edit. What is not
// right is a fixture asserting the opposite of what production does.

// Every id a technique names must be one the catalogue carries. This fails
// against the tree as it stood before rc.58, naming the effect and listing
// what there was instead.
func TestEveryLawTechniquesEffectResolves(t *testing.T) {
	catalog := shippedCatalog(t)
	if len(catalog.LawSystem.Techniques) < 5 {
		t.Fatalf("the technique roster all but disappeared: %d", len(catalog.LawSystem.Techniques))
	}
	if len(catalog.SpecialEffects) < 8 {
		t.Fatalf("the effect catalogue all but disappeared: %d", len(catalog.SpecialEffects))
	}
	known := make([]string, 0, len(catalog.SpecialEffects))
	for id := range catalog.SpecialEffects {
		known = append(known, id)
	}
	sort.Strings(known)
	for key, technique := range catalog.LawSystem.Techniques {
		if strings.TrimSpace(technique.Effect) == "" {
			continue // `folded_step` grants nothing, deliberately.
		}
		if _, ok := catalog.SpecialEffects[technique.Effect]; !ok {
			t.Fatalf("law technique %s names effect %q, which special_effects does not carry (it carries: %s)",
				key, technique.Effect, strings.Join(known, ", "))
		}
	}
}

// The second of the two places in the tree that names an effect id. It is a
// package-level map rather than a literal inside `abode.focus` so this can
// walk it.
func TestEveryFacilityEffectResolves(t *testing.T) {
	catalog := shippedCatalog(t)
	if len(abodeFacilityEffects) == 0 {
		t.Fatal("the facility map is empty; this gate would pass on anything")
	}
	for facility, effectID := range abodeFacilityEffects {
		if _, _, err := specialEffectPayload(catalog, effectID); err != nil {
			t.Fatalf("facility %s names effect %q: %v", facility, effectID, err)
		}
	}
}

// An effect may not carry a stat no rule reads. This is the content-side half
// of `modifier_vocabulary_test.go`, and it is what stops the next effect being
// authored with a dead number in it.
func TestEverySpecialEffectIsShapedLikeOne(t *testing.T) {
	catalog := shippedCatalog(t)
	readable := authoredModifierStats(t)
	for id, effect := range catalog.SpecialEffects {
		if strings.TrimSpace(fmt.Sprint(effect["name"])) == "" {
			t.Errorf("%s has no name", id)
		}
		if strings.TrimSpace(fmt.Sprint(effect["category"])) == "" {
			t.Errorf("%s has no category", id)
		}
		modifiers, _ := effect["modifiers"].([]any)
		if len(modifiers) == 0 {
			t.Errorf("%s grants nothing", id)
			continue
		}
		for _, raw := range modifiers {
			m, _ := raw.(map[string]any)
			stat := strings.TrimSpace(fmt.Sprint(m["stat"]))
			if _, ok := readable[stat]; !ok {
				t.Errorf("%s grants %q, which is not in the modifier vocabulary", id, stat)
			}
		}
	}
}

// What "capstone" means, stated in numbers rather than in prose: the realm-30
// technique must be worth strictly more than the realm-14 one it sits above.
func TestTheCapstoneIsStrictlyAboveTheDomainBelowIt(t *testing.T) {
	catalog := shippedCatalog(t)
	below, _, err := specialEffectPayload(catalog, "space_domain")
	if err != nil {
		t.Fatal(err)
	}
	above, _, err := specialEffectPayload(catalog, "world_collapse")
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(above["severity"]) <= storage.ParseInt(below["severity"]) {
		t.Fatalf("world_collapse severity %v is not above space_domain's %v",
			above["severity"], below["severity"])
	}
	stats := func(effect map[string]any) map[string]float64 {
		out := map[string]float64{}
		modifiers, _ := effect["modifiers"].([]any)
		for _, raw := range modifiers {
			m, _ := raw.(map[string]any)
			value, _ := m["value"].(float64)
			out[strings.TrimSpace(fmt.Sprint(m["stat"]))] = value
		}
		return out
	}
	low, high := stats(below), stats(above)
	for stat, value := range low {
		got, ok := high[stat]
		if !ok {
			t.Errorf("world_collapse drops %s, which space_domain grants", stat)
			continue
		}
		if got <= value {
			t.Errorf("world_collapse grants %s %v, not above space_domain's %v", stat, got, value)
		}
	}
}

// The behavioural one: the cultivator the capstone was written for can use it.
// Against the tree before rc.58 this fails with the production error exactly -
// `law technique world_collapse names an unknown effect "world_collapse"`.
func TestTheCapstoneCanBeUsedByTheCultivatorItWasWrittenFor(t *testing.T) {
	catalog := shippedCatalog(t)
	technique, ok := catalog.LawSystem.Techniques["world_collapse"]
	if !ok {
		t.Fatal("world_collapse is not in the shipped technique roster")
	}
	path := setupLawTechniqueDB(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=? WHERE user_id=42`, int64(technique.MinRealmIndex))
	batch4Exec(t, path, `DELETE FROM law_progress WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(42,?,100,0,0)`, technique.Law)

	// Both halves of the gate that used to live in the fixture next door.
	if _, err := lawTechniqueApplyWith(t, path, catalog, "world_collapse"); err == nil {
		t.Fatal("world collapse ran without a personal world")
	}
	batch4Exec(t, path, `INSERT INTO personal_worlds(user_id,stability) VALUES(42,100)`)

	mut, err := lawTechniqueApplyWith(t, path, catalog, "world_collapse")
	if err != nil {
		t.Fatalf("the capstone was refused to the cultivator it was written for: %v", err)
	}
	out, _ := mut.Result.(map[string]any)
	if got := fmt.Sprint(out["effect_id"]); got != "world_collapse" {
		t.Fatalf("effect_id=%q", got)
	}

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT name,effect_json FROM active_effects WHERE user_id=42 AND effect_key='world_collapse'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) != 1 {
		t.Fatalf("active_effects rows for the capstone = %d", len(res.Rows))
	}
	if body := fmt.Sprint(res.Rows[0][1]); strings.Contains(body, "null") && len(body) < 10 {
		t.Fatalf("the effect row carries nothing: %s", body)
	}
	if name := fmt.Sprint(res.Rows[0][0]); name == "" || name == "world_collapse" {
		t.Fatalf("the row was not named from the effect: %q", name)
	}
}

// A control technique is cast at somebody, and out of a battle there is
// nobody. This path writes the effect row on the *user*, so before rc.58 the
// only thing stopping a cultivator applying `agility -3, escape_bonus -5` to
// themselves for two hours was a check in `app/bot/commands/law.py` - a bound
// that lives in the client is not a bound.
func TestAControlTechniqueIsRefusedOutsideABattle(t *testing.T) {
	catalog := shippedCatalog(t)
	control := []string{}
	for key, technique := range catalog.LawSystem.Techniques {
		if technique.Effect == "" {
			continue
		}
		effect, _, err := specialEffectPayload(catalog, technique.Effect)
		if err != nil {
			continue
		}
		if strings.TrimSpace(fmt.Sprint(effect["category"])) == lawControlCategory {
			control = append(control, key)
		}
	}
	sort.Strings(control)
	if len(control) < 2 {
		t.Fatalf("the shipped content carries %d control techniques; this gate would match nothing", len(control))
	}

	for _, key := range control {
		technique := catalog.LawSystem.Techniques[key]
		path := setupLawTechniqueDB(t)
		batch4Exec(t, path, `UPDATE characters SET realm_index=? WHERE user_id=42`, int64(technique.MinRealmIndex))
		batch4Exec(t, path, `DELETE FROM law_progress WHERE user_id=42`)
		batch4Exec(t, path, `INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(42,?,100,0,0)`, technique.Law)

		if _, err := lawTechniqueApplyWith(t, path, catalog, key); err == nil {
			t.Fatalf("a self-debuff was applied out of battle: %s carries %s", key, technique.Effect)
		} else if !strings.Contains(err.Error(), "target") {
			t.Fatalf("%s was refused for the wrong reason: %v", key, err)
		}
		if got := lawEffectRows(t, path); got != 0 {
			t.Fatalf("%s wrote %d effect rows on the way to being refused", key, got)
		}
	}
}

// The catalogue has one door, and `TestThePurseHasOneDoor` is the shape.
// There were two lookups and they disagreed about what an unknown id means:
// the law path refused it, the abode path indexed the map bare, got a nil
// value, wrote the literal `null` into effect_json and succeeded applying
// nothing.
func TestTheSpecialEffectsCatalogueHasOneDoor(t *testing.T) {
	offenders := goProductionIndexesOf(t, "SpecialEffects", "specialEffectPayload")
	if len(offenders) > 0 {
		t.Fatalf("special_effects is indexed outside its one door:\n  %s", strings.Join(offenders, "\n  "))
	}
}
