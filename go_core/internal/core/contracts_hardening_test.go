package core

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
)

func validRequest(operation string, payload string) Request {
	return Request{
		APIVersion:      APIVersion,
		Operation:       operation,
		IdempotencyKey:  "core-test-key",
		ActorID:         "123456789012345678",
		ExpectedVersion: 7,
		Payload:         json.RawMessage(payload),
	}
}

func requireContractCode(t *testing.T, err *ContractError, code string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected contract error %q, got nil", code)
	}
	if err.Code != code {
		t.Fatalf("expected contract error %q, got %q (%s)", code, err.Code, err.Message)
	}
	if err.Status != 400 {
		t.Fatalf("expected HTTP-style status 400, got %d", err.Status)
	}
}

func TestValidateContractEnvelope(t *testing.T) {
	valid := validRequest("check.resolve", `{"dice":[1,1],"modifier":0,"tn":2}`)
	if err := Validate(valid); err != nil {
		t.Fatalf("valid request rejected: %v", err)
	}

	cases := []struct {
		name string
		edit func(*Request)
		code string
	}{
		{"unsupported operation", func(r *Request) { r.Operation = "cultivation.ascend" }, "unsupported_operation"},
		{"empty operation", func(r *Request) { r.Operation = "" }, "unsupported_operation"},
		{"blank idempotency key", func(r *Request) { r.IdempotencyKey = "   " }, "invalid_idempotency_key"},
		{"idempotency key over 160 runes", func(r *Request) { r.IdempotencyKey = strings.Repeat("界", 161) }, "invalid_idempotency_key"},
		{"blank actor", func(r *Request) { r.ActorID = " " }, "invalid_actor_id"},
		{"non decimal actor", func(r *Request) { r.ActorID = "123abc" }, "invalid_actor_id"},
		{"actor over 32 digits", func(r *Request) { r.ActorID = strings.Repeat("9", 33) }, "invalid_actor_id"},
		{"negative expected version", func(r *Request) { r.ExpectedVersion = -1 }, "invalid_expected_version"},
		{"max expected version", func(r *Request) { r.ExpectedVersion = math.MaxInt64 }, "invalid_expected_version"},
		{"nil payload", func(r *Request) { r.Payload = nil }, "invalid_payload"},
		{"null payload", func(r *Request) { r.Payload = json.RawMessage(`null`) }, "invalid_payload"},
		{"array payload", func(r *Request) { r.Payload = json.RawMessage(`[]`) }, "invalid_payload"},
		{"malformed payload", func(r *Request) { r.Payload = json.RawMessage(`{"dice":`) }, "invalid_payload"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := valid
			tc.edit(&req)
			requireContractCode(t, Validate(req), tc.code)
		})
	}
}

func TestContractErrorPayload(t *testing.T) {
	err := invalid("bad_omen", "Tribulation failed.")
	payload := err.Payload()
	if payload["api_version"] != APIVersion {
		t.Fatalf("api version mismatch: %#v", payload)
	}
	detail, ok := payload["error"].(map[string]string)
	if !ok {
		t.Fatalf("error payload has unexpected shape: %#v", payload["error"])
	}
	if detail["code"] != "bad_omen" || detail["message"] != "Tribulation failed." {
		t.Fatalf("unexpected error detail: %#v", detail)
	}
}

