package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const worldEventNodesDDL = `CREATE TABLE world_event_nodes (
    node_id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,node_key TEXT NOT NULL,node_type TEXT NOT NULL,
    name TEXT NOT NULL,descriptor TEXT NOT NULL DEFAULT '',rank INTEGER NOT NULL DEFAULT 1,total INTEGER NOT NULL DEFAULT 1,
    remaining INTEGER NOT NULL DEFAULT 0,cleared_by INTEGER NOT NULL DEFAULT 0,tn INTEGER NOT NULL DEFAULT 12,
    attribute TEXT NOT NULL DEFAULT 'insight',item_id TEXT NOT NULL DEFAULT '',item_qty INTEGER NOT NULL DEFAULT 0,
    cultivation INTEGER NOT NULL DEFAULT 0,spirit_stones INTEGER NOT NULL DEFAULT 0,contribution INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,updated_at REAL NOT NULL,UNIQUE(event_key,node_key));`

const worldEventNPCsDDL = `CREATE TABLE world_event_npcs (
    event_key TEXT NOT NULL,npc_key TEXT NOT NULL,name TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',personality TEXT NOT NULL DEFAULT '',speech TEXT NOT NULL DEFAULT '',
    want TEXT NOT NULL DEFAULT '',fear TEXT NOT NULL DEFAULT '',descriptor TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,PRIMARY KEY(event_key,npc_key));
CREATE UNIQUE INDEX idx_world_event_npcs_name ON world_event_npcs(name);`

const worldEventParticipationDDL = `CREATE TABLE world_event_participation (
    event_key TEXT NOT NULL,user_id INTEGER NOT NULL,stance TEXT NOT NULL DEFAULT 'observing',
    contribution INTEGER NOT NULL DEFAULT 0,investigation INTEGER NOT NULL DEFAULT 0,support INTEGER NOT NULL DEFAULT 0,
    interference INTEGER NOT NULL DEFAULT 0,combat_victories INTEGER NOT NULL DEFAULT 0,actions_taken INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,last_action TEXT NOT NULL DEFAULT '',
    last_target TEXT NOT NULL DEFAULT '',first_game_minute INTEGER NOT NULL DEFAULT 0,last_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,PRIMARY KEY(event_key,user_id));`

const worldEventActionsDDL = `CREATE TABLE world_event_actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,user_id INTEGER NOT NULL,action_key TEXT NOT NULL,
    stance TEXT NOT NULL DEFAULT '',target TEXT NOT NULL DEFAULT '',attribute TEXT NOT NULL DEFAULT '',total INTEGER NOT NULL DEFAULT 0,
    tn INTEGER NOT NULL DEFAULT 0,success INTEGER NOT NULL DEFAULT 0,contribution_delta INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);`

// setupEventSiteDB gives a batch-5 authority database the three world-event
// tables plus an active event at the starting town, and returns its path.
func setupEventSiteDB(t *testing.T, category string, severity int64) (string, string) {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, worldEventNodesDDL)
	batch4Exec(t, path, worldEventNPCsDDL)
	batch4Exec(t, path, worldEventParticipationDDL)
	batch4Exec(t, path, worldEventActionsDDL)
	now := float64(time.Now().UnixNano()) / 1e9
	payload := `{"severity":` + itoa(severity) + `,"category":"` + category + `","definition_id":"test_event"}`
	batch4Exec(t, path, `INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at)
        VALUES(?,?,?,?,?,?,1,?,?)`, "site-event", "random:test", "random_event", "Test Event", "Greenriver Town", payload, now-10, now+7200)
	return path, world
}

func itoa(v int64) string {
	if v == 0 {
		return "0"
	}
	neg := v < 0
	if neg {
		v = -v
	}
	var b []byte
	for v > 0 {
		b = append([]byte{byte('0' + v%10)}, b...)
		v /= 10
	}
	if neg {
		return "-" + string(b)
	}
	return string(b)
}

func spawnTestSite(t *testing.T, path, world, category string, severity int64) int64 {
	t.Helper()
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	n, err := SpawnWorldEventNodes(conn, catalog, "site-event", category, "Greenriver Town", severity, float64(time.Now().UnixNano())/1e9)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return n
}

