package game

import (
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The seam (v1.2.0): forage's twin for ore. Every assertion that a find landed
// lends the dice, exactly as the forage tests do; the refusals are certain.

func mineOnce(t *testing.T, world, location, worldName string, resources int64) map[string]any {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES(?,?,?)`,
		location, worldName, resources)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, location)
	return batch4Result(t, batch4Apply(t, path, world, "exploration.mine", 1, map[string]any{}))
}

func mineErr(t *testing.T, world, location string) error {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, location)
	_, err := batch4ApplyErr(path, world, "exploration.mine", 42, 1, map[string]any{})
	return err
}

func TestASeamGivesUpTheOreOfItsWorld(t *testing.T) {
	// With the finds lent, the common ore is certain: the world's own `@ore`
	// through the one resolver the event sites and the send-off use.
	defer everyMakingIsFound()()
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct{ location, worldName string }{
		{"Greenriver Town", "Mortal World"},
		{"Froststar Border City", "Celestial World"},
	} {
		want := catalog.EventSites.Material(tc.worldName, "@ore")
		result := mineOnce(t, world, tc.location, tc.worldName, 100)
		if !result["success"].(bool) {
			t.Fatalf("%s: a dig with the dice lent and every attribute at 100 missed: %v", tc.worldName, result["roll"])
		}
		loot, _ := result["loot"].(map[string]int64)
		if loot[want] < 1 {
			t.Fatalf("%s: the seam gave up %v, want at least one %s", tc.worldName, loot, want)
		}
		if result["world"] != tc.worldName {
			t.Fatalf("the result names world %v, want %s", result["world"], tc.worldName)
		}
	}
}

func TestARareVeinIsTheNextWorldsOre(t *testing.T) {
	// `Intn(100)` answering 0 is under any chance, so the rare vein lands - and
	// it is the Spiritual World's ore for a Mortal seam. A Celestial seam has
	// no world above it and must never invent one.
	defer everyMakingIsFound()()
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	mortal := mineOnce(t, world, "Greenriver Town", "Mortal World", 100)
	if got, want := mortal["rare_found"], catalog.EventSites.Material("Spiritual World", "@ore"); got != want {
		t.Fatalf("a Mortal seam's rare vein is %v, want %s", got, want)
	}
	celestial := mineOnce(t, world, "Froststar Border City", "Celestial World", 100)
	if celestial["rare_found"] != "" {
		t.Fatalf("a Celestial seam found %v above it", celestial["rare_found"])
	}
}

func TestAFailedDigCarriesNothingHome(t *testing.T) {
	// Both dice come up ones against a Celestial TN with no attributes, so the
	// check certainly misses; the finds were planned before the roll, and the
	// clearing is what is tested.
	defer everyMakingIsFound()()
	world := batch4WorldPath(t)
	path := setupBatch5AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Froststar Border City','Celestial World',100)`)
	batch4Exec(t, path, `UPDATE characters SET location='Froststar Border City',
		attributes_json='{"body":0,"agility":0,"spirit":0,"insight":0,"will":0,"presence":0}' WHERE user_id=42`)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.mine", 1, map[string]any{}))
	if success, _ := result["success"].(bool); success {
		t.Fatal("a cultivator with no attributes at all passed a Celestial dig on two ones")
	}
	if loot, _ := result["loot"].(map[string]int64); len(loot) != 0 {
		t.Fatalf("a failed dig carried %v", loot)
	}
	if found, _ := result["materials_found"].(map[string]int64); len(found) != 0 {
		t.Fatalf("a failed dig reported %v", found)
	}
	if result["rare_found"] != "" || storage.ParseInt(result["stones"]) != 0 {
		t.Fatalf("a failed dig reported a vein (%v) or stones (%v)", result["rare_found"], result["stones"])
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42`)); got != 0 {
		t.Fatalf("a failed dig left %d rows in the bags", got)
	}
}

func TestTheMineResultCarriesTheRollTheReplyPrints(t *testing.T) {
	// v1.0.3's rule: a result that reports a roll reports the whole roll.
	result := mineOnce(t, batch4WorldPath(t), "Cloudspine Foothills", "Mortal World", 80)
	roll, ok := result["roll"].(map[string]any)
	if !ok {
		t.Fatalf("mine result carries no roll map: %v", result)
	}
	for _, key := range []string{"die1", "die2", "modifier", "tn", "total", "margin", "success", "degree", "probability"} {
		if _, present := roll[key]; !present {
			t.Fatalf("the roll lacks %q: %v", key, roll)
		}
	}
}

func TestNobodyDigsIndoorsOrOnAShrine(t *testing.T) {
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	shrine := ""
	for name, loc := range catalog.Locations {
		if loc.RoadSite == "shrine" {
			shrine = name
			break
		}
	}
	if shrine == "" {
		t.Fatal("the content file carries no shrine; the test cannot ask the question")
	}
	for _, tc := range []struct{ location, refusal string }{
		{"birth_family:1", "private residence"},
		{"personal_world:42", "private residence"},
		{shrine, "shrine"},
	} {
		err := mineErr(t, world, tc.location)
		if err == nil || !strings.Contains(err.Error(), tc.refusal) {
			t.Fatalf("at %s the dig answered %v, want a refusal naming %q", tc.location, err, tc.refusal)
		}
	}
}

func TestTheSeamsWaitIsTheEnginesAndItsRankAdvances(t *testing.T) {
	// The wait is `actionCooldowns`' (rc.56), not a payload's, and a dig -
	// landed or not - is Mining practice.
	defer everyMakingIsFound()()
	world := batch4WorldPath(t)
	path := setupBatch5AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Greenriver Town','Mortal World',100)`)
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	first := batch4Result(t, batch4Apply(t, path, world, "exploration.mine", 1, map[string]any{}))
	prog, _ := first["profession_progress"].(map[string]any)
	if prog == nil || storage.ParseInt(prog["xp"]) <= 0 {
		t.Fatalf("a dig advanced no Mining: %v", first["profession_progress"])
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT xp FROM profession_progress WHERE user_id=42 AND profession=?`, miningProfession)); got <= 0 {
		t.Fatalf("Mining xp on the row is %d after a dig", got)
	}
	_, err := batch4ApplyErr(path, world, "exploration.mine", 42, 2, map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "cooldown active") {
		t.Fatalf("a second dig inside the wait answered %v, want the cooldown refusal", err)
	}
	if got := cooldownSecondsFor(cooldownMine); got != 30*60 {
		t.Fatalf("the seam's wait is %ds at the shipped default, want the table's 30 minutes", got)
	}
}
