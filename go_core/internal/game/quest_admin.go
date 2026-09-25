package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The GM's two quest levers (v1.4.1), driven from the Player Editor's Quests
// card.
//
// A quest objective is reported once, by the command that did the work, and
// only against quests the player holds `active` at that instant. A report that
// misses its moment is lost for good: the household lesson is once per life,
// so a player who passed it while "The Last Lesson" was not yet active could
// never finish that stage. Nothing but a GM could reach the row, and no lever
// did.
//
// Both levers go through questProgressTx, the same code `quest.progress` runs,
// so a quest a GM finishes pays its reward, resolves its commission, moves a
// household's standing and hands over its follow-on exactly as one a player
// finishes. Neither is in reversibleAdminActions: completing a quest pays out,
// and restoring a snapshot of one row would leave the payment standing.

func adminQuestProgress(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, payload json.RawMessage) (any, error) {
	return adminQuestLever(conn, catalog, adminUserID, payload, false)
}

func adminQuestComplete(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, payload json.RawMessage) (any, error) {
	return adminQuestLever(conn, catalog, adminUserID, payload, true)
}

func adminQuestLever(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, raw json.RawMessage, forceComplete bool) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	questKey := strings.TrimSpace(stringField(p, "quest_key"))
	if questKey == "" {
		return nil, errors.New("quest_key is required")
	}
	report := questPayload{QuestKey: questKey}
	action := "admin.player.quest_complete"
	if !forceComplete {
		action = "admin.player.quest_progress"
		report.ObjectiveType = strings.TrimSpace(stringField(p, "objective_type"))
		if report.ObjectiveType == "" {
			return nil, errors.New("objective_type is required")
		}
		if target := strings.TrimSpace(stringField(p, "target")); target != "" {
			report.Target = &target
		}
		if _, ok := p["amount"]; ok {
			amount, err := requiredInt(p, "amount")
			if err != nil {
				return nil, err
			}
			if amount < 1 || amount > 1000 {
				return nil, errors.New("amount must be between 1 and 1000")
			}
			report.Amount = &amount
		}
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT status,progress_json FROM character_quests WHERE user_id=? AND quest_key=?`, []any{uid, questKey})
	if err != nil {
		return nil, err
	}
	before := firstRowMap(res)
	if before == nil {
		return nil, fmt.Errorf("that player does not hold %s", questKey)
	}
	if fmt.Sprint(before["status"]) != "active" {
		return nil, fmt.Errorf("%s is %s, not active; only an active quest can be advanced", questKey, before["status"])
	}
	transition, found, err := questProgressTx(conn, catalog, uid, report, forceComplete)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, fmt.Errorf("that player does not hold %s active", questKey)
	}
	if touched, _ := transition["touched"].(bool); !touched {
		return nil, fmt.Errorf("%s has no objective of type %q left to advance", questKey, report.ObjectiveType)
	}
	res, err = conn.Execute(`SELECT status,progress_json FROM character_quests WHERE user_id=? AND quest_key=?`, []any{uid, questKey})
	if err != nil {
		return nil, err
	}
	after := firstRowMap(res)
	if after == nil {
		// A commission resolved on its last objective may leave no row behind.
		after = map[string]any{"status": "resolved"}
	}
	after["objective_type"] = report.ObjectiveType
	for _, key := range []string{"rewards_granted", "follow_on", "caught_up", "commission"} {
		if v, ok := transition[key]; ok {
			after[key] = v
		}
	}
	target := fmt.Sprintf("user:%d quest:%s", uid, questKey)
	if err := auditAdmin(conn, adminUserID, action, target, before, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	transition["user_id"] = uid
	return transition, nil
}
