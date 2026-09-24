package game

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// v1.1.0: a new cultivator can join a sect, and every door that says where a
// sect takes applicants reads the gate off the catalogue.
//
// The report this answers: at a Major Sect Recruitment world event a player
// talked to the Visiting Elder and was told they were impatient, and another
// asked "What menu?". The event named no sect, the elder belonged to none, the
// envoys' hall wrote nothing a route could be read from, and a recommendation
// wrote whatever location its caller named onto the travel list.

// sectDoorTables are the recruitment tables in production's shape, added to a
// batch-4/5 authority database (which already carries characters, inventory,
// faction_reputation and - batch 5 - character_location_discoveries).
func sectDoorTables(t *testing.T, path string) {
	t.Helper()
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS character_sect_discoveries(user_id INTEGER NOT NULL,sect_name TEXT NOT NULL,discovery_kind TEXT NOT NULL DEFAULT 'rumor',source_key TEXT NOT NULL DEFAULT '',discovered_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,PRIMARY KEY(user_id,sect_name))`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_recommendations(recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,npc_name TEXT NOT NULL,sect_name TEXT NOT NULL,bonus INTEGER NOT NULL DEFAULT 2,status TEXT NOT NULL DEFAULT 'active',issued_game_minute INTEGER NOT NULL DEFAULT 0,used_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_recruitment_attempts(attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,sect_name TEXT NOT NULL,attempt_type TEXT NOT NULL,npc_name TEXT NOT NULL DEFAULT '',location TEXT NOT NULL DEFAULT '',result TEXT NOT NULL,score INTEGER NOT NULL DEFAULT 0,target INTEGER NOT NULL DEFAULT 0,recommendation_bonus INTEGER NOT NULL DEFAULT 0,details_json TEXT NOT NULL DEFAULT '{}',game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL)`)
}

func shippedSectCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatalf("the content file must load; the gate is broken, not the tree: %v", err)
	}
	return catalog
}

// The Mortal and Spiritual worlds' public sects, by name. Written out rather
// than computed, because the rule under test is what decides the set; the
// private ones (Blood River, Corpse Lantern) and the hidden sect are named so
// a delegation speaking for one fails loudly.
var (
	mortalPublicSects    = []string{"Azure Cloud Sect", "Black Serpent Clan", "Crimson Furnace Sect", "Frozen Moon Palace"}
	spiritualPublicSects = []string{"Jade Meridian Sect", "Thousand Beast Valley"}
	neverRecruitingSects = []string{"Blood River Sect", "Corpse Lantern Pavilion", "Heaven-Devouring Demon Sect"}
)

func TestEveryPublicSectOfAWorldCanSendADelegation(t *testing.T) {
	catalog := shippedSectCatalog(t)
	for _, tc := range []struct {
		location string
		want     []string
	}{
		{"Greenriver Town", mortalPublicSects},
		{"Spirit Jade Capital", spiritualPublicSects},
	} {
		seen := map[string]bool{}
		for i := 0; i < 64; i++ {
			sect := recruitingSectFor(catalog, fmt.Sprintf("event-%d", i), tc.location)
			if sect == "" {
				t.Fatalf("a delegation at %s spoke for nobody", tc.location)
			}
			seen[sect] = true
		}
		got := make([]string, 0, len(seen))
		for sect := range seen {
			got = append(got, sect)
		}
		sort.Strings(got)
		if strings.Join(got, ",") != strings.Join(tc.want, ",") {
			t.Fatalf("delegations at %s spoke for %v, want exactly %v", tc.location, got, tc.want)
		}
		for _, never := range neverRecruitingSects {
			if seen[never] {
				t.Fatalf("a delegation at %s spoke for %s, whose gate is not public", tc.location, never)
			}
		}
	}
	// Deterministic: the same event always names the same sect.
	first := recruitingSectFor(catalog, "event-7", "Greenriver Town")
	for i := 0; i < 8; i++ {
		if again := recruitingSectFor(catalog, "event-7", "Greenriver Town"); again != first {
			t.Fatalf("one event named two sects: %q then %q", first, again)
		}
	}
}

