package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The ghost road (v1.0.0-rc.8): born to it or not at all, death qi read from
// the ground the living have left, and a residue that never entirely washes
// out.

func ghostFixture(t *testing.T) (string, string) {
	t.Helper()
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	// A ghost cultivator with an old pool, which the first settle rescales.
	batch4Exec(t, path, `UPDATE characters SET path='Ghost Cultivator',realm_index=2,phase=4,cultivation=0,qi=28,qi_max=28 WHERE user_id=42`)
	return path, world
}

// ghostSite is a road-side place of the given kind in the Mortal World.
func ghostSite(t *testing.T, kind string) string {
	t.Helper()
	catalog := qiBodyCatalog(t)
	best := ""
	for name, def := range catalog.Locations {
		if def.RoadSite == kind && def.World == "Mortal World" && (best == "" || name < best) {
			best = name
		}
	}
	if best == "" {
		t.Fatalf("the world has no %s in the Mortal World", kind)
	}
	return best
}

func TestTheGhostRoadBelongsToThoseBornAmongTheDead(t *testing.T) {
	catalog := qiBodyCatalog(t)
	if !isGhostPath(catalog, "Ghost Cultivator") || isGhostPath(catalog, "Qi Refiner") {
		t.Fatal("the ghost path must be the one the content names")
	}
	if len(catalog.DeathQi.Families) != 2 {
		t.Fatalf("two households and no more: %v", catalog.DeathQi.Families)
	}
	for _, id := range []string{"nether_market_house", "tomb_watch_clan"} {
		if !ghostBornFamily(catalog, id) {
			t.Fatalf("%s should raise a ghost cultivator", id)
		}
	}
	for _, id := range []string{"martial_household", "alchemy_family", ""} {
		if ghostBornFamily(catalog, id) {
			t.Fatalf("%q should not", id)
		}
	}
	// And the two households are two more archetypes, not two instead of two.
	if len(birthFamilyArchetypes) != 13 {
		t.Fatalf("thirteen households: %d", len(birthFamilyArchetypes))
	}
}

func TestCharacterCreateRefusesTheGhostRoadToEveryOtherHousehold(t *testing.T) {
	path := setupCharacterCreationAuthorityDB(t)
	world := batch4WorldPath(t)
	zero := int64(0)
	options := creationApply(t, path, world, "ghost-family-options", "character.family_options", 77, &zero, map[string]any{"world_name": "Mortal World", "game_minute": 500})
	offers := batch4Result(t, options)["families"].([]familyOffer)
	var ordinary, ghost familyOffer
	for _, offer := range offers {
		if offer.ID == "martial_household" {
			ordinary = offer
		}
		if offer.ID == "tomb_watch_clan" {
			ghost = offer
		}
	}
	if ordinary.ChoiceID == "" || ghost.ChoiceID == "" {
		t.Fatalf("both households must be offered: %d offers", len(offers))
	}
	expected := options.StateVersion
	batch4SetCanonicalGameMinute(t, path, 501)
	raw, _ := json.Marshal(map[string]any{
		"name": "Han Wrongborn", "path": "Ghost Cultivator", "gender": "male", "family_choice_id": ordinary.ChoiceID,
	})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "ghost-create-refused", Operation: "character.create", ActorID: 77, ExpectedVersion: &expected, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "born among the dead") {
		t.Fatalf("a martial household cannot raise one: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=77")); got != 0 {
		t.Fatalf("character rows=%d", got)
	}
	// The tomb-watch clan can.
	created := batch4Result(t, creationApply(t, path, world, "ghost-create-allowed", "character.create", 77, &expected, map[string]any{
		"discord_name": "Tester", "name": "Xie Graveborn", "concept": "Keep the barrows", "gender": "female",
		"path": "Ghost Cultivator", "family_choice_id": ghost.ChoiceID, "game_minute": 502, "age_at_creation_years": 18,
	}))
	if created["created"] != true || created["path"] != "Ghost Cultivator" {
		t.Fatalf("the ghost-born must be allowed: %v", created)
	}
}

