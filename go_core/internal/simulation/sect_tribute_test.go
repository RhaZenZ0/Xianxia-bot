package simulation

import (
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A sect with no player members stocks its own storehouse (v1.0.0-rc.18).
//
// `sect_treasury` had one writer, `sect.contribute`, and it needed a player
// holding the goods - so a disciple of a quiet sect earned contribution points
// against empty shelves forever.

const sectTributeSchema = `
CREATE TABLE npc_civilization_state(
    npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL DEFAULT '', current_location TEXT NOT NULL DEFAULT '',
    world_name TEXT NOT NULL DEFAULT '', profession TEXT NOT NULL DEFAULT '', faction TEXT NOT NULL DEFAULT 'Independent',
    status TEXT NOT NULL DEFAULT 'alive', updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE sects(sect_name TEXT PRIMARY KEY, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE sect_treasury(
    sect_name TEXT NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(sect_name,item_id));
CREATE TABLE sect_politics_state(
    sect_name TEXT PRIMARY KEY, influence INTEGER NOT NULL DEFAULT 50, cohesion INTEGER NOT NULL DEFAULT 50,
    resources INTEGER NOT NULL DEFAULT 50, recruitment_pressure INTEGER NOT NULL DEFAULT 50,
    doctrine_pressure INTEGER NOT NULL DEFAULT 50, leader_policy TEXT NOT NULL DEFAULT '',
    last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
`

// tributeRunner is two sects in two different worlds, so the tier resolution
// is exercised rather than assumed, plus one hidden sect that takes none.
func tributeRunner() *Runner {
	return &Runner{World: worlddata.Catalog{
		Locations: map[string]worlddata.LocationDefinition{
			"Azure Cloud Mountain Gate":  {World: "Mortal World"},
			"Mandate Academy Star Steps": {World: "Celestial World"},
			"Nowhere In Particular":      {World: "Mortal World"},
		},
		Sects: map[string]worlddata.SectDefinition{
			"Azure Cloud Sect":          {Recruitment: worlddata.SectRecruitment{Location: "Azure Cloud Mountain Gate"}},
			"Celestial Mandate Academy": {Recruitment: worlddata.SectRecruitment{Location: "Mandate Academy Star Steps"}},
			"Heaven-Devouring Demon Sect": {Hidden: true,
				Recruitment: worlddata.SectRecruitment{Location: "Nowhere In Particular"}},
		},
		Items: map[string]worlddata.Item{
			"spirit_herb":      {Name: "Spirit Herb"},
			"spirit_iron":      {Name: "Spirit Iron"},
			"beast_core":       {Name: "Beast Core"},
			"heavenpetal_herb": {Name: "Heavenpetal Herb"},
			"starsteel_ore":    {Name: "Starsteel Ore"},
		},
		EventSites: worlddata.EventSites{TierMaterials: map[string]map[string]string{
			"Mortal World":    {"herb": "spirit_herb", "ore": "spirit_iron", "core": "beast_core"},
			"Celestial World": {"herb": "heavenpetal_herb", "ore": "starsteel_ore", "core": "beast_core"},
		}},
		SectSystem: map[string]any{"tribute": map[string]any{
			"materials":         []any{"@herb", "@ore", "@core"},
			"disciples_per_lot": 2,
			"cap":               6,
		}},
	}}
}

func addDisciple(t *testing.T, path, name, sect string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state(npc_name,faction,status,updated_at) VALUES(?,?,'alive',0)`,
		[]any{name, sect}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func runTribute(t *testing.T, path string, r *Runner, steps int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	n, err := r.sectTribute(conn, steps)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return n
}

func treasury(t *testing.T, path, sect, item string) int64 {
	t.Helper()
	return storage.ParseInt(simScalar(t, path, `SELECT quantity FROM sect_treasury WHERE sect_name=? AND item_id=?`, sect, item))
}

func TestASectWithNoPlayersStillStocksItsTreasury(t *testing.T) {
	path := setupSimulationDB(t, sectTributeSchema)
	r := tributeRunner()
	addDisciple(t, path, "Gate Elder Jian Mu", "Azure Cloud Sect")
	addDisciple(t, path, "Sword Hall Deacon", "Azure Cloud Sect")
	addDisciple(t, path, "A Wandering Nobody", "Independent")

	if n := runTribute(t, path, r, 1); n <= 0 {
		t.Fatalf("stocked %d; the shelves are still empty", n)
	}
	// Two disciples at two-per-lot is one of each material.
	for _, item := range []string{"spirit_herb", "spirit_iron", "beast_core"} {
		if got := treasury(t, path, "Azure Cloud Sect", item); got != 1 {
			t.Fatalf("%s=%d, want 1", item, got)
		}
	}
	// Nobody's tribute is drawn from the Independent NPCs, who are in no sect.
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM sect_treasury WHERE sect_name='Independent'`)); got != 0 {
		t.Fatalf("%d treasury rows for a faction that is not a sect", got)
	}
	// The sect exists as a row now, which is what the contribution shop reads.
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM sects WHERE sect_name='Azure Cloud Sect'`)); got != 1 {
		t.Fatal("the sect stocked a treasury without a sect row to hang it on")
	}
}

func TestTributeIsTheTierOfTheWorldTheSectStandsIn(t *testing.T) {
	path := setupSimulationDB(t, sectTributeSchema)
	r := tributeRunner()
	addDisciple(t, path, "Star Steps Preceptor", "Celestial Mandate Academy")
	addDisciple(t, path, "Mandate Reader", "Celestial Mandate Academy")
	runTribute(t, path, r, 1)

	// A Celestial sect stocks Celestial materials, from the same three-line
	// content list a Mortal World sect reads.
	if got := treasury(t, path, "Celestial Mandate Academy", "heavenpetal_herb"); got != 1 {
		t.Fatalf("heavenpetal_herb=%d, want 1", got)
	}
	if got := treasury(t, path, "Celestial Mandate Academy", "starsteel_ore"); got != 1 {
		t.Fatalf("starsteel_ore=%d, want 1", got)
	}
	if got := treasury(t, path, "Celestial Mandate Academy", "spirit_herb"); got != 0 {
		t.Fatalf("a Celestial academy stocked %d Mortal World herbs", got)
	}
	// beast_core is deliberately the same item in all four worlds.
	if got := treasury(t, path, "Celestial Mandate Academy", "beast_core"); got != 1 {
		t.Fatalf("beast_core=%d, want 1", got)
	}
}

func TestAHiddenSectTakesNoTribute(t *testing.T) {
	path := setupSimulationDB(t, sectTributeSchema)
	r := tributeRunner()
	addDisciple(t, path, "A Nameless Cell Leader", "Heaven-Devouring Demon Sect")
	runTribute(t, path, r, 1)
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM sect_treasury WHERE sect_name='Heaven-Devouring Demon Sect'`)); got != 0 {
		t.Fatalf("%d rows: a hidden sect has no counter to hand things in at", got)
	}
}

