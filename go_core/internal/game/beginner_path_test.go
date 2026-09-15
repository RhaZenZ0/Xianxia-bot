package game

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The path a new cultivator is put on (v1.0.0-rc.26). Nothing here rolls a
// die: who is handed what is entirely a function of the content and of what
// they are already carrying, so every assertion below is exact.

func beginnerConn(t *testing.T, path string) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return conn
}

// defineQuest writes one approved definition. `giver` is the whole difference
// between an ordinary quest and a commission.
func defineQuest(t *testing.T, conn *storage.Conn, key, giver, followOn string) {
	t.Helper()
	seed := "{}"
	if followOn != "" {
		raw, _ := json.Marshal(map[string]string{"follow_on": followOn})
		seed = string(raw)
	}
	if _, err := conn.Execute(`INSERT INTO quest_definitions(
        quest_key,title,description,objectives_json,rewards_json,status,giver_npc,seed_json,created_at,updated_at)
        VALUES(?,?,'','[{"id":"sit","type":"cultivate","count":1,"label":"Sit"}]','{"insight_xp":5}','approved',?,?,0,0)`,
		[]any{key, key, giver, seed}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func heldQuests(t *testing.T, conn *storage.Conn, userID int64) []string {
	t.Helper()
	res, err := conn.Execute(`SELECT quest_key FROM character_quests WHERE user_id=? ORDER BY quest_key`, []any{userID})
	if err != nil {
		t.Fatal(err)
	}
	out := []string{}
	for _, row := range res.Rows {
		out = append(out, fmt.Sprint(row[0]))
	}
	return out
}

func TestAnOrdinaryQuestCanBeHandedOver(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "beginner_household", "", "")
	granted, err := grantOrdinaryQuestTx(conn, 42, "beginner_household", 100)
	if err != nil {
		t.Fatal(err)
	}
	if !granted {
		t.Fatal("a definition with no giver was not handed over")
	}
	if got := heldQuests(t, conn, 42); len(got) != 1 || got[0] != "beginner_household" {
		t.Fatalf("held %v", got)
	}
	// The terms are pinned at the hand-over, exactly as commission.accept
	// pins them, so a later GM edit cannot rewrite what somebody is carrying.
	terms := fmt.Sprint(beginnerScalar(t, conn, `SELECT terms_json FROM character_quests WHERE user_id=42`))
	if terms == "" || terms == "{}" {
		t.Fatalf("the terms were not pinned at hand-over: %q", terms)
	}
}

func TestNobodyIsHandedTheSameQuestTwice(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "beginner_household", "", "")
	if _, err := grantOrdinaryQuestTx(conn, 42, "beginner_household", 100); err != nil {
		t.Fatal(err)
	}
	// Creation, a dao-family rebirth and a samsara return can each reach this
	// against somebody who already has it - the same re-entry the household's
	// own schooling guards against.
	again, err := grantOrdinaryQuestTx(conn, 42, "beginner_household", 200)
	if err != nil {
		t.Fatal(err)
	}
	if again {
		t.Fatal("the same quest was handed over a second time")
	}
	if got := heldQuests(t, conn, 42); len(got) != 1 {
		t.Fatalf("held %v", got)
	}
}

