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
	// The six anonymise tables, a player family's members and soul_legacy.
	// Column names, NOT NULL **and the foreign keys** match production: the
	// sweep deletes `characters` first (it walks tables alphabetically), and in
	// production that delete fires `player_families`' ON DELETE CASCADE and the
	// SET NULLs on the rest. A fixture without them accepted a release that ran
	// after the sweep, which production would have turned into a founder's
	// whole house deleted - the `npc_consignments` lesson (v1.0.14).
	if err := conn.ExecScript(`
CREATE TABLE world_history_events(history_id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',summary TEXT NOT NULL DEFAULT '',visibility TEXT NOT NULL DEFAULT 'public',actor_key TEXT NOT NULL DEFAULT '',actor_name TEXT NOT NULL DEFAULT '',target_key TEXT NOT NULL DEFAULT '',target_name TEXT NOT NULL DEFAULT '',related_user_id INTEGER,game_minute INTEGER NOT NULL DEFAULT 0);
CREATE TABLE player_families(family_id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL DEFAULT '',founder_user_id INTEGER NOT NULL,
	FOREIGN KEY(founder_user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE player_family_members(family_id INTEGER NOT NULL,user_id INTEGER NOT NULL UNIQUE,seniority_order INTEGER NOT NULL,joined_at REAL NOT NULL DEFAULT 0,
	PRIMARY KEY(family_id,user_id),FOREIGN KEY(family_id) REFERENCES player_families(family_id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE quest_definitions(quest_key TEXT PRIMARY KEY,title TEXT NOT NULL DEFAULT '',owner_user_id INTEGER,
	FOREIGN KEY(owner_user_id) REFERENCES characters(user_id) ON DELETE SET NULL);
CREATE TABLE sect_manors(manor_id INTEGER PRIMARY KEY AUTOINCREMENT,sect_id TEXT NOT NULL DEFAULT '',founded_by_user_id INTEGER,
	FOREIGN KEY(founded_by_user_id) REFERENCES characters(user_id) ON DELETE SET NULL);
CREATE TABLE world_crossings(location_key TEXT PRIMARY KEY,name TEXT NOT NULL DEFAULT '',opened_by_user_id INTEGER,
	FOREIGN KEY(opened_by_user_id) REFERENCES characters(user_id) ON DELETE SET NULL);
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

// TestTheAllowanceSurvivesTheActionItBounds spends every reset an account has
// and then asks for one more. The count has to survive the sweep that counts
// it, or the bound is not a bound - it is kept by the same predicate that
// keeps it readable to a GM, so it is one thing that can break, not two.
func TestTheAllowanceSurvivesTheActionItBounds(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	for i := 0; i < characterResetAllowance; i++ {
		makeCultivator(t, path, world, 77, fmt.Sprintf("Lin %d", i), int64(200+i*100))
		result, err := resetCultivator(t, path, world, 77, fmt.Sprintf("reset-loop-%d", i))
		if err != nil {
			t.Fatalf("reset %d refused: %v", i+1, err)
		}
		if got := storage.ParseInt(result["resets_used"]); got != int64(i+1) {
			t.Fatalf("reset %d reported resets_used=%d; the allowance was erased by the action it bounds", i+1, got)
		}
		if got := storage.ParseInt(result["resets_remaining"]); got != int64(characterResetAllowance-i-1) {
			t.Fatalf("reset %d reported resets_remaining=%d", i+1, got)
		}
	}
	makeCultivator(t, path, world, 77, "Lin Last", 900)
	_, err := resetCultivator(t, path, world, 77, "reset-over")
	if err == nil {
		t.Fatalf("a %dth reset was allowed", characterResetAllowance+1)
	}
	if !strings.Contains(err.Error(), "all this world allows") {
		t.Fatalf("refusal=%q", err)
	}
	// The character that was refused is still standing: a refused reset
	// removes nothing.
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=77"); got != 1 {
		t.Fatalf("a refused reset removed the character anyway")
	}
}

// TestAResetIsNotASmallSamsara is the distinction between the two systems,
// held rather than assumed.
//
// Samsara is what **death** opens, and it deliberately remembers: the memory
// seed, the talent, law and insight echoes, the legacy points, the craft echo
// and a family lineage rolled off the dead life's karma all ride into the next
// life. `soul_legacy` is where that lives, and `past_lives_json` is the record
// itself.
//
// A reset must keep none of it. That is true today only because `soul_legacy`
// carries no anonymise disposition and no keep, so the sweep deletes it like
// any other row of the account's - which means the property is real but
// invisible, and a keep added to that table later would quietly turn a reset
// into a cut-price samsara with no test going red. This is the test that goes
// red.
func TestAResetIsNotASmallSamsara(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin Remembering", 200)
	// A soul with a past: echoes, legacy points and a life on the record.
	resetExec(t, path, `INSERT INTO soul_legacy(user_id,incarnation_count,legacy_points,memory_seed,`+
		`talent_echo,law_echo,insight_echo,special_trait,past_lives_json) VALUES(`+
		`77,1,140,60,55,40,35,'Old Soul','[{"name":"Lin Before","realm_index":7}]');`)

	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM soul_legacy WHERE user_id=77"); got != 1 {
		t.Fatalf("the fixture wrote no soul legacy to forget")
	}
	if _, err := resetCultivator(t, path, world, 77, "reset-forgets"); err != nil {
		t.Fatal(err)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM soul_legacy WHERE user_id=77"); got != 0 {
		t.Fatalf("a reset left %d soul_legacy row(s); that is samsara's memory, and a reset keeps none of it", got)
	}
	// And the account starts again from nothing: the next life reads
	// incarnation 1, not 2.
	if got, err := characterIncarnationCountTx(mustOpen(t, path), 77); err != nil {
		t.Fatal(err)
	} else if got != 1 {
		t.Fatalf("after a reset the soul reads incarnation %d; a reset is not a rebirth", got)
	}
}

func mustOpen(t *testing.T, path string) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn
}

// TestWhatTheWorldKeepsIsReleasedNotRefused is the owner's call in v1.0.14:
// every shipped anonymise column is released. The shared thing stays in the
// world and only the link to this account goes. One case per table, because
// the release walks them off the live schema and a single case would pass with
// the walk broken.
func TestWhatTheWorldKeepsIsReleasedNotRefused(t *testing.T) {
	for _, tc := range []struct {
		table, column, insert, what string
	}{
		{"quest_definitions", "owner_user_id", "INSERT INTO quest_definitions(quest_key,title,owner_user_id) VALUES('drafted','A draft',77)", "a quest they wrote"},
		{"sect_manors", "founded_by_user_id", "INSERT INTO sect_manors(sect_id,founded_by_user_id) VALUES('azure',77)", "the sect manor they founded"},
		{"world_crossings", "opened_by_user_id", "INSERT INTO world_crossings(location_key,name,opened_by_user_id) VALUES('greenriver','A Gate',77)", "the gate between worlds they opened"},
		{"npc_graves", "claimed_by_user_id", "INSERT INTO npc_graves(npc_name,claimed_by_user_id,claimed_game_minute) VALUES('A herbalist',77,10)", "a grave they emptied"},
	} {
		t.Run(tc.table, func(t *testing.T) {
			path := setupCharacterResetDB(t)
			world := batch4WorldPath(t)
			makeCultivator(t, path, world, 77, "Lin Marked", 200)
			resetExec(t, path, tc.insert+";")

			result, err := resetCultivator(t, path, world, 77, "reset-released-"+tc.table)
			if err != nil {
				t.Fatalf("a cultivator named in %s still cannot reset: %v", tc.table, err)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM "+tc.table); got != 1 {
				t.Fatalf("the reset took the %s row out of the world (%d left)", tc.table, got)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM "+tc.table+" WHERE "+tc.column+"=77"); got != 0 {
				t.Fatalf("the %s row still names the reset account", tc.table)
			}
			left := fmt.Sprint(result["left_behind"])
			if !strings.Contains(left, tc.what) {
				t.Errorf("the reply does not say what was left behind: %s", left)
			}
		})
	}
}

// TestAFounderWhoResetsIsSucceeded holds the family to the rule a founder
// walking out already has: the most senior who stays takes the house. Its
// drill is the one this fixture's foreign keys exist for - release after the
// sweep and the characters delete cascades the whole family away, the other
// member's place in it included.
func TestAFounderWhoResetsIsSucceeded(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin Founder", 200)
	makeCultivator(t, path, world, 88, "Lin Heir", 210)
	resetExec(t, path, `INSERT INTO player_families(family_id,name,founder_user_id) VALUES(5,'The Lin House',77);
		INSERT INTO player_family_members(family_id,user_id,seniority_order) VALUES(5,77,1),(5,88,2);`)

	result, err := resetCultivator(t, path, world, 77, "reset-founder")
	if err != nil {
		t.Fatalf("a family founder cannot reset: %v", err)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM player_families WHERE family_id=5 AND founder_user_id=88"); got != 1 {
		t.Fatalf("the house did not pass to its most senior remaining member (%d rows)", got)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM player_family_members WHERE family_id=5 AND user_id=88"); got != 1 {
		t.Fatalf("the heir lost their place in the house when the founder reset")
	}
	family, _ := result["family"].(map[string]any)
	if family == nil || i64(family["heir_user_id"]) != 88 || family["dissolved"] != false {
		t.Errorf("the reply does not name the heir: %v", result["family"])
	}
}

func TestALoneFounderWhoResetsDissolvesTheHouse(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin Alone", 200)
	resetExec(t, path, `INSERT INTO player_families(family_id,name,founder_user_id) VALUES(5,'The Lin House',77);
		INSERT INTO player_family_members(family_id,user_id,seniority_order) VALUES(5,77,1);`)

	result, err := resetCultivator(t, path, world, 77, "reset-alone")
	if err != nil {
		t.Fatalf("a lone founder cannot reset: %v", err)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM player_families"); got != 0 {
		t.Fatalf("a house with nobody left in it outlived its only member")
	}
	family, _ := result["family"].(map[string]any)
	if family == nil || family["dissolved"] != true {
		t.Errorf("the reply does not say the house was dissolved: %v", result["family"])
	}
}

// TestAnUnreleasedMarkStillRefuses is the other half of the release: a new
// anonymise column is a mark until somebody decides what a reset does with
// it. It also holds the refusal's wording - the v1.0.14 report was a refusal
// that named a database column and said nothing about whether waiting helped.
func TestAnUnreleasedMarkStillRefuses(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Xie Kormaq", 200)
	resetExec(t, path, `CREATE TABLE shared_shrines(shrine_id INTEGER PRIMARY KEY,owner_user_id INTEGER);
		INSERT INTO shared_shrines(owner_user_id) VALUES(77);`)
	erasureAnonymise["shared_shrines.owner_user_id"] = "test: a shrine other players pray at"
	defer delete(erasureAnonymise, "shared_shrines.owner_user_id")

	_, err := resetCultivator(t, path, world, 77, "reset-unreleased")
	if err == nil {
		t.Fatal("an anonymise column nobody released let the reset through")
	}
	text := err.Error()
	for _, want := range []string{"Xie Kormaq cannot be reset", "waiting will not change that",
		"There is no timer", "• they are named in shared_shrines"} {
		if !strings.Contains(text, want) {
			t.Errorf("the refusal does not say %q:\n%s", want, text)
		}
	}
	if strings.Contains(text, "owner_user_id") {
		t.Errorf("the refusal still names a database column to a player:\n%s", text)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=77"); got != 1 {
		t.Fatalf("a refused reset still removed the character")
	}
}

// TestAGateForgetsItsMaker: a raised gate is named from a template,
// "{character}'s Ascension Gate", and other cultivators' history quotes it
// when they walk through - so the name goes from the gate and from their
// rows too, while their rows stay theirs.
func TestAGateForgetsItsMaker(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Xie Kormaq", 200)
	resetExec(t, path, `INSERT INTO world_crossings(location_key,name,opened_by_user_id) VALUES('greenriver','Xie Kormaq''s Ascension Gate',77);
		INSERT INTO world_history_events(event_type,title,visibility,related_user_id) VALUES
		('world_crossing_used','Mo Walker stepped through the Xie Kormaq''s Ascension Gate','public',88);`)

	if _, err := resetCultivator(t, path, world, 77, "reset-gate"); err != nil {
		t.Fatalf("the gate's maker cannot reset: %v", err)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM world_crossings WHERE name='Unknown Cultivator''s Ascension Gate' AND opened_by_user_id IS NULL"); got != 1 {
		t.Errorf("the gate still carries its maker's name or link")
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM world_history_events WHERE related_user_id=88 AND title='Mo Walker stepped through the Unknown Cultivator''s Ascension Gate'"); got != 1 {
		t.Errorf("another cultivator's record of the gate still names its maker")
	}
}

// TestHistoryDoesNotStopAReset is the owner's call in v1.0.14. A new
// cultivator's first discovery wrote a history row naming them and that row
// alone made the reset refuse for ever. History no longer refuses: a row only
// this character could see goes with them, and a public one stays in the world
// with the link to the account cut - what an erasure does to it.
func TestHistoryDoesNotStopAReset(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Xie Kormaq", 200)
	resetExec(t, path, `INSERT INTO world_history_events(event_type,title,visibility,related_user_id,game_minute) VALUES
		('discovery','Xie Kormaq discovered Ironbanner City','participant',77,100),
		('trade','A trade at the inn','participant',77,200),
		('world_event','A Dragon Appears','public',77,300),
		('duel','Somebody else''s duel','public',88,400);
	INSERT INTO world_history_events(event_type,title,summary,visibility,actor_key,actor_name,target_key,target_name,related_user_id,game_minute) VALUES
		('major_battle','Major battle: Xie Kormaq defeated Wolf King',
		 'A high-stakes battle at Ashenwall ended with Wolf King''s death at the hands of Xie Kormaq. Xie Kormaq walked away.',
		 'public','77','Xie Kormaq','Wolf King','Wolf King',77,500);`)

	result, err := resetCultivator(t, path, world, 77, "reset-history")
	if err != nil {
		t.Fatalf("history still stops a reset: %v", err)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM world_history_events WHERE visibility<>'public'"); got != 0 {
		t.Errorf("%d private history row(s) outlived the character they belonged to", got)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM world_history_events WHERE title='A Dragon Appears' AND related_user_id IS NULL"); got != 1 {
		t.Errorf("the public row was not kept and unlinked (found %d)", got)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM world_history_events WHERE related_user_id=88"); got != 1 {
		t.Errorf("a reset touched another player's history")
	}
	// The deed stays and the doer does not: a kept public row must not go on
	// naming somebody this world no longer has (the owner's suggestion).
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM world_history_events WHERE title LIKE '%Xie Kormaq%' OR summary LIKE '%Xie Kormaq%' OR actor_name='Xie Kormaq' OR actor_key='77'"); got != 0 {
		t.Errorf("%d kept history row(s) still name the reset cultivator", got)
	}
	if got := resetScalarI(t, path, `SELECT COUNT(*) FROM world_history_events WHERE
		title='Major battle: an unknown cultivator defeated Wolf King'
		AND summary='A high-stakes battle at Ashenwall ended with Wolf King''s death at the hands of an unknown cultivator. An unknown cultivator walked away.'
		AND actor_name='An unknown cultivator' AND target_name='Wolf King' AND actor_key=''`); got != 1 {
		t.Errorf("the kept battle row was not rewritten to an unknown cultivator")
	}
	if left := fmt.Sprint(result["left_behind"]); !strings.Contains(left, "2 records in the world's history") {
		t.Errorf("the reply does not say what history was left behind: %s", left)
	}
	if i64(result["history_removed"]) != 2 || i64(result["history_unlinked"]) != 2 {
		t.Errorf("result reported removed=%v unlinked=%v, want 2 and 2",
			result["history_removed"], result["history_unlinked"])
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
