package game

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The party and the battle, four findings (v1.34.0).
//
//   - A party is joined without asking, cannot put a member out, and a raid
//     waits for ever on a raider who never comes back.
//   - A Spare after an easy fight paid insight, karma and fate every time.
//   - A pill drunk in the battle panel left no toxicity and cost no turn, and
//     a treatment taken mid-fight healed a figure the fight never read.
//   - A battle outlived the fighter, and three actions never asked if they
//     were alive.
//
// The tables carry production's DDL, foreign keys included: `storage.Open`
// turns them on, so a fixture cannot take a row the real schema refuses.

const battlesDDL = `CREATE TABLE IF NOT EXISTS battles (
	battle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, npc_name TEXT NOT NULL,
	npc_realm_index INTEGER NOT NULL, npc_stage INTEGER NOT NULL,
	player_hp INTEGER NOT NULL, player_hp_max INTEGER NOT NULL DEFAULT 1,
	npc_hp INTEGER NOT NULL, npc_hp_max INTEGER NOT NULL DEFAULT 1,
	status TEXT NOT NULL DEFAULT 'active', location TEXT NOT NULL, source TEXT NOT NULL,
	target_key TEXT NOT NULL DEFAULT '', thread_id INTEGER,
	npc_suppressed_turns INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 0,
	final_outcome TEXT NOT NULL DEFAULT '', finalized_at REAL,
	created_at REAL NOT NULL, updated_at REAL NOT NULL,
	FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
)`

const alchemyStateDDL = `CREATE TABLE IF NOT EXISTS alchemy_state (
	user_id INTEGER PRIMARY KEY,
	pill_toxicity INTEGER NOT NULL DEFAULT 0,
	last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0,
	total_refinements INTEGER NOT NULL DEFAULT 0,
	successful_refinements INTEGER NOT NULL DEFAULT 0,
	flawless_refinements INTEGER NOT NULL DEFAULT 0,
	best_margin INTEGER NOT NULL DEFAULT -99,
	last_quality TEXT NOT NULL DEFAULT '',
	updated_at REAL NOT NULL,
	FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
)`

const eventLogDDL = `CREATE TABLE IF NOT EXISTS event_log (
	id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT NOT NULL,
	payload_json TEXT NOT NULL, created_at REAL NOT NULL
)`

// withBattles gives a fixture that has none the real battles table.
func withBattles(t *testing.T, path string) {
	t.Helper()
	batch4Exec(t, path, battlesDDL)
}

// --- the raid: who may join, who may be put out, who is waited for --------

// raidParty makes a party by hand - 42 leads, 43 follows - and starts the Boar
// King for it. The fixture is the solo raid's: production's party and raid
// tables on the shared character fixture, both standing in the Boar King's lair.
func raidParty(t *testing.T) (path string, partyID, encounterID int64) {
	t.Helper()
	path = setupSoloRaidDB(t)
	batch4Exec(t, path, `INSERT INTO parties(party_id,leader_user_id,name,status,created_at,updated_at) VALUES(1,42,'Raiders','active',0,0)`)
	batch4Exec(t, path, `INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(1,42,'leader',0),(1,43,'member',1)`)
	out, err := soloBoarStart(t, path, 42)
	if err != nil {
		t.Fatalf("the raid did not start: %v", err)
	}
	return path, 1, i64(out["encounter_id"])
}

func partyOp(t *testing.T, path string, actor int64, op string, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(payload)
	catalog := crossingCatalog(t)
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := partyAction(conn, catalog, actor, raw, op)
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

func raidAct(t *testing.T, path string, actor, encounter int64, style string) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"encounter_id": encounter, "style": style, "game_minute": 100})
	catalog := crossingCatalog(t)
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := bossActActionGo(conn, catalog, actor, raw)
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

