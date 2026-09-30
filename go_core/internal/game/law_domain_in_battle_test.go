package game

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// Whose effect a Law technique is, and how often it lands (v1.12.3).
//
// `combat.technique` put every effect a technique carried onto the opponent,
// but only `lawControlCategory` effects describe a target: `space_domain` and
// `world_collapse` are Domains, the caster's own ground, and
// `lawTechniqueAction` has always written them onto the caster. So a
// World Collapse pressed in a fight put combat +6 and escape +4 on the
// *opponent* - which made the player's own flee four harder. And a control
// effect was summed with `+=`, with no in-battle cooldown on the technique, so
// the same lockdown cast again and again drove the opponent's agility to minus
// forty. The raid skipped World Collapse's personal-world requirement, which
// the battle and the out-of-battle cast both hold.
//
// The Domain tests drive the shipped catalogue, not a fixture that rewrote the
// link (rc.58): the technique and its effect are the content's own.

func domainBattleDB(t *testing.T) string {
	t.Helper()
	path := setupLawControlDB(t, true)
	batch4Exec(t, path, `CREATE TABLE personal_worlds (
		user_id INTEGER PRIMARY KEY, location_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, stability INTEGER NOT NULL DEFAULT 1,
		laws_json TEXT NOT NULL DEFAULT '{}', access_mode TEXT NOT NULL DEFAULT 'private', created_at REAL NOT NULL, updated_at REAL NOT NULL,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	// Dao Saint with the whole of Space Law: world_collapse's own floors.
	batch4Exec(t, path, `UPDATE characters SET realm_index=30 WHERE user_id=901`)
	batch4Exec(t, path, `UPDATE law_progress SET comprehension=100 WHERE user_id=901 AND law_id='space'`)
	return path
}

func giveAPersonalWorld(t *testing.T, path string) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO personal_worlds(user_id,location_key,name,created_at,updated_at) VALUES(901,'personal_world:901','Folded Seam',0,0)`)
}

func shippedTechnique(t *testing.T, path, technique string) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 1, "technique": technique, "game_minute": 100})
	mut, err := combatTechniqueAction(conn, crossingCatalog(t), 901, raw)
	if err != nil {
		return nil, err
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any), nil
}

// fleeModifier is the modifier the player's flee rolls at, read off the roll.
func fleeModifier(t *testing.T, path string) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 1, "style": "flee", "game_minute": 100})
	mut, err := combatTurnAction(conn, crossingCatalog(t), 901, raw)
	if err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return rollModifier(t, mut.Result.(map[string]any), "player_roll")
}

func opponentModsJSON(t *testing.T, path string) map[string]float64 {
	t.Helper()
	raw := actionScalar(t, path, "SELECT opponent_modifiers_json FROM battles WHERE battle_id=1")
	var mods map[string]float64
	if err := json.Unmarshal([]byte(raw.(string)), &mods); err != nil {
		t.Fatal(err)
	}
	return mods
}

func TestADomainInBattleIsTheCastersOwnAndNotTheOpponents(t *testing.T) {
	path := domainBattleDB(t)
	giveAPersonalWorld(t, path)
	baseline := fleeModifier(t, domainBattleFreshWithWorld(t))

	out, err := shippedTechnique(t, path, "world_collapse")
	if err != nil {
		t.Fatalf("World Collapse was refused with a personal world and every floor met: %v", err)
	}
	if !bval(out["roll"].(map[string]any), "success") {
		t.Fatal("the fixture's attributes of 500 must make the technique certain")
	}
	if mods := opponentModsJSON(t, path); len(mods) != 0 {
		t.Fatalf("a Domain was written onto the opponent: %v", mods)
	}
	if target := out["effect_target"]; target != "user" {
		t.Fatalf("the reply says the effect went to %v, want the user", target)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM active_effects WHERE user_id=901 AND effect_key='world_collapse' AND source_type='law'`)); n != 1 {
		t.Fatalf("the caster holds %d World Collapse rows, want 1: it was not written on the user", n)
	}
	// The symptom: the opponent's escape +4 made the player's own flee four
	// harder. The caster's escape +4 and combat +6 make it easier.
	if helped := fleeModifier(t, path); helped <= baseline {
		t.Fatalf("fleeing after World Collapse rolls at modifier %d against %d without it; the Domain still counts against the caster", helped, baseline)
	}
}

// domainBattleFreshWithWorld is a second, untouched battle to read a baseline
// flee modifier off (a flee can end the battle, so one DB cannot serve both).
func domainBattleFreshWithWorld(t *testing.T) string {
	t.Helper()
	path := domainBattleDB(t)
	giveAPersonalWorld(t, path)
	return path
}

func TestAControlEffectStillDebuffsTheOpponentAndNotTheCaster(t *testing.T) {
	path := setupLawControlDB(t, true)
	out := lawControlTechnique(t, path)
	if out["effect_target"] != "opponent" {
		t.Fatalf("a control effect reports going to %v, want the opponent", out["effect_target"])
	}
	mods := opponentModsJSON(t, path)
	if mods["agility"] != -3 || mods["escape_bonus"] != -5 {
		t.Fatalf("the lockdown's modifiers did not reach the opponent: %v", mods)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM active_effects WHERE user_id=901`)); n != 0 {
		t.Fatalf("a control effect wrote %d row(s) on the caster", n)
	}
}

