package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// How a cultivator crosses ground (v1.0.0-rc.15). Before this, realm bought
// a cultivator at most a third off a walk and a flying sword was scenery:
// the Ascension Realm ancestor and the porter took the same road at the
// same speed. Now the realm - or the artifact in the bags standing in for
// it - decides whether they walk it, fly it, or fold it.

func TestTheRoadGetsShorterAsTheCultivatorGetsHigher(t *testing.T) {
	catalog := districtCatalog(t)
	origin, destination := catalog.Locations["Greenriver Town"], catalog.Locations["Riverguard City"]

	walked := canonicalRoadTravelProfileRiding(origin, destination, 0, 0, "")
	flown := canonicalRoadTravelProfileRiding(origin, destination, 0, travelFlightRealm, "")
	folded := canonicalRoadTravelProfileRiding(origin, destination, 0, travelFoldRealm, "")

	if walked.Mode != "on foot" || flown.Mode != "flying" || folded.Mode != "folding space" {
		t.Fatalf("modes: %q %q %q", walked.Mode, flown.Mode, folded.Mode)
	}
	if !(walked.TravelMinutes > flown.TravelMinutes && flown.TravelMinutes > folded.TravelMinutes) {
		t.Fatalf("a higher realm must cross faster: walked=%d flown=%d folded=%d",
			walked.TravelMinutes, flown.TravelMinutes, folded.TravelMinutes)
	}
	// Flying is a third of the walk, not a shaving off it.
	if flown.TravelMinutes > walked.TravelMinutes/2 {
		t.Fatalf("flying should be far shorter than walking: %d vs %d", flown.TravelMinutes, walked.TravelMinutes)
	}
	// And what cannot reach you cannot rob you. A quiet river road is
	// already at the danger floor, so the drop is asserted where there is
	// room for it: a volcanic leg in the world that ships the worst roads.
	hard := worlddata.LocationDefinition{Terrain: "volcanic ridge", World: "Celestial World"}
	hardWalked := canonicalRoadTravelProfileRiding(hard, hard, 0, 0, "")
	hardFlown := canonicalRoadTravelProfileRiding(hard, hard, 0, travelFlightRealm, "")
	hardFolded := canonicalRoadTravelProfileRiding(hard, hard, 0, travelFoldRealm, "")
	if !(hardWalked.DangerScore > hardFlown.DangerScore && hardFlown.DangerScore > hardFolded.DangerScore) {
		t.Fatalf("danger must fall off the ground: walked=%d flown=%d folded=%d",
			hardWalked.DangerScore, hardFlown.DangerScore, hardFolded.DangerScore)
	}
	if walked.DangerScore < flown.DangerScore || flown.DangerScore < folded.DangerScore {
		t.Fatalf("leaving the ground must never raise the danger: %d %d %d",
			walked.DangerScore, flown.DangerScore, folded.DangerScore)
	}
	// The floor scales with the mode, so the fastest travel in the setting
	// is never indistinguishable from the slowest on a short leg.
	near := canonicalRoadTravelProfileRiding(origin, origin, 0, travelFoldRealm, "")
	if near.TravelMinutes >= 30 {
		t.Fatalf("folding space still bottoms out at a walking floor: %d", near.TravelMinutes)
	}
}

