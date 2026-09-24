package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The world's people do things now (v1.0.0-rc.22).
//
// The rolls are real, so these pin the rules that hold whatever the dice say -
// money is conserved, nobody robs somebody poorer, a grudge needs a witness,
// a body is always found - and reach for a bounded loop only where the point
// is that the thing happens at all. At a 16-20% per-NPC chance, three hundred
// ticks not producing one is a probability with twenty zeros in front of it.

const npcDeedsSchema = npcFindsSchema + `
CREATE TABLE npc_life_state(
    npc_name TEXT PRIMARY KEY, birth_game_minute INTEGER NOT NULL DEFAULT 0,
    age_at_creation_years INTEGER NOT NULL DEFAULT 18, natural_lifespan_years INTEGER NOT NULL DEFAULT 75,
    health INTEGER NOT NULL DEFAULT 100, injury TEXT NOT NULL DEFAULT '', injury_severity INTEGER NOT NULL DEFAULT 0,
    sect_rank TEXT NOT NULL DEFAULT 'Independent Cultivator', career_progress INTEGER NOT NULL DEFAULT 0,
    relationship_status TEXT NOT NULL DEFAULT 'single', spouse_name TEXT NOT NULL DEFAULT '',
    children_count INTEGER NOT NULL DEFAULT 0, last_social_game_minute INTEGER NOT NULL DEFAULT 0,
    last_cultivation_game_minute INTEGER NOT NULL DEFAULT 0, death_game_minute INTEGER,
    cause_of_death TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE npc_social_relations(
    npc_a TEXT NOT NULL, npc_b TEXT NOT NULL, affinity INTEGER NOT NULL DEFAULT 0,
    trust INTEGER NOT NULL DEFAULT 0, grudge INTEGER NOT NULL DEFAULT 0,
    relation_type TEXT NOT NULL DEFAULT 'acquaintance', status TEXT NOT NULL DEFAULT 'active',
    started_game_minute INTEGER NOT NULL DEFAULT 0, last_interaction_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(npc_a,npc_b));
`

func deedsRunner() *Runner {
	r := findsRunner()
	// What a hunt pays out, so a successful one has somewhere to put it.
	r.World.Items["beast_core"] = worlddata.Item{Name: "Beast Core", AuctionInterest: "special", BasePrice: 120}
	r.World.Items["spirit_iron"] = worlddata.Item{Name: "Spirit Iron", AuctionInterest: "special", BasePrice: 60}
	r.World.Items["spirit_herb"] = worlddata.Item{Name: "Spirit Herb", AuctionInterest: "special", BasePrice: 40}
	return r
}

func deedsDB(t *testing.T) string {
	t.Helper()
	return setupSimulationDB(t, npcDeedsSchema)
}

func addPerson(t *testing.T, path, name, where, profession string, wealth, ambition, realmIndex int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state
        (npc_name,home_location,current_location,world_name,profession,wealth,ambition,realm_index,phase,status,updated_at)
        VALUES(?,?,?, 'Mortal World',?,?,?,?,1,'alive',0)`,
		[]any{name, where, where, profession, wealth, ambition, realmIndex}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO npc_life_state(npc_name,updated_at) VALUES(?,0)`, []any{name}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func deedText(t *testing.T, path, sql string, args ...any) string {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return ""
	}
	return fmt.Sprint(res.Rows[0][0])
}

func deedScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return i64(res.Rows[0][0])
}

// runCrimes drives the criminal half for `ticks` world days, committing each.
//
// `from` is the world minute the first tick happens at and every tick after it
// advances. That is not decoration: a deed's source_key carries the minute, so
// a harness that ran every tick at the same minute would have every row after
// the first dropped by ON CONFLICT DO NOTHING while its wealth transfer
// happened anyway - and would then "prove" things about a history that was
// mostly missing. Production cannot hit that (each NPC acts at most once per
// call, and the world clock moves between calls), but a test loop can.
func runCrimesFrom(t *testing.T, path string, r *Runner, ticks int, from int64) (int64, int64, int64) {
	t.Helper()
	total, seen, fatal := int64(0), int64(0), int64(0)
	for i := 0; i < ticks; i++ {
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		c, w, f, err := r.npcCrimes(conn, from+int64(i))
		if err != nil {
			conn.Close()
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			conn.Close()
			t.Fatal(err)
		}
		conn.Close()
		total, seen, fatal = total+c, seen+w, fatal+f
	}
	return total, seen, fatal
}

