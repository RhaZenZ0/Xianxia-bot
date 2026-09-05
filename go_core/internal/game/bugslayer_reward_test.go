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

// --- Pure decision-function tests -----------------------------------------
//
// These are the functions that actually decide whether the Bugslayer Sword's
// "Heavenly Flawfinder" passive fires. They are plain (bool/int64) -> bool
// functions with no DB or RNG involved, so every threshold is tested exactly.

func TestBugslayerCombatPassiveTriggers(t *testing.T) {
	if bugslayerCombatPassiveTriggers(false, bugslayerPassiveMargin+50) {
		t.Fatal("passive must not trigger when the Bugslayer Sword is not equipped")
	}
	if bugslayerCombatPassiveTriggers(true, bugslayerPassiveMargin-1) {
		t.Fatal("combat passive triggered below the required margin")
	}
	if !bugslayerCombatPassiveTriggers(true, bugslayerPassiveMargin) {
		t.Fatal("combat passive did not trigger at the required margin")
	}
	if !bugslayerCombatPassiveTriggers(true, bugslayerPassiveMargin+50) {
		t.Fatal("combat passive did not trigger well above the required margin")
	}
}

func TestBugslayerBossPassiveTriggers(t *testing.T) {
	if bugslayerBossPassiveTriggers(false, "attack", 90, 10) {
		t.Fatal("boss passive must not trigger when the Bugslayer Sword is not equipped")
	}
	if bugslayerBossPassiveTriggers(true, "technique", 90, 10) {
		t.Fatal("boss passive must only trigger on normal attacks, not techniques")
	}
	if bugslayerBossPassiveTriggers(true, "attack", 70, 51) {
		t.Fatal("boss passive triggered without a strong-enough hit (accuracy-roll < 20)")
	}
	if !bugslayerBossPassiveTriggers(true, "attack", 70, 50) {
		t.Fatal("boss passive did not trigger on a strong normal hit (accuracy-roll == 20)")
	}
	if !bugslayerBossPassiveTriggers(true, "attack", 90, 10) {
		t.Fatal("boss passive did not trigger well above the required margin")
	}
}

func TestIsIndestructibleEquipmentGo(t *testing.T) {
	if !isIndestructibleEquipmentGo(bugslayerSwordItemID) {
		t.Fatal("the Bugslayer Sword must be indestructible")
	}
	if isIndestructibleEquipmentGo("spirit_iron_sword") {
		t.Fatal("an ordinary item must not be treated as indestructible")
	}
	if isIndestructibleEquipmentGo("not_a_real_item") {
		t.Fatal("an unknown item id must not be treated as indestructible")
	}
}

func TestIndestructibleEquipmentIDsGo(t *testing.T) {
	ids := indestructibleEquipmentIDsGo()
	if len(ids) != 1 || ids[0] != bugslayerSwordItemID {
		t.Fatalf("indestructible ids = %v, want [%s]", ids, bugslayerSwordItemID)
	}
}

// --- damageEquipmentGo: indestructible items must be skipped ---------------

func setupBugslayerDurabilityDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "bugslayer_durability.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,slot TEXT NOT NULL,durability INTEGER NOT NULL,max_durability INTEGER NOT NULL,quality INTEGER NOT NULL DEFAULT 100,equipped INTEGER NOT NULL DEFAULT 0,bound_at REAL NOT NULL,updated_at REAL NOT NULL);
INSERT INTO equipment_instances(equipment_id,user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at) VALUES(1,901,'bugslayer_sword','weapon',100,100,100,1,0,0);
INSERT INTO equipment_instances(equipment_id,user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at) VALUES(2,901,'spirit_iron_armor','armor',150,160,100,1,0,0);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestDamageEquipmentGoSkipsIndestructibleItems(t *testing.T) {
	path := setupBugslayerDurabilityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := damageEquipmentGo(conn, 901, 5); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	r, err := conn.Execute(`SELECT item_id,durability,equipped FROM equipment_instances WHERE user_id=? ORDER BY equipment_id`, []any{901})
	if err != nil {
		t.Fatal(err)
	}
	rows := rowsToMaps(r)
	if fmt.Sprint(rows[0]["item_id"]) != "bugslayer_sword" || i64(rows[0]["durability"]) != 100 {
		t.Fatalf("bugslayer_sword durability = %v, want unchanged at 100", rows[0]["durability"])
	}
	if i64(rows[0]["equipped"]) != 1 {
		t.Fatal("bugslayer_sword must remain equipped - it can never hit 0 durability")
	}
	if fmt.Sprint(rows[1]["item_id"]) != "spirit_iron_armor" || i64(rows[1]["durability"]) != 145 {
		t.Fatalf("spirit_iron_armor durability = %v, want 145 (150-5)", rows[1]["durability"])
	}
}