func TestAFlyingArtifactCarriesADiscipleWhoCannotYetFly(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=5000,vitality=100,realm_index=0 WHERE user_id=42`)
	quiet := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = quiet }()
	missed := roadSiteDiscoveryIntn
	roadSiteDiscoveryIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadSiteDiscoveryIntn = missed }()

	batch4SetCanonicalGameMinute(t, path, 3000)
	walked := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{"destination": "Riverguard City", "mode": "known"}))
	if fmt.Sprint(walked["travel_mode"]) != "on foot" || fmt.Sprint(walked["travel_mount"]) != "" {
		t.Fatalf("a Body Tempering disciple walks: mode=%v mount=%v", walked["travel_mode"], walked["travel_mount"])
	}
	onFoot := i64(walked["travel_minutes"])
	if onFoot <= 0 {
		t.Fatalf("no journey: %v", walked["travel_minutes"])
	}

	// The same disciple, the same road, one flying sword in the bags.
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'azure_flying_sword',1)`)
	batch4SetCanonicalGameMinute(t, path, 20000)
	flown := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 2, map[string]any{"destination": "Greenriver Town", "mode": "known"}))
	if fmt.Sprint(flown["travel_mode"]) != "flying" {
		t.Fatalf("a flying sword should get them off the road: %v", flown["travel_mode"])
	}
	if fmt.Sprint(flown["travel_mount"]) != "a flying sword" {
		t.Fatalf("the reply should name what carries them: %v", flown["travel_mount"])
	}
	if flying := i64(flown["travel_minutes"]); flying >= onFoot {
		t.Fatalf("flying took no less than walking: %d vs %d", flying, onFoot)
	}
	// The artifact is a vehicle, not a consumable: it is still there after.
	if got := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='azure_flying_sword'`)); got != 1 {
		t.Fatalf("the sword was spent by the journey: quantity=%d", got)
	}
}

func TestABoundFlyingSwordStillFlies(t *testing.T) {
	// `equipment.bind` takes the item out of the inventory to make it an
	// equipment instance. A flying sword read only off `inventory` would
	// therefore stop flying the moment its owner bound it as a weapon -
	// exactly backwards, since a flying sword is the genre's default mount
	// *because* it is also the weapon.
	path := setupShopDB(t)
	catalog := districtCatalog(t)
	batch4Exec(t, path, `INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,quality,equipped) VALUES(42,'azure_flying_sword','weapon',220,220,100,1)`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	flight, mount, err := bestFlightArtifact(conn, catalog, 42)
	conn.Close()
	if err != nil {
		t.Fatal(err)
	}
	if flight != catalog.Items["azure_flying_sword"].Flight || mount != "a flying sword" {
		t.Fatalf("a bound sword should still carry its owner: %d %q", flight, mount)
	}

	// A sword broken to nothing carries nobody.
	batch4Exec(t, path, `UPDATE equipment_instances SET durability=0 WHERE user_id=42`)
	conn2, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	flight, _, err = bestFlightArtifact(conn2, catalog, 42)
	conn2.Close()
	if err != nil {
		t.Fatal(err)
	}
	if flight != 0 {
		t.Fatalf("a broken sword should not fly: %d", flight)
	}
}

// The flying sword is also a weapon, which is the whole reason the genre
// makes it the default: you do not choose between going and fighting.
func TestTheFlyingSwordIsAlsoASword(t *testing.T) {
	def, ok := equipmentDefinitionsGo()["azure_flying_sword"]
	if !ok {
		t.Fatal("the flying sword is not equipment")
	}
	if def.Slot != "weapon" || def.Attack <= 0 {
		t.Fatalf("a sword that cannot cut: %+v", def)
	}
	stats, ok := equipDefs["azure_flying_sword"]
	if !ok || stats[0] != def.Attack || stats[1] != def.Defense || stats[2] != def.Spirit || stats[3] != def.Agility {
		t.Fatalf("1v1 combat disagrees with party combat: %v vs %+v", stats, def)
	}
}

func TestNothingInTheBagsBeatsTheCultivatorsOwnRealm(t *testing.T) {
	path := setupShopDB(t)
	catalog := districtCatalog(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	flight, mount, err := bestFlightArtifact(conn, catalog, 42)
	conn.Close()
	if err != nil {
		t.Fatal(err)
	}
	if flight != 0 || mount != "" {
		t.Fatalf("empty bags should carry nothing: %d %q", flight, mount)
	}
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'paper_crane_charm',1),(42,'void_stride_talisman',1),(42,'spirit_herb',3)`)
	conn2, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn2.Close()
	flight, mount, err = bestFlightArtifact(conn2, catalog, 42)
	if err != nil {
		t.Fatal(err)
	}
	if flight != catalog.Items["void_stride_talisman"].Flight {
		t.Fatalf("the best thing carried should win: %d", flight)
	}
	if mount != "a folded step" {
		t.Fatalf("mount=%q", mount)
	}
}
