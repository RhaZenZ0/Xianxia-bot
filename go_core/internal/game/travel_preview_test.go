package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// The trip before it is taken (v1.26.0). The preview answers through
// planTravelTx, the planner exploration.travel itself walks, so these hold the
// two to the same road and the same toll, and the preview to costing nothing.

func travelPreview(t *testing.T, path, world, destination string) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"destination": destination, "mode": "known"})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: "exploration.travel_preview", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	res, _ := out.Result.(map[string]any)
	return res, nil
}

func TestThePreviewIsTheJourneyItShows(t *testing.T) {
	// The journey rolls its road's encounters; the dice are lent so nothing
	// here depends on one landing (the toll and the road are not rolls).
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	path, world := instantTravelDB(t)
	preview, err := travelPreview(t, path, world, "Azure Crown Imperial City")
	if err != nil {
		t.Fatalf("a known capital a road away was refused a preview: %v", err)
	}
	if preview["road_connection"] != true || storage.ParseInt(preview["road_hops"]) < 1 {
		t.Fatalf("the preview found no road: %v", preview)
	}
	cost := storage.ParseInt(preview["travel_cost_spirit_stones"])
	if cost <= 0 || preview["currency"] == "" || preview["affordable"] != true {
		t.Fatalf("the preview priced the road at %d %v (affordable %v)", cost, preview["currency"], preview["affordable"])
	}
	before := storage.ParseInt(actionScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`))
	if again, _ := travelPreview(t, path, world, "Azure Crown Imperial City"); storage.ParseInt(again["travel_cost_spirit_stones"]) != cost {
		t.Fatal("two previews of one road disagreed")
	}
	if after := storage.ParseInt(actionScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`)); after != before {
		t.Fatalf("a preview spent %d stones; it is a read", before-after)
	}
	if loc := fmtAny(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); loc != "Greenriver Town" {
		t.Fatalf("a preview moved the traveller to %s", loc)
	}
	trip := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{"destination": "Azure Crown Imperial City", "mode": "known"}))
	if got := storage.ParseInt(trip["travel_cost_spirit_stones"]); got != cost {
		t.Fatalf("the journey charged %d and the preview showed %d", got, cost)
	}
	if fmt.Sprint(trip["road_route"]) != fmt.Sprint(preview["road_route"]) {
		t.Fatalf("the journey walked %v and the preview showed %v", trip["road_route"], preview["road_route"])
	}
	if fmt.Sprint(trip["arrived_at"]) != fmt.Sprint(preview["arrives_at"]) {
		t.Fatalf("the journey arrived at %v and the preview said %v", trip["arrived_at"], preview["arrives_at"])
	}
	if paid := before - storage.ParseInt(actionScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`)); paid != cost {
		t.Fatalf("the purse lost %d for a %d-stone road", paid, cost)
	}
}

func TestThePreviewRefusesInTheJourneysWords(t *testing.T) {
	path, world := instantTravelDB(t)
	_, previewErr := travelPreview(t, path, world, "Frostwatch City")
	_, tripErr := batch4ApplyErr(path, world, "exploration.travel", 42, 1, map[string]any{"destination": "Frostwatch City", "mode": "known"})
	if previewErr == nil || tripErr == nil {
		t.Fatalf("an undiscovered city was not refused: preview %v, journey %v", previewErr, tripErr)
	}
	if !strings.Contains(previewErr.Error(), "has not been discovered") || previewErr.Error() != tripErr.Error() {
		t.Fatalf("the preview refused %q and the journey %q", previewErr, tripErr)
	}
}

func TestAShortPurseIsShownBeforeItIsRefused(t *testing.T) {
	path, world := instantTravelDB(t)
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=0 WHERE user_id=42`)
	syncPurse(t, path)
	preview, err := travelPreview(t, path, world, "Azure Crown Imperial City")
	if err != nil {
		t.Fatalf("an empty purse refused the preview itself: %v", err)
	}
	if preview["affordable"] != false || storage.ParseInt(preview["balance"]) != 0 {
		t.Fatalf("an empty purse was shown as affordable: %v", preview)
	}
}