func runCrimes(t *testing.T, path string, r *Runner, ticks int) (int64, int64, int64) {
	t.Helper()
	return runCrimesFrom(t, path, r, ticks, 1000)
}

func runHunts(t *testing.T, path string, r *Runner, ticks int) (int64, int64, int64) {
	t.Helper()
	total, took, died := int64(0), int64(0), int64(0)
	for i := 0; i < ticks; i++ {
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		h, k, d, err := r.npcBeastHunts(conn, int64(2000+i))
		if err != nil {
			conn.Close()
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			conn.Close()
			t.Fatal(err)
		}
		conn.Close()
		total, took, died = total+h, took+k, died+d
	}
	return total, took, died
}

// topUp is the victim earning a living between robberies.
func topUp(t *testing.T, path, name string, wealth int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET wealth=? WHERE npc_name=? AND status='alive'`,
		[]any{wealth, name}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestACriminalTradeRobsAndTheMoneyActuallyMoves(t *testing.T) {
	path := deedsDB(t)
	r := deedsRunner()
	addPerson(t, path, "Bo the Knife", "Greenriver Town", "bandit", 5, 50, 1)
	addPerson(t, path, "Merchant Yun", "Greenriver Town", "merchant", 400, 40, 1)
	before := deedScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Bo the Knife'`) +
		deedScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Merchant Yun'`)

	// Lent dice: a criminal trade robs on 20 of 100, so sixty days missed it
	// about one run in 650,000. What the test is about is that the money
	// moves and the deed is recorded, neither of which is a matter of chance.
	defer gamerng.UseRoller(func(int) int { return 0 })()
	committed, _, _ := runCrimes(t, path, r, 60)
	if committed == 0 {
		t.Fatal("sixty days and a bandit never once robbed the rich merchant standing next to him")
	}
	// The world does not mint wealth to do it. (Smuggling is the one path
	// that pays from outside, so this world has no black market post.)
	after := deedScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Bo the Knife'`) +
		deedScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Merchant Yun'`)
	if after != before {
		t.Fatalf("wealth was created or destroyed: %d before, %d after", before, after)
	}
	if got := deedScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE actor_name='Bo the Knife'`); got == 0 {
		t.Fatal("a crime nobody recorded is a crime that did not happen")
	}
}

func TestNobodyRobsSomeoneWithLessThanTheyHave(t *testing.T) {
	path := deedsDB(t)
	r := deedsRunner()
	// The only bandit in town is also the richest person in it.
	addPerson(t, path, "Bo the Knife", "Greenriver Town", "bandit", 500, 90, 1)
	addPerson(t, path, "Poor Hu", "Greenriver Town", "porter", 3, 40, 0)
	if committed, _, _ := runCrimes(t, path, r, 120); committed != 0 {
		t.Fatalf("there was nothing worth taking, and %d crime(s) happened anyway", committed)
	}
	if got := deedScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Poor Hu'`); got != 3 {
		t.Fatalf("the porter was robbed of what he did not have: %d", got)
	}
}

func TestAnOrdinaryTradeNeedsBothAmbitionAndNeed(t *testing.T) {
	path := deedsDB(t)
	r := deedsRunner()
	// Ambitious but comfortable, and poor but content: neither is desperate.
	addPerson(t, path, "Steward Qiao", "Greenriver Town", "steward", 200, 95, 2)
	addPerson(t, path, "Quiet Shen", "Greenriver Town", "farmer", 4, 20, 0)
	addPerson(t, path, "Merchant Yun", "Greenriver Town", "merchant", 800, 40, 1)
	if committed, _, _ := runCrimes(t, path, r, 120); committed != 0 {
		t.Fatalf("an honest town committed %d crime(s)", committed)
	}
}

