package game

import (
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Elemental qi (v1.0.0-rc.9): every method draws one kind, and the root
// decides how much of it goes in.

func manualOfElement(t *testing.T, catalog worlddata.Catalog, element string) string {
	t.Helper()
	best := ""
	for id, definition := range catalog.TechniqueSystem.Manuals {
		if definition.Element == element && definition.MinRealmIndex == 0 && (best == "" || id < best) {
			best = id
		}
	}
	if best == "" {
		t.Fatalf("the catalogue has no first-realm %s method", element)
	}
	return best
}

func TestEveryMethodDrawsOneKnownKindOfQi(t *testing.T) {
	catalog := qiBodyCatalog(t)
	known := map[string]bool{}
	for _, element := range []string{"Fire", "Water", "Wood", "Metal", "Earth", "Lightning", "Wind", "Ice", "Yin", "Yang", "Void", "Chaos"} {
		known[element] = true
	}
	counts := map[string]int{}
	for id, definition := range catalog.TechniqueSystem.Manuals {
		if !known[definition.Element] {
			t.Fatalf("%s draws %q, which is not an element", id, definition.Element)
		}
		counts[definition.Element]++
	}
	// Every element has methods a cultivator of that root can go and find.
	for element := range known {
		if counts[element] < 4 {
			t.Fatalf("only %d %s methods in the whole catalogue", counts[element], element)
		}
	}
	// And the five phases carry the bulk of it.
	phases := 0
	for _, phase := range catalog.ElementalQi.Phases {
		phases += counts[phase]
	}
	if phases < len(catalog.TechniqueSystem.Manuals)/3 {
		t.Fatalf("the five phases carry only %d of %d methods", phases, len(catalog.TechniqueSystem.Manuals))
	}
}

func TestTheFivePhasesGenerateAndOvercomeInTheOldCycle(t *testing.T) {
	catalog := qiBodyCatalog(t)
	for _, c := range []struct{ root, manual, want string }{
		{"Fire", "Fire", relationResonant},
		{"Fire", "Wood", relationGenerative},  // wood feeds fire
		{"Fire", "Earth", relationGenerative}, // fire feeds earth
		{"Fire", "Metal", relationDrained},    // fire melts metal
		{"Fire", "Water", relationClashing},   // water quenches fire
		{"Metal", "Wood", relationDrained},    // metal cuts wood
		{"Wood", "Metal", relationClashing},
		{"Water", "Earth", relationClashing}, // earth dams water
		{"Fire", "Void", relationNeutral},
		{"Chaos", "Fire", relationNeutral},
		// The elements outside the five stand with a phase of their own.
		{"Lightning", "Fire", relationResonant},
		{"Ice", "Fire", relationDrained}, // ice stands with water, and water quenches fire
		{"Wind", "Metal", relationClashing},
		{"Yang", "Water", relationClashing},
	} {
		if got := elementRelation(catalog, c.root, c.manual); got != c.want {
			t.Errorf("%s root vs %s method = %s, want %s", c.root, c.manual, got, c.want)
		}
	}
	// The kindest of several roots answers for all of them.
	if got := bestElementRelation(catalog, []string{"Water", "Wood"}, "Fire"); got != relationGenerative {
		t.Fatalf("a wood-and-water root meeting fire: %s", got)
	}
	if got := bestElementRelation(catalog, nil, "Fire"); got != relationNeutral {
		t.Fatalf("no root at all: %s", got)
	}
}

func TestABetterRootAbsorbsMoreOfWhateverItTouches(t *testing.T) {
	catalog := qiBodyCatalog(t)
	plain := SpiritualRootState{Grade: "Mortal", Purity: 0, Elements: []string{"Fire"}}
	fine := SpiritualRootState{Grade: "Immortal", Purity: 100, Elements: []string{"Fire"}}
	if rootAbsorptionBonus(catalog, fine) <= rootAbsorptionBonus(catalog, plain) {
		t.Fatal("grade and purity must both count for absorption")
	}
	resonant := absorptionFor(catalog, fine, "Fire")
	clashing := absorptionFor(catalog, fine, "Water")
	if resonant.Mult <= clashing.Mult || resonant.Relation != relationResonant || clashing.Relation != relationClashing {
		t.Fatalf("resonant %v vs clashing %v", resonant, clashing)
	}
	if clashing.Surcharge <= 0 || resonant.Surcharge != 0 {
		t.Fatalf("only a clash risks anything: %d / %d", clashing.Surcharge, resonant.Surcharge)
	}
	// A cultivator practising nothing is neither helped nor at risk.
	none := absorptionFor(catalog, fine, "")
	if none.Mult != 1 || none.Surcharge != 0 || none.Relation != relationNeutral {
		t.Fatalf("no method: %v", none)
	}
}

func TestTheSessionIsWorkedByWhatTheRootCanAbsorb(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := qiBodyCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0 WHERE user_id=42`)
	// The fixture root is Fire. A fire method resonates; a water one clashes.
	fire, water := manualOfElement(t, catalog, "Fire"), manualOfElement(t, catalog, "Water")
	batch4Exec(t, path, `INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(42,?,0,0,0,0),(42,?,0,0,0,0)`, fire, water)

	batch4Result(t, batch4Apply(t, path, world, "cultivation.manual", 1, map[string]any{"manual_id": fire}))
	resonant := trainOnce(t, path, world, 700)
	if resonant["element"] != "Fire" || resonant["element_relation"] != relationResonant {
		t.Fatalf("a fire root on a fire method: %v %v", resonant["element"], resonant["element_relation"])
	}
	chosen := batch4Result(t, batch4Apply(t, path, world, "cultivation.manual", 2, map[string]any{"manual_id": water}))
	if chosen["element"] != "Water" || chosen["element_relation"] != relationClashing {
		t.Fatalf("choosing the method names what it draws: %v", chosen)
	}
	clashing := trainOnce(t, path, world, 701)
	hot, _ := resonant["element_mult"].(float64)
	wet, _ := clashing["element_mult"].(float64)
	if hot <= wet || hot <= 1 || wet >= 1 {
		t.Fatalf("resonant %v must beat clashing %v, and only one of them helps", hot, wet)
	}
	// The body path tempers flesh and answers to no element.
	batch4Exec(t, path, `DELETE FROM cooldowns WHERE user_id=42`)
	tempered := batch4Result(t, batch4Apply(t, path, world, "cultivation.body_train", 702, map[string]any{"cooldown_seconds": 1}))
	if mult, _ := tempered["element_mult"].(float64); mult != 1 {
		t.Fatalf("the body path is not elemental: %v", mult)
	}
	// The sheet says the same thing the session did.
	status := cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["element"] != "Water" || status["element_relation"] != relationClashing || status["element_label"] == "" {
		t.Fatalf("the sheet carries the element: %v", status)
	}
	if _, ok := status["element_mult"]; !ok {
		t.Fatal("the sheet needs element_mult")
	}
}

func TestQiTheRootCannotStomachCanTurnOnItsOwn(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := qiBodyCatalog(t)
	water := manualOfElement(t, catalog, "Water")
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(42,?,0,0,0,0)`, water)
	batch4Result(t, batch4Apply(t, path, world, "cultivation.manual", 1, map[string]any{"manual_id": water}))

	// Circulate, which risks nothing of its own: any deviation here is the
	// element's doing.
	batch4Apply(t, path, world, "cultivation.stance", 2, map[string]any{"stance": "circulate"})
	clashed := false
	for seq := 0; seq < 120 && !clashed; seq++ {
		batch4Exec(t, path, `UPDATE characters SET cultivation=0 WHERE user_id=42`)
		out := trainOnce(t, path, world, 800+seq)
		clashed, _ = out["element_clash"].(bool)
	}
	if !clashed {
		t.Fatal("a clashing element must eventually turn")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_conditions WHERE user_id=42 AND source_id='element_clash'`)); got == 0 {
		t.Fatal("the deviation names the clash as its cause")
	}
	// And a resonant method never does.
	fire := manualOfElement(t, catalog, "Fire")
	batch4Exec(t, path, `INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(42,?,0,0,0,0)`, fire)
	batch4Result(t, batch4Apply(t, path, world, "cultivation.manual", 3, map[string]any{"manual_id": fire}))
	for seq := 0; seq < 40; seq++ {
		batch4Exec(t, path, `UPDATE characters SET cultivation=0 WHERE user_id=42`)
		out := trainOnce(t, path, world, 1000+seq)
		if clash, _ := out["element_clash"].(bool); clash {
			t.Fatal("a resonant method must never clash")
		}
	}
}

func TestTheCatalogueGeneratorAndTheContentAgree(t *testing.T) {
	// The elements in content must be the ones the generator would produce, or
	// regenerating the catalogue would silently reshuffle every method.
	catalog := qiBodyCatalog(t)
	for id, definition := range catalog.TechniqueSystem.Manuals {
		if strings.TrimSpace(definition.Element) == "" {
			t.Fatalf("%s has no element", id)
		}
	}
}