func TestCheckResolveDegreeBoundariesAndValidation(t *testing.T) {
	boundaryCases := []struct {
		margin int64
		want   string
	}{
		{10, "Overwhelming Success"},
		{9, "Strong Success"},
		{5, "Strong Success"},
		{4, "Success"},
		{0, "Success"},
		{-1, "Soft Failure"},
		{-3, "Soft Failure"},
		{-4, "Hard Failure"},
		{-7, "Hard Failure"},
		{-8, "Severe Failure"},
	}
	for _, tc := range boundaryCases {
		if got := degree(tc.margin); got != tc.want {
			t.Errorf("degree(%d) = %q, want %q", tc.margin, got, tc.want)
		}
	}

	extreme := validRequest("check.resolve", `{"dice":[10,10],"modifier":9223372036854775807,"tn":-9223372036854775808}`)
	response, err := Apply(extreme)
	if err != nil {
		t.Fatal(err)
	}
	result := response.Result.(map[string]any)
	if result["total"] != int64(math.MaxInt64) || result["margin"] != int64(math.MaxInt64) {
		t.Fatalf("check arithmetic should saturate rather than wrap: %#v", result)
	}

	invalidDice := []string{
		`{"dice":[1],"modifier":0,"tn":2}`,
		`{"dice":[1,1,1],"modifier":0,"tn":2}`,
		`{"dice":[0,1],"modifier":0,"tn":2}`,
		`{"dice":[1,11],"modifier":0,"tn":2}`,
		`{"dice":"1,1","modifier":0,"tn":2}`,
	}
	for _, payload := range invalidDice {
		_, err := Apply(validRequest("check.resolve", payload))
		requireContractCode(t, err, "invalid_dice")
	}
}

func TestRelationshipUpdateBoundsDefaultsAndUnicodeTruncation(t *testing.T) {
	summary := strings.Repeat("界", 805)
	req := validRequest("relationship.update", `{
        "current":{"trust":9223372036854775807,"grudge":-9223372036854775808,"encounter_count":-9},
        "deltas":{"trust":1,"grudge":-1},
        "summary":"`+summary+`"
    }`)
	response, err := Apply(req)
	if err != nil {
		t.Fatal(err)
	}
	result := response.Result.(map[string]any)
	if result["trust"] != int64(100) || result["grudge"] != int64(-100) {
		t.Fatalf("relationship overflow/clamp failure: trust=%v grudge=%v", result["trust"], result["grudge"])
	}
	if result["respect"] != int64(0) || result["fear"] != int64(0) || result["affection"] != int64(0) || result["debt"] != int64(0) {
		t.Fatalf("missing relationship dimensions should default to zero: %#v", result)
	}
	if result["encounter_count"] != int64(1) {
		t.Fatalf("negative encounter count should normalize then increment: %v", result["encounter_count"])
	}
	if got := len([]rune(result["last_summary"].(string))); got != 800 {
		t.Fatalf("summary rune length = %d, want 800", got)
	}
}

func TestQuestProgressHardening(t *testing.T) {
	huge := int64(math.MaxInt64)
	payload, marshalErr := json.Marshal(map[string]any{
		"progress":       map[string]int64{"steps": 1, "legacy": -20},
		"objectives":     []map[string]any{{"id": "steps", "type": "explore", "count": huge}},
		"objective_type": "explore",
		"amount":         huge,
	})
	if marshalErr != nil {
		t.Fatal(marshalErr)
	}
	req := validRequest("quest.progress", string(payload))
	response, err := Apply(req)
	if err != nil {
		t.Fatal(err)
	}
	result := response.Result.(map[string]any)
	progress := result["progress"].(map[string]int64)
	if progress["steps"] != huge {
		t.Fatalf("quest progress overflowed: got %d want %d", progress["steps"], huge)
	}
	if progress["legacy"] != 0 {
		t.Fatalf("preexisting negative progress should clamp to zero, got %d", progress["legacy"])
	}
	if result["touched"] != true || result["complete"] != true {
		t.Fatalf("unexpected completion flags: %#v", result)
	}

	noMatch := validRequest("quest.progress", `{
        "progress":{"talk":0},
        "objectives":[{"id":"talk","type":"talk","target":"Elder Pine","count":1}],
        "objective_type":"talk","target":"Elder Oak","amount":5
    }`)
	response, err = Apply(noMatch)
	if err != nil {
		t.Fatal(err)
	}
	result = response.Result.(map[string]any)
	if result["touched"] != false || result["complete"] != false {
		t.Fatalf("nonmatching target should not progress quest: %#v", result)
	}

	missingID := validRequest("quest.progress", `{"objectives":[{"type":"explore","count":1}],"objective_type":"explore"}`)
	_, err = Apply(missingID)
	requireContractCode(t, err, "invalid_objective")
}

