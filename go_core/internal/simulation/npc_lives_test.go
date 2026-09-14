package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const npcLivesSchema = `
CREATE TABLE npc_life_state(
    npc_name TEXT PRIMARY KEY, birth_game_minute INTEGER NOT NULL DEFAULT 0,
    age_at_creation_years INTEGER NOT NULL DEFAULT 18, natural_lifespan_years INTEGER NOT NULL DEFAULT 75,
    health INTEGER NOT NULL DEFAULT 100, injury TEXT NOT NULL DEFAULT '', injury_severity INTEGER NOT NULL DEFAULT 0,
    sect_rank TEXT NOT NULL DEFAULT 'Independent Cultivator', career_progress INTEGER NOT NULL DEFAULT 0,
    relationship_status TEXT NOT NULL DEFAULT 'single', spouse_name TEXT NOT NULL DEFAULT '',
    children_count INTEGER NOT NULL DEFAULT 0, last_social_game_minute INTEGER NOT NULL DEFAULT 0,
    last_cultivation_game_minute INTEGER NOT NULL DEFAULT 0, death_game_minute INTEGER,
    cause_of_death TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE npc_descendants(
    descendant_id INTEGER PRIMARY KEY AUTOINCREMENT, child_name TEXT NOT NULL UNIQUE,
    parent_a TEXT NOT NULL, parent_b TEXT NOT NULL, birth_game_minute INTEGER NOT NULL,
    gender TEXT NOT NULL DEFAULT 'neutral', spiritual_root TEXT NOT NULL DEFAULT 'Mortal Root',
    realm_index INTEGER NOT NULL DEFAULT 0, phase INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'alive', generated_as_npc INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE npc_disciple_bonds(
    master_name TEXT NOT NULL, disciple_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    started_game_minute INTEGER NOT NULL DEFAULT 0, ended_game_minute INTEGER,
    reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL,
    PRIMARY KEY(master_name,disciple_name));
CREATE TABLE npc_social_relations(
    npc_a TEXT NOT NULL, npc_b TEXT NOT NULL, affinity INTEGER NOT NULL DEFAULT 0,
    trust INTEGER NOT NULL DEFAULT 0, grudge INTEGER NOT NULL DEFAULT 0,
    relation_type TEXT NOT NULL DEFAULT 'acquaintance', status TEXT NOT NULL DEFAULT 'active',
    started_game_minute INTEGER NOT NULL DEFAULT 0, last_interaction_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL, PRIMARY KEY(npc_a,npc_b));
CREATE TABLE world_history_events(
    source_key TEXT PRIMARY KEY, event_type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
    significance INTEGER NOT NULL DEFAULT 0, visibility TEXT NOT NULL DEFAULT 'public',
    location TEXT NOT NULL DEFAULT '', world_name TEXT NOT NULL DEFAULT '', faction TEXT NOT NULL DEFAULT '',
    actor_type TEXT NOT NULL DEFAULT '', actor_key TEXT NOT NULL DEFAULT '', actor_name TEXT NOT NULL DEFAULT '',
    target_type TEXT NOT NULL DEFAULT '', target_key TEXT NOT NULL DEFAULT '', target_name TEXT NOT NULL DEFAULT '',
    related_user_id INTEGER, related_npc_name TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
    game_minute INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
`

func livesRunner() *Runner {
	r := movementRunner()
	r.World.Roots = []string{"Fire", "Water", "Wood"}
	r.World.EventSites.NamePool = []string{"Hou Jin", "Bai Shen", "Lu Wenqing", "Shen Yi", "Tang Muyan"}
	r.World.Realms = []worlddata.Realm{
		{Name: "Body Tempering"}, {Name: "Qi Refining"}, {Name: "Foundation Establishment"},
	}
	return r
}

func livesDB(t *testing.T) string {
	t.Helper()
	return setupSimulationDB(t, npcMovementSchema+npcLivesSchema)
}

func livesConn(t *testing.T, path string) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn
}

