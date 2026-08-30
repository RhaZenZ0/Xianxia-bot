package game

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

func TestPlayerLifespanClockPausesLongInactiveGap(t *testing.T) {
	now := time.Unix(2_000_000_000, 0)
	start := int64(12_345)
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.Add(-8 * 24 * time.Hour).Unix()),
		LastActiveGameMinute: start,
	}
	current := start + 5*minutesPerYear
	got := resolvePlayerLifespanClock(state, current, now, 0)

	if !got.AgingPaused {
		t.Fatal("expected long inactive gap to pause biological aging")
	}
	if got.PausedGameMinutes != 5*minutesPerYear {
		t.Fatalf("paused game minutes=%d", got.PausedGameMinutes)
	}
	if got.EffectiveGameMinute != start {
		t.Fatalf("effective game minute=%d want=%d", got.EffectiveGameMinute, start)
	}
}

func TestPlayerLifespanClockDoesNotPauseShortInactiveGap(t *testing.T) {
	now := time.Unix(2_000_000_000, 0)
	start := int64(12_345)
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.Add(-6 * 24 * time.Hour).Unix()),
		LastActiveGameMinute: start,
	}
	current := start + 2*minutesPerYear
	got := resolvePlayerLifespanClock(state, current, now, 0)

	if got.AgingPaused {
		t.Fatal("short inactivity must not pause biological aging")
	}
	if got.PausedGameMinutes != 0 || got.EffectiveGameMinute != current {
		t.Fatalf("clock=%+v", got)
	}
}

func TestPlayerLifespanStatusPreventsOfflineOldAgeDeath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lifespan.db")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE seclusion_sessions(
	user_id INTEGER PRIMARY KEY,
	started_game_minute INTEGER NOT NULL DEFAULT 0,
	ends_game_minute INTEGER NOT NULL DEFAULT 0,
	status TEXT NOT NULL
);
`); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(2_000_000_000, 0)
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.Add(-8 * 24 * time.Hour).Unix()),
		LastActiveGameMinute: 0,
	}
	raw, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)`,
		[]any{playerLifespanStateKey(42), string(raw), 0},
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	current := 80 * minutesPerYear
	character := CharacterState{
		UserID:               42,
		RealmIndex:           0,
		Phase:                1,
		NaturalLifespanYears: 75,
		CreatedGameMinute:    0,
		AgeAtCreationYears:   18,
	}
	status, err := playerLifespanStatus(conn, character, current, now)
	if err != nil {
		t.Fatal(err)
	}
	if !status.AgingPaused {
		t.Fatal("expected inactive character aging to be paused")
	}
	if status.AgeYears != 18 {
		t.Fatalf("age=%v want=18", status.AgeYears)
	}
	if status.RemainingYears == nil || *status.RemainingYears != 57 {
		t.Fatalf("remaining=%v want=57", status.RemainingYears)
	}
}

