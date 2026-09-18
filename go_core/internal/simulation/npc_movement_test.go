package simulation

import (
	"fmt"
	"path/filepath"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const npcMovementSchema = `
CREATE TABLE npc_civilization_state(
    npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL, current_location TEXT NOT NULL,
    world_name TEXT NOT NULL, profession TEXT NOT NULL, faction TEXT NOT NULL DEFAULT 'Independent',
    wealth INTEGER NOT NULL DEFAULT 20, influence INTEGER NOT NULL DEFAULT 10,
    ambition INTEGER NOT NULL DEFAULT 50, realm_index INTEGER NOT NULL DEFAULT 0,
    phase INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'alive',
    activity TEXT NOT NULL DEFAULT '', missing_since_game_minute INTEGER NOT NULL DEFAULT 0, last_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE sect_politics_state(
    sect_name TEXT PRIMARY KEY, alignment TEXT NOT NULL DEFAULT 'Neutral', specialty TEXT NOT NULL DEFAULT '',
    influence INTEGER NOT NULL DEFAULT 50, cohesion INTEGER NOT NULL DEFAULT 50, resources INTEGER NOT NULL DEFAULT 50,
    recruitment_pressure INTEGER NOT NULL DEFAULT 50, doctrine_pressure INTEGER NOT NULL DEFAULT 50,
    leader_policy TEXT NOT NULL DEFAULT 'Balanced', last_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE sect_politics_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, sect_name TEXT NOT NULL, event_text TEXT NOT NULL,
    severity INTEGER NOT NULL DEFAULT 1, game_minute INTEGER NOT NULL, created_at REAL NOT NULL);
`

// A small, fully-known map: two towns joined by a road, one town off on its
// own, and a fourth in another world entirely.
func movementRunner() *Runner {
	return &Runner{World: worlddata.Catalog{Locations: map[string]worlddata.LocationDefinition{
		"Greenriver Town": {World: "Mortal World", Roads: []string{"Riverguard City"}},
		"Riverguard City": {World: "Mortal World", Roads: []string{"Greenriver Town"},
			Gates: map[string][]string{"North": {"Skyroad Immortal City"}}},
		"Lonely Rock":           {World: "Mortal World"},
		"Skyroad Immortal City": {World: "Immortal World", Roads: []string{"Riverguard City"}},
	}, NPCs: map[string]worlddata.NPCDefinition{}}}
}

func movementDB(t *testing.T) string {
	t.Helper()
	return setupSimulationDB(t, npcMovementSchema)
}

func addNPC(t *testing.T, path, name, home, current, world, profession string, ambition int64, faction string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state
        (npc_name,home_location,current_location,world_name,profession,faction,ambition,status,updated_at)
        VALUES(?,?,?,?,?,?,?, 'alive',0)`,
		[]any{name, home, current, world, profession, faction, ambition}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func locationOf(t *testing.T, path, name string) string {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT current_location FROM npc_civilization_state WHERE npc_name=?`, []any{name})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		t.Fatalf("%s is not in the world", name)
	}
	return fmt.Sprint(res.Rows[0][0])
}

// everyRollLands answers 0 to every bound, so every traveller sets out, every
// rootless cultivator swears and every crumbling sect's disciple walks. What
// the tick does after the roll is arithmetic, which turns "somebody moved"
// into the exact count the cap allows.
func everyRollLands() func() {
	return gamerng.UseRoller(func(int) int { return 0 })
}