func addLife(t *testing.T, path, name string, fields map[string]any) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_life_state(npc_name,updated_at) VALUES(?,0)
        ON CONFLICT(npc_name) DO NOTHING`, []any{name}); err != nil {
		t.Fatal(err)
	}
	for column, value := range fields {
		if _, err := conn.Execute(
			fmt.Sprintf(`UPDATE npc_life_state SET %s=? WHERE npc_name=?`, column), []any{value, name}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func scalar(t *testing.T, conn *storage.Conn, sql string, args ...any) int64 {
	t.Helper()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	row := firstMap(res)
	if row == nil {
		return 0
	}
	return i64(row["n"])
}

// Married couples never had a child: children_count was read only by the query
// looking for singles to marry, and npc_descendants had no writer at all. A
// world where everyone dies of old age and nobody is born has one ending.
func TestMarriedNPCsHaveChildren(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	for i := 0; i < 20; i++ {
		a, b := fmt.Sprintf("Chen A%02d", i), fmt.Sprintf("Chen B%02d", i)
		addNPC(t, path, a, "Greenriver Town", "Greenriver Town", "Mortal World", "Farmer", 50, "Independent")
		addNPC(t, path, b, "Greenriver Town", "Greenriver Town", "Mortal World", "Farmer", 50, "Independent")
		addLife(t, path, a, map[string]any{"relationship_status": "married", "spouse_name": b, "health": 90})
		addLife(t, path, b, map[string]any{"relationship_status": "married", "spouse_name": a, "health": 90})
	}
	conn := livesConn(t, path)
	born, err := r.npcChildbirth(conn, 4, 50000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if born == 0 {
		t.Fatal("twenty healthy married couples and not one child")
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_descendants`); got != born {
		t.Errorf("%d births reported, %d descendants recorded", born, got)
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_life_state WHERE children_count>0`); got == 0 {
		t.Error("a child was born and no parent's children_count moved")
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM world_history_events WHERE event_type='npc_birth'`); got == 0 {
		t.Error("children are born and the world never hears of it")
	}
}

// A child must not be born with a name somebody already carries: child_name is
// UNIQUE, and colliding with a catalogue NPC is worse than a refused birth.
func TestAChildIsNeverGivenATakenName(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	// Every name the pool could build for this family is already carried by
	// somebody, so there is nothing left to call the child.
	r.World.NPCs = map[string]worlddata.NPCDefinition{}
	for _, entry := range r.World.EventSites.NamePool {
		given := entry
		if idx := indexOfSpace(entry); idx >= 0 {
			given = entry[idx+1:]
		}
		r.World.NPCs["Chen "+given] = worlddata.NPCDefinition{}
	}
	conn := livesConn(t, path)
	name, err := r.freeChildName(conn, "Chen Wei")
	if err != nil {
		t.Fatal(err)
	}
	if name != "" {
		t.Errorf("every candidate name was taken and it still produced %q", name)
	}
}

func indexOfSpace(s string) int {
	for i := 0; i < len(s); i++ {
		if s[i] == ' ' {
			return i
		}
	}
	return -1
}

// career_progress climbed to its ceiling and was read by nothing, and sect_rank
// never moved. An NPC inside a sect climbs it; one outside earns a reputation.
func TestCareerProgressBuysRankInsideASectAndInfluenceOutside(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	addNPC(t, path, "Sworn Disciple", "Greenriver Town", "Greenriver Town", "Mortal World", "Disciple", 60, "Azure Cloud Sect")
	addLife(t, path, "Sworn Disciple", map[string]any{"career_progress": 900, "sect_rank": "Outer Disciple"})
	addNPC(t, path, "Rootless Wanderer", "Greenriver Town", "Greenriver Town", "Mortal World", "Wanderer", 60, "Independent")
	addLife(t, path, "Rootless Wanderer", map[string]any{"career_progress": 900})

	conn := livesConn(t, path)
	before := scalar(t, conn, `SELECT influence AS n FROM npc_civilization_state WHERE npc_name='Rootless Wanderer'`)
	if _, err := r.npcCareers(conn, 50000); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	res, err := conn.Execute(`SELECT sect_rank,career_progress FROM npc_life_state WHERE npc_name='Sworn Disciple'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if rank := fmt.Sprint(res.Rows[0][0]); rank != "Inner Disciple" {
		t.Errorf("sect_rank = %q, want Inner Disciple", rank)
	}
	if left := i64(res.Rows[0][1]); left != 0 {
		t.Errorf("career_progress was not spent: %d", left)
	}
	after := scalar(t, conn, `SELECT influence AS n FROM npc_civilization_state WHERE npc_name='Rootless Wanderer'`)
	if after <= before {
		t.Errorf("an independent's career bought nothing: influence %d -> %d", before, after)
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM world_history_events WHERE event_type='sect_promotion'`); got == 0 {
		t.Error("a promotion happened and the world never heard")
	}
}

