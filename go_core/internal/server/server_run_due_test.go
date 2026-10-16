package server

import (
	"errors"
	"net/http"
	"testing"

	"xianxia/core/internal/simulation"
)

// RunDue commits system by system and returns the runs it got through beside
// the error that stopped it. The handler used to answer 400 and drop them, so
// an event the autonomous system had already committed reached the bot as
// nothing at all and sat active with no thread.
func TestATickErrorAfterACommittedRunStillAnswersWithTheRuns(t *testing.T) {
	committed := []simulation.Run{{
		System: "autonomous_world_events",
		Events: []simulation.SpawnedWorldEvent{{EventKey: "auto:test:1", Title: "A Test Event"}},
	}}
	status, body := runDueAnswer(committed, errors.New("advanced_world: boom"))
	if status != http.StatusOK {
		t.Fatalf("a tick that committed a run answered %d, so the bot never hears of its events", status)
	}
	runs, ok := body["runs"].([]simulation.Run)
	if !ok || len(runs) != 1 || len(runs[0].Events) != 1 {
		t.Fatalf("the committed run did not come back with the answer: %#v", body)
	}
	if body["error"] != "advanced_world: boom" {
		t.Fatalf("the error that stopped the tick was not carried beside the runs: %#v", body)
	}
}

func TestATickThatDidNothingStillAnswers400(t *testing.T) {
	for name, runs := range map[string][]simulation.Run{"nil": nil, "empty": {}} {
		status, body := runDueAnswer(runs, errors.New("could not open"))
		if status != http.StatusBadRequest || body["error"] != "simulation_failed" {
			t.Fatalf("%s runs with an error answered %d %#v, want the old 400", name, status, body)
		}
	}
	status, body := runDueAnswer([]simulation.Run{}, nil)
	if status != http.StatusOK || body["error"] != nil {
		t.Fatalf("a clean tick answered %d %#v", status, body)
	}
}