func roundOf(t *testing.T, path string, encounter int64) int64 {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT round_index FROM boss_encounters WHERE encounter_id=?`, encounter))
}

// goneQuiet makes a raider's last word `minutes` real minutes old.
func goneQuiet(t *testing.T, path string, encounter, user int64, minutes int) {
	t.Helper()
	at := float64(time.Now().Add(-time.Duration(minutes)*time.Minute).UnixNano()) / 1e9
	batch4Exec(t, path, `UPDATE boss_participants SET updated_at=? WHERE encounter_id=? AND user_id=?`, at, encounter, user)
}

// A party that is fighting takes nobody. Joining asks no one's leave, so a
// stranger could walk into a raid that was waiting on its own members, hold no
// seat in it and be unable to leave until it ended.
//
// Drill: drop the encounter check from party.join and this prints "a stranger
// joined a party in the middle of a boss raid".
func TestAPartyInTheMiddleOfARaidTakesNobody(t *testing.T) {
	path, partyID, _ := raidParty(t)
	batch4Exec(t, path, `DELETE FROM party_members WHERE user_id=43`)
	if _, err := partyOp(t, path, 43, "party.join", map[string]any{"party_id": partyID}); err == nil {
		t.Fatal("a stranger joined a party in the middle of a boss raid")
	} else if !strings.Contains(err.Error(), "raid") {
		t.Fatalf("the refusal does not say why: %v", err)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM party_members WHERE user_id=43`)); n != 0 {
		t.Fatalf("a refused join left %d seat(s) behind", n)
	}
	// And once the raid is over the door is open again.
	batch4Exec(t, path, `UPDATE boss_encounters SET status='defeat'`)
	if _, err := partyOp(t, path, 43, "party.join", map[string]any{"party_id": partyID}); err != nil {
		t.Fatalf("a party between raids refused a member: %v", err)
	}
}

// Only the leader puts a member out, and not themself.
//
// Drill: drop the leader check and a member's kick of the leader goes through
// ("a member put the leader out").
func TestOnlyTheLeaderPutsAMemberOut(t *testing.T) {
	path := setupSoloRaidDB(t)
	batch4Exec(t, path, `INSERT INTO parties(party_id,leader_user_id,name,status,created_at,updated_at) VALUES(1,42,'Mine','active',0,0)`)
	batch4Exec(t, path, `INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(1,42,'leader',0),(1,43,'member',1)`)
	if _, err := partyOp(t, path, 43, "party.kick", map[string]any{"user_id": 42}); err == nil {
		t.Fatal("a member put the leader out")
	}
	if _, err := partyOp(t, path, 42, "party.kick", map[string]any{"user_id": 42}); err == nil {
		t.Fatal("the leader put themself out instead of leaving")
	}
	if _, err := partyOp(t, path, 42, "party.kick", map[string]any{"user_id": 999}); err == nil {
		t.Fatal("the leader put out somebody who is not in the party")
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM party_members WHERE party_id=1`)); n != 2 {
		t.Fatalf("refused kicks changed the party to %d member(s)", n)
	}
	out, err := partyOp(t, path, 42, "party.kick", map[string]any{"user_id": 43})
	if err != nil {
		t.Fatalf("the leader could not put a member out: %v", err)
	}
	if out["kicked"] != true || i64(out["kicked_user_id"]) != 43 {
		t.Fatalf("the reply does not say who went: %+v", out)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM party_members WHERE user_id=43`)); n != 0 {
		t.Fatalf("the member is still in the party")
	}
	// The party is free for them to be put out of and to come back to.
	if _, err := partyOp(t, path, 43, "party.join", map[string]any{"party_id": 1}); err != nil {
		t.Fatalf("a member put out could not rejoin between raids: %v", err)
	}
}

