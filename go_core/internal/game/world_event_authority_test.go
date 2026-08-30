package game

import (
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

func TestWorldEventActWithdrawIsGoAuthoritativeAndPersistent(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `CREATE TABLE world_event_participation (
        event_key TEXT NOT NULL,user_id INTEGER NOT NULL,stance TEXT NOT NULL DEFAULT 'observing',
        contribution INTEGER NOT NULL DEFAULT 0,investigation INTEGER NOT NULL DEFAULT 0,support INTEGER NOT NULL DEFAULT 0,
        interference INTEGER NOT NULL DEFAULT 0,combat_victories INTEGER NOT NULL DEFAULT 0,actions_taken INTEGER NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,last_action TEXT NOT NULL DEFAULT '',last_target TEXT NOT NULL DEFAULT '',
        first_game_minute INTEGER NOT NULL DEFAULT 0,last_game_minute INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,
        PRIMARY KEY(event_key,user_id));`)
	batch4Exec(t, path, `CREATE TABLE world_event_actions (
        action_id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,user_id INTEGER NOT NULL,action_key TEXT NOT NULL,
        stance TEXT NOT NULL DEFAULT '',target TEXT NOT NULL DEFAULT '',attribute TEXT NOT NULL DEFAULT '',total INTEGER NOT NULL DEFAULT 0,
        tn INTEGER NOT NULL DEFAULT 0,success INTEGER NOT NULL DEFAULT 0,contribution_delta INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL DEFAULT '',
        game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);`)
	now := float64(time.Now().UnixNano()) / 1e9
	batch4Exec(t, path, `INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at)
        VALUES(?,?,?,?,?,?,1,?,?)`, "go-event-1", "random:test", "storm", "Heavenly Storm", "Greenriver Town", `{"severity":3}`, now-10, now+3600)

	out := batch4Result(t, batch4Apply(t, path, world, "world_event.act", 700, map[string]any{
		"event_key": "go-event-1", "action_key": "withdraw", "game_minute": 777,
	}))
	if out["success"] != true || out["action_key"] != "withdraw" {
		t.Fatalf("unexpected world_event.act result: %v", out)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT actions_taken FROM world_event_participation WHERE event_key='go-event-1' AND user_id=42")); got != 1 {
		t.Fatalf("participation actions=%d", got)
	}
	if got := actionScalar(t, path, "SELECT stance FROM world_event_participation WHERE event_key='go-event-1' AND user_id=42"); got != "withdrawn" {
		t.Fatalf("stance=%v", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM world_event_actions WHERE event_key='go-event-1' AND user_id=42 AND action_key='withdraw'")); got != 1 {
		t.Fatalf("action log rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='world_event.act'")); got != 1 {
		t.Fatalf("authority receipts=%d", got)
	}
}
