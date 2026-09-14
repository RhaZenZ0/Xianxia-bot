package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// Disappearances (v1.0.0-rc.23, schema 47). The hardship half contains no die
// at all, so everything about the grace, the decline and the death is asserted
// exactly; the vanishing itself is a roll and is only ever bounded.

func missingNPC(t *testing.T, path, name, home, where string, missingSince int64, status string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state(
        npc_name,home_location,current_location,world_name,profession,faction,
        wealth,influence,ambition,realm_index,phase,status,activity,
        missing_since_game_minute,last_game_minute,updated_at)
        VALUES(?,?,?,'Mortal World','farmer','Independent',20,10,50,0,1,?,'',?,0,0)`,
		[]any{name, home, where, status, missingSince}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO npc_life_state(
        npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,
        injury,injury_severity,sect_rank,career_progress,relationship_status,spouse_name,
        children_count,last_social_game_minute,last_cultivation_game_minute,updated_at)
        VALUES(?,0,30,75,100,'',0,'Independent Cultivator',0,'single','',0,0,0,0)`,
		[]any{name}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestOnlyThoseAlreadyAwayCanGoMissing(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	// At home, so not missing however the die falls.
	missingNPC(t, path, "Homebody He", "Greenriver Town", "Greenriver Town", 0, "alive")
	conn := livesConn(t, path)
	for tick := 0; tick < 40; tick++ {
		if _, _, err := r.npcDisappearances(conn, int64(1000+tick*10080)); err != nil {
			t.Fatal(err)
		}
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name='Homebody He'`); got != "alive" {
		t.Fatalf("somebody standing in their own town went missing from it: %q", got)
	}
}

func TestADisappearanceIsSignificantEnoughToReachTheQuestForge(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	for i := 0; i < 30; i++ {
		missingNPC(t, path, fmt.Sprintf("Traveller %02d", i), "Greenriver Town", "Riverguard City", 0, "alive")
	}
	conn := livesConn(t, path)
	vanished := int64(0)
	for tick := 0; tick < 25; tick++ {
		got, _, err := r.npcDisappearances(conn, int64(1000+tick*10080))
		if err != nil {
			t.Fatal(err)
		}
		vanished += got
		if got > missingCap {
			t.Fatalf("one tick lost %d people against a cap of %d", got, missingCap)
		}
	}
	romanceCommit(t, conn)
	rows := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_missing'`))
	if rows != vanished {
		t.Fatalf("%d disappearances, %d recorded", vanished, rows)
	}
	// Whatever the die said, every row it wrote has to clear the Forge's
	// default bar of 80 - which nothing else in this package has ever done.
	if rows > 0 {
		below := storage.ParseInt(simScalar(t,
			path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_missing' AND significance < 80`))
		if below != 0 {
			t.Fatalf("%d disappearance(s) were recorded below the Quest Forge threshold and can never become a quest", below)
		}
		public := storage.ParseInt(simScalar(t,
			path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_missing' AND visibility<>'public'`))
		if public != 0 {
			t.Fatal("a disappearance the town cannot hear about is not a disappearance")
		}
	}
	// A missing person stops being available to every batch that reads
	// `status='alive'`, which is the whole point of using the column.
	if got := storage.ParseInt(simScalar(t, path,
		`SELECT COUNT(*) FROM npc_civilization_state WHERE status='missing' AND missing_since_game_minute=0`)); got != 0 {
		t.Fatalf("%d missing person(s) have no minute they went missing in", got)
	}
}

func TestTheSurroundingsKeepThemAliveAndThenDoNot(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	gone := int64(100000)
	missingNPC(t, path, "Lost Lu", "Greenriver Town", "Lonely Rock", gone, "missing")
	conn := livesConn(t, path)

	// Inside the grace, the place provides and nothing costs them anything.
	withinGrace := gone + (missingGraceDays-1)*minutesPerDay
	for tick := 0; tick < 5; tick++ {
		if _, err := r.npcMissingHardship(conn, withinGrace); err != nil {
			t.Fatal(err)
		}
	}
	romanceCommit(t, conn)
	if got := storage.ParseInt(simScalar(t, path, `SELECT health FROM npc_life_state WHERE npc_name='Lost Lu'`)); got != 100 {
		t.Fatalf("the grace period cost them %d health; it should cost nothing", 100-got)
	}

	// Past it, it does not - and this is arithmetic, not a roll.
	past := gone + (missingGraceDays+1)*minutesPerDay
	if _, err := r.npcMissingHardship(conn, past); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got := storage.ParseInt(simScalar(t, path, `SELECT health FROM npc_life_state WHERE npc_name='Lost Lu'`)); got != 100-missingHealthDrain {
		t.Fatalf("one tick past the grace left them at %d, expected %d", got, 100-missingHealthDrain)
	}

	// Run it out. Nobody came.
	died := int64(0)
	for tick := 0; tick < 40 && died == 0; tick++ {
		got, err := r.npcMissingHardship(conn, past)
		if err != nil {
			t.Fatal(err)
		}
		died += got
	}
	romanceCommit(t, conn)
	if died != 1 {
		t.Fatalf("the supplies never ran out: %d died", died)
	}
	if got := romanceStr(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name='Lost Lu'`); got != "dead" {
		t.Fatalf("status is %q", got)
	}
	if got := romanceStr(t, path, `SELECT cause_of_death FROM npc_life_state WHERE npc_name='Lost Lu'`); got != missingDeathCause {
		t.Fatalf("cause of death is %q", got)
	}
	if got := simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_missing_death'`); storage.ParseInt(got) != 1 {
		t.Fatalf("nobody recorded that they never came back: %v", got)
	}
	// The town learns they are gone; it does not learn where they were.
	summary := romanceStr(t, path, `SELECT summary FROM world_history_events WHERE event_type='npc_missing_death'`)
	if want := "Greenriver Town"; !contains(summary, want) {
		t.Fatalf("the notice does not name the home that was waiting: %q", summary)
	}
	if contains(summary, "Lonely Rock") {
		t.Fatalf("the notice gives away where nobody found them: %q", summary)
	}
}

func TestTheMissingAreNotHealedBackUpEveryTick(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	gone := int64(100000)
	missingNPC(t, path, "Lost Lu", "Greenriver Town", "Lonely Rock", gone, "missing")
	conn := livesConn(t, path)
	past := gone + (missingGraceDays+1)*minutesPerDay
	if _, err := r.npcMissingHardship(conn, past); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	hurt := storage.ParseInt(simScalar(t, path, `SELECT health FROM npc_life_state WHERE npc_name='Lost Lu'`))

	// npcLife's own bulk heal was `WHERE health>0` with no join to status, so
	// it would have put this straight back every tick and made being missing
	// survivable forever. Run the real batch, not the step.
	if err := conn.ExecScript(`CREATE TABLE IF NOT EXISTS world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
INSERT OR REPLACE INTO world_simulation_state VALUES('npc_life',0,10080,0,0);`); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	runner, err := NewRunner(path, "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := runner.Force(ForceRequest{System: "npc_life", Steps: 1, GameMinute: past}); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT health FROM npc_life_state WHERE npc_name='Lost Lu'`)); got > hurt {
		t.Fatalf("the tick healed a missing person from %d back to %d", hurt, got)
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (func() bool {
		for i := 0; i+len(needle) <= len(haystack); i++ {
			if haystack[i:i+len(needle)] == needle {
				return true
			}
		}
		return false
	})()
}
