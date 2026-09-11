package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// Road-side sites (v0.39.0): a place on every road, found by walking the
// road or exploring from either end, reached as half a leg from either
// end, and leading nowhere but back to them.

func roadSiteTravel(t *testing.T, path, world string, seq int, destination string) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"destination": destination, "mode": "known"})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("road-site-%d", seq), Operation: "exploration.travel", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	return batch4Result(t, out), nil
}

func TestWalkingARoadFindsTheShrineOnItAndTheHuntingGroundOneTimeInTwo(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	sites := roadSitesOnLeg(catalog, "Greenriver Town", "Riverguard City")
	if len(sites) != 1 || catalog.Locations[sites[0]].RoadSite != "shrine" {
		t.Fatalf("the Greenriver-Riverguard road should carry one shrine, got %v", sites)
	}
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=500,vitality=100 WHERE user_id=42`)
	batch4SetCanonicalGameMinute(t, path, 3000)
	quiet := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = quiet }()
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{"destination": "Riverguard City", "mode": "known"}))
	found, _ := result["road_sites_found"].([]map[string]any)
	if len(found) != 1 || fmt.Sprint(found[0]["name"]) != sites[0] || fmt.Sprint(found[0]["kind"]) != "shrine" {
		t.Fatalf("road_sites_found=%v want the shrine", result["road_sites_found"])
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT discovery_kind FROM character_location_discoveries WHERE user_id=42 AND location=?`, sites[0])); got != "road_side" {
		t.Fatalf("discovery kind=%q", got)
	}
	// A hunting ground lies off the road: the roll decides. Riverguard to
	// Jadewood carries one.
	ground := roadSitesOnLeg(catalog, "Jadewood Medicine City", "Riverguard City")
	if len(ground) != 1 || catalog.Locations[ground[0]].RoadSite != "hunting_ground" {
		t.Fatalf("the Jadewood-Riverguard road should carry one hunting ground, got %v", ground)
	}
	previous := roadSiteDiscoveryIntn
	roadSiteDiscoveryIntn = func(n int) (int, error) { return n - 1, nil } // missed
	defer func() { roadSiteDiscoveryIntn = previous }()
	batch4SetCanonicalGameMinute(t, path, 9000)
	result = batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 2, map[string]any{"destination": "Jadewood Medicine City", "mode": "known"}))
	if found, _ := result["road_sites_found"].([]map[string]any); len(found) != 0 {
		t.Fatalf("a missed roll should find nothing: %v", found)
	}
	roadSiteDiscoveryIntn = func(n int) (int, error) { return 0, nil } // seen
	batch4SetCanonicalGameMinute(t, path, 15000)
	result = batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 3, map[string]any{"destination": "Riverguard City", "mode": "known"}))
	found, _ = result["road_sites_found"].([]map[string]any)
	if len(found) != 1 || fmt.Sprint(found[0]["name"]) != ground[0] {
		t.Fatalf("the way back should find the hunting ground: %v", result["road_sites_found"])
	}
}