func TestSceneTransitionNormalizationAndLimits(t *testing.T) {
	location := "  " + strings.Repeat("山", 205) + "  "
	sceneType := "  " + strings.Repeat("realm", 20) + "  "
	sceneKey := "  " + strings.Repeat("秘", 245) + "  "
	metadata := json.RawMessage(`{"realm":"azure","floor":3}`)
	payload, marshalErr := json.Marshal(map[string]any{
		"physical_location": location,
		"scene_type":        sceneType,
		"scene_key":         sceneKey,
		"metadata":          json.RawMessage(metadata),
	})
	if marshalErr != nil {
		t.Fatal(marshalErr)
	}
	response, err := Apply(validRequest("scene.transition", string(payload)))
	if err != nil {
		t.Fatal(err)
	}
	result := response.Result.(map[string]any)
	if got := len([]rune(result["physical_location"].(string))); got != 200 {
		t.Fatalf("physical location rune length = %d, want 200", got)
	}
	if got := len([]rune(result["scene_type"].(string))); got != 80 {
		t.Fatalf("scene type rune length = %d, want 80", got)
	}
	if got := len([]rune(result["scene_key"].(string))); got != 240 {
		t.Fatalf("scene key rune length = %d, want 240", got)
	}
	if got := len([]rune(result["scene_label"].(string))); got != 200 {
		t.Fatalf("default scene label rune length = %d, want 200", got)
	}
	if !strings.HasPrefix(result["scene_key"].(string), result["scene_label"].(string)) {
		t.Fatalf("default scene label should be the display-length prefix of scene key")
	}
	if result["channel_id"] != (*int64)(nil) {
		t.Fatalf("missing channel_id should remain nil, got %#v", result["channel_id"])
	}

	invalidScenes := []string{
		`{"physical_location":" ","scene_type":"expedition","scene_key":"x"}`,
		`{"physical_location":"Town","scene_type":" ","scene_key":"x"}`,
		`{"physical_location":"Town","scene_type":"expedition","scene_key":" "}`,
	}
	for _, raw := range invalidScenes {
		_, contractErr := Apply(validRequest("scene.transition", raw))
		requireContractCode(t, contractErr, "invalid_scene")
	}
}

// The gap that let the wildcard survive: there was a test for a *wrong*
// target ("Elder Oak" against "Elder Pine") and none for a *missing* one.
// `payload.Target == nil` short-circuited the comparison, so a bare event
// progressed every targeted objective of its type the character was carrying.
func TestATargetlessEventDoesNotProgressTargetedObjectives(t *testing.T) {
	untargeted := validRequest("quest.progress", `{
        "progress":{"pine":0,"qiao":0,"any":0},
        "objectives":[
            {"id":"pine","type":"talk","target":"Elder Pine","count":1},
            {"id":"qiao","type":"talk","target":"Steward Qiao","count":1},
            {"id":"any","type":"talk","count":1}
        ],
        "objective_type":"talk","amount":1
    }`)
	response, err := Apply(untargeted)
	if err != nil {
		t.Fatal(err)
	}
	result := response.Result.(map[string]any)
	progress := result["progress"].(map[string]int64)

	// The objective that names nobody is satisfied by talking to anyone.
	if progress["any"] != 1 {
		t.Fatalf("an untargeted objective did not progress: %#v", progress)
	}
	// The two that name someone are not.
	for _, id := range []string{"pine", "qiao"} {
		if progress[id] != 0 {
			t.Fatalf("objective %q progressed on an event with no target: %#v", id, progress)
		}
	}
	if result["complete"] == true {
		t.Fatalf("a targetless event completed a targeted quest: %#v", result)
	}
}

// And the case that must keep working: the right target still counts.
func TestAMatchingTargetStillProgresses(t *testing.T) {
	matching := validRequest("quest.progress", `{
        "progress":{"pine":0},
        "objectives":[{"id":"pine","type":"talk","target":"Elder Pine","count":1}],
        "objective_type":"talk","target":"elder pine","amount":1
    }`)
	response, err := Apply(matching)
	if err != nil {
		t.Fatal(err)
	}
	result := response.Result.(map[string]any)
	progress := result["progress"].(map[string]int64)
	if progress["pine"] != 1 {
		t.Fatalf("a matching target did not progress (case-insensitively): %#v", progress)
	}
}
