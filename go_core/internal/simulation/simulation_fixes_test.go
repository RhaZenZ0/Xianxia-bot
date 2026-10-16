package simulation

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// Six faults the simulation carried that each had a door left open
// (v1.34.0): the disappearance row that named the place, the sect hall whose
// upper-world people could never climb, the households seeded again by every
// bootstrap, a missing master's bond ended as death, and two name walks that
// did not ask the registry. The roster-turn pair is in roster_turn_test.go.

// A disappearance row is the one the narrator's RAG and the Quest Forge's quest
// story read, and the NPC stays where they went missing. Naming that place told
// every searcher where to stand; the death notice was held to this and the
// disappearance was not.
func TestTheDisappearanceNeverNamesWhereTheyAre(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path := romanceDB(t)
	r := romanceRunner()
	missingNPC(t, path, "Traveller Zhu", "Greenriver Town", "Riverguard City", 0, "alive")
	conn := livesConn(t, path)
	vanished, _, err := r.npcDisappearances(conn, 10000)
	if err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if vanished != 1 {
		t.Fatalf("the traveller away from home did not go missing: %d", vanished)
	}
	summary := romanceStr(t, path, `SELECT summary FROM world_history_events WHERE event_type='npc_missing'`)
	if !contains(summary, "Greenriver Town") {
		t.Fatalf("the notice does not name the home that was waiting: %q", summary)
	}
	if contains(summary, "Riverguard City") {
		t.Fatalf("the notice gives away %q, where they are: %q", "Riverguard City", summary)
	}
	if got := romanceStr(t, path, `SELECT location FROM world_history_events WHERE event_type='npc_missing'`); got != "Greenriver Town" {
		t.Fatalf("the row is filed under %q, not the home they left", got)
	}
}

// A sect hall's people stand at their world's floor plus an offset, and the
// catalogue does not carry their names, so their ceiling was measured from
// realm 0: an Immortal World disciple at realm 17 was above a ceiling of at
// most 12 on the day they were made, and said so for good.
func TestAnUpperWorldHallMemberCanStillClimb(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path := setupSimulationDB(t, npcMovementSchema+npcLivesSchema)
	r, err := NewRunner(path, bootstrapWorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	// A name whose own draw gives two or three realms of headroom: realm 17 is
	// below a ceiling measured from the Immortal floor of 16, and above one
	// measured from zero, which is the whole difference.
	name := ""
	for i := 0; i < 200 && name == ""; i++ {
		candidate := fmt.Sprintf("Hall Disciple %03d", i)
		if band := npcTalentBand(candidate, "Disciple"); band >= 2 && band <= 3 {
			name = candidate
		}
	}
	if name == "" {
		t.Fatal("no name drew a small band of talent")
	}
	floor := r.worldFloorRealm("Immortal World")
	if floor != 16 {
		t.Fatalf("the Immortal World begins at realm %d, the fixture assumes 16", floor)
	}
	addNPC(t, path, name, "Greenriver Town", "Greenriver Town", "Immortal World", "Disciple", 60, "Independent")
	addLife(t, path, name, map[string]any{"health": 100})
	conn := livesConn(t, path)
	mustExec(t, conn, `UPDATE npc_civilization_state SET phase=9,wealth=900,realm_index=17 WHERE npc_name='`+name+`'`)
	if _, err := r.npcBreakthroughs(conn, 100); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	res, err := conn.Execute(`SELECT realm_index,activity FROM npc_civilization_state WHERE npc_name=?`, []any{name})
	if err != nil {
		t.Fatal(err)
	}
	realm, activity := i64(res.Rows[0][0]), fmt.Sprint(res.Rows[0][1])
	if activity == "Has gone as far as their talent allows" || realm != 18 {
		t.Fatalf("an Immortal World disciple at realm 17 is at realm %d and %q", realm, activity)
	}
	// The ceiling itself: the world's floor plus the person's own band, and in
	// the Mortal World the floor is zero, so the band alone.
	band := npcTalentBand(name, "Disciple")
	if ceiling := r.npcTalentCeiling(name, "Disciple", "Immortal World"); ceiling != floor+band {
		t.Fatalf("an Immortal World disciple's ceiling is %d, want the world's floor %d plus their band %d", ceiling, floor, band)
	}
	if ceiling := r.npcTalentCeiling(name, "Disciple", "Mortal World"); ceiling != band {
		t.Fatalf("a Mortal World disciple's ceiling is %d, want their band %d alone", ceiling, band)
	}
}

// Bootstrap runs after every character creation, reincarnation and start, and
// the household seeding read "the singles" each time, so every call married a
// fresh share of whoever was still single and planted children for them.
func TestHouseholdsAreSeededOncePerWorld(t *testing.T) {
	// The seeding is keyed off hashes, not dice; the dice are lent because the
	// bootstrap's call graph reaches a draw and the assertion below is a tally.
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path := setupSimulationDB(t, seededWorldSchema)
	runner, err := NewRunner(path, bootstrapWorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	setSimulationGameMinute(t, path, 100)
	first, err := runner.Bootstrap(BootstrapRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if first.NPCHouseholdsSeeded == 0 {
		t.Fatal("the first bootstrap seeded no household; the fixture proves nothing")
	}
	count := func() (int64, int64) {
		return storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_life_state WHERE relationship_status='married'`)),
			storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_descendants`))
	}
	married, kids := count()
	for run := 2; run <= 4; run++ {
		again, err := runner.Bootstrap(BootstrapRequest{})
		if err != nil {
			t.Fatal(err)
		}
		if again.NPCHouseholdsSeeded != 0 || again.NPCChildrenSeeded != 0 {
			t.Fatalf("bootstrap %d seeded %d household(s) and %d child(ren) into a world that already had its past",
				run, again.NPCHouseholdsSeeded, again.NPCChildrenSeeded)
		}
		if m, k := count(); m != married || k != kids {
			t.Fatalf("bootstrap %d moved the world from %d married and %d children to %d and %d", run, married, kids, m, k)
		}
	}
}

// A world that predates the marker has had its households written already,
// perhaps several times. The first bootstrap after the upgrade must mark it and
// leave it alone, not seed it once more.
func TestAWorldWithHouseholdsAlreadyIsNotSeededAgain(t *testing.T) {
	path := setupSimulationDB(t, seededWorldSchema)
	runner, err := NewRunner(path, bootstrapWorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	setSimulationGameMinute(t, path, 100)
	if _, err := runner.Bootstrap(BootstrapRequest{}); err != nil {
		t.Fatal(err)
	}
	married := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_life_state WHERE relationship_status='married'`))
	conn := livesConn(t, path)
	mustExec(t, conn, `DELETE FROM world_state WHERE key='`+householdsSeededKey+`'`)
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	again, err := runner.Bootstrap(BootstrapRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if again.NPCHouseholdsSeeded != 0 {
		t.Fatalf("a world with %d married people was seeded %d more household(s) on the first bootstrap after the marker", married, again.NPCHouseholdsSeeded)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM world_state WHERE key=?`, householdsSeededKey)); got != 1 {
		t.Fatalf("the existing world was not marked: %d marker row(s)", got)
	}
}

