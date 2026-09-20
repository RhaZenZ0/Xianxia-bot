package game

// What a retreat is worth, at any world time scale (v1.0.0-rc.56).
//
// `seclusionDailyShare = 0.60` was *named* for the rule - "around 60% of an
// active cultivation day" - and was spent as `daily * seclusionDailyShare /
// 0.6`, which is exactly 1.0 and did nothing whatsoever. The rule really lived
// in an uncommented `seclusionSessionsPerDay = 1.2` one file away, and a count
// of sessions only means a share of active play at one scale: at the shipped
// WORLD_TIME_SCALE=4 it happened to be 60%, at 2 it was 30%, and at 8 it was
// 120% - an operator who sped their world up made closed-door cultivation
// strictly better than playing, and nothing anywhere said so.
//
// The share is stated once now and the rate derived from the cooldown it is a
// share of, so this is the thing the old constant could never be asked: does
// the rule hold at a scale nobody measured it on.

import (
	"math"
	"testing"

	"xianxia/core/internal/worlddata"
)

// A game day of hand-sat cultivation, in sessions, at this scale: the number
// the share is a share of. Computed here from the world's own two facts - a
// game day is gameMinutesPerDay/scale real minutes, and active play fits one
// session per cultivate cooldown - rather than read off the production
// helper, so the two cannot agree by sharing a mistake.
func handSatSessionsPerGameDay(scale int64) float64 {
	realMinutes := float64(gameMinutesPerDay) / float64(scale)
	return realMinutes / (float64(cooldownSecondsFor(cooldownCultivate)) / 60)
}

func TestARetreatIsWorthTheSameShareAtEveryScale(t *testing.T) {
	for _, scale := range []int64{2, 4, 8, 12, 60} {
		got := seclusionSessionsPerGameDay(scale)
		want := seclusionShareOfActive * handSatSessionsPerGameDay(scale)
		if math.Abs(got-want) > 1e-9 {
			t.Errorf("scale %d: %.4f sessions a game day, want %.4f", scale, got, want)
		}
		// The share itself, said the way a player would ask it: how does a
		// day behind the door compare with the same day spent sitting down?
		if share := got / handSatSessionsPerGameDay(scale); math.Abs(share-1.25) > 1e-9 {
			t.Errorf("scale %d: a retreat is worth %.4f of active play, want 1.25", scale, share)
		}
	}
}

// The premium is deliberate and it is the one number a reader should be able
// to find: the door is shut, so a retreat pays more than the same wall-clock
// spent playing. A share below 1 would be the old rule wearing the new shape.
func TestTheClosedDoorIsAPremiumAndNotADiscount(t *testing.T) {
	if seclusionShareOfActive <= 1 {
		t.Fatalf("the share is %.2f; a retreat that pays less than playing asks nothing and gives nothing", seclusionShareOfActive)
	}
	if seclusionShareOfActive > 2 {
		t.Fatalf("the share is %.2f; a retreat worth twice the play is the whole game", seclusionShareOfActive)
	}
}

// The operator's cooldown is the calendar of the whole game (rc.5), so the
// retreat has to follow it: halve the wait between hand-sat sessions and a
// day of them is worth twice as much, and so is a day behind the door.
func TestTheRetreatFollowsTheCooldownItIsAShareOf(t *testing.T) {
	t.Setenv("CULTIVATE_COOLDOWN_MINUTES", "180")
	slow := seclusionSessionsPerGameDay(4)
	t.Setenv("CULTIVATE_COOLDOWN_MINUTES", "90")
	quick := seclusionSessionsPerGameDay(4)
	if math.Abs(quick-2*slow) > 1e-9 {
		t.Fatalf("halving the cooldown gave %.4f, want twice %.4f", quick, slow)
	}
}

// A stopped clock is a supported state - the dashboard offers scale 0 and
// authority2_test pins `"0" -> 0`. No game minute passes with one, so this
// rate is never actually spent; it is still read by the projection the start
// prints, and a division by zero there would print an infinity.
func TestAStoppedClockPricesTheRetreatAtTheWorldsOwnRate(t *testing.T) {
	stopped := seclusionSessionsPerGameDay(0)
	running := seclusionSessionsPerGameDay(fallbackClockScale)
	if math.Abs(stopped-running) > 1e-9 {
		t.Fatalf("a stopped clock priced the retreat at %.4f, want the baseline %.4f", stopped, running)
	}
	if math.IsInf(stopped, 0) || math.IsNaN(stopped) || stopped <= 0 {
		t.Fatalf("a stopped clock priced the retreat at %v", stopped)
	}
}

// And the whole of it through the gain function, so a term dropped between
// the rate and the payment is caught here rather than in a player's ledger.
func TestTheDailyGainCarriesTheRateAtEveryScale(t *testing.T) {
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	character := map[string]any{
		"realm_index": int64(2), "phase": int64(4), "body_realm_index": int64(0), "body_phase": int64(1),
		"attributes_json": `{"will":5,"body":3,"insight":2}`}
	// Every multiplier at 1 so what is left is the pace and the rate.
	base := seclusionDailyGainGo(catalog, character, "qi", 1, 1, 1, 4)
	for _, scale := range []int64{2, 8, 12} {
		got := seclusionDailyGainGo(catalog, character, "qi", 1, 1, 1, scale)
		// A game day is fewer real minutes the faster the world runs, so a
		// game day of retreat is worth proportionally less - the one thing
		// the old count could not express.
		want := float64(base) * 4 / float64(scale)
		if math.Abs(float64(got)-want) > 1.5 {
			t.Errorf("scale %d paid %d a game day, want about %.1f", scale, got, want)
		}
	}
}
