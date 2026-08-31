package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
)

var dynastyClaimTypes = map[string]bool{
	"inheritance":           true,
	"restoration":           true,
	"revenge":               true,
	"replacement_challenge": true,
}

var dynastyConflictTactics = map[string]int64{
	"negotiate":   8,
	"expose":      12,
	"rally":       10,
	"investigate": 7,
	"duel":        15,
}

var dynastyQuestAttributes = map[string]string{
	"archive_research":   "insight",
	"ruin_excavation":    "agility",
	"tomb_inquest":       "will",
	"retainer_testimony": "presence",
}

var dynastyConflictAttributes = map[string]string{
	"negotiate":   "presence",
	"expose":      "insight",
	"rally":       "presence",
	"investigate": "insight",
	"duel":        "will",
}

// Indirection keeps dynasty resolution deterministic in tests while production uses
// the same cryptographically-backed 2d10 resolver as the rest of the Go action layer.
var dynastyRoll2d10 = roll2d10

type dynastyQuestPayload struct {
	HistoryID  int64 `json:"history_id"`
	QuestID    int64 `json:"quest_id"`
	GameMinute int64 `json:"game_minute"`
}

type dynastyClaimPayload struct {
	HistoryID  int64  `json:"history_id"`
	ClaimType  string `json:"claim_type"`
	GameMinute int64  `json:"game_minute"`
}

type dynastyConflictPayload struct {
	ClaimID    int64  `json:"claim_id"`
	Tactic     string `json:"tactic"`
	GameMinute int64  `json:"game_minute"`
}

type dynastyLeadDefinition struct {
	Kind             string
	Name             string
	Description      string
	ClueRequired     int64
	Danger           int64
	EvidenceWeight   int64
	QuestKind        string
	QuestTitle       string
	QuestDescription string
	QuestTarget      int64
	HostileCause     bool
	CulpritName      string
	RetainerName     string
	RetainerRelation string
}