// A world event must never reach a player as an empty room.
func TestSpawnWorldEventNodesFillsTheSite(t *testing.T) {
	path, world := setupEventSiteDB(t, "Beast Tide", 9)
	if n := spawnTestSite(t, path, world, "Beast Tide", 9); n < 3 {
		t.Fatalf("beast tide spawned %d nodes; expected the full roster", n)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_nodes WHERE event_key='site-event' AND node_type='beast'`)); got < 2 {
		t.Fatalf("beast nodes=%d; a beast tide has to contain beasts", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_nodes WHERE event_key='site-event' AND node_type='herb'`)); got < 1 {
		t.Fatalf("herb nodes=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_nodes WHERE event_key='site-event' AND node_type='task'`)); got < 1 {
		t.Fatalf("task nodes=%d", got)
	}
	// Greenriver Town is in the Mortal World, so its materials are the tier-1 ones.
	if got := actionScalar(t, path, `SELECT item_id FROM world_event_nodes WHERE event_key='site-event' AND node_key='trampled_bed'`); got != "spirit_herb" {
		t.Fatalf("mortal-tier herb resolved to %v; expected spirit_herb", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT remaining FROM world_event_nodes WHERE event_key='site-event' AND node_key='charger'`)); got <= 0 {
		t.Fatalf("charger remaining=%d", got)
	}
}

// An unknown category still gets the default roster rather than nothing.
func TestSpawnWorldEventNodesFallsBackToTheDefaultRoster(t *testing.T) {
	path, world := setupEventSiteDB(t, "Category Nobody Wrote", 4)
	if n := spawnTestSite(t, path, world, "Category Nobody Wrote", 4); n < 2 {
		t.Fatalf("default roster spawned %d nodes", n)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_nodes WHERE event_key='site-event'`)); got < 2 {
		t.Fatalf("fallback nodes=%d", got)
	}
}

// Severity is what makes a calamity bigger than a disturbance.
func TestSpawnWorldEventNodesScalesWithSeverity(t *testing.T) {
	lowPath, world := setupEventSiteDB(t, "Beast Tide", 1)
	spawnTestSite(t, lowPath, world, "Beast Tide", 1)
	low := storage.ParseInt(actionScalar(t, lowPath, `SELECT SUM(total) FROM world_event_nodes WHERE event_key='site-event'`))

	highPath, _ := setupEventSiteDB(t, "Beast Tide", 10)
	spawnTestSite(t, highPath, world, "Beast Tide", 10)
	high := storage.ParseInt(actionScalar(t, highPath, `SELECT SUM(total) FROM world_event_nodes WHERE event_key='site-event'`))

	if high <= low {
		t.Fatalf("severity 10 site (%d) is not bigger than severity 1 (%d)", high, low)
	}
}

// Spawning twice must not double the roster.
func TestSpawnWorldEventNodesIsIdempotent(t *testing.T) {
	path, world := setupEventSiteDB(t, "Beast Tide", 5)
	first := spawnTestSite(t, path, world, "Beast Tide", 5)
	if second := spawnTestSite(t, path, world, "Beast Tide", 5); second != 0 {
		t.Fatalf("respawn created %d more nodes; expected 0", second)
	}
	// The return counts everything the site spawned - nodes and cast alike.
	got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_nodes WHERE event_key='site-event'`)) +
		storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_npcs WHERE event_key='site-event'`))
	if got != first {
		t.Fatalf("site size drifted from %d to %d on respawn", first, got)
	}
}

// Working a node has to move real state: depletion, loot and contribution.
func TestWorldEventEngageDepletesTheNodeAndPaysRealLoot(t *testing.T) {
	path, world := setupEventSiteDB(t, "Heavenly Treasure", 6)
	spawnTestSite(t, path, world, "Heavenly Treasure", 6)
	// A very high total makes the check deterministic without touching the RNG.
	batch4Exec(t, path, `UPDATE world_event_nodes SET tn=0,total=3,remaining=3 WHERE event_key='site-event' AND node_key='bloom'`)
	before := storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`))

	out := batch4Result(t, batch4Apply(t, path, world, "world_event.engage", 900, map[string]any{
		"event_key": "site-event", "node_key": "bloom", "game_minute": 4242,
	}))
	if out["success"] != true {
		t.Fatalf("engage against TN 0 failed: %v", out)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT remaining FROM world_event_nodes WHERE event_key='site-event' AND node_key='bloom'`)); got != 2 {
		t.Fatalf("remaining after one harvest=%d; expected 2", got)
	}
	after := storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`))
	if after <= before {
		t.Fatalf("harvest granted no herb (before=%d after=%d)", before, after)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT contribution FROM world_event_participation WHERE event_key='site-event' AND user_id=42`)); got <= 0 {
		t.Fatalf("harvest contributed %d to the event", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_actions WHERE event_key='site-event' AND action_key='engage:bloom'`)); got != 1 {
		t.Fatalf("action log rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='world_event.engage'`)); got != 1 {
		t.Fatalf("authority receipts=%d", got)
	}
}

// A cleared node is genuinely gone - the site can be used up.
func TestWorldEventEngageRefusesADepletedNode(t *testing.T) {
	path, world := setupEventSiteDB(t, "Heavenly Treasure", 6)
	spawnTestSite(t, path, world, "Heavenly Treasure", 6)
	batch4Exec(t, path, `UPDATE world_event_nodes SET remaining=0 WHERE event_key='site-event' AND node_key='bloom'`)
	batch4SetCanonicalGameMinute(t, path, 10)
	raw, err := json.Marshal(map[string]any{"event_key": "site-event", "node_key": "bloom"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion, ActionID: "site-depleted", Operation: "world_event.engage",
		ActorID: 42, Payload: raw,
	})
	if err == nil || !strings.Contains(err.Error(), "cleared out") {
		t.Fatalf("engaging a cleared node did not refuse: %v", err)
	}
}

