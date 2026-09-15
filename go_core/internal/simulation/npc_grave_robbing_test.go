package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// Somebody gets there first (v1.0.0-rc.24). The grace and the guard contain no
// die at all, so those are asserted exactly; the robbery itself is a roll and
// is only ever made certain by lending the dice, never by iterating until it
// happens.

// alwaysRob answers every roll with 0, which passes any `roll < chance` gate
// and picks the first of any list. It is the whole die this step uses.
func alwaysRob() func() { return gamerng.UseRoller(func(int) int { return 0 }) }

// neverRob answers every roll with the largest face, which fails every gate.
func neverRob() func() { return gamerng.UseRoller(func(n int) int { return n - 1 }) }

// What `addFinder` leaves in a pocket: npcFindsSchema's own column default,
// named here so the arithmetic below is about the grave and not the fixture.
const finderPurse = 20

func robbingDB(t *testing.T) string {
	t.Helper()
	path := findsDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	gravesTable(t, conn)
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func buryAt(t *testing.T, path, name, where, item string, stones, died int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_graves(
        npc_name,location,world_name,home_location,died_game_minute,days_missing,
        keepsake_item,keepsake_stones,created_at,updated_at)
        VALUES(?,?,'Mortal World','Greenriver Town',?,70,?,?,0,0)`,
		[]any{name, where, died, item, stones}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func robTick(t *testing.T, path string, r *Runner, gm int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	robbed, err := r.npcGraveRobbing(conn, gm)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return robbed
}

// The grace is the searcher's window, and it is the whole reason this step can
// exist without taking the quest away from the player who was sent on it.
func TestAFreshGraveIsTheSearchersAlone(t *testing.T) {
	defer alwaysRob()()
	path := robbingDB(t)
	r := findsRunner()
	addFinder(t, path, "Spade Shen", "Greenriver Town", "grave-robber", 0)
	died := int64(100000)
	buryAt(t, path, "Lost Lu", "Greenriver Town", "spirit_herb", 40, died)

	// One minute short of the grace, with the dice saying yes to everything.
	if got := robTick(t, path, r, died+graveRobGraceDays*minutesPerDay-1); got != 0 {
		t.Fatalf("a grave inside its grace was robbed anyway (%d)", got)
	}
	if got := simScalar(t, path, `SELECT claimed_game_minute FROM npc_graves WHERE npc_name='Lost Lu'`); got != nil {
		t.Fatalf("the grave was closed inside its grace: %v", got)
	}
	// One minute past it, and the same dice do.
	if got := robTick(t, path, r, died+graveRobGraceDays*minutesPerDay+1); got != 1 {
		t.Fatalf("a grave past its grace with a robber standing on it was not robbed (%d)", got)
	}
}

func TestARobbedGraveReadsAsEmptiedAndPaysTheRobber(t *testing.T) {
	defer alwaysRob()()
	path := robbingDB(t)
	r := findsRunner()
	addFinder(t, path, "Spade Shen", "Greenriver Town", "grave-robber", 0)
	died := int64(100000)
	buryAt(t, path, "Lost Lu", "Greenriver Town", "spirit_herb", 40, died)
	gm := died + (graveRobGraceDays+1)*minutesPerDay
	if got := robTick(t, path, r, gm); got != 1 {
		t.Fatalf("nothing was robbed: %d", got)
	}

	// Emptied, and by nobody a player could be. `claimed_by_user_id` staying
	// NULL is what says no cultivator reached this one; `claimed_game_minute`
	// is what says it is empty, and is the column both this and a player's
	// claim are guarded on.
	if got := storage.ParseInt(simScalar(t, path,
		`SELECT claimed_game_minute FROM npc_graves WHERE npc_name='Lost Lu'`)); got != gm {
		t.Fatalf("the grave was not closed at the minute it was robbed: %v", got)
	}
	if got := simScalar(t, path, `SELECT claimed_by_user_id FROM npc_graves WHERE npc_name='Lost Lu'`); got != nil {
		t.Fatalf("a robbery credited a player with the find: %v", got)
	}
	// The purse left the world when the grave was dug; this is it coming back.
	if got := storage.ParseInt(simScalar(t, path,
		`SELECT wealth FROM npc_civilization_state WHERE npc_name='Spade Shen'`)); got != finderPurse+40 {
		t.Fatalf("the robber's purse is %d, expected %d once the grave's 40 is in it", got, finderPurse+40)
	}
	// The one thing the world is left with: a lot under their own name.
	if got := storage.ParseInt(simScalar(t, path,
		`SELECT COUNT(*) FROM auctions WHERE seller_npc_name='Spade Shen' AND item_id='spirit_herb'`)); got != 1 {
		t.Fatalf("the keepsake never reached a floor under the robber's name (%d lots)", got)
	}
}

// The deed is hidden and the goods are not. Nobody stood in the wilderness and
// watched, so the history row must never reach narrator RAG - what a player
// gets to work from is the lot, which carries the name.
func TestNobodySawItDone(t *testing.T) {
	defer alwaysRob()()
	path := robbingDB(t)
	r := findsRunner()
	addFinder(t, path, "Spade Shen", "Greenriver Town", "grave-robber", 0)
	died := int64(100000)
	buryAt(t, path, "Lost Lu", "Greenriver Town", "spirit_herb", 40, died)
	robTick(t, path, r, died+(graveRobGraceDays+1)*minutesPerDay)

	if got := storage.ParseInt(simScalar(t, path,
		`SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_grave_robbery'`)); got != 1 {
		t.Fatalf("the chronicle has %d row(s) for a robbery", got)
	}
	if got := fmt.Sprint(simScalar(t, path,
		`SELECT visibility FROM world_history_events WHERE event_type='npc_grave_robbery'`)); got != "hidden" {
		t.Fatalf("a robbery nobody witnessed is %q; the world must not know who did it", got)
	}
	// And it must not be able to become a quest: there is no one to report it.
	if got := storage.ParseInt(simScalar(t, path,
		`SELECT significance FROM world_history_events WHERE event_type='npc_grave_robbery'`)); got >= 80 {
		t.Fatalf("an unwitnessed robbery at significance %d can reach the Quest Forge", got)
	}
	// The summary must not name the robber - a hidden row is filtered before
	// scoring, but the summary is the thing that would leak if it ever were
	// not, and this is the ladder npc_deeds.go already keeps.
	summary := fmt.Sprint(simScalar(t, path,
		`SELECT summary FROM world_history_events WHERE event_type='npc_grave_robbery'`))
	if contains(summary, "Spade Shen") {
		t.Fatalf("the notice names the culprit of a crime nobody saw: %q", summary)
	}
}

