package game

// A retreat lasts at most two real hours, and the deadline is a real one
// (schema 57, v1.0.0-rc.56).
//
// `duration_game_minutes` was floored at 1 and bounded by nothing. The only
// limit in the game was `days: Range[int, 1, 365]` on the slash command -
// presentation, which any other caller could simply not have - so the engine
// would happily seclude somebody for a millennium. That is the same fault the
// action cooldowns had in fourteen places: a bound that lives in the client is
// not a bound.
//
// The deadline is stored in real seconds rather than game minutes because a GM
// may change the world's time scale, and a deadline in game minutes would
// silently re-size every retreat already under way.

import (
	"encoding/json"
	"math"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func secludeFor(t *testing.T, path, world string, seq int, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(payload)
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "cap-seclusion-" + string(rune('a'+seq)),
		Operation:  "seclusion.start",
		ActorID:    42,
		Payload:    raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func secludeReady(t *testing.T) (string, string) {
	t.Helper()
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location='abode:42' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,property_type,cultivation_level) VALUES(42,'abode:42','Greenriver Town','Quiet Cave','cave_abode',2)`)
	// A retreat's own multipliers read the era (v1.0.0-rc.55) and so does a
	// hand-sat session, which the lockout tests drive to prove the doors
	// opened. The table is empty, which production reads as "no era".
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS world_eras(era_id INTEGER PRIMARY KEY AUTOINCREMENT, world TEXT NOT NULL DEFAULT 'Mortal World',name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',modifiers_json TEXT NOT NULL DEFAULT '{}',started_game_minute INTEGER NOT NULL DEFAULT 0,duration_days INTEGER,active INTEGER NOT NULL DEFAULT 0)`)
	return path, world
}

// Refused, not clamped. House style splits on intent: a number where landing
// near it is fine is clamped, and one where the player must know they got
// something else is a sentence. A player who asked for a year and was silently
// given two hours would be told twice over that they had what they asked for.
func TestARetreatOverTheCapIsRefusedAndNotClamped(t *testing.T) {
	path, world := secludeReady(t)
	_, err := secludeFor(t, path, world, 0, map[string]any{"mode": "qi", "duration_real_minutes": 121})
	if err == nil {
		t.Fatal("a retreat of 121 real minutes was accepted")
	}
	for _, want := range []string{"at most 2 hours", "you asked for 2.0"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal must name %q: %q", want, err.Error())
		}
	}
	// And nothing was written: a refusal that half-secluded somebody would be
	// worse than either answer alone.
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42`)); got != 0 {
		t.Fatalf("%d sessions after a refusal", got)
	}
}

// The old field still works, converted at the world's rate - a client from
// before this release must not break a rolling deploy - but the cap answers
// it honestly rather than quietly giving it two hours it did not ask for.
func TestTheOldFieldIsReadAndThenHeldToTheSameCap(t *testing.T) {
	path, world := secludeReady(t)
	batch4SetCanonicalGameMinute(t, path, 1000)
	// Thirty game minutes at the fixture's stopped clock is thirty real
	// minutes at the baseline rate, which is inside the cap.
	result, err := secludeFor(t, path, world, 1, map[string]any{"mode": "qi", "duration_game_minutes": 30 * int64(fallbackClockScale)})
	if err != nil {
		t.Fatalf("a short legacy retreat was refused: %v", err)
	}
	if got := storage.ParseInt(result["duration_real_minutes"]); got != 30 {
		t.Fatalf("duration_real_minutes=%d, want the 30 the legacy field converts to", got)
	}

	path, world = secludeReady(t)
	_, err = secludeFor(t, path, world, 2, map[string]any{"mode": "qi", "duration_game_minutes": 10 * 1440})
	if err == nil || !strings.Contains(err.Error(), "at most 2 hours") {
		t.Fatalf("ten world-days through the legacy field: %v", err)
	}
}

// The whole reason the deadline is real. A GM speeding the world up or slowing
// it down must not re-size a retreat already under way: what a rate change
// moves is how many game minutes that wall-clock covers, which is what a rate
// change means.
func TestAScaleChangeDoesNotMoveARetreatAlreadyUnderWay(t *testing.T) {
	path, world := secludeReady(t)
	batch4SetCanonicalGameMinute(t, path, 1000)
	result, err := secludeFor(t, path, world, 3, map[string]any{"mode": "qi", "duration_real_minutes": 90})
	if err != nil {
		t.Fatal(err)
	}
	before, _ := strconvFloat(result["ends_real_ts"])
	if before <= 0 {
		t.Fatalf("no real deadline was stored: %v", result["ends_real_ts"])
	}

	// The GM's lever: a new anchor and a new rate.
	batch4Exec(t, path, `UPDATE world_state SET value_json='{"anchor_game_minute":1000,"anchor_real_ts":1,"scale":48}' WHERE key='world_clock'`)

	after := func() float64 {
		v, _ := strconvFloat(actionScalar(t, path, `SELECT ends_real_ts FROM seclusion_sessions WHERE user_id=42`))
		return v
	}()
	if math.Abs(after-before) > 1e-6 {
		t.Fatalf("the deadline moved from %.3f to %.3f when the scale changed", before, after)
	}
	// Stored in real seconds, and roughly where it should be: ninety minutes
	// out, whatever the world clock is doing.
	if left := after - nowSeconds(); left < 89*60 || left > 91*60 {
		t.Fatalf("the deadline is %.1f minutes out, want about 90", left/60)
	}
}

// A stopped clock is a supported state (the dashboard offers scale 0 and
// authority2_test pins `"0" -> 0`). A cap computed in game minutes would be
// `120 * 0 = 0` and refuse every retreat; the real deadline simply does not
// care what the world clock is doing.
func TestAStoppedWorldClockStillAdmitsARetreat(t *testing.T) {
	path, world := secludeReady(t)
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock','{"anchor_game_minute":1000,"anchor_real_ts":1,"scale":0}',0)
		ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json`)
	result, err := secludeFor(t, path, world, 4, map[string]any{"mode": "qi", "duration_real_minutes": 120})
	if err != nil {
		t.Fatalf("a retreat in a frozen world was refused: %v", err)
	}
	ends, _ := strconvFloat(result["ends_real_ts"])
	if left := ends - nowSeconds(); left < 119*60 || left > 121*60 {
		t.Fatalf("the deadline is %.1f minutes out, want about 120", left/60)
	}
}

