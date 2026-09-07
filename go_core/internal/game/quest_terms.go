package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
)

// Pinned quest terms (v0.24.0).
//
// Until now, what a quest asked for and what it paid were read from the
// definition at the moment a player reported progress - and they arrived in
// the engine as fields of the caller's payload. That is two faults wearing one
// coat. A GM editing a definition silently rewrote a deal a player had already
// accepted, and Python, not the engine, was deciding what the work was and
// what it was worth.
//
// A commission never had the first fault. It locks its variant and its
// deadline onto the character_quests row at accept, exactly so that a later
// edit cannot change what a held commission pays. This generalises that lock:
// every quest, commission or not, records the terms it was accepted under, and
// quest.progress reads them off the row instead of being told them.
//
// Legacy rows - accepted before this release - carry no pinned terms. They are
// backfilled from the current definition the first time they are touched,
// which is exactly the deal they were already on, and pinned from then on.

// questTermsColumn is the column the pin lives in. Guarded rather than assumed:
// the engine can be pointed at a database Python has not migrated yet, and a
// missing pin degrades to the old behaviour rather than failing every quest.
const questTermsColumn = "terms_json"

// questTerms is a quest as one player accepted it.
type questTerms struct {
	Objectives []map[string]any `json:"objectives"`
	Rewards    map[string]any   `json:"rewards"`
	Label      string           `json:"variant_label,omitempty"`
	// PinnedAtGameMinute is recorded for the audit trail and for the dashboard,
	// which shows a player "accepted Day 14 · terms fixed at acceptance".
	PinnedAtGameMinute int64 `json:"pinned_at_game_minute"`
}

func (t questTerms) empty() bool {
	return len(t.Objectives) == 0 && len(t.Rewards) == 0
}

func encodeQuestTerms(terms questTerms) string {
	if terms.Objectives == nil {
		terms.Objectives = []map[string]any{}
	}
	if terms.Rewards == nil {
		terms.Rewards = map[string]any{}
	}
	encoded, err := json.Marshal(terms)
	if err != nil {
		return ""
	}
	return string(encoded)
}

func decodeQuestTerms(raw any) (questTerms, bool) {
	text, ok := raw.(string)
	if !ok || strings.TrimSpace(text) == "" {
		return questTerms{}, false
	}
	var terms questTerms
	if err := json.Unmarshal([]byte(text), &terms); err != nil {
		return questTerms{}, false
	}
	if terms.empty() {
		return questTerms{}, false
	}
	// A nil objective list and an empty one mean the same thing here, but they
	// do not encode the same way: nil marshals to `null`, which the quest
	// contract rejects outright as a missing objective list.
	if terms.Objectives == nil {
		terms.Objectives = []map[string]any{}
	}
	if terms.Rewards == nil {
		terms.Rewards = map[string]any{}
	}
	return terms, true
}

// questDefinitionTerms reads the terms a definition currently offers. The
// second return says whether the definition exists at all; a quest key with no
// row is not a quest, and the caller decides what to do about that.
//
// `variantIndex` picks a set of commission terms. Objectives never vary - a
// variant is what the giver will pay, not what he is asking for - so they come
// from the definition either way.
func questDefinitionTerms(conn *storage.Conn, questKey string, variantIndex int64) (questTerms, bool, error) {
	if !tableExistsTx(conn, "quest_definitions") {
		return questTerms{}, false, nil
	}
	res, err := conn.Execute(
		`SELECT objectives_json,rewards_json,variants_json,deadline_game_minutes FROM quest_definitions WHERE quest_key=?`,
		[]any{questKey})
	if err != nil {
		return questTerms{}, false, err
	}
	row := firstRowMap(res)
	if row == nil {
		return questTerms{}, false, nil
	}
	terms := questTerms{Objectives: []map[string]any{}, Rewards: map[string]any{}, Label: "standard"}
	if text, ok := row["objectives_json"].(string); ok && strings.TrimSpace(text) != "" {
		if err := json.Unmarshal([]byte(text), &terms.Objectives); err != nil {
			return questTerms{}, true, fmt.Errorf("quest %s has an unreadable objective list: %w", questKey, err)
		}
	}
	if text, ok := row["rewards_json"].(string); ok && strings.TrimSpace(text) != "" {
		if err := json.Unmarshal([]byte(text), &terms.Rewards); err != nil {
			return questTerms{}, true, fmt.Errorf("quest %s has an unreadable reward list: %w", questKey, err)
		}
	}
	if variantIndex > 0 || strings.TrimSpace(fmt.Sprint(row["variants_json"])) != "" {
		variants := decodeCommissionTerms(fmt.Sprint(row["variants_json"]), i64(row["deadline_game_minutes"]))
		if len(variants) > 0 {
			if variantIndex < 0 || variantIndex >= int64(len(variants)) {
				return questTerms{}, true, errors.New("unknown terms for this quest")
			}
			terms.Rewards = variants[variantIndex].Rewards
			terms.Label = variants[variantIndex].Label
		} else if variantIndex != 0 {
			return questTerms{}, true, errors.New("this quest has only the terms offered")
		}
	}
	if terms.Rewards == nil {
		terms.Rewards = map[string]any{}
	}
	return terms, true, nil
}

// pinQuestTermsTx writes the accepted terms onto the player's row. A database
// without the column is left alone: nothing is lost, because the fallback path
// reads the definition, which is what that database was doing anyway.
func pinQuestTermsTx(conn *storage.Conn, userID int64, questKey string, terms questTerms) error {
	pinned, err := tableHasColumns(conn, "character_quests", questTermsColumn)
	if err != nil || !pinned {
		return err
	}
	_, err = conn.Execute(
		`UPDATE character_quests SET terms_json=? WHERE user_id=? AND quest_key=?`,
		[]any{encodeQuestTerms(terms), userID, questKey})
	return err
}

// acceptedQuestTermsTx answers the only question quest.progress needs to ask:
// what did this player agree to? The pinned terms if they are there, otherwise
// today's definition - pinned on the way past, so the answer stops moving.
//
// `storedTerms` is the terms_json value already read with the quest row, so the
// common path costs no extra query.
func acceptedQuestTermsTx(conn *storage.Conn, userID int64, questKey string, variantIndex int64, storedTerms any, gameMinute int64) (questTerms, error) {
	if terms, ok := decodeQuestTerms(storedTerms); ok {
		return terms, nil
	}
	terms, known, err := questDefinitionTerms(conn, questKey, variantIndex)
	if err != nil {
		return questTerms{}, err
	}
	if !known {
		// The definition was deleted out from under a held quest. Refusing
		// would strand the player holding it forever; an empty objective list
		// completes nothing and pays nothing, which leaves them able to
		// abandon it and leaves a GM able to see why.
		return questTerms{Objectives: []map[string]any{}, Rewards: map[string]any{}}, nil
	}
	terms.PinnedAtGameMinute = gameMinute
	if err := pinQuestTermsTx(conn, userID, questKey, terms); err != nil {
		return questTerms{}, err
	}
	return terms, nil
}
