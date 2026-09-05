package game

import (
	"fmt"
	"math"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

// --- realTimestampForGameMinute: pure unit tests, no DB -------------------

func TestRealTimestampForGameMinuteLinearConversion(t *testing.T) {
	clock := canonicalWorldClock{AnchorGameMinute: 1000, AnchorRealTS: 5000, Scale: 4}
	// 40 game-minutes ahead of anchor, at scale 4 (4 game-minutes per real
	// minute) is 10 real minutes = 600 real seconds ahead of the anchor.
	ts, ok := realTimestampForGameMinute(clock, 1040)
	if !ok {
		t.Fatalf("expected ok=true")
	}
	if math.Abs(ts-5600) > 1e-9 {
		t.Fatalf("ts=%v want 5600", ts)
	}
	// Behind the anchor works the same way, just negative.
	ts, ok = realTimestampForGameMinute(clock, 960)
	if !ok || math.Abs(ts-4400) > 1e-9 {
		t.Fatalf("ts=%v ok=%v want 4400,true", ts, ok)
	}
}

func TestRealTimestampForGameMinuteFrozenClock(t *testing.T) {
	clock := canonicalWorldClock{AnchorGameMinute: 1000, AnchorRealTS: 5000, Scale: 0}
	// A frozen clock (scale 0) never reaches a future game-minute in real
	// time - there is no timestamp to report.
	if _, ok := realTimestampForGameMinute(clock, 1001); ok {
		t.Fatalf("expected ok=false for a target beyond a frozen clock")
	}
	// A target at or before the anchor is already "reached" - the anchor's
	// own timestamp is the answer.
	ts, ok := realTimestampForGameMinute(clock, 1000)
	if !ok || ts != 5000 {
		t.Fatalf("ts=%v ok=%v want 5000,true", ts, ok)
	}
}

// --- exploration.travel + exploration.travel_status, end to end -----------

func setTravelStatusWorldClock(t *testing.T, path string, anchorGameMinute int64, anchorRealTS float64, scale int64) {
	t.Helper()
	state := fmt.Sprintf(`{"anchor_game_minute":%d,"anchor_real_ts":%f,"scale":%d}`, anchorGameMinute, anchorRealTS, scale)
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0)
		ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, state)
}

// travelStatusApply calls exploration.travel_status directly rather than
// through batch4Apply: that shared helper asserts StateVersion > 0, which
// doesn't hold for a read-only query against an actor with no prior
// authoritative mutation yet (a fresh actor's ledger starts at version 0,
// and a query never advances it) - exactly the case for the very first
// travel_status check before any travel has happened.
func travelStatusApply(t *testing.T, path, world string, seq int) map[string]any {
	t.Helper()
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   fmt.Sprintf("travel-status-%d", seq),
		Operation:  "exploration.travel_status",
		ActorID:    42,
		Payload:    []byte("{}"),
	})
	if err != nil {
		t.Fatalf("exploration.travel_status: %v", err)
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("exploration.travel_status: unexpected result type %#v", out.Result)
	}
	return result
}

func TestTravelStatusReflectsInTransitJourneyWithRealTimestamp(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)

	originalEncounterIntn := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return 100, nil } // never trigger a road encounter
	defer func() { roadEncounterIntn = originalEncounterIntn }()

	const anchorGameMinute = int64(1000)
	const scale = int64(4)
	nowTS := float64(time.Now().Unix())
	setTravelStatusWorldClock(t, path, anchorGameMinute, nowTS, scale)

	// Before traveling, status must report not-traveling.
	idle := travelStatusApply(t, path, world, 1)
	if traveling, _ := idle["traveling"].(bool); traveling {
		t.Fatalf("expected traveling=false before any journey, got %v", idle)
	}

	// Fast-travel to the Mortal World's realm hub (mode=hub bypasses the
	// known-destination check and is instant - no transit).
	hub := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 2, map[string]any{
		"destination": "Azure Crown Imperial City",
		"mode":        "hub",
	}))
	if hub["destination"] != "Azure Crown Imperial City" {
		t.Fatalf("hub travel=%v", hub)
	}
	if traveling, _ := hub["traveling"].(bool); traveling {
		t.Fatalf("hub travel must not start a road transit: %v", hub)
	}

	// Now a real road journey to one of the hub's neighbors, auto-known by
	// physical presence at Azure Crown Imperial City. Road travel costs
	// spirit stones; fund the character so the journey isn't rejected.
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=1000 WHERE user_id=42`)
	road := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 3, map[string]any{
		"destination": "Riverguard City",
		"mode":        "known",
	}))
	if road["road_connection"] != true {
		t.Fatalf("expected a road connection Azure Crown Imperial City -> Riverguard City: %v", road)
	}
	traveling, _ := road["traveling"].(bool)
	if !traveling {
		t.Fatalf("expected traveling=true once a nonzero-duration road journey starts: %v", road)
	}
	departureGameMinute := storage.ParseInt(road["departure_game_minute"])
	arrivalGameMinute := storage.ParseInt(road["arrival_game_minute"])
	if arrivalGameMinute <= departureGameMinute {
		t.Fatalf("expected arrival after departure: %v", road)
	}
	departureTS, depOK := road["departure_unix_ts"].(float64)
	arrivalTS, arrOK := road["arrival_unix_ts"].(float64)
	if !depOK || !arrOK {
		t.Fatalf("expected real-time timestamps on the travel result: %v", road)
	}
	wantDelta := float64(arrivalGameMinute-departureGameMinute) / float64(scale) * 60.0
	if math.Abs((arrivalTS-departureTS)-wantDelta) > 1e-6 {
		t.Fatalf("arrival-departure delta=%v want %v", arrivalTS-departureTS, wantDelta)
	}

	// /travel status while mid-journey: same destination, same arrival, and
	// a real countdown timestamp consistent with the travel result.
	status := travelStatusApply(t, path, world, 4)
	if traveling, _ := status["traveling"].(bool); !traveling {
		t.Fatalf("expected travel_status traveling=true mid-journey: %v", status)
	}
	if status["destination"] != "Riverguard City" {
		t.Fatalf("status destination=%v want Riverguard City", status["destination"])
	}
	if storage.ParseInt(status["arrival_game_minute"]) != arrivalGameMinute {
		t.Fatalf("status arrival_game_minute=%v want %v", status["arrival_game_minute"], arrivalGameMinute)
	}
	statusArrivalTS, ok := status["arrival_unix_ts"].(float64)
	if !ok || math.Abs(statusArrivalTS-arrivalTS) > 1e-6 {
		t.Fatalf("status arrival_unix_ts=%v want %v", status["arrival_unix_ts"], arrivalTS)
	}
	if remaining := storage.ParseInt(status["remaining_game_minutes"]); remaining <= 0 {
		t.Fatalf("expected positive remaining_game_minutes mid-journey, got %v", remaining)
	}

	// Fast-forward the world clock past the arrival minute and confirm the
	// transit state clears itself instead of reporting a stale journey.
	setTravelStatusWorldClock(t, path, arrivalGameMinute+5, nowTS, scale)
	arrived := travelStatusApply(t, path, world, 5)
	if traveling, _ := arrived["traveling"].(bool); traveling {
		t.Fatalf("expected traveling=false once the arrival minute has passed: %v", arrived)
	}
}