func ensureLivingDynastyCharacter(conn *storage.Conn, userID int64) error {
	row, err := conn.Execute(`SELECT life_status FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return err
	}
	if len(row.Rows) == 0 {
		return errors.New("character not found")
	}
	if fmt.Sprint(row.Rows[0][0]) != "alive" {
		return errors.New("dynasty actions require a living incarnation")
	}
	return nil
}

func dynastyHostileCause(historyID int64, status string) (bool, string) {
	switch strings.TrimSpace(status) {
	case "fallen_severed_branch":
		return true, "the Oathbreaker Coalition"
	case "extinct_branch_replaced":
		if historyID%2 == 0 {
			return true, "the succession-war usurpers"
		}
	}
	return false, ""
}

func dynastyLegacyDefinitions(historyID int64, sourceFamily, destinationFamily, status string) []dynastyLeadDefinition {
	hostile, culprit := dynastyHostileCause(historyID, status)
	sourceFamily = firstNonempty(strings.TrimSpace(sourceFamily), "Lost House")
	destinationFamily = firstNonempty(strings.TrimSpace(destinationFamily), "Current House")
	return []dynastyLeadDefinition{
		{
			Kind:         "archive",
			Name:         fmt.Sprintf("Sealed Registry of %s", sourceFamily),
			Description:  fmt.Sprintf("Household rolls, deeds and sect correspondence that may show how %s changed before %s appeared.", sourceFamily, destinationFamily),
			ClueRequired: 1, Danger: 10, EvidenceWeight: 15,
			QuestKind:        "archive_research",
			QuestTitle:       "Break the Archive Seals",
			QuestDescription: "Compare household registries, property transfers, sect records and ancestral seals without accepting a single source as proof.",
			QuestTarget:      1,
		},
		{
			Kind:         "ruin",
			Name:         fmt.Sprintf("%s Branch Ruins", sourceFamily),
			Description:  "Collapsed courtyards and cultivation chambers where inscriptions, workshop marks and battle damage can preserve material evidence.",
			ClueRequired: 2, Danger: 35, EvidenceWeight: 20,
			QuestKind:        "ruin_excavation",
			QuestTitle:       "Excavate the Ancestral Ruins",
			QuestDescription: "Search the ruined estate for seals, formation scars, hidden ledgers and possessions that can be cross-checked against the archive.",
			QuestTarget:      2,
		},
		{
			Kind:         "tomb",
			Name:         fmt.Sprintf("Last-Heir Tomb of %s", sourceFamily),
			Description:  "A sealed burial site attributed to the final known branch generation. Tomb evidence can confirm succession, extinction, or an escaped heir.",
			ClueRequired: 2, Danger: 50, EvidenceWeight: 25,
			QuestKind:        "tomb_inquest",
			QuestTitle:       "Open the Last-Heir Tomb",
			QuestDescription: "Survive the tomb wards and establish who was actually buried there before drawing any inheritance conclusion.",
			QuestTarget:      2,
		},
		{
			Kind:         "retainer",
			Name:         "Last Oathkeeper",
			Description:  "A surviving long-lived retainer or oathkeeper line preserves testimony unavailable in official family histories.",
			ClueRequired: 3, Danger: 25, EvidenceWeight: 30,
			QuestKind:        "retainer_testimony",
			QuestTitle:       "Find the Last Oathkeeper",
			QuestDescription: "Earn the trust of the surviving retainer and corroborate their testimony against physical and written evidence.",
			QuestTarget:      2,
			HostileCause:     hostile,
			CulpritName:      culprit,
			RetainerName:     "Elder Mo, Last Oathkeeper",
			RetainerRelation: "surviving ancestral retainer",
		},
	}
}

func dynastyDestinationLocation(conn *storage.Conn, destinationFamilyID int64, destinationWorld string) string {
	if destinationFamilyID > 0 {
		row, err := conn.Execute(`SELECT location FROM birth_families WHERE family_id=?`, []any{destinationFamilyID})
		if err == nil && len(row.Rows) > 0 {
			if location := strings.TrimSpace(fmt.Sprint(row.Rows[0][0])); location != "" {
				return location
			}
		}
	}
	return firstNonempty(strings.TrimSpace(destinationWorld), "Unknown World")
}

func ensureDynastyLegacyContent(conn *storage.Conn, userID, historyID, gameMinute int64) error {
	row, err := conn.Execute(`
		SELECT source_family_name,destination_family_name,destination_world,lineage_status,destination_family_id
		FROM samsara_dynasty_history
		WHERE history_id=? AND user_id=?`, []any{historyID, userID})
	if err != nil {
		return err
	}
	if len(row.Rows) == 0 {
		return errors.New("dynasty history record not found")
	}
	r := row.Rows[0]
	sourceFamily := fmt.Sprint(r[0])
	destinationFamily := fmt.Sprint(r[1])
	destinationWorld := fmt.Sprint(r[2])
	status := fmt.Sprint(r[3])
	destinationFamilyID := i64(r[4])
	location := dynastyDestinationLocation(conn, destinationFamilyID, destinationWorld)
	now := float64(time.Now().UnixNano()) / 1e9

	for _, def := range dynastyLegacyDefinitions(historyID, sourceFamily, destinationFamily, status) {
		_, err = conn.Execute(`
			INSERT OR IGNORE INTO samsara_ancestral_leads(
				user_id,history_id,lead_kind,name,location,world_name,description,status,clue_required,danger,
				evidence_weight,retainer_name,retainer_relation,created_game_minute,created_at,updated_at
			) VALUES(?,?,?,?,?,?,?,'hidden',?,?,?,?,?,?,?,?)`,
			[]any{
				userID, historyID, def.Kind, def.Name, location, destinationWorld, def.Description,
				def.ClueRequired, def.Danger, def.EvidenceWeight, def.RetainerName, def.RetainerRelation,
				gameMinute, now, now,
			})
		if err != nil {
			return err
		}
		leadRow, err := conn.Execute(`SELECT lead_id FROM samsara_ancestral_leads WHERE history_id=? AND lead_kind=?`, []any{historyID, def.Kind})
		if err != nil || len(leadRow.Rows) == 0 {
			return firstNonNilError(err, errors.New("ancestral lead could not be resolved"))
		}
		leadID := i64(leadRow.Rows[0][0])
		hostile := int64(0)
		if def.HostileCause {
			hostile = 1
		}
		_, err = conn.Execute(`
			INSERT OR IGNORE INTO samsara_investigation_quests(
				user_id,history_id,lead_id,quest_kind,title,description,status,progress,target,reward_evidence,
				hostile_cause,culprit_name,created_game_minute,created_at,updated_at
			) VALUES(?,?,?,?,?,?,'locked',0,?,?,?,?,?,?,?)`,
			[]any{
				userID, historyID, leadID, def.QuestKind, def.QuestTitle, def.QuestDescription,
				def.QuestTarget, def.EvidenceWeight, hostile, def.CulpritName, gameMinute, now, now,
			})
		if err != nil {
			return err
		}
	}
	return nil
}

func unlockDynastyLegacyContent(conn *storage.Conn, userID, historyID, investigationLevel, gameMinute int64) ([]map[string]any, []map[string]any, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`
		UPDATE samsara_ancestral_leads
		SET status='discovered',discovered_game_minute=COALESCE(discovered_game_minute,?),updated_at=?
		WHERE user_id=? AND history_id=? AND status='hidden' AND clue_required<=?`,
		[]any{gameMinute, now, userID, historyID, investigationLevel}); err != nil {
		return nil, nil, err
	}
	if _, err := conn.Execute(`
		UPDATE samsara_investigation_quests
		SET status='available',updated_at=?
		WHERE user_id=? AND history_id=? AND status='locked'
		  AND lead_id IN (
			SELECT lead_id FROM samsara_ancestral_leads
			WHERE user_id=? AND history_id=? AND status IN ('discovered','resolved')
		  )`,
		[]any{now, userID, historyID, userID, historyID}); err != nil {
		return nil, nil, err
	}

	leadRows, err := conn.Execute(`
		SELECT lead_id,lead_kind,name,location,world_name,description,status,danger,retainer_name,retainer_relation
		FROM samsara_ancestral_leads
		WHERE user_id=? AND history_id=? AND status!='hidden'
		ORDER BY clue_required,lead_id`, []any{userID, historyID})
	if err != nil {
		return nil, nil, err
	}
	leads := make([]map[string]any, 0, len(leadRows.Rows))
	for _, r := range leadRows.Rows {
		leads = append(leads, map[string]any{
			"lead_id": i64(r[0]), "lead_kind": fmt.Sprint(r[1]), "name": fmt.Sprint(r[2]),
			"location": fmt.Sprint(r[3]), "world": fmt.Sprint(r[4]), "description": fmt.Sprint(r[5]),
			"status": fmt.Sprint(r[6]), "danger": i64(r[7]), "retainer_name": fmt.Sprint(r[8]),
			"retainer_relation": fmt.Sprint(r[9]),
		})
	}

	questRows, err := conn.Execute(`
		SELECT quest_id,lead_id,quest_kind,title,description,status,progress,target,reward_evidence,hostile_cause,culprit_name
		FROM samsara_investigation_quests
		WHERE user_id=? AND history_id=? AND status!='locked'
		ORDER BY quest_id`, []any{userID, historyID})
	if err != nil {
		return nil, nil, err
	}
	quests := make([]map[string]any, 0, len(questRows.Rows))
	for _, r := range questRows.Rows {
		quests = append(quests, map[string]any{
			"quest_id": i64(r[0]), "lead_id": i64(r[1]), "quest_kind": fmt.Sprint(r[2]),
			"title": fmt.Sprint(r[3]), "description": fmt.Sprint(r[4]), "status": fmt.Sprint(r[5]),
			"progress": i64(r[6]), "target": i64(r[7]), "reward_evidence": i64(r[8]),
			"hostile_cause": i64(r[9]) != 0, "culprit_name": fmt.Sprint(r[10]),
		})
	}
	return leads, quests, nil
}

func dynastyActionAttribute(conn *storage.Conn, userID int64, attr string) (int64, error) {
	attr = strings.TrimSpace(strings.ToLower(attr))
	row, err := conn.Execute(`SELECT attributes_json FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(row.Rows) == 0 {
		return 0, errors.New("living character not found")
	}
	attrs := map[string]any{}
	if err := json.Unmarshal([]byte(fmt.Sprint(row.Rows[0][0])), &attrs); err != nil {
		return 0, err
	}
	value, ok := attrs[attr]
	if !ok {
		return 0, fmt.Errorf("character attribute %s is unavailable", attr)
	}
	return storage.ParseInt(value), nil
}

func dynastyQuestTN(danger int64) int64 {
	// Danger is intentionally mechanical: 10 -> TN 14, 25 -> TN 17,
	// 35 -> TN 19, 50 -> TN 22. The full 0-100 range remains bounded.
	danger = clampI64(danger, 0, 100)
	return 12 + (danger+4)/5
}

func dynastyPhysicalQuest(kind string) bool {
	switch strings.TrimSpace(kind) {
	case "ruin_excavation", "tomb_inquest":
		return true
	default:
		return false
	}
}

func dynastyQuestAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var payload dynastyQuestPayload
	if len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if err := ensureLivingDynastyCharacter(conn, userID); err != nil {
		return authoritativeMutation{}, err
	}
	if payload.HistoryID <= 0 {
		return authoritativeMutation{}, errors.New("history_id is required")
	}
	if err := ensureDynastyLegacyContent(conn, userID, payload.HistoryID, payload.GameMinute); err != nil {
		return authoritativeMutation{}, err
	}
	history, err := conn.Execute(`SELECT investigation_level FROM samsara_dynasty_history WHERE history_id=? AND user_id=?`, []any{payload.HistoryID, userID})
	if err != nil || len(history.Rows) == 0 {
		return authoritativeMutation{}, firstNonNilError(err, errors.New("dynasty history record not found"))
	}
	level := i64(history.Rows[0][0])
	if _, _, err = unlockDynastyLegacyContent(conn, userID, payload.HistoryID, level, payload.GameMinute); err != nil {
		return authoritativeMutation{}, err
	}

	query := `
		SELECT q.quest_id,q.lead_id,q.quest_kind,q.title,q.status,q.progress,q.target,q.reward_evidence,
		       q.hostile_cause,q.culprit_name,l.danger
		FROM samsara_investigation_quests AS q
		JOIN samsara_ancestral_leads AS l ON l.lead_id=q.lead_id AND l.user_id=q.user_id
		WHERE q.user_id=? AND q.history_id=? AND q.status IN ('available','active')`
	args := []any{userID, payload.HistoryID}
	if payload.QuestID > 0 {
		query += ` AND q.quest_id=?`
		args = append(args, payload.QuestID)
	}
	query += ` ORDER BY q.quest_id LIMIT 1`
	row, err := conn.Execute(query, args)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(row.Rows) == 0 {
		return authoritativeMutation{}, errors.New("no available investigation quest; investigate the dynasty record further or choose an unfinished quest")
	}
	r := row.Rows[0]
	questID := i64(r[0])
	leadID := i64(r[1])
	questKind := fmt.Sprint(r[2])
	progress := i64(r[5])
	target := i64(r[6])
	danger := clampI64(i64(r[10]), 0, 100)
	attribute := dynastyQuestAttributes[questKind]
	if attribute == "" {
		attribute = "will"
	}
	modifier, err := dynastyActionAttribute(conn, userID, attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	tn := dynastyQuestTN(danger)
	roll, err := dynastyRoll2d10(modifier, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := boolResult(roll)
	margin := resultMargin(roll)
	progressGain := int64(0)
	setback := int64(0)
	vitalityLoss := int64(0)
	if success {
		progressGain = 1
		progress = minI64(target, progress+progressGain)
	} else {
		// Hard failures can erase one previously-earned step. Physical leads can
		// also inflict non-lethal vitality damage scaled by their displayed danger.
		if margin <= -4 && progress > 0 {
			setback = 1
			progress--
		}
		if margin <= -4 && dynastyPhysicalQuest(questKind) && danger >= 25 {
			vitalityLoss = maxI64(1, danger/20)
		}
	}
	status := "active"
	completed := progress >= target
	if completed {
		progress = target
		status = "completed"
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if vitalityLoss > 0 {
		if _, err = conn.Execute(`UPDATE characters SET vitality=MAX(1,vitality-?),updated_at=? WHERE user_id=?`, []any{vitalityLoss, now, userID}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	completedMinute := any(nil)
	if completed {
		completedMinute = payload.GameMinute
	}
	if _, err = conn.Execute(`
		UPDATE samsara_investigation_quests
		SET status=?,progress=?,completed_game_minute=COALESCE(completed_game_minute,?),updated_at=?
		WHERE quest_id=? AND user_id=?`,
		[]any{status, progress, completedMinute, now, questID, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if completed {
		if _, err = conn.Execute(`
			UPDATE samsara_ancestral_leads
			SET status='resolved',resolved_game_minute=COALESCE(resolved_game_minute,?),updated_at=?
			WHERE lead_id=? AND user_id=?`, []any{payload.GameMinute, now, leadID, userID}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	result := map[string]any{
		"history_id": payload.HistoryID, "quest_id": questID, "lead_id": leadID,
		"quest_kind": questKind, "title": fmt.Sprint(r[3]), "status": status,
		"progress": progress, "target": target, "completed": completed,
		"danger": danger, "attribute": attribute, "difficulty_tn": tn, "roll": roll, "success": success,
		"progress_gain": progressGain, "setback": setback, "vitality_loss": vitalityLoss,
		"reward_evidence": i64(r[7]), "hostile_cause": i64(r[8]) != 0, "culprit_name": fmt.Sprint(r[9]),
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "family", EventType: "family.lineage.quest", EntityType: "samsara_investigation_quest",
			EntityID: fmt.Sprint(questID), SubjectType: "character", SubjectID: fmt.Sprint(userID),
			GameMinute: payload.GameMinute, Payload: result,
		},
	}, nil
}

func dynastyCompletedQuestStats(conn *storage.Conn, userID, historyID int64) (int64, bool, string, error) {
	rows, err := conn.Execute(`
		SELECT hostile_cause,culprit_name
		FROM samsara_investigation_quests
		WHERE user_id=? AND history_id=? AND status='completed'`, []any{userID, historyID})
	if err != nil {
		return 0, false, "", err
	}
	hostile := false
	culprit := ""
	for _, r := range rows.Rows {
		if i64(r[0]) != 0 {
			hostile = true
			if culprit == "" {
				culprit = fmt.Sprint(r[1])
			}
		}
	}
	return int64(len(rows.Rows)), hostile, culprit, nil
}

func dynastyClaimAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var payload dynastyClaimPayload
	if len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if err := ensureLivingDynastyCharacter(conn, userID); err != nil {
		return authoritativeMutation{}, err
	}
	claimType := strings.TrimSpace(strings.ToLower(payload.ClaimType))
	if payload.HistoryID <= 0 || !dynastyClaimTypes[claimType] {
		return authoritativeMutation{}, errors.New("history_id and a valid claim_type are required")
	}
	row, err := conn.Execute(`
		SELECT source_family_name,source_world,destination_family_name,destination_world,lineage_status,
		       blood_continuity,investigation_level
		FROM samsara_dynasty_history WHERE history_id=? AND user_id=?`,
		[]any{payload.HistoryID, userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(row.Rows) == 0 {
		return authoritativeMutation{}, errors.New("dynasty history record not found")
	}
	r := row.Rows[0]
	sourceFamily := fmt.Sprint(r[0])
	sourceWorld := fmt.Sprint(r[1])
	destinationFamily := fmt.Sprint(r[2])
	destinationWorld := fmt.Sprint(r[3])
	lineageStatus := fmt.Sprint(r[4])
	bloodContinuity := i64(r[5]) != 0
	investigationLevel := i64(r[6])
	if investigationLevel < maxDynastyInvestigationLevel {
		return authoritativeMutation{}, errors.New("the dynasty record must be fully investigated before making a claim")
	}
	if err = ensureDynastyLegacyContent(conn, userID, payload.HistoryID, payload.GameMinute); err != nil {
		return authoritativeMutation{}, err
	}
	completed, hostile, culprit, err := dynastyCompletedQuestStats(conn, userID, payload.HistoryID)
	if err != nil {
		return authoritativeMutation{}, err
	}

	switch claimType {
	case "inheritance":
		if !bloodContinuity {
			return authoritativeMutation{}, errors.New("blood inheritance is unavailable: this record confirms no blood continuity")
		}
		if completed < 2 {
			return authoritativeMutation{}, errors.New("complete at least two ancestral investigation quests before asserting inheritance")
		}
	case "restoration":
		if lineageStatus != "fallen_severed_branch" && lineageStatus != "extinct_branch_replaced" {
			return authoritativeMutation{}, errors.New("dynasty restoration requires a fallen or extinct historical house")
		}
		if completed < 2 {
			return authoritativeMutation{}, errors.New("complete at least two ancestral investigation quests before attempting restoration")
		}
	case "revenge":
		if !hostile || strings.TrimSpace(culprit) == "" {
			return authoritativeMutation{}, errors.New("no investigated evidence establishes a hostile culprit; revenge cannot be claimed")
		}
	case "replacement_challenge":
		if lineageStatus != "extinct_branch_replaced" {
			return authoritativeMutation{}, errors.New("replacement-family challenges require an extinct_branch_replaced record")
		}
		if completed < 3 {
			return authoritativeMutation{}, errors.New("complete at least three ancestral investigation quests before challenging the replacement house")
		}
	}

	legitimacy := int64(35 + investigationLevel*10 + completed*8)
	if bloodContinuity {
		legitimacy += 12
	}
	if legitimacy > 100 {
		legitimacy = 100
	}
	support := int64(20 + completed*10)
	if support > 100 {
		support = 100
	}
	// Minimum-evidence claims keep the historical 55 opposition baseline;
	// each additional completed investigation quest meaningfully weakens it.
	opposition := maxI64(35, 65-completed*5)
	bloodBased := int64(0)
	if claimType == "inheritance" {
		bloodBased = 1
	}
	targetFamily := sourceFamily
	targetWorld := sourceWorld
	status := "contested"
	resolution := ""
	if claimType == "inheritance" && legitimacy >= 75 {
		status = "recognized"
		resolution = "ancestral_inheritance_recognized"
	}
	if claimType == "replacement_challenge" {
		targetFamily = destinationFamily
		targetWorld = destinationWorld
	}
	if claimType == "revenge" {
		targetFamily = culprit
		targetWorld = destinationWorld
	}
	now := float64(time.Now().UnixNano()) / 1e9

	_, err = conn.Execute(`
		INSERT INTO samsara_dynasty_claims(
			user_id,history_id,claim_type,dynasty_name,target_family_name,target_world,status,legitimacy,support,
			opposition,blood_based,resolution,created_game_minute,resolved_game_minute,created_at,updated_at
		) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(user_id,history_id,claim_type) DO UPDATE SET
			target_family_name=excluded.target_family_name,target_world=excluded.target_world,
			legitimacy=MAX(samsara_dynasty_claims.legitimacy,excluded.legitimacy),
			support=MAX(samsara_dynasty_claims.support,excluded.support),updated_at=excluded.updated_at`,
		[]any{
			userID, payload.HistoryID, claimType, sourceFamily, targetFamily, targetWorld, status, legitimacy, support,
			opposition, bloodBased, resolution, payload.GameMinute,
			func() any {
				if status == "recognized" {
					return payload.GameMinute
				}
				return nil
			}(),
			now, now,
		})
	if err != nil {
		return authoritativeMutation{}, err
	}
	existing, queryErr := conn.Execute(`
		SELECT claim_id,status,resolution,legitimacy,support
		FROM samsara_dynasty_claims
		WHERE user_id=? AND history_id=? AND claim_type=?`,
		[]any{userID, payload.HistoryID, claimType})
	if queryErr != nil || len(existing.Rows) == 0 {
		return authoritativeMutation{}, firstNonNilError(queryErr, errors.New("dynasty claim could not be resolved"))
	}
	claimID := i64(existing.Rows[0][0])
	status = fmt.Sprint(existing.Rows[0][1])
	resolution = fmt.Sprint(existing.Rows[0][2])
	legitimacy = i64(existing.Rows[0][3])
	support = i64(existing.Rows[0][4])

	conflictID := int64(0)
	if status == "contested" {
		conflictType := "dynasty_legitimacy_dispute"
		opponent := "Local Legacy Custodians"
		opponentFamily := ""
		stakes := fmt.Sprintf("Recognition of %s's historical legacy", sourceFamily)
		switch claimType {
		case "restoration":
			conflictType = "dynasty_restoration"
			opponent = "Entrenched Local Powers"
			stakes = fmt.Sprintf("Restoration of the %s name and legacy", sourceFamily)
		case "revenge":
			conflictType = "ancestral_revenge"
			opponent = culprit
			stakes = fmt.Sprintf("Judgment against %s for the investigated destruction of %s", culprit, sourceFamily)
		case "replacement_challenge":
			conflictType = "replacement_legacy_dispute"
			opponent = destinationFamily
			opponentFamily = destinationFamily
			stakes = fmt.Sprintf("Control of the extinct %s legacy formerly occupied by %s", sourceFamily, destinationFamily)
		}
		_, conflictErr := conn.Execute(`
			INSERT OR IGNORE INTO samsara_dynasty_conflicts(
				user_id,claim_id,history_id,conflict_type,opponent_name,opponent_family_name,stakes,status,
				player_progress,opponent_progress,rounds,last_tactic,outcome,created_game_minute,created_at,updated_at
			) VALUES(?,?,?,?,?,?,?,'active',0,0,0,'','',?,?,?)`,
			[]any{userID, claimID, payload.HistoryID, conflictType, opponent, opponentFamily, stakes, payload.GameMinute, now, now})
		if conflictErr != nil {
			return authoritativeMutation{}, conflictErr
		}
		existingConflict, queryErr := conn.Execute(`SELECT conflict_id FROM samsara_dynasty_conflicts WHERE claim_id=?`, []any{claimID})
		if queryErr != nil || len(existingConflict.Rows) == 0 {
			return authoritativeMutation{}, firstNonNilError(queryErr, errors.New("dynasty conflict could not be resolved"))
		}
		conflictID = i64(existingConflict.Rows[0][0])
	}

	result := map[string]any{
		"claim_id": claimID, "history_id": payload.HistoryID, "claim_type": claimType,
		"dynasty_name": sourceFamily, "target_family": targetFamily, "target_world": targetWorld,
		"status": status, "legitimacy": legitimacy, "support": support, "opposition": opposition, "blood_based": bloodBased != 0,
		"resolution": resolution, "conflict_id": conflictID,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "family", EventType: "family.dynasty.claim", EntityType: "samsara_dynasty_claim",
			EntityID: fmt.Sprint(claimID), SubjectType: "character", SubjectID: fmt.Sprint(userID),
			GameMinute: payload.GameMinute, Payload: result,
		},
	}, nil
}

func dynastyConflictAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var payload dynastyConflictPayload
	if len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if err := ensureLivingDynastyCharacter(conn, userID); err != nil {
		return authoritativeMutation{}, err
	}
	tactic := strings.TrimSpace(strings.ToLower(payload.Tactic))
	bonus, ok := dynastyConflictTactics[tactic]
	if payload.ClaimID <= 0 || !ok {
		return authoritativeMutation{}, errors.New("claim_id and a valid tactic are required")
	}
	row, err := conn.Execute(`
		SELECT c.conflict_id,c.history_id,c.conflict_type,c.opponent_name,c.opponent_family_name,c.stakes,c.status,
		       c.player_progress,c.opponent_progress,c.rounds,
		       cl.claim_type,cl.legitimacy,cl.support,cl.opposition,cl.dynasty_name
		FROM samsara_dynasty_conflicts AS c
		JOIN samsara_dynasty_claims AS cl ON cl.claim_id=c.claim_id
		WHERE c.claim_id=? AND c.user_id=?`, []any{payload.ClaimID, userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(row.Rows) == 0 {
		return authoritativeMutation{}, errors.New("no dynasty conflict exists for that claim")
	}
	r := row.Rows[0]
	if fmt.Sprint(r[6]) != "active" {
		return authoritativeMutation{}, errors.New("that dynasty conflict is already resolved")
	}
	playerProgress := i64(r[7])
	opponentProgress := i64(r[8])
	rounds := i64(r[9]) + 1
	legitimacy := i64(r[11])
	support := i64(r[12])
	opposition := i64(r[13])

	attribute := dynastyConflictAttributes[tactic]
	if attribute == "" {
		attribute = "will"
	}
	attributeValue, err := dynastyActionAttribute(conn, userID, attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}

	// Extended-contest pressure: both sides get an equal escalation as rounds
	// pass, so a long fight still trends toward resolution instead of
	// stalling forever, without systematically favoring either side. Before
	// this fix, only the opponent's side scaled with rounds - in both its
	// base gain AND its roll modifier at once - which meant every extra
	// round the player failed to close things out quietly tilted the odds
	// further against them on two axes simultaneously.
	roundsPressure := rounds / 6

	playerBaseGain := int64(10) + bonus + legitimacy/12 + support/20
	opponentBaseGain := int64(10) + opposition/12
	playerModifier := attributeValue + legitimacy/25 + support/40 + bonus/6 + roundsPressure
	opponentModifier := opposition/10 + roundsPressure
	playerTN := int64(16) + opposition/10
	opponentTN := int64(16) + legitimacy/35 + support/50
	switch tactic {
	case "negotiate":
		opponentBaseGain = maxI64(6, opponentBaseGain-4)
		playerTN--
		opponentTN++
	case "expose":
		if legitimacy >= 70 {
			playerBaseGain += 6
			playerModifier += 2
		}
	case "rally":
		playerBaseGain += support / 15
		playerModifier += support / 30
	case "investigate":
		opponentBaseGain = maxI64(5, opponentBaseGain-3)
		opponentTN += 2
	case "duel":
		opponentBaseGain += 3
		playerTN--
		opponentModifier += 2
	}

	playerRoll, err := dynastyRoll2d10(playerModifier, playerTN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	opponentRoll, err := dynastyRoll2d10(opponentModifier, opponentTN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	playerSuccess := boolResult(playerRoll)
	opponentSuccess := boolResult(opponentRoll)
	playerGain := int64(0)
	opponentGain := int64(0)
	if playerSuccess {
		playerGain = playerBaseGain + maxI64(0, resultMargin(playerRoll))/2
	}
	if opponentSuccess {
		opponentGain = opponentBaseGain + maxI64(0, resultMargin(opponentRoll))/2
	}
	// A formal duel is deliberately high-risk: losing the player's check grants
	// the opposition extra momentum even if its own pressure check is weak.
	if tactic == "duel" && !playerSuccess {
		opponentGain += 4
	}
	playerProgress += playerGain
	opponentProgress += opponentGain

	status := "active"
	outcome := ""
	claimStatus := "contested"
	resolution := ""
	if playerProgress >= 100 || opponentProgress >= 100 {
		if playerProgress >= opponentProgress {
			status = "won"
			claimStatus = "won"
			switch fmt.Sprint(r[10]) {
			case "restoration":
				outcome = fmt.Sprintf("%s is restored as a recognized historical dynasty legacy.", fmt.Sprint(r[14]))
				resolution = "dynasty_restored"
			case "revenge":
				outcome = fmt.Sprintf("%s is defeated or exposed; the ancestral revenge claim is satisfied.", fmt.Sprint(r[3]))
				resolution = "ancestral_revenge_satisfied"
			case "replacement_challenge":
				outcome = "The replacement house must concede legacy rights, archives or disputed holdings; this does not create blood continuity."
				resolution = "replacement_legacy_conceded"
			default:
				outcome = "The dynasty claim is recognized after contest."
				resolution = "claim_recognized"
			}
		} else {
			status = "lost"
			claimStatus = "rejected"
			outcome = "The opposing side defeats the claim. The historical record remains intact, but this claim is rejected."
			resolution = "claim_rejected"
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	resolvedMinute := any(nil)
	if status != "active" {
		resolvedMinute = payload.GameMinute
	}
	if _, err = conn.Execute(`
		UPDATE samsara_dynasty_conflicts
		SET status=?,player_progress=?,opponent_progress=?,rounds=?,last_tactic=?,outcome=?,
		    resolved_game_minute=COALESCE(resolved_game_minute,?),updated_at=?
		WHERE conflict_id=? AND user_id=?`,
		[]any{status, playerProgress, opponentProgress, rounds, tactic, outcome, resolvedMinute, now, i64(r[0]), userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if status != "active" {
		if _, err = conn.Execute(`
			UPDATE samsara_dynasty_claims
			SET status=?,resolution=?,resolved_game_minute=COALESCE(resolved_game_minute,?),updated_at=?
			WHERE claim_id=? AND user_id=?`,
			[]any{claimStatus, resolution, payload.GameMinute, now, payload.ClaimID, userID}); err != nil {
			return authoritativeMutation{}, err
		}
	}

	result := map[string]any{
		"claim_id": payload.ClaimID, "conflict_id": i64(r[0]), "history_id": i64(r[1]),
		"conflict_type": fmt.Sprint(r[2]), "opponent": fmt.Sprint(r[3]), "opponent_family": fmt.Sprint(r[4]),
		"stakes": fmt.Sprint(r[5]), "status": status, "tactic": tactic, "rounds": rounds,
		"attribute": attribute, "attribute_value": attributeValue, "player_tn": playerTN, "opponent_tn": opponentTN,
		"player_roll": playerRoll, "opponent_roll": opponentRoll, "player_success": playerSuccess, "opponent_success": opponentSuccess,
		"player_gain": playerGain, "opponent_gain": opponentGain,
		"player_progress": playerProgress, "opponent_progress": opponentProgress,
		"outcome": outcome, "claim_status": claimStatus, "resolution": resolution,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "family", EventType: "family.dynasty.conflict", EntityType: "samsara_dynasty_conflict",
			EntityID: fmt.Sprint(i64(r[0])), SubjectType: "character", SubjectID: fmt.Sprint(userID),
			GameMinute: payload.GameMinute, Payload: result,
		},
	}, nil
}
