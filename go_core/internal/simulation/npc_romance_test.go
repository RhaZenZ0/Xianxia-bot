package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/game"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Courtship, widowing and the seeded households (v1.0.0-rc.24).
//
// Everything asserted here is certain by its fixture rather than by iteration.
// `advanceCourtships`, `marryPair`, `endCourtship`, `seedHouseholds` and
// `ReleaseNPCBondsTx` contain no die at all; the single roll that does exist -
// whether a plausible pair actually begins something - is only ever asserted
// as a bound, never as "surely one of them landed".

func romanceRunner() *Runner {
	r := livesRunner()
	// A city with two districts, so the reach rule has something to prove:
	// two people at two addresses in one city can court, and that is the
	// whole difference between 89 possible pairs and 240.
	r.World.Locations["Riverguard City"] = worlddata.LocationDefinition{
		World: "Mortal World", SettlementType: "city", Roads: []string{"Greenriver Town"},
	}
	r.World.Locations["Riverguard Forge Terraces"] = worlddata.LocationDefinition{
		World: "Mortal World", OutsideLocation: "Riverguard City", District: "forge",
	}
	r.World.Locations["Riverguard Lower Town"] = worlddata.LocationDefinition{
		World: "Mortal World", OutsideLocation: "Riverguard City", District: "lower",
	}
	return r
}

func romanceNPC(t *testing.T, path, name, location string, realm, age int64, status string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state(
        npc_name,home_location,current_location,world_name,profession,faction,
        wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
        VALUES(?,?,?,'Mortal World','farmer','Independent',20,10,50,?,1,'alive','',0,0)`,
		[]any{name, location, location, realm}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO npc_life_state(
        npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,
        injury,injury_severity,sect_rank,career_progress,relationship_status,spouse_name,
        children_count,last_social_game_minute,last_cultivation_game_minute,updated_at)
        VALUES(?,0,?,75,100,'',0,'Independent Cultivator',0,?,'',0,0,0,0)`,
		[]any{name, age, status}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func romanceCommit(t *testing.T, conn *storage.Conn) {
	t.Helper()
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func romanceStr(t *testing.T, path, sql string, params ...any) string {
	t.Helper()
	return fmt.Sprint(simScalar(t, path, sql, params...))
}

func TestReachIsACityAndNotATile(t *testing.T) {
	r := romanceRunner()
	reach := r.romanceReach("Riverguard Forge Terraces", "Mortal World", 0)
	if !reach["Riverguard Lower Town"] {
		t.Fatal("two people in two districts of one city cannot reach each other; that is the rule that left 392 of 574 NPCs unable to marry anybody")
	}
	if !reach["Riverguard City"] {
		t.Fatal("a district cannot reach the city it is part of")
	}
	if reach["Lonely Rock"] {
		t.Fatal("reach should be one step, not the whole world")
	}
}

func TestACourtshipRipensIntoAMarriage(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard Forge Terraces", 0, 30, "single")
	romanceNPC(t, path, "Cui Ping", "Riverguard Lower Town", 0, 32, "single")

	conn := livesConn(t, path)
	if err := r.openCourtship(conn, "Bao Lin", "Cui Ping", 10, 1); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Bao Lin'`); got != "courting" {
		t.Fatalf("a courtship did not mark its people as courting: %q", got)
	}
	// Affinity opens at 20 and gains 14 a tick while they are still within
	// reach of each other, so the seventieth point arrives on the fourth.
	wed := int64(0)
	for tick := 0; tick < 4; tick++ {
		got, _, err := r.advanceCourtships(conn, int64(20+tick), 1)
		if err != nil {
			t.Fatal(err)
		}
		wed += got
	}
	romanceCommit(t, conn)
	if wed != 1 {
		t.Fatalf("four ticks of a courtship nobody interrupted produced %d marriage(s)", wed)
	}
	if got := romanceStr(t, path, `SELECT spouse_name FROM npc_life_state WHERE npc_name='Bao Lin'`); got != "Cui Ping" {
		t.Fatalf("spouse not written: %q", got)
	}
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Cui Ping'`); got != "married" {
		t.Fatalf("the other half of the couple is %q", got)
	}
	if got := romanceStr(t, path, `SELECT relation_type FROM npc_social_relations WHERE npc_a='Bao Lin' AND npc_b='Cui Ping'`); got != "marriage" {
		t.Fatalf("the bond is still %q", got)
	}
	if got := simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_marriage'`); storage.ParseInt(got) != 1 {
		t.Fatalf("a wedding nobody recorded: %v", got)
	}
}

