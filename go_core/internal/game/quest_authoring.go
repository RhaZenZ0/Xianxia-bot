package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
)

// Quest authoring (v0.24.0).
//
// Until now a quest definition could be created (by the Forge, from Discord)
// and its status could be changed, and that was the whole vocabulary. There was
// no edit. A drafted quest that was ninety per cent right had to be discarded
// and re-rolled, because nothing anywhere could change a word of it.
//
// `admin.quest.save` is that missing verb, and the reason it belongs in the
// engine rather than in the dashboard is the third argument: what happens to
// the people already carrying the thing being edited. That is a decision about
// live player state, it has to be made in the same transaction as the edit, and
// it is exactly the kind of decision the authority split says Python does not
// get to make.

// questHoldPolicy says what an edit does to players already holding the quest.
type questHoldPolicy string

const (
	// holdPolicyKeep leaves every holder on the terms they accepted. The new
	// version is what anybody taking it from now on gets. This is the default
	// and the only one that cannot cost a player anything.
	holdPolicyKeep questHoldPolicy = "keep"
	// holdPolicyMigrate moves holders onto the new terms, carrying progress
	// across objective by objective wherever the objective still means the
	// same thing. Progress on an objective that changed is lost, and the count
	// of what was lost comes back so the GM is told rather than finding out.
	holdPolicyMigrate questHoldPolicy = "migrate"
	// holdPolicyRevoke takes the quest back. The row is removed, so it can be
	// accepted again from scratch - for when the old version was broken enough
	// that finishing it would be wrong.
	holdPolicyRevoke questHoldPolicy = "revoke"
)

func parseHoldPolicy(raw string) (questHoldPolicy, error) {
	switch questHoldPolicy(strings.TrimSpace(strings.ToLower(raw))) {
	case "", holdPolicyKeep:
		return holdPolicyKeep, nil
	case holdPolicyMigrate:
		return holdPolicyMigrate, nil
	case holdPolicyRevoke:
		return holdPolicyRevoke, nil
	}
	return "", errors.New("hold_policy must be keep, migrate or revoke")
}

// questDefinitionFields is everything a GM may write. `status` is deliberately
// absent: approving, retiring and discarding are their own action, so that
// "I fixed a typo" and "this is now live for players" can never be the same
// click.
type questDefinitionFields struct {
	QuestKey            string           `json:"quest_key"`
	Title               string           `json:"title"`
	Description         string           `json:"description"`
	Objectives          []map[string]any `json:"objectives"`
	Rewards             map[string]any   `json:"rewards"`
	Variants            []map[string]any `json:"variants"`
	GiverNPC            string           `json:"giver_npc"`
	RealmBand           string           `json:"realm_band"`
	Tier                int64            `json:"tier"`
	OwnerUserID         *int64           `json:"owner_user_id"`
	DeadlineGameMinutes int64            `json:"deadline_game_minutes"`
	RequiresSect        string           `json:"requires_sect"`
	RewardVisibility    string           `json:"reward_visibility"`
	Boast               string           `json:"boast"`
	HoldPolicy          string           `json:"hold_policy"`
	Reason              string           `json:"reason"`
}

// objectiveIdentity is what has to match for progress on an objective to
// survive a migration. The count is not part of it: raising "speak with him
// twice" to three times leaves the two conversations that happened having
// happened, and progress is clamped to the new requirement.
type objectiveIdentity struct {
	Type   string
	Target string
}

func objectiveIdentities(objectives []map[string]any) map[string]objectiveIdentity {
	out := make(map[string]objectiveIdentity, len(objectives))
	for _, objective := range objectives {
		id := strings.TrimSpace(fmt.Sprint(objective["id"]))
		if id == "" || id == "<nil>" {
			continue
		}
		out[id] = objectiveIdentity{
			Type:   strings.ToLower(strings.TrimSpace(fmt.Sprint(objective["type"]))),
			Target: strings.ToLower(strings.TrimSpace(stringField(objective, "target"))),
		}
	}
	return out
}

func objectiveCounts(objectives []map[string]any) map[string]int64 {
	out := make(map[string]int64, len(objectives))
	for _, objective := range objectives {
		id := strings.TrimSpace(fmt.Sprint(objective["id"]))
		if id == "" || id == "<nil>" {
			continue
		}
		count := i64(objective["count"])
		if count < 1 {
			count = 1
		}
		out[id] = count
	}
	return out
}

// migrateProgress carries a holder's progress onto the new objective list.
// Returns the surviving progress and how many objectives lost their progress.
func migrateProgress(progress map[string]int64, oldObjectives, newObjectives []map[string]any) (map[string]int64, int64) {
	before := objectiveIdentities(oldObjectives)
	after := objectiveIdentities(newObjectives)
	counts := objectiveCounts(newObjectives)
	carried := map[string]int64{}
	dropped := int64(0)
	for id, done := range progress {
		if done <= 0 {
			continue
		}
		next, stillThere := after[id]
		if !stillThere || next != before[id] {
			// Either the objective is gone, or it now asks for something else.
			dropped++
			continue
		}
		carried[id] = clamp(done, 0, counts[id])
	}
	return carried, dropped
}

