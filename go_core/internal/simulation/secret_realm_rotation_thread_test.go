package simulation

import (
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A realm the rotation opens has to reach Discord (v1.0.0-rc.22).
//
// The rotation wrote its world_events row and its history row and told the
// caller only how many realms it had opened, so the bot - which spawns one
// scene thread per event the tick reports - was never told. The entrance
// stood open, the admin event list said "Thread: none", and there was no way
// in. This pins the wiring rather than the rotation's own rules (those are
// the game package's): the maintenance pass carries the opening out as a
// spawned event, typed as the realm it is.

// Only the tables the maintenance pass reads on a world where nothing else is
// happening. Held here rather than shared so a later step's new table shows up
// as a failure in this test instead of silently skipping the rotation.
const maintenanceRotationSchema = `
CREATE TABLE auctions(auction_id INTEGER PRIMARY KEY AUTOINCREMENT, house_id TEXT NOT NULL DEFAULT '',
    seller_user_id INTEGER REFERENCES characters(user_id) ON DELETE CASCADE, item_id TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL DEFAULT 1,
    currency_id TEXT NOT NULL DEFAULT 'low_spirit_stone', starting_bid INTEGER NOT NULL DEFAULT 0,
    current_bid INTEGER NOT NULL DEFAULT 0, current_bidder_user_id INTEGER NOT NULL DEFAULT 0,
    anonymous INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, ends_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE caravan_operations(
    caravan_id INTEGER PRIMARY KEY, escort_strength INTEGER NOT NULL DEFAULT 0, concealment INTEGER NOT NULL DEFAULT 0,
    smuggling INTEGER NOT NULL DEFAULT 0, tax_rate INTEGER NOT NULL DEFAULT 8, toll_paid INTEGER NOT NULL DEFAULT 0,
    intercepted INTEGER NOT NULL DEFAULT 0, seized INTEGER NOT NULL DEFAULT 0, payout_final INTEGER NOT NULL DEFAULT 0,
    losses_json TEXT NOT NULL DEFAULT '{}', outcome TEXT NOT NULL DEFAULT 'traveling', resolved_game_minute INTEGER,
    updated_at REAL NOT NULL, FOREIGN KEY(caravan_id) REFERENCES caravans(caravan_id) ON DELETE CASCADE);
CREATE TABLE world_eras(
    era_id INTEGER PRIMARY KEY AUTOINCREMENT, world TEXT NOT NULL DEFAULT 'Mortal World', name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    started_game_minute INTEGER NOT NULL DEFAULT 0, ended_game_minute INTEGER, active INTEGER NOT NULL DEFAULT 1,
    modifiers_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
CREATE TABLE bounties(
    bounty_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    jurisdiction TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    reason TEXT NOT NULL DEFAULT '',
    source_crime_id INTEGER,
    created_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL);
CREATE TABLE bounty_hunter_pursuits(
    pursuit_id INTEGER PRIMARY KEY AUTOINCREMENT, bounty_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    hunter_name TEXT NOT NULL, hunter_power INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'tracking',
    pressure INTEGER NOT NULL DEFAULT 0, escape_progress INTEGER NOT NULL DEFAULT 0, capture_progress INTEGER NOT NULL DEFAULT 0,
    next_action_game_minute INTEGER NOT NULL DEFAULT 0, created_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE crime_records(
    crime_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    jurisdiction TEXT NOT NULL,
    crime_type TEXT NOT NULL,
    severity INTEGER NOT NULL DEFAULT 1,
    evidence INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    description TEXT NOT NULL DEFAULT '',
    created_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL);
CREATE TABLE inventory(
    user_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, item_id));
CREATE TABLE equipment_instances(
    equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, item_id TEXT NOT NULL, slot TEXT NOT NULL,
    durability INTEGER NOT NULL, max_durability INTEGER NOT NULL, quality INTEGER NOT NULL DEFAULT 100,
    equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE item_provenance(
    provenance_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1, source_type TEXT NOT NULL DEFAULT 'unknown',
    source_key TEXT NOT NULL DEFAULT '', ownership_mark TEXT NOT NULL DEFAULT '',
    legal_status TEXT NOT NULL DEFAULT 'clean', authenticity INTEGER NOT NULL DEFAULT 100,
    tracking_strength INTEGER NOT NULL DEFAULT 0, acquired_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE seclusion_sessions(
    user_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    started_game_minute INTEGER NOT NULL,
    ends_game_minute INTEGER NOT NULL,
    last_settled_game_minute INTEGER NOT NULL,
    start_location TEXT NOT NULL,
    environment_mult REAL NOT NULL DEFAULT 1.0,
    accumulated_gain INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    ended_reason TEXT NOT NULL DEFAULT '',
    ends_real_ts REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL);
CREATE TABLE caravans(caravan_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL DEFAULT 'npc',
    owner_key TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
    cargo_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'traveling', risk INTEGER NOT NULL DEFAULT 10,
    depart_game_minute INTEGER NOT NULL DEFAULT 0, arrive_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE characters(
    user_id INTEGER PRIMARY KEY,
    discord_name TEXT NOT NULL,
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    path TEXT NOT NULL,
    spiritual_root TEXT NOT NULL,
    concept TEXT NOT NULL,
    gender TEXT NOT NULL DEFAULT 'neutral',
    age_at_creation_years INTEGER NOT NULL DEFAULT 18,
    created_game_minute INTEGER NOT NULL DEFAULT 0,
    natural_lifespan_years INTEGER NOT NULL DEFAULT 75,
    life_extension_years INTEGER NOT NULL DEFAULT 0,
    life_status TEXT NOT NULL DEFAULT 'alive',
    karma_score INTEGER NOT NULL DEFAULT 0,
    true_death_count INTEGER NOT NULL DEFAULT 0,
    death_game_minute INTEGER,
    reincarnation_ready_game_minute INTEGER,
    realm_index INTEGER NOT NULL DEFAULT 0,
    phase INTEGER NOT NULL DEFAULT 1,
    cultivation INTEGER NOT NULL DEFAULT 0,
    body_realm_index INTEGER NOT NULL DEFAULT 0,
    body_phase INTEGER NOT NULL DEFAULT 1,
    body_cultivation INTEGER NOT NULL DEFAULT 0,
    sense_power_bonus INTEGER NOT NULL DEFAULT 0,
    sense_precision_bonus INTEGER NOT NULL DEFAULT 0,
    sense_range_bonus INTEGER NOT NULL DEFAULT 0,
    concealment_bonus INTEGER NOT NULL DEFAULT 0,
    concealment_active INTEGER NOT NULL DEFAULT 0,
    qi INTEGER NOT NULL DEFAULT 10,
    qi_max INTEGER NOT NULL DEFAULT 10,
    vitality INTEGER NOT NULL DEFAULT 12,
    vitality_max INTEGER NOT NULL DEFAULT 12,
    spirit_stones INTEGER NOT NULL DEFAULT 25,
    insight_xp INTEGER NOT NULL DEFAULT 0,
    location TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL);
CREATE TABLE territory_wars(war_id INTEGER PRIMARY KEY AUTOINCREMENT, attacker_key TEXT NOT NULL,
    defender_key TEXT NOT NULL, territory_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    attacker_score INTEGER NOT NULL DEFAULT 0, defender_score INTEGER NOT NULL DEFAULT 0,
    created_game_minute INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE territory_war_operations(war_id INTEGER PRIMARY KEY, siege_progress INTEGER NOT NULL DEFAULT 0,
    attacker_morale INTEGER NOT NULL DEFAULT 100, defender_morale INTEGER NOT NULL DEFAULT 100,
    attacker_force INTEGER NOT NULL DEFAULT 0, defender_force INTEGER NOT NULL DEFAULT 0,
    last_tick_game_minute INTEGER NOT NULL DEFAULT 0, winner_key TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '', occupation_until_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE world_events(event_key TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1,
    starts_at REAL NOT NULL DEFAULT 0, ends_at REAL NOT NULL DEFAULT 0);
`

func TestMaintenanceCarriesAnOpenedRealmOutAsASpawnedEvent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "maintenance.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(simulationClockSchema + maintenanceRotationSchema); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	runner := &Runner{DatabasePath: path, World: worlddata.Catalog{SecretRealms: map[string]worlddata.SecretRealm{
		"hollow_throne_vault": {
			Name: "Hollow Throne Vault", Location: "Hollow Throne Ruin",
			Description: "A vault under a throne nobody sits on.", OpenHours: 6,
		},
	}}}

	conn, err = storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	run, changed, err := runner.advancedMaintenance(conn, 5000, map[string]bool{})
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("opening a realm is a change")
	}
	if len(run.Events) != 1 {
		t.Fatalf("the tick should report the realm it opened, reported %d event(s)", len(run.Events))
	}
	event := run.Events[0]
	if event.EventType != "secret_realm" {
		t.Fatalf("a realm is not a random event: event_type=%q", event.EventType)
	}
	if event.Title != "Hollow Throne Vault" || event.Location != "Hollow Throne Ruin" {
		t.Fatalf("the event must name the realm and its entrance: %+v", event)
	}
	if event.EventKey == "" || event.ExpiresAt <= 0 {
		t.Fatalf("a scene thread needs the key and the closing time: %+v", event)
	}
	if event.Description == "" {
		t.Fatalf("the announcement has nothing to say about it: %+v", event)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM world_events WHERE event_key=? AND active=1`, event.EventKey)); got != 1 {
		t.Fatalf("the reported key should name the open row, found %d", got)
	}

	// The next tick is inside the rotation interval, so nothing opens and
	// nothing is reported: a thread per tick would be a thread every 30s.
	run, _, err = runner.advancedMaintenance(conn, 5001, map[string]bool{})
	if err != nil {
		t.Fatal(err)
	}
	if len(run.Events) != 0 {
		t.Fatalf("nothing opened, so nothing should be reported: %+v", run.Events)
	}
}
