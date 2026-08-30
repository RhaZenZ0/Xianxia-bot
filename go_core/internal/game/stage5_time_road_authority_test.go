package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func TestStage5AllAuthoritativeMutationsRejectCallerGameMinute(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	for operation := range authoritativeMutations {
		t.Run(operation, func(t *testing.T) {
			raw, err := json.Marshal(map[string]any{"game_minute": 999999999})
			if err != nil {
				t.Fatal(err)
			}
			_, err = ApplyWithWorld(path, world, ActionRequest{
				APIVersion: authoritativeAPIVersion,
				ActionID:   "stage5-forged-time-" + strings.ReplaceAll(operation, ".", "-"),
				Operation:  operation,
				ActorID:    42,
				Payload:    raw,
			})
			if err == nil || !strings.Contains(err.Error(), "client-supplied game_minute is forbidden") {
				t.Fatalf("err=%v", err)
			}
		})
	}
}

func TestStage5ReplayDoesNotBypassForgedTimeRejection(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 2500)

	valid, err := json.Marshal(map[string]any{"attribute": "body", "tn": 10, "label": "stage5 replay guard"})
	if err != nil {
		t.Fatal(err)
	}
	request := ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "stage5-replay-forged-time",
		Operation:  "check.resolve",
		ActorID:    42,
		Payload:    valid,
	}
	if _, err := ApplyWithWorld(path, world, request); err != nil {
		t.Fatal(err)
	}

	forged, err := json.Marshal(map[string]any{
		"attribute": "body", "tn": 10, "label": "stage5 replay guard", "game_minute": 999999999,
	})
	if err != nil {
		t.Fatal(err)
	}
	request.Payload = forged
	if _, err := ApplyWithWorld(path, world, request); err == nil ||
		!strings.Contains(err.Error(), "client-supplied game_minute is forbidden") {
		t.Fatalf("err=%v", err)
	}
}

func TestStage5AllAuthoritativeQueriesRejectCallerGameMinute(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	raw, err := json.Marshal(map[string]any{"game_minute": 123})
	if err != nil {
		t.Fatal(err)
	}
	for operation := range authoritativeQueries {
		t.Run(operation, func(t *testing.T) {
			_, err := ApplyWithWorld(path, world, ActionRequest{
				APIVersion: authoritativeAPIVersion,
				Operation:  operation,
				ActorID:    42,
				Payload:    raw,
			})
			if err == nil || !strings.Contains(err.Error(), "client-supplied game_minute is forbidden") {
				t.Fatalf("err=%v", err)
			}
		})
	}
}

func TestStage5RoadTravelUsesCanonicalClockDurationAndDanger(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	current := "Riverguard City"
	destination := catalog.Locations[current].Roads[0]
	batch4Exec(t, path, `UPDATE characters SET location=?,vitality=100,spirit_stones=100 WHERE user_id=42`, current)
	batch4SetCanonicalGameMinute(t, path, 3000)

	previous := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = previous }()

	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 901, map[string]any{
		"destination": destination,
		"mode":        "known",
	}))

	if got := storage.ParseInt(result["departure_game_minute"]); got != 3000 {
		t.Fatalf("departure_game_minute=%d", got)
	}
	travelMinutes := storage.ParseInt(result["travel_minutes"])
	if travelMinutes <= 0 {
		t.Fatalf("travel_minutes=%d", travelMinutes)
	}
	if got := storage.ParseInt(result["arrival_game_minute"]); got != 3000+travelMinutes {
		t.Fatalf("arrival_game_minute=%d want %d", got, 3000+travelMinutes)
	}
	danger := storage.ParseInt(result["road_danger"])
	if danger < 5 || danger > 45 {
		t.Fatalf("road_danger=%d", danger)
	}
	if chance := storage.ParseInt(result["road_encounter_chance_percent"]); chance != danger {
		t.Fatalf("encounter chance=%d danger=%d", chance, danger)
	}
	if result["road_encounter"] != nil {
		t.Fatalf("unexpected encounter=%v", result["road_encounter"])
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT vitality FROM characters WHERE user_id=42`)); got != 100 {
		t.Fatalf("vitality=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT game_minute FROM domain_events WHERE actor_id=42 AND event_type='travel_completed' ORDER BY event_id DESC LIMIT 1`)); got != 3000 {
		t.Fatalf("domain event game_minute=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_state WHERE key=?`, roadTransitStateKey(42))); got != 1 {
		t.Fatalf("road transit rows=%d", got)
	}

	checkPayload, err := json.Marshal(map[string]any{"attribute": "body", "tn": 10, "label": "during road transit"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "stage5-road-transit-blocked",
		Operation:  "check.resolve",
		ActorID:    42,
		Payload:    checkPayload,
	})
	if err == nil || !strings.Contains(err.Error(), "road journey") {
		t.Fatalf("transit gate err=%v", err)
	}

	arrival := storage.ParseInt(result["arrival_game_minute"])
	batch4SetCanonicalGameMinute(t, path, arrival)
	if _, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "stage5-road-transit-arrived",
		Operation:  "check.resolve",
		ActorID:    42,
		Payload:    checkPayload,
	}); err != nil {
		t.Fatalf("post-arrival action: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_state WHERE key=?`, roadTransitStateKey(42))); got != 0 {
		t.Fatalf("expired road transit rows=%d", got)
	}
}

