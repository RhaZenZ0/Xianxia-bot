package simulation

import (
	"fmt"
	"path/filepath"
	"testing"

	"xianxia/core/internal/game"
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

// A gate a cultivator tore open is the one road in the game that leaves a
// world, and the world's own people use it (v1.0.0-rc.44).
//
// The dice are lent because both halves are rolls - whether this NPC sets out
// at all, and whether they are the one who steps through - and a test that
// waited for a six-percent chance to land is exactly the shape rc.42 spent a
// release removing.
const crossingSchema = `
CREATE TABLE world_crossings(location_key TEXT PRIMARY KEY,name TEXT NOT NULL DEFAULT '',from_world TEXT NOT NULL,to_world TEXT NOT NULL,destination_location TEXT NOT NULL,min_realm_index INTEGER NOT NULL DEFAULT 0,opened_realm_index INTEGER NOT NULL DEFAULT 0,cost INTEGER NOT NULL DEFAULT 0,opened_by_user_id INTEGER,opened_game_minute INTEGER NOT NULL DEFAULT 0,player_uses INTEGER NOT NULL DEFAULT 0,npc_uses INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE world_history_events(source_key TEXT PRIMARY KEY,event_type TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,significance INTEGER NOT NULL DEFAULT 1,visibility TEXT NOT NULL DEFAULT 'public',location TEXT NOT NULL DEFAULT '',world_name TEXT NOT NULL DEFAULT '',faction TEXT NOT NULL DEFAULT '',actor_type TEXT NOT NULL DEFAULT '',actor_key TEXT NOT NULL DEFAULT '',actor_name TEXT NOT NULL DEFAULT '',target_type TEXT NOT NULL DEFAULT '',target_key TEXT NOT NULL DEFAULT '',target_name TEXT NOT NULL DEFAULT '',related_user_id INTEGER,related_npc_name TEXT NOT NULL DEFAULT '',tags TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,metadata_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0);
`

func TestTheGateIsUnlockedForTheWorldsOwnPeople(t *testing.T) {
	defer everyRollLands()()
	path := setupSimulationDB(t, npcMovementSchema+crossingSchema)
	r := movementRunner()
	addNPC(t, path, "Bao the Carter", "Greenriver Town", "Greenriver Town",
		"Mortal World", "Travelling Peddler", 50, "Independent")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	// Nothing stands at Greenriver yet, so the only road out is Riverguard.
	if _, err := r.npcTravel(conn, 1, 100); err != nil {
		t.Fatal(err)
	}
	if got := locationOf(t, path, "Bao the Carter"); got == "Skyroad Immortal City" {
		t.Fatalf("a carter walked into the Immortal World with no gate: %s", got)
	}

	// Torn by a cultivator at realm 8. The carter is a mortal at realm 0, and
	// a seam cut to an Ascension-realm measure is not a road for them however
	// long they stand under it.
	if _, err := conn.Execute(`INSERT INTO world_crossings(location_key,name,from_world,to_world,destination_location,min_realm_index,opened_realm_index,cost,opened_game_minute,created_at)
        VALUES('Greenriver Town','Lin Test''s Ascension Gate','Mortal World','Immortal World','Skyroad Immortal City',0,8,200,0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET current_location='Greenriver Town' WHERE npc_name='Bao the Carter'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := r.npcTravel(conn, 1, 200); err != nil {
		t.Fatal(err)
	}
	if got := locationOf(t, path, "Bao the Carter"); got == "Skyroad Immortal City" {
		t.Fatal("a mortal carter walked through an Ascension-realm seam")
	}

	// Raise the carter to within the seam's reach and they can follow.
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET current_location='Greenriver Town',realm_index=7 WHERE npc_name='Bao the Carter'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := r.npcTravel(conn, 1, 300); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if got := locationOf(t, path, "Bao the Carter"); got != "Skyroad Immortal City" {
		t.Fatalf("the carter did not step through the gate: %s", got)
	}
	if uses := simScalarInt(t, path, `SELECT npc_uses FROM world_crossings WHERE location_key='Greenriver Town'`); uses != 1 {
		t.Fatalf("the gate counted %d crossings", uses)
	}
	// The world can see it, and it is well under the Quest Forge's 80: the
	// crossing is the significant event, not each traveller after it.
	if significance := simScalarInt(t, path, `SELECT significance FROM world_history_events WHERE event_type='world_crossing_used'`); significance != game.DefaultNPCHistorySignificance || significance >= 80 {
		t.Fatalf("significance=%d", significance)
	}
}

func simScalarInt(t *testing.T, path, query string) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(query, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		t.Fatalf("no row for %s", query)
	}
	return i64(res.Rows[0][0])
}

// The reach, stated once and held here rather than inferred from a tick.
func TestOnlyCultivationNearTheSeamFitsThroughIt(t *testing.T) {
	gate := game.Crossing{Destination: "Skyroad Immortal City", MinRealmIndex: 4, OpenedRealmIndex: 8}
	for _, tc := range []struct {
		name  string
		realm int64
		reach int64
		want  bool
	}{
		{"the cultivator's own measure", 8, 2, true},
		{"two realms under, still within reach", 6, 2, true},
		{"two realms over, still within reach", 10, 2, true},
		{"three under is too far below the seam", 5, 2, false},
		{"three over is too far above it", 11, 2, false},
		{"and the road's own floor still refuses", 3, 9, false},
		{"a reach of nothing admits only the opener", 7, 0, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := game.NPCMayCross(gate, tc.realm, tc.reach); got != tc.want {
				t.Fatalf("realm %d, reach %d: %v", tc.realm, tc.reach, got)
			}
		})
	}
	// A row with no far side is not a road, whatever the realms say.
	if game.NPCMayCross(game.Crossing{OpenedRealmIndex: 8}, 8, 2) {
		t.Fatal("a crossing with no destination admitted somebody")
	}
}