func TestADelegationAtASectsOwnGateIsThatSects(t *testing.T) {
	catalog := shippedSectCatalog(t)
	for i := 0; i < 16; i++ {
		if got := recruitingSectFor(catalog, fmt.Sprintf("gate-%d", i), "Frozen Moon Terrace"); got != "Frozen Moon Palace" {
			t.Fatalf("a delegation on the Frozen Moon Palace's own gate spoke for %q", got)
		}
	}
	// A place the catalogue does not carry speaks for nobody.
	if got := recruitingSectFor(catalog, "private", "birth_family:1"); got != "" {
		t.Fatalf("a delegation in a household spoke for %q", got)
	}
}

func TestADelegationRecruitsForARealSectOfItsWorld(t *testing.T) {
	path, world := setupEventSiteDB(t, "Sect Recruitment", 3)
	spawnTestSite(t, path, world, "Sect Recruitment", 3)
	elder := fmt.Sprint(actionScalar(t, path, `SELECT sect_name FROM world_event_npcs WHERE event_key='site-event' AND npc_key='elder'`))
	disciple := fmt.Sprint(actionScalar(t, path, `SELECT sect_name FROM world_event_npcs WHERE event_key='site-event' AND npc_key='disciple'`))
	if elder == "" {
		t.Fatal("the Visiting Elder speaks for nobody")
	}
	if elder != disciple {
		t.Fatalf("the elder speaks for %q and the disciple for %q", elder, disciple)
	}
	found := false
	for _, sect := range mortalPublicSects {
		found = found || sect == elder
	}
	if !found {
		t.Fatalf("a Greenriver Town delegation spoke for %q, not a public Mortal sect", elder)
	}
	if storage.ParseInt(actionScalar(t, path, `SELECT can_recommend FROM world_event_npcs WHERE event_key='site-event' AND npc_key='elder'`)) != 1 {
		t.Fatal("the Visiting Elder cannot recommend anybody")
	}
	if storage.ParseInt(actionScalar(t, path, `SELECT can_recommend FROM world_event_npcs WHERE event_key='site-event' AND npc_key='disciple'`)) != 0 {
		t.Fatal("the recruiting disciple may sponsor applicants")
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT GROUP_CONCAT(node_key) FROM world_event_nodes WHERE event_key='site-event' AND reveals_sect<>''`)); got != "trial" {
		t.Fatalf("nodes revealing the sect: %q, want only the trial", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT reveals_sect FROM world_event_nodes WHERE event_key='site-event' AND node_key='trial'`)); got != elder {
		t.Fatalf("the trial reveals %q while the elder speaks for %q", got, elder)
	}
	// Enough of the trial for more than the first comer (v1.1.0: [1,3] was one).
	if got := storage.ParseInt(actionScalar(t, path, `SELECT total FROM world_event_nodes WHERE event_key='site-event' AND node_key='trial'`)); got < 3 {
		t.Fatalf("the recruitment trial holds %d; only the first player could pass it", got)
	}

	// A category that names no sect stamps nothing.
	tide, world := setupEventSiteDB(t, "Beast Tide", 5)
	spawnTestSite(t, tide, world, "Beast Tide", 5)
	if got := storage.ParseInt(actionScalar(t, tide, `SELECT COUNT(*) FROM world_event_npcs WHERE sect_name<>'' OR can_recommend<>0`)); got != 0 {
		t.Fatalf("a beast tide's cast carries a sect (%d rows)", got)
	}
	if got := storage.ParseInt(actionScalar(t, tide, `SELECT COUNT(*) FROM world_event_nodes WHERE reveals_sect<>''`)); got != 0 {
		t.Fatalf("a beast tide's site reveals a sect (%d nodes)", got)
	}
}