// A failed check must not quietly consume the node.
func TestWorldEventEngageFailureLeavesTheNodeStanding(t *testing.T) {
	path, world := setupEventSiteDB(t, "Heavenly Treasure", 6)
	spawnTestSite(t, path, world, "Heavenly Treasure", 6)
	batch4Exec(t, path, `UPDATE world_event_nodes SET tn=999,total=2,remaining=2 WHERE event_key='site-event' AND node_key='bloom'`)
	out := batch4Result(t, batch4Apply(t, path, world, "world_event.engage", 902, map[string]any{
		"event_key": "site-event", "node_key": "bloom", "game_minute": 11,
	}))
	if out["success"] != false {
		t.Fatalf("engage against TN 999 succeeded: %v", out)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT remaining FROM world_event_nodes WHERE event_key='site-event' AND node_key='bloom'`)); got != 2 {
		t.Fatalf("failed harvest consumed the node: remaining=%d", got)
	}
}

// The panel needs a shared objective readout, not just per-node counts.
func TestWorldEventSiteProgressTracksClearing(t *testing.T) {
	path, world := setupEventSiteDB(t, "Beast Tide", 5)
	spawnTestSite(t, path, world, "Beast Tide", 5)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	start, err := worldEventSiteProgressTx(conn, "site-event")
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(start["percent"]) != 0 || start["resolved"] != false {
		t.Fatalf("fresh site reports %v", start)
	}
	if _, err := conn.Execute(`UPDATE world_event_nodes SET remaining=0 WHERE event_key='site-event'`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	done, err := worldEventSiteProgressTx(conn, "site-event")
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(done["percent"]) != 100 || done["resolved"] != true {
		t.Fatalf("cleared site reports %v", done)
	}
}

// The scene needs people in it, not only things to hit.
func TestSpawnWorldEventCastFillsTheScene(t *testing.T) {
	path, world := setupEventSiteDB(t, "Beast Tide", 7)
	spawnTestSite(t, path, world, "Beast Tide", 7)
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_npcs WHERE event_key='site-event'`)); got < 2 {
		t.Fatalf("beast tide brought %d people; the scene needs a cast", got)
	}
	// Named, located, and carrying enough character to be worth talking to.
	name := fmt.Sprint(actionScalar(t, path, `SELECT name FROM world_event_npcs WHERE event_key='site-event' AND npc_key='captain'`))
	if !strings.HasPrefix(name, "Militia Captain ") || name == "Militia Captain " {
		t.Fatalf("captain is named %q; expected a title plus a name from the pool", name)
	}
	if got := actionScalar(t, path, `SELECT location FROM world_event_npcs WHERE event_key='site-event' AND npc_key='captain'`); got != "Greenriver Town" {
		t.Fatalf("captain is at %v, not at the event", got)
	}
	for _, column := range []string{"role", "personality", "speech", "want"} {
		if got := fmt.Sprint(actionScalar(t, path, `SELECT `+column+` FROM world_event_npcs WHERE event_key='site-event' AND npc_key='captain'`)); strings.TrimSpace(got) == "" {
			t.Fatalf("captain has no %s; the narrator would render an anonymous local", column)
		}
	}
}

// An unwritten category still gets people.
func TestSpawnWorldEventCastFallsBackToTheDefault(t *testing.T) {
	path, world := setupEventSiteDB(t, "Category Nobody Wrote", 4)
	spawnTestSite(t, path, world, "Category Nobody Wrote", 4)
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_npcs WHERE event_key='site-event'`)); got < 1 {
		t.Fatalf("fallback cast=%d", got)
	}
}

// Two live events must not both field the same captain - the name is how a
// player addresses them, so it has to identify exactly one person.
func TestWorldEventCastNamesDoNotCollideAcrossEvents(t *testing.T) {
	path, world := setupEventSiteDB(t, "Beast Tide", 7)
	spawnTestSite(t, path, world, "Beast Tide", 7)

	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at)
        VALUES('site-event-2','random:test2','random_event','Second Tide','Cloudspine Foothills','{"severity":7}',1,?,?)`, []any{now - 10, now + 7200}); err != nil {
		t.Fatal(err)
	}
	if _, err := SpawnWorldEventNodes(conn, catalog, "site-event-2", "Beast Tide", "Cloudspine Foothills", 7, now); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	total := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_npcs`))
	distinct := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(DISTINCT name) FROM world_event_npcs`))
	if total != distinct || total < 4 {
		t.Fatalf("cast names collided: %d rows, %d distinct names", total, distinct)
	}
}