func TestASiteIsHalfALegFromEitherEndAndLeadsNowhereElse(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	shrine := roadSitesOnLeg(catalog, "Greenriver Town", "Riverguard City")[0]
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=500,vitality=100 WHERE user_id=42`)
	batch4SetCanonicalGameMinute(t, path, 3000)
	quiet := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = quiet }()
	// Unfound, the site is refused like any unknown place.
	if _, err := roadSiteTravel(t, path, world, 1, shrine); err == nil || !strings.Contains(err.Error(), "not been discovered") {
		t.Fatalf("an unfound site should be refused, got %v", err)
	}
	batch4Exec(t, path, `INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,?,'road_side',900,0)`, shrine)
	whole, _ := canonicalRoadRoute(catalog, "Greenriver Town", "Riverguard City", 0)
	result, err := roadSiteTravel(t, path, world, 2, shrine)
	if err != nil {
		t.Fatal(err)
	}
	if route, _ := result["road_route"].([]string); len(route) != 2 || route[1] != shrine {
		t.Fatalf("road_route=%v", result["road_route"])
	}
	if got := storage.ParseInt(result["travel_minutes"]); got != whole.TravelMinutes/2 {
		t.Fatalf("travel_minutes=%d want half of %d", got, whole.TravelMinutes)
	}
	if fmt.Sprint(result["arrived_at"]) != shrine || fmt.Sprint(result["site_kind"]) != "shrine" || fmt.Sprint(result["left_by_gate"]) != "North" {
		t.Fatalf("arrival: %v %v %v", result["arrived_at"], result["site_kind"], result["left_by_gate"])
	}
	// From the site the road leads back or on, and nowhere else.
	batch4SetCanonicalGameMinute(t, path, 9000)
	if _, err := roadSiteTravel(t, path, world, 3, "Azure Crown Imperial City"); err == nil || !strings.Contains(err.Error(), "leads back to Greenriver Town or on to Riverguard City") {
		t.Fatalf("a site should lead only to its ends, got %v", err)
	}
	result, err = roadSiteTravel(t, path, world, 4, "Riverguard City")
	if err != nil {
		t.Fatal(err)
	}
	if gate, _, _ := gateFacing(catalog, "Riverguard City", "Greenriver Town"); fmt.Sprint(result["arrived_at"]) != gate {
		t.Fatalf("arriving from the site should land at the gate facing the road: %v want %s", result["arrived_at"], gate)
	}
	// A site on another road cannot be reached from here.
	other := roadSitesOnLeg(catalog, "Azure Crown Imperial City", "Greenriver Town")[0]
	batch4Exec(t, path, `INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,?,'road_side',900,0)`, other)
	batch4SetCanonicalGameMinute(t, path, 15000)
	if _, err := roadSiteTravel(t, path, world, 5, other); err == nil || !strings.Contains(err.Error(), "lies on the road between") {
		t.Fatalf("a site on another road should send you to its end first, got %v", err)
	}
}

func TestHuntingIsRicherOnAHuntingGroundAndForbiddenAtAShrine(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	ground := roadSitesOnLeg(catalog, "Jadewood Medicine City", "Riverguard City")[0]
	shrine := roadSitesOnLeg(catalog, "Greenriver Town", "Riverguard City")[0]
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, ground)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.hunt", 1, map[string]any{"game_minute": 620, "cooldown_seconds": 0}))
	if fmt.Sprint(result["site_kind"]) != "hunting_ground" || storage.ParseInt(result["site_bonus"]) != huntingGroundRollBonus {
		t.Fatalf("hunt at a hunting ground: %v %v", result["site_kind"], result["site_bonus"])
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, shrine)
	raw, _ := json.Marshal(map[string]any{"cooldown_seconds": 0})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "shrine-hunt", Operation: "exploration.hunt", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "shrine") {
		t.Fatalf("a shrine should refuse the hunt, got %v", err)
	}
}

func TestAWaystationKeepsAStallAndStandsOnTheMerchantsRoad(t *testing.T) {
	path := setupMerchantDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE IF NOT EXISTS shop_state(shop TEXT PRIMARY KEY, last_restock_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS shop_stock(shop TEXT NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, price INTEGER NOT NULL DEFAULT 0, made_here INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(shop,item_id));
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	waystation := roadSitesOnLeg(catalog, "Azure Crown Imperial City", "Riverguard City")[0]
	if catalog.Locations[waystation].RoadSite != "waystation" || catalog.Locations[waystation].Shop == "" {
		t.Fatalf("%s should be a waystation with a stall", waystation)
	}
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=500 WHERE user_id=42`, waystation)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,?,200)`, catalog.Shops[catalog.Locations[waystation].Shop].Currency)
	browse := shopQuery(t, path, world, "shop.browse")
	if fmt.Sprint(browse["kind"]) != "waystation" || len(browse["stock"].([]map[string]any)) == 0 {
		t.Fatalf("browse at the stall: %v", browse)
	}
	result := batch4Result(t, batch4Apply(t, path, world, "shop.buy", 1, map[string]any{"item_id": "recovery_pill", "quantity": 1, "game_minute": 600}))
	if storage.ParseInt(result["quantity"]) != 1 {
		t.Fatalf("buy at the stall: %v", result)
	}
	// Old Hu walks Azure Crown to Riverguard: on that leg he is met from
	// the waystation.
	batch4Exec(t, path, `INSERT INTO merchant_state(merchant,location,destination,depart_game_minute,arrive_game_minute,dwell_until_game_minute,budget,route_index,updated_at) VALUES('old_hu_the_peddler','Azure Crown Imperial City','Riverguard City',0,99999,0,400,1,0)`)
	status := shopQuery(t, path, world, "merchant.status")
	road, _ := status["actor_road"].(map[string]any)
	if road == nil || fmt.Sprint(road["from"]) != "Azure Crown Imperial City" || fmt.Sprint(road["to"]) != "Riverguard City" {
		t.Fatalf("at a waystation the player is on its road: %v", status["actor_road"])
	}
	met := false
	for _, row := range status["merchants"].([]map[string]any) {
		if fmt.Sprint(row["merchant"]) == "old_hu_the_peddler" && row["meetable"] == true {
			met = true
		}
	}
	if !met {
		t.Fatalf("Old Hu on the leg should be reachable from the waystation: %v", status["merchants"])
	}
}
