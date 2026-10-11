package game

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
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

// When a GM's Complete hands no next stage over, the result says why (v1.23.2):
// reported as "fix stuck quest doesn't give the next one", where the chain had
// simply never been re-pointed on that world (migration 77) and the lever,
// which had worked, showed nothing to tell the two apart.
func TestAGMCompleteSaysWhyNoNextStageCame(t *testing.T) {
	for _, tc := range []struct {
		name, seed, want string
		holdNext         bool
	}{
		{"handed over", `{"follow_on":"next"}`, "handed over next", false},
		{"nothing chained", `{"follow_on":""}`, "nothing is chained after stuck", false},
		{"already held", `{"follow_on":"next"}`, "next is already held (completed)", true},
		{"not in this world", `{"follow_on":"nowhere"}`, "nowhere could not be handed over", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := questAdminDB(t)
			conn := beginnerConn(t, path)
			if _, err := conn.Execute(`UPDATE quest_definitions SET seed_json=? WHERE quest_key='stuck'`, []any{tc.seed}); err != nil {
				t.Fatal(err)
			}
			if tc.holdNext {
				if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at) VALUES(42,'next','completed',0,0)`, nil); err != nil {
					t.Fatal(err)
				}
			}
			if err := conn.Commit(); err != nil {
				t.Fatal(err)
			}
			out, err := applyAdminRaw(t, path, "admin.player.quest_complete", 1, map[string]any{"user_id": 42, "quest_key": "stuck"})
			if err != nil {
				t.Fatal(err)
			}
			note, _ := out.Result.(map[string]any)["next_stage"].(string)
			if !strings.HasPrefix(note, tc.want) {
				t.Fatalf("next_stage=%q, want it to start %q", note, tc.want)
			}
			if rows := auditRows(t, path, "admin.player.quest_complete"); len(rows) != 1 || !strings.Contains(fmt.Sprint(rows[0]["after_json"]), "next_stage") {
				t.Fatalf("the audit row does not carry the note: %v", rows)
			}
		})
	}
}

// The GM's third quest lever (v1.23.2): hand a player a quest. Reported from
// the dashboard: a player who finished "A Road Toward a Sect" held nothing,
// and Complete and Report act only on a quest already held.
func TestAGMCanHandAPlayerTheirNextQuest(t *testing.T) {
	path := questAdminDB(t)
	conn := beginnerConn(t, path)
	defineQuest(t, conn, "road_next", "", "")
	defineQuest(t, conn, "a_commission", "Elder Xue Hong", "")
	if _, err := conn.Execute(`INSERT INTO quest_definitions(quest_key,title,description,objectives_json,rewards_json,status,giver_npc,seed_json,created_at,updated_at)
        VALUES('a_draft','Draft','','[]','{}','draft','','{}',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	out, err := applyAdminRaw(t, path, "admin.player.quest_grant", 1, map[string]any{"user_id": 42, "quest_key": "road_next", "reason": "stuck after the sect road"})
	if err != nil {
		t.Fatal(err)
	}
	if granted, _ := out.Result.(map[string]any)["granted"].(bool); !granted {
		t.Fatalf("result %v", out.Result)
	}
	if status, _ := questStatus(t, path, 42, "road_next"); status != "active" {
		t.Fatalf("road_next is %q after the grant, want active", status)
	}
	rows := auditRows(t, path, "admin.player.quest_grant")
	if len(rows) != 1 || rows[0]["target"] != "user:42 quest:road_next" || rows[0]["reason"] != "stuck after the sect road" {
		t.Fatalf("audit rows %v", rows)
	}
	// The lever is a door the player then walks: the granted quest advances
	// by the player's own report, as any other quest does.
	if _, err := applyAdminRaw(t, path, "admin.player.quest_progress", 1, map[string]any{"user_id": 42, "quest_key": "road_next", "objective_type": "cultivate"}); err != nil {
		t.Fatalf("the granted quest could not be advanced: %v", err)
	}

	for _, tc := range []struct{ name, key, want string }{
		{"already held", "road_next", "already holds road_next"},
		{"a commission", "a_commission", "is a commission"},
		{"not approved", "a_draft", "not approved"},
		{"not in this world", "nowhere", "no quest nowhere"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := applyAdminRaw(t, path, "admin.player.quest_grant", 1, map[string]any{"user_id": 42, "quest_key": tc.key}); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("want a refusal naming %q, got %v", tc.want, err)
			}
		})
	}
	if _, err := applyAdminRaw(t, path, "admin.player.quest_grant", 1, map[string]any{"user_id": 999, "quest_key": "road_next"}); err == nil || !strings.Contains(err.Error(), "no character") {
		t.Fatalf("a grant to nobody: %v", err)
	}
	if got := len(auditRows(t, path, "admin.player.quest_grant")); got != 1 {
		t.Fatalf("%d audit rows; a refused grant must write none", got)
	}
}

// shippedQuestObjectives walks the content file raw - a quest roster the Go
// catalogue does not parse still counts - for every objective list sitting
// beside a quest_key: the commission pool, the realm road, the household
// errands and the beginner path.
func shippedQuestObjectives(t *testing.T) map[string][]map[string]any {
	t.Helper()
	raw, err := os.ReadFile(batch4WorldPath(t))
	if err != nil {
		t.Fatalf("content: %v", err)
	}
	var doc any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("content: %v", err)
	}
	found := map[string][]map[string]any{}
	var walk func(any)
	walk = func(node any) {
		switch v := node.(type) {
		case map[string]any:
			key, _ := v["quest_key"].(string)
			if list, ok := v["objectives"].([]any); ok && key != "" {
				for _, o := range list {
					if m, ok := o.(map[string]any); ok {
						found[key] = append(found[key], m)
					}
				}
			}
			for _, child := range v {
				walk(child)
			}
		case []any:
			for _, child := range v {
				walk(child)
			}
		}
	}
	walk(doc)
	return found
}