func TestStage5RoadEncounterAddsDelayAndAppliesNonlethalDanger(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	current := "Riverguard City"
	destination := catalog.Locations[current].Roads[0]
	origin := catalog.Locations[current]
	dest := catalog.Locations[destination]
	base := canonicalRoadTravelProfile(origin, dest, 0)

	batch4Exec(t, path, `UPDATE characters SET location=?,vitality=12,spirit_stones=100 WHERE user_id=42`, current)
	batch4SetCanonicalGameMinute(t, path, 4000)

	rolls := []int{0, 2}
	index := 0
	previous := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) {
		if index >= len(rolls) {
			return 0, fmt.Errorf("unexpected road RNG call %d", index)
		}
		value := rolls[index]
		index++
		if value >= n {
			value = n - 1
		}
		return value, nil
	}
	defer func() { roadEncounterIntn = previous }()

	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 902, map[string]any{
		"destination": destination,
		"mode":        "known",
	}))
	encounter, ok := result["road_encounter"].(map[string]any)
	if !ok {
		t.Fatalf("road_encounter=%T %v", result["road_encounter"], result["road_encounter"])
	}
	if encounter["kind"] != "spirit_beast" {
		t.Fatalf("kind=%v", encounter["kind"])
	}
	delay := storage.ParseInt(encounter["delay_minutes"])
	if delay <= 0 {
		t.Fatalf("delay=%d", delay)
	}
	if got := storage.ParseInt(result["travel_minutes"]); got != base.TravelMinutes+delay {
		t.Fatalf("travel_minutes=%d base=%d delay=%d", got, base.TravelMinutes, delay)
	}
	damage := storage.ParseInt(encounter["vitality_damage"])
	if damage <= 0 {
		t.Fatalf("damage=%d", damage)
	}
	vitality := storage.ParseInt(actionScalar(t, path, `SELECT vitality FROM characters WHERE user_id=42`))
	if vitality < 1 || vitality >= 12 {
		t.Fatalf("vitality=%d", vitality)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM event_log WHERE user_id=42 AND event_type='road_encounter'`)); got != 1 {
		t.Fatalf("road encounter events=%d", got)
	}
}

func TestStage5QuestProgressRejectsCallerGameMinute(t *testing.T) {
	path := setupActionDB(t)
	raw, err := json.Marshal(map[string]any{
		"quest_key":      "first_steps",
		"objectives":     []map[string]any{},
		"objective_type": "talk",
		"amount":         1,
		"game_minute":    123,
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = Apply(path, ActionRequest{Operation: "quest.progress", ActorID: 42, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "client-supplied game_minute is forbidden") {
		t.Fatalf("err=%v", err)
	}
}

func TestStage6MultiHopRoadTravelUsesShortestCanonicalRouteAndCost(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}

	origin := "Riverguard City"
	destination := ""
	var expected roadRoutePlan
	for candidate := range catalog.Locations {
		plan, found := canonicalRoadRoute(catalog, origin, candidate, 0)
		if found && len(plan.Legs) >= 2 {
			destination = candidate
			expected = plan
			break
		}
	}
	if destination == "" {
		t.Fatal("world catalog has no multi-hop realm-0 road route")
	}

	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=1000,vitality=100 WHERE user_id=42`, origin)
	batch4Exec(t, path, `INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?, 'test',0,0)`, 42, destination)
	batch4SetCanonicalGameMinute(t, path, 5000)

	previous := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = previous }()

	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 903, map[string]any{
		"destination": destination,
		"mode":        "known",
	}))
	if got := storage.ParseInt(result["road_hops"]); got != int64(len(expected.Legs)) {
		t.Fatalf("road_hops=%d want=%d result=%v", got, len(expected.Legs), result)
	}
	if got := storage.ParseInt(result["travel_cost_spirit_stones"]); got != expected.Cost {
		t.Fatalf("cost=%d want=%d", got, expected.Cost)
	}
	if got := storage.ParseInt(result["travel_minutes"]); got != expected.TravelMinutes {
		t.Fatalf("travel_minutes=%d want=%d", got, expected.TravelMinutes)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`)); got != 1000-expected.Cost {
		t.Fatalf("spirit_stones=%d want=%d", got, 1000-expected.Cost)
	}
	for _, location := range expected.Nodes[1:] {
		if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location=?`, location)); got != 1 {
			t.Fatalf("route location %q discovery rows=%d", location, got)
		}
	}
}

func TestStage6RoadTravelRejectsInsufficientFundsAtomically(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	origin := "Riverguard City"
	destination := catalog.Locations[origin].Roads[0]
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=0 WHERE user_id=42`, origin)
	batch4SetCanonicalGameMinute(t, path, 6000)

	_, err = ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "stage6-road-cost-reject",
		Operation:  "exploration.travel",
		ActorID:    42,
		Payload: func() json.RawMessage {
			raw, marshalErr := json.Marshal(map[string]any{"destination": destination, "mode": "known"})
			if marshalErr != nil {
				t.Fatal(marshalErr)
			}
			return raw
		}(),
	})
	if err == nil || !strings.Contains(err.Error(), "spirit stones") {
		t.Fatalf("err=%v", err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != origin {
		t.Fatalf("location=%q", got)
	}
}