// In the compose stack the engine is healthy before db-init migrates, so a
// site can be spawned against the schema-60 tables. It must spawn - generic -
// rather than fail the tick that spawned it.
func TestASiteSpawnsBeforeTheMigrationHasRun(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `CREATE TABLE world_event_nodes (
    node_id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,node_key TEXT NOT NULL,node_type TEXT NOT NULL,
    name TEXT NOT NULL,descriptor TEXT NOT NULL DEFAULT '',rank INTEGER NOT NULL DEFAULT 1,total INTEGER NOT NULL DEFAULT 1,
    remaining INTEGER NOT NULL DEFAULT 0,cleared_by INTEGER NOT NULL DEFAULT 0,tn INTEGER NOT NULL DEFAULT 12,
    attribute TEXT NOT NULL DEFAULT 'insight',item_id TEXT NOT NULL DEFAULT '',item_qty INTEGER NOT NULL DEFAULT 0,
    cultivation INTEGER NOT NULL DEFAULT 0,spirit_stones INTEGER NOT NULL DEFAULT 0,contribution INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,updated_at REAL NOT NULL,UNIQUE(event_key,node_key))`)
	batch4Exec(t, path, `CREATE TABLE world_event_npcs (
    event_key TEXT NOT NULL,npc_key TEXT NOT NULL,name TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',personality TEXT NOT NULL DEFAULT '',speech TEXT NOT NULL DEFAULT '',
    want TEXT NOT NULL DEFAULT '',fear TEXT NOT NULL DEFAULT '',descriptor TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,PRIMARY KEY(event_key,npc_key))`)
	if n := spawnTestSite(t, path, world, "Sect Recruitment", 3); n < 4 {
		t.Fatalf("a recruitment site spawned %d rows against the schema-60 tables", n)
	}
	// Engaging a node reads the column too; absent, it reveals nothing and still works.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	node, err := loadWorldEventNodeTx(conn, "site-event", "trial")
	if err != nil {
		t.Fatalf("a node could not be read before the migration: %v", err)
	}
	if node.RevealsSect != "" {
		t.Fatalf("a pre-migration node reveals %q", node.RevealsSect)
	}
}