// Putting out the raider the round was waiting on lets the round go, and what
// the party wins afterwards is not theirs.
//
// Drill: leave their boss_participants row in place when they are put out and
// this prints "the round still waits on a raider who is no longer in the party".
func TestPuttingOutTheRaiderTheRoundWaitsOnLetsItGo(t *testing.T) {
	path, _, encounter := raidParty(t)
	if _, err := raidAct(t, path, 42, encounter, "guard"); err != nil {
		t.Fatalf("the leader's guard was refused: %v", err)
	}
	if got := roundOf(t, path, encounter); got != 1 {
		t.Fatalf("the round moved on to %d with a raider still to act", got)
	}
	out, err := partyOp(t, path, 42, "party.kick", map[string]any{"user_id": 43})
	if err != nil {
		t.Fatalf("the kick was refused: %v", err)
	}
	if out["forfeited_raid"] != true {
		t.Fatalf("the reply does not say the raid was forfeited: %+v", out)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM boss_participants WHERE encounter_id=? AND user_id=43`, encounter)); n != 0 {
		t.Fatalf("the round still waits on a raider who is no longer in the party")
	}
	if got := roundOf(t, path, encounter); got != 2 {
		t.Fatalf("the boss did not answer the round once nobody was left to wait for: round %d", got)
	}
	// Winning now pays the one who is left.
	batch4Exec(t, path, `UPDATE boss_encounters SET boss_hp=1`)
	won, err := raidAct(t, path, 42, encounter, "attack")
	if err != nil || won["status"] != "victory" {
		t.Fatalf("the last raider did not win: %v %+v", err, won)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM boss_reward_claims WHERE encounter_id=?`, encounter)); n != 1 {
		t.Fatalf("%d claim(s) were written for a one-raider victory", n)
	}
}

// A raid does not wait on a raider who has gone quiet. The boss answers
// without them, they are struck as the rest are, and they are still in the
// raid.
//
// Drill: count every active raider as waiting whatever their age and this
// prints "the round is still waiting on a raider gone quiet for two hours".
func TestARoundDoesNotWaitForAnAbsentRaider(t *testing.T) {
	path, _, encounter := raidParty(t)
	goneQuiet(t, path, encounter, 43, 120)
	if _, err := raidAct(t, path, 42, encounter, "guard"); err != nil {
		t.Fatalf("the leader's guard was refused: %v", err)
	}
	if got := roundOf(t, path, encounter); got != 2 {
		t.Fatalf("the round is still waiting on a raider gone quiet for two hours: round %d", got)
	}
	if st := actionScalar(t, path, `SELECT status FROM boss_participants WHERE encounter_id=? AND user_id=43`, encounter); st != "active" && st != "knocked_out" {
		t.Fatalf("the absent raider was forfeited, not skipped: status %v", st)
	}
	if v := i64(actionScalar(t, path, `SELECT vitality FROM boss_participants WHERE encounter_id=? AND user_id=43`, encounter)); v >= 20 {
		t.Fatalf("the boss spared the raider who did not come: vitality %d of 20", v)
	}
}

// Quiet for a little is not absent: the half hour is a half hour.
func TestARaiderGoneQuietForTenMinutesIsStillWaitedFor(t *testing.T) {
	path, _, encounter := raidParty(t)
	goneQuiet(t, path, encounter, 43, 10)
	if _, err := raidAct(t, path, 42, encounter, "guard"); err != nil {
		t.Fatal(err)
	}
	if got := roundOf(t, path, encounter); got != 1 {
		t.Fatalf("the round went on without a raider who was gone ten minutes: round %d", got)
	}
	// The operator's key moves the line: with five minutes of patience the
	// same ten-minute silence is an absence, the leader's second press settles
	// the round, and the leader acts in the one that follows.
	t.Setenv("RAID_ABSENT_MINUTES", "5")
	if _, err := raidAct(t, path, 42, encounter, "guard"); err != nil {
		t.Fatalf("a five-minute patience left a ten-minute absence waited for: %v", err)
	}
	if got := roundOf(t, path, encounter); got != 2 {
		t.Fatalf("a five-minute patience left a ten-minute absence waited for: round %d", got)
	}
}