// A missing master is not a dead one. The disappearance path never releases
// their disciples, and the bond sweep ended it as "the master is dead" for good
// - a rescue does not restore an ended bond.
func TestAMissingMastersBondSurvivesUntilTheyDie(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	addNPC(t, path, "Elder Gao", "Greenriver Town", "Lonely Rock", "Mortal World", "Elder", 60, "Azure Cloud Sect")
	addNPC(t, path, "Young Fan", "Greenriver Town", "Greenriver Town", "Mortal World", "Cultivator", 50, "Independent")
	conn := livesConn(t, path)
	mustExec(t, conn, `INSERT INTO npc_disciple_bonds(master_name,disciple_name,status,started_game_minute,updated_at)
        VALUES('Elder Gao','Young Fan','active',1,0)`)
	mustExec(t, conn, `UPDATE npc_civilization_state SET status='missing',missing_since_game_minute=40000 WHERE npc_name='Elder Gao'`)
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	bond := func() string {
		res, err := conn.Execute(`SELECT status||':'||reason FROM npc_disciple_bonds WHERE master_name='Elder Gao'`, nil)
		if err != nil || len(res.Rows) != 1 {
			t.Fatalf("bond read: %v %v", err, res)
		}
		return fmt.Sprint(res.Rows[0][0])
	}
	if _, err := r.npcDiscipleBonds(conn, 50000); err != nil {
		t.Fatal(err)
	}
	if got := bond(); got != "active:" {
		t.Fatalf("a missing master's bond was ended: %q", got)
	}
	mustExec(t, conn, `UPDATE npc_civilization_state SET status='dead' WHERE npc_name='Elder Gao'`)
	if _, err := r.npcDiscipleBonds(conn, 60000); err != nil {
		t.Fatal(err)
	}
	if got := bond(); got != "ended:the master is dead" {
		t.Fatalf("a dead master's bond is %q", got)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

// `npc_registry` is the catalogue of every person the world made for itself,
// and a name it holds is never taken. Both child-name walks skipped it, so a
// newborn who drew a registry-held name was accepted and, at maturation,
// marked `generated_as_npc` without ever becoming a person.
func TestAChildIsNeverNamedForSomebodyTheRegistryHolds(t *testing.T) {
	path := maturationDB(t)
	r := livesRunner()
	r.World.EventSites.NamePool = []string{"Hou Jin"}
	conn := livesConn(t, path)
	mustExec(t, conn, `INSERT INTO npc_registry(name,created_at,updated_at) VALUES('Chen Jin',0,0)`)
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if name, err := r.freeChildName(conn, "Chen Wei"); err != nil || name != "" {
		t.Fatalf("freeChildName named a newborn %q (err %v); the registry already holds that name", name, err)
	}
	if name, err := r.seededChildName(conn, "Chen Wei", 0); err != nil || name != "" {
		t.Fatalf("seededChildName named a child %q (err %v); the registry already holds that name", name, err)
	}
	// And the walk still names a child when nothing holds the name.
	if name, err := r.seededChildName(conn, "Lu Wei", 0); err != nil || !strings.HasSuffix(name, " Jin") {
		t.Fatalf("a free name was refused: %q (err %v)", name, err)
	}
}