func TestACourtshipAcrossTheWorldCools(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 30, "single")
	romanceNPC(t, path, "Far Shen", "Lonely Rock", 0, 30, "single")

	conn := livesConn(t, path)
	if err := r.openCourtship(conn, "Bao Lin", "Far Shen", 10, 1); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	// Twenty points, seven lost a tick with the roads between them.
	ended := int64(0)
	for tick := 0; tick < 3; tick++ {
		_, got, err := r.advanceCourtships(conn, int64(20+tick), 1)
		if err != nil {
			t.Fatal(err)
		}
		ended += got
	}
	romanceCommit(t, conn)
	if ended != 1 {
		t.Fatalf("a courtship held across the whole world never cooled: %d ended", ended)
	}
	for _, name := range []string{"Bao Lin", "Far Shen"} {
		if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name=?`, name); got != "single" {
			t.Fatalf("%s is %q after the courtship ended, not single", name, got)
		}
	}
	if got := romanceStr(t, path, `SELECT status FROM npc_social_relations WHERE npc_a='Bao Lin' AND npc_b='Far Shen'`); got != "ended" {
		t.Fatalf("the bond is still %q", got)
	}
}

func TestKinAndEnemiesAreNotCourted(t *testing.T) {
	r := romanceRunner()
	here := map[string][]romanceCandidate{
		"Riverguard City": {
			{name: "Bao Lin", location: "Riverguard City", world: "Mortal World", realm: 0, age: 30},
			{name: "Bao Yan", location: "Riverguard City", world: "Mortal World", realm: 0, age: 28},
		},
	}
	courter := here["Riverguard City"][0]

	// Siblings: both children of the same parent.
	kin := map[string]map[string]bool{
		"Bao Lin": {"Bao Elder": true},
		"Bao Yan": {"Bao Elder": true},
	}
	if _, ok := r.bestMatch(courter, here, kin, map[string]romanceBond{}, map[string]bool{}); ok {
		t.Fatal("a brother was offered his sister")
	}
	// A parent and their child.
	kin = map[string]map[string]bool{"Bao Yan": {"Bao Lin": true}}
	if _, ok := r.bestMatch(courter, here, kin, map[string]romanceBond{}, map[string]bool{}); ok {
		t.Fatal("a parent was offered their own child")
	}
	// Somebody they hold a grudge against - which only became possible at
	// all once anything in this engine started raising that column.
	_, _, key := romancePairKey("Bao Lin", "Bao Yan")
	bonds := map[string]romanceBond{key: {relation: "grudge", grudge: courtshipGrudgeMax + 20}}
	if _, ok := r.bestMatch(courter, here, map[string]map[string]bool{}, bonds, map[string]bool{}); ok {
		t.Fatal("a standing grudge was courted anyway")
	}
	// And with none of that in the way, they are a match.
	if _, ok := r.bestMatch(courter, here, map[string]map[string]bool{}, map[string]romanceBond{}, map[string]bool{}); !ok {
		t.Fatal("two unrelated neighbours of the same realm and age were not a match")
	}
}

func TestARealmOrALifetimeApartIsNotAMatch(t *testing.T) {
	r := romanceRunner()
	courter := romanceCandidate{name: "Bao Lin", location: "Riverguard City", world: "Mortal World", realm: 0, age: 30}
	tooHigh := map[string][]romanceCandidate{"Riverguard City": {courter,
		{name: "Elder Wu", location: "Riverguard City", world: "Mortal World", realm: courtshipRealmGap + 2, age: 30}}}
	if _, ok := r.bestMatch(courter, tooHigh, nil, map[string]romanceBond{}, map[string]bool{}); ok {
		t.Fatal("a farmer was matched with an elder several realms above them")
	}
	tooOld := map[string][]romanceCandidate{"Riverguard City": {courter,
		{name: "Ancient Mu", location: "Riverguard City", world: "Mortal World", realm: 0, age: 30 + courtshipAgeGap + 5}}}
	if _, ok := r.bestMatch(courter, tooOld, nil, map[string]romanceBond{}, map[string]bool{}); ok {
		t.Fatal("a sixty-year age gap was treated as a match")
	}
}

func TestADeathWidowsTheSurvivorAndFreesACourtship(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 40, "single")
	romanceNPC(t, path, "Cui Ping", "Riverguard City", 0, 40, "single")
	romanceNPC(t, path, "Du Ran", "Riverguard City", 0, 30, "single")
	romanceNPC(t, path, "En Mei", "Riverguard City", 0, 30, "single")

	// Two attachments and two deaths: a marriage leaves a widow, a courtship
	// leaves somebody single. Nobody is ever both at once.
	conn := livesConn(t, path)
	if err := r.marryPair(conn, "Bao Lin", "Cui Ping", 10, 1); err != nil {
		t.Fatal(err)
	}
	if err := r.openCourtship(conn, "Du Ran", "En Mei", 10, 1); err != nil {
		t.Fatal(err)
	}
	if err := game.ReleaseNPCBondsTx(conn, "Cui Ping", 20, 2); err != nil {
		t.Fatal(err)
	}
	if err := game.ReleaseNPCBondsTx(conn, "En Mei", 20, 2); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Bao Lin'`); got != "widowed" {
		t.Fatalf("the survivor is %q; nothing had ever set this column back from 'married'", got)
	}
	if got := romanceStr(t, path, `SELECT spouse_name FROM npc_life_state WHERE npc_name='Bao Lin'`); got != "" {
		t.Fatalf("the widower is still carrying a spouse: %q", got)
	}
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Du Ran'`); got != "single" {
		t.Fatalf("the courted partner of the dead is %q", got)
	}
	if got := simScalar(t, path, `SELECT COUNT(*) FROM npc_social_relations WHERE status='active'`); storage.ParseInt(got) != 0 {
		t.Fatalf("%v bond(s) survived the person they belonged to", got)
	}
}