// Nothing autonomous had ever written current_location: every NPC stood where
// they were born, for the life of the world.
func TestNPCsActuallyTakeTheRoad(t *testing.T) {
	defer everyRollLands()()
	path := movementDB(t)
	r := movementRunner()
	for i := 0; i < 40; i++ {
		addNPC(t, path, fmt.Sprintf("Peddler %02d", i), "Greenriver Town", "Greenriver Town",
			"Mortal World", "Travelling Peddler", 50, "Independent")
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	moved, err := r.npcTravel(conn, 3, 6000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if moved != travelMovedCap {
		t.Fatalf("forty peddlers who all set out moved %d, want the cap of %d", moved, travelMovedCap)
	}
	away := 0
	for i := 0; i < 40; i++ {
		if locationOf(t, path, fmt.Sprintf("Peddler %02d", i)) != "Greenriver Town" {
			away++
		}
	}
	if int64(away) != moved {
		t.Errorf("npcTravel reported %d moves and %d of them reached the table", moved, away)
	}
}

// The road is the map's own. A traveller must not step somewhere no road goes,
// and must never cross into another world on foot.
func TestTravelStaysOnTheRoadAndInTheWorld(t *testing.T) {
	r := movementRunner()
	if got := r.neighbours("Greenriver Town", "Mortal World", 99); len(got) != 1 || got[0] != "Riverguard City" {
		t.Errorf("Greenriver's neighbours = %v, want just Riverguard City", got)
	}
	if got := r.neighbours("Lonely Rock", "Mortal World", 99); len(got) != 0 {
		t.Errorf("a place with no roads has neighbours: %v", got)
	}
	// Riverguard's north gate opens on the Immortal World, which a Mortal
	// World NPC may not walk through.
	for _, name := range r.neighbours("Riverguard City", "Mortal World", 99) {
		if name == "Skyroad Immortal City" {
			t.Error("a Mortal World NPC can walk to the Immortal World")
		}
	}
	// An NPC whose world_name does not match where they are standing is not
	// somewhere the map can answer for, so the tick leaves them be.
	if got := r.neighbours("Riverguard City", "Immortal World", 99); got != nil {
		t.Errorf("a mismatched world answered neighbours: %v", got)
	}
	if got := r.neighbours("Nowhere At All", "Mortal World", 99); got != nil {
		t.Errorf("an unknown place has neighbours: %v", got)
	}
}

// A wandering hidden master is placed by content against the canonical clock.
// If the tick moved them too, the two answers would fight and the master would
// be in one place for /sense and another for everything else.
func TestTravelLeavesTheWanderingMastersAlone(t *testing.T) {
	path := movementDB(t)
	r := movementRunner()
	r.World.NPCs["The Barefoot Pilgrim"] = worlddata.NPCDefinition{
		Circuit: []string{"Greenriver Town", "Riverguard City"},
	}
	addNPC(t, path, "The Barefoot Pilgrim", "Greenriver Town", "Greenriver Town",
		"Mortal World", "Travelling Peddler", 90, "Independent")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for i := 0; i < 20; i++ {
		if _, err := r.npcTravel(conn, 3, int64(6000+i)); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if got := locationOf(t, path, "The Barefoot Pilgrim"); got != "Greenriver Town" {
		t.Errorf("the tick walked a master whose road is content: now at %q", got)
	}
}

// A rooted profession stays put far more than a travelling one. Without this
// the whole world drifts at the same rate and nowhere has regulars.
func TestWhoTravelsDependsOnWhatTheyDo(t *testing.T) {
	if travelChanceFor("Travelling Peddler") <= travelChanceFor("Gate Guard") {
		t.Error("a guard is as likely to wander off as a peddler")
	}
	if travelChanceFor("Sect Elder") > travelChanceFor("Herb Gatherer") {
		t.Error("an elder wanders more than an ordinary villager")
	}
	if travelChanceFor("Caravan Master") != travelChanceTrader {
		t.Error("a caravan master is not counted as a trader")
	}
}

// faction was chosen at bootstrap and frozen: no sect ever gained or lost a
// member in the life of a world.
func TestASectUnderRecruitmentPressureActuallyRecruits(t *testing.T) {
	defer everyRollLands()()
	path := movementDB(t)
	r := movementRunner()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,recruitment_pressure,cohesion,updated_at)
        VALUES('Azure Cloud Sect',95,80,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 40; i++ {
		addNPC(t, path, fmt.Sprintf("Rootless %02d", i), "Greenriver Town", "Greenriver Town",
			"Mortal World", "Wandering Cultivator", 80, "Independent")
	}
	conn2, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn2.Close()
	joined, left, err := r.npcSectChanges(conn2, 3, 9000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn2.Commit(); err != nil {
		t.Fatal(err)
	}
	if joined != sectChangeCap {
		t.Fatalf("a sect at 95 recruitment pressure with forty willing cultivators took %d, want the cap of %d", joined, sectChangeCap)
	}
	if left != 0 {
		t.Errorf("a sect at 80 cohesion lost %d members", left)
	}
	res, err := conn2.Execute(`SELECT COUNT(*) AS n FROM sect_politics_events WHERE sect_name='Azure Cloud Sect'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if i64(firstMap(res)["n"]) == 0 {
		t.Error("people joined and the sect's own log says nothing")
	}
}

// A sect that has come apart loses people, and a healthy one does not.
func TestASectThatHasComeApartLosesPeople(t *testing.T) {
	defer everyRollLands()()
	path := movementDB(t)
	r := movementRunner()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,recruitment_pressure,cohesion,updated_at)
        VALUES('Broken Banner Sect',10,5,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 40; i++ {
		addNPC(t, path, fmt.Sprintf("Disciple %02d", i), "Greenriver Town", "Greenriver Town",
			"Mortal World", "Sect Disciple", 50, "Broken Banner Sect")
	}
	conn2, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn2.Close()
	joined, left, err := r.npcSectChanges(conn2, 3, 9000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn2.Commit(); err != nil {
		t.Fatal(err)
	}
	if left != sectChangeCap {
		t.Fatalf("a sect at 5 cohesion lost %d of its forty disciples, want the cap of %d", left, sectChangeCap)
	}
	if joined != 0 {
		t.Errorf("a sect at 10 recruitment pressure recruited %d", joined)
	}
	res, err := conn2.Execute(`SELECT COUNT(*) AS n FROM npc_civilization_state WHERE faction='Independent'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if i64(firstMap(res)["n"]) == 0 {
		t.Error("people left and nobody became Independent")
	}
}

// A quiet sect - neither desperate for members nor falling apart - is left
// alone entirely. Churn for its own sake is noise, not a living world.
func TestAQuietSectIsLeftAlone(t *testing.T) {
	path := movementDB(t)
	r := movementRunner()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,recruitment_pressure,cohesion,updated_at)
        VALUES('Settled Sect',50,50,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 30; i++ {
		addNPC(t, path, fmt.Sprintf("Member %02d", i), "Greenriver Town", "Greenriver Town",
			"Mortal World", "Sect Disciple", 90, "Settled Sect")
		addNPC(t, path, fmt.Sprintf("Free %02d", i), "Greenriver Town", "Greenriver Town",
			"Mortal World", "Wandering Cultivator", 90, "Independent")
	}
	conn2, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn2.Close()
	joined, left, err := r.npcSectChanges(conn2, 3, 9000)
	if err != nil {
		t.Fatal(err)
	}
	if joined != 0 || left != 0 {
		t.Errorf("a settled sect churned: %d in, %d out", joined, left)
	}
}

// The check that caught the first version of this. A fixture map with two
// towns on a road passes happily while the real world does not join up at all:
// matching only `roads` and `gates` connected the 48 cities and left 429 of
// 477 places - every district, waystation, shrine and shop - with no neighbour,
// so almost every NPC alive could never have moved. This holds the real
// content to being walkable.
func TestTheRealWorldIsWalkable(t *testing.T) {
	catalog, err := worlddata.Load(filepath.Join("..", "..", "..", "content", "world.json"))
	if err != nil {
		t.Skipf("real world content not readable from here: %v", err)
	}
	r := &Runner{World: catalog}
	stranded, total := 0, 0
	for name, def := range catalog.Locations {
		if def.Private {
			continue
		}
		total++
		if len(r.neighbours(name, def.World, 99)) == 0 {
			stranded++
		}
	}
	if total == 0 {
		t.Fatal("the world has no public locations")
	}
	if stranded*10 > total {
		t.Errorf("%d of %d public places have nowhere to walk to - the map is not joined up",
			stranded, total)
	}

	// And the wandering hidden masters must reach Go, or the tick will walk
	// them on top of the circuit content already walks them on.
	circuits := 0
	for _, def := range catalog.NPCs {
		if len(def.Circuit) > 0 {
			circuits++
		}
	}
	if circuits == 0 {
		t.Error("no NPC carries a circuit - the field is not reaching Go from world.json")
	}
}