func TestClearingTheTrialNodeRevealsTheSectAndItsGate(t *testing.T) {
	path, world := setupEventSiteDB(t, "Sect Recruitment", 3)
	sectDoorTables(t, path)
	spawnTestSite(t, path, world, "Sect Recruitment", 3)
	sect := fmt.Sprint(actionScalar(t, path, `SELECT reveals_sect FROM world_event_nodes WHERE event_key='site-event' AND node_key='trial'`))
	gate := sectGate(shippedSectCatalog(t), sect)
	if gate == "" {
		t.Fatalf("the delegation's sect %q has no gate in the catalogue", sect)
	}

	// A failed attempt reveals nothing: the dice come up 1 and 1 against a
	// body of nothing.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":0,"agility":0,"spirit":0,"insight":0,"will":0,"presence":0}' WHERE user_id=42`)
	restore := gamerng.UseRoller(func(int) int { return 0 })
	failed := batch4Result(t, batch4Apply(t, path, world, "world_event.engage", 1, map[string]any{"event_key": "site-event", "node_key": "trial"}))
	restore()
	if failed["success"] == true {
		t.Fatalf("the forced failure succeeded: %v", failed)
	}
	if _, ok := failed["sect_revealed"]; ok {
		t.Fatal("a failed trial revealed the sect")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location=?`, gate)); got != 0 {
		t.Fatal("a failed trial put the gate on the travel list")
	}

	// A pass (attributes back at 100) reveals the sect and its gate.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":100,"agility":100,"spirit":100,"insight":100,"will":100,"presence":100}' WHERE user_id=42`)
	passed := batch4Result(t, batch4Apply(t, path, world, "world_event.engage", 2, map[string]any{"event_key": "site-event", "node_key": "trial"}))
	shown, ok := passed["sect_revealed"].(map[string]any)
	if !ok {
		t.Fatalf("clearing the delegation's trial revealed nothing: %v", passed)
	}
	if shown["sect_name"] != sect || shown["gate"] != gate {
		t.Fatalf("revealed %v, want %s at %s", shown, sect, gate)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_sect_discoveries WHERE user_id=42 AND sect_name=?`, sect)); got != 1 {
		t.Fatal("the sect was not recorded as discovered")
	}
	// The whole point: /travel now reaches the gate.
	travel := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 3, map[string]any{"destination": gate, "mode": "known"}))
	if travel["destination"] != gate {
		t.Fatalf("travel to the revealed gate: %v", travel)
	}
}

func TestTheVisitingElderRecommendsForHisSect(t *testing.T) {
	path, world := setupEventSiteDB(t, "Sect Recruitment", 3)
	sectDoorTables(t, path)
	spawnTestSite(t, path, world, "Sect Recruitment", 3)
	elder := fmt.Sprint(actionScalar(t, path, `SELECT name FROM world_event_npcs WHERE event_key='site-event' AND npc_key='elder'`))
	disciple := fmt.Sprint(actionScalar(t, path, `SELECT name FROM world_event_npcs WHERE event_key='site-event' AND npc_key='disciple'`))
	sect := fmt.Sprint(actionScalar(t, path, `SELECT sect_name FROM world_event_npcs WHERE event_key='site-event' AND npc_key='elder'`))
	gate := sectGate(shippedSectCatalog(t), sect)

	// Not the elder: the disciple is of the sect and may not sponsor anybody.
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 1, map[string]any{"npc_name": disciple}); err == nil || !strings.Contains(err.Error(), "cannot sponsor") {
		t.Fatalf("the recruiting disciple sponsored an applicant: %v", err)
	}
	// Not somewhere else: the elder is with the delegation.
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 2, map[string]any{"npc_name": elder}); err == nil || !strings.Contains(err.Error(), "is with the delegation at Greenriver Town") {
		t.Fatalf("the elder sponsored somebody from another town: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	// Not for another sect.
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 3, map[string]any{"npc_name": elder, "sect_name": "Void Serpent Cult"}); err == nil || !strings.Contains(err.Error(), "speaks for the "+sect) {
		t.Fatalf("the elder sponsored somebody into a sect he does not speak for: %v", err)
	}

	// In person, for his own sect. The caller names a location on the far
	// side of the heavens; it must not reach the travel list.
	out, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 4, map[string]any{"npc_name": elder, "location": "Void Serpent Pit"})
	if err != nil {
		t.Fatalf("the Visiting Elder could not recommend a candidate standing before him: %v", err)
	}
	result := batch4Result(t, out)
	if result["success"] != true || result["sect_name"] != sect || result["gate"] != gate {
		t.Fatalf("recommendation=%v, want a success for %s at %s", result, sect, gate)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location='Void Serpent Pit'`)); got != 0 {
		t.Fatal("a caller named Void Serpent Pit and it went on the travel list")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location=?`, gate)); got != 1 {
		t.Fatalf("the sponsored sect's gate %s is not on the travel list", gate)
	}

	// A delegation that has left sponsors nobody.
	batch4Exec(t, path, `UPDATE sect_recommendations SET status='used' WHERE user_id=42`)
	batch4Exec(t, path, `UPDATE world_events SET active=0 WHERE event_key='site-event'`)
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 5, map[string]any{"npc_name": elder}); err == nil || !strings.Contains(err.Error(), "cannot sponsor") {
		t.Fatalf("a departed delegation's elder still sponsored: %v", err)
	}
}

func TestACatalogueSponsorOnlySpeaksForTheirOwnSect(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	sectDoorTables(t, path)
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 1, map[string]any{"npc_name": "Inquisitor Shen Rui", "sect_name": "Blood River Sect"}); err == nil || !strings.Contains(err.Error(), "speaks for the Azure Cloud Sect") {
		t.Fatalf("Shen Rui sponsored an applicant into the Blood River Sect: %v", err)
	}
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 2, map[string]any{"npc_name": "Gate Elder Jian Mu"}); err == nil || !strings.Contains(err.Error(), "cannot sponsor") {
		t.Fatalf("an NPC who carries no can_recommend sponsored somebody: %v", err)
	}
	out, err := batch4ApplyErr(path, world, "sect.recruitment.recommendation", 42, 3, map[string]any{"npc_name": "Inquisitor Shen Rui"})
	if err != nil {
		t.Fatalf("Shen Rui could not sponsor anybody: %v", err)
	}
	if result := batch4Result(t, out); result["sect_name"] != "Azure Cloud Sect" || result["gate"] != "Azure Cloud Mountain Gate" {
		t.Fatalf("Shen Rui's recommendation=%v", result)
	}
}

// sitForcedTrial sits the Azure Cloud trial with both dice of both rolls
// forced to 5 and attributes of 2, at realm 0 phase 0, on a path and root the
// sect has no opinion of and at karma 0, so its authored tuning (v1.2.4) adds
// nothing: primary 10+2 against its base TN 14, secondary 10+2+1 against 13 -
// a fail by 2 and a bare success with nothing behind you.
func sitForcedTrial(t *testing.T, bonus int64, seq int) map[string]any {
	t.Helper()
	path := setupSectTrialDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=0,path='Beast Binder',spiritual_root='Water Root',karma_score=0,attributes_json='{"body":2,"agility":2,"spirit":2,"insight":2,"will":2,"presence":2}' WHERE user_id=42`)
	if bonus > 0 {
		batch4Exec(t, path, `INSERT INTO sect_recommendations(user_id,npc_name,sect_name,bonus,status,created_at,updated_at) VALUES(42,'Inquisitor Shen Rui','Azure Cloud Sect',?,'active',0,0)`, bonus)
	}
	restore := gamerng.UseRoller(func(int) int { return 4 })
	defer restore()
	return sitTrial(t, path, world, "Azure Cloud Sect", "Azure Cloud Mountain Gate", seq)
}