func TestAWidowBearsNoMoreChildrenToADeadMan(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 40, "married")
	romanceNPC(t, path, "Cui Ping", "Riverguard City", 0, 40, "married")

	conn := livesConn(t, path)
	if _, err := conn.Execute(`UPDATE npc_life_state SET spouse_name='Cui Ping' WHERE npc_name='Bao Lin'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE npc_life_state SET spouse_name='Bao Lin' WHERE npc_name='Cui Ping'`, nil); err != nil {
		t.Fatal(err)
	}
	// The husband dies, and - this is the bug - nothing widows her, because
	// the only death path here is a bare UPDATE with no bond release.
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead' WHERE npc_name='Cui Ping'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE npc_life_state SET health=0 WHERE npc_name='Cui Ping'`, nil); err != nil {
		t.Fatal(err)
	}
	// No die is reached: the query itself returns nothing, so this holds on
	// every run rather than on most of them.
	for tick := 0; tick < 12; tick++ {
		born, err := r.npcChildbirth(conn, 1, int64(30+tick))
		if err != nil {
			t.Fatal(err)
		}
		if born != 0 {
			t.Fatalf("a widow bore %d child(ren) to a dead husband", born)
		}
	}
}

func TestBeginningCourtshipsRespectsItsCapAndItsRules(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	// Twenty people who could all plausibly pair off, which is well past the
	// cap. The count that comes back is a die; the bound is not.
	for i := 0; i < 20; i++ {
		romanceNPC(t, path, fmt.Sprintf("Person %02d", i), "Riverguard City", 0, 30, "single")
	}
	conn := livesConn(t, path)
	started, err := r.beginCourtships(conn, 1, 40, 1)
	if err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if started > courtshipStartCap {
		t.Fatalf("one tick opened %d courtships against a cap of %d", started, courtshipStartCap)
	}
	pairs := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_social_relations WHERE relation_type='courtship'`))
	if pairs != started {
		t.Fatalf("%d courtships reported, %d rows written", started, pairs)
	}
	courting := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_life_state WHERE relationship_status='courting'`))
	if courting != started*2 {
		t.Fatalf("%d courtships should mark %d people, marked %d", started, started*2, courting)
	}
}