// Finishing it counts as having held it. The row is the memory, so a player
// cannot be walked back round a chain they have already completed.
func TestAFinishedQuestIsNotHandedOverAgain(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "beginner_household", "", "")
	if _, err := grantOrdinaryQuestTx(conn, 42, "beginner_household", 100); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE character_quests SET status='completed' WHERE user_id=42`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	again, err := grantOrdinaryQuestTx(conn, 42, "beginner_household", 300)
	if err != nil {
		t.Fatal(err)
	}
	if again {
		t.Fatal("a quest already completed was handed over again")
	}
}

// The window that must never cost anybody a character: the bot seeds the
// definitions at boot, and `character.create` hands the first stage over in
// the transaction that makes the character.
func TestAMissingDefinitionCostsTheQuestAndNotTheCharacter(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	granted, err := grantOrdinaryQuestTx(conn, 42, "nothing_has_seeded_this_yet", 100)
	if err != nil {
		t.Fatalf("a quest that does not exist must be a no-op, not an error: %v", err)
	}
	if granted {
		t.Fatal("a quest with no definition was handed over")
	}
}

// A commission is something a giver offers you in person: it takes the
// one-at-a-time slot and carries a deadline. Nothing may put one in your
// hands behind your back, whatever a chain or a content file says.
func TestACommissionIsNeverHandedOverBehindTheirBack(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "steward_crate", "Steward Qiao", "")
	granted, err := grantOrdinaryQuestTx(conn, 42, "steward_crate", 100)
	if err != nil {
		t.Fatal(err)
	}
	if granted {
		t.Fatal("a commission was handed over without its giver offering it")
	}
	if got := heldQuests(t, conn, 42); len(got) != 0 {
		t.Fatalf("held %v", got)
	}
}

func TestTheChainIsReadOffTheRowAndNotTheContentFile(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "beginner_household", "", "beginner_town")
	next, err := questFollowOnTx(conn, "beginner_household")
	if err != nil {
		t.Fatal(err)
	}
	if next != "beginner_town" {
		t.Fatalf("follow-on is %q", next)
	}
	// A GM re-pointing the chain in the workbench edits this row, and the
	// engine obeys it - the shipped content is the starting shape only.
	if _, err := conn.Execute(`UPDATE quest_definitions SET seed_json='{"follow_on":"somewhere_else"}' WHERE quest_key='beginner_household'`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	next, err = questFollowOnTx(conn, "beginner_household")
	if err != nil {
		t.Fatal(err)
	}
	if next != "somewhere_else" {
		t.Fatalf("the engine did not obey the edited chain: %q", next)
	}
}

func TestAQuestWithNoChainSaysSo(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "beginner_road", "", "")
	next, err := questFollowOnTx(conn, "beginner_road")
	if err != nil {
		t.Fatal(err)
	}
	if next != "" {
		t.Fatalf("the last stage of a path named a follow-on: %q", next)
	}
}

// A chain pointing at itself would re-grant a quest the moment it completed,
// forever. Refused here rather than trusted to whoever wrote the content.
func TestAChainCannotPointAtItself(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "loop", "", "loop")
	next, err := questFollowOnTx(conn, "loop")
	if err != nil {
		t.Fatal(err)
	}
	if next != "" {
		t.Fatalf("a self-pointing chain was accepted: %q", next)
	}
}

func TestTheFirstStageIsTheFirstEntryInTheContent(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "beginner_household", "", "beginner_town")
	defineQuest(t, conn, "beginner_town", "", "")
	catalog := worlddata.Catalog{BeginnerPath: []worlddata.BeginnerStage{
		{QuestKey: "beginner_household"}, {QuestKey: "beginner_town"},
	}}
	key, err := grantBeginnerPathTx(conn, catalog, 42, 100)
	if err != nil {
		t.Fatal(err)
	}
	if key != "beginner_household" {
		t.Fatalf("started on %q", key)
	}
	// Only the first. The rest arrive by finishing the one before.
	if got := heldQuests(t, conn, 42); len(got) != 1 || got[0] != "beginner_household" {
		t.Fatalf("held %v; a new character must not be handed the whole path at once", got)
	}
}

// A world whose content has no beginner path simply has none. That is an
// operator's decision, not a fault, and it must not stop a character existing.
func TestAWorldWithNoBeginnerPathStillMakesCharacters(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	key, err := grantBeginnerPathTx(conn, worlddata.Catalog{}, 42, 100)
	if err != nil {
		t.Fatalf("an empty beginner path must be a no-op, not an error: %v", err)
	}
	if key != "" {
		t.Fatalf("started on %q out of an empty roster", key)
	}
}

func beginnerScalar(t *testing.T, conn *storage.Conn, sql string) any {
	t.Helper()
	res, err := conn.Execute(sql, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return nil
	}
	return res.Rows[0][0]
}
