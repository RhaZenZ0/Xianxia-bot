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
	if note, err := questNextStageNoteTx(conn, uid, questKey, transition); err != nil {
		return nil, err
	} else if note != "" {
		transition["next_stage"] = note
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
	for _, key := range []string{"rewards_granted", "follow_on", "caught_up", "commission", "next_stage"} {
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

// questNextStageNoteTx says, when the GM finished an ordinary quest and no next
// stage came with it, why (v1.23.2). A GM who pressed Complete to unstick a
// player was shown nothing when the chain handed nothing over, and could not
// tell "nothing follows this quest" from "the next one is already held" from
// "the next one is not in this world" - which is how a chain the seeder never
// re-pointed (migration 77) looked like a broken lever. It reads only; it
// hands nothing over that questProgressTx did not.
func questNextStageNoteTx(conn *storage.Conn, userID int64, questKey string, transition map[string]any) (string, error) {
	if complete, _ := transition["complete"].(bool); !complete {
		return "", nil
	}
	if _, isCommission := transition["commission"]; isCommission {
		return "", nil
	}
	if next, _ := transition["follow_on"].(string); next != "" {
		return "handed over " + next, nil
	}
	next, err := questFollowOnTx(conn, questKey)
	if err != nil {
		return "", err
	}
	if strings.TrimSpace(next) == "" {
		return "nothing is chained after " + questKey, nil
	}
	held, err := conn.Execute(`SELECT status FROM character_quests WHERE user_id=? AND quest_key=?`, []any{userID, next})
	if err != nil {
		return "", err
	}
	if row := firstRowMap(held); row != nil {
		return fmt.Sprintf("%s is already held (%s)", next, row["status"]), nil
	}
	return fmt.Sprintf("%s could not be handed over: it is not an approved, giver-less quest in this world", next), nil
}

// adminQuestGrant hands a player a quest (v1.23.2), the third quest lever.
// Asked for from the dashboard as "a command for next quest": a player who
// finished "A Road Toward a Sect" held nothing at all - the sect road chains
// to nothing, and the realm road is handed over only at the instant of a
// breakthrough crossing, so somebody already past the crossing was never put
// on it - and Complete and Report act only on a quest the player holds.
//
// It is grantOrdinaryQuestTx, the one door every roster hands a quest over
// by, so it obeys that door's three refusals: a quest already held (any
// status - a finished quest is not handed over twice), a definition that is
// not approved, and a commission, which an NPC offers in person and which
// this lever must not slip into somebody's hands behind the giver's back. It
// pays nothing, so a mistake is corrected by completing or ignoring the
// quest; it is not in reversibleAdminActions, beside the other two levers.
func adminQuestGrant(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return nil, errors.New("that player has no character")
	}
	held, err := conn.Execute(`SELECT status FROM character_quests WHERE user_id=? AND quest_key=?`, []any{uid, questKey})
	if err != nil {
		return nil, err
	}
	if row := firstRowMap(held); row != nil {
		return nil, fmt.Errorf("that player already holds %s (%s)", questKey, row["status"])
	}
	defined, err := conn.Execute(`SELECT COALESCE(status,'') AS status,COALESCE(giver_npc,'') AS giver FROM quest_definitions WHERE quest_key=?`, []any{questKey})
	if err != nil {
		return nil, err
	}
	def := firstRowMap(defined)
	switch {
	case def == nil:
		return nil, fmt.Errorf("no quest %s in this world", questKey)
	case fmt.Sprint(def["giver"]) != "":
		return nil, fmt.Errorf("%s is a commission, offered in person by %s; it is not handed over", questKey, def["giver"])
	case fmt.Sprint(def["status"]) != "approved":
		return nil, fmt.Errorf("%s is %s, not approved", questKey, def["status"])
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	granted, err := grantOrdinaryQuestTx(conn, uid, questKey, gameMinute)
	if err != nil {
		return nil, err
	}
	if !granted {
		return nil, fmt.Errorf("%s could not be handed over", questKey)
	}
	after := map[string]any{"quest_key": questKey, "status": "active"}
	if err := auditAdmin(conn, adminUserID, "admin.player.quest_grant", fmt.Sprintf("user:%d quest:%s", uid, questKey), map[string]any{"held": false}, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "quest_key": questKey, "granted": true}, nil
}