func romanceDB(t *testing.T) string {
	t.Helper()
	return livesDB(t)
}

// The seeded world (v1.0.0-rc.24). Bootstrap is keyed off `hash64` throughout,
// so these numbers are a property of the shipped content rather than of a die,
// and the bands below are written down so the next person can see at a glance
// whether the world still opens with a past in it.
const seededWorldSchema = `
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY,world_name TEXT,population INTEGER,prosperity INTEGER,security INTEGER,spirit_resources INTEGER,food_supply INTEGER,migration_pressure INTEGER,unrest INTEGER,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,world_name TEXT,profession TEXT,faction TEXT,wealth INTEGER,influence INTEGER,ambition INTEGER,realm_index INTEGER,phase INTEGER,status TEXT,activity TEXT,missing_since_game_minute INTEGER DEFAULT 0,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_mind_state(npc_name TEXT PRIMARY KEY,current_goal TEXT,mood TEXT,focus_target TEXT,recent_event TEXT,goal_progress INTEGER,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_life_state(npc_name TEXT PRIMARY KEY,birth_game_minute INTEGER,age_at_creation_years INTEGER,natural_lifespan_years INTEGER,health INTEGER,injury TEXT,injury_severity INTEGER,sect_rank TEXT,career_progress INTEGER,relationship_status TEXT,spouse_name TEXT,children_count INTEGER,last_social_game_minute INTEGER,last_cultivation_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_social_relations(npc_a TEXT,npc_b TEXT,affinity INTEGER,trust INTEGER,grudge INTEGER,relation_type TEXT,status TEXT,started_game_minute INTEGER,last_interaction_game_minute INTEGER,updated_at REAL,PRIMARY KEY(npc_a,npc_b));
CREATE TABLE npc_descendants(descendant_id INTEGER PRIMARY KEY AUTOINCREMENT,child_name TEXT NOT NULL UNIQUE,parent_a TEXT,parent_b TEXT,birth_game_minute INTEGER,gender TEXT,spiritual_root TEXT,realm_index INTEGER,phase INTEGER,status TEXT,generated_as_npc INTEGER,created_at REAL,updated_at REAL);
CREATE TABLE sect_politics_state(sect_name TEXT PRIMARY KEY,alignment TEXT,specialty TEXT,influence INTEGER,cohesion INTEGER,resources INTEGER,recruitment_pressure INTEGER,doctrine_pressure INTEGER,leader_policy TEXT,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE sect_factions(sect_name TEXT,faction_name TEXT,agenda TEXT,power INTEGER,loyalty INTEGER,updated_at REAL,PRIMARY KEY(sect_name,faction_name));
CREATE TABLE sect_relations(sect_a TEXT,sect_b TEXT,relation_score INTEGER,relation_type TEXT,treaty_status TEXT,updated_at REAL,PRIMARY KEY(sect_a,sect_b));
CREATE TABLE economy_markets(location TEXT,item_id TEXT,world_name TEXT,currency_id TEXT,base_price INTEGER,supply INTEGER,demand INTEGER,price_index REAL,last_game_minute INTEGER,updated_at REAL,PRIMARY KEY(location,item_id));
CREATE TABLE world_history_events(source_key TEXT PRIMARY KEY,event_type TEXT,title TEXT,summary TEXT,significance INTEGER,visibility TEXT,location TEXT,world_name TEXT,faction TEXT,actor_type TEXT,actor_key TEXT,actor_name TEXT,target_type TEXT,target_key TEXT,target_name TEXT,related_user_id INTEGER,related_npc_name TEXT,tags TEXT,game_minute INTEGER,metadata_json TEXT,created_at REAL,updated_at REAL);
CREATE TABLE birth_families(family_id INTEGER PRIMARY KEY,family_name TEXT,surname TEXT,tier INTEGER,head_name TEXT,head_realm_index INTEGER,branch_count INTEGER,retainer_count INTEGER,line_status TEXT);
CREATE TABLE martial_clan_branches(branch_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER,branch_name TEXT,branch_type TEXT,leader_name TEXT,members_estimate INTEGER,martial_strength INTEGER,wealth_share INTEGER,loyalty INTEGER,status TEXT,updated_at REAL);
CREATE TABLE martial_clan_retainers(retainer_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER,group_name TEXT,leader_name TEXT,role TEXT,members INTEGER,realm_index INTEGER,loyalty INTEGER,upkeep INTEGER,status TEXT,updated_at REAL);
CREATE TABLE martial_clan_relations(relation_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER,partner_family_id INTEGER,partner_name TEXT,relation_type TEXT,relation_score INTEGER,active INTEGER,started_game_minute INTEGER,updated_at REAL);
INSERT INTO birth_families(family_id,family_name,surname,tier,head_name,head_realm_index,branch_count,retainer_count,line_status)
VALUES(1,'Han Clan','Han',2,'Han Patriarch',3,2,12,'active');
`