func TestATreasuryFillsToTheCapAndStops(t *testing.T) {
	path := setupSimulationDB(t, sectTributeSchema)
	r := tributeRunner()
	for _, name := range []string{"One", "Two", "Three", "Four"} {
		addDisciple(t, path, name, "Azure Cloud Sect")
	}
	// Four disciples is two lots a tick, and the content cap is six.
	for range 5 {
		runTribute(t, path, r, 1)
	}
	if got := treasury(t, path, "Azure Cloud Sect", "spirit_herb"); got != 6 {
		t.Fatalf("spirit_herb=%d, want the cap of 6", got)
	}
	if n := runTribute(t, path, r, 1); n != 0 {
		t.Fatalf("stocked %d more past the cap", n)
	}
}

func TestTributeNeverShrinksWhatAPlayerContributed(t *testing.T) {
	// The cap refuses the next delivery; it does not take anything back. A
	// treasury the tick could shrink would be a way to lose what you handed in.
	path := setupSimulationDB(t, sectTributeSchema)
	r := tributeRunner()
	addDisciple(t, path, "One", "Azure Cloud Sect")
	addDisciple(t, path, "Two", "Azure Cloud Sect")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES('Azure Cloud Sect','spirit_herb',400)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	runTribute(t, path, r, 1)
	if got := treasury(t, path, "Azure Cloud Sect", "spirit_herb"); got != 400 {
		t.Fatalf("spirit_herb=%d, want the 400 that were already there", got)
	}
}

func TestASectSpendingFasterThanItGathersStocksNothing(t *testing.T) {
	path := setupSimulationDB(t, sectTributeSchema)
	r := tributeRunner()
	addDisciple(t, path, "One", "Azure Cloud Sect")
	addDisciple(t, path, "Two", "Azure Cloud Sect")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,resources) VALUES('Azure Cloud Sect',11)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	if n := runTribute(t, path, r, 1); n != 0 {
		t.Fatalf("a sect at 11 resources stocked %d", n)
	}
}

func TestTributeIsANoOpWithoutTheContentRoster(t *testing.T) {
	// The roster is content: an installation whose world.json predates it
	// simply does not stock, rather than falling back to a list in the engine.
	path := setupSimulationDB(t, sectTributeSchema)
	r := tributeRunner()
	r.World.SectSystem = map[string]any{}
	addDisciple(t, path, "One", "Azure Cloud Sect")
	if n := runTribute(t, path, r, 1); n != 0 {
		t.Fatalf("stocked %d with no tribute roster in content", n)
	}
}