func TestTheSectRankLadderEndsAtTheTop(t *testing.T) {
	if got := nextSectRank("Independent Cultivator"); got != "Outer Disciple" {
		t.Errorf("a new sect member starts at %q", got)
	}
	if got := nextSectRank("Elder"); got != "Grand Elder" {
		t.Errorf("an Elder is raised to %q", got)
	}
	if got := nextSectRank("Grand Elder"); got != "" {
		t.Errorf("a Grand Elder was promoted to %q", got)
	}
}

// realm_index was fixed at bootstrap: phase crept to nine and stopped there
// forever, so no NPC in the world ever crossed a realm. Wealth pays for it,
// which is the first thing wealth has ever been for.
func TestNPCsCrossRealmsAndPayForIt(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	for i := 0; i < 30; i++ {
		name := fmt.Sprintf("Climber %02d", i)
		addNPC(t, path, name, "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 90, "Independent")
		addLife(t, path, name, map[string]any{"health": 100})
	}
	conn := livesConn(t, path)
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET phase=9,wealth=500,realm_index=0`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	crossed, err := r.npcBreakthroughs(conn, 50000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if crossed == 0 {
		t.Fatal("thirty cultivators at stage nine with full purses and nobody crossed")
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_civilization_state WHERE realm_index>0`); got != crossed {
		t.Errorf("%d crossings reported, %d NPCs actually moved realm", crossed, got)
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_civilization_state WHERE realm_index>0 AND wealth>=500`); got != 0 {
		t.Error("a breakthrough was free")
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_civilization_state WHERE realm_index>0 AND phase<>1`); got != 0 {
		t.Error("a new realm did not start at stage one")
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM world_history_events WHERE event_type='npc_breakthrough'`); got == 0 {
		t.Error("a realm was crossed in silence")
	}
}

// A pauper cannot buy a breakthrough.
func TestABreakthroughNeedsTheMeansForIt(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	addNPC(t, path, "Penniless", "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 99, "Independent")
	addLife(t, path, "Penniless", map[string]any{"health": 100})
	conn := livesConn(t, path)
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET phase=9,wealth=5`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 30; i++ {
		if _, err := r.npcBreakthroughs(conn, int64(50000+i)); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if got := scalar(t, conn, `SELECT realm_index AS n FROM npc_civilization_state WHERE npc_name='Penniless'`); got != 0 {
		t.Error("a cultivator with five coins crossed a realm")
	}
}

// npc_disciple_bonds had no writer while world_status_queries.go *queried* it,
// so "who is whose disciple" was a question the world always answered empty.
//
// The hall is deliberately full. Taking a disciple is one `gamerng` roll per
// candidate pair at `discipleChance` (20 in 100), and this test asserts that
// *somebody* is taken - which with the fifteen youths it first had was a false
// failure once in every 28 runs (0.8^15 = 3.5%), and duly failed a CI run that
// had nothing to do with it. Seventy puts that at one run in six million
// (0.8^70), which is the same bargain `TestTheWorldsPeopleConsignWhatTheyFind`
// makes with its thirty grave-robbers. The production roll is untouched: the
// sample was too small to ask the question, not the odds wrong.
func TestMastersTakeDisciples(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	addNPC(t, path, "Elder Gao", "Greenriver Town", "Greenriver Town", "Mortal World", "Elder", 60, "Azure Cloud Sect")
	for i := 0; i < 70; i++ {
		addNPC(t, path, fmt.Sprintf("Youth %02d", i), "Greenriver Town", "Greenriver Town",
			"Mortal World", "Cultivator", 50, "Independent")
	}
	conn := livesConn(t, path)
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET realm_index=12 WHERE npc_name='Elder Gao'`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	formed, err := r.npcDiscipleBonds(conn, 50000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if formed == 0 {
		t.Fatal("an elder twelve realms above fifteen youths took none of them")
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_disciple_bonds WHERE status='active'`); got != formed {
		t.Errorf("%d bonds reported, %d recorded", formed, got)
	}
	// Nobody serves two masters.
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM (
        SELECT disciple_name FROM npc_disciple_bonds WHERE status='active' GROUP BY disciple_name HAVING COUNT(*)>1)`); got != 0 {
		t.Error("a disciple was taken by two masters at once")
	}
}

// A bond outlives neither party.
func TestABondEndsWhenTheMasterDies(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	addNPC(t, path, "Elder Gao", "Greenriver Town", "Greenriver Town", "Mortal World", "Elder", 60, "Azure Cloud Sect")
	addNPC(t, path, "Young Fan", "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 50, "Independent")
	conn := livesConn(t, path)
	if _, err := conn.Execute(`INSERT INTO npc_disciple_bonds(master_name,disciple_name,status,started_game_minute,updated_at)
        VALUES('Elder Gao','Young Fan','active',1,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead' WHERE npc_name='Elder Gao'`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if _, err := r.npcDiscipleBonds(conn, 50000); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_disciple_bonds WHERE status='active'`); got != 0 {
		t.Error("the master is dead and the bond is still active")
	}
}

// grudge climbed to a hundred and nothing ever happened; the only death in the
// world was old age.
func TestAGrudgeIsEventuallyAnswered(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	for i := 0; i < 30; i++ {
		a, b := fmt.Sprintf("Rival A%02d", i), fmt.Sprintf("Rival B%02d", i)
		addNPC(t, path, a, "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 60, "Independent")
		addNPC(t, path, b, "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 60, "Independent")
		addLife(t, path, a, map[string]any{"health": 100})
		addLife(t, path, b, map[string]any{"health": 100})
		seedRelation(t, path, a, b, 95)
	}
	conn := livesConn(t, path)
	fought, killed, err := r.npcFeuds(conn, 50000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if fought == 0 {
		t.Fatal("thirty grudges at ninety-five and nobody ever came to blows")
	}
	if killed > fought {
		t.Errorf("%d died out of %d confrontations", killed, fought)
	}
	settled := scalar(t, conn, `SELECT COUNT(*) AS n FROM npc_social_relations WHERE grudge<95`)
	if settled != fought {
		t.Errorf("%d fights but %d grudges came down", fought, settled)
	}
	if got := scalar(t, conn, `SELECT COUNT(*) AS n FROM world_history_events WHERE event_type IN ('npc_duel','npc_killing')`); got == 0 {
		t.Error("people fought and the world never heard")
	}
}

// A quiet relationship is left alone: only a grudge that has actually run its
// course is answered.
func TestASmallGrudgeIsNotAKnifeFight(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	addNPC(t, path, "Mild A", "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 50, "Independent")
	addNPC(t, path, "Mild B", "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 50, "Independent")
	addLife(t, path, "Mild A", map[string]any{"health": 100})
	addLife(t, path, "Mild B", map[string]any{"health": 100})
	seedRelation(t, path, "Mild A", "Mild B", 20)
	conn := livesConn(t, path)
	for i := 0; i < 30; i++ {
		fought, killed, err := r.npcFeuds(conn, int64(50000+i))
		if err != nil {
			t.Fatal(err)
		}
		if fought != 0 || killed != 0 {
			t.Fatalf("a grudge of twenty produced %d fights and %d deaths", fought, killed)
		}
	}
}

func seedRelation(t *testing.T, path, a, b string, grudge int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_social_relations(npc_a,npc_b,grudge,status,updated_at)
        VALUES(?,?,?,'active',0)`, []any{a, b, grudge}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}