func seededWorld(t *testing.T) (string, BootstrapResult) {
	t.Helper()
	path := setupSimulationDB(t, seededWorldSchema)
	runner, err := NewRunner(path, bootstrapWorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	out, err := runner.Bootstrap(BootstrapRequest{GameMinute: 100})
	if err != nil {
		t.Fatal(err)
	}
	return path, out
}

func TestTheWorldOpensWithHouseholdsAndChildrenInIt(t *testing.T) {
	path, out := seededWorld(t)
	npcs := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_civilization_state`))
	married := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_life_state WHERE relationship_status='married'`))
	children := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_descendants`))
	bonds := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_social_relations WHERE relation_type='marriage'`))
	t.Logf("seeded world: %d NPCs, %d households (%d people married), %d children, %d marriage bonds",
		npcs, out.NPCHouseholdsSeeded, married, children, bonds)

	if out.NPCHouseholdsSeeded == 0 {
		t.Fatal("a freshly created world still contains nobody who has ever married anybody")
	}
	if married != out.NPCHouseholdsSeeded*2 {
		t.Fatalf("%d households should be %d married people, found %d", out.NPCHouseholdsSeeded, out.NPCHouseholdsSeeded*2, married)
	}
	if bonds != out.NPCHouseholdsSeeded {
		t.Fatalf("%d households, %d relation rows", out.NPCHouseholdsSeeded, bonds)
	}
	if out.NPCChildrenSeeded == 0 {
		t.Fatal("households were seeded but not one of them has a child")
	}
	if children != out.NPCChildrenSeeded {
		t.Fatalf("%d children reported, %d rows", out.NPCChildrenSeeded, children)
	}
	// Short of half the world, deliberately: a town where everyone is married
	// reads as odd as one where nobody is.
	if married*100/npcs > 55 {
		t.Fatalf("%d of %d NPCs are married; that is a pageant, not a town", married, npcs)
	}
	// Nobody is their own spouse, and every spouse is somebody real.
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_life_state WHERE spouse_name=npc_name`)); got != 0 {
		t.Fatalf("%d NPC(s) married themselves", got)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_life_state l
        WHERE l.spouse_name<>'' AND NOT EXISTS(SELECT 1 FROM npc_civilization_state c WHERE c.npc_name=l.spouse_name)`)); got != 0 {
		t.Fatalf("%d NPC(s) are married to somebody who does not exist", got)
	}
	// A child is nobody's age-mate: the parent gap is the whole reason the
	// seeding bothers to read ages at all.
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_descendants WHERE parent_a=parent_b`)); got != 0 {
		t.Fatalf("%d child(ren) have one person as both parents", got)
	}
}

func TestTheWorldOpensWithEldersInIt(t *testing.T) {
	path, _ := seededWorld(t)
	// "Near the end" has to be measured against the realm ceiling, not the
	// natural span: a Foundation Establishment cultivator dies at two or
	// three hundred with a `natural_lifespan_years` of seventy-five, so
	// comparing an age to the natural span calls four hundred and forty-eight
	// of five hundred and seventy-four people dying. Ask the lifespan model
	// the same question `bootstrapElderAge` asked it.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT l.npc_name,l.age_at_creation_years,l.natural_lifespan_years,l.health,
            c.realm_index,c.phase
        FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name`, nil)
	if err != nil {
		t.Fatal(err)
	}
	total, old, frail, ageless := 0, 0, 0, 0
	for _, row := range res.Rows {
		total++
		age, natural := i64(row[1]), i64(row[2])
		if i64(row[3]) < 100 {
			frail++
		}
		ceiling := lifespanmodel.RealmCeiling(i64(row[4]), i64(row[5]), natural)
		if ceiling == nil {
			ageless++
			continue
		}
		if *ceiling > 0 && age*100 / *ceiling >= int64(elderFloorPct) {
			old++
		}
	}
	t.Logf("seeded world: %d of %d NPCs are near the end of the span their realm allows (%d are ageless, %d begin below full health)",
		old, total, ageless, frail)

	if old == 0 {
		t.Fatal("not one person in the world is near the end of a life, so npc_life has nobody to bury for decades")
	}
	if frail == 0 {
		t.Fatal("the old begin at full health, which makes them indistinguishable from the young")
	}
	if old != frail {
		t.Fatalf("%d are near the end but %d begin frail; the two should be the same people", old, frail)
	}
	// A share, not a cull - the lesson of the hunting figure in rc.22: a rate
	// that empties a trade inside a season is not a living world.
	if old*100/total > 20 {
		t.Fatalf("%d of %d NPCs begin near death; that is a plague, not a generation", old, total)
	}
}