type questHolder struct {
	UserID     int64
	Progress   map[string]int64
	Terms      questTerms
	Commission bool
}

// activeQuestHolders reads everyone currently carrying the quest, with the
// terms they are carrying it under.
func activeQuestHolders(conn *storage.Conn, questKey string) ([]questHolder, error) {
	if !tableExistsTx(conn, "character_quests") {
		return nil, nil
	}
	pinned, err := tableHasColumns(conn, "character_quests", questTermsColumn)
	if err != nil {
		return nil, err
	}
	columns := `user_id,progress_json,variant_index,commission`
	if pinned {
		columns += `,` + questTermsColumn
	}
	res, err := conn.Execute(
		`SELECT `+columns+` FROM character_quests WHERE quest_key=? AND status='active'`,
		[]any{questKey})
	if err != nil {
		return nil, err
	}
	holders := make([]questHolder, 0, len(res.Rows))
	for _, raw := range res.Rows {
		row := rowMap(res.Columns, raw)
		holder := questHolder{
			UserID:     i64(row["user_id"]),
			Progress:   map[string]int64{},
			Commission: i64(row["commission"]) == 1,
		}
		if text, ok := row["progress_json"].(string); ok && text != "" {
			_ = json.Unmarshal([]byte(text), &holder.Progress)
		}
		if terms, ok := decodeQuestTerms(row[questTermsColumn]); ok {
			holder.Terms = terms
		} else {
			// No pin yet: they are on the definition as it stands right now,
			// which is what is about to be overwritten. Read it before the
			// write, or "keep them as they are" quietly means the opposite.
			current, _, termsErr := questDefinitionTerms(conn, questKey, i64(row["variant_index"]))
			if termsErr != nil {
				return nil, termsErr
			}
			holder.Terms = current
		}
		holders = append(holders, holder)
	}
	return holders, nil
}

func questDefinitionRow(conn *storage.Conn, questKey string) (map[string]any, error) {
	res, err := conn.Execute(
		`SELECT quest_key,title,description,status,objectives_json,rewards_json,variants_json,giver_npc,
		        realm_band,tier,owner_user_id,deadline_game_minutes,
		        COALESCE(requires_sect,'') AS requires_sect,
		        COALESCE(reward_visibility,'shown') AS reward_visibility,
		        COALESCE(boast,'') AS boast
		 FROM quest_definitions WHERE quest_key=?`, []any{questKey})
	if err != nil {
		return nil, err
	}
	return firstRowMap(res), nil
}

