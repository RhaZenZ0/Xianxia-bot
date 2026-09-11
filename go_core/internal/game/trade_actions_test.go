package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// Player-to-player trade (v0.39.0): offered at the inn, confirmed on both
// sides, both hands checked again at the accept.

func setupTradeDB(t *testing.T) (string, string) {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE trade_offers(offer_id INTEGER PRIMARY KEY AUTOINCREMENT, from_user_id INTEGER NOT NULL, to_user_id INTEGER NOT NULL, location TEXT NOT NULL, give_json TEXT NOT NULL DEFAULT '{}', give_stones INTEGER NOT NULL DEFAULT 0, want_json TEXT NOT NULL DEFAULT '{}', want_stones INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', created_game_minute INTEGER NOT NULL DEFAULT 0, expires_game_minute INTEGER NOT NULL DEFAULT 0, resolved_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	catalog := districtCatalog(t)
	inn := cityInn(catalog, "Greenriver Town")
	if inn == "" {
		t.Fatal("Greenriver Town has no inn")
	}
	return path, inn
}

func tradeApply(t *testing.T, path, world, op string, actor int64, seq int, payload map[string]any) (map[string]any, error) {
	t.Helper()
	if rawMinute, ok := payload["game_minute"]; ok {
		batch4SetCanonicalGameMinute(t, path, storage.ParseInt(rawMinute))
		payload = clonePayloadWithoutGameMinute(payload)
	}
	raw, _ := json.Marshal(payload)
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("trade-%s-%d-%d", op, actor, seq), Operation: op, ActorID: actor, Payload: raw})
	if err != nil {
		return nil, err
	}
	return batch4Result(t, out), nil
}

