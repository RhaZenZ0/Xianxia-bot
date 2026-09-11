package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// City gates and districts (v0.36.0): a road journey ends at the gate
// facing the road you came by and leaves by the gate facing the first leg;
// inside the walls, gates, districts, the centre and the shops are a walk
// apart, and a shop or a merchant is reached from any of them.

func districtCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	return catalog
}

func TestARoadJourneyArrivesAtTheGateFacingTheRoad(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	origin := "Riverguard City"
	destination := "Azure Crown Imperial City"
	if _, found := canonicalRoadRoute(catalog, origin, destination, 0); !found {
		t.Skip("no realm-0 road between the two cities")
	}
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=500,vitality=100 WHERE user_id=42`, origin)
	batch4SetCanonicalGameMinute(t, path, 3000)
	previous := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = previous }()

	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 31, map[string]any{"destination": destination, "mode": "known"}))
	route := result["road_route"].([]string)
	wantGate, wantDir, ok := gateFacing(catalog, destination, route[len(route)-2])
	if !ok {
		t.Fatalf("%s has no gate facing %s", destination, route[len(route)-2])
	}
	if fmt.Sprint(result["arrived_at"]) != wantGate || fmt.Sprint(result["arrival_gate"]) != wantDir {
		t.Fatalf("arrived_at=%v (%v) want %s (%s)", result["arrived_at"], result["arrival_gate"], wantGate, wantDir)
	}
	if _, leftDir, ok := gateFacing(catalog, origin, route[1]); ok && fmt.Sprint(result["left_by_gate"]) != leftDir {
		t.Fatalf("left_by_gate=%v want %s", result["left_by_gate"], leftDir)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != wantGate {
		t.Fatalf("location=%q want the gate %q", got, wantGate)
	}
	if parts, _ := result["city_parts"].([]string); len(parts) < 4 {
		t.Fatalf("a capital should list its gates and districts: %v", result["city_parts"])
	}
	// Direction is a property of the road: the same road the other way
	// lands at the opposite side.
	opposite := map[string]string{"North": "South", "South": "North", "East": "West", "West": "East"}
	if _, backDir, ok := gateFacing(catalog, origin, destination); ok {
		if _, dir, ok := gateFacing(catalog, destination, origin); ok && opposite[dir] != backDir {
			t.Fatalf("%s faces %s by the %s gate but %s faces back by the %s gate", destination, origin, dir, origin, backDir)
		}
	}
}

func TestInsideTheWallsEverythingIsAWalkApart(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	city := "Azure Crown Imperial City"
	parts := cityPartsOf(catalog, city)
	if len(parts) < 5 {
		t.Fatalf("parts of %s: %v", city, parts)
	}
	var gate, district string
	for _, part := range parts {
		loc := catalog.Locations[part]
		if loc.Gate != "" && gate == "" {
			gate = part
		}
		if loc.Gate == "" && district == "" {
			district = part
		}
	}
	batch4SetCanonicalGameMinute(t, path, 4000)
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=500 WHERE user_id=42`, gate)
	// Gate -> district -> centre -> district, all instant, none needing discovery.
	for i, dest := range []string{district, city, district} {
		result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 40+i, map[string]any{"destination": dest, "mode": "known"}))
		if result["traveling"] != false || storage.ParseInt(result["travel_minutes"]) != 0 {
			t.Fatalf("walking to %s should be instant: %v", dest, result)
		}
	}
	// A shop is entered from a district, and a merchant in the city is met there.
	shopKey := cityShopKeys(catalog, city)[0]
	shop := catalog.Shops[shopKey]
	batch4Exec(t, path, `INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,?,'shop',0,0)`, shop.Location)
	batch4Apply(t, path, world, "exploration.travel", 45, map[string]any{"destination": shop.Location, "mode": "known"})
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != shop.Location {
		t.Fatalf("location=%q want the shop", got)
	}
	// The shop door opens onto the street, not a district.
	raw, _ := json.Marshal(map[string]any{"destination": district, "mode": "known"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "district-shop-door", Operation: "exploration.travel", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "door opens onto") {
		t.Fatalf("a shop door should open onto the street, got %v", err)
	}
	// A district of another city is not a walk.
	far := cityPartsOf(catalog, "Riverguard City")[0]
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, city)
	raw, _ = json.Marshal(map[string]any{"destination": far, "mode": "known"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "district-far", Operation: "exploration.travel", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "is in Riverguard City") {
		t.Fatalf("a far district should send you to its city first, got %v", err)
	}
}

func TestACityIsLeftByRoadFromAnyOfItsParts(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	city := "Riverguard City"
	parts := cityPartsOf(catalog, city)
	district := ""
	for _, part := range parts {
		if catalog.Locations[part].Gate == "" {
			district = part
		}
	}
	if district == "" {
		t.Fatalf("%s has no district: %v", city, parts)
	}
	neighbour := catalog.Locations[city].Roads[0]
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=500,vitality=100 WHERE user_id=42`, district)
	batch4SetCanonicalGameMinute(t, path, 5000)
	previous := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = previous }()
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 50, map[string]any{"destination": neighbour, "mode": "known"}))
	if result["road_connection"] != true || fmt.Sprint(result["from"]) != district {
		t.Fatalf("leaving from a district should still be a road journey from the city: %v", result)
	}
	if _, dir, ok := gateFacing(catalog, city, neighbour); ok && fmt.Sprint(result["left_by_gate"]) != dir {
		t.Fatalf("left_by_gate=%v want %s", result["left_by_gate"], dir)
	}
}
