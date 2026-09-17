package game

import (
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// The last lesson (v1.0.0-rc.34). These hold that the head speaks only at
// home; that a pass qualifies every trade, hands over the house's manual and
// keepsake, raises standing and writes the chronicle; that a fail costs one
// world day and nothing else; that a pass is refused again in the same life
// and allowed in the next; that a trade already held is never lowered; that
// the modifier reads the trade's level and the standing cap; and that a
// graduate of the path is handed the stage at the door. The dice are lent,
// never rolled: the test character carries no attributes, so the two dice
// decide the outcome alone.

// lessonDB is the household fixture with the tables the lesson touches, in
// production's shape, and the head's title on the family row.
func lessonDB(t *testing.T) string {
	t.Helper()
	path := householdDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
ALTER TABLE birth_families ADD COLUMN head_title TEXT NOT NULL DEFAULT 'Patriarch';
CREATE TABLE IF NOT EXISTS event_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS character_manuals(user_id INTEGER NOT NULL, manual_id TEXT NOT NULL, mastery INTEGER NOT NULL DEFAULT 0, practice INTEGER NOT NULL DEFAULT 0, learned_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(user_id, manual_id), FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS soul_legacy(user_id INTEGER PRIMARY KEY, incarnation_count INTEGER NOT NULL DEFAULT 1, legacy_points INTEGER NOT NULL DEFAULT 0, memory_seed INTEGER NOT NULL DEFAULT 0, talent_echo INTEGER NOT NULL DEFAULT 0, law_echo INTEGER NOT NULL DEFAULT 0, insight_echo INTEGER NOT NULL DEFAULT 0, karmic_fortune INTEGER NOT NULL DEFAULT 0, special_trait TEXT NOT NULL DEFAULT '', awakened_memory INTEGER NOT NULL DEFAULT 0, past_lives_json TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS faction_reputation(user_id INTEGER NOT NULL, faction_key TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0, last_reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL, PRIMARY KEY(user_id,faction_key), FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS inventory(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, item_id), FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS character_recipes(user_id INTEGER NOT NULL, recipe TEXT NOT NULL, learned_game_minute INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id, recipe), FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
UPDATE characters SET attributes_json='{"body":2,"agility":2,"spirit":2,"insight":2,"will":2,"presence":2,"heart":2}' WHERE user_id=42;
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func lessonAt(t *testing.T, path string, gameMinute int64) (map[string]any, error) {
	t.Helper()
	catalog := districtCatalog(t)
	return householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
		return familyLessonActionGo(c, catalog, 42, payload(map[string]any{"game_minute": gameMinute}))
	})
}

// highDice lends tens; lowDice lends ones.
func highDice(n int) int { return n - 1 }
func lowDice(int) int    { return 0 }

func TestTheHeadSpeaksOnlyAtHome(t *testing.T) {
	path := lessonDB(t)
	householdFamily(t, path, 42, "Greenriver Town")
	_, err := lessonAt(t, path, 1000)
	if err == nil || !strings.Contains(err.Error(), "asked for at home") {
		t.Fatalf("away from home: err=%v, want the at-home refusal", err)
	}
}

func TestAPassedLessonQualifiesEveryTradeAndHandsOverTheHouse(t *testing.T) {
	defer gamerng.UseRoller(highDice)()
	path := lessonDB(t)
	fid := householdFamily(t, path, 42, "home")
	out, err := lessonAt(t, path, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if out["outcome"] != "pass" {
		t.Fatalf("outcome=%v with the best dice, want pass (check %v)", out["outcome"], out["check"])
	}
	for _, trade := range householdLessonTrades {
		level := actionScalar(t, path, `SELECT level FROM profession_progress WHERE user_id=42 AND profession=?`, trade)
		if level == nil || i64(level) != 0 {
			t.Errorf("%s: level=%v, want a level-0 record", trade, level)
		}
		methods := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_recipes cr WHERE cr.user_id=42 AND cr.source=? AND cr.recipe IN (SELECT recipe FROM character_recipes WHERE user_id=42)`, householdLessonSource))
		if methods == 0 {
			t.Errorf("%s: no method was taught by the lesson", trade)
		}
	}
	taughtTrades := map[string]bool{}
	for _, row := range out["trades"].([]map[string]any) {
		taughtTrades[row["profession"].(string)] = true
		if row["profession"] != "Forging" && i64(row["new_methods"]) == 0 {
			t.Errorf("%v: the lesson reports no new methods for a trade the send-off never taught", row["profession"])
		}
	}
	if len(taughtTrades) != 4 {
		t.Fatalf("trades named: %v, want all four", taughtTrades)
	}
	manual := out["manual"].(map[string]any)
	if got := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, manual["item_id"])); got != 1 {
		t.Errorf("manual item %v: quantity=%d, want 1", manual["item_id"], got)
	}
	if got := actionScalar(t, path, `SELECT mastery FROM character_manuals WHERE user_id=42 AND manual_id=?`, manual["key"]); got == nil {
		t.Errorf("the manual %v was not studied once", manual["key"])
	}
	if manual["first_study"] != true {
		t.Errorf("first_study=%v, want true", manual["first_study"])
	}
	if got := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, out["keepsake"])); got != 1 {
		t.Errorf("keepsake %v: quantity=%d, want 1", out["keepsake"], got)
	}
	if got := i64(actionScalar(t, path, `SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key=?`, householdStandingKey(fid))); got != householdLessonStanding {
		t.Errorf("standing=%d, want %d", got, householdLessonStanding)
	}
	history := actionScalar(t, path, `SELECT history_json FROM birth_families WHERE family_id=?`, fid)
	if !strings.Contains(fmtAny(history), "took the last lesson from Patriarch") {
		t.Errorf("chronicle: %v", history)
	}
	if !strings.Contains(fmtAny(out["story"]), "famous") {
		t.Errorf("the story of the house is missing: %v", out["story"])
	}
	if _, err := lessonAt(t, path, 1001); err == nil || !strings.Contains(err.Error(), "taught you all this lesson holds") {
		t.Fatalf("a second ask in the same life: err=%v, want the once-per-life refusal", err)
	}
}

func TestAFailedLessonCostsOneWorldDayAndNothingElse(t *testing.T) {
	restore := gamerng.UseRoller(lowDice)
	path := lessonDB(t)
	householdFamily(t, path, 42, "home")
	out, err := lessonAt(t, path, 1000)
	if err != nil {
		restore()
		t.Fatal(err)
	}
	if out["outcome"] != "fail" {
		restore()
		t.Fatalf("outcome=%v with the worst dice, want fail (check %v)", out["outcome"], out["check"])
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM profession_progress WHERE user_id=42`)); n != 0 {
		t.Errorf("a failed lesson wrote %d trade records", n)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42`)); n != 0 {
		t.Errorf("a failed lesson handed over %d items", n)
	}
	if _, err := lessonAt(t, path, 1000+householdLessonRetryGameMinutes-1); err == nil || !strings.Contains(err.Error(), "hear you again in 1 in-world minutes") {
		restore()
		t.Fatalf("inside the day: err=%v, want the wait", err)
	}
	restore()
	defer gamerng.UseRoller(highDice)()
	out, err = lessonAt(t, path, 1000+householdLessonRetryGameMinutes)
	if err != nil || out["outcome"] != "pass" {
		t.Fatalf("after the day: err=%v outcome=%v, want a pass", err, out["outcome"])
	}
}

func TestTheLessonNeverLowersATradeAlreadyHeld(t *testing.T) {
	defer gamerng.UseRoller(highDice)()
	path := lessonDB(t)
	householdFamily(t, path, 42, "home")
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at) VALUES(42,'Forging',1,12,3,1,2,0)`)
	if _, err := lessonAt(t, path, 1000); err != nil {
		t.Fatal(err)
	}
	if level, xp := i64(actionScalar(t, path, `SELECT level FROM profession_progress WHERE user_id=42 AND profession='Forging'`)), i64(actionScalar(t, path, `SELECT xp FROM profession_progress WHERE user_id=42 AND profession='Forging'`)); level != 1 || xp != 12 {
		t.Fatalf("Forging after the lesson: level %d xp %d, want 1/12 untouched", level, xp)
	}
}

func TestANewLifeMayTakeTheLessonAgain(t *testing.T) {
	defer gamerng.UseRoller(highDice)()
	path := lessonDB(t)
	householdFamily(t, path, 42, "home")
	if out, err := lessonAt(t, path, 1000); err != nil || out["outcome"] != "pass" {
		t.Fatalf("first life: err=%v outcome=%v", err, out["outcome"])
	}
	if _, err := lessonAt(t, path, 1001); err == nil {
		t.Fatal("the same life took the lesson twice")
	}
	batch4Exec(t, path, `INSERT INTO soul_legacy(user_id,incarnation_count,updated_at) VALUES(42,2,0) ON CONFLICT(user_id) DO UPDATE SET incarnation_count=2`)
	out, err := lessonAt(t, path, 1002)
	if err != nil || out["outcome"] != "pass" {
		t.Fatalf("second life: err=%v outcome=%v, want the lesson given again", err, out["outcome"])
	}
	if out["manual"].(map[string]any)["first_study"] != false {
		t.Errorf("the manual was already studied; first_study=%v", out["manual"].(map[string]any)["first_study"])
	}
}

func TestTheModifierReadsTheTradeAndTheStandingCap(t *testing.T) {
	defer gamerng.UseRoller(lowDice)()
	path := lessonDB(t)
	fid := householdFamily(t, path, 42, "home")
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at) VALUES(42,'Forging',1,0,0,0,0,0)`)
	batch4Exec(t, path, `INSERT INTO faction_reputation(user_id,faction_key,score,last_reason,updated_at) VALUES(42,?,90,'',0)`, householdStandingKey(fid))
	out, err := lessonAt(t, path, 1000)
	if err != nil {
		t.Fatal(err)
	}
	check := out["check"].(map[string]any)
	// Body 2, Forging level 1, standing 90 capped at 2: modifier 5, and two
	// ones on the dice make 7 against 10.
	if i64(check["modifier"]) != 5 || i64(check["total"]) != 7 || i64(check["tn"]) != householdLessonTN {
		t.Fatalf("check=%v, want modifier 5, total 7, tn %d", check, householdLessonTN)
	}
	if out["attribute"] != "body" || i64(out["trade_level"]) != 1 || i64(out["standing_bonus"]) != 2 {
		t.Fatalf("attribute=%v trade_level=%v standing_bonus=%v", out["attribute"], out["trade_level"], out["standing_bonus"])
	}
}

func TestAGraduateOfThePathIsHandedTheStageAtTheDoor(t *testing.T) {
	defer gamerng.UseRoller(highDice)()
	path := lessonDB(t)
	householdFamily(t, path, 42, "home")
	catalog := districtCatalog(t)
	last := catalog.BeginnerPath[len(catalog.BeginnerPath)-1].QuestKey
	before := catalog.BeginnerPath[len(catalog.BeginnerPath)-2].QuestKey
	for _, key := range []string{before, last} {
		batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,description,objectives_json,rewards_json,status,created_at,updated_at,seed_json) VALUES(?,?,'d','[{"id":"x","type":"family_lesson","count":1}]','{}','approved',0,0,'{}')`, key, key)
	}
	// They finished the road home before the lesson existed.
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at) VALUES(42,?,'completed',0,0)`, before)
	out, err := lessonAt(t, path, 1000)
	if err != nil {
		t.Fatal(err)
	}
	handed := out["handed_over"].([]string)
	if len(handed) != 1 || handed[0] != last {
		t.Fatalf("handed_over=%v, want [%s]", handed, last)
	}
	if status := actionScalar(t, path, `SELECT status FROM character_quests WHERE user_id=42 AND quest_key=?`, last); fmtAny(status) != "active" {
		t.Fatalf("the stage is %v, want active", status)
	}
}
