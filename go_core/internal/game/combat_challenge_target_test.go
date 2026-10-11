package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A challenge's opponent is the world's to name, not the caller's.
//
// `combat.start kind=challenge` used to store whatever realm and stage the
// payload carried, and the source and lock exactly as sent. The bot resolved
// the person first and sent the answer, so nothing a player does could forge
// it - but a bound that lives in the client is not a bound (rc.48), and the
// realm is worth a stage-lead on every roll of the fight, the severity of the
// kill, and the region and sects a kill marks. A challenge carrying
// `event:K|node:N` would also have been settled as an event kill, because
// finalize branches on that prefix.
//
// Every test here goes through ApplyWithWorld against the shipped content, so
// the hidden masters are the real ones and the dispatch is the one production
// runs. The tables are the production DDL, foreign keys included: a fixture
// that accepts what production refuses cannot fail the way production fails.

// challengeWorldDDL is `battles`, `npc_civilization_state` and `birth_families`
// as `app/database/core.py` creates them.
const challengeWorldDDL = `
DROP TABLE IF EXISTS character_birth_family;
DROP TABLE IF EXISTS birth_families;
DROP TABLE IF EXISTS battles;
CREATE TABLE battles (
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
);
CREATE INDEX IF NOT EXISTS idx_battles_user_active ON battles(user_id,status,updated_at);
CREATE TABLE IF NOT EXISTS npc_civilization_state (
	npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL, current_location TEXT NOT NULL, world_name TEXT NOT NULL,
	profession TEXT NOT NULL, faction TEXT NOT NULL DEFAULT 'Independent', wealth INTEGER NOT NULL DEFAULT 20,
	influence INTEGER NOT NULL DEFAULT 10, ambition INTEGER NOT NULL DEFAULT 50, realm_index INTEGER NOT NULL DEFAULT 0,
	phase INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'alive', activity TEXT NOT NULL DEFAULT 'Following established routine',
	last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
);
CREATE TABLE birth_families (
	family_id INTEGER PRIMARY KEY AUTOINCREMENT,
	family_name TEXT NOT NULL, surname TEXT NOT NULL, archetype TEXT NOT NULL,
	tier INTEGER NOT NULL DEFAULT 1, wealth INTEGER NOT NULL DEFAULT 20,
	influence INTEGER NOT NULL DEFAULT 10, stability INTEGER NOT NULL DEFAULT 60,
	alignment_bias INTEGER NOT NULL DEFAULT 0, location TEXT NOT NULL,
	head_name TEXT NOT NULL, head_gender TEXT NOT NULL DEFAULT 'neutral',
	head_title TEXT NOT NULL DEFAULT 'Family Head', head_realm_index INTEGER NOT NULL DEFAULT 0,
	head_phase INTEGER NOT NULL DEFAULT 1, treasury_balance INTEGER NOT NULL DEFAULT 0,
	generation INTEGER NOT NULL DEFAULT 1, created_game_minute INTEGER NOT NULL DEFAULT 0,
	last_simulated_game_minute INTEGER NOT NULL DEFAULT 0, history_json TEXT NOT NULL DEFAULT '[]',
	line_status TEXT NOT NULL DEFAULT 'active', extinct_afterlife_minute INTEGER,
	clan_structure TEXT NOT NULL DEFAULT 'extended_household', bloodline_name TEXT NOT NULL DEFAULT 'None',
	bloodline_affinity TEXT NOT NULL DEFAULT 'None', bloodline_trait TEXT NOT NULL DEFAULT 'No awakened ancestral bloodline',
	bloodline_purity INTEGER NOT NULL DEFAULT 0, branch_count INTEGER NOT NULL DEFAULT 1,
	retainer_count INTEGER NOT NULL DEFAULT 0, confederacy_name TEXT NOT NULL DEFAULT 'None',
	created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE character_birth_family (
	user_id INTEGER PRIMARY KEY, family_id INTEGER NOT NULL, birth_order INTEGER NOT NULL DEFAULT 1,
	generation INTEGER NOT NULL DEFAULT 1, last_support_game_minute INTEGER NOT NULL DEFAULT -999999999,
	FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
	FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE
);
`

