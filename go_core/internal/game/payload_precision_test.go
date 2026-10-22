package game

import (
	"encoding/json"
	"strings"
	"testing"
)

// A JSON number decoded into `any` is a float64, and a Discord snowflake is
// above 2^53: the canonicalizer that writes the engine's game_minute over a
// payload used to round every member id a player named (v1.38.0). The claim
// is byte-exactness, not a parse: the id comes out spelled as it went in.
func TestCanonicalisingAPayloadKeepsASnowflakeExact(t *testing.T) {
	raw := json.RawMessage(`{"partner_user_id":987654321098765432,"stakes":"a cup of tea","quantity":2,"ratio":0.25,"flag":true,"nested":{"user_id":1234567890123456789}}`)
	out, err := canonicalPayloadWithGameMinute(raw, 777)
	if err != nil {
		t.Fatalf("canonicalise: %v", err)
	}
	text := string(out)
	for _, want := range []string{`"partner_user_id":987654321098765432`, `"user_id":1234567890123456789`, `"game_minute":777`, `"quantity":2`, `"ratio":0.25`, `"flag":true`, `"stakes":"a cup of tea"`} {
		if !strings.Contains(text, want) {
			t.Fatalf("the canonical payload lost %s: %s", want, text)
		}
	}
	var decoded struct {
		Partner    int64 `json:"partner_user_id"`
		GameMinute int64 `json:"game_minute"`
	}
	if err := json.Unmarshal(out, &decoded); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if decoded.Partner != 987654321098765432 || decoded.GameMinute != 777 {
		t.Fatalf("a handler reading the canonical payload sees partner %d at minute %d", decoded.Partner, decoded.GameMinute)
	}
}

// The caller's own game_minute is refused before this point
// (rejectCallerGameMinute); here an empty or null payload still gets the
// engine's minute, the shape every no-argument action sends.
func TestCanonicalisingAnEmptyPayloadStillWritesTheMinute(t *testing.T) {
	for _, raw := range []json.RawMessage{nil, json.RawMessage(``), json.RawMessage(`null`), json.RawMessage(`{}`)} {
		out, err := canonicalPayloadWithGameMinute(raw, 5)
		if err != nil {
			t.Fatalf("%q: %v", string(raw), err)
		}
		if string(out) != `{"game_minute":5}` {
			t.Fatalf("%q canonicalised to %s", string(raw), string(out))
		}
	}
}