// --- combatTurnAction: end-to-end passive trigger --------------------------
//
// gamerng.D10() uses crypto/rand with no seed hook, so the dice themselves
// cannot be pinned. Instead these tests force an overwhelming attack modifier
// (body/spirit attributes of 500 against a realm-0 NPC) so the roll succeeds
// with a margin far past bugslayerPassiveMargin regardless of the two dice -
// the same "make the outcome deterministic through the build, not the RNG"
// approach this combat system already has no alternative to.

func setupBugslayerCombatTurnDB(t *testing.T, userID int64, equipBugslayer bool) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "bugslayer_combat_turn.sqlite3")
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
CREATE TABLE character_spiritual_roots(user_id INTEGER PRIMARY KEY,grade TEXT NOT NULL DEFAULT 'Common',purity INTEGER NOT NULL DEFAULT 50,elements_json TEXT NOT NULL DEFAULT '[]',mutation TEXT NOT NULL DEFAULT '',stability INTEGER NOT NULL DEFAULT 100,refinement_progress INTEGER NOT NULL DEFAULT 0,compatibility INTEGER NOT NULL DEFAULT 50,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE character_bloodlines(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,bloodline_id TEXT NOT NULL,name TEXT NOT NULL,affinity TEXT NOT NULL DEFAULT 'None',purity INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'dormant',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,rejection INTEGER NOT NULL DEFAULT 0,mutation TEXT NOT NULL DEFAULT '',primary_lineage INTEGER NOT NULL DEFAULT 0,unlocked_techniques_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE character_physiques(user_id INTEGER PRIMARY KEY,physique_id TEXT NOT NULL DEFAULT 'ordinary_mortal_body',name TEXT NOT NULL DEFAULT 'Ordinary Mortal Body',state TEXT NOT NULL DEFAULT 'ordinary',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,stability INTEGER NOT NULL DEFAULT 100,instability INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE active_effects(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,effect_key TEXT NOT NULL,name TEXT NOT NULL,source_type TEXT NOT NULL,source_id TEXT NOT NULL,effect_json TEXT NOT NULL,stacks INTEGER NOT NULL DEFAULT 1,starts_game_minute INTEGER NOT NULL,ends_game_minute INTEGER,created_at REAL NOT NULL,UNIQUE(user_id,effect_key,source_type,source_id));
CREATE TABLE spirit_beasts(beast_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,name TEXT NOT NULL,species TEXT NOT NULL,rank INTEGER NOT NULL DEFAULT 0,element TEXT NOT NULL DEFAULT 'None',intelligence INTEGER NOT NULL DEFAULT 10,temperament TEXT NOT NULL DEFAULT 'wary',bloodline TEXT NOT NULL DEFAULT 'Common',evolution_stage INTEGER NOT NULL DEFAULT 0,loyalty INTEGER NOT NULL DEFAULT 25,contract_type TEXT NOT NULL DEFAULT 'temporary',active INTEGER NOT NULL DEFAULT 0,techniques_json TEXT NOT NULL DEFAULT '[]',created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE artifact_bonds(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, bond_level INTEGER NOT NULL DEFAULT 0, resonance INTEGER NOT NULL DEFAULT 0, awakened INTEGER NOT NULL DEFAULT 0, spirit_name TEXT NOT NULL DEFAULT '', temperament TEXT NOT NULL DEFAULT 'dormant', created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(user_id,item_id));
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,slot TEXT NOT NULL,durability INTEGER NOT NULL,max_durability INTEGER NOT NULL,quality INTEGER NOT NULL DEFAULT 100,equipped INTEGER NOT NULL DEFAULT 0,bound_at REAL NOT NULL,updated_at REAL NOT NULL);
`); err != nil {
		t.Fatal(err)
	}
	huge := `{"body":500,"spirit":500,"agility":500,"will":500,"insight":500}`
	if _, err := conn.Execute(
		`INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		[]any{userID, "Tester", "", "Sword Cultivator", "Fire", "Greenriver Town", huge, 3, 4, 0, 1, 0, 0, "alive", 20, 20, 0.0},
	); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO character_spiritual_roots(user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility,updated_at) VALUES(?,?,?,?,?,?,?,?,?)`,
		[]any{userID, "Common", 50, `["Fire"]`, "", 100, 0, 50, 0.0},
	); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO battles(battle_id,user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,status,location,source,target_key,npc_suppressed_turns,version,created_at,updated_at) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		[]any{userID, "Training Dummy", 0, 0, 20, 20, 999, 999, "active", "Greenriver Town", "challenge:npc:dummy", "challenge:npc:dummy", 0, 0, 0.0, 0.0},
	); err != nil {
		t.Fatal(err)
	}
	if equipBugslayer {
		if _, err := conn.Execute(
			`INSERT INTO equipment_instances(equipment_id,user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at) VALUES(1,?,?,?,?,?,?,1,0,0)`,
			[]any{userID, "bugslayer_sword", "weapon", 100, 100, 100},
		); err != nil {
			t.Fatal(err)
		}
	}
	// conn.Execute writes open an implicit transaction; commit it so the
	// seed rows survive the connection closing (ExecScript autocommits,
	// parameterised Execute calls do not).
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return path
}

func callCombatTurn(t *testing.T, path string, userID int64) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(map[string]any{"battle_id": 1, "style": "attack", "game_minute": 100})
	if err != nil {
		t.Fatal(err)
	}
	mut, mutErr := combatTurnAction(conn, worlddata.Catalog{}, userID, raw)
	if mutErr != nil {
		if conn.InTransaction() {
			_ = conn.Rollback()
		}
		return nil, mutErr
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any), nil
}

func TestCombatTurnActionAppliesBugslayerPassiveOnStrongHit(t *testing.T) {
	path := setupBugslayerCombatTurnDB(t, 901, true)
	out, err := callCombatTurn(t, path, 901)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if int64(out["equipment_attack"].(int64)) != 5 {
		t.Fatalf("equipment_attack = %v, want 5 (the sword's own +5 attack, proves equipDefs carries it too)", out["equipment_attack"])
	}
	if out["bugslayer_passive"] != bugslayerPassiveName {
		t.Fatalf("bugslayer_passive = %v, want %q", out["bugslayer_passive"], bugslayerPassiveName)
	}
	if out["bugslayer_bonus_damage"] != bugslayerPassiveBonusDamage {
		t.Fatalf("bugslayer_bonus_damage = %v, want %d", out["bugslayer_bonus_damage"], bugslayerPassiveBonusDamage)
	}
	if out["counter_suppressed"] != true {
		t.Fatal("the passive must disrupt this turn's counter")
	}
	if _, hasCounterRoll := out["counter_roll"]; hasCounterRoll {
		t.Fatal("a suppressed counter must not also roll a counter_roll")
	}
	// Dice are random but the roll's margin is reported, so the damage can
	// be pinned exactly: base formula + the passive's bonus.
	if got, want := i64(out["damage_dealt"]), expectedTurnDamage(t, out)+bugslayerPassiveBonusDamage; got != want {
		t.Fatalf("damage_dealt = %d, want %d (base formula + %d passive bonus)", got, want, bugslayerPassiveBonusDamage)
	}
}

// expectedTurnDamage mirrors combatTurnAction's attack damage formula for
// the fixture character (realm_index 3) using the margin and equipment
// attack the action itself reports.
func expectedTurnDamage(t *testing.T, out map[string]any) int64 {
	t.Helper()
	roll, ok := out["player_roll"].(map[string]any)
	if !ok {
		t.Fatalf("player_roll missing or wrong shape: %#v", out["player_roll"])
	}
	margin := i64(roll["margin"])
	atk := i64(out["equipment_attack"])
	const realm = int64(3)
	return maxI64(1, 2+maxI64(0, margin)/3+realm/4+maxI64(0, atk)/3)
}

func TestCombatTurnActionNoPassiveWithoutBugslayerSword(t *testing.T) {
	path := setupBugslayerCombatTurnDB(t, 902, false)
	out, err := callCombatTurn(t, path, 902)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if int64(out["equipment_attack"].(int64)) != 0 {
		t.Fatalf("equipment_attack = %v, want 0 (no equipment bound)", out["equipment_attack"])
	}
	if _, ok := out["bugslayer_passive"]; ok {
		t.Fatal("the passive must not fire without the Bugslayer Sword equipped")
	}
	if _, ok := out["bugslayer_bonus_damage"]; ok {
		t.Fatal("bugslayer_bonus_damage must not be set without the Bugslayer Sword equipped")
	}
	if _, hasCounterRoll := out["counter_roll"]; !hasCounterRoll {
		t.Fatal("without the passive, the NPC's counter should roll normally")
	}
	if got, want := i64(out["damage_dealt"]), expectedTurnDamage(t, out); got != want {
		t.Fatalf("damage_dealt = %d, want %d (base formula only, no passive bonus)", got, want)
	}
}

// --- bossActActionGo: end-to-end boss passive -----------------------------
//
// Boss rolls come from stablePercentGo, which is a deterministic hash, so
// the test computes the very same roll the engine will see and picks
// participant ids whose roll lands inside the hit window. Two participants
// are seeded so the boss does not retaliate (and consume the guard flag)
// in the same call - that only happens once everyone has acted.

const bugslayerBossTemplate = "iron_tusk_boar_king"

// pickBossHitUserID returns the first user id at or above start whose
// attack roll this round will land under the clamped accuracy ceiling.
func pickBossHitUserID(start int64) int64 {
	for uid := start; uid < start+1000; uid++ {
		if stablePercentGo(int64(1), int64(1), uid, "attack", int64(0)) < 95 {
			return uid
		}
	}
	panic("no hitting user id found - stablePercentGo distribution changed?")
}

func setupBugslayerBossDB(t *testing.T, swordUser, plainUser int64) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "bugslayer_boss.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	attrs := `{"body":40,"spirit":10,"agility":500}`
	if err := conn.ExecScript(fmt.Sprintf(`
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, attributes_json TEXT, realm_index INTEGER);
CREATE TABLE boss_encounters(encounter_id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, template_key TEXT NOT NULL, location TEXT NOT NULL, boss_name TEXT NOT NULL, boss_hp INTEGER NOT NULL, boss_hp_max INTEGER NOT NULL, phase_index INTEGER NOT NULL DEFAULT 0, round_index INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active', winner_party_id INTEGER, version INTEGER NOT NULL DEFAULT 0, started_game_minute INTEGER NOT NULL DEFAULT 0, finished_game_minute INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE boss_participants(encounter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, vitality INTEGER NOT NULL, vitality_max INTEGER NOT NULL, acted_round INTEGER NOT NULL DEFAULT 0, total_damage INTEGER NOT NULL DEFAULT 0, guard INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL, PRIMARY KEY(encounter_id,user_id));
CREATE TABLE boss_reward_claims(encounter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, currency_amount INTEGER NOT NULL DEFAULT 0, item_id TEXT NOT NULL DEFAULT '', item_quantity INTEGER NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, claimed_at REAL, PRIMARY KEY(encounter_id,user_id));
CREATE TABLE party_formations(formation_id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, name TEXT NOT NULL, stance TEXT NOT NULL DEFAULT 'balanced', cohesion INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE formation_positions(formation_id INTEGER NOT NULL, user_id INTEGER NOT NULL, position TEXT NOT NULL, assigned_at REAL NOT NULL, PRIMARY KEY(formation_id,user_id));
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,slot TEXT NOT NULL,durability INTEGER NOT NULL,max_durability INTEGER NOT NULL,quality INTEGER NOT NULL DEFAULT 100,equipped INTEGER NOT NULL DEFAULT 0,bound_at REAL NOT NULL,updated_at REAL NOT NULL);
INSERT INTO characters VALUES(%[1]d,'%[3]s',2);
INSERT INTO characters VALUES(%[2]d,'%[3]s',2);
INSERT INTO boss_encounters(encounter_id,party_id,template_key,location,boss_name,boss_hp,boss_hp_max,phase_index,round_index,status,version,created_at,updated_at) VALUES(1,10,'%[4]s','Greenriver Town','Iron-Tusk Boar King',180,180,0,1,'active',0,0,0);
INSERT INTO boss_participants(encounter_id,user_id,vitality,vitality_max,acted_round,total_damage,guard,status,updated_at) VALUES(1,%[1]d,20,20,0,0,0,'active',0);
INSERT INTO boss_participants(encounter_id,user_id,vitality,vitality_max,acted_round,total_damage,guard,status,updated_at) VALUES(1,%[2]d,20,20,0,0,0,'active',0);
INSERT INTO equipment_instances(equipment_id,user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at) VALUES(1,%[1]d,'bugslayer_sword','weapon',100,100,100,1,0,0);
`, swordUser, plainUser, attrs, bugslayerBossTemplate)); err != nil {
		t.Fatal(err)
	}
	return path
}

func callBossAct(t *testing.T, path string, userID int64) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(map[string]any{"encounter_id": 1, "style": "attack", "game_minute": 100})
	if err != nil {
		t.Fatal(err)
	}
	mut, mutErr := bossActActionGo(conn, worlddata.Catalog{}, userID, raw)
	if mutErr != nil {
		if conn.InTransaction() {
			_ = conn.Rollback()
		}
		return nil, mutErr
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any), nil
}

// expectedBossAttackDamage mirrors bossActActionGo's plain attack formula
// for the fixture (body 40, realm 2, no formation, phase 0 of the template)
// and the given equipment attack.
func expectedBossAttackDamage(userID, equipAttack int64) int64 {
	phase := bossTemplatesGo[bugslayerBossTemplate].Phases[0]
	roll := stablePercentGo(int64(1), int64(1), userID, "attack", int64(0))
	base := int64(40) + 2 + equipAttack
	return max64(1, base+1+(100-roll)/20-phase.Defense)
}

func bossParticipantRow(t *testing.T, path string, userID int64) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	r, err := conn.Execute(`SELECT guard,total_damage,acted_round FROM boss_participants WHERE encounter_id=1 AND user_id=?`, []any{userID})
	if err != nil {
		t.Fatal(err)
	}
	row := firstRowMap(r)
	if row == nil {
		t.Fatalf("participant %d missing", userID)
	}
	return row
}

func eventsContain(out map[string]any, needle string) bool {
	events, _ := out["events"].([]string)
	for _, ev := range events {
		if strings.Contains(ev, needle) {
			return true
		}
	}
	return false
}

func TestBossActAppliesBugslayerPassiveOnAttack(t *testing.T) {
	swordUser := pickBossHitUserID(901)
	plainUser := pickBossHitUserID(swordUser + 1)
	path := setupBugslayerBossDB(t, swordUser, plainUser)
	out, err := callBossAct(t, path, swordUser)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !eventsContain(out, bugslayerPassiveName) {
		t.Fatalf("expected a %q event, got %v", bugslayerPassiveName, out["events"])
	}
	row := bossParticipantRow(t, path, swordUser)
	if i64(row["guard"]) != 1 {
		t.Fatalf("guard = %v, want 1 (the passive must disrupt the next boss hit)", row["guard"])
	}
	want := expectedBossAttackDamage(swordUser, 5) + bugslayerPassiveBonusDamage
	if got := i64(row["total_damage"]); got != want {
		t.Fatalf("total_damage = %d, want %d (formula + %d passive bonus)", got, want, bugslayerPassiveBonusDamage)
	}
}

func TestBossActNoBugslayerPassiveWithoutSword(t *testing.T) {
	swordUser := pickBossHitUserID(901)
	plainUser := pickBossHitUserID(swordUser + 1)
	path := setupBugslayerBossDB(t, swordUser, plainUser)
	out, err := callBossAct(t, path, plainUser)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if eventsContain(out, bugslayerPassiveName) {
		t.Fatalf("passive must not fire without the sword, got %v", out["events"])
	}
	row := bossParticipantRow(t, path, plainUser)
	if i64(row["guard"]) != 0 {
		t.Fatalf("guard = %v, want 0", row["guard"])
	}
	if got, want := i64(row["total_damage"]), expectedBossAttackDamage(plainUser, 0); got != want {
		t.Fatalf("total_damage = %d, want %d (plain formula)", got, want)
	}
}
