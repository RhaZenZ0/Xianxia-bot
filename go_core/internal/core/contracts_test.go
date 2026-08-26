package core

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"reflect"
	"testing"
)

//go:embed testdata/golden.json
var goldenFixtures []byte

type fixture struct {
	Name     string          `json:"name"`
	Request  Request         `json:"request"`
	Expected json.RawMessage `json:"expected"`
}

func decodeNumberSafe(t *testing.T, raw []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestGoldenParity(t *testing.T) {
	var fixtures []fixture
	if err := json.Unmarshal(goldenFixtures, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, item := range fixtures {
		t.Run(item.Name, func(t *testing.T) {
			response, contractErr := Apply(item.Request)
			if contractErr != nil {
				t.Fatal(contractErr)
			}
			actual, err := json.Marshal(response)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(decodeNumberSafe(t, actual), decodeNumberSafe(t, item.Expected)) {
				t.Fatalf("parity mismatch\nactual: %s\nexpected: %s", actual, item.Expected)
			}
		})
	}
}

func TestRejectsUnknownVersion(t *testing.T) {
	_, contractErr := Apply(Request{
		APIVersion: "v2", Operation: "check.resolve", IdempotencyKey: "bad-version",
		ActorID: "42", Payload: json.RawMessage(`{"dice":[1,1],"modifier":0,"tn":2}`),
	})
	if contractErr == nil || contractErr.Code != "unsupported_version" {
		t.Fatalf("expected unsupported_version, got %#v", contractErr)
	}
}