// installChallengeWorld puts the three production tables a challenge reads and
// writes onto a fixture that already has characters. Anything the fixture made
// of those names is replaced, not extended.
func installChallengeWorld(t *testing.T, path string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(challengeWorldDDL); err != nil {
		t.Fatal(err)
	}
}

// seedChallengeNPC stands somebody in the simulation's table, which is where
// `combat.targets` and a challenge both look.
func seedChallengeNPC(t *testing.T, path, name, location, status string, influence, realm, phase int64) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,status,influence,realm_index,phase,updated_at)
		VALUES(?,?,?,'Mortal World','Test',?,?,?,?,0)`, name, location, location, status, influence, realm, phase)
}

func seedChallengeHead(t *testing.T, path, family, location, head string, influence, realm, phase int64) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO birth_families(family_name,surname,archetype,location,head_name,head_realm_index,head_phase,influence,created_at,updated_at)
		VALUES(?,?,'martial_household',?,?,?,?,?,0,0)`, family, family, location, head, realm, phase, influence)
}

func setupChallengeWorld(t *testing.T) (path, world string) {
	t.Helper()
	path = setupBatch4AuthorityDB(t)
	installChallengeWorld(t, path)
	return path, batch4WorldPath(t)
}

var challengeSeq int

// challengeStartAs sends combat.start through the production dispatch as one
// of the fixture's cultivators.
func challengeStartAs(t *testing.T, path, world string, actor int64, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	challengeSeq++
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   fmt.Sprintf("challenge-%d-%d", actor, challengeSeq),
		Operation:  "combat.start",
		ActorID:    actor,
		Payload:    raw,
	})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func challengeStart(t *testing.T, path, world string, payload map[string]any) (map[string]any, error) {
	t.Helper()
	return challengeStartAs(t, path, world, 42, payload)
}

