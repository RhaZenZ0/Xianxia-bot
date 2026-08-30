package eventledger

import (
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
)

func setup(t *testing.T) (*storage.Conn, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "ledger.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE authoritative_actor_versions(actor_id INTEGER PRIMARY KEY,state_version INTEGER NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE authoritative_action_receipts(action_id TEXT PRIMARY KEY,actor_id INTEGER NOT NULL,operation TEXT NOT NULL,state_version INTEGER NOT NULL,result_json TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE authoritative_entity_versions(domain TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,state_version INTEGER NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(domain,entity_type,entity_id));
CREATE TABLE domain_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,event_uid TEXT UNIQUE NOT NULL,domain TEXT NOT NULL,event_type TEXT NOT NULL,actor_id INTEGER,entity_type TEXT NOT NULL DEFAULT '',entity_id TEXT NOT NULL DEFAULT '',subject_type TEXT NOT NULL DEFAULT '',subject_id TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,state_version INTEGER NOT NULL DEFAULT 0,payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);
`); err != nil {
		t.Fatal(err)
	}
	return conn, path
}

func TestVersionReceiptAndEvent(t *testing.T) {
	conn, _ := setup(t)
	defer conn.Close()
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		t.Fatal(err)
	}
	v, err := AdvanceActorVersion(conn, 42, 0, 1)
	if err != nil || v != 1 {
		t.Fatalf("v=%d err=%v", v, err)
	}
	actor := int64(42)
	if err := Append(conn, Event{EventUID: "a1:event", Domain: "character", EventType: "check", ActorID: &actor, EntityType: "character", EntityID: "42", StateVersion: v, Payload: map[string]any{"ok": true}}); err != nil {
		t.Fatal(err)
	}
	if err := RecordReceipt(conn, Receipt{ActionID: "a1", ActorID: 42, Operation: "check.resolve", StateVersion: v, Result: map[string]any{"ok": true}}, 1); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	replay, err := Replay(conn, "a1")
	if err != nil || replay == nil || replay.StateVersion != 1 {
		t.Fatalf("replay=%v err=%v", replay, err)
	}
	if _, err := AdvanceActorVersion(conn, 42, 0, 2); err == nil {
		t.Fatal("expected stale version error")
	}
}
