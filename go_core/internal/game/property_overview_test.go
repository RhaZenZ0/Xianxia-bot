package game

import (
	"encoding/json"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func readPropertyOverview(t *testing.T, path, world string) map[string]any {
	t.Helper()
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: "property.overview", ActorID: 42, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	result, _ := out.Result.(map[string]any)
	if result == nil {
		t.Fatalf("the overview answered nothing: %#v", out.Result)
	}
	return result
}

func overviewRooms(t *testing.T, home any) map[string]map[string]any {
	t.Helper()
	h, _ := home.(map[string]any)
	if h == nil {
		t.Fatalf("no home in the overview: %#v", home)
	}
	rooms := map[string]map[string]any{}
	raw, _ := json.Marshal(h["facilities"])
	var list []map[string]any
	if err := json.Unmarshal(raw, &list); err != nil {
		t.Fatal(err)
	}
	for _, row := range list {
		rooms[row["key"].(string)] = row
	}
	return rooms
}

func roomNumber(row map[string]any, part, key string) float64 {
	m, _ := row[part].(map[string]any)
	if m == nil {
		return -1
	}
	switch v := m[key].(type) {
	case float64:
		return v
	case json.Number:
		f, _ := v.Float64()
		return f
	case int64:
		return float64(v)
	}
	return -1
}

// v1.30.0: a home's status card says what each room does, from the numbers
// the rules themselves use, and what the next level adds and costs.
func TestTheOverviewSaysWhatEachRoomDoes(t *testing.T) {
	path := setupSectResidenceDB(t)
	world := batch4WorldPath(t)
	setMembership(t, path, "Deacon", 40, 1000)
	if _, err := establishProperty(t, path, world, "homestead"); err != nil {
		t.Fatal(err)
	}
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	overview := readPropertyOverview(t, path, world)
	home := overviewRooms(t, overview["homestead"])
	if got, want := roomNumber(home["cultivation"], "does", "cultivation_mult"), round4(homeCultivationMult(1)); got != want {
		t.Fatalf("the chamber says x%v; a session under it is given x%v", got, want)
	}
	if got := roomNumber(home["storage"], "does", "storage_slots"); got != float64(propertyStorageSlotsPerLevel) {
		t.Fatalf("the storeroom says %v stacks; a deposit is given %d", got, propertyStorageSlotsPerLevel)
	}
	furnace := home["alchemy"]
	if storage.ParseInt(furnace["level"]) != 0 || roomNumber(furnace, "does", "craft_bonus") != -1 {
		t.Fatalf("an unbuilt furnace claims to do something: %#v", furnace)
	}
	if got := roomNumber(furnace, "next", "craft_bonus"); got != float64(abodeFacilityRollBonus(1)) {
		t.Fatalf("building the furnace says +%v; a craft under it is given +%d", got, abodeFacilityRollBonus(1))
	}
	if got, want := storage.ParseInt(furnace["next_cost"]), homesteadUpgradeCost(catalog, 0); got != want {
		t.Fatalf("the furnace says it costs %d; the upgrade charges %d", got, want)
	}
	slots, fee := stallSlotsAndFee(catalog, 0)
	if roomNumber(home["merchant"], "does", "stall_slots") != float64(slots) || roomNumber(home["merchant"], "does", "stall_fee_percent") != float64(fee) {
		t.Fatalf("the merchant hall's stall terms disagree with the stall's own: %#v", home["merchant"])
	}

	residence := overviewRooms(t, overview["residence"])
	chamber := residence["cultivation"]
	if got, want := storage.ParseInt(chamber["next_cost"]), residenceUpgradeCost(catalog, 2); got != want {
		t.Fatalf("the residence chamber says it costs %d; the upgrade charges %d", got, want)
	}
	// The fixture stands at Body Tempering, and level 2 asks the next stage:
	// the card says so before the upgrade is asked.
	if needs, _ := chamber["next_needs"].(string); needs == "" {
		t.Fatalf("the residence chamber does not say what level 2 still asks: %#v", chamber)
	}
	if got := storage.ParseInt(overview["storage_slots_from_homes"]); got != 2*propertyStorageSlotsPerLevel {
		t.Fatalf("both storerooms add %d stacks; the deposit is given %d", got, propertyStorageSlotsPerLevel*2)
	}
}

// v1.30.0: the overview answers from helpers, and the rules ask the same ones;
// a helper's own test passes against a tree nothing calls it from.
func TestEveryHomeRuleAsksTheOverviewsHelpers(t *testing.T) {
	for _, site := range []struct{ file, fn, helper string }{
		{"cultivation_place.go", "placeMultiplierForPath", "homeCultivationMult"},
		{"seclusion_environment.go", "seclusionEnvironmentGo", "homeCultivationMult"},
		{"crafting_actions.go", "canonicalCraftAbodeBonus", "abodeFacilityRollBonus"},
		{"crafting_actions.go", "forageResolveAction", "abodeFacilityRollBonus"},
		{"beast_artifact_actions.go", "canonicalBeastTrainingContext", "abodeFacilityRollBonus"},
		{"property_storage_actions.go", "abodeUpgradeActionGo", "homesteadUpgradeCost"},
		{"sect_abode_facilities.go", "sectAbodeUpgradeAction", "residenceUpgradeCost"},
		{"../simulation/advanced_maintenance.go", "advanceHunters", "PropertyWardShare"},
	} {
		if indexOf(callsIn(t, site.file, site.fn)[site.fn], site.helper) < 0 {
			t.Errorf("%s (%s) no longer asks %s, so the home's status card and the rule can disagree", site.fn, site.file, site.helper)
		}
	}
}
