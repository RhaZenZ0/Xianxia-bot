package game

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
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
CREATE TABLE battles(
	battle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, npc_name TEXT,
	npc_realm_index INTEGER, npc_stage INTEGER, player_hp INTEGER, player_hp_max INTEGER,
	npc_hp INTEGER, npc_hp_max INTEGER, status TEXT, location TEXT, source TEXT,
	target_key TEXT, npc_suppressed_turns INTEGER DEFAULT 0, version INTEGER DEFAULT 0,
	created_at REAL, updated_at REAL
);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at)
	VALUES(101,'Tester 101','','Sword Cultivator','Fire','Greenriver Town','{}',3,4,0,1,0,0,'alive',12,20,0);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at)
	VALUES(202,'Tester 202','','Sword Cultivator','Fire','Greenriver Town','{}',1,2,0,1,0,0,'alive',20,20,0);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at)
	VALUES(303,'Tester 303','','Sword Cultivator','Fire','Greenriver Town','{}',1,2,0,1,0,0,'dead',0,20,0);
`); err != nil {
		t.Fatal(err)
	}
	return path
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
	mut, mutErr := combatStartAction(conn, userID, raw)
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
	mut, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "challenge", "npc_name": "Iron Bandit", "npc_realm_index": 5, "npc_stage": 7,
		"source": "challenge:npc:iron-bandit", "target_key": "challenge:npc:iron-bandit", "game_minute": 100,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	out := mut.Result.(map[string]any)
	// npc_hp = max(10, 12 + realm*4 + stage*2) = 12 + 20 + 14 = 46
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

func TestCombatStartEventKindDerivesOpponentFromCallersOwnCharacterNotPayload(t *testing.T) {
	path := setupCombatStartDB(t)
	// user 101 is realm_index=3, phase=4. Pass deliberately wrong
	// npc_realm_index/npc_stage in the payload to prove the "event" kind
	// ignores them and derives the opponent from the caller's own
	// canonical character plus severity instead of trusting the client.
	mut, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "event", "npc_name": "Beast Tide Wraith", "npc_realm_index": 999, "npc_stage": 999,
		"severity": 8, "source": "event:tide", "target_key": "event:tide:101", "game_minute": 100,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	out := mut.Result.(map[string]any)
	// ri = max(0, 3 + max(0,8-5)/3) = 3+1 = 4; stage = max(1,min(9, 4 + max(0,8-4)/2)) = 4+2 = 6
	// hp = max(12, 14 + 4*5 + 6*2 + 8*2) = 14+20+12+16 = 62
	if storage.ParseInt(out["npc_realm_index"]) != 4 {
		t.Fatalf("npc_realm_index=%v (payload's 999 must be ignored for event kind)", out["npc_realm_index"])
	}
	if storage.ParseInt(out["npc_stage"]) != 6 {
		t.Fatalf("npc_stage=%v", out["npc_stage"])
	}
	if storage.ParseInt(out["npc_hp"]) != 62 {
		t.Fatalf("npc_hp=%v", out["npc_hp"])
	}
}

func TestCombatStartRejectsDuplicateActiveTargetKeyFromAnotherUser(t *testing.T) {
	path := setupCombatStartDB(t)
	if _, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "challenge", "npc_name": "Named Opponent", "npc_realm_index": 1, "npc_stage": 2,
		"source": "challenge:npc:named", "target_key": "challenge:npc:named", "game_minute": 100,
	}); err != nil {
		t.Fatalf("first start: %v", err)
	}
	_, err := callCombatStart(t, path, 202, map[string]any{
		"kind": "challenge", "npc_name": "Named Opponent", "npc_realm_index": 1, "npc_stage": 2,
		"source": "challenge:npc:named", "target_key": "challenge:npc:named", "game_minute": 100,
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
		"kind": "challenge", "npc_name": "First Foe", "npc_realm_index": 1, "npc_stage": 1,
		"source": "challenge:npc:first", "target_key": "challenge:npc:first", "game_minute": 100,
	})
	if err != nil {
		t.Fatalf("first start: %v", err)
	}
	firstID := storage.ParseInt(first.Result.(map[string]any)["battle_id"])
	second, err := callCombatStart(t, path, 101, map[string]any{
		"kind": "challenge", "npc_name": "Second Foe", "npc_realm_index": 1, "npc_stage": 1,
		"source": "challenge:npc:second", "target_key": "challenge:npc:second", "game_minute": 100,
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
