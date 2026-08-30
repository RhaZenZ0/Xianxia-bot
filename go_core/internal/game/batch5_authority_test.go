package game

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

func setupBatch5AuthorityDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	if err := conn.ExecScript(`
ALTER TABLE characters ADD COLUMN spirit_stones INTEGER NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN insight_xp INTEGER NOT NULL DEFAULT 0;
CREATE TABLE character_location_discoveries(
    user_id INTEGER NOT NULL,
    location TEXT NOT NULL,
    discovery_kind TEXT NOT NULL DEFAULT 'exploration',
    discovered_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id,location)
);
CREATE TABLE IF NOT EXISTS world_state(
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE world_events(
    event_key TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    starts_at REAL NOT NULL DEFAULT 0,
    ends_at REAL NOT NULL DEFAULT 0,
    thread_id INTEGER
);
CREATE TABLE event_claims(
    user_id INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    claimed_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id,event_key)
);
CREATE TABLE exploration_events(
    event_id TEXT PRIMARY KEY,
    definition_id TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Event',
    kind TEXT NOT NULL DEFAULT 'personal',
    visibility TEXT NOT NULL DEFAULT 'personal',
    location TEXT NOT NULL,
    severity INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'active',
    stage TEXT NOT NULL DEFAULT 'introduced',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_game_minute INTEGER NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE exploration_event_participants(
    event_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'introduced',
    status TEXT NOT NULL DEFAULT 'active',
    joined_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(event_id,user_id)
);
CREATE TABLE exploration_event_actions(
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    action_key TEXT NOT NULL,
    attribute TEXT NOT NULL DEFAULT '',
    total INTEGER NOT NULL DEFAULT 0,
    tn INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    UNIQUE(event_id,user_id,action_key)
);
CREATE TABLE event_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE spirit_beasts(
    beast_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    species TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    element TEXT NOT NULL DEFAULT '',
    intelligence INTEGER NOT NULL DEFAULT 0,
    temperament TEXT NOT NULL DEFAULT '',
    bloodline TEXT NOT NULL DEFAULT '',
    evolution_stage INTEGER NOT NULL DEFAULT 0,
    loyalty INTEGER NOT NULL DEFAULT 0,
    contract_type TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 0,
    techniques_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE wild_beast_encounters(
    encounter_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    species TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    element TEXT NOT NULL DEFAULT '',
    intelligence INTEGER NOT NULL DEFAULT 0,
    temperament TEXT NOT NULL DEFAULT '',
    bloodline TEXT NOT NULL DEFAULT '',
    taming_tn INTEGER NOT NULL DEFAULT 0,
    location TEXT NOT NULL,
    expires_game_minute INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'available',
    created_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE secret_realm_runs(
    user_id INTEGER PRIMARY KEY,
    realm_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    room_index INTEGER NOT NULL DEFAULT 0,
    danger INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0,
    entered_at REAL NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE inheritances(
    user_id INTEGER NOT NULL,
    inheritance_id TEXT NOT NULL,
    source_realm_id TEXT NOT NULL,
    acquired_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id,inheritance_id)
);
UPDATE characters
SET realm_index=0,phase=1,cultivation=0,body_realm_index=0,body_phase=1,body_cultivation=0,
    location='Greenriver Town',spirit_stones=0,insight_xp=0,qi=100,qi_max=100,vitality=100,vitality_max=100
WHERE user_id=42;
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func batch5Query(t *testing.T, path, world, op string, payload map[string]any) map[string]any {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		Operation:  op,
		ActorID:    42,
		Payload:    raw,
	})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	return batch4Result(t, out)
}

func TestBatch5ExploreOwnsRewardsCooldownReceiptAndLocationDiscovery(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)

	originalIntn := locationDiscoveryIntn
	calls := 0
	locationDiscoveryIntn = func(n int) (int, error) {
		calls++
		return 0, nil
	}
	defer func() { locationDiscoveryIntn = originalIntn }()

	result := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", 1, map[string]any{
		"game_minute":                     600,
		"cooldown_seconds":                0,
		"unexpected_events_enabled":       false,
		"unexpected_event_chance_percent": 0,
	}))
	if result["location"] != "Greenriver Town" || storage.ParseInt(result["cultivation_awarded"]) <= 0 {
		t.Fatalf("explore result=%v", result)
	}
	discovered := fmt.Sprint(result["discovered_location"])
	if discovered == "" || discovered == "<nil>" || calls < 2 {
		t.Fatalf("discovery=%q calls=%d result=%v", discovered, calls, result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location=?", discovered)); got != 1 {
		t.Fatalf("discovery rows=%d for %s", got, discovered)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT cultivation FROM characters WHERE user_id=42")); got <= 0 {
		t.Fatalf("cultivation=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got <= 0 {
		t.Fatalf("wallet=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='explore'")); got != 1 {
		t.Fatalf("explore cooldown rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM event_log WHERE user_id=42 AND event_type='explore_discovery'")); got != 1 {
		t.Fatalf("explore event log rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='exploration.explore'")); got != 1 {
		t.Fatalf("explore receipt rows=%d", got)
	}
}

func TestBatch5TravelRequiresCanonicalDiscoveryAndPersistsLocation(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,'Cloudspine Foothills','test',610,0)")

	travel := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{
		"destination": "Cloudspine Foothills",
		"mode":        "known",
		"game_minute": 611,
	}))
	if travel["from"] != "Greenriver Town" || travel["destination"] != "Cloudspine Foothills" {
		t.Fatalf("travel=%v", travel)
	}
	if got := actionScalar(t, path, "SELECT location FROM characters WHERE user_id=42"); got != "Cloudspine Foothills" {
		t.Fatalf("location=%v", got)
	}

	hub := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 2, map[string]any{
		"destination": "Azure Crown Imperial City",
		"mode":        "hub",
		"game_minute": 612,
	}))
	if hub["destination"] != "Azure Crown Imperial City" {
		t.Fatalf("hub travel=%v", hub)
	}
	if got := actionScalar(t, path, "SELECT location FROM characters WHERE user_id=42"); got != "Azure Crown Imperial City" {
		t.Fatalf("hub location=%v", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='exploration.travel'")); got != 2 {
		t.Fatalf("travel receipts=%d", got)
	}
}

func TestBatch5HuntOwnsRollRewardsCooldownAndEncounterCreation(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)

	result := batch4Result(t, batch4Apply(t, path, world, "exploration.hunt", 1, map[string]any{
		"game_minute":      620,
		"cooldown_seconds": 0,
	}))
	if success, _ := result["success"].(bool); !success {
		t.Fatalf("high-stat hunt should succeed: %v", result)
	}
	if storage.ParseInt(result["cultivation_awarded"]) <= 0 {
		t.Fatalf("hunt cultivation=%v", result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got <= 0 {
		t.Fatalf("hunt wallet=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42")); got <= 0 {
		t.Fatalf("hunt inventory quantity=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM wild_beast_encounters WHERE user_id=42 AND status='available'")); got != 1 {
		t.Fatalf("wild encounters=%d result=%v", got, result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='hunt'")); got != 1 {
		t.Fatalf("hunt cooldown rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM event_log WHERE user_id=42 AND event_type='hunt_success'")); got != 1 {
		t.Fatalf("hunt event log rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='exploration.hunt'")); got != 1 {
		t.Fatalf("hunt receipt rows=%d", got)
	}
}

func TestBatch5SecretRealmLifecycleOwnsStatusRoomsInheritanceAndLeave(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "UPDATE characters SET location='Cloudspine Foothills' WHERE user_id=42")
	now := float64(time.Now().UnixNano()) / 1e9
	payload := `{"realm_id":"sword_grave_nine_echoes"}`
	batch4Exec(t, path, "INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at,thread_id) VALUES(?,?,?,?,?,?,1,?,?,?)",
		"batch5-secret-open", "secret_realm:sword_grave_nine_echoes", "secret_realm", "Sword Grave of Nine Echoes", "Cloudspine Foothills", payload, now-10, now+3600, 987654321)

	status := batch5Query(t, path, world, "secret_realm.status", map[string]any{})
	available, ok := status["available"].([]map[string]any)
	if !ok || len(available) != 1 || available[0]["realm_id"] != "sword_grave_nine_echoes" {
		t.Fatalf("initial status=%v", status)
	}

	entered := batch4Result(t, batch4Apply(t, path, world, "secret_realm.enter", 1, map[string]any{
		"realm_id":    "sword_grave_nine_echoes",
		"game_minute": 631,
	}))
	if entered["entered"] != true || entered["realm_id"] != "sword_grave_nine_echoes" {
		t.Fatalf("enter=%v", entered)
	}
	status = batch5Query(t, path, world, "secret_realm.status", map[string]any{})
	if active, _ := status["active"].(bool); !active {
		t.Fatalf("active status=%v", status)
	}

	for i := 0; i < 4; i++ {
		room := batch4Result(t, batch4Apply(t, path, world, "secret_realm.explore", 10+i, map[string]any{
			"game_minute":      633 + i,
			"cooldown_seconds": 0,
		}))
		if success, _ := room["success"].(bool); !success {
			t.Fatalf("room %d should succeed with high stats: %v", i, room)
		}
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT room_index FROM secret_realm_runs WHERE user_id=42")); got != 4 {
		t.Fatalf("room_index=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT active FROM secret_realm_runs WHERE user_id=42")); got != 0 {
		t.Fatalf("run active=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM inheritances WHERE user_id=42 AND inheritance_id='nine_echo_sword_legacy'")); got != 1 {
		t.Fatalf("inheritance rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='nine_echo_sword_tablet'")); got != 1 {
		t.Fatalf("inheritance item quantity=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT qi_max FROM characters WHERE user_id=42")); got != 101 {
		t.Fatalf("qi_max=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT vitality_max FROM characters WHERE user_id=42")); got != 102 {
		t.Fatalf("vitality_max=%d", got)
	}

	batch4Result(t, batch4Apply(t, path, world, "secret_realm.enter", 30, map[string]any{
		"realm_id":    "sword_grave_nine_echoes",
		"game_minute": 640,
	}))
	left := batch4Result(t, batch4Apply(t, path, world, "secret_realm.leave", 31, map[string]any{"game_minute": 641}))
	if left["left"] != true || left["realm_id"] != "sword_grave_nine_echoes" {
		t.Fatalf("leave=%v", left)
	}
	status = batch5Query(t, path, world, "secret_realm.status", map[string]any{})
	if active, _ := status["active"].(bool); active {
		t.Fatalf("status after leave=%v", status)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='secret_realm.enter'")); got != 2 {
		t.Fatalf("enter receipts=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='secret_realm.explore'")); got != 4 {
		t.Fatalf("explore receipts=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='secret_realm.leave'")); got != 1 {
		t.Fatalf("leave receipts=%d", got)
	}
}

func TestBatch5AuthorityOperationNamesHaveNativeCoverage(t *testing.T) {
	for _, op := range []string{
		"exploration.explore", "exploration.travel", "exploration.hunt",
		"secret_realm.status", "secret_realm.enter", "secret_realm.explore", "secret_realm.leave",
	} {
		if !isAuthoritativeOperation(op) {
			t.Errorf("%s is no longer registered as authoritative", op)
		}
	}
}

func forcePersonalUnexpectedEvent(t *testing.T) {
	t.Helper()
	original := unexpectedEventIntn
	unexpectedEventIntn = func(n int) (int, error) {
		if n == 100 {
			return 0, nil
		}
		if n > 7 {
			return 7, nil // after the first two world events, select wounded_wandering_senior
		}
		return 0, nil
	}
	t.Cleanup(func() { unexpectedEventIntn = original })
}

func TestBatch5ExploreStartsPersistentPersonalEventAndReopensIt(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	forcePersonalUnexpectedEvent(t)

	started := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", 90, map[string]any{
		"game_minute":                     700,
		"cooldown_seconds":                0,
		"unexpected_event_chance_percent": 100,
		"event_key":                       "evt:test:wounded:42",
	}))
	if started["kind"] != "event_started" {
		t.Fatalf("kind=%v result=%v", started["kind"], started)
	}
	event, ok := started["event"].(map[string]any)
	if !ok || event["event_id"] != "evt:test:wounded:42" || event["id"] != "wounded_wandering_senior" {
		t.Fatalf("event=%#v", started["event"])
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM exploration_events WHERE event_id='evt:test:wounded:42' AND state='active'")); got != 1 {
		t.Fatalf("active exploration events=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM exploration_event_participants WHERE event_id='evt:test:wounded:42' AND user_id=42 AND status='active'")); got != 1 {
		t.Fatalf("active participants=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM event_log WHERE user_id=42 AND event_type='unexpected_wounded_wandering_senior'")); got != 0 {
		t.Fatalf("personal event reward resolved too early: %d", got)
	}
	before := storage.ParseInt(actionScalar(t, path, "SELECT cultivation FROM characters WHERE user_id=42"))

	reopened := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", 91, map[string]any{
		"game_minute":                     701,
		"cooldown_seconds":                0,
		"unexpected_events_enabled":       false,
		"unexpected_event_chance_percent": 0,
	}))
	if reopened["kind"] != "event_active" {
		t.Fatalf("reopened=%v", reopened)
	}
	if after := storage.ParseInt(actionScalar(t, path, "SELECT cultivation FROM characters WHERE user_id=42")); after != before {
		t.Fatalf("active-event /explore awarded new cultivation: before=%d after=%d", before, after)
	}

	status := batch5Query(t, path, world, "exploration.event.status", map[string]any{"event_id": "evt:test:wounded:42"})
	if active, _ := status["active"].(bool); !active {
		t.Fatalf("status=%v", status)
	}
	actions, _ := status["available_actions"].([]map[string]any)
	if len(actions) < 4 {
		t.Fatalf("available actions=%v", status["available_actions"])
	}

	originalRoll := explorationEventActionRoll
	explorationEventActionRoll = func(modifier, tn int64) (map[string]any, error) {
		return map[string]any{"die1": int64(10), "die2": int64(10), "modifier": modifier, "tn": tn, "total": tn + 8, "margin": int64(8), "success": true, "degree": "Strong Success"}, nil
	}
	defer func() { explorationEventActionRoll = originalRoll }()

	resolved := batch4Result(t, batch4Apply(t, path, world, "exploration.event.act", 92, map[string]any{
		"event_id":    "evt:test:wounded:42",
		"action":      "help",
		"game_minute": 702,
	}))
	if ok, _ := resolved["resolved"].(bool); !ok {
		t.Fatalf("resolved=%v", resolved)
	}
	if after := storage.ParseInt(actionScalar(t, path, "SELECT cultivation FROM characters WHERE user_id=42")); after <= before {
		t.Fatalf("help did not apply event reward: before=%d after=%d", before, after)
	}
	if got := actionScalar(t, path, "SELECT state FROM exploration_events WHERE event_id='evt:test:wounded:42'"); got != "resolved" {
		t.Fatalf("event state=%v", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='exploration.event.act'")); got != 1 {
		t.Fatalf("event act receipts=%d", got)
	}
}

func TestBatch5ExplorationEventLeaveEndsEventWithoutReward(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	forcePersonalUnexpectedEvent(t)

	started := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", 93, map[string]any{
		"game_minute":                     710,
		"cooldown_seconds":                0,
		"unexpected_event_chance_percent": 100,
		"event_key":                       "evt:test:leave:42",
	}))
	if started["kind"] != "event_started" {
		t.Fatalf("started=%v", started)
	}
	before := storage.ParseInt(actionScalar(t, path, "SELECT cultivation FROM characters WHERE user_id=42"))
	left := batch4Result(t, batch4Apply(t, path, world, "exploration.event.leave", 94, map[string]any{
		"event_id":    "evt:test:leave:42",
		"game_minute": 711,
	}))
	if ok, _ := left["resolved"].(bool); !ok || left["action"] != "leave" {
		t.Fatalf("left=%v", left)
	}
	if after := storage.ParseInt(actionScalar(t, path, "SELECT cultivation FROM characters WHERE user_id=42")); after != before {
		t.Fatalf("leave changed cultivation: before=%d after=%d", before, after)
	}
	if got := actionScalar(t, path, "SELECT state FROM exploration_events WHERE event_id='evt:test:leave:42'"); got != "left" {
		t.Fatalf("event state=%v", got)
	}
}
