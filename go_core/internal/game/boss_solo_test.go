package game

import (
	"encoding/json"
	"math"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A solo raid (v1.7.8). /boss start with no party makes a party of one for the
// raid, marked raid_only and closed when the raid ends; a party of one fights
// the boss at bossSoloHPScale of its health.
//
// The party tables carry production's DDL, foreign keys included, and
// schema 68's column: a fixture that cannot fail the way production fails is
// not testing production.
func setupSoloRaidDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE parties (
		party_id INTEGER PRIMARY KEY AUTOINCREMENT, leader_user_id INTEGER NOT NULL, name TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, updated_at REAL NOT NULL,
		raid_only INTEGER NOT NULL DEFAULT 0,
		FOREIGN KEY(leader_user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE party_members (
		party_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'member', joined_at REAL NOT NULL,
		PRIMARY KEY(party_id,user_id),
		FOREIGN KEY(party_id) REFERENCES parties(party_id) ON DELETE CASCADE,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE boss_encounters(encounter_id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, template_key TEXT NOT NULL, location TEXT NOT NULL, boss_name TEXT NOT NULL, boss_hp INTEGER NOT NULL, boss_hp_max INTEGER NOT NULL, phase_index INTEGER NOT NULL DEFAULT 0, round_index INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active', winner_party_id INTEGER, version INTEGER NOT NULL DEFAULT 0, started_game_minute INTEGER NOT NULL DEFAULT 0, finished_game_minute INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE boss_participants(encounter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, vitality INTEGER NOT NULL, vitality_max INTEGER NOT NULL, acted_round INTEGER NOT NULL DEFAULT 0, total_damage INTEGER NOT NULL DEFAULT 0, guard INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL, PRIMARY KEY(encounter_id,user_id),
		FOREIGN KEY(encounter_id) REFERENCES boss_encounters(encounter_id) ON DELETE CASCADE,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE boss_reward_claims (
		encounter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, currency_amount INTEGER NOT NULL DEFAULT 0,
		item_id TEXT NOT NULL DEFAULT '', item_quantity INTEGER NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0,
		created_at REAL NOT NULL, claimed_at REAL, PRIMARY KEY(encounter_id,user_id),
		FOREIGN KEY(encounter_id) REFERENCES boss_encounters(encounter_id) ON DELETE CASCADE,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS equipment_instances (
		equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
		user_id INTEGER NOT NULL, item_id TEXT NOT NULL, slot TEXT NOT NULL,
		durability INTEGER NOT NULL, max_durability INTEGER NOT NULL, quality INTEGER NOT NULL DEFAULT 100,
		equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL, updated_at REAL NOT NULL,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE party_formations (
		formation_id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, name TEXT NOT NULL,
		stance TEXT NOT NULL DEFAULT 'balanced', cohesion INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 0,
		created_at REAL NOT NULL, updated_at REAL NOT NULL,
		FOREIGN KEY(party_id) REFERENCES parties(party_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE formation_positions (
		formation_id INTEGER NOT NULL, user_id INTEGER NOT NULL, position TEXT NOT NULL, assigned_at REAL NOT NULL,
		PRIMARY KEY(formation_id,user_id), UNIQUE(formation_id,position),
		FOREIGN KEY(formation_id) REFERENCES party_formations(formation_id) ON DELETE CASCADE,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	// The Boar King's lair and its realm.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town', realm_index=2`)
	return path
}

func soloBoarStart(t *testing.T, path string, caller int64) (map[string]any, error) {
	t.Helper()
	catalog := crossingCatalog(t)
	raw, _ := json.Marshal(map[string]any{"template_key": "iron_tusk_boar_king"})
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := bossStartActionGo(conn, catalog, caller, raw)
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

func TestASoloRaidNeedsNoParty(t *testing.T) {
	path := setupSoloRaidDB(t)
	out, err := soloBoarStart(t, path, 42)
	if err != nil {
		t.Fatalf("a cultivator with no party could not raid alone: %v", err)
	}
	if out["solo_party"] != true {
		t.Fatalf("the result does not say a party was made for the raid: %+v", out)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM parties WHERE leader_user_id=42 AND raid_only=1 AND status='active'`)); n != 1 {
		t.Fatalf("expected one raid-only party led by the caller, found %d", n)
	}
	want := int64(math.Round(float64(bossTemplatesGo["iron_tusk_boar_king"].MaxHP) * bossSoloHPScale))
	if hp := storage.ParseInt(out["boss_hp_max"]); hp != want {
		t.Fatalf("a solo boar has %d health, want %d (%.0f%% of its base)", hp, want, bossSoloHPScale*100)
	}
}

// A refused start rolls the party back with it, so a failed attempt does not
// leave a player sitting in a party they never asked for.
func TestARefusedSoloStartLeavesNoParty(t *testing.T) {
	path := setupSoloRaidDB(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=0 WHERE user_id=42`)
	if _, err := soloBoarStart(t, path, 42); err == nil || !strings.Contains(err.Error(), "Lin Test") {
		t.Fatalf("a solo start below the boss's realm was not refused by name: %v", err)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM parties`)); n != 0 {
		t.Fatalf("a refused solo start left %d party row(s) behind", n)
	}
}

func TestASoloPartyIsClosedWhenItsRaidEnds(t *testing.T) {
	path := setupSoloRaidDB(t)
	out, err := soloBoarStart(t, path, 42)
	if err != nil {
		t.Fatalf("the solo raid did not start: %v", err)
	}
	// One blow from the fixture's overwhelming cultivator ends it; the roll is
	// a stable hash, not gamerng, so this is the same every run.
	batch4Exec(t, path, `UPDATE boss_encounters SET boss_hp=1`)
	raw, _ := json.Marshal(map[string]any{"encounter_id": out["encounter_id"], "style": "attack"})
	var acted map[string]any
	err = crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := bossActActionGo(conn, crossingCatalog(t), 42, raw)
		acted, _ = m.Result.(map[string]any)
		return err
	})
	if err != nil {
		t.Fatalf("the blow was refused: %v", err)
	}
	if acted["status"] != "victory" {
		t.Fatalf("the one-health boar was not beaten: %+v", acted)
	}
	if st := actionScalar(t, path, `SELECT status FROM parties WHERE leader_user_id=42`); st != "disbanded" {
		t.Fatalf("the party made for the raid is %v after it was won, want disbanded", st)
	}
}

// A party somebody made by hand is theirs, and a raid ending does not close it.
func TestAPartyMadeByHandOutlivesItsRaid(t *testing.T) {
	path := setupSoloRaidDB(t)
	batch4Exec(t, path, `INSERT INTO parties(party_id,leader_user_id,name,status,created_at,updated_at) VALUES(1,42,'Mine','active',0,0)`)
	batch4Exec(t, path, `INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(1,42,'leader',0)`)
	out, err := soloBoarStart(t, path, 42)
	if err != nil {
		t.Fatalf("the raid did not start: %v", err)
	}
	if out["solo_party"] != false {
		t.Fatalf("a hand-made party was reported as made for the raid: %+v", out)
	}
	batch4Exec(t, path, `UPDATE boss_encounters SET boss_hp=1`)
	raw, _ := json.Marshal(map[string]any{"encounter_id": out["encounter_id"], "style": "attack"})
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		_, err := bossActActionGo(conn, crossingCatalog(t), 42, raw)
		return err
	}); err != nil {
		t.Fatalf("the blow was refused: %v", err)
	}
	if st := actionScalar(t, path, `SELECT status FROM parties WHERE party_id=1`); st != "active" {
		t.Fatalf("a party made by hand is %v after its raid, want active", st)
	}
}

func TestAPartyOfOneFacesAWeakerBoss(t *testing.T) {
	if got := bossHPScale(1); got != bossSoloHPScale || got >= 1 {
		t.Fatalf("a party of one is scaled %v, want %v and below the whole base", got, bossSoloHPScale)
	}
	if got := bossHPScale(3); math.Abs(got-1.4) > 1e-9 {
		t.Fatalf("a party of three is scaled %v, want 1.4 (0.8 + 0.2 a member)", got)
	}
}

// A GM clearing a stuck raid closes the party made for it, the same as a win.
func TestAClearedRaidClosesItsSoloParty(t *testing.T) {
	path := setupSoloRaidDB(t)
	if _, err := soloBoarStart(t, path, 42); err != nil {
		t.Fatalf("the solo raid did not start: %v", err)
	}
	// The lever clears every kind of fight at once; production's tables for
	// the other two kinds.
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS battles (
		battle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, npc_name TEXT NOT NULL,
		npc_realm_index INTEGER NOT NULL, npc_stage INTEGER NOT NULL,
		player_hp INTEGER NOT NULL, player_hp_max INTEGER NOT NULL DEFAULT 1,
		npc_hp INTEGER NOT NULL, npc_hp_max INTEGER NOT NULL DEFAULT 1,
		status TEXT NOT NULL DEFAULT 'active', location TEXT NOT NULL, source TEXT NOT NULL,
		target_key TEXT NOT NULL DEFAULT '', thread_id INTEGER,
		npc_suppressed_turns INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 0,
		final_outcome TEXT NOT NULL DEFAULT '', finalized_at REAL,
		created_at REAL NOT NULL, updated_at REAL NOT NULL,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS pvp_challenges (
		challenge_id INTEGER PRIMARY KEY AUTOINCREMENT, challenger_user_id INTEGER NOT NULL, target_user_id INTEGER NOT NULL,
		stakes TEXT NOT NULL DEFAULT 'honor', status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL, expires_at REAL NOT NULL,
		FOREIGN KEY(challenger_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
		FOREIGN KEY(target_user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS pvp_matches (
		match_id INTEGER PRIMARY KEY AUTOINCREMENT, challenge_id INTEGER NOT NULL UNIQUE,
		player1_user_id INTEGER NOT NULL, player2_user_id INTEGER NOT NULL,
		player1_hp INTEGER NOT NULL, player2_hp INTEGER NOT NULL, turn_user_id INTEGER NOT NULL,
		player1_guard INTEGER NOT NULL DEFAULT 0, player2_guard INTEGER NOT NULL DEFAULT 0,
		status TEXT NOT NULL DEFAULT 'active', winner_user_id INTEGER, version INTEGER NOT NULL DEFAULT 0,
		created_at REAL NOT NULL, updated_at REAL NOT NULL,
		FOREIGN KEY(challenge_id) REFERENCES pvp_challenges(challenge_id) ON DELETE CASCADE,
		FOREIGN KEY(player1_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
		FOREIGN KEY(player2_user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)`)
	applyAdmin(t, path, "admin.player.clear_battle", map[string]any{"user_id": 42, "reason": "stuck"})
	if st := actionScalar(t, path, `SELECT status FROM boss_encounters`); st != "abandoned" {
		t.Fatalf("the raid is %v after the GM cleared it, want abandoned", st)
	}
	if st := actionScalar(t, path, `SELECT status FROM parties WHERE leader_user_id=42`); st != "disbanded" {
		t.Fatalf("the party made for a cleared raid is %v, want disbanded", st)
	}
}