// adminQuestSave creates or edits a definition and settles what happens to
// everybody holding it, in one transaction.
func adminQuestSave(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	var p questDefinitionFields
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	questKey := strings.TrimSpace(p.QuestKey)
	if questKey == "" {
		return nil, errors.New("quest_key is required")
	}
	if strings.TrimSpace(p.Title) == "" {
		return nil, errors.New("a quest needs a title")
	}
	policy, err := parseHoldPolicy(p.HoldPolicy)
	if err != nil {
		return nil, err
	}
	if p.Objectives == nil {
		p.Objectives = []map[string]any{}
	}
	if p.Rewards == nil {
		p.Rewards = map[string]any{}
	}
	if p.Variants == nil {
		p.Variants = []map[string]any{}
	}
	visibility := strings.TrimSpace(strings.ToLower(p.RewardVisibility))
	if visibility == "" {
		visibility = "shown"
	}
	if visibility != "shown" && visibility != "hidden" {
		return nil, errors.New("reward_visibility must be shown or hidden")
	}
	tier := p.Tier
	if tier < 1 {
		tier = 1
	}
	objectivesJSON, err := json.Marshal(p.Objectives)
	if err != nil {
		return nil, err
	}
	rewardsJSON, err := json.Marshal(p.Rewards)
	if err != nil {
		return nil, err
	}
	variantsJSON, err := json.Marshal(p.Variants)
	if err != nil {
		return nil, err
	}

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}

	before, err := questDefinitionRow(conn, questKey)
	if err != nil {
		return nil, err
	}
	// Read the holders BEFORE the definition changes: an unpinned holder is on
	// the old definition, and once it is overwritten there is no way back to
	// what they agreed to.
	holders, err := activeQuestHolders(conn, questKey)
	if err != nil {
		return nil, err
	}

	now := nowSeconds()
	created := before == nil
	if created {
		if _, err := conn.Execute(
			`INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,
			     rewards_json,status,origin,story_prompt,model,created_by,created_at,updated_at,
			     giver_npc,realm_band,tier,owner_user_id,deadline_game_minutes,variants_json,seed_json,
			     requires_sect,reward_visibility,boast)
			 VALUES(?,?,?,'authored','',?,?,'draft','gm_dashboard','','',?,?,?,?,?,?,?,?,?,'{}',?,?,?)`,
			[]any{
				questKey, strings.TrimSpace(p.Title), p.Description, string(objectivesJSON), string(rewardsJSON),
				adminUserID, now, now, p.GiverNPC, p.RealmBand, tier, ownerValue(p.OwnerUserID),
				maxI64(0, p.DeadlineGameMinutes), string(variantsJSON), p.RequiresSect, visibility, p.Boast,
			}); err != nil {
			return nil, err
		}
	} else {
		if _, err := conn.Execute(
			`UPDATE quest_definitions SET title=?,description=?,objectives_json=?,rewards_json=?,variants_json=?,
			     giver_npc=?,realm_band=?,tier=?,owner_user_id=?,deadline_game_minutes=?,
			     requires_sect=?,reward_visibility=?,boast=?,updated_at=?
			 WHERE quest_key=?`,
			[]any{
				strings.TrimSpace(p.Title), p.Description, string(objectivesJSON), string(rewardsJSON),
				string(variantsJSON), p.GiverNPC, p.RealmBand, tier, ownerValue(p.OwnerUserID),
				maxI64(0, p.DeadlineGameMinutes), p.RequiresSect, visibility, p.Boast, now, questKey,
			}); err != nil {
			return nil, err
		}
	}

	newTerms := questTerms{Objectives: p.Objectives, Rewards: p.Rewards, Label: "standard", PinnedAtGameMinute: gameMinute}
	affected := make([]map[string]any, 0, len(holders))
	totalDropped := int64(0)
	for _, holder := range holders {
		switch policy {
		case holdPolicyKeep:
			// Write the terms they were already on. For a holder who already
			// had a pin this changes nothing; for one who did not, it is the
			// difference between keeping their deal and silently moving them.
			if err := pinQuestTermsTx(conn, holder.UserID, questKey, holder.Terms); err != nil {
				return nil, err
			}
			affected = append(affected, map[string]any{"user_id": holder.UserID, "outcome": "kept"})
		case holdPolicyMigrate:
			carried, dropped := migrateProgress(holder.Progress, holder.Terms.Objectives, p.Objectives)
			totalDropped += dropped
			encoded, encodeErr := json.Marshal(carried)
			if encodeErr != nil {
				return nil, encodeErr
			}
			if _, err := conn.Execute(
				`UPDATE character_quests SET progress_json=?,updated_at=? WHERE user_id=? AND quest_key=?`,
				[]any{string(encoded), now, holder.UserID, questKey}); err != nil {
				return nil, err
			}
			migrated := newTerms
			if holder.Commission {
				// A commission's payment is the variant it was taken on, and
				// that is a promise about money made by a named person. The
				// objectives move; what he agreed to pay does not.
				migrated.Rewards = holder.Terms.Rewards
				migrated.Label = holder.Terms.Label
			}
			if err := pinQuestTermsTx(conn, holder.UserID, questKey, migrated); err != nil {
				return nil, err
			}
			affected = append(affected, map[string]any{
				"user_id": holder.UserID, "outcome": "migrated", "objectives_dropped": dropped,
			})
		case holdPolicyRevoke:
			if _, err := conn.Execute(
				`DELETE FROM character_quests WHERE user_id=? AND quest_key=?`,
				[]any{holder.UserID, questKey}); err != nil {
				return nil, err
			}
			affected = append(affected, map[string]any{"user_id": holder.UserID, "outcome": "revoked"})
		}
	}

	out := map[string]any{
		"quest_key":          questKey,
		"title":              strings.TrimSpace(p.Title),
		"created":            created,
		"hold_policy":        string(policy),
		"holders":            len(holders),
		"affected":           affected,
		"objectives_dropped": totalDropped,
		"status":             definitionStatus(before),
	}
	beforeAudit := map[string]any{"existed": !created}
	if before != nil {
		beforeAudit["title"] = fmt.Sprint(before["title"])
		beforeAudit["objectives_json"] = fmt.Sprint(before["objectives_json"])
		beforeAudit["rewards_json"] = fmt.Sprint(before["rewards_json"])
		beforeAudit["giver_npc"] = fmt.Sprint(before["giver_npc"])
	}
	if err := auditAdmin(conn, adminUserID, "admin.quest.save", questKey, beforeAudit, out, p.Reason); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}

func definitionStatus(before map[string]any) string {
	if before == nil {
		return "draft"
	}
	return fmt.Sprint(before["status"])
}

func ownerValue(owner *int64) any {
	if owner == nil {
		return nil
	}
	return *owner
}