// Nothing asked for is the longest allowed. The old floor of one game minute
// gave a caller that sent no duration a retreat that ended the instant it
// began, which is not a thing anybody wanted.
func TestARetreatWithNoStatedLengthIsTheLongestAllowed(t *testing.T) {
	path, world := secludeReady(t)
	result, err := secludeFor(t, path, world, 5, map[string]any{"mode": "qi"})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["duration_real_minutes"]); got != seclusionMaxRealMinutes {
		t.Fatalf("an unstated length gave %d minutes, want the cap of %d", got, seclusionMaxRealMinutes)
	}
}

// The settle side of the same rule. Storing the deadline is half of it; the
// other half is that the settle reads it. A GM speeding the world up by a
// factor of twelve mid-retreat runs the game clock past the retreat's
// *projected* end within seconds - and the retreat must still be open,
// because two real hours have not passed.
func TestASpedUpWorldDoesNotEndARetreatEarly(t *testing.T) {
	path, world := secludeReady(t)
	batch4SetCanonicalGameMinute(t, path, 1000)
	if _, err := secludeFor(t, path, world, 6, map[string]any{"mode": "qi", "duration_real_minutes": 120}); err != nil {
		t.Fatal(err)
	}
	projectedEnd := storage.ParseInt(actionScalar(t, path, `SELECT ends_game_minute FROM seclusion_sessions WHERE user_id=42`))

	// The world clock is now far past where the retreat was projected to end.
	batch4SetCanonicalGameMinute(t, path, projectedEnd+50_000)

	raw, _ := json.Marshal(map[string]any{})
	if _, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "cap-sped-settle",
		Operation:  "seclusion.settle",
		ActorID:    42,
		Payload:    raw}); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42 AND status='active'`)); got != 1 {
		t.Fatal("the retreat ended because the game clock ran on, not because two real hours passed")
	}
}
