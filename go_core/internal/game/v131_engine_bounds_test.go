package game

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The six engine wires of v1.3.1: each was a rule the bot held and the engine
// did not, or a literal the engine kept beside its own table.

func TestTheForageWaitIsTheTables(t *testing.T) {
	if got := cooldownSecondsFor(cooldownForage); got != 20*60 {
		t.Fatalf("forage waits %d seconds by default, want 1200", got)
	}
	t.Setenv("FORAGE_COOLDOWN_MINUTES", "7")
	if got := cooldownSecondsFor(cooldownForage); got != 7*60 {
		t.Fatalf("FORAGE_COOLDOWN_MINUTES=7 served %d seconds", got)
	}
}

func TestPeriodsAndCircuitsAreTheClocks(t *testing.T) {
	for hour, want := range map[int64]string{0: "Night", 5: "Dawn", 6: "Dawn", 7: "Morning", 11: "Morning", 12: "Afternoon", 16: "Afternoon", 17: "Evening", 19: "Evening", 20: "Night", 23: "Night"} {
		if got := periodForHour(hour); got != want {
			t.Fatalf("hour %d is %s, want %s", hour, got, want)
		}
	}
	stops := []string{"A", "B", "C", "D"}
	month := minutesPerYear / 12
	cases := []struct {
		minute, months, offset int64
		want                   string
	}{
		{0, 2, 0, "A"}, {2*month - 1, 2, 0, "A"}, {2 * month, 2, 0, "B"}, {8 * month, 2, 0, "A"},
		{0, 2, 1, "B"}, {6 * month, 2, 1, "A"}, {0, 0, 0, "A"}, {month, 0, 0, "B"},
	}
	for _, c := range cases {
		if got := circuitStop(stops, c.minute, c.months, c.offset); got != c.want {
			t.Fatalf("minute %d months %d offset %d -> %s, want %s", c.minute, c.months, c.offset, got, c.want)
		}
	}
	if circuitStop(nil, 100, 2, 0) != "" || circuitStop([]string{" "}, 100, 2, 0) != "" {
		t.Fatal("an empty circuit has no stop")
	}
}

// A catalogue sponsor is asked in person: Elder Xue Hong keeps Moonfen Marsh
// and walks to Greenriver Town of an evening, and the engine reads that
// schedule itself now.
func TestACatalogueSponsorMustBeStandingHere(t *testing.T) {
	path := crossingDB(t)
	world := batch4WorldPath(t)
	sectDoorTables(t, path)
	catalog := crossingCatalog(t)
	npc, ok := catalog.NPCs["Elder Xue Hong"]
	if !ok || npc.Schedule["Evening"] != "Greenriver Town" || npc.Schedule["Morning"] != "Moonfen Marsh" || !npc.CanRecommend {
		t.Fatalf("the content moved Elder Xue Hong; the reader is broken, not the tree: %+v", npc)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	// Morning: he is in the marsh.
	batch4SetCanonicalGameMinute(t, path, 9*60)
	_, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 1, map[string]any{"npc_name": "Elder Xue Hong"})
	if err == nil || !strings.Contains(err.Error(), "is at Moonfen Marsh; you are at Greenriver Town") {
		t.Fatalf("a sponsor a town away put his name to an applicant: err=%v", err)
	}
	// Evening: he is here, and the ask is heard (whatever the dice say).
	batch4SetCanonicalGameMinute(t, path, 18*60)
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 2, map[string]any{"npc_name": "Elder Xue Hong"}); err != nil && strings.Contains(err.Error(), "is at ") {
		t.Fatalf("the sponsor was here and the engine said otherwise: %v", err)
	}
	// And a dead sponsor recommends nobody.
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT NOT NULL,current_location TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'alive')`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,status) VALUES('Elder Xue Hong','Moonfen Marsh','Greenriver Town','dead')`)
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 3, map[string]any{"npc_name": "Elder Xue Hong"}); err == nil || !strings.Contains(err.Error(), "dead") {
		t.Fatalf("a dead sponsor was asked: err=%v", err)
	}
}

func TestWhereaboutsFollowTheSimulationOnceSomebodyHasLeftHome(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT NOT NULL,current_location TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'alive')`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,status) VALUES('Elder Xue Hong','Moonfen Marsh','Moonfen Marsh','alive')`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// At home, the schedule holds: evening is Greenriver Town.
	got, err := npcWhereaboutsTx(conn, catalog, "Elder Xue Hong", 18*60, 0)
	if err != nil || got.Location != "Greenriver Town" {
		t.Fatalf("at home in the evening: %+v err=%v", got, err)
	}
	// Walked away by the tick, the schedule no longer applies.
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET current_location='Riverguard City' WHERE npc_name='Elder Xue Hong'`, nil); err != nil {
		t.Fatal(err)
	}
	got, err = npcWhereaboutsTx(conn, catalog, "Elder Xue Hong", 18*60, 0)
	if err != nil || got.Location != "Riverguard City" {
		t.Fatalf("walked away: %+v err=%v", got, err)
	}
	// A missing person is exactly where they are.
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='missing',current_location='Moonfen Marsh' WHERE npc_name='Elder Xue Hong'`, nil); err != nil {
		t.Fatal(err)
	}
	got, err = npcWhereaboutsTx(conn, catalog, "Elder Xue Hong", 18*60, 0)
	if err != nil || got.Location != "Moonfen Marsh" {
		t.Fatalf("missing: %+v err=%v", got, err)
	}
	// Nobody the world knows is not known.
	got, err = npcWhereaboutsTx(conn, catalog, "Nobody At All", 0, 0)
	if err != nil || got.Known {
		t.Fatalf("a stranger has whereabouts: %+v err=%v", got, err)
	}
}