func TestActiveSeclusionKeepsBiologicalAgingRunning(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lifespan.db")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE seclusion_sessions(
	user_id INTEGER PRIMARY KEY,
	started_game_minute INTEGER NOT NULL DEFAULT 0,
	ends_game_minute INTEGER NOT NULL DEFAULT 0,
	status TEXT NOT NULL
);
`); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(2_000_000_000, 0)
	current := 80 * minutesPerYear
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.Add(-30 * 24 * time.Hour).Unix()),
		LastActiveGameMinute: 0,
	}
	raw, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)`,
		[]any{playerLifespanStateKey(42), string(raw), 0},
	); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO seclusion_sessions(user_id,started_game_minute,ends_game_minute,status) VALUES(42,0,?,'active')`,
		[]any{current},
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	character := CharacterState{
		UserID:               42,
		RealmIndex:           0,
		Phase:                1,
		NaturalLifespanYears: 75,
		CreatedGameMinute:    0,
		AgeAtCreationYears:   18,
	}
	status, err := playerLifespanStatus(conn, character, current, now)
	if err != nil {
		t.Fatal(err)
	}
	if status.AgingPaused {
		t.Fatal("active seclusion must keep biological aging running")
	}
	if status.AgeYears != 98 {
		t.Fatalf("age=%v want=98", status.AgeYears)
	}
	if status.RemainingYears == nil || *status.RemainingYears != 0 {
		t.Fatalf("remaining=%v want=0", status.RemainingYears)
	}
}

func TestCompletedSeclusionOnlyConsumesItsOwnLifespanMinutes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lifespan.db")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE seclusion_sessions(
	user_id INTEGER PRIMARY KEY,
	started_game_minute INTEGER NOT NULL DEFAULT 0,
	ends_game_minute INTEGER NOT NULL DEFAULT 0,
	status TEXT NOT NULL
);
`); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(2_000_000_000, 0)
	current := 80 * minutesPerYear
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.Add(-30 * 24 * time.Hour).Unix()),
		LastActiveGameMinute: 0,
	}
	raw, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)`,
		[]any{playerLifespanStateKey(42), string(raw), 0},
	); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO seclusion_sessions(user_id,started_game_minute,ends_game_minute,status)
		 VALUES(42,0,?,'completed')`,
		[]any{10 * minutesPerYear},
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	character := CharacterState{
		UserID:               42,
		RealmIndex:           0,
		Phase:                1,
		NaturalLifespanYears: 75,
		CreatedGameMinute:    0,
		AgeAtCreationYears:   18,
	}
	status, err := playerLifespanStatus(conn, character, current, now)
	if err != nil {
		t.Fatal(err)
	}
	if !status.AgingPaused {
		t.Fatal("inactive time after completed seclusion should still pause")
	}
	if status.PausedGameMinutes != 70*minutesPerYear {
		t.Fatalf("paused=%d want=%d", status.PausedGameMinutes, 70*minutesPerYear)
	}
	if status.AgeYears != 28 {
		t.Fatalf("age=%v want=28", status.AgeYears)
	}
}