// A raider who has acted presses again after the rest have gone: the press
// asks the gate and the round that follows is theirs to act in. Without it a
// round that only an absentee holds up waits for ever, because no one who is
// left has anything to press.
//
// Drill: drop the settle from the front of boss.act and this prints "you
// already acted this round".
func TestAnActedRaiderSettlesARoundOnlyAnAbsenteeHoldsUp(t *testing.T) {
	path, _, encounter := raidParty(t)
	if _, err := raidAct(t, path, 42, encounter, "guard"); err != nil {
		t.Fatal(err)
	}
	if got := roundOf(t, path, encounter); got != 1 {
		t.Fatalf("round %d", got)
	}
	goneQuiet(t, path, encounter, 43, 90)
	out, err := raidAct(t, path, 42, encounter, "guard")
	if err != nil {
		t.Fatalf("an acted raider could not move on a round held up only by an absentee: %v", err)
	}
	if got := roundOf(t, path, encounter); got != 2 {
		t.Fatalf("the round did not move on: round %d (%+v)", got, out)
	}
	if got := i64(actionScalar(t, path, `SELECT acted_round FROM boss_participants WHERE encounter_id=? AND user_id=42`, encounter)); got != 2 {
		t.Fatalf("the raider's press settled the old round and did not act in the new one: acted_round %d", got)
	}
}

// A heal is not the healed raider being heard from. If it were, the leader
// could keep a raid waiting on an absentee by healing them every round.
//
// Drill: write updated_at on the healed raider in the support branch and this
// prints "a heal kept an absent raider on the clock".
func TestAHealDoesNotRefreshAnAbsentRaiderOnTheClock(t *testing.T) {
	path, _, encounter := raidParty(t)
	batch4Exec(t, path, `UPDATE boss_participants SET vitality=3 WHERE encounter_id=? AND user_id=43`, encounter)
	goneQuiet(t, path, encounter, 43, 90)
	if _, err := raidAct(t, path, 42, encounter, "support"); err != nil {
		t.Fatalf("the support action was refused: %v", err)
	}
	if got := roundOf(t, path, encounter); got != 2 {
		t.Fatalf("a heal kept an absent raider on the clock: round %d", got)
	}
}

// --- a won fight is paid once a world day, per opponent --------------------

func pillWorldDB(t *testing.T) string {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	withBattles(t, path)
	batch4Exec(t, path, alchemyStateDDL)
	batch4Exec(t, path, eventLogDDL)
	// What the counter-attack reads about the cultivator: a companion's bonus.
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS spirit_beasts(beast_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,name TEXT NOT NULL,species TEXT NOT NULL,rank INTEGER NOT NULL DEFAULT 0,element TEXT NOT NULL DEFAULT 'None',intelligence INTEGER NOT NULL DEFAULT 10,temperament TEXT NOT NULL DEFAULT 'wary',bloodline TEXT NOT NULL DEFAULT 'Common',evolution_stage INTEGER NOT NULL DEFAULT 0,loyalty INTEGER NOT NULL DEFAULT 25,contract_type TEXT NOT NULL DEFAULT 'temporary',active INTEGER NOT NULL DEFAULT 0,techniques_json TEXT NOT NULL DEFAULT '[]',created_at REAL NOT NULL,updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS artifact_bonds(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, bond_level INTEGER NOT NULL DEFAULT 0, resonance INTEGER NOT NULL DEFAULT 0, awakened INTEGER NOT NULL DEFAULT 0, spirit_name TEXT NOT NULL DEFAULT '', temperament TEXT NOT NULL DEFAULT 'dormant', created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(user_id,item_id))`)
	batch4Exec(t, path, `INSERT INTO alchemy_state(user_id,updated_at) VALUES(42,0)`)
	batch4Exec(t, path, `UPDATE characters SET vitality=10,vitality_max=100,qi=20,qi_max=100,realm_index=1,phase=2 WHERE user_id=42`)
	return path
}

func startBattle(t *testing.T, path string, hp, npcHP, suppressed int64, target string, realm int64) int64 {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO battles(user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,status,location,source,target_key,npc_suppressed_turns,version,created_at,updated_at)
		VALUES(42,'Iron Bandit',?,2,?,100,?,10,'active','Greenriver Town',?,?,?,0,0,0)`, realm, hp, npcHP, "challenge:"+target, target, suppressed)
	return i64(actionScalar(t, path, `SELECT MAX(battle_id) FROM battles`))
}