func TestTheRecommendationRidesBothTrialRolls(t *testing.T) {
	if got := fmt.Sprint(sitForcedTrial(t, 0, 1)["outcome"]); got != "fail" {
		t.Fatalf("the unsponsored forced trial was %s, want fail", got)
	}
	// +3 on both: 15 against 14 and 16 against 13 - a pass outright.
	result := sitForcedTrial(t, 3, 2)
	if got := fmt.Sprint(result["outcome"]); got != "pass" {
		t.Fatalf("a +3 recommendation left the trial at %s; the bonus is not riding the rolls (%v)", got, result)
	}
}

func TestARecommendationStillOpensTheConditionalPass(t *testing.T) {
	// +1 on both: primary 13 against 14 (-1), secondary 14 against 13 (+1) -
	// one success, combined 0, which only a sponsor turns into admission.
	if got := fmt.Sprint(sitForcedTrial(t, 1, 3)["outcome"]); got != "conditional_pass" {
		t.Fatalf("a +1 recommendation near miss was %s, want conditional_pass", got)
	}
}

func TestTheTrialIsSatAtTheGateTheCatalogNames(t *testing.T) {
	path := setupSectTrialDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	_, err := batch4ApplyErr(path, world, "sect.recruitment.trial", 42, 1, map[string]any{
		"sect_name": "Azure Cloud Sect", "location": "Greenriver Town", "trial_name": "Entrance"})
	if err == nil || !strings.Contains(err.Error(), "entrance trial is sat at Azure Cloud Mountain Gate") {
		t.Fatalf("a trial was sat in Greenriver Town because the caller said it was the gate: %v", err)
	}
}

func TestTheEnvoysHallPutsItsWorldsPublicGatesOnTheMap(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	sectDoorTables(t, path)
	catalog := shippedSectCatalog(t)

	if _, err := batch4ApplyErr(path, world, "sect.recruitment.envoys", 42, 1, map[string]any{}); err == nil || !strings.Contains(err.Error(), "realm capitals") {
		t.Fatalf("the envoys received somebody in Greenriver Town: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Azure Crown Imperial City' WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "sect.recruitment.envoys", 42, 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), "Azure Crown East District") {
		t.Fatalf("the envoys received somebody outside the temple quarter: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Azure Crown East District' WHERE user_id=42`)
	out, err := batch4ApplyErr(path, world, "sect.recruitment.envoys", 42, 3, map[string]any{})
	if err != nil {
		t.Fatalf("the envoys' hall refused a caller standing in it: %v", err)
	}
	result := batch4Result(t, out)
	raw, _ := json.Marshal(result["sects"])
	var shown []map[string]any
	_ = json.Unmarshal(raw, &shown)
	names := []string{}
	for _, s := range shown {
		names = append(names, fmt.Sprint(s["sect_name"]))
	}
	sort.Strings(names)
	if strings.Join(names, ",") != strings.Join(mortalPublicSects, ",") {
		t.Fatalf("the Mortal envoys named %v, want exactly %v", names, mortalPublicSects)
	}
	for _, sect := range mortalPublicSects {
		gate := sectGate(catalog, sect)
		if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location=?`, gate)); got != 1 {
			t.Fatalf("the envoys named %s and its gate %s is not on the travel list", sect, gate)
		}
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location='Blood River Gorge'`)); got != 0 {
		t.Fatal("the envoys revealed a private gate")
	}
	// "Their gates are on your travel list now" has to be true.
	travel := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 4, map[string]any{"destination": "Crimson Furnace Valley", "mode": "known"}))
	if travel["destination"] != "Crimson Furnace Valley" {
		t.Fatalf("travel to a gate the envoys named: %v", travel)
	}
}

func seedSectCommission(t *testing.T, path, key, seed string) {
	t.Helper()
	seedCommission(t, path, key, nil)
	batch4Exec(t, path, `UPDATE quest_definitions SET requires_sect='Azure Cloud Sect',seed_json=? WHERE quest_key=?`, seed, key)
}

