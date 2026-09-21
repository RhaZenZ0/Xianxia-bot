package game

// What a player may take back, and what the world keeps.
//
// The fixture is deliberately the character-creation one plus the six tables
// that carry an anonymise disposition, because the whole gate is a question
// about those tables and a fixture without them could only prove that an empty
// set is empty. Every one of them carries the column production carries.
//
// The completeness half - "is every anonymise column in the real 182-table
// schema found by this" - is not here for the same reason it is not in
// privacy_actions_test.go: the fixtures in this package build a schema by hand.
// That check bootstraps the real database and lives in
// tests/python/contracts/test_character_reset.py.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupCharacterResetDB(t *testing.T) string {
	t.Helper()
	path := setupCharacterCreationAuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// The six anonymise tables and soul_legacy. Column names and NOT NULL match
	// production, because the mark check reads the column and the sweep reads
	// the NOT NULL - a fixture that got either wrong would pass while
	// production refused.
	if err := conn.ExecScript(`
CREATE TABLE world_history_events(history_id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',related_user_id INTEGER);
CREATE TABLE player_families(family_id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL DEFAULT '',founder_user_id INTEGER NOT NULL);
CREATE TABLE quest_definitions(quest_key TEXT PRIMARY KEY,title TEXT NOT NULL DEFAULT '',owner_user_id INTEGER);
CREATE TABLE sect_manors(manor_id INTEGER PRIMARY KEY AUTOINCREMENT,sect_id TEXT NOT NULL DEFAULT '',founded_by_user_id INTEGER);
CREATE TABLE world_crossings(location_key TEXT PRIMARY KEY,opened_by_user_id INTEGER);
CREATE TABLE npc_graves(grave_id INTEGER PRIMARY KEY AUTOINCREMENT,npc_name TEXT NOT NULL DEFAULT '',claimed_by_user_id INTEGER,claimed_game_minute INTEGER);
CREATE TABLE soul_legacy(user_id INTEGER PRIMARY KEY,incarnation_count INTEGER NOT NULL DEFAULT 1,legacy_points INTEGER NOT NULL DEFAULT 0,memory_seed INTEGER NOT NULL DEFAULT 0,talent_echo INTEGER NOT NULL DEFAULT 0,law_echo INTEGER NOT NULL DEFAULT 0,insight_echo INTEGER NOT NULL DEFAULT 0,karmic_fortune INTEGER NOT NULL DEFAULT 0,special_trait TEXT NOT NULL DEFAULT '',past_lives_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL DEFAULT 0);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

// makeCultivator runs the real two-step creation, so every row a reset has to
// remove is a row creation actually wrote.
func makeCultivator(t *testing.T, path, world string, actor int64, name string, minute int64) map[string]any {
	t.Helper()
	optionsOut := creationApply(t, path, world, fmt.Sprintf("reset-options-%d-%s", actor, name),
		"character.family_options", actor, nil, map[string]any{"world_name": "Mortal World", "game_minute": minute})
	offers := batch4Result(t, optionsOut)["families"].([]familyOffer)
	createdOut := creationApply(t, path, world, fmt.Sprintf("reset-create-%d-%s", actor, name),
		"character.create", actor, nil, map[string]any{
			"discord_name": "Tester", "name": name, "concept": "Prove the Dao", "gender": "neutral",
			"path": "Sword Cultivator", "family_choice_id": offers[0].ChoiceID,
			"game_minute": minute + 1, "age_at_creation_years": 18,
		})
	return batch4Result(t, createdOut)
}

func resetCultivator(t *testing.T, path, world string, actor int64, actionID string) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion, ActionID: actionID,
		Operation: "character.reset", ActorID: actor, Payload: raw,
	})
	if err != nil {
		return nil, err
	}
	return batch4Result(t, out), nil
}

// resetExec stages a fixture row. It goes through ExecScript rather than
// Execute because `storage.Conn` opens an implicit transaction on a handler's
// first write and `Close` rolls it back - the exact shape v1.0.0-rc.38 found in
// `npcFound`, met here in a test helper, where it silently discarded every row
// these refusals are about and made all six of them pass as successes.
func resetExec(t *testing.T, path, statement string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(statement); err != nil {
		t.Fatal(err)
	}
}

func resetScalarI(t *testing.T, path, query string, args ...any) int64 {
	t.Helper()
	return storage.ParseInt(actionScalar(t, path, query, args...))
}

func TestAFreshCultivatorCanBeginAgain(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin First", 200)

	result, err := resetCultivator(t, path, world, 77, "reset-1")
	if err != nil {
		t.Fatalf("a cultivator who has done nothing was refused: %v", err)
	}
	if result["reset"] != true {
		t.Fatalf("reset=%v", result["reset"])
	}
	if got := storage.ParseInt(result["resets_used"]); got != 1 {
		t.Fatalf("resets_used=%d want 1", got)
	}
	if storage.ParseInt(result["rows_deleted"]) <= 0 {
		t.Fatalf("a reset that deleted nothing is not a reset: %v", result["rows_deleted"])
	}
	// Everything creation wrote about this person is gone.
	for _, table := range []string{
		"characters", "inventory", "currency_wallets", "storage_containers",
		"character_birth_family", "character_spiritual_roots", "character_physiques",
		"player_scene_state", "character_location_discoveries",
	} {
		if got := resetScalarI(t, path, fmt.Sprintf("SELECT COUNT(*) FROM %s WHERE user_id=77", table)); got != 0 {
			t.Fatalf("%s still holds %d row(s) after a reset", table, got)
		}
	}
	// The one exception, and it is the allowance: the reset's own record
	// survives and creation's does not.
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM event_log WHERE user_id=77"); got != 1 {
		t.Fatalf("event_log rows=%d want exactly the reset's own", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, "SELECT event_type FROM event_log WHERE user_id=77")); got != characterResetEvent {
		t.Fatalf("surviving event_log row is %q, not the allowance", got)
	}
	// And the point of the whole feature: /begin works again.
	makeCultivator(t, path, world, 77, "Lin Second", 400)
	if got := fmt.Sprint(actionScalar(t, path, "SELECT name FROM characters WHERE user_id=77")); got != "Lin Second" {
		t.Fatalf("second cultivator=%q", got)
	}
}

// TestTheRecordSurvivesTheActionItRecords is what is left of the allowance
// test. There is no limit any more (the world-mark gate is the one that
// protects other players, and a cultivator re-rolling their own first minute
// takes nothing from anybody), but the *count* still has to survive, because
// it is what a GM reads to see how often somebody has started over - and it is
// kept by the same predicate, so it is the same thing that can break.
func TestTheRecordSurvivesTheActionItRecords(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	for i := 0; i < 4; i++ {
		makeCultivator(t, path, world, 77, fmt.Sprintf("Lin %d", i), int64(200+i*100))
		result, err := resetCultivator(t, path, world, 77, fmt.Sprintf("reset-loop-%d", i))
		if err != nil {
			t.Fatalf("reset %d refused, and nothing limits them: %v", i+1, err)
		}
		if got := storage.ParseInt(result["resets_used"]); got != int64(i+1) {
			t.Fatalf("reset %d reported resets_used=%d; the record was erased by the action it records", i+1, got)
		}
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM event_log WHERE user_id=77 AND event_type=?",
		characterResetEvent); got != 4 {
		t.Fatalf("the reset log holds %d rows after four resets", got)
	}
}

// refusedMarks pulls the parenthesised list out of the mark refusal, so a test
// can assert which rows stopped the reset rather than merely that something
// did.
func refusedMarks(t *testing.T, err error) []string {
	t.Helper()
	text := err.Error()
	open := strings.Index(text, "(")
	close := strings.Index(text, ");")
	if open < 0 || close < open {
		t.Fatalf("refusal is not a mark refusal: %q", text)
	}
	var marks []string
	for _, part := range strings.Split(text[open+1:close], ",") {
		if part = strings.TrimSpace(part); part != "" {
			marks = append(marks, part)
		}
	}
	return marks
}

func TestAMarkTheWorldKeepsRefusesAReset(t *testing.T) {
	// One case per anonymise disposition, because the gate walks them off the
	// live schema and a single case would pass with the walk broken.
	for _, tc := range []struct {
		table  string
		insert string
	}{
		{"world_history_events", "INSERT INTO world_history_events(event_type,title,related_user_id) VALUES('duel','A duel',77)"},
		{"player_families", "INSERT INTO player_families(name,founder_user_id) VALUES('The Lin Clan',77)"},
		{"quest_definitions", "INSERT INTO quest_definitions(quest_key,title,owner_user_id) VALUES('drafted','A draft',77)"},
		{"sect_manors", "INSERT INTO sect_manors(sect_id,founded_by_user_id) VALUES('azure',77)"},
		{"world_crossings", "INSERT INTO world_crossings(location_key,opened_by_user_id) VALUES('greenriver',77)"},
		{"npc_graves", "INSERT INTO npc_graves(npc_name,claimed_by_user_id,claimed_game_minute) VALUES('A herbalist',77,10)"},
	} {
		t.Run(tc.table, func(t *testing.T) {
			path := setupCharacterResetDB(t)
			world := batch4WorldPath(t)
			makeCultivator(t, path, world, 77, "Lin Marked", 200)
			resetExec(t, path, tc.insert+";")

			_, err := resetCultivator(t, path, world, 77, "reset-marked-"+tc.table)
			if err == nil {
				t.Fatalf("a cultivator named in %s was allowed to un-exist", tc.table)
			}
			// Naming the table is not enough, and the drill is why: disabling
			// the disposition check makes *every* target a mark, so a refusal
			// listing all 100-odd of them still contains this one and a
			// `Contains` assertion passes against a broken gate. The refusal
			// has to name this mark and nothing else.
			marks := refusedMarks(t, err)
			if len(marks) != 1 || !strings.HasPrefix(marks[0], tc.table+".") {
				t.Fatalf("refusal named %v; want exactly the one mark in %s", marks, tc.table)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=77"); got != 1 {
				t.Fatalf("a refused reset still removed the character")
			}
		})
	}
}

func TestADeadCultivatorIsSamsarasNotTheResets(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin Departed", 200)
	resetExec(t, path, `UPDATE characters SET life_status='deceased' WHERE user_id=77;`)

	_, err := resetCultivator(t, path, world, 77, "reset-dead")
	if err == nil {
		t.Fatal("a dead cultivator was reset instead of reincarnated")
	}
	if !strings.Contains(err.Error(), "Samsara") {
		t.Fatalf("refusal=%q", err)
	}
}

func TestARebornSoulKeepsItsRecord(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin Reborn", 200)
	resetExec(t, path, `INSERT INTO soul_legacy(user_id,incarnation_count) VALUES(77,3);`)

	_, err := resetCultivator(t, path, world, 77, "reset-reborn")
	if err == nil {
		t.Fatal("a soul that has turned through Samsara was allowed to reset")
	}
	if !strings.Contains(err.Error(), "wheel") {
		t.Fatalf("refusal=%q", err)
	}
}

func TestAResetTakesBackTheHouseholdsWelcome(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	created := makeCultivator(t, path, world, 77, "Lin Welcomed", 200)
	familyID := storage.ParseInt(created["family_id"])
	family := created["family"].(BirthFamily)
	line := householdWelcomeLine(family.FamilyName, "Lin Welcomed")

	before := fmt.Sprint(actionScalar(t, path, "SELECT history_json FROM birth_families WHERE family_id=?", familyID))
	if !strings.Contains(before, "Lin Welcomed") {
		t.Fatalf("creation wrote no welcome line to remove: %s", before)
	}
	var beforeLines []string
	if err := json.Unmarshal([]byte(before), &beforeLines); err != nil {
		t.Fatal(err)
	}

	if _, err := resetCultivator(t, path, world, 77, "reset-welcome"); err != nil {
		t.Fatal(err)
	}

	after := fmt.Sprint(actionScalar(t, path, "SELECT history_json FROM birth_families WHERE family_id=?", familyID))
	if strings.Contains(after, "Lin Welcomed") {
		t.Fatalf("the household still remembers a cultivator who does not exist: %s", after)
	}
	var afterLines []string
	if err := json.Unmarshal([]byte(after), &afterLines); err != nil {
		t.Fatal(err)
	}
	// Exactly the one line, and the household's own past untouched.
	if len(afterLines) != len(beforeLines)-1 {
		t.Fatalf("history went from %d lines to %d", len(beforeLines), len(afterLines))
	}
	for _, entry := range afterLines {
		if entry == line {
			t.Fatalf("the welcome line survived: %q", entry)
		}
	}
}
