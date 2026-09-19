package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// Disappearances (v1.0.0-rc.24, schema 47). The hardship half contains no die
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
	setSimulationGameMinute(t, path, past)
	if _, err := runner.Force(ForceRequest{System: "npc_life", Steps: 1}); err != nil {
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

const graveTestSchema = `
CREATE TABLE IF NOT EXISTS npc_graves(npc_name TEXT PRIMARY KEY,location TEXT NOT NULL,world_name TEXT NOT NULL DEFAULT '',home_location TEXT NOT NULL DEFAULT '',died_game_minute INTEGER NOT NULL DEFAULT 0,days_missing INTEGER NOT NULL DEFAULT 0,keepsake_item TEXT NOT NULL DEFAULT '',keepsake_stones INTEGER NOT NULL DEFAULT 0,claimed_by_user_id INTEGER,claimed_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL);
`

func gravesTable(t *testing.T, conn *storage.Conn) {
	t.Helper()
	if err := conn.ExecScript(graveTestSchema); err != nil {
		t.Fatal(err)
	}
}

func TestTheKeepsakeIsReadOffTheTradeTheyPractised(t *testing.T) {
	for profession, want := range map[string]string{
		"herbalist":         "spirit_herb",
		"Greenriver smith":  "spirit_iron",
		"talisman scribe":   "talisman_paper",
		"formation adept":   "array_disk_blank",
		"beast hunter":      "beast_core",
		"Apothecary keeper": "spirit_herb",
		"gate guard":        "talisman_paper",
	} {
		if got := missingKeepsake(profession); got != want {
			t.Fatalf("a %q was buried with %q, expected %q", profession, got, want)
		}
	}
}

func TestDyingOutThereLeavesAGraveHoldingTheirOwnPurse(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	gone := int64(100000)
	missingNPC(t, path, "Lost Lu", "Greenriver Town", "Lonely Rock", gone, "missing")
	conn := livesConn(t, path)
	gravesTable(t, conn)
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET profession='herbalist',wealth=42 WHERE npc_name='Lost Lu'`, nil); err != nil {
		t.Fatal(err)
	}
	past := gone + (missingGraceDays+1)*minutesPerDay
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
		t.Fatalf("nobody died out there: %d", died)
	}
	if got := romanceStr(t, path, `SELECT location FROM npc_graves WHERE npc_name='Lost Lu'`); got != "Lonely Rock" {
		t.Fatalf("the grave is at %q, not where they actually stopped", got)
	}
	if got := romanceStr(t, path, `SELECT home_location FROM npc_graves WHERE npc_name='Lost Lu'`); got != "Greenriver Town" {
		t.Fatalf("the grave does not say where to carry the answer: %q", got)
	}
	if got := romanceStr(t, path, `SELECT keepsake_item FROM npc_graves WHERE npc_name='Lost Lu'`); got != "spirit_herb" {
		t.Fatalf("a herbalist was buried with %q", got)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT keepsake_stones FROM npc_graves WHERE npc_name='Lost Lu'`)); got != 42 {
		t.Fatalf("the grave holds %d stones, they were carrying 42", got)
	}
	// The purse leaves the NPC as it enters the grave, or the world's total
	// grows by the value of everybody who ever got lost.
	if got := storage.ParseInt(simScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Lost Lu'`)); got != 0 {
		t.Fatalf("the dead are still carrying %d stones the grave also has", got)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_graves WHERE claimed_by_user_id IS NOT NULL`)); got != 0 {
		t.Fatal("a fresh grave was already marked visited")
	}
}

func TestTheTownGivesUpBeforeTheWildernessDoes(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	gone := int64(100000)
	missingNPC(t, path, "Lost Lu", "Greenriver Town", "Lonely Rock", gone, "missing")
	missingNPC(t, path, "Waiting Wen", "Greenriver Town", "Greenriver Town", 0, "alive")
	conn := livesConn(t, path)
	if _, err := conn.Execute(`UPDATE npc_life_state SET relationship_status='married',spouse_name='Waiting Wen' WHERE npc_name='Lost Lu'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE npc_life_state SET relationship_status='married',spouse_name='Lost Lu' WHERE npc_name='Waiting Wen'`, nil); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)

	// Inside the wait, nothing changes: the town is still setting a place.
	if err := r.npcPresumedDead(conn, gone+(presumedDeadDays-1)*minutesPerDay); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Waiting Wen'`); got != "married" {
		t.Fatalf("the spouse was widowed early: %q", got)
	}

	// Past it, the marriage is dissolved from both sides.
	if err := r.npcPresumedDead(conn, gone+(presumedDeadDays+1)*minutesPerDay); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Waiting Wen'`); got != "widowed" {
		t.Fatalf("the one at home is %q and may never marry again", got)
	}
	if got := romanceStr(t, path, `SELECT spouse_name FROM npc_life_state WHERE npc_name='Waiting Wen'`); got != "" {
		t.Fatalf("the widow still carries a spouse: %q", got)
	}
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Lost Lu'`); got != "single" {
		t.Fatalf("the one still out there is %q; found later, they would be married to somebody who buried them", got)
	}
	// And they are still alive, still missing, and still exactly where they
	// are. That is the whole of the tragedy and none of the bug.
	if got := romanceStr(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name='Lost Lu'`); got != "missing" {
		t.Fatalf("being given up for dead killed them: status %q", got)
	}
	if got := romanceStr(t, path, `SELECT current_location FROM npc_civilization_state WHERE npc_name='Lost Lu'`); got != "Lonely Rock" {
		t.Fatalf("they moved when the town stopped looking: %q", got)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_presumed_dead'`)); got != 1 {
		t.Fatalf("nobody recorded that the town had stopped waiting: %d", got)
	}
}

func TestAWidowIsNotCourtedTheWeekAfterTheFuneral(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	romanceNPC(t, path, "Widow Wu", "Riverguard City", 0, 40, "widowed")
	romanceNPC(t, path, "Suitor Su", "Riverguard City", 0, 42, "single")
	conn := livesConn(t, path)
	widowed := int64(500000)
	if _, err := conn.Execute(`UPDATE npc_life_state SET last_social_game_minute=? WHERE npc_name='Widow Wu'`, []any{widowed}); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)

	// Inside mourning the widow is not a candidate at all, so this holds on
	// every run rather than most of them - no die is reached.
	for tick := 0; tick < 30; tick++ {
		if _, err := r.beginCourtships(conn, 1, widowed+(mourningDays-1)*minutesPerDay, 1); err != nil {
			t.Fatal(err)
		}
	}
	romanceCommit(t, conn)
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM npc_social_relations WHERE relation_type='courtship'`)); got != 0 {
		t.Fatalf("a widow was courted %d time(s) inside the mourning period", got)
	}
	if got := romanceStr(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Widow Wu'`); got != "widowed" {
		t.Fatalf("the widow is %q before the mourning period is out", got)
	}
}