func TestTheGroundAndTheHoursRunTheOtherWayOnTheGhostRoad(t *testing.T) {
	catalog := qiBodyCatalog(t)
	ruin, shrine := ghostSite(t, "ruin"), ghostSite(t, "shrine")

	_, ruinMult := deathQiGroundMultiplier(catalog, ruin)
	_, shrineMult := deathQiGroundMultiplier(catalog, shrine)
	if ruinMult <= 1 || shrineMult >= 1 {
		t.Fatalf("a ruin is rich and a shrine is hostile: %v / %v", ruinMult, shrineMult)
	}
	// Exactly the other way round from a living cultivator, for whom the
	// shrine is the best ground on the road.
	if shrineMult >= placeShrineMult || ruinMult <= placeShrineMult {
		t.Fatalf("the ghost road must invert the living one: ruin=%v shrine=%v living shrine=%v", ruinMult, shrineMult, placeShrineMult)
	}
	// A city full of the living is thin ground.
	if _, city := deathQiGroundMultiplier(catalog, "Moonfen City"); city >= 1 {
		t.Fatalf("the streets of the living: %v", city)
	}
	// Night is the ghost road's noon.
	night, afternoon := deathQiHourMultiplier(catalog, "Night"), deathQiHourMultiplier(catalog, "Afternoon")
	if night <= 1 || afternoon >= 1 || night <= afternoon {
		t.Fatalf("night %v, afternoon %v", night, afternoon)
	}
	// And the deeper the form, the worse daylight is - but only in daylight.
	deep := qiBody{QiType: deathQiType, GhostForm: 3}
	if ghostDaylightPenalty(catalog, deep, "Night") != 0 {
		t.Fatal("night costs a ghost nothing")
	}
	if ghostDaylightPenalty(catalog, deep, "Afternoon") <= ghostDaylightPenalty(catalog, qiBody{QiType: deathQiType, GhostForm: 1}, "Afternoon") {
		t.Fatal("a deeper form must suffer the sun more")
	}
	// A living cultivator never pays it.
	if ghostDaylightPenalty(catalog, qiBody{QiType: spiritQiType, GhostForm: 3}, "Afternoon") != 0 {
		t.Fatal("the living owe the sun nothing")
	}
}

func TestEverySessionOnTheRoadLeavesItsResidue(t *testing.T) {
	path, world := ghostFixture(t)
	catalog := qiBodyCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, ghostSite(t, "ruin"))

	first := trainOnce(t, path, world, 900)
	if first["qi_type"] != deathQiType {
		t.Fatalf("a ghost cultivator gathers death qi: %v", first["qi_type"])
	}
	if storage.ParseInt(first["corruption_gain"]) <= 0 || storage.ParseInt(first["corruption"]) <= 0 {
		t.Fatalf("the road leaves a residue: %v", first)
	}
	if !strings.Contains(sprint(first["place_name"]), "ruin") {
		t.Fatalf("the ruin is the ground it names: %v", first["place_name"])
	}
	// The form rises when the residue is deep enough for it.
	forms := catalog.DeathQi.GhostForms
	if len(forms) < 2 {
		t.Fatal("the content has no ghost-form ladder")
	}
	batch4Exec(t, path, `UPDATE character_qi_body SET corruption=? WHERE user_id=42`, forms[1].Corruption-1)
	risen := trainOnce(t, path, world, 901)
	if risen["form_risen"] != forms[1].Name {
		t.Fatalf("crossing into the second form: %v", risen["form_risen"])
	}
	if storage.ParseInt(actionScalar(t, path, `SELECT ghost_form FROM character_qi_body WHERE user_id=42`)) != 1 {
		t.Fatal("the form is written")
	}
	// And a form the realm cannot carry is not granted.
	deep := forms[len(forms)-1]
	if ghostFormIndex(catalog, corruptionCap, 0) >= int64(len(forms)-1) && deep.MinRealmIndex > 0 {
		t.Fatal("a realm floor on the ladder must hold")
	}
	// A living cultivator in the same place gathers nothing of the sort.
	batch4Exec(t, path, `UPDATE characters SET path='Qi Refiner' WHERE user_id=42`)
	living := trainOnce(t, path, world, 902)
	if living["qi_type"] != spiritQiType || storage.ParseInt(living["corruption_gain"]) != 0 {
		t.Fatalf("the living leave no residue: %v", living)
	}
}

