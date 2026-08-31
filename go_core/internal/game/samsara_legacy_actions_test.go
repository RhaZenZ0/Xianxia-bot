package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func runDynastyInvestigation(t *testing.T, conn *storage.Conn, historyID int64) {
	t.Helper()
	for level := int64(1); level <= maxDynastyInvestigationLevel; level++ {
		raw, _ := json.Marshal(map[string]any{"history_id": historyID, "game_minute": 100 + level})
		if _, err := lineageInvestigateAction(conn, 77, raw); err != nil {
			t.Fatalf("investigate level %d: %v", level, err)
		}
	}
}

func completeDynastyQuest(t *testing.T, conn *storage.Conn, historyID, questID int64) map[string]any {
	t.Helper()
	var result map[string]any
	for attempt := 0; attempt < 4; attempt++ {
		raw, _ := json.Marshal(map[string]any{
			"history_id":  historyID,
			"quest_id":    questID,
			"game_minute": 200 + attempt,
		})
		mutation, err := dynastyQuestAction(conn, 77, raw)
		if err != nil {
			t.Fatalf("quest %d: %v", questID, err)
		}
		result = mutation.Result.(map[string]any)
		if result["completed"] == true {
			return result
		}
	}
	t.Fatalf("quest %d did not complete", questID)
	return nil
}

