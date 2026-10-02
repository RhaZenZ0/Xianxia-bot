package game

import (
	"testing"

	"xianxia/core/internal/gamerng"
)

// What a control technique does in a fight is read off its effect's tags
// (v1.18.0). Until the ten other Laws had a technique, the battle switched on
// two ids - `spatial_lockdown` held and `spatial_strangulation` crushed - and
// every other id was "law dominance" and nothing. A control effect tagged
// `damage` crushes; any other control effect holds. These drive the shipped
// catalogue, so a technique authored tomorrow is held to the same rule.

func taggedControlDB(t *testing.T, law string, realm int64) string {
	t.Helper()
	path := setupLawControlDB(t, true)
	batch4Exec(t, path, `INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(901,'`+law+`',60,0,0)`)
	batch4Exec(t, path, `UPDATE characters SET realm_index=`+itoa(realm)+` WHERE user_id=901`)
	return path
}

// Both technique tests lend the dice (every die its highest face) rather
// than trusting the fixture's attributes of 500 to make the check certain:
// `TestATestThatAssertsARollLandedLendsTheDice` holds a test that drives a
// roll and then asserts it landed to that rule, and the rule is right - a
// stacked fixture is a probability, not a proof.
func TestAControlEffectTaggedDamageCrushesLikeTheStrangulation(t *testing.T) {
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	catalog := crossingCatalog(t)
	technique := catalog.LawSystem.Techniques["ember_brand"]
	effect, _, err := specialEffectPayload(catalog, technique.Effect)
	if err != nil || !effectHasTag(effect, "damage") || !lawEffectIsControl(effect) {
		t.Fatalf("ember_brand is no longer a damage-tagged control technique in the content (%v); the fixture is wrong, not the rule", err)
	}
	path := taggedControlDB(t, technique.Law, int64(technique.MinRealmIndex))
	out, err := shippedTechnique(t, path, "ember_brand")
	if err != nil {
		t.Fatalf("ember_brand was refused with every floor met: %v", err)
	}
	if !bval(out["roll"].(map[string]any), "success") {
		t.Fatal("the fixture's attributes of 500 must make the technique certain")
	}
	if dmg, _ := out["damage_dealt"].(int64); dmg < 2 {
		t.Fatalf("a damage-tagged control technique dealt %v; it should crush as the strangulation does", out["damage_dealt"])
	}
	if out["effect_target"] != "opponent" {
		t.Fatalf("the brand went to %v, want the opponent", out["effect_target"])
	}
	if mods := opponentModsJSON(t, path); mods["body"] != -2 {
		t.Fatalf("the brand's body -2 did not reach the opponent: %v", mods)
	}
}

func TestAControlEffectWithoutDamageHoldsLikeTheLockdown(t *testing.T) {
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	catalog := crossingCatalog(t)
	technique := catalog.LawSystem.Techniques["tide_bind"]
	effect, _, err := specialEffectPayload(catalog, technique.Effect)
	if err != nil || effectHasTag(effect, "damage") || !lawEffectIsControl(effect) {
		t.Fatalf("tide_bind is no longer a holding control technique in the content (%v); the fixture is wrong, not the rule", err)
	}
	path := taggedControlDB(t, technique.Law, int64(technique.MinRealmIndex))
	out, err := shippedTechnique(t, path, "tide_bind")
	if err != nil {
		t.Fatalf("tide_bind was refused with every floor met: %v", err)
	}
	if _, dealt := out["damage_dealt"]; dealt {
		t.Fatalf("a holding technique dealt damage: %v", out["damage_dealt"])
	}
	if turns, _ := out["suppressed_turns"].(int64); turns < 1 {
		t.Fatalf("a holding technique suppressed nothing: %v", out)
	}
}

func TestEveryLawCarriesAControlTechniqueAndADomain(t *testing.T) {
	catalog := crossingCatalog(t)
	control, domain := map[string]int{}, map[string]int{}
	for key, technique := range catalog.LawSystem.Techniques {
		if technique.Effect == "" {
			continue
		}
		effect, _, err := specialEffectPayload(catalog, technique.Effect)
		if err != nil {
			t.Fatalf("%s: %v", key, err)
		}
		if lawEffectIsControl(effect) {
			control[technique.Law]++
		} else {
			domain[technique.Law]++
		}
	}
	for law := range catalog.LawSystem.Laws {
		if control[law] == 0 {
			t.Errorf("the Law of %s carries no control technique", law)
		}
		if domain[law] == 0 {
			t.Errorf("the Law of %s carries no Domain", law)
		}
	}
	if len(catalog.LawSystem.Laws) < 11 {
		t.Fatalf("only %d Laws; the content read is broken, not the tree", len(catalog.LawSystem.Laws))
	}
}