func TestTheSameControlEffectLandsOncePerBattle(t *testing.T) {
	path := setupLawControlDB(t, true)
	first := lawControlTechnique(t, path)
	second := lawControlTechnique(t, path)
	third := lawControlTechnique(t, path)
	if first["effect_repeated"] != false || second["effect_repeated"] != true || third["effect_repeated"] != true {
		t.Fatalf("effect_repeated read %v / %v / %v, want false then true then true", first["effect_repeated"], second["effect_repeated"], third["effect_repeated"])
	}
	mods := opponentModsJSON(t, path)
	if mods["agility"] != -3 || mods["escape_bonus"] != -5 {
		t.Fatalf("three casts of one lockdown left the opponent at %v; the debuff stacked (want agility -3, escape_bonus -5)", mods)
	}
	for _, reply := range []map[string]any{first, second, third} {
		shown, _ := reply["opponent_modifiers"].(map[string]float64)
		for stat := range shown {
			if strings.HasPrefix(stat, opponentEffectMark) {
				t.Fatalf("the engine's record of a landed effect (%s) reached the reply a player reads: %v", stat, shown)
			}
		}
	}
	// And the counter-attack reader sees one lockdown, not three.
	debuffed := setupLawControlDB(t, true)
	lawControlTechnique(t, debuffed)
	lawControlTechnique(t, debuffed)
	lawControlTechnique(t, debuffed)
	plain := setupLawControlDB(t, true)
	var baseline, weakened int64
	for i := 0; i < 6 && baseline == 0; i++ {
		if out := lawControlTurn(t, plain, "attack"); out["counter_roll"] != nil {
			baseline = rollModifier(t, out, "counter_roll")
		}
	}
	for i := 0; i < 12 && weakened == 0; i++ {
		if out := lawControlTurn(t, debuffed, "attack"); out["counter_roll"] != nil {
			weakened = rollModifier(t, out, "counter_roll")
		}
	}
	if weakened != baseline-3 {
		t.Fatalf("after three casts the counter-attack rolled at %d against %d unhindered; one lockdown is agility -3", weakened, baseline)
	}
}

// World Collapse needs somewhere to collapse, in a fight and in a raid alike.
func TestWorldCollapseNeedsAPersonalWorldInABattle(t *testing.T) {
	path := domainBattleDB(t)
	if _, err := shippedTechnique(t, path, "world_collapse"); err == nil || !strings.Contains(err.Error(), "personal world") {
		t.Fatalf("World Collapse in a battle with no personal world was not refused: %v", err)
	}
}

func TestWorldCollapseNeedsAPersonalWorldInARaid(t *testing.T) {
	path := setupSoloRaidDB(t)
	batch4Exec(t, path, `CREATE TABLE personal_worlds (
		user_id INTEGER PRIMARY KEY, location_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, stability INTEGER NOT NULL DEFAULT 1,
		laws_json TEXT NOT NULL DEFAULT '{}', access_mode TEXT NOT NULL DEFAULT 'private', created_at REAL NOT NULL, updated_at REAL NOT NULL,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	batch4Exec(t, path, `UPDATE characters SET realm_index=30 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(42,'space',100,0,0)`)
	out, err := soloBoarStart(t, path, 42)
	if err != nil {
		t.Fatalf("the raid did not start: %v", err)
	}
	strike := func() error {
		raw, _ := json.Marshal(map[string]any{"encounter_id": out["encounter_id"], "style": "technique", "technique": "world_collapse"})
		return crossingApply(t, path, func(conn *storage.Conn) error {
			_, err := bossActActionGo(conn, crossingCatalog(t), 42, raw)
			return err
		})
	}
	if err := strike(); err == nil || !strings.Contains(err.Error(), "personal world") {
		t.Fatalf("World Collapse in a raid with no personal world was not refused: %v", err)
	}
	batch4Exec(t, path, `INSERT INTO personal_worlds(user_id,location_key,name,created_at,updated_at) VALUES(42,'personal_world:42','Folded Seam',0,0)`)
	if err := strike(); err != nil {
		t.Fatalf("World Collapse in a raid was refused with a personal world and every floor met: %v", err)
	}
}

// Three doors use a Law technique and all three ask one helper for its ground.
// The behavioural tests above pass against a tree where one door has stopped
// asking, so the wire is read by AST: the out-of-battle cast, the battle and
// the raid each call it, and none keeps its own personal-world lookup.
func TestEveryLawTechniqueDoorAsksForItsGround(t *testing.T) {
	want := map[string]string{
		"lawTechniqueAction":    "law_technique_actions.go",
		"combatTechniqueAction": "combat_actions.go",
		"bossActActionGo":       "group_combat_actions.go",
	}
	fset := token.NewFileSet()
	for fn, file := range want {
		parsed, err := parser.ParseFile(fset, file, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		found, ownLookup := false, false
		for _, decl := range parsed.Decls {
			d, ok := decl.(*ast.FuncDecl)
			if !ok || d.Name.Name != fn {
				continue
			}
			ast.Inspect(d, func(n ast.Node) bool {
				switch x := n.(type) {
				case *ast.CallExpr:
					if id, ok := x.Fun.(*ast.Ident); ok && id.Name == "requireLawTechniqueGroundTx" {
						found = true
					}
				case *ast.BasicLit:
					if strings.Contains(x.Value, "personal_worlds") {
						ownLookup = true
					}
				}
				return true
			})
		}
		if !found {
			t.Errorf("%s no longer asks requireLawTechniqueGroundTx for World Collapse's personal world (%s)", fn, file)
		}
		if ownLookup {
			t.Errorf("%s keeps its own personal-world lookup; the ground has one door", fn)
		}
	}
}