func TestRefreshPlayerLifespanActivityPersistsPauseAndResumesAging(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lifespan.db")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY,
	life_status TEXT NOT NULL
);
CREATE TABLE world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL DEFAULT 0
);
`); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO characters(user_id,life_status) VALUES(42,'alive')`,
		nil,
	); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(2_000_000_000, 0)
	start := int64(100)
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.Add(-8 * 24 * time.Hour).Unix()),
		LastActiveGameMinute: start,
	}
	raw, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)`,
		[]any{playerLifespanStateKey(42), string(raw), 0},
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	current := start + 10*minutesPerYear
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		t.Fatal(err)
	}
	if err := refreshPlayerLifespanActivityTx(conn, 42, current, now); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	res, err := conn.Execute(
		`SELECT value_json FROM world_state WHERE key=?`,
		[]any{playerLifespanStateKey(42)},
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) != 1 {
		t.Fatalf("activity rows=%d", len(res.Rows))
	}
	var persisted playerLifespanClockState
	if err := json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &persisted); err != nil {
		t.Fatal(err)
	}
	if persisted.PausedGameMinutes != 10*minutesPerYear {
		t.Fatalf("paused=%d", persisted.PausedGameMinutes)
	}
	if persisted.LastActiveGameMinute != current {
		t.Fatalf("last game minute=%d want=%d", persisted.LastActiveGameMinute, current)
	}

	oneDayLater := now.Add(24 * time.Hour)
	next := resolvePlayerLifespanClock(
		persisted,
		current+minutesPerYear,
		oneDayLater,
		0,
	)
	if next.AgingPaused {
		t.Fatal("aging should resume after the returning action")
	}
	if next.EffectiveGameMinute != start+minutesPerYear {
		t.Fatalf("effective game minute=%d want=%d", next.EffectiveGameMinute, start+minutesPerYear)
	}
}

func TestLegacyPlayerActivityFallsBackToAuthoritativeReceipt(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lifespan.db")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE authoritative_action_receipts(
	action_id TEXT PRIMARY KEY,
	actor_id INTEGER NOT NULL,
	operation TEXT NOT NULL,
	state_version INTEGER NOT NULL,
	result_json TEXT NOT NULL,
	created_at REAL NOT NULL
);
CREATE TABLE domain_events(
	event_uid TEXT PRIMARY KEY,
	game_minute INTEGER NOT NULL
);
`); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(2_000_000_000, 0)
	lastGameMinute := int64(250)
	lastActive := float64(now.Add(-10 * 24 * time.Hour).Unix())
	if _, err := conn.Execute(
		`INSERT INTO authoritative_action_receipts(action_id,actor_id,operation,state_version,result_json,created_at)
		 VALUES('legacy-action',42,'cultivation.train',1,'{}',?)`,
		[]any{lastActive},
	); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO domain_events(event_uid,game_minute) VALUES('legacy-action:event',?)`,
		[]any{lastGameMinute},
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	current := lastGameMinute + 3*minutesPerYear
	clock, err := playerLifespanClock(conn, 42, current, now)
	if err != nil {
		t.Fatal(err)
	}
	if !clock.AgingPaused {
		t.Fatal("legacy receipt should seed inactivity pause")
	}
	if clock.EffectiveGameMinute != lastGameMinute {
		t.Fatalf("effective=%d want=%d", clock.EffectiveGameMinute, lastGameMinute)
	}
}

func TestLegacyPlayerActivityFallsBackToDomainEventWithoutReceipt(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lifespan.db")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE authoritative_action_receipts(
	action_id TEXT PRIMARY KEY,
	actor_id INTEGER NOT NULL,
	operation TEXT NOT NULL,
	state_version INTEGER NOT NULL,
	result_json TEXT NOT NULL,
	created_at REAL NOT NULL
);
CREATE TABLE domain_events(
	event_uid TEXT PRIMARY KEY,
	actor_id INTEGER,
	game_minute INTEGER NOT NULL,
	created_at REAL NOT NULL
);
`); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(2_000_000_000, 0)
	lastGameMinute := int64(700)
	lastActive := float64(now.Add(-9 * 24 * time.Hour).Unix())
	if _, err := conn.Execute(
		`INSERT INTO domain_events(event_uid,actor_id,game_minute,created_at)
		 VALUES('legacy:event',42,?,?)`,
		[]any{lastGameMinute, lastActive},
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	current := lastGameMinute + 2*minutesPerYear
	clock, err := playerLifespanClock(conn, 42, current, now)
	if err != nil {
		t.Fatal(err)
	}
	if !clock.AgingPaused {
		t.Fatal("domain event should seed inactivity pause when no receipt exists")
	}
	if clock.EffectiveGameMinute != lastGameMinute {
		t.Fatalf("effective=%d want=%d", clock.EffectiveGameMinute, lastGameMinute)
	}
}

func TestLegacyPlayerActivityFallsBackToCharacterTimestamp(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lifespan.db")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE authoritative_action_receipts(
	action_id TEXT PRIMARY KEY,
	actor_id INTEGER NOT NULL,
	operation TEXT NOT NULL,
	state_version INTEGER NOT NULL,
	result_json TEXT NOT NULL,
	created_at REAL NOT NULL
);
CREATE TABLE domain_events(
	event_uid TEXT PRIMARY KEY,
	actor_id INTEGER,
	game_minute INTEGER NOT NULL,
	created_at REAL NOT NULL
);
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY,
	created_game_minute INTEGER NOT NULL,
	updated_at REAL NOT NULL
);
`); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(2_000_000_000, 0)
	createdGameMinute := int64(900)
	lastActive := float64(now.Add(-20 * 24 * time.Hour).Unix())
	if _, err := conn.Execute(
		`INSERT INTO characters(user_id,created_game_minute,updated_at) VALUES(42,?,?)`,
		[]any{createdGameMinute, lastActive},
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}

	current := createdGameMinute + 4*minutesPerYear
	clock, err := playerLifespanClock(conn, 42, current, now)
	if err != nil {
		t.Fatal(err)
	}
	if !clock.AgingPaused {
		t.Fatal("legacy character timestamp should seed inactivity pause")
	}
	if clock.EffectiveGameMinute != createdGameMinute {
		t.Fatalf("effective=%d want=%d", clock.EffectiveGameMinute, createdGameMinute)
	}
}
