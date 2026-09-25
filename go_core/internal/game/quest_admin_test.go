package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// The GM's quest levers (v1.4.1). Reported from play: the household lesson was
// passed while "The Last Lesson" was not active, so its one report was lost,
// the lesson refused a second pass, and the stage sat at 0/1 for good. These
// hold that a GM can finish it through the same path a player's report takes -
// paid, chained and audited - and that the lever reaches nothing it should not.

// questAdminDB is the commission fixture with a two-stage chain held by 42:
// `stuck` is active and asks for two things, `next` is its follow-on.
func questAdminDB(t *testing.T) string {
	t.Helper()
	path := setupCommissionDB(t)
	conn := beginnerConn(t, path)
	if _, err := conn.Execute(`INSERT INTO quest_definitions(
        quest_key,title,description,objectives_json,rewards_json,status,giver_npc,seed_json,created_at,updated_at)
        VALUES('stuck','Stuck','','[{"id":"lesson","type":"family_lesson","count":1},{"id":"sit","type":"cultivate","count":3}]','{"insight_xp":5}','approved','','{"follow_on":"next"}',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	defineQuest(t, conn, "next", "", "")
	if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at,terms_json) VALUES(42,'stuck','active',0,0,?)`,
		[]any{`{"objectives":[{"id":"lesson","type":"family_lesson","count":1},{"id":"sit","type":"cultivate","count":3}],"rewards":{"insight_xp":5}}`}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func questStatus(t *testing.T, path string, uid int64, key string) (string, map[string]int64) {
	t.Helper()
	res, err := beginnerConn(t, path).Execute(`SELECT status,progress_json FROM character_quests WHERE user_id=? AND quest_key=?`, []any{uid, key})
	if err != nil {
		t.Fatal(err)
	}
	row := firstRowMap(res)
	if row == nil {
		return "", nil
	}
	progress := map[string]int64{}
	_ = json.Unmarshal([]byte(fmt.Sprint(row["progress_json"])), &progress)
	return fmt.Sprint(row["status"]), progress
}

func TestAGMCompletingAStuckQuestPaysAndHandsOverTheNext(t *testing.T) {
	path := questAdminDB(t)
	out, err := applyAdminRaw(t, path, "admin.player.quest_complete", 1, map[string]any{"user_id": 42, "quest_key": "stuck", "reason": "missed report"})
	if err != nil {
		t.Fatal(err)
	}
	result := out.Result.(map[string]any)
	status, progress := questStatus(t, path, 42, "stuck")
	if status != "completed" || progress["lesson"] != 1 || progress["sit"] != 3 {
		t.Fatalf("stuck is %s with %v, want completed with every objective full", status, progress)
	}
	if next, _ := questStatus(t, path, 42, "next"); next != "active" {
		t.Fatalf("the follow-on is %q, want active: a GM-finished quest must chain like any other (result %v)", next, result)
	}
	if granted, _ := result["rewards_granted"].(map[string]any); i64(granted["insight_xp"]) != 5 {
		t.Fatalf("rewards_granted=%v; the lever did not pay through the quest's own reward path", result["rewards_granted"])
	}
	rows := auditRows(t, path, "admin.player.quest_complete")
	if len(rows) != 1 || rows[0]["target"] != "user:42 quest:stuck" || rows[0]["reason"] != "missed report" {
		t.Fatalf("audit rows %v", rows)
	}
	if _, err := applyAdminRaw(t, path, "admin.player.quest_complete", 1, map[string]any{"user_id": 42, "quest_key": "stuck"}); err == nil || !strings.Contains(err.Error(), "not active") {
		t.Fatalf("a completed quest was completed again: %v", err)
	}
}

func TestAGMReportAdvancesOnlyTheNamedObjective(t *testing.T) {
	path := questAdminDB(t)
	if _, err := applyAdminRaw(t, path, "admin.player.quest_progress", 1, map[string]any{"user_id": 42, "quest_key": "stuck", "objective_type": "family_lesson"}); err != nil {
		t.Fatal(err)
	}
	status, progress := questStatus(t, path, 42, "stuck")
	if status != "active" || progress["lesson"] != 1 || progress["sit"] != 0 {
		t.Fatalf("stuck is %s with %v, want active with only the lesson reported", status, progress)
	}
	if _, err := applyAdminRaw(t, path, "admin.player.quest_progress", 1, map[string]any{"user_id": 42, "quest_key": "stuck", "objective_type": "combat_win"}); err == nil {
		t.Fatal("a report of a type the quest does not ask for was accepted")
	}
	if _, err := applyAdminRaw(t, path, "admin.player.quest_progress", 1, map[string]any{"user_id": 42, "quest_key": "stuck", "objective_type": "cultivate", "amount": 3}); err != nil {
		t.Fatal(err)
	}
	if status, _ := questStatus(t, path, 42, "stuck"); status != "completed" {
		t.Fatalf("the last objective reported left the quest %s", status)
	}
	if len(auditRows(t, path, "admin.player.quest_progress")) != 2 {
		t.Fatal("each landed report must write one audit row, and a refused one none")
	}
}

func TestAGMCannotAdvanceAQuestThePlayerDoesNotHold(t *testing.T) {
	path := questAdminDB(t)
	_, err := applyAdminRaw(t, path, "admin.player.quest_complete", 1, map[string]any{"user_id": 42, "quest_key": "next"})
	if err == nil || !strings.Contains(err.Error(), "does not hold") {
		t.Fatalf("completing an unheld quest: %v", err)
	}
	if held := heldQuests(t, beginnerConn(t, path), 42); len(held) != 1 {
		t.Fatalf("a refused lever wrote a quest row: %v", held)
	}
	if len(auditRows(t, path, "admin.player.quest_complete")) != 0 {
		t.Fatal("a refused lever wrote an audit row")
	}
}

func TestTheQuestLeversAreNotUndoable(t *testing.T) {
	for _, action := range []string{"admin.player.quest_complete", "admin.player.quest_progress"} {
		if _, ok := reversibleAdminActions[action]; ok {
			t.Fatalf("%s pays a reward; restoring one row's snapshot would leave the payment standing", action)
		}
	}
}

func TestTheQuestLeverReachesASnowflake(t *testing.T) {
	path := questAdminDB(t)
	const snowflake = int64(1456074443989188610)
	conn := beginnerConn(t, path)
	for _, table := range []string{"characters", "character_quests"} {
		if _, err := conn.Execute(`UPDATE `+table+` SET user_id=? WHERE user_id=42`, []any{snowflake}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	raw := []byte(fmt.Sprintf(`{"user_id":%d,"quest_key":"stuck"}`, snowflake))
	if _, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{Operation: "admin.player.quest_complete", ActorID: 1, Payload: raw}); err != nil {
		t.Fatalf("a Discord-sized id: %v", err)
	}
	if status, _ := questStatus(t, path, snowflake, "stuck"); status != "completed" {
		t.Fatalf("stuck is %q for the snowflake", status)
	}
}