func TestAGrudgeNeedsAWitnessAndAKillingIsAlwaysFound(t *testing.T) {
	path := deedsDB(t)
	r := deedsRunner()
	// A quiet waystation: few enough eyes that most crimes here go unseen.
	// Three merchants rather than one because a robbery occasionally kills,
	// and a scenario whose only victim can die is a scenario that sometimes
	// ends before it has rolled anything worth reading.
	addPerson(t, path, "Bo the Knife", "Lonely Rock", "bandit", 2, 80, 2)
	for _, name := range []string{"Merchant Yun", "Merchant Ge", "Merchant Pan"} {
		addPerson(t, path, name, "Lonely Rock", "merchant", 900, 40, 1)
	}
	// The merchant trades between robberies. Without that the scenario runs
	// itself dry in three thefts - a theft takes a quarter of what the victim
	// has, so the victim is soon poorer than the thief and there is nothing
	// left to steal - and three rolls is not a sample.
	// Each tick the merchants trade and the bandit spends what he took. Both
	// halves matter: without the top-up the victims are soon poorer than the
	// thief, and without spending the *thief* is soon richer than all three -
	// and nobody robs somebody with less than they have, so either way the
	// scenario quietly stops after four rolls and proves nothing.
	// Lent dice, one tick at a time. This test needs three different crimes -
	// one with witnesses, one without, and a killing - and hoping three
	// hundred days of 20% robberies produce all three is how the class this
	// gate exists for reads. `npcCrimes` rolls in a fixed order per crime:
	// whether it happens, what kind it is, whether anybody saw, and (when it
	// is violent) whether it kills. The counter answers each in turn and the
	// loop resets it, so a tick is a written scenario rather than a sample.
	const (
		seenTheft = iota
		unseenTheft
		aKillingNobodySaw
	)
	mode, call := seenTheft, 0
	defer gamerng.UseRoller(func(n int) int {
		call++
		switch call {
		case 1:
			return 0 // under crimeChanceCriminal: the robbery happens
		case 2:
			if mode == aKillingNobodySaw {
				return crimeViolentFrom
			}
			return 0 // neither violent nor smuggling: an ordinary theft
		case 3:
			if mode == seenTheft {
				return 0 // the road was not as empty as it looked
			}
			return n - 1 // and nobody saw this one
		default:
			return 0 // under crimeFatal: the violence is fatal
		}
	})()

	committed := int64(0)
	for tick := 0; tick < 300; tick++ {
		switch {
		case tick < 150:
			mode = seenTheft
		case tick < 299:
			mode = unseenTheft
		default:
			mode = aKillingNobodySaw
		}
		call = 0
		topUp(t, path, "Bo the Knife", 2)
		for _, name := range []string{"Merchant Yun", "Merchant Ge", "Merchant Pan"} {
			topUp(t, path, name, 900)
		}
		c, _, _ := runCrimesFrom(t, path, r, 1, int64(1000+tick))
		committed += c
	}
	if committed != 300 {
		t.Fatalf("three hundred days of certain robbery committed %d", committed)
	}
	hidden := deedScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE visibility='hidden'`)
	if hidden != 149 {
		t.Fatalf("%d of the 149 unwitnessed thefts went unrecorded as hidden", hidden)
	}
	// A grudge is held against a face. It exists exactly when a crime was
	// seen - an unsolved robbery leaves the victim angry at nobody.
	// One grudge row per pair that was ever seen: a victim robbed unseen is
	// angry at nobody, because there is nobody to be angry at.
	seenPairs := deedScalar(t, path, `SELECT COUNT(DISTINCT target_name) FROM world_history_events
        WHERE visibility='public' AND event_type IN ('npc_theft','npc_robbery')`)
	grudges := deedScalar(t, path, `SELECT COUNT(*) FROM npc_social_relations WHERE grudge>0`)
	if grudges != seenPairs {
		t.Fatalf("%d victim(s) were robbed in front of witnesses and %d grudge(s) were held", seenPairs, grudges)
	}
	// A body is found whether or not the killer is named. The last day's
	// robbery was fatal and nobody saw it, which is the one case where a
	// hidden row would be the plausible mistake.
	if got := deedScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_killing'`); got != 1 {
		t.Fatalf("the last day's killing left %d record(s)", got)
	}
	if got := deedScalar(t, path, `SELECT COUNT(*) FROM world_history_events
        WHERE event_type='npc_killing' AND visibility<>'public'`); got != 0 {
		t.Fatalf("%d killing(s) nobody ever noticed", got)
	}
}