func TestHarvestFillsTheDantianAndIncenseShedsTheResidue(t *testing.T) {
	path, world := ghostFixture(t)
	ruin, shrine := ghostSite(t, "ruin"), ghostSite(t, "shrine")

	// Not on this ground.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "ghost.harvest", 42, 1, map[string]any{}); err == nil || !strings.Contains(err.Error(), "nothing died here") {
		t.Fatalf("a living town holds nothing: %v", err)
	}
	// On the right ground, with room in the dantian.
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, ruin)
	cultivationQuery(t, path, world, "qi.status", 42)
	batch4Exec(t, path, `UPDATE characters SET qi=0 WHERE user_id=42`)
	karmaBefore := storage.ParseInt(actionScalar(t, path, `SELECT karma_score FROM characters WHERE user_id=42`))
	out := batch4Result(t, batch4Apply(t, path, world, "ghost.harvest", 2, map[string]any{}))
	if storage.ParseInt(out["qi_gained"]) <= 0 || storage.ParseInt(out["corruption_gain"]) <= 0 {
		t.Fatalf("a harvest is qi for corruption: %v", out)
	}
	if storage.ParseInt(out["karma_delta"]) != -2 {
		t.Fatalf("taking what the dead left costs karma: %v", out["karma_delta"])
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT karma_score FROM characters WHERE user_id=42`)); got != karmaBefore-2 {
		t.Fatalf("karma=%d want %d", got, karmaBefore-2)
	}
	// Twice in a row is refused.
	if _, err := batch4ApplyErr(path, world, "ghost.harvest", 42, 3, map[string]any{}); err == nil || !strings.Contains(err.Error(), "gathered again") {
		t.Fatalf("a place needs time: %v", err)
	}

	// Incense: not on a ruin, and not without the stones.
	if _, err := batch4ApplyErr(path, world, "ghost.appease", 42, 4, map[string]any{}); err == nil || !strings.Contains(err.Error(), "where the living keep their dead") {
		t.Fatalf("a ruin is no place for rites: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=0 WHERE user_id=42`, shrine)
	if _, err := batch4ApplyErr(path, world, "ghost.appease", 42, 5, map[string]any{}); err == nil || !strings.Contains(err.Error(), "spirit stones") {
		t.Fatalf("the rites are not free: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=5000 WHERE user_id=42`)
	before := storage.ParseInt(actionScalar(t, path, `SELECT corruption FROM character_qi_body WHERE user_id=42`))
	shed := batch4Result(t, batch4Apply(t, path, world, "ghost.appease", 6, map[string]any{}))
	if storage.ParseInt(shed["corruption_shed"]) <= 0 || storage.ParseInt(shed["corruption"]) >= before {
		t.Fatalf("incense must lift some of it: %v (was %d)", shed, before)
	}
	if storage.ParseInt(shed["stones_spent"]) <= 0 || storage.ParseInt(shed["karma_delta"]) != 1 {
		t.Fatalf("appease: %v", shed)
	}
	// A living cultivator is refused both, wherever they stand.
	batch4Exec(t, path, `UPDATE characters SET path='Qi Refiner' WHERE user_id=42`)
	batch4Exec(t, path, `DELETE FROM cooldowns WHERE user_id=42`)
	for _, op := range []string{"ghost.harvest", "ghost.appease"} {
		if _, err := batch4ApplyErr(path, world, op, 42, 7, map[string]any{}); err == nil || !strings.Contains(err.Error(), "born to a ghost household") {
			t.Fatalf("%s must be refused to the living: %v", op, err)
		}
	}
}

func TestTheResidueEatsThePurityCeilingAndTearsChannels(t *testing.T) {
	catalog := qiBodyCatalog(t)
	clean := qiBody{QiType: deathQiType}
	dirty := qiBody{QiType: deathQiType, Corruption: 50}
	if purityCeilingFor(catalog, 3, "", dirty) >= purityCeilingFor(catalog, 3, "", clean) {
		t.Fatal("the residue must eat the ceiling")
	}
	// A living cultivator's ceiling is never touched by it.
	living := qiBody{QiType: spiritQiType, Corruption: 50}
	if purityCeilingFor(catalog, 3, "", living) != purityCeilingFor(catalog, 3, "", qiBody{QiType: spiritQiType}) {
		t.Fatal("the living carry no residue")
	}
	if corruptionPurityPenalty(catalog, dirty) <= 0 || corruptionPurityPenalty(catalog, living) != 0 {
		t.Fatalf("penalty: %v / %v", corruptionPurityPenalty(catalog, dirty), corruptionPurityPenalty(catalog, living))
	}

	// Below the threshold nothing tears, however many times it is rolled.
	path, _ := ghostFixture(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	shallow := qiBody{QiType: deathQiType, Corruption: int64(catalog.DeathQi.Corruption.RuptureThreshold) - 1}
	for i := 0; i < 40; i++ {
		torn, err := corruptionRupture(conn, catalog, 42, shallow, 1)
		if err != nil {
			t.Fatal(err)
		}
		if torn {
			t.Fatal("nothing tears below the threshold")
		}
	}
	// Above it, it happens - not every time, but it happens.
	deep := qiBody{QiType: deathQiType, MeridiansOpen: 40, Corruption: corruptionCap}
	torn := false
	for i := 0; i < 200 && !torn; i++ {
		if torn, err = corruptionRupture(conn, catalog, 42, deep, 1); err != nil {
			t.Fatal(err)
		}
	}
	if !torn {
		t.Fatal("a residue that deep must eventually tear a channel")
	}
}

func TestTheGhostSheetSaysWhereYouStandAndWhatYouHaveBecome(t *testing.T) {
	path, world := ghostFixture(t)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, ghostSite(t, "ruin"))

	status := cultivationQuery(t, path, world, "ghost.status", 42)
	for _, key := range []string{"walking_the_road", "qi_type", "corruption", "corruption_cap", "ghost_form",
		"ghost_form_name", "capacity_mult", "daylight_penalty", "next_form", "ground_name", "ground_mult",
		"period", "hour_mult", "purity_ceiling_penalty", "rupture_threshold", "can_harvest_here", "can_appease_here"} {
		if _, ok := status[key]; !ok {
			t.Fatalf("the ghost sheet needs %s", key)
		}
	}
	if status["walking_the_road"] != true || status["qi_type"] != deathQiType {
		t.Fatalf("this one walks it: %v", status)
	}
	if status["can_harvest_here"] != true || status["can_appease_here"] != false {
		t.Fatalf("a ruin harvests and does not absolve: %v", status)
	}
	// The qi body sheet carries the road too, so the panel needs one call.
	body := cultivationQuery(t, path, world, "qi.status", 42)
	for _, key := range []string{"qi_type", "corruption", "ghost_form", "ghost_form_name"} {
		if _, ok := body[key]; !ok {
			t.Fatalf("the qi body sheet needs %s", key)
		}
	}
	// A living cultivator gets the same shape and a plain answer.
	batch4Exec(t, path, `UPDATE characters SET path='Qi Refiner' WHERE user_id=42`)
	living := cultivationQuery(t, path, world, "ghost.status", 42)
	if living["walking_the_road"] != false || living["qi_type"] != spiritQiType {
		t.Fatalf("the living are not on it: %v", living)
	}
	if living["can_harvest_here"] != false || living["can_appease_here"] != false {
		t.Fatalf("and can do none of it: %v", living)
	}
}