// What the Player Editor's Report sends is the objective's own type and its
// own stored target, verbatim (v1.33.0). That is a fix only if the engine's
// report matching meets every shipped objective by exactly that echo - after
// the grade strip and the gate-is-its-city rewrite - and moves nothing else.
// Reported from the dashboard: the card sent the type alone, 459 of the 524
// shipped objectives name a target, and `progressQuest` counts a targeted
// objective only against a report naming the same thing, so the card could not
// advance the realm-road breakthrough its own text tells a GM to report.
func TestEveryShippedObjectiveIsMetByItsOwnTarget(t *testing.T) {
	quests := shippedQuestObjectives(t)
	if len(quests["realm_road_1"]) == 0 || len(quests) < 200 {
		t.Fatalf("the content walk found %d quests and realm_road_1 has %d objectives; the reader is broken, not the tree", len(quests), len(quests["realm_road_1"]))
	}
	keys := make([]string, 0, len(quests))
	for key := range quests {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	path := questAdminDB(t)
	conn := beginnerConn(t, path)
	for _, key := range keys {
		for _, o := range quests[key] {
			o["count"] = 1000 // nothing completes, so every report lands on an active quest
		}
		encoded, _ := json.Marshal(quests[key])
		if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at,terms_json) VALUES(42,?,'active',0,0,?)`,
			[]any{"echo_" + key, `{"objectives":` + string(encoded) + `,"rewards":{}}`}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	var bad []string
	targeted := 0
	for _, key := range keys {
		held := "echo_" + key
		for _, o := range quests[key] {
			id, kind := fmt.Sprint(o["id"]), fmt.Sprint(o["type"])
			target := ""
			if o["target"] != nil {
				target = fmt.Sprint(o["target"])
				targeted++
			}
			_, before := questStatus(t, path, 42, held)
			if _, err := applyAdminRaw(t, path, "admin.player.quest_progress", 1, map[string]any{"user_id": 42, "quest_key": held, "objective_type": kind, "target": target, "amount": 1}); err != nil {
				bad = append(bad, fmt.Sprintf("%s/%s (%s %q): %v", key, id, kind, target, err))
				continue
			}
			_, after := questStatus(t, path, 42, held)
			for _, other := range quests[key] {
				oid := fmt.Sprint(other["id"])
				want := before[oid]
				if oid == id {
					want++
				}
				if after[oid] != want {
					bad = append(bad, fmt.Sprintf("%s: reporting %s moved %s from %d to %d", key, id, oid, before[oid], after[oid]))
				}
			}
		}
	}
	if targeted < 400 {
		t.Fatalf("only %d targeted objectives came out of the content; the reader is broken, not the tree", targeted)
	}
	if len(bad) > 0 {
		t.Fatalf("%d shipped objectives are not met by their own target alone, so the Quests card cannot report them:\n%s", len(bad), strings.Join(bad[:min(len(bad), 10)], "\n"))
	}
}

// A report that reaches a targeted objective without its target is refused
// with what the objective names, not with a denial that the objective exists
// (v1.33.0). The lever is a replay of a player's report, so the match is
// progressQuest's: the same target in any case, an untargeted objective
// meeting any event of its type - and the audit row says which was named.
func TestAReportMissingItsTargetSaysWhatTheObjectiveNames(t *testing.T) {
	path := questAdminDB(t)
	conn := beginnerConn(t, path)
	objs := `[{"id":"gate","type":"breakthrough","target":"Qi Refining","count":1},{"id":"sit","type":"cultivate","count":1},{"id":"pine","type":"talk","target":"Elder Pine","count":1},{"id":"qiao","type":"talk","target":"Steward Qiao","count":1}]`
	if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at,terms_json) VALUES(42,'road','active',0,0,?)`, []any{`{"objectives":` + objs + `,"rewards":{}}`}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	report := func(extra map[string]any) error {
		payload := map[string]any{"user_id": 42, "quest_key": "road", "reason": "missed"}
		for k, v := range extra {
			payload[k] = v
		}
		_, err := applyAdminRaw(t, path, "admin.player.quest_progress", 1, payload)
		return err
	}
	err := report(map[string]any{"objective_type": "breakthrough"})
	if err == nil || !strings.Contains(err.Error(), "Qi Refining") || strings.Contains(err.Error(), "no objective of type") {
		t.Fatalf("a report missing its target was refused with %v; it must name what the objective asks for, not deny the objective exists", err)
	}
	err = report(map[string]any{"objective_type": "talk", "target": "Nobody"})
	if err == nil || !strings.Contains(err.Error(), "Elder Pine") || !strings.Contains(err.Error(), "Steward Qiao") || !strings.Contains(err.Error(), `"Nobody"`) {
		t.Fatalf("a report naming the wrong target was refused with %v; it must list what the objectives name and say what the report named", err)
	}
	if err := report(map[string]any{"objective_type": "combat_win"}); err == nil || !strings.Contains(err.Error(), "no objective of type") {
		t.Fatalf("a type the quest does not ask for: %v", err)
	}
	if _, p := questStatus(t, path, 42, "road"); p["gate"] != 0 || p["pine"] != 0 || p["qiao"] != 0 || p["sit"] != 0 {
		t.Fatalf("the refused reports moved %v", p)
	}
	if rows := auditRows(t, path, "admin.player.quest_progress"); len(rows) != 0 {
		t.Fatalf("a refused report wrote %d audit rows", len(rows))
	}
	if err := report(map[string]any{"objective_type": "cultivate", "target": "Anywhere"}); err != nil {
		t.Fatalf("an objective with no target accepts any event of its type, so this report must land: %v", err)
	}
	if err := report(map[string]any{"objective_type": "breakthrough", "target": "qi refining"}); err != nil {
		t.Fatalf("a report naming the objective's target in another case was refused: %v", err)
	}
	if _, p := questStatus(t, path, 42, "road"); p["gate"] != 1 || p["sit"] != 1 || p["pine"] != 0 || p["qiao"] != 0 {
		t.Fatalf("the reports moved %v, want the breakthrough and the untargeted sitting only", p)
	}
	rows := auditRows(t, path, "admin.player.quest_progress")
	if len(rows) != 2 {
		t.Fatalf("%d audit rows, want one for each landed report", len(rows))
	}
	if after := fmt.Sprint(rows[1]["after_json"]); !strings.Contains(after, `"target":"qi refining"`) {
		t.Fatalf("the audit row does not say which objective the report named: %s", after)
	}
}