func TestTheDeadCommitNoFurtherCrimes(t *testing.T) {
	path := deedsDB(t)
	r := deedsRunner()
	addPerson(t, path, "Bo the Knife", "Lonely Rock", "bandit", 2, 80, 6)
	addPerson(t, path, "Merchant Yun", "Lonely Rock", "merchant", 900, 40, 0)
	// This used to hope for a killing across three hundred days - a 20%
	// robbery, a seventh of those violent, a fifth of those fatal - and skip
	// itself when none came, which is the same fault as a flake wearing a
	// quieter coat: most runs it proved nothing. The dice are lent, so the
	// first day is the killing and the twenty-nine after it are the question.
	call := 0
	defer gamerng.UseRoller(func(int) int {
		call++
		if call == 2 {
			return crimeViolentFrom // the robbery turns violent
		}
		return 0 // it happens, it is seen, and it kills
	})()
	for tick := 0; tick < 30; tick++ {
		call = 0
		runCrimesFrom(t, path, r, 1, int64(1000+tick))
	}
	if got := deedScalar(t, path, `SELECT COUNT(*) FROM npc_civilization_state WHERE status='dead'`); got != 1 {
		t.Fatalf("a certain killing left %d dead", got)
	}
	// Whoever died, nothing after their death names them as the victim again.
	if got := deedScalar(t, path, `SELECT COUNT(*) FROM world_history_events h
        JOIN npc_life_state l ON l.npc_name=h.target_name
        WHERE l.death_game_minute IS NOT NULL AND h.game_minute>l.death_game_minute`); got != 0 {
		t.Fatalf("%d thing(s) happened to somebody already dead", got)
	}
}

func TestOneTickNeverExceedsTheCrimeCap(t *testing.T) {
	path := deedsDB(t)
	r := deedsRunner()
	for i := 0; i < 30; i++ {
		addPerson(t, path, fmt.Sprintf("Bandit %02d", i), "Greenriver Town", "bandit", 1, 90, 1)
	}
	addPerson(t, path, "Merchant Yun", "Greenriver Town", "merchant", 100000, 40, 1)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	committed, _, _, err := r.npcCrimes(conn, 5000)
	if err != nil {
		t.Fatal(err)
	}
	if committed > crimeCap {
		t.Fatalf("one tick produced %d crimes, cap is %d", committed, crimeCap)
	}
}

// everyHuntLands sends the hunter out every day and rolls the top of the d20,
// so what the woods answer is the hunter's own realm against the quarry's TN
// rather than the dice. Every other bound takes the first of its options.
func everyHuntLands() func() {
	return gamerng.UseRoller(func(n int) int {
		if n == 20 {
			return 19 // the hunt's own d20
		}
		return 0
	})
}

func TestAHunterGoesOutAndTheWoodsAnswer(t *testing.T) {
	defer everyHuntLands()()
	path := deedsDB(t)
	r := deedsRunner()
	addPerson(t, path, "Hunter Gao", "Greenriver Town", "beast hunter", 10, 50, 6)
	hunted, took, _ := runHunts(t, path, r, 200)
	if hunted != 200 {
		t.Fatalf("a hunter who goes out every day went out %d time(s) in two hundred", hunted)
	}
	if took != hunted {
		t.Fatalf("%d hunts at the top of the die brought back %d", hunted, took)
	}
	// What they took reaches the same floor their finds already reach.
	if got := deedScalar(t, path, `SELECT COUNT(*) FROM auctions WHERE seller_npc_name='Hunter Gao'`); got == 0 {
		t.Fatal("a hunt that paid out put nothing on any floor")
	}
	if got := deedScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_beast_hunt'`); got == 0 {
		t.Fatal("nobody in the world heard about any of it")
	}
}