func finalize(t *testing.T, path string, battle int64, outcome string, gameMinute int64) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"battle_id": battle, "outcome": outcome, "game_minute": gameMinute})
	catalog := shippedCatalog(t)
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := combatFinalizeAction(conn, catalog, 42, raw)
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

type payout struct{ insight, karma, fate int64 }

func payoutOf(t *testing.T, path string) payout {
	t.Helper()
	return payout{
		insight: i64(actionScalar(t, path, `SELECT insight_xp FROM characters WHERE user_id=42`)),
		karma:   i64(actionScalar(t, path, `SELECT karma_score FROM characters WHERE user_id=42`)),
		fate:    i64(actionScalar(t, path, `SELECT points FROM character_fate WHERE user_id=42`)),
	}
}

// The fight is never refused, but what it pays is paid once a world day per
// opponent: insight, the body's tempering, a Spare's karma and standing and the
// fate point a meaningful mercy earns.
//
// Drill: pay every win and this prints "the second Spare of the day paid again".
func TestTheSecondSpareOfTheDayPaysNothingMore(t *testing.T) {
	path := pillWorldDB(t)
	first, err := finalize(t, path, startBattle(t, path, 50, 0, 0, "npc:bandit", 8), "spare", 100)
	if err != nil {
		t.Fatalf("the first Spare was refused: %v", err)
	}
	if first["reward_repeated"] != false {
		t.Fatalf("the first win of the day was called a repeat: %+v", first)
	}
	afterFirst := payoutOf(t, path)
	if afterFirst.insight == 0 || afterFirst.karma != 52 || afterFirst.fate != 2 {
		t.Fatalf("the first Spare paid insight %d, karma %d, fate %d; want some, 52, 2", afterFirst.insight, afterFirst.karma, afterFirst.fate)
	}

	second, err := finalize(t, path, startBattle(t, path, 50, 0, 0, "npc:bandit", 8), "spare", 400)
	if err != nil {
		t.Fatalf("the second fight was refused; it must only pay less: %v", err)
	}
	if second["reward_repeated"] != true {
		t.Fatalf("the second Spare of the day paid again: %+v", second)
	}
	if got := payoutOf(t, path); got != afterFirst {
		t.Fatalf("the second Spare of the day paid again: before %+v, after %+v", afterFirst, got)
	}

	// Another opponent the same day is a different win.
	if _, err := finalize(t, path, startBattle(t, path, 50, 0, 0, "npc:other", 8), "spare", 500); err != nil {
		t.Fatal(err)
	}
	if got := payoutOf(t, path); got.insight <= afterFirst.insight || got.karma != 54 {
		t.Fatalf("a different opponent was held to the first one's cap: %+v", got)
	}

	// And the next world day is a new day.
	before := payoutOf(t, path)
	if _, err := finalize(t, path, startBattle(t, path, 50, 0, 0, "npc:bandit", 8), "spare", 100+1440); err != nil {
		t.Fatal(err)
	}
	if got := payoutOf(t, path); got.insight <= before.insight || got.karma != before.karma+2 {
		t.Fatalf("the next world day did not pay: before %+v, after %+v", before, got)
	}
}

// --- a pill in the battle panel -------------------------------------------

func recoveryCatalog(vitality, qi int64) worlddata.Catalog {
	return worlddata.Catalog{Items: map[string]worlddata.Item{
		"healing_pill": {Name: "Healing Pill", Use: worlddata.ItemUse{Instant: worlddata.ItemInstantUse{VitalityRestore: vitality, QiRestore: qi}}},
	}}
}