func TestAnOutsiderMayTakeEntryLevelSectWorkAndEarnsStanding(t *testing.T) {
	path := setupCommissionDB(t)
	seedSectCommission(t, path, "commission_gate_roster", `{"outsider_standing": 25}`)
	commissionMustApply(t, path, "commission.accept", 42, 1, map[string]any{"quest_key": "commission_gate_roster", "game_minute": 1000})
	out := completeCommission(t, path, "commission_gate_roster", 1100)
	standing, ok := out["sect_standing"].(map[string]any)
	if !ok || storage.ParseInt(standing["delta"]) != 25 {
		t.Fatalf("an outsider's finished sect work paid no standing: %v", out)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key='Azure Cloud Sect'`)); got != 25 {
		t.Fatalf("standing with the Azure Cloud Sect is %d, want 25", got)
	}
}

func TestOutsiderStandingIsCappedAndOnlyForTheWayIn(t *testing.T) {
	// A seed that asks for more than the cap is paid the cap.
	path := setupCommissionDB(t)
	seedSectCommission(t, path, "commission_greedy", `{"outsider_standing": 99}`)
	commissionMustApply(t, path, "commission.accept", 42, 1, map[string]any{"quest_key": "commission_greedy", "game_minute": 1000})
	completeCommission(t, path, "commission_greedy", 1100)
	if got := storage.ParseInt(actionScalar(t, path, `SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key='Azure Cloud Sect'`)); got != outsiderStandingCap {
		t.Fatalf("a seed of 99 paid %d standing; the cap is %d", got, outsiderStandingCap)
	}

	// A disciple who takes it earns no standing: they are already in.
	member := setupCommissionDB(t)
	seedSectCommission(t, member, "commission_gate_roster", `{"outsider_standing": 25}`)
	batch4Exec(t, member, `INSERT INTO sect_membership(user_id,sect_name,joined_at) VALUES(42,'Azure Cloud Sect',0)`)
	commissionMustApply(t, member, "commission.accept", 42, 1, map[string]any{"quest_key": "commission_gate_roster", "game_minute": 1000})
	out := completeCommission(t, member, "commission_gate_roster", 1100)
	if _, ok := out["sect_standing"]; ok {
		t.Fatal("a disciple was paid the outsider's standing")
	}

	// A rival sect's disciple is refused even open work.
	rival := setupCommissionDB(t)
	seedSectCommission(t, rival, "commission_gate_roster", `{"outsider_standing": 25}`)
	batch4Exec(t, rival, `INSERT INTO sect_membership(user_id,sect_name,joined_at) VALUES(42,'Blood River Sect',0)`)
	if _, err := commissionApply(t, rival, "commission.accept", 42, 1, map[string]any{"quest_key": "commission_gate_roster", "game_minute": 1000}); err == nil {
		t.Fatal("a Blood River disciple took Azure Cloud outsider work")
	}

	// Work nobody opened stays members-only.
	closed := setupCommissionDB(t)
	seedSectCommission(t, closed, "commission_inner", `{}`)
	if _, err := commissionApply(t, closed, "commission.accept", 42, 1, map[string]any{"quest_key": "commission_inner", "game_minute": 1000}); err == nil {
		t.Fatal("an outsider took members-only sect work")
	}

	// A key the Quest Forge could write never opens the gate, whatever its seed.
	forged := setupCommissionDB(t)
	seedSectCommission(t, forged, "forge_gate_roster", `{"outsider_standing": 25}`)
	if _, err := commissionApply(t, forged, "commission.accept", 42, 1, map[string]any{"quest_key": "forge_gate_roster", "game_minute": 1000}); err == nil {
		t.Fatal("a forged key carrying outsider_standing let an outsider in")
	}
}

// v1.1.0: a caravan leaves from the city you stand in. From a gate - where a
// walk into a city ends - it used to find no road at all.
func TestACaravanLeavesFromTheCityWhoseGateYouStandAt(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 7000)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	caravanTables(t, conn)
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town North Gate' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',10) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=10`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',5000) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=5000`)
	out, err := batch4ApplyErr(path, world, "caravan.dispatch", 42, 1, map[string]any{"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 2, "escort": 0})
	if err != nil {
		t.Fatalf("a caravan dispatched from Greenriver Town's own gate found no road: %v", err)
	}
	_ = out
	if got := fmt.Sprint(actionScalar(t, path, `SELECT origin FROM caravans ORDER BY caravan_id DESC LIMIT 1`)); got != "Greenriver Town" {
		t.Fatalf("the caravan left from %q, want the city Greenriver Town", got)
	}
}
