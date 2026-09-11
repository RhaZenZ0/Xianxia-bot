package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// v1.0.0-rc.2: the realm rotation rides on secret_realm.status, and the
// dashboard can void an open trade offer with an audit row.

func TestSecretRealmStatusCarriesTheRotation(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := RotateSecretRealms(conn, catalog, 5000); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	batch4SetCanonicalGameMinute(t, path, 5100)
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "rc2-status", Operation: "secret_realm.status", ActorID: 42, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	result := batch4Result(t, out)
	rotation, _ := result["rotation"].(map[string]any)
	ids := secretRealmIDs(catalog)
	if rotation == nil || fmt.Sprint(rotation["last_realm_id"]) != ids[0] || fmt.Sprint(rotation["next_realm_id"]) != ids[1%len(ids)] {
		t.Fatalf("rotation on status: %v", result["rotation"])
	}
	if storage.ParseInt(rotation["next_game_minute"]) != 5000+secretRealmRotationMinutes {
		t.Fatalf("next_game_minute=%v", rotation["next_game_minute"])
	}
}

func TestTheDashboardVoidsAnOpenTradeOfferWithAnAuditRow(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE IF NOT EXISTS trade_offers(offer_id INTEGER PRIMARY KEY AUTOINCREMENT, from_user_id INTEGER NOT NULL, to_user_id INTEGER NOT NULL, location TEXT NOT NULL, give_json TEXT NOT NULL DEFAULT '{}', give_stones INTEGER NOT NULL DEFAULT 0, want_json TEXT NOT NULL DEFAULT '{}', want_stones INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', created_game_minute INTEGER NOT NULL DEFAULT 0, expires_game_minute INTEGER NOT NULL DEFAULT 0, resolved_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
INSERT INTO trade_offers(from_user_id,to_user_id,location,give_json,status,created_at,updated_at) VALUES(42,43,'Greenriver Inn','{"spirit_herb":1}','open',0,0);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	result, _ := applyAdmin(t, path, "admin.trade.void", map[string]any{"offer_id": 1, "reason": "test"}).(map[string]any)
	if result == nil || fmt.Sprint(result["status"]) != "voided" || storage.ParseInt(result["from_user_id"]) != 42 {
		t.Fatalf("void: %v", result)
	}
	if got := fmt.Sprint(scalar(t, path, `SELECT status FROM trade_offers WHERE offer_id=1`)); got != "voided" {
		t.Fatalf("status=%q", got)
	}
	if got := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.trade.void' AND target='1'`)); got != 1 {
		t.Fatalf("audit rows=%d", got)
	}
	// Voiding twice is refused: the offer is no longer open.
	raw, _ := json.Marshal(map[string]any{"offer_id": 1, "reason": "again"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.trade.void", ActorID: 0, Payload: raw}); err == nil || !strings.Contains(err.Error(), "already voided") {
		t.Fatalf("a second void should be refused, got %v", err)
	}
}