func drinkInBattle(t *testing.T, path string, battle int64, catalog worlddata.Catalog) (map[string]any, error) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'healing_pill',3) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=3`)
	raw, _ := json.Marshal(map[string]any{"battle_id": battle, "item_id": "healing_pill", "game_minute": 100})
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := combatRecoveryItemAction(conn, catalog, 42, raw)
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

// A pill is as dirty in a fight as out of one.
//
// Drill: drop the toxicity step from combat.recovery_item and this prints
// "a pill drunk in battle left no toxicity".
func TestAPillDrunkInBattleLeavesItsToxicity(t *testing.T) {
	path := pillWorldDB(t)
	battle := startBattle(t, path, 10, 10, 1, "npc:bandit", 1)
	out, err := drinkInBattle(t, path, battle, recoveryCatalog(12, 0))
	if err != nil {
		t.Fatalf("the pill was refused: %v", err)
	}
	if i64(out["toxicity_gain"]) != 7 {
		t.Fatalf("a pill drunk in battle left no toxicity: %+v", out)
	}
	if got := i64(actionScalar(t, path, `SELECT pill_toxicity FROM alchemy_state WHERE user_id=42`)); got != 7 {
		t.Fatalf("the body holds %d toxicity after the pill, want 7", got)
	}
}

// A pill is a turn spent: the opponent answers it as they answer an attack, and
// the battle's vitality follows the character's.
//
// Drill: drop the counter from combat.recovery_item and this prints "the
// opponent let a pill pass unanswered".
func TestAPillDrunkInBattleCostsTheTurn(t *testing.T) {
	// The dice lent high: the counter-attack lands.
	defer gamerng.UseRoller(highDice)()
	path := pillWorldDB(t)
	battle := startBattle(t, path, 10, 10, 0, "npc:bandit", 1)
	out, err := drinkInBattle(t, path, battle, recoveryCatalog(12, 0))
	if err != nil {
		t.Fatalf("the pill was refused: %v", err)
	}
	if out["counter_roll"] == nil {
		t.Fatalf("the opponent let a pill pass unanswered: %+v", out)
	}
	hurt := i64(out["damage_taken"])
	if hurt < 1 {
		t.Fatalf("the counter-attack was lent a winning roll and did nothing: %+v", out)
	}
	if want := 10 + 12 - hurt; i64(out["vitality"]) != want {
		t.Fatalf("the reply says vitality %v, want %d (10 + 12 healed - %d struck)", out["vitality"], want, hurt)
	}
	vitality := i64(actionScalar(t, path, `SELECT vitality FROM characters WHERE user_id=42`))
	if hp := i64(actionScalar(t, path, `SELECT player_hp FROM battles WHERE battle_id=?`, battle)); hp != vitality {
		t.Fatalf("the battle reads %d hp and the character %d", hp, vitality)
	}
	if v := i64(actionScalar(t, path, `SELECT version FROM battles WHERE battle_id=?`, battle)); v != 1 {
		t.Fatalf("battle version %d after one turn, want 1", v)
	}
}

// A held opponent spends the suppressed turn rather than answering, and the
// pill still costs the turn.
func TestAHeldOpponentSpendsItsSuppressedTurnOnAPill(t *testing.T) {
	defer gamerng.UseRoller(highDice)()
	path := pillWorldDB(t)
	battle := startBattle(t, path, 10, 10, 1, "npc:bandit", 1)
	out, err := drinkInBattle(t, path, battle, recoveryCatalog(12, 0))
	if err != nil {
		t.Fatal(err)
	}
	if out["counter_suppressed"] != true || out["counter_roll"] != nil {
		t.Fatalf("a held opponent answered: %+v", out)
	}
	if left := i64(actionScalar(t, path, `SELECT npc_suppressed_turns FROM battles WHERE battle_id=?`, battle)); left != 0 {
		t.Fatalf("the held turn was not spent: %d left", left)
	}
}

// A pill can lose the fight, and a lost fight is the same lost fight.
//
// Drill: end the pill's turn without the defeat branch and this prints "the
// pill's turn left the battle active at zero hp".
func TestAPillTurnCanLoseTheFight(t *testing.T) {
	defer gamerng.UseRoller(highDice)()
	path := pillWorldDB(t)
	batch4Exec(t, path, `UPDATE characters SET vitality=1 WHERE user_id=42`)
	battle := startBattle(t, path, 1, 10, 0, "npc:bandit", 6)
	out, err := drinkInBattle(t, path, battle, recoveryCatalog(0, 5))
	if err != nil {
		t.Fatalf("the pill was refused: %v", err)
	}
	if out["status"] != "lost" {
		t.Fatalf("the pill's turn left the battle active at zero hp: %+v", out)
	}
	if st := actionScalar(t, path, `SELECT status FROM battles WHERE battle_id=?`, battle); st != "lost" {
		t.Fatalf("the battle row is %v", st)
	}
	if out["injury"] == nil && out["true_death"] == nil && out["fate_rescue"] != true {
		t.Fatalf("the defeat left no mark on the cultivator: %+v", out)
	}
}

// A treatment taken in the middle of a fight heals the fight's figure too.
//
// Drill: drop the battles update from condition.treat and this prints "the
// treatment healed the character and not the battle".
func TestATreatmentMidBattleHealsTheBattleToo(t *testing.T) {
	path := pillWorldDB(t)
	world := batch4WorldPath(t)
	battle := startBattle(t, path, 10, 10, 0, "npc:bandit", 1)
	batch4Exec(t, path, "INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',1)")
	batch4Exec(t, path, "INSERT INTO character_conditions(user_id,condition_key,category,name,severity,state,source_type,source_id,effect_json,created_game_minute,updated_game_minute,created_at,updated_at) VALUES(42,'flesh_wound','Injury','Flesh Wound',2,'active','test','seed','{}',0,0,0,0)")
	batch4Exec(t, path, "INSERT INTO active_effects(user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at) VALUES(42,'condition:flesh_wound','Flesh Wound','condition','flesh_wound','{}',1,0,NULL,0)")
	batch4Result(t, batch4Apply(t, path, world, "condition.treat", 1, map[string]any{"condition": "flesh_wound", "game_minute": 300}))
	vitality := i64(actionScalar(t, path, `SELECT vitality FROM characters WHERE user_id=42`))
	if vitality <= 10 {
		t.Fatalf("the treatment healed nothing (%d): the fixture is not testing the sync", vitality)
	}
	if hp := i64(actionScalar(t, path, `SELECT player_hp FROM battles WHERE battle_id=?`, battle)); hp != vitality {
		t.Fatalf("the treatment healed the character to %d and not the battle (%d)", vitality, hp)
	}
}

// --- the grave -------------------------------------------------------------

// A battle does not outlive the fighter. Every death comes through the one
// function, so the old-age sweep - which no fight is anywhere near - abandons
// the battle too.
//
// Drill: drop the abandon from recordTrueDeathAuthoritative and this prints
// "a deceased character's battle is still active".
func TestDeathAbandonsTheBattle(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	withBattles(t, path)
	battle := startBattle(t, path, 20, 10, 0, "npc:bandit", 1)
	batch4Exec(t, path, `UPDATE characters SET natural_lifespan_years=1,life_extension_years=0,age_at_creation_years=18,created_game_minute=0,realm_index=0,phase=1,body_realm_index=0,body_phase=1 WHERE user_id=42`)
	batch4SetCanonicalGameMinute(t, path, 20000000)
	if _, err := applyModerationOp(t, path, world, "check.resolve", validCheckResolvePayload()); err != nil {
		t.Fatalf("the old-age death did not fire: %v", err)
	}
	if st := actionScalar(t, path, `SELECT life_status FROM characters WHERE user_id=42`); st != "deceased" {
		t.Fatalf("the fixture's character is %v, not deceased", st)
	}
	if st := actionScalar(t, path, `SELECT status FROM battles WHERE battle_id=?`, battle); st != "abandoned" {
		t.Fatalf("a deceased character's battle is still %v", st)
	}
}

// The technique, the pill and the final decision ask whether the fighter lives,
// as the turn does.
//
// Drill: drop the check from any one and this names it.
func TestADeceasedCharacterTakesNoPartInABattle(t *testing.T) {
	path := setupBugslayerCombatTurnDB(t, 901, false)
	batch4Exec(t, path, `UPDATE characters SET life_status='deceased', vitality=0 WHERE user_id=901`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id))`)
	batch4Exec(t, path, `INSERT INTO inventory VALUES(901,'healing_pill',2)`)
	batch4Exec(t, path, `UPDATE battles SET npc_hp=0 WHERE battle_id=1`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	catalog := recoveryCatalog(12, 0)
	catalog.LawSystem = lawTechniqueCatalog().LawSystem
	for name, run := range map[string]func() error{
		"combat.technique": func() error {
			raw, _ := json.Marshal(map[string]any{"battle_id": 1, "technique": "quiet_law", "game_minute": 100})
			_, e := combatTechniqueAction(conn, catalog, 901, raw)
			return e
		},
		"combat.recovery_item": func() error {
			raw, _ := json.Marshal(map[string]any{"battle_id": 1, "item_id": "healing_pill", "game_minute": 100})
			_, e := combatRecoveryItemAction(conn, catalog, 901, raw)
			return e
		},
		"combat.finalize": func() error {
			raw, _ := json.Marshal(map[string]any{"battle_id": 1, "outcome": "spare", "game_minute": 100})
			_, e := combatFinalizeAction(conn, catalog, 901, raw)
			return e
		},
	} {
		err := run()
		if err == nil || !strings.Contains(err.Error(), "deceased") {
			t.Errorf("%s answered a deceased character with %v", name, err)
		}
	}
	if n := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=901 AND item_id='healing_pill'`)); n != 2 {
		t.Fatalf("a refused pill was spent: %d left", n)
	}
}

// The wait is the engine's, with its key.
func TestTheRaidsPatienceIsTheEnginesWithAKey(t *testing.T) {
	if got := raidAbsentSeconds(); got != 30*60 {
		t.Fatalf("a raid waits %ds for an absent raider, want half an hour", got)
	}
	t.Setenv("RAID_ABSENT_MINUTES", "7")
	if got := raidAbsentSeconds(); got != 7*60 {
		t.Fatalf("the operator's 7 minutes served %ds", got)
	}
	t.Setenv("RAID_ABSENT_MINUTES", "junk")
	if got := raidAbsentSeconds(); got != 30*60 {
		t.Fatalf("a junk key moved the wait to %ds", got)
	}
}

// A raid whose every present raider has acted used to stay at "to act" until
// somebody pressed something: the question "is everybody who is coming in?"
// was asked only after a raid action and at a kick. The tick asks it now
// (v1.35.0), and the card is told whom the round has stopped waiting on.
func TestTheTickSettlesARoundNobodyIsPressing(t *testing.T) {
	path, _, encounter := raidParty(t)
	if _, err := raidAct(t, path, 42, encounter, "attack"); err != nil {
		t.Fatal(err)
	}
	before := roundOf(t, path, encounter)
	catalog := crossingCatalog(t)
	settle := func() int64 {
		var n int64
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			var err error
			n, err = SettleAbsentRaidsTx(conn, catalog, 100, nowSeconds())
			return err
		}); err != nil {
			t.Fatal(err)
		}
		return n
	}
	// Ten minutes quiet is still waited for: the tick settles nothing.
	goneQuiet(t, path, encounter, 43, 10)
	if n := settle(); n != 0 || roundOf(t, path, encounter) != before {
		t.Fatalf("the tick settled %d round(s) while a raider was ten minutes quiet (round %d -> %d)", n, before, roundOf(t, path, encounter))
	}
	// Past the half hour the card names them away, and the tick settles the
	// round with nobody pressing.
	goneQuiet(t, path, encounter, 43, 31)
	raw, _ := json.Marshal(map[string]any{"encounter_id": encounter})
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{APIVersion: authoritativeAPIVersion, Operation: "boss.absent", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	result, _ := out.Result.(map[string]any)
	if absent, _ := result["absent_user_ids"].([]int64); len(absent) != 1 || absent[0] != 43 {
		t.Fatalf("boss.absent names %v, want [43]", result["absent_user_ids"])
	}
	if n := settle(); n != 1 || roundOf(t, path, encounter) != before+1 {
		t.Fatalf("the tick settled %d round(s) and the round is %d, want 1 and %d", n, roundOf(t, path, encounter), before+1)
	}
}