func TestATradeIsOfferedAtTheInnAndStruckWhenTheOtherSideAccepts(t *testing.T) {
	path, inn := setupTradeDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=100 WHERE user_id IN (42,43)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',3),(43,'spirit_iron',5)`)
	offer := map[string]any{"to_user_id": 43, "give_items": map[string]int64{"recovery_pill": 2}, "give_stones": 10, "want_items": map[string]int64{"spirit_iron": 4}, "want_stones": 0, "game_minute": 1000}
	// Not at the inn: refused.
	if _, err := tradeApply(t, path, world, "trade.offer", 42, 1, offer); err == nil || !strings.Contains(err.Error(), "inn") {
		t.Fatalf("a trade away from the inn should be refused, got %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, inn)
	if _, err := tradeApply(t, path, world, "trade.offer", 42, 2, offer); err == nil || !strings.Contains(err.Error(), "is not at") {
		t.Fatalf("the other side must be at the inn too, got %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=43`, inn)
	result, err := tradeApply(t, path, world, "trade.offer", 42, 3, offer)
	if err != nil {
		t.Fatal(err)
	}
	offerID := storage.ParseInt(result["offer_id"])
	if offerID <= 0 || result["offered"] != true || fmt.Sprint(result["to_name"]) != "Target Test" {
		t.Fatalf("offer: %v", result)
	}
	// Nothing has moved yet.
	if got := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`)); got != 3 {
		t.Fatalf("an offer moves nothing: pills=%d", got)
	}
	// The wrong side cannot accept; the receiver sees it in their status.
	if _, err := tradeApply(t, path, world, "trade.accept", 42, 1, map[string]any{"offer_id": offerID, "game_minute": 1010}); err == nil || !strings.Contains(err.Error(), "not made to you") {
		t.Fatalf("the offerer cannot accept their own offer, got %v", err)
	}
	status, err := tradeApply(t, path, world, "trade.status", 43, 1, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	if received, _ := status["offers_received"].([]map[string]any); len(received) != 1 || storage.ParseInt(received[0]["offer_id"]) != offerID {
		t.Fatalf("status for the receiver: %v", status)
	}
	// The receiver spent the iron in between: the accept checks again.
	batch4Exec(t, path, `UPDATE inventory SET quantity=3 WHERE user_id=43 AND item_id='spirit_iron'`)
	if _, err := tradeApply(t, path, world, "trade.accept", 43, 2, map[string]any{"offer_id": offerID, "game_minute": 1010}); err == nil || !strings.Contains(err.Error(), "do not carry enough spirit_iron") {
		t.Fatalf("an accept without the goods should be refused, got %v", err)
	}
	batch4Exec(t, path, `UPDATE inventory SET quantity=5 WHERE user_id=43 AND item_id='spirit_iron'`)
	result, err = tradeApply(t, path, world, "trade.accept", 43, 3, map[string]any{"offer_id": offerID, "game_minute": 1020})
	if err != nil {
		t.Fatal(err)
	}
	if result["accepted"] != true || fmt.Sprint(result["status"]) != "accepted" {
		t.Fatalf("accept: %v", result)
	}
	checks := map[string]int64{
		`SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`: 1,
		`SELECT quantity FROM inventory WHERE user_id=43 AND item_id='recovery_pill'`: 2,
		`SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_iron'`:   4,
		`SELECT quantity FROM inventory WHERE user_id=43 AND item_id='spirit_iron'`:   1,
		`SELECT spirit_stones FROM characters WHERE user_id=42`:                       90,
		`SELECT spirit_stones FROM characters WHERE user_id=43`:                       110,
	}
	for sql, want := range checks {
		if got := storage.ParseInt(actionScalar(t, path, sql)); got != want {
			t.Fatalf("%s = %d want %d", sql, got, want)
		}
	}
	// Struck once: a second accept finds it closed.
	if _, err := tradeApply(t, path, world, "trade.accept", 43, 4, map[string]any{"offer_id": offerID, "game_minute": 1030}); err == nil || !strings.Contains(err.Error(), "accepted") {
		t.Fatalf("a struck trade cannot be struck twice, got %v", err)
	}
}

func TestAnOfferLapsesIsDeclinedOrWithdrawnAndANewOneReplacesTheOld(t *testing.T) {
	path, inn := setupTradeDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=50 WHERE user_id IN (42,43)`, inn)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',3)`)
	// Offering more than you hold is refused on the spot.
	if _, err := tradeApply(t, path, world, "trade.offer", 42, 1, map[string]any{"to_user_id": 43, "give_items": map[string]int64{"recovery_pill": 9}, "game_minute": 1000}); err == nil || !strings.Contains(err.Error(), "do not carry enough") {
		t.Fatalf("an offer beyond your hand should be refused, got %v", err)
	}
	first, err := tradeApply(t, path, world, "trade.offer", 42, 2, map[string]any{"to_user_id": 43, "give_items": map[string]int64{"recovery_pill": 1}, "want_stones": 5, "game_minute": 1000})
	if err != nil {
		t.Fatal(err)
	}
	second, err := tradeApply(t, path, world, "trade.offer", 42, 3, map[string]any{"to_user_id": 43, "give_items": map[string]int64{"recovery_pill": 2}, "want_stones": 5, "game_minute": 1001})
	if err != nil {
		t.Fatal(err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT status FROM trade_offers WHERE offer_id=?`, storage.ParseInt(first["offer_id"]))); got != "withdrawn" {
		t.Fatalf("a new offer to the same person replaces the old: first is %q", got)
	}
	// The receiver declines; the offerer's next one they withdraw themselves.
	result, err := tradeApply(t, path, world, "trade.decline", 43, 1, map[string]any{"offer_id": storage.ParseInt(second["offer_id"]), "game_minute": 1002})
	if err != nil || fmt.Sprint(result["status"]) != "declined" {
		t.Fatalf("decline: %v %v", result, err)
	}
	third, err := tradeApply(t, path, world, "trade.offer", 42, 4, map[string]any{"to_user_id": 43, "give_stones": 5, "game_minute": 1003})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := tradeApply(t, path, world, "trade.decline", 99, 1, map[string]any{"offer_id": storage.ParseInt(third["offer_id"])}); err == nil {
		t.Fatal("a stranger cannot close an offer")
	}
	result, err = tradeApply(t, path, world, "trade.decline", 42, 2, map[string]any{"offer_id": storage.ParseInt(third["offer_id"]), "game_minute": 1004})
	if err != nil || fmt.Sprint(result["status"]) != "withdrawn" {
		t.Fatalf("withdraw: %v %v", result, err)
	}
	// An offer left for two hours lapses at the accept.
	fourth, err := tradeApply(t, path, world, "trade.offer", 42, 5, map[string]any{"to_user_id": 43, "give_stones": 5, "game_minute": 2000})
	if err != nil {
		t.Fatal(err)
	}
	result, err = tradeApply(t, path, world, "trade.accept", 43, 5, map[string]any{"offer_id": storage.ParseInt(fourth["offer_id"]), "game_minute": 2000 + tradeOfferMinutes + 1})
	if err != nil || result["lapsed"] != true || result["accepted"] != false {
		t.Fatalf("a stale offer should lapse, got %v %v", result, err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT status FROM trade_offers WHERE offer_id=?`, storage.ParseInt(fourth["offer_id"]))); got != "expired" {
		t.Fatalf("lapsed offer status=%q", got)
	}
}
