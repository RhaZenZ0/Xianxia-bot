package game

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func setupCombatStartDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "combat_start.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY, name TEXT, gender TEXT, path TEXT, spiritual_root TEXT,
	location TEXT, attributes_json TEXT, realm_index INTEGER, phase INTEGER,
	body_realm_index INTEGER, body_phase INTEGER, cultivation INTEGER, body_cultivation INTEGER,
	life_status TEXT, vitality INTEGER, vitality_max INTEGER, updated_at REAL
);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at)
	VALUES(101,'Tester 101','','Sword Cultivator','Fire','Greenriver Town','{}',3,4,0,1,0,0,'alive',12,20,0);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at)
	VALUES(202,'Tester 202','','Sword Cultivator','Fire','Greenriver Town','{}',1,2,0,1,0,0,'alive',20,20,0);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at)
	VALUES(303,'Tester 303','','Sword Cultivator','Fire','Greenriver Town','{}',1,2,0,1,0,0,'dead',0,20,0);
` + challengeWorldDDL + `
` + challengeSeeds("Greenriver Town",
		challengeSeed{"Iron Bandit", 5, 7}, challengeSeed{"Named Opponent", 1, 2},
		challengeSeed{"First Foe", 1, 1}, challengeSeed{"Second Foe", 1, 1})); err != nil {
		t.Fatal(err)
	}
	return path
}

// challengeSeed is somebody the simulation has standing at a place, written as
// the SQL a fixture script runs. A challenge reads whom it fights from this row
// and from nothing the payload says.
type challengeSeed struct {
	name         string
	realm, phase int
}

func challengeSeeds(location string, people ...challengeSeed) string {
	var b strings.Builder
	for _, p := range people {
		fmt.Fprintf(&b, "INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,influence,realm_index,phase,updated_at) VALUES('%s','%s','%s','Mortal World','Test',10,%d,%d,0);\n", p.name, location, location, p.realm, p.phase)
	}
	return b.String()
}

func callCombatStart(t *testing.T, path string, userID int64, payload map[string]any) (authoritativeMutation, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	// An empty catalogue knows no locations, so the v1.0.6 safe-zone gate
	// does not fire here; `violence_suppression_test.go` drives it against a
	// catalogue that does.
	mut, mutErr := combatStartAction(conn, worlddata.Catalog{}, userID, raw)
	// combatStartAction only issues writes (an implicit BEGIN opens on the
	// first one); the real dispatcher commits after every successful
	// mutation and rolls back on error, so mirror that here rather than
	// leaving writes uncommitted when the connection closes.
	if mutErr != nil {
		if conn.InTransaction() {
			_ = conn.Rollback()
		}
		return mut, mutErr
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut, nil
}

func TestCombatStartChallengeComputesServerSideHPFromCanonicalCharacter(t *testing.T) {
	path := setupCombatStartDB(t)
	// The payload says realm 0, stage 1. The curve below is Iron Bandit's own
	// 5/7 from the row the simulation holds: what a challenge fights is the
	// world's to say, and this test used to pin the opposite (the defect).
	mut, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "challenge", "npc_name": "Iron Bandit", "npc_realm_index": 0, "npc_stage": 1, "game_minute": 100,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	out := mut.Result.(map[string]any)
	// npc_hp = max(10, 12 + realm*4 + stage*2) = 12 + 20 + 14 = 46, from the row
	if storage.ParseInt(out["npc_hp"]) != 46 || storage.ParseInt(out["npc_hp_max"]) != 46 {
		t.Fatalf("npc_hp=%v", out["npc_hp"])
	}
	// player_hp/player_hp_max must come from the character's own canonical
	// vitality/vitality_max (12/20), never from a client-supplied field -
	// the payload above never even included one.
	if storage.ParseInt(out["player_hp"]) != 12 || storage.ParseInt(out["player_hp_max"]) != 20 {
		t.Fatalf("player_hp=%v player_hp_max=%v", out["player_hp"], out["player_hp_max"])
	}
	if out["location"] != "Greenriver Town" {
		t.Fatalf("location=%v", out["location"])
	}
	if got := actionScalar(t, path, "SELECT status FROM battles WHERE battle_id=?", out["battle_id"]); got != "active" {
		t.Fatalf("battle status=%v", got)
	}
}

func TestCombatStartEventKindNeedsItsEvent(t *testing.T) {
	// An event battle is the event's (v1.35.0): with no event_key, and no
	// older bot's `event:<key>` source to take one from, there is nothing to
	// read the fight off, and the payload's own severity is not it. The
	// derivation itself is driven in combat_event_start_test.go against a
	// fixture that has an event.
	path := setupCombatStartDB(t)
	_, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "event", "npc_name": "Beast Tide Wraith", "severity": 8, "target_key": "event:tide:101", "game_minute": 100,
	})
	if err == nil || !strings.Contains(err.Error(), "event_key is required") {
		t.Fatalf("an event battle with no event was started: %v", err)
	}
}

func TestCombatStartRejectsDuplicateActiveTargetKeyFromAnotherUser(t *testing.T) {
	path := setupCombatStartDB(t)
	if _, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "challenge", "npc_name": "Named Opponent", "game_minute": 100,
	}); err != nil {
		t.Fatalf("first start: %v", err)
	}
	_, err := callCombatStart(t, path, 202, map[string]any{
		"kind": "challenge", "npc_name": "Named Opponent", "game_minute": 100,
	})
	if err == nil || !strings.Contains(err.Error(), "already locked") {
		t.Fatalf("expected already-locked error, got %v", err)
	}
}

func TestCombatStartRejectsDeceasedActor(t *testing.T) {
	path := setupCombatStartDB(t)
	_, err := callCombatStart(t, path, 303, map[string]any{
		"kind": "challenge", "npc_name": "Anyone", "npc_realm_index": 1, "npc_stage": 1,
		"source": "challenge:npc:anyone", "target_key": "", "game_minute": 100,
	})
	if err == nil || !strings.Contains(err.Error(), "deceased") {
		t.Fatalf("expected deceased-actor rejection, got %v", err)
	}
}

func TestCombatStartAbandonsAnyPriorActiveBattleForSameUser(t *testing.T) {
	path := setupCombatStartDB(t)
	first, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "challenge", "npc_name": "First Foe", "game_minute": 100,
	})
	if err != nil {
		t.Fatalf("first start: %v", err)
	}
	firstID := storage.ParseInt(first.Result.(map[string]any)["battle_id"])
	second, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "challenge", "npc_name": "Second Foe", "game_minute": 100,
	})
	if err != nil {
		t.Fatalf("second start: %v", err)
	}
	secondID := storage.ParseInt(second.Result.(map[string]any)["battle_id"])
	if got := actionScalar(t, path, "SELECT status FROM battles WHERE battle_id=?", firstID); got != "abandoned" {
		t.Fatalf("first battle status=%v", got)
	}
	if got := actionScalar(t, path, "SELECT status FROM battles WHERE battle_id=?", secondID); got != "active" {
		t.Fatalf("second battle status=%v", got)
	}
}

func setupCombatRecoveryItemDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "combat_recovery_item.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY, name TEXT, gender TEXT, path TEXT, spiritual_root TEXT,
	location TEXT, attributes_json TEXT, realm_index INTEGER, phase INTEGER,
	body_realm_index INTEGER, body_phase INTEGER, cultivation INTEGER, body_cultivation INTEGER,
	life_status TEXT, qi INTEGER, qi_max INTEGER, vitality INTEGER, vitality_max INTEGER, updated_at REAL
);
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
CREATE TABLE inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id));
CREATE TABLE alchemy_state (user_id INTEGER PRIMARY KEY, pill_toxicity INTEGER NOT NULL DEFAULT 0, last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0, total_refinements INTEGER NOT NULL DEFAULT 0, successful_refinements INTEGER NOT NULL DEFAULT 0, flawless_refinements INTEGER NOT NULL DEFAULT 0, best_margin INTEGER NOT NULL DEFAULT -99, last_quality TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE character_spiritual_roots(user_id INTEGER PRIMARY KEY,grade TEXT NOT NULL DEFAULT 'Common',purity INTEGER NOT NULL DEFAULT 50,elements_json TEXT NOT NULL DEFAULT '[]',mutation TEXT NOT NULL DEFAULT '',stability INTEGER NOT NULL DEFAULT 100,refinement_progress INTEGER NOT NULL DEFAULT 0,compatibility INTEGER NOT NULL DEFAULT 50,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE character_bloodlines(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,bloodline_id TEXT NOT NULL,name TEXT NOT NULL,affinity TEXT NOT NULL DEFAULT 'None',purity INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'dormant',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,rejection INTEGER NOT NULL DEFAULT 0,mutation TEXT NOT NULL DEFAULT '',primary_lineage INTEGER NOT NULL DEFAULT 0,unlocked_techniques_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE character_physiques(user_id INTEGER PRIMARY KEY,physique_id TEXT NOT NULL DEFAULT 'ordinary_mortal_body',name TEXT NOT NULL DEFAULT 'Ordinary Mortal Body',state TEXT NOT NULL DEFAULT 'ordinary',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,stability INTEGER NOT NULL DEFAULT 100,instability INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE active_effects(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,effect_key TEXT NOT NULL,name TEXT NOT NULL,source_type TEXT NOT NULL,source_id TEXT NOT NULL,effect_json TEXT NOT NULL,stacks INTEGER NOT NULL DEFAULT 1,starts_game_minute INTEGER NOT NULL,ends_game_minute INTEGER,created_at REAL NOT NULL,UNIQUE(user_id,effect_key,source_type,source_id));
CREATE TABLE spirit_beasts(beast_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,name TEXT NOT NULL,species TEXT NOT NULL,rank INTEGER NOT NULL DEFAULT 0,element TEXT NOT NULL DEFAULT 'None',intelligence INTEGER NOT NULL DEFAULT 10,temperament TEXT NOT NULL DEFAULT 'wary',bloodline TEXT NOT NULL DEFAULT 'Common',evolution_stage INTEGER NOT NULL DEFAULT 0,loyalty INTEGER NOT NULL DEFAULT 25,contract_type TEXT NOT NULL DEFAULT 'temporary',active INTEGER NOT NULL DEFAULT 0,techniques_json TEXT NOT NULL DEFAULT '[]',created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE artifact_bonds(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, bond_level INTEGER NOT NULL DEFAULT 0, resonance INTEGER NOT NULL DEFAULT 0, awakened INTEGER NOT NULL DEFAULT 0, spirit_name TEXT NOT NULL DEFAULT '', temperament TEXT NOT NULL DEFAULT 'dormant', created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(user_id,item_id));
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,slot TEXT NOT NULL,durability INTEGER NOT NULL,max_durability INTEGER NOT NULL,quality INTEGER NOT NULL DEFAULT 100,equipped INTEGER NOT NULL DEFAULT 0,bound_at REAL NOT NULL,updated_at REAL NOT NULL);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,qi,qi_max,vitality,vitality_max,updated_at)
	VALUES(101,'Tester','','Sword Cultivator','Fire','Greenriver Town','{"body":10,"spirit":10,"agility":10,"will":10,"insight":10}',1,2,0,1,0,0,'alive',5,20,3,20,0);
INSERT INTO character_spiritual_roots(user_id) VALUES(101);
INSERT INTO alchemy_state(user_id,updated_at) VALUES(101,0);
-- The opponent is held for a turn, so the pill's counter-attack is the
-- suppressed one and the tests below read the heal without a dice roll
-- in it; the tests of the counter itself lend the dice.
INSERT INTO battles(battle_id,user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,status,location,source,target_key,npc_suppressed_turns,version,created_at,updated_at)
	VALUES(9,101,'Iron Bandit',1,2,3,20,10,10,'active','Greenriver Town','challenge:npc:iron-bandit','challenge:npc:iron-bandit',1,0,0,0);
INSERT INTO inventory(user_id,item_id,quantity) VALUES(101,'healing_pill',2);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func combatRecoveryItemCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		Items: map[string]worlddata.Item{
			"healing_pill": {
				Name: "Healing Pill",
				Use:  worlddata.ItemUse{Instant: worlddata.ItemInstantUse{VitalityRestore: 12}},
			},
		},
	}
}

func TestCombatRecoveryItemKeepsBattlePlayerHPInLockstepWithCharacterVitality(t *testing.T) {
	path := setupCombatRecoveryItemDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 9, "item_id": "healing_pill", "game_minute": 100})
	mut, err := combatRecoveryItemAction(conn, combatRecoveryItemCatalog(), 101, raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	out := mut.Result.(map[string]any)
	// vitality 3 + 12 restore = 15, clamped to vitality_max 20.
	if storage.ParseInt(out["vitality"]) != 15 {
		t.Fatalf("result vitality=%v", out["vitality"])
	}
	// Before this fix, battles.player_hp never moved off its stale value (3)
	// after a mid-battle heal - only characters.vitality changed.
	if got := actionScalar(t, path, "SELECT player_hp FROM battles WHERE battle_id=9"); storage.ParseInt(got) != 15 {
		t.Fatalf("battles.player_hp=%v, want 15 (in lockstep with characters.vitality)", got)
	}
	if got := actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=101 AND item_id='healing_pill'"); storage.ParseInt(got) != 1 {
		t.Fatalf("inventory quantity=%v, want 1 (one pill consumed)", got)
	}
	if got := actionScalar(t, path, "SELECT version FROM battles WHERE battle_id=9"); storage.ParseInt(got) != 1 {
		t.Fatalf("battles.version=%v, want bumped to 1", got)
	}
}
