package game

// How long a player waits was never the caller's to say (v1.0.0-rc.56).
//
// Fourteen actions read their own cooldown off the request payload, and seven
// of them had no floor at all - a caller sending `cooldown_seconds: 0` served
// no wait whatsoever. That is `rejectCallerGameMinute`'s fault in a second
// place: a bound that lives in the client is not a bound. The waits are the
// engine's now, stated once in `actionCooldowns`.
//
// The checks here are behavioural wherever they can be. A grep would happily
// pass on a refusal somebody had commented out, and the one thing worth
// proving is that the wait a player actually serves is the engine's.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// The operations that used to take their wait off the payload, one per file
// that had to change, with the field each of them carried.
var waitsTheCallerUsedToSend = []struct{ operation, field string }{
	{"cultivation.train", "cooldown_seconds"},
	{"exploration.explore", "cooldown_seconds"},
	{"exploration.hunt", "cooldown_seconds"},
	{"secret_realm.enter", "cooldown_seconds"},
	{"aptitude.evolve", "cooldown_seconds"},
	{"perfection.quest", "quest_cooldown_seconds"},
	{"perfection.trial", "trial_cooldown_seconds"},
	{"manual.study", "cooldown_seconds"},
	{"qi.refine", "cooldown_seconds"},
	{"dao.dual_cultivate", "cooldown_seconds"},
}

func TestAWaitIsNotTheCallersToSend(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)

	for _, each := range waitsTheCallerUsedToSend {
		// Zero is the value that mattered: seven of the fourteen sites had
		// no floor, so this is the payload that used to buy a free action.
		payload, _ := json.Marshal(map[string]any{each.field: 0})
		_, err := ApplyWithWorld(path, world, ActionRequest{
			APIVersion: authoritativeAPIVersion,
			ActionID:   "wait-forged-" + each.operation,
			Operation:  each.operation,
			ActorID:    42,
			Payload:    payload})
		want := "client-supplied " + each.field + " is forbidden"
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%s carrying %s: err=%v, want %q", each.operation, each.field, err, want)
		}
	}
}

// And the other half, which no refusal can show: that the wait the engine
// then serves is its own. A caller who cannot send a short wait but is
// served none anyway has gained exactly what the field used to give them.
func TestTheEngineServesItsOwnWait(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	clearCooldowns(t, path, 42)

	batch4Apply(t, path, world, "cultivation.train", 1, map[string]any{"game_minute": 600})

	_, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "wait-served-second",
		Operation:  "cultivation.train",
		ActorID:    42,
		Payload:    json.RawMessage(`{}`)})
	if err == nil || !strings.Contains(err.Error(), "cultivation cooldown remaining") {
		t.Fatalf("a second session inside the wait: err=%v", err)
	}
	// Not merely "some wait": the table's own, to within the second the action
	// took. A handler quietly falling back to the old five-minute floor would
	// pass the line above and fail here.
	//
	// It deliberately does not pin *which* number ships. It used to assert
	// `want == 180*60`, so the owner retuning the pace turned an ownership
	// test red - and a gate that pins how a rule is written rather than that
	// it holds fails exactly when the decision behind it is taken again
	// (v1.0.8, and v1.0.13's panel window made the same call). What is held is
	// that the wait served is the one the table states, whatever that is; the
	// floor below keeps the old five minutes from creeping back.
	var remaining int64
	if _, scanErr := fmt.Sscanf(err.Error(), "cultivation cooldown remaining: %d", &remaining); scanErr != nil {
		t.Fatalf("cannot read the wait out of %q: %v", err.Error(), scanErr)
	}
	want := cooldownSecondsFor(cooldownCultivate)
	if want <= 300 {
		t.Fatalf("the cultivate wait is %ds, at or under the five-minute floor the handlers used to fall back to", want)
	}
	if remaining < want-5 || remaining > want {
		t.Fatalf("served a wait of %ds, want %ds", remaining, want)
	}
}

func TestAnUnknownWaitIsAnHourAndNeverNothing(t *testing.T) {
	// `gradeIndex` answering 0 for a grade nobody has is the lesson this
	// repeats: a fallback that looks like a value is not a sentinel. A wait
	// the table forgot must cost something, or forgetting it is the same as
	// the caller-supplied zero this release removed.
	if got := cooldownSecondsFor("no_such_action"); got != 3600 {
		t.Fatalf("an unnamed wait is %ds, want an hour", got)
	}
	for action := range actionCooldowns {
		if got := cooldownSecondsFor(action); got <= 0 {
			t.Fatalf("%s serves %ds", action, got)
		}
	}
}

func TestEveryNamedWaitHasAnEntry(t *testing.T) {
	for _, action := range []string{
		cooldownCultivate, cooldownExplore, cooldownHunt, cooldownSecretRealm,
		cooldownAptitude, cooldownPerfectQuest, cooldownPerfectTrial,
		cooldownForbidden, cooldownQiRefine, cooldownDaoDual,
		cooldownGhostHarvest, cooldownGhostAppease,
	} {
		if _, known := actionCooldowns[action]; !known {
			t.Errorf("%q is a named wait with no entry, so it silently costs an hour", action)
		}
	}
}

func TestTheOperatorsKeyIsReadAndBounded(t *testing.T) {
	t.Setenv("EXPLORE_COOLDOWN_MINUTES", "45")
	if got := cooldownSecondsFor(cooldownExplore); got != 45*60 {
		t.Fatalf("an operator's 45 minutes served %ds", got)
	}
	// `.env` is the baseline and not the last word, the rc.39 rule - but a
	// value that is not a wait at all leaves the engine's own standing
	// rather than becoming one.
	for _, junk := range []string{"", "   ", "soon", "0", "-30", "99999999"} {
		t.Setenv("EXPLORE_COOLDOWN_MINUTES", junk)
		if got := cooldownSecondsFor(cooldownExplore); got != 20*60 {
			t.Fatalf("%q served %ds, want the engine's own 20 minutes", junk, got)
		}
	}
}

// The two keys that share a value share it deliberately: an aptitude
// evolution and a dao-partnered session have always been paced with
// cultivation, and Python sent the cultivate cooldown for both.
func TestWhatIsPacedWithCultivationStaysPacedWithIt(t *testing.T) {
	t.Setenv("CULTIVATE_COOLDOWN_MINUTES", "90")
	for _, action := range []string{cooldownCultivate, cooldownAptitude, cooldownDaoDual} {
		if got := cooldownSecondsFor(action); got != 90*60 {
			t.Fatalf("%s served %ds, want the cultivate wait", action, got)
		}
	}
}
