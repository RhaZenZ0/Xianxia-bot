package game

import (
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// everyMakingIsFound answers 0 to every bound: each material's find roll lands,
// each yields the bottom of its range, and both dice of the check come up ones.
// What is left is the gate - the region's richness against `min_resources` -
// which is the half these tests are about.
func everyMakingIsFound() func() {
	return gamerng.UseRoller(func(int) int { return 0 })
}

// makingsWithin counts the entries the content file says a region this rich can
// give up, so the expectation moves with the roster rather than with a literal
// that a later content batch would quietly falsify.
func makingsWithin(t *testing.T, world string, resources int64) int {
	t.Helper()
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	within := 0
	for id, spec := range catalog.ForageMaterials {
		if _, ok := catalog.Items[id]; !ok {
			continue
		}
		if spec.Chance > 0 && spec.Max > 0 && resources >= spec.MinResources {
			within++
		}
	}
	if within == 0 {
		t.Fatal("no forage material in the content file is reachable at all")
	}
	return within
}

// The makings of the other three crafts (v1.0.0-rc.21).
//
// `talisman_paper`, `spirit_ink` and `array_disk_blank` were named by items,
// recipes, shops and merchants and by no gathering path at all, so Alchemy and
// Forging could be gathered into and Inscription and Formation could only be
// bought into. A forager brings them back now, gated on how rich the region is.

func forageOnce(t *testing.T, world, location, worldName string, resources int64) map[string]any {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES(?,?,?)`,
		location, worldName, resources)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, location)
	return batch4Result(t, batch4Apply(t, path, world, "forage.resolve", 1, map[string]any{}))
}

func materialsOf(t *testing.T, result map[string]any) map[string]int64 {
	t.Helper()
	found, _ := result["materials_found"].(map[string]int64)
	return found
}

func TestAThinRegionHoldsNoneOfTheMakings(t *testing.T) {
	// The deterministic half. Every entry's `min_resources` is above 30, so a
	// poor region must yield nothing at all - the gate is what stops the three
	// crafts' materials raining out of every hillside.
	world := batch4WorldPath(t)
	for attempt := 0; attempt < 6; attempt++ {
		result := forageOnce(t, world, "Greenriver Town", "Mortal World", 30)
		if found := materialsOf(t, result); len(found) != 0 {
			t.Fatalf("a region at 30 spirit resources gave up %v", found)
		}
	}
}

func TestARichRegionGivesUpTheCraftMakings(t *testing.T) {
	// A forager in a rich region brings back the makings. This used to ask
	// across thirty independent trips because the find is a roll - at ~38% a
	// trip that is a miss about one run in a million, which is a bet the suite
	// does not need to take. With the finds lent, one trip is the whole
	// question: everything the region is rich enough to hold comes home.
	defer everyMakingIsFound()()
	world := batch4WorldPath(t)
	want := makingsWithin(t, world, 100)
	seen := materialsOf(t, forageOnce(t, world, "Greenriver Town", "Mortal World", 100))
	if len(seen) != want {
		t.Fatalf("a region at full spirit resources gave up %v, want all %d of the makings it holds", seen, want)
	}
	for item, qty := range seen {
		if qty < 1 {
			t.Fatalf("%s was found %d times", item, qty)
		}
	}
}

func TestWhatIsFoundIsWhatIsCarriedHome(t *testing.T) {
	// The report and the bags have to agree: a `materials_found` naming a
	// thing the inventory does not hold is the same lie as a silent gate.
	defer everyMakingIsFound()()
	world := batch4WorldPath(t)
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Greenriver Town','Mortal World',100)`)
	result := batch4Result(t, batch4Apply(t, path, world, "forage.resolve", 1, map[string]any{}))
	found := materialsOf(t, result)
	if len(found) != makingsWithin(t, world, 100) {
		t.Fatalf("one trip with every find lent reported %v", found)
	}
	for item, qty := range found {
		held := storage.ParseInt(actionScalar(t, path,
			`SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, item))
		if held != qty {
			t.Fatalf("%s: reported %d, holding %d", item, qty, held)
		}
	}
}

func TestAFailedForageCarriesNothingHome(t *testing.T) {
	// `materials_found` is planned before the roll, because the roll needs no
	// part of it. A miss must still report nothing, or the embed would name
	// goods the player does not have.
	//
	// The fixture's cultivator has 100 in every attribute and so cannot fail a
	// forage at all - which is why this zeroes them. A test that can only pass
	// by never running is the fault this file exists to prevent.
	//
	// The same dice the two tests above lend make this one certain from the
	// other side: the finds all land, so `materials_found` is genuinely full
	// when the check is made, and both dice come up ones against a Celestial
	// TN of 14 with a modifier of 4, so the check certainly misses. What is
	// tested is the clearing, and it is now tested with something to clear.
	defer everyMakingIsFound()()
	world := batch4WorldPath(t)
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Froststar Border City','Celestial World',100)`)
	batch4Exec(t, path, `UPDATE characters SET location='Froststar Border City',
		attributes_json='{"body":0,"agility":0,"spirit":0,"insight":0,"will":0,"presence":0}' WHERE user_id=42`)
	result := batch4Result(t, batch4Apply(t, path, world, "forage.resolve", 1, map[string]any{}))
	if success, _ := result["success"].(bool); success {
		t.Fatal("a cultivator with no attributes at all passed a Celestial forage on two ones")
	}
	if found := materialsOf(t, result); len(found) != 0 {
		t.Fatalf("a failed forage reported %v", found)
	}
	if loot, _ := result["loot"].(map[string]int64); len(loot) != 0 {
		t.Fatalf("a failed forage carried %v", loot)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42`)); got != 0 {
		t.Fatalf("a failed forage left %d rows in the bags", got)
	}
}

// The Discord reply prints the check through the same line every other roll
// uses (die1, die2, modifier, tn, total, degree, probability). The forage
// result flattened d1/d2 and dropped the degree, so the reply raised on every
// forage from the hub (v1.0.0-rc.35, found by the leaf sweep). The roll map
// rides the result whole; the flat fields stay for the readers that use them.
func TestTheForageResultCarriesTheRollTheReplyPrints(t *testing.T) {
	result := forageOnce(t, batch4WorldPath(t), "Cloudspine Foothills", "Mortal World", 80)
	roll, ok := result["roll"].(map[string]any)
	if !ok {
		t.Fatalf("forage result carries no roll map: %v", result)
	}
	for _, key := range []string{"die1", "die2", "modifier", "tn", "total", "margin", "success", "degree", "probability"} {
		if _, present := roll[key]; !present {
			t.Fatalf("the roll lacks %q: %v", key, roll)
		}
	}
	if roll["die1"] != result["d1"] || roll["die2"] != result["d2"] || roll["total"] != result["total"] {
		t.Fatalf("the roll and the flat fields disagree: %v vs d1=%v d2=%v total=%v", roll, result["d1"], result["d2"], result["total"])
	}
}