func TestAFailedHuntIsPaidForInBlood(t *testing.T) {
	// Ones all the way down: the trapper goes out, rolls the bottom of the
	// d20 against a quarry scaled to a realm above his own, and the beast is
	// as deadly as the roll allows.
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path := deedsDB(t)
	r := deedsRunner()
	// A mortal-realm hunter against beasts scaled to that realm loses often.
	addPerson(t, path, "Hunter Gao", "Greenriver Town", "trapper", 10, 50, 0)
	if hunted, _, died := runHunts(t, path, r, 300); hunted != 1 || died != 1 {
		t.Fatalf("the trapper went out %d time(s) and died %d time(s); the first hunt should have been the last", hunted, died)
	}
	hurt := deedScalar(t, path, `SELECT COUNT(*) FROM npc_life_state WHERE npc_name='Hunter Gao' AND (injury<>'' OR death_game_minute IS NOT NULL)`)
	if hurt == 0 {
		t.Fatal("three hundred hunts at the bottom realm and never a scratch")
	}
}

// The dead leave a widow on every path (rc.24). The hunting death and the
// robbery-murder wrote status='dead' and never released the bonds (v1.2.1),
// so a spouse stayed married to a corpse.
func TestAHunterWhoDiesLeavesAWidow(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path := deedsDB(t)
	r := deedsRunner()
	addPerson(t, path, "Hunter Gao", "Greenriver Town", "trapper", 10, 50, 0)
	addPerson(t, path, "Widow Gao", "Greenriver Town", "weaver", 10, 0, 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, stmt := range []string{
		`UPDATE npc_life_state SET relationship_status='married',spouse_name='Widow Gao' WHERE npc_name='Hunter Gao'`,
		`UPDATE npc_life_state SET relationship_status='married',spouse_name='Hunter Gao' WHERE npc_name='Widow Gao'`,
	} {
		if _, err := conn.Execute(stmt, nil); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	if _, _, died := runHunts(t, path, r, 300); died != 1 {
		t.Fatalf("the trapper died %d time(s); the first hunt should have been the last", died)
	}
	status := deedText(t, path, `SELECT relationship_status FROM npc_life_state WHERE npc_name='Widow Gao'`)
	if status != "widowed" {
		t.Fatalf("Widow Gao is %q, still married to a corpse", status)
	}
}

func TestATradeThatDoesNotHuntNeverHunts(t *testing.T) {
	path := deedsDB(t)
	r := deedsRunner()
	addPerson(t, path, "Scribe Lan", "Greenriver Town", "scribe", 30, 50, 2)
	if hunted, _, _ := runHunts(t, path, r, 200); hunted != 0 {
		t.Fatalf("the scribe went hunting %d time(s)", hunted)
	}
}

func TestTheTradeMatchersReadTheWordsAnAuthorWrote(t *testing.T) {
	for _, trade := range []string{"Bandit Chief", "river pirate", "smuggler", "Outlaw"} {
		if !npcCriminalTrade(trade) {
			t.Errorf("%q should be a criminal trade", trade)
		}
	}
	for _, trade := range []string{"merchant", "steward", "alchemist", "farmer"} {
		if npcCriminalTrade(trade) {
			t.Errorf("%q is not a criminal trade", trade)
		}
	}
	for _, trade := range []string{"Beast Hunter", "trapper", "Ranger", "beast tamer"} {
		if !npcHuntingTrade(trade) {
			t.Errorf("%q should hunt", trade)
		}
	}
	for _, trade := range []string{"herbalist", "scribe", "merchant"} {
		if npcHuntingTrade(trade) {
			t.Errorf("%q does not hunt", trade)
		}
	}
}