// A commission is taken from its giver's own city.
func TestACommissionIsTakenFromItsGiversCity(t *testing.T) {
	path := setupCommissionDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	seedCommission(t, path, "commission_crate", nil)
	home := commissionGiverHome(catalog, "Steward Qiao")
	if home == "" {
		t.Fatal("the content no longer places Steward Qiao; the reader is broken, not the tree")
	}
	city := cityOf(catalog, home)
	elsewhere := ""
	for name, loc := range catalog.Locations {
		if loc.World == "Mortal World" && loc.SettlementType != "" && cityOf(catalog, name) != city {
			elsewhere = name
			break
		}
	}
	if elsewhere == "" {
		t.Fatal("no other Mortal city to stand in")
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, elsewhere)
	_, err := batch4ApplyErr(path, world, "commission.accept", 42, 1, map[string]any{"quest_key": "commission_crate", "variant_index": 0})
	if err == nil || !strings.Contains(err.Error(), "posts work on "+city+"'s board") {
		t.Fatalf("work from %s's board was taken in %s: err=%v", city, elsewhere, err)
	}
	// A gate or district of the giver's city is that city.
	part := ""
	for _, candidate := range cityPartsOf(catalog, city) {
		part = candidate
		break
	}
	if part == "" {
		t.Fatalf("%s has no parts", city)
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, part)
	if _, err := batch4ApplyErr(path, world, "commission.accept", 42, 2, map[string]any{"quest_key": "commission_crate", "variant_index": 0}); err != nil {
		t.Fatalf("at %s, a part of %s, the board refused: %v", part, city, err)
	}
}

// A territory is claimed standing on it.
func TestATerritoryIsClaimedFromItsGround(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS territory_state(territory_key TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50, defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS territory_wars(war_id INTEGER PRIMARY KEY AUTOINCREMENT, attacker_key TEXT NOT NULL, defender_key TEXT NOT NULL, territory_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', started_game_minute INTEGER NOT NULL DEFAULT 0, operations_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,'Azure Cloud Sect','Deacon',40,0)`)
	batch4Exec(t, path, `INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES('riverguard','Riverguard Reach','Riverguard City',0)`)
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	_, err := batch4ApplyErr(path, world, "territory.claim", 42, 1, map[string]any{"territory_key": "riverguard"})
	if err == nil || !strings.Contains(err.Error(), "is claimed from Riverguard City; you are at Greenriver Town") {
		t.Fatalf("a territory was claimed from a town away: err=%v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	out, err := batch4ApplyErr(path, world, "territory.claim", 42, 2, map[string]any{"territory_key": "riverguard"})
	if err != nil {
		t.Fatalf("standing on it, the claim refused: %v", err)
	}
	if result, _ := out.Result.(map[string]any); result["claimed"] != true {
		t.Fatalf("claimed=%v", result["claimed"])
	}
}

// A graduate holding no quest is caught up on any action at all.
func TestAGraduateHoldingNoQuestIsCaughtUpOnAnyAction(t *testing.T) {
	path := crossingDB(t)
	world := batch4WorldPath(t)
	for _, key := range []string{"finished_stage", "added_stage"} {
		seed := "{}"
		if key == "finished_stage" {
			seed = `{"follow_on":"added_stage"}`
		}
		batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,description,objectives_json,rewards_json,status,created_at,updated_at,seed_json) VALUES(?,?,'d','[{"id":"x","type":"cultivate","count":1}]','{}','approved',0,0,?)`, key, key, seed)
	}
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at) VALUES(42,'finished_stage','completed',0,0)`)
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND status='active'`)); n != 0 {
		t.Fatalf("the fixture holds %d active quests; the graduate must hold none", n)
	}
	if _, err := batch4ApplyErr(path, world, "check.resolve", 42, 1, validCheckResolvePayload()); err != nil {
		t.Fatalf("the ordinary action itself failed: %v", err)
	}
	status := fmt.Sprint(actionScalar(t, path, `SELECT status FROM character_quests WHERE user_id=42 AND quest_key='added_stage'`))
	if status != "active" {
		t.Fatalf("after an ordinary action the added stage is %q, want active: a graduate holding no quest was never caught up", status)
	}
	// And a re-pointed chain is obeyed: the catch-up reads seed_json, not
	// the content file's order.
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,description,objectives_json,rewards_json,status,created_at,updated_at,seed_json) VALUES('gm_stage','gm_stage','d','[]','{}','approved',0,0,'{}')`)
	batch4Exec(t, path, `UPDATE quest_definitions SET seed_json='{"follow_on":"gm_stage"}' WHERE quest_key='finished_stage'`)
	if _, err := batch4ApplyErr(path, world, "check.resolve", 42, 2, validCheckResolvePayload()); err != nil {
		t.Fatalf("the second ordinary action failed: %v", err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT status FROM character_quests WHERE user_id=42 AND quest_key='gm_stage'`)); got != "active" {
		t.Fatalf("the chain the GM re-pointed was not followed: %q", got)
	}
}
