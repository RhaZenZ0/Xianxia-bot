package game

import (
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The two edges where the Python twin (`current_npc_location`) disagreed with
// this resolver (v1.12.3). The engine is authoritative, so these pin what it
// answers and `tests/python/contracts/test_who_is_here.py` holds the twin to
// the same two answers:
//
//   - at home with no schedule entry for the period, the simulation's row is
//     the answer, not the content's `location` (they differ after an edit to
//     the file moves somebody);
//   - a catalogue NPC with no simulation row stands where content puts them:
//     their schedule this period, else the file's location.
func edgeCatalog() worlddata.Catalog {
	return worlddata.Catalog{NPCs: map[string]worlddata.NPCDefinition{
		"Edge Walker": {Location: "Content Town", Schedule: map[string]string{"Morning": "Market Town"}},
	}}
}

func TestAtHomeWithNoScheduleEntryTheSimulationRowIsTheAnswer(t *testing.T) {
	path := crossingDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT NOT NULL,current_location TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'alive')`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,status) VALUES('Edge Walker','Sim Town','Sim Town','alive')`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// Morning has an entry: the schedule holds while they are home.
	got, err := npcWhereaboutsTx(conn, edgeCatalog(), "Edge Walker", 9*60, 0)
	if err != nil || got.Location != "Market Town" {
		t.Fatalf("a scheduled morning at home: %+v err=%v", got, err)
	}
	// Afternoon has none: the row answers, and the content's own location is
	// not a whereabouts.
	got, err = npcWhereaboutsTx(conn, edgeCatalog(), "Edge Walker", 13*60, 0)
	if err != nil || got.Location != "Sim Town" {
		t.Fatalf("an unscheduled afternoon at home answered %q, want the simulation's Sim Town (err=%v)", got.Location, err)
	}
}

func TestACatalogueNPCWithNoRowStandsWhereContentPutsThem(t *testing.T) {
	path := crossingDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT NOT NULL,current_location TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'alive')`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	got, err := npcWhereaboutsTx(conn, edgeCatalog(), "Edge Walker", 9*60, 0)
	if err != nil || !got.Known || got.Location != "Market Town" {
		t.Fatalf("a scheduled morning with no row: %+v err=%v", got, err)
	}
	got, err = npcWhereaboutsTx(conn, edgeCatalog(), "Edge Walker", 13*60, 0)
	if err != nil || !got.Known || got.Location != "Content Town" {
		t.Fatalf("an unscheduled afternoon with no row: %+v err=%v", got, err)
	}
}