func TestAPoliticalMarriageIsATreatyAndNotARomance(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	conn := livesConn(t, path)
	if err := conn.ExecScript(`CREATE TABLE sect_relations(sect_a TEXT,sect_b TEXT,relation_score INTEGER,relation_type TEXT,treaty_status TEXT,updated_at REAL,PRIMARY KEY(sect_a,sect_b));`); err != nil {
		t.Fatal(err)
	}
	romanceNPC(t, path, "Elder Ma", "Riverguard City", 2, 80, "single")
	romanceNPC(t, path, "Elder Nie", "Riverguard City", 2, 90, "single")
	romanceNPC(t, path, "Junior Ou", "Riverguard City", 0, 25, "single")
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET faction='Azure Cloud Sect',influence=90 WHERE npc_name='Elder Ma'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET faction='Crimson Furnace Sect',influence=80 WHERE npc_name='Elder Nie'`, nil); err != nil {
		t.Fatal(err)
	}
	// Junior enough that no banner would offer them.
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET faction='Crimson Furnace Sect',influence=5 WHERE npc_name='Junior Ou'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO sect_relations VALUES('Azure Cloud Sect','Crimson Furnace Sect',20,'neutral','none',0)`, nil); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)

	// Who a sect would actually offer is not a die.
	who, err := r.mostInfluentialFree(conn, "Crimson Furnace Sect")
	if err != nil {
		t.Fatal(err)
	}
	if who != "Elder Nie" {
		t.Fatalf("a sect offered %q rather than its weightiest unattached member", who)
	}

	// The match itself is written directly, so what it writes is certain.
	if err := r.marryPairAs(conn, "Elder Ma", "Elder Nie", true, 50, 2); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT spouse_name FROM npc_life_state WHERE npc_name='Elder Ma'`); got != "Elder Nie" {
		t.Fatalf("the treaty did not marry anybody: %q", got)
	}
	affinity := storage.ParseInt(simScalar(t, path, `SELECT affinity FROM npc_social_relations WHERE npc_a='Elder Ma' AND npc_b='Elder Nie'`))
	trust := storage.ParseInt(simScalar(t, path, `SELECT trust FROM npc_social_relations WHERE npc_a='Elder Ma' AND npc_b='Elder Nie'`))
	if affinity != politicalAffinity || trust != politicalTrust {
		t.Fatalf("an arranged match opened at affinity %d / trust %d; it should be standing without warmth", affinity, trust)
	}
	if affinity >= trust {
		t.Fatal("a marriage nobody was asked about should not begin warmer than it is trusted")
	}
	if got := simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_political_marriage'`); storage.ParseInt(got) != 1 {
		t.Fatalf("the world did not record whose treaty this was: %v", got)
	}
}