// A grave a player already emptied is not a second payday, and the guard that
// stops it is the one the erasure fix put on `claimed_game_minute`.
func TestARobberCannotEmptyAGraveTwice(t *testing.T) {
	defer alwaysRob()()
	path := robbingDB(t)
	r := findsRunner()
	addFinder(t, path, "Spade Shen", "Greenriver Town", "grave-robber", 0)
	died := int64(100000)
	buryAt(t, path, "Lost Lu", "Greenriver Town", "spirit_herb", 40, died)
	gm := died + (graveRobGraceDays+1)*minutesPerDay
	if got := robTick(t, path, r, gm); got != 1 {
		t.Fatalf("setup: nothing was robbed (%d)", got)
	}
	if got := robTick(t, path, r, gm+minutesPerDay); got != 0 {
		t.Fatalf("the same grave was robbed %d more time(s)", got)
	}
	if got := storage.ParseInt(simScalar(t, path,
		`SELECT wealth FROM npc_civilization_state WHERE npc_name='Spade Shen'`)); got != finderPurse+40 {
		t.Fatalf("the purse was taken more than once: %d", got)
	}
	if got := storage.ParseInt(simScalar(t, path,
		`SELECT COUNT(*) FROM auctions WHERE seller_npc_name='Spade Shen'`)); got != 1 {
		t.Fatalf("the keepsake was sold %d times", got)
	}
}

// Reach, not teleportation: the same rule courtship uses. A digger a whole
// world away cannot rob a grave they could never walk to.
func TestOnlySomebodyWhoCouldWalkThereRobsIt(t *testing.T) {
	defer alwaysRob()()
	path := robbingDB(t)
	r := findsRunner()
	addFinder(t, path, "Spade Shen", "Golden Pavilion", "grave-robber", 0)
	died := int64(100000)
	// Greenriver Alley is inside Greenriver Town; the Golden Pavilion is not
	// on any road to it in this fixture.
	buryAt(t, path, "Lost Lu", "Greenriver Alley", "spirit_herb", 40, died)
	if got := robTick(t, path, r, died+(graveRobGraceDays+1)*minutesPerDay); got != 0 {
		t.Fatalf("a grave was robbed by somebody who could not reach it (%d)", got)
	}
}

// A gate guard does not open a stranger's grave, however long it has stood.
func TestAnHonestTradeLeavesTheGraveAlone(t *testing.T) {
	defer alwaysRob()()
	path := robbingDB(t)
	r := findsRunner()
	// Rooted, incurious and comfortable: not the trade, and not desperate.
	addFinder(t, path, "Gate Guard Gao", "Greenriver Town", "gate guard", 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`UPDATE npc_civilization_state SET ambition=10,wealth=500 WHERE npc_name='Gate Guard Gao'`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	died := int64(100000)
	buryAt(t, path, "Lost Lu", "Greenriver Town", "spirit_herb", 40, died)
	if got := robTick(t, path, r, died+(graveRobGraceDays+10)*minutesPerDay); got != 0 {
		t.Fatalf("a gate guard robbed a grave (%d)", got)
	}
}

// The cap is per tick, for the reason travel and crime are capped.
func TestTheWorldDoesNotTurnOverEveryGraveInANight(t *testing.T) {
	defer alwaysRob()()
	path := robbingDB(t)
	r := findsRunner()
	addFinder(t, path, "Spade Shen", "Greenriver Town", "grave-robber", 0)
	died := int64(100000)
	for i := 0; i < 6; i++ {
		buryAt(t, path, fmt.Sprintf("Lost %02d", i), "Greenriver Town", "spirit_herb", 10, died)
	}
	got := robTick(t, path, r, died+(graveRobGraceDays+1)*minutesPerDay)
	if got != graveRobCap {
		t.Fatalf("one tick robbed %d graves against a cap of %d", got, graveRobCap)
	}
}

// And with the dice against it, nothing happens at all - so the step is a
// chance and not a certainty dressed as one.
func TestARobberyIsStillARoll(t *testing.T) {
	defer neverRob()()
	path := robbingDB(t)
	r := findsRunner()
	addFinder(t, path, "Spade Shen", "Greenriver Town", "grave-robber", 0)
	died := int64(100000)
	buryAt(t, path, "Lost Lu", "Greenriver Town", "spirit_herb", 40, died)
	for tick := 0; tick < 20; tick++ {
		if got := robTick(t, path, r, died+int64(graveRobGraceDays+1+tick*7)*minutesPerDay); got != 0 {
			t.Fatalf("a roll that fails every gate robbed a grave (%d)", got)
		}
	}
}