func battleCount(t *testing.T, path string) int64 {
	t.Helper()
	return storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM battles`))
}

func TestAChallengesOpponentIsTheWorldsNotTheCallers(t *testing.T) {
	path, world := setupChallengeWorld(t)
	seedChallengeNPC(t, path, "Elder Feng", "Greenriver Town", "alive", 90, 20, 9)

	// What a forged request would say: the strongest person in town stated as
	// the weakest, the casing wrong, and a source and lock that name an event
	// node, which finalize would pay out as one.
	result, err := challengeStart(t, path, world, map[string]any{
		"kind": "challenge", "npc_name": "elder feng",
		"npc_realm_index": 0, "npc_stage": 1,
		"source": "event:forged|node:x", "target_key": "event:forged|node:x",
	})
	if err != nil {
		t.Fatalf("a challenge carrying a realm and stage was refused rather than ignored: %v", err)
	}
	id := result["battle_id"]
	realm := storage.ParseInt(actionScalar(t, path, `SELECT npc_realm_index FROM battles WHERE battle_id=?`, id))
	stage := storage.ParseInt(actionScalar(t, path, `SELECT npc_stage FROM battles WHERE battle_id=?`, id))
	if realm != 20 || stage != 9 {
		t.Fatalf("the battle stood at realm %d stage %d - the payload's 0/1, not Elder Feng's 20/9", realm, stage)
	}
	// hp = 12 + 20*4 + 9*2: the curve is the engine's, on the engine's numbers.
	if hp := storage.ParseInt(actionScalar(t, path, `SELECT npc_hp FROM battles WHERE battle_id=?`, id)); hp != 110 {
		t.Fatalf("npc_hp=%d, want 110", hp)
	}
	if name := actionScalar(t, path, `SELECT npc_name FROM battles WHERE battle_id=?`, id); name != "Elder Feng" {
		t.Fatalf("the battle is against %q; the canonical name is Elder Feng", name)
	}
	for _, column := range []string{"source", "target_key"} {
		got := actionScalar(t, path, `SELECT `+column+` FROM battles WHERE battle_id=?`, id)
		if got != "challenge:npc:Elder Feng" {
			t.Fatalf("%s=%q, want challenge:npc:Elder Feng - a challenge carrying an event's source would be settled as an event kill", column, got)
		}
	}
	if result["source"] != "challenge:npc:Elder Feng" || storage.ParseInt(result["npc_realm_index"]) != 20 {
		t.Fatalf("the reply does not say what the row says: %#v", result)
	}
}

func TestAnOlderBotsChallengeIsStillAnswered(t *testing.T) {
	// A rolling deploy: the bot before this one sends the realm, the stage, the
	// source and the lock it resolved itself. They are accepted and nothing
	// else - the request is not refused, and the answer is the row's.
	path, world := setupChallengeWorld(t)
	seedChallengeNPC(t, path, "Iron Bandit", "Greenriver Town", "alive", 10, 5, 7)
	result, err := challengeStart(t, path, world, map[string]any{
		"kind": "challenge", "npc_name": "Iron Bandit", "npc_realm_index": 5, "npc_stage": 7,
		"source": "challenge:npc:Iron Bandit", "target_key": "challenge:npc:Iron Bandit",
	})
	if err != nil {
		t.Fatalf("the previous bot's request was refused: %v", err)
	}
	// 12 + 5*4 + 7*2
	if storage.ParseInt(result["npc_hp"]) != 46 {
		t.Fatalf("npc_hp=%v, want 46", result["npc_hp"])
	}
}

func TestAChallengeNamesSomebodyStandingHere(t *testing.T) {
	path, world := setupChallengeWorld(t)
	seedChallengeNPC(t, path, "Far Elder", "Cloudblade City", "alive", 90, 20, 9)
	seedChallengeNPC(t, path, "Dead Elder", "Greenriver Town", "dead", 90, 20, 9)
	seedChallengeNPC(t, path, "Missing Elder", "Greenriver Town", "missing", 90, 20, 9)
	// A real hidden master, standing exactly where the caller stands: the
	// refusal must not be the thing that tells a player what he is.
	seedChallengeNPC(t, path, "Old Beggar Chen", "Greenriver Town", "alive", 90, 25, 9)

	var refusals []string
	for _, name := range []string{"Far Elder", "Dead Elder", "Missing Elder", "Old Beggar Chen", "Nobody At All"} {
		_, err := challengeStart(t, path, world, map[string]any{
			"kind": "challenge", "npc_name": name, "npc_realm_index": 0, "npc_stage": 1, "source": "challenge:npc:" + name,
		})
		if err == nil {
			t.Fatalf("a challenge to %q from Greenriver Town was accepted", name)
		}
		refusals = append(refusals, err.Error())
	}
	for i, got := range refusals {
		if got != challengeTargetAbsent {
			t.Fatalf("refusal %d is %q, want the one sentence %q - a separate sentence for any of the five is a hidden-power detector", i, got, challengeTargetAbsent)
		}
	}
	if n := battleCount(t, path); n != 0 {
		t.Fatalf("%d battle row(s) were written by challenges that were all refused", n)
	}
}

func TestAChallengedFamilyHeadIsTheHousesHead(t *testing.T) {
	path, world := setupChallengeWorld(t)
	seedChallengeHead(t, path, "Han Family", "Greenriver Town", "Han Wei", 60, 2, 4)
	result, err := challengeStart(t, path, world, map[string]any{
		"kind": "challenge", "npc_name": "han wei", "npc_realm_index": 0, "npc_stage": 1, "source": "challenge:npc:han wei",
	})
	if err != nil {
		t.Fatalf("a family head standing here was refused: %v", err)
	}
	if storage.ParseInt(result["npc_realm_index"]) != 2 || storage.ParseInt(result["npc_stage"]) != 4 {
		t.Fatalf("the head stood at %v/%v, want the house's 2/4", result["npc_realm_index"], result["npc_stage"])
	}
	if result["source"] != "challenge:family_head:1" || result["target_key"] != "challenge:family_head:1" {
		t.Fatalf("a head is locked by the house, not the name: source=%v target_key=%v", result["source"], result["target_key"])
	}
	if result["npc_name"] != "Han Wei" {
		t.Fatalf("npc_name=%v, want the canonical Han Wei", result["npc_name"])
	}
}

func TestAHeadAndAnNPCSharingANameResolveToTheNPC(t *testing.T) {
	// The picker's list deduplicates people ahead of heads, so the engine does
	// too: one name is one answer in both.
	path, world := setupChallengeWorld(t)
	seedChallengeNPC(t, path, "Merchant Wu", "Greenriver Town", "alive", 70, 1, 3)
	seedChallengeHead(t, path, "Wu Family", "Greenriver Town", "Merchant Wu", 10, 0, 1)
	result, err := challengeStart(t, path, world, map[string]any{"kind": "challenge", "npc_name": "Merchant Wu"})
	if err != nil {
		t.Fatal(err)
	}
	if result["source"] != "challenge:npc:Merchant Wu" || storage.ParseInt(result["npc_realm_index"]) != 1 {
		t.Fatalf("the name resolved to the head instead of the person: %#v", result)
	}
}

func TestAChallengeIsNotCappedAtThePickersFiftyRows(t *testing.T) {
	// The picker lists fifty at most, because a select holds twenty-five. A
	// name past that is somebody standing in the square all the same, and
	// refusing them only because the list was short would be the engine
	// agreeing with a limit that was never about the rule.
	path, world := setupChallengeWorld(t)
	for i := 1; i <= 55; i++ {
		seedChallengeNPC(t, path, fmt.Sprintf("Crowd Member %02d", i), "Greenriver Town", "alive", int64(1000-i), 1, 1)
	}
	listed := authority2Rows(t, authority2Query(t, path, world, "combat.targets", 0, map[string]any{"location": "Greenriver Town"}), "targets")
	if len(listed) != 50 {
		t.Fatalf("the picker lists %d, want its cap of 50", len(listed))
	}
	if _, err := challengeStart(t, path, world, map[string]any{"kind": "challenge", "npc_name": "crowd member 55"}); err != nil {
		t.Fatalf("the fifty-fifth person in the square cannot be challenged: %v", err)
	}
}

func TestTwoSpellingsOfOneNameShareOneLock(t *testing.T) {
	// The lock used to be whatever target_key the caller sent, so the same
	// person under two spellings was two locks. It is derived from the row now.
	path, world := setupChallengeWorld(t)
	seedChallengeNPC(t, path, "Named Opponent", "Greenriver Town", "alive", 10, 1, 2)
	if _, err := challengeStartAs(t, path, world, 42, map[string]any{"kind": "challenge", "npc_name": "Named Opponent"}); err != nil {
		t.Fatalf("first challenge: %v", err)
	}
	_, err := challengeStartAs(t, path, world, 43, map[string]any{"kind": "challenge", "npc_name": "NAMED OPPONENT", "target_key": "somewhere:else"})
	if err == nil || !strings.Contains(err.Error(), "already locked") {
		t.Fatalf("a second spelling of the same opponent was not locked out: %v", err)
	}
}

func TestAnEventStillNeedsItsSource(t *testing.T) {
	// `source is required` belongs to the kind that has no row to derive it
	// from. The sibling gap in that branch (its severity and its node) is a
	// separate finding.
	path, world := setupChallengeWorld(t)
	_, err := challengeStart(t, path, world, map[string]any{"kind": "event", "npc_name": "Beast Tide Wraith", "severity": 3})
	if err == nil || !strings.Contains(err.Error(), "source is required") {
		t.Fatalf("an event with no source was not refused for it: %v", err)
	}
}

func TestCombatTargetRowsAreOneRuleForThePickerAndTheEngine(t *testing.T) {
	// The picker is the same rule with caps. Anything the engine would let
	// somebody challenge, the picker (uncapped) lists, and the other way round.
	path := setupBatch4AuthorityDB(t)
	installChallengeWorld(t, path)
	seedChallengeNPC(t, path, "Old Gou", "Greenriver Town", "alive", 80, 0, 2)
	seedChallengeNPC(t, path, "Old Beggar Chen", "Greenriver Town", "alive", 90, 0, 1)
	seedChallengeHead(t, path, "Han Family", "Greenriver Town", "Han Wei", 60, 2, 4)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// The content file is in the repository and always present, so a read that
	// fails means this test cannot do its job - it is a failure, not a skip.
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatalf("shipped content: %v", err)
	}
	capped, err := combatTargetsGo(conn, catalog, "Greenriver Town")
	if err != nil {
		t.Fatal(err)
	}
	whole, err := combatTargetRows(conn, catalog, "Greenriver Town", false)
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(capped) != fmt.Sprint(whole) {
		t.Fatalf("with fewer rows than either cap the two must agree:\ncapped %v\nwhole  %v", capped, whole)
	}
	for _, row := range whole {
		if row["name"] == "Old Beggar Chen" {
			t.Fatal("a real hidden master was listed")
		}
	}
}