func TestPoliticalMarriagesRespectTheirCapAndNeverMarryASectToItself(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	conn := livesConn(t, path)
	if err := conn.ExecScript(`CREATE TABLE sect_relations(sect_a TEXT,sect_b TEXT,relation_score INTEGER,relation_type TEXT,treaty_status TEXT,updated_at REAL,PRIMARY KEY(sect_a,sect_b));`); err != nil {
		t.Fatal(err)
	}
	sects := []string{"Sect A", "Sect B", "Sect C", "Sect D", "Sect E", "Sect F"}
	// Every NPC is written on its own connection, so they all go in before
	// this one opens a transaction - interleaving the two deadlocks the file.
	for i := range sects {
		romanceNPC(t, path, fmt.Sprintf("Elder %02d", i), "Riverguard City", 2, 80, "single")
	}
	for i, sect := range sects {
		if _, err := conn.Execute(`UPDATE npc_civilization_state SET faction=?,influence=90 WHERE npc_name=?`,
			[]any{sect, fmt.Sprintf("Elder %02d", i)}); err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i+1 < len(sects); i += 2 {
		if _, err := conn.Execute(`INSERT INTO sect_relations VALUES(?,?,20,'neutral','none',0)`, []any{sects[i], sects[i+1]}); err != nil {
			t.Fatal(err)
		}
	}
	// A sect on terms with itself must never produce a wedding.
	if _, err := conn.Execute(`INSERT INTO sect_relations VALUES('Sect A','Sect A',20,'neutral','none',0)`, nil); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)

	made, err := r.npcPoliticalMarriages(conn, 60, 3)
	if err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if made > politicalCap {
		t.Fatalf("one tick arranged %d marriages against a cap of %d", made, politicalCap)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_life_state WHERE spouse_name=npc_name`)); got != 0 {
		t.Fatalf("%d person(s) were married to themselves", got)
	}
	// Whatever the die said, every marriage that happened is between two
	// different banners.
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_social_relations s
        JOIN npc_civilization_state a ON a.npc_name=s.npc_a
        JOIN npc_civilization_state b ON b.npc_name=s.npc_b
        WHERE s.relation_type='marriage' AND a.faction=b.faction`)); got != 0 {
		t.Fatalf("%d political marriage(s) joined a sect to itself", got)
	}
}
