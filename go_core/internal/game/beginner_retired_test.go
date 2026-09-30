package game

import (
	"testing"

	"xianxia/core/internal/worlddata"
)

// A definition that is not approved is not handed over (v1.12.3). The catch-up
// runs on every action and follows `follow_on` off a completed quest, so a
// chain pointing at a quest a GM retired handed it over again on the next
// action, after the holder rows had been revoked. Every seeder writes
// 'approved' and the Forge's drafts wait for a GM, so nothing handed over
// today stops being handed over.

func TestARetiredDefinitionIsNotHandedOver(t *testing.T) {
	for _, status := range []string{"retired", "discarded", "draft"} {
		conn := beginnerConn(t, setupCommissionDB(t))
		defineQuest(t, conn, "beginner_household", "", "")
		if _, err := conn.Execute(`UPDATE quest_definitions SET status=? WHERE quest_key='beginner_household'`, []any{status}); err != nil {
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		granted, err := grantOrdinaryQuestTx(conn, 42, "beginner_household", 100)
		if err != nil {
			t.Fatalf("%s: a definition that is not approved is a no, not an error: %v", status, err)
		}
		if granted || len(heldQuests(t, conn, 42)) != 0 {
			t.Fatalf("a %s definition was handed over: held %v", status, heldQuests(t, conn, 42))
		}
	}
}

func TestTheCatchUpHandsOverAnApprovedFollowOnAndNotARetiredOne(t *testing.T) {
	conn := beginnerConn(t, setupCommissionDB(t))
	defineQuest(t, conn, "stage_one", "", "stage_two")
	defineQuest(t, conn, "stage_two", "", "")
	if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at) VALUES(42,'stage_one','completed',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	handed, err := catchUpBeginnerPathTx(conn, worlddata.Catalog{}, 42, 100)
	if err != nil {
		t.Fatal(err)
	}
	if len(handed) != 1 || handed[0] != "stage_two" {
		t.Fatalf("an approved follow_on was not handed over: %v", handed)
	}
	// A GM retires the stage and revokes the holder row; the next action must
	// not hand it back.
	if _, err := conn.Execute(`UPDATE quest_definitions SET status='retired' WHERE quest_key='stage_two'`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`DELETE FROM character_quests WHERE user_id=42 AND quest_key='stage_two'`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	handed, err = catchUpBeginnerPathTx(conn, worlddata.Catalog{}, 42, 200)
	if err != nil {
		t.Fatal(err)
	}
	if len(handed) != 0 || len(heldQuests(t, conn, 42)) != 1 {
		t.Fatalf("the catch-up handed a retired quest over again: handed %v, held %v", handed, heldQuests(t, conn, 42))
	}
}
