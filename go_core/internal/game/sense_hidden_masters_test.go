package game

import (
	"sort"
	"testing"

	"xianxia/core/internal/storage"
)

// Which hidden masters stand where the sensor stands is the engine's to say
// (v1.36.0). The bot walked the catalogue's hidden masters through its own
// whereabouts reader and sent the names on the area sweep, so a request naming
// any hidden master got a reading of them wherever they really were; the
// engine asks npcWhereaboutsTx now, and a caller sending the list is refused
// (TestAWaitIsNotTheCallersToSend). The reader is the challenge's and the
// recommender's, so a master who is dead or elsewhere is not here.
func TestTheHiddenMastersHereAreTheEnginesToName(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	sectDoorTables(t, path)
	installChallengeWorld(t, path)
	catalog := mustLoadCatalog(t, batch4WorldPath(t))
	// Two hidden masters who walk no circuit: a circuit walker stands where
	// the month's stop puts them whatever a simulation row says (the reader's
	// own rule), so the rows below could not place one.
	names := []string{}
	for name, npc := range catalog.NPCs {
		if npc.HiddenMaster != nil && len(npc.Circuit) == 0 {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	if len(names) < 2 {
		t.Fatalf("the content carries %d hidden masters off any circuit; this test needs two", len(names))
	}
	here, away := names[0], names[1]
	for _, name := range []string{here, away} {
		batch4Exec(t, path, `DELETE FROM npc_civilization_state WHERE npc_name=?`, name)
	}
	// Both stand away from home: at home the reader lets the daily schedule
	// speak, which is its rule and not this test's subject.
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,realm_index,phase,status,updated_at) VALUES(?,?,?,'Mortal World','hermit','',8,5,'alive',0)`, here, "Moonfen Marsh", "Greenriver Town")
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,realm_index,phase,status,updated_at) VALUES(?,?,?,'Mortal World','hermit','',8,5,'alive',0)`, away, "Moonfen Marsh", "Riverguard City")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	present, err := hiddenMastersAtTx(conn, catalog, "Greenriver Town", 900)
	if err != nil {
		t.Fatal(err)
	}
	has := func(list []string, name string) bool {
		for _, n := range list {
			if n == name {
				return true
			}
		}
		return false
	}
	if !has(present, here) || has(present, away) {
		t.Fatalf("the hidden masters at Greenriver Town are %v; %s stands there and %s stands in Riverguard City", present, here, away)
	}
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead' WHERE npc_name=?`, []any{here}); err != nil {
		t.Fatal(err)
	}
	present, err = hiddenMastersAtTx(conn, catalog, "Greenriver Town", 900)
	if err != nil {
		t.Fatal(err)
	}
	// The reader answers no place for the dead, so this holds with or without
	// the helper's own Dead guard; the guard is belt to that brace.
	if has(present, here) {
		t.Fatalf("a dead hidden master is still sensed at Greenriver Town: %v", present)
	}
}