func TestDynastyInvestigationUnlocksArchiveRuinsTombAndRetainerQuests(t *testing.T) {
	conn := dynastyTestConn(t)
	historyID, err := recordSamsaraDynastyHistory(
		conn, 77, 2, 1, 2, 100,
		"Han Family", "martial_household", "Mortal World",
		"Ji Stone-Marrow House", "spirit_body_house", "Spiritual World",
		"fallen_severed_branch", "A severed branch survived under a new identity.", 1,
	)
	if err != nil {
		t.Fatal(err)
	}
	runDynastyInvestigation(t, conn, historyID)

	leads, err := conn.Execute(`
		SELECT lead_kind,status,location,retainer_name
		FROM samsara_ancestral_leads
		WHERE history_id=?
		ORDER BY lead_id`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	if len(leads.Rows) != 4 {
		t.Fatalf("lead count=%d want=4", len(leads.Rows))
	}
	kinds := map[string]bool{}
	foundRetainer := false
	for _, row := range leads.Rows {
		kinds[fmt.Sprint(row[0])] = true
		if fmt.Sprint(row[1]) != "discovered" {
			t.Fatalf("lead %q status=%q", row[0], row[1])
		}
		if strings.TrimSpace(fmt.Sprint(row[2])) == "" {
			t.Fatalf("lead %q has no location", row[0])
		}
		if fmt.Sprint(row[0]) == "retainer" && strings.TrimSpace(fmt.Sprint(row[3])) != "" {
			foundRetainer = true
		}
	}
	for _, kind := range []string{"archive", "ruin", "tomb", "retainer"} {
		if !kinds[kind] {
			t.Fatalf("missing lead kind %q", kind)
		}
	}
	if !foundRetainer {
		t.Fatal("surviving retainer lead was not generated")
	}

	quests, err := conn.Execute(`
		SELECT quest_id,status,target
		FROM samsara_investigation_quests
		WHERE history_id=?
		ORDER BY quest_id`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	if len(quests.Rows) != 4 {
		t.Fatalf("quest count=%d want=4", len(quests.Rows))
	}
	for _, row := range quests.Rows {
		if fmt.Sprint(row[1]) != "available" {
			t.Fatalf("quest %d status=%q", i64(row[0]), row[1])
		}
		if i64(row[2]) < 1 {
			t.Fatalf("quest %d invalid target=%d", i64(row[0]), i64(row[2]))
		}
	}
}

func TestReplacementFamilyCannotBecomeBloodInheritanceButCanBeChallenged(t *testing.T) {
	conn := dynastyTestConn(t)
	historyID, err := recordSamsaraDynastyHistory(
		conn, 77, 2, 1, 2, 100,
		"Han Family", "martial_household", "Mortal World",
		"Ji Stone-Marrow House", "spirit_body_house", "Spiritual World",
		"extinct_branch_replaced", "The old branch died out and Ji House later occupied its role.", 1,
	)
	if err != nil {
		t.Fatal(err)
	}
	runDynastyInvestigation(t, conn, historyID)

	questRows, err := conn.Execute(`SELECT quest_id FROM samsara_investigation_quests WHERE history_id=? ORDER BY quest_id`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	for i, row := range questRows.Rows {
		if i >= 3 {
			break
		}
		completeDynastyQuest(t, conn, historyID, i64(row[0]))
	}

	inheritanceRaw, _ := json.Marshal(map[string]any{
		"history_id": historyID, "claim_type": "inheritance", "game_minute": 300,
	})
	if _, err = dynastyClaimAction(conn, 77, inheritanceRaw); err == nil || !strings.Contains(strings.ToLower(err.Error()), "no blood continuity") {
		t.Fatalf("replacement inheritance error=%v", err)
	}

	challengeRaw, _ := json.Marshal(map[string]any{
		"history_id": historyID, "claim_type": "replacement_challenge", "game_minute": 301,
	})
	mutation, err := dynastyClaimAction(conn, 77, challengeRaw)
	if err != nil {
		t.Fatal(err)
	}
	claim := mutation.Result.(map[string]any)
	if claim["blood_based"] == true {
		t.Fatal("replacement challenge incorrectly became blood-based")
	}
	if i64(claim["conflict_id"]) <= 0 {
		t.Fatalf("replacement challenge conflict=%v", claim["conflict_id"])
	}

	claimID := i64(claim["claim_id"])
	var final map[string]any
	for round := 0; round < 6; round++ {
		raw, _ := json.Marshal(map[string]any{
			"claim_id": claimID, "tactic": "expose", "game_minute": 310 + round,
		})
		action, actionErr := dynastyConflictAction(conn, 77, raw)
		if actionErr != nil {
			t.Fatal(actionErr)
		}
		final = action.Result.(map[string]any)
		if fmt.Sprint(final["status"]) != "active" {
			break
		}
	}
	if fmt.Sprint(final["status"]) != "won" {
		t.Fatalf("replacement conflict final=%#v", final)
	}
	if fmt.Sprint(final["resolution"]) != "replacement_legacy_conceded" {
		t.Fatalf("replacement resolution=%q", final["resolution"])
	}
	history, err := conn.Execute(`SELECT blood_continuity FROM samsara_dynasty_history WHERE history_id=?`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	if i64(history.Rows[0][0]) != 0 {
		t.Fatal("replacement conflict rewrote blood continuity")
	}
}

func TestFallenDynastyCanRestoreAndPursueEvidenceBasedRevenge(t *testing.T) {
	conn := dynastyTestConn(t)
	historyID, err := recordSamsaraDynastyHistory(
		conn, 77, 2, 1, 2, 100,
		"Han Family", "martial_household", "Mortal World",
		"Ji Stone-Marrow House", "spirit_body_house", "Spiritual World",
		"fallen_severed_branch", "The branch fell under hostile pressure and later severed its identity.", 1,
	)
	if err != nil {
		t.Fatal(err)
	}
	runDynastyInvestigation(t, conn, historyID)

	questRows, err := conn.Execute(`SELECT quest_id FROM samsara_investigation_quests WHERE history_id=? ORDER BY quest_id`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range questRows.Rows {
		completeDynastyQuest(t, conn, historyID, i64(row[0]))
	}

	inheritanceRaw, _ := json.Marshal(map[string]any{
		"history_id": historyID, "claim_type": "inheritance", "game_minute": 399,
	})
	inheritance, err := dynastyClaimAction(conn, 77, inheritanceRaw)
	if err != nil {
		t.Fatal(err)
	}
	inheritanceResult := inheritance.Result.(map[string]any)
	if fmt.Sprint(inheritanceResult["status"]) != "recognized" {
		t.Fatalf("inheritance status=%q", inheritanceResult["status"])
	}
	if inheritanceResult["blood_based"] != true {
		t.Fatal("confirmed surviving bloodline did not produce a blood-based inheritance claim")
	}

	restorationRaw, _ := json.Marshal(map[string]any{
		"history_id": historyID, "claim_type": "restoration", "game_minute": 400,
	})
	restoration, err := dynastyClaimAction(conn, 77, restorationRaw)
	if err != nil {
		t.Fatal(err)
	}
	restorationResult := restoration.Result.(map[string]any)
	if i64(restorationResult["conflict_id"]) <= 0 {
		t.Fatal("restoration did not create a contest")
	}
	restorationClaimID := i64(restorationResult["claim_id"])
	var restorationFinal map[string]any
	for round := 0; round < 6; round++ {
		raw, _ := json.Marshal(map[string]any{
			"claim_id": restorationClaimID, "tactic": "rally", "game_minute": 410 + round,
		})
		action, actionErr := dynastyConflictAction(conn, 77, raw)
		if actionErr != nil {
			t.Fatal(actionErr)
		}
		restorationFinal = action.Result.(map[string]any)
		if fmt.Sprint(restorationFinal["status"]) != "active" {
			break
		}
	}
	if fmt.Sprint(restorationFinal["resolution"]) != "dynasty_restored" {
		t.Fatalf("restoration resolution=%q", restorationFinal["resolution"])
	}

	revengeRaw, _ := json.Marshal(map[string]any{
		"history_id": historyID, "claim_type": "revenge", "game_minute": 401,
	})
	revenge, err := dynastyClaimAction(conn, 77, revengeRaw)
	if err != nil {
		t.Fatal(err)
	}
	revengeResult := revenge.Result.(map[string]any)
	if i64(revengeResult["conflict_id"]) <= 0 {
		t.Fatal("evidence-based revenge did not create a conflict")
	}
	if !strings.Contains(strings.ToLower(fmt.Sprint(revengeResult["target_family"])), "oathbreaker") {
		t.Fatalf("revenge target=%q", revengeResult["target_family"])
	}
	revengeClaimID := i64(revengeResult["claim_id"])
	var revengeFinal map[string]any
	for round := 0; round < 6; round++ {
		raw, _ := json.Marshal(map[string]any{
			"claim_id": revengeClaimID, "tactic": "duel", "game_minute": 420 + round,
		})
		action, actionErr := dynastyConflictAction(conn, 77, raw)
		if actionErr != nil {
			t.Fatal(actionErr)
		}
		revengeFinal = action.Result.(map[string]any)
		if fmt.Sprint(revengeFinal["status"]) != "active" {
			break
		}
	}
	if fmt.Sprint(revengeFinal["resolution"]) != "ancestral_revenge_satisfied" {
		t.Fatalf("revenge resolution=%q", revengeFinal["resolution"])
	}
}

func TestDynastyQuestDangerDrivesDifficultyAndPhysicalFailureCost(t *testing.T) {
	conn := dynastyTestConn(t)
	historyID, err := recordSamsaraDynastyHistory(
		conn, 77, 2, 1, 2, 100,
		"Han Family", "martial_household", "Mortal World",
		"Ji Stone-Marrow House", "spirit_body_house", "Spiritual World",
		"fallen_severed_branch", "A severed branch survived under a new identity.", 1,
	)
	if err != nil {
		t.Fatal(err)
	}
	runDynastyInvestigation(t, conn, historyID)
	if _, err = conn.Execute(`UPDATE characters SET attributes_json=? WHERE user_id=?`, []any{
		`{"body":0,"agility":0,"spirit":0,"insight":0,"will":0,"presence":0,"heart":0}`, 77,
	}); err != nil {
		t.Fatal(err)
	}

	quests, err := conn.Execute(`
		SELECT q.quest_id,q.quest_kind,l.danger
		FROM samsara_investigation_quests AS q
		JOIN samsara_ancestral_leads AS l ON l.lead_id=q.lead_id
		WHERE q.history_id=? AND q.quest_kind IN ('archive_research','tomb_inquest')
		ORDER BY q.quest_kind`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	ids := map[string]int64{}
	for _, row := range quests.Rows {
		ids[fmt.Sprint(row[1])] = i64(row[0])
	}
	oldRoller := dynastyRoll2d10
	dynastyRoll2d10 = func(modifier, tn int64) (map[string]any, error) {
		total := int64(2) + modifier
		return map[string]any{
			"die1": int64(1), "die2": int64(1), "modifier": modifier, "tn": tn,
			"total": total, "margin": total - tn, "success": total >= tn, "degree": "Severe Failure",
		}, nil
	}
	t.Cleanup(func() { dynastyRoll2d10 = oldRoller })

	archiveRaw, _ := json.Marshal(map[string]any{"history_id": historyID, "quest_id": ids["archive_research"], "game_minute": 501})
	archiveMutation, err := dynastyQuestAction(conn, 77, archiveRaw)
	if err != nil {
		t.Fatal(err)
	}
	archive := archiveMutation.Result.(map[string]any)
	if got := i64(archive["difficulty_tn"]); got != 14 {
		t.Fatalf("archive TN=%d want=14", got)
	}
	if archive["success"] == true || i64(archive["progress_gain"]) != 0 {
		t.Fatalf("archive failure unexpectedly progressed: %#v", archive)
	}
	if got := i64(archive["vitality_loss"]); got != 0 {
		t.Fatalf("archive vitality loss=%d want=0", got)
	}

	tombRaw, _ := json.Marshal(map[string]any{"history_id": historyID, "quest_id": ids["tomb_inquest"], "game_minute": 502})
	tombMutation, err := dynastyQuestAction(conn, 77, tombRaw)
	if err != nil {
		t.Fatal(err)
	}
	tomb := tombMutation.Result.(map[string]any)
	if got := i64(tomb["difficulty_tn"]); got != 22 {
		t.Fatalf("tomb TN=%d want=22", got)
	}
	if got := i64(tomb["danger"]); got != 50 {
		t.Fatalf("tomb danger=%d want=50", got)
	}
	if got := i64(tomb["vitality_loss"]); got != 2 {
		t.Fatalf("tomb vitality loss=%d want=2", got)
	}
	vitality, err := conn.Execute(`SELECT vitality FROM characters WHERE user_id=?`, []any{77})
	if err != nil {
		t.Fatal(err)
	}
	if got := i64(vitality.Rows[0][0]); got != 98 {
		t.Fatalf("vitality=%d want=98", got)
	}
}

func TestDynastyConflictResolutionUsesOpposedRandomChecks(t *testing.T) {
	conn := dynastyTestConn(t)
	historyID, err := recordSamsaraDynastyHistory(
		conn, 77, 2, 1, 2, 100,
		"Han Family", "martial_household", "Mortal World",
		"Ji Stone-Marrow House", "spirit_body_house", "Spiritual World",
		"fallen_severed_branch", "The branch fell under hostile pressure.", 1,
	)
	if err != nil {
		t.Fatal(err)
	}
	runDynastyInvestigation(t, conn, historyID)
	questRows, err := conn.Execute(`SELECT quest_id FROM samsara_investigation_quests WHERE history_id=? ORDER BY quest_id LIMIT 2`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range questRows.Rows {
		completeDynastyQuest(t, conn, historyID, i64(row[0]))
	}
	raw, _ := json.Marshal(map[string]any{"history_id": historyID, "claim_type": "restoration", "game_minute": 600})
	claimMutation, err := dynastyClaimAction(conn, 77, raw)
	if err != nil {
		t.Fatal(err)
	}
	claim := claimMutation.Result.(map[string]any)
	claimID := i64(claim["claim_id"])
	if got := i64(claim["opposition"]); got != 55 {
		t.Fatalf("minimum-evidence opposition=%d want=55", got)
	}

	oldRoller := dynastyRoll2d10
	call := 0
	dynastyRoll2d10 = func(modifier, tn int64) (map[string]any, error) {
		call++
		if call == 1 {
			return map[string]any{"die1": int64(1), "die2": int64(1), "modifier": modifier, "tn": tn, "total": int64(2) + modifier, "margin": int64(-9), "success": false, "degree": "Severe Failure"}, nil
		}
		return map[string]any{"die1": int64(10), "die2": int64(10), "modifier": modifier, "tn": tn, "total": int64(20) + modifier, "margin": int64(8), "success": true, "degree": "Strong Success"}, nil
	}
	t.Cleanup(func() { dynastyRoll2d10 = oldRoller })

	conflictRaw, _ := json.Marshal(map[string]any{"claim_id": claimID, "tactic": "duel", "game_minute": 601})
	conflictMutation, err := dynastyConflictAction(conn, 77, conflictRaw)
	if err != nil {
		t.Fatal(err)
	}
	result := conflictMutation.Result.(map[string]any)
	if result["player_success"] == true {
		t.Fatalf("player check unexpectedly succeeded: %#v", result["player_roll"])
	}
	if result["opponent_success"] != true {
		t.Fatalf("opponent check unexpectedly failed: %#v", result["opponent_roll"])
	}
	if got := i64(result["player_gain"]); got != 0 {
		t.Fatalf("failed player check gained %d pressure", got)
	}
	if got := i64(result["opponent_gain"]); got <= 4 {
		t.Fatalf("successful opponent plus duel-risk gain=%d want>4", got)
	}
}

func TestExtraDynastyQuestEvidenceReducesClaimOpposition(t *testing.T) {
	conn := dynastyTestConn(t)
	historyID, err := recordSamsaraDynastyHistory(
		conn, 77, 2, 1, 2, 100,
		"Han Family", "martial_household", "Mortal World",
		"Ji Stone-Marrow House", "spirit_body_house", "Spiritual World",
		"fallen_severed_branch", "The branch fell under hostile pressure.", 1,
	)
	if err != nil {
		t.Fatal(err)
	}
	runDynastyInvestigation(t, conn, historyID)
	questRows, err := conn.Execute(`SELECT quest_id FROM samsara_investigation_quests WHERE history_id=? ORDER BY quest_id`, []any{historyID})
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range questRows.Rows {
		completeDynastyQuest(t, conn, historyID, i64(row[0]))
	}
	raw, _ := json.Marshal(map[string]any{"history_id": historyID, "claim_type": "restoration", "game_minute": 700})
	claimMutation, err := dynastyClaimAction(conn, 77, raw)
	if err != nil {
		t.Fatal(err)
	}
	claim := claimMutation.Result.(map[string]any)
	if got := i64(claim["opposition"]); got != 45 {
		t.Fatalf("full-evidence opposition=%d want=45", got)
	}
}
