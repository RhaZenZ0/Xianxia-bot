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

const maxDynastyInvestigationLevel int64 = 3

type lineageInvestigatePayload struct {
	HistoryID  int64 `json:"history_id"`
	GameMinute int64 `json:"game_minute"`
}

func samsaraDynastyEventKind(status string) string {
	switch strings.TrimSpace(status) {
	case "distant_surviving_branch":
		return "surviving_branch"
	case "fallen_severed_branch":
		return "fallen_branch"
	case "extinct_branch_replaced":
		return "extinction_and_replacement"
	case "no_known_connection", "unrelated_rebirth":
		return "unrelated_rebirth"
	case "new_mortal_incarnation":
		return "rebirth_into_established_house"
	default:
		return "uncertain_lineage"
	}
}

func samsaraDynastyBloodContinuity(status string) bool {
	switch strings.TrimSpace(status) {
	case "distant_surviving_branch", "fallen_severed_branch":
		return true
	default:
		return false
	}
}

func samsaraDynastyEvidence(status, sourceFamily, destinationFamily, sourceWorld, destinationWorld string) []string {
	sourceFamily = firstNonempty(strings.TrimSpace(sourceFamily), "the former household")
	destinationFamily = firstNonempty(strings.TrimSpace(destinationFamily), "the current household")
	switch strings.TrimSpace(status) {
	case "distant_surviving_branch":
		return []string{
			fmt.Sprintf("A surviving branch register links %s in the %s to an ancestor line later recorded in the %s.", sourceFamily, sourceWorld, destinationWorld),
			fmt.Sprintf("Ancestral seals associated with %s recur in early records of %s, though titles and property do not transfer.", sourceFamily, destinationFamily),
			fmt.Sprintf("Independent blood-resonance and genealogy records corroborate a distant ancestral connection to %s.", destinationFamily),
		}
	case "fallen_severed_branch":
		return []string{
			fmt.Sprintf("A ruined branch register shows descendants of %s reaching the %s before the old house-name disappeared.", sourceFamily, destinationWorld),
			fmt.Sprintf("Discarded ancestral tablets and debt/property records document the branch's fall before %s emerged under its new identity.", destinationFamily),
			fmt.Sprintf("Weak but consistent blood-resonance evidence confirms that %s descends from the severed branch without inheriting its former standing.", destinationFamily),
		}
	case "extinct_branch_replaced":
		return []string{
			fmt.Sprintf("An extinction register records the last known members of the %s-associated branch dying without a surviving heir in the %s.", sourceFamily, destinationWorld),
			fmt.Sprintf("Later estate, workshop, military-post or charter records show %s taking over the vacant local role after the extinction.", destinationFamily),
			fmt.Sprintf("Genealogy and blood-resonance records do not match: %s replaced the extinct house and has no blood continuity with it.", destinationFamily),
		}
	case "new_mortal_incarnation":
		return []string{
			fmt.Sprintf("The Samsara record places the soul into the already-established %s rather than carrying the former household forward.", destinationFamily),
			fmt.Sprintf("The %s household registry predates this incarnation and has its own independent ancestry.", destinationWorld),
			"There is no automatic blood claim on the previous incarnation's family, titles, property or resources.",
		}
	case "no_known_connection", "unrelated_rebirth":
		return []string{
			fmt.Sprintf("Household registers for %s and %s trace separate local genealogies.", sourceFamily, destinationFamily),
			fmt.Sprintf("No reliable ancestral seal, inheritance charter or oath-chain bridges the %s and the %s.", sourceWorld, destinationWorld),
			fmt.Sprintf("Available blood-resonance records provide no evidence that %s descends from %s.", destinationFamily, sourceFamily),
		}
	default:
		return []string{
			"Existing records conflict and do not establish a reliable genealogy.",
			"Independent household archives must be compared before any ancestral claim is accepted.",
			"No blood-continuity conclusion can be considered confirmed from the available evidence.",
		}
	}
}

func dynastyInvestigationState(level int64) string {
	switch {
	case level <= 0:
		return "uninvestigated"
	case level == 1:
		return "clue_found"
	case level == 2:
		return "corroborated"
	default:
		return "confirmed"
	}
}

func recordSamsaraDynastyHistory(
	conn *storage.Conn,
	userID, incarnationNumber, sourceFamilyID, destinationFamilyID, gameMinute int64,
	sourceFamilyName, sourceFamilyArchetype, sourceWorld string,
	destinationFamilyName, destinationFamilyArchetype, destinationWorld,
	lineageStatus, summary string,
	now float64,
) (int64, error) {
	if userID <= 0 {
		return 0, errors.New("dynasty history requires a positive user_id")
	}
	if incarnationNumber < 2 {
		return 0, errors.New("dynasty history requires a reincarnated incarnation number")
	}
	if strings.TrimSpace(sourceFamilyName) == "" || strings.TrimSpace(destinationFamilyName) == "" {
		return 0, errors.New("dynasty history requires source and destination families")
	}
	if now <= 0 {
		now = float64(time.Now().UnixNano()) / 1e9
	}
	status := strings.TrimSpace(lineageStatus)
	if status == "" {
		status = "uncertain_lineage"
	}
	eventKind := samsaraDynastyEventKind(status)
	bloodContinuity := int64(0)
	if samsaraDynastyBloodContinuity(status) {
		bloodContinuity = 1
	}
	evidenceJSON, err := json.Marshal(samsaraDynastyEvidence(
		status,
		sourceFamilyName,
		destinationFamilyName,
		firstNonempty(sourceWorld, "Unknown World"),
		firstNonempty(destinationWorld, "Unknown World"),
	))
	if err != nil {
		return 0, err
	}

	res, err := conn.Execute(`
		INSERT INTO samsara_dynasty_history(
			user_id,incarnation_number,source_family_id,source_family_name,source_family_archetype,source_world,
			destination_family_id,destination_family_name,destination_family_archetype,destination_world,
			lineage_status,blood_continuity,event_kind,summary,evidence_json,investigation_level,investigation_count,
			created_game_minute,created_at,updated_at
		) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(user_id,incarnation_number) DO UPDATE SET
			source_family_id=excluded.source_family_id,
			source_family_name=excluded.source_family_name,
			source_family_archetype=excluded.source_family_archetype,
			source_world=excluded.source_world,
			destination_family_id=excluded.destination_family_id,
			destination_family_name=excluded.destination_family_name,
			destination_family_archetype=excluded.destination_family_archetype,
			destination_world=excluded.destination_world,
			lineage_status=excluded.lineage_status,
			blood_continuity=excluded.blood_continuity,
			event_kind=excluded.event_kind,
			summary=excluded.summary,
			evidence_json=excluded.evidence_json,
			updated_at=excluded.updated_at
	`, []any{
		userID, incarnationNumber, nullablePositiveID(sourceFamilyID), sourceFamilyName, sourceFamilyArchetype, sourceWorld,
		nullablePositiveID(destinationFamilyID), destinationFamilyName, destinationFamilyArchetype, destinationWorld,
		status, bloodContinuity, eventKind, firstNonempty(strings.TrimSpace(summary), "No reliable dynasty summary was recorded."),
		string(evidenceJSON), 0, 0, gameMinute, now, now,
	})
	if err != nil {
		return 0, err
	}
	if res.LastInsertID > 0 {
		return res.LastInsertID, nil
	}
	row, err := conn.Execute(
		`SELECT history_id FROM samsara_dynasty_history WHERE user_id=? AND incarnation_number=?`,
		[]any{userID, incarnationNumber},
	)
	if err != nil || len(row.Rows) == 0 {
		return 0, firstNonNilError(err, errors.New("dynasty history record could not be resolved"))
	}
	return i64(row.Rows[0][0]), nil
}

func nullablePositiveID(value int64) any {
	if value <= 0 {
		return nil
	}
	return value
}

func firstNonNilError(errs ...error) error {
	for _, err := range errs {
		if err != nil {
			return err
		}
	}
	return nil
}

func lineageInvestigateAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var payload lineageInvestigatePayload
	if len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return authoritativeMutation{}, err
		}
	}

	character, err := conn.Execute(`SELECT life_status FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(character.Rows) == 0 {
		return authoritativeMutation{}, errors.New("character not found")
	}
	if fmt.Sprint(character.Rows[0][0]) != "alive" {
		return authoritativeMutation{}, errors.New("dynasty archives can only be investigated during a living incarnation")
	}

	query := `
		SELECT history_id,incarnation_number,source_family_name,source_world,destination_family_name,destination_world,
		       lineage_status,blood_continuity,event_kind,summary,evidence_json,investigation_level,investigation_count
		FROM samsara_dynasty_history
		WHERE user_id=?`
	args := []any{userID}
	if payload.HistoryID > 0 {
		query += ` AND history_id=?`
		args = append(args, payload.HistoryID)
	}
	query += ` ORDER BY incarnation_number DESC,history_id DESC LIMIT 1`

	row, err := conn.Execute(query, args)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(row.Rows) == 0 {
		return authoritativeMutation{}, errors.New("no Samsara dynasty history is available to investigate")
	}
	r := row.Rows[0]
	historyID := i64(r[0])
	level := i64(r[11])
	if level < maxDynastyInvestigationLevel {
		level++
	}
	count := i64(r[12]) + 1
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`
		UPDATE samsara_dynasty_history
		SET investigation_level=?,investigation_count=?,
		    first_discovered_game_minute=COALESCE(first_discovered_game_minute,?),
		    last_investigated_game_minute=?,updated_at=?
		WHERE history_id=? AND user_id=?`,
		[]any{level, count, payload.GameMinute, payload.GameMinute, now, historyID, userID},
	); err != nil {
		return authoritativeMutation{}, err
	}

	evidence := []string{}
	_ = json.Unmarshal([]byte(fmt.Sprint(r[10])), &evidence)
	revealCount := int(level)
	if revealCount > len(evidence) {
		revealCount = len(evidence)
	}
	if revealCount < 0 {
		revealCount = 0
	}
	revealed := append([]string(nil), evidence[:revealCount]...)
	continuity := "not_yet_confirmed"
	if level >= maxDynastyInvestigationLevel {
		if i64(r[7]) != 0 {
			continuity = "confirmed_ancestral_continuity"
		} else {
			continuity = "confirmed_no_blood_continuity"
		}
	}
	if err := ensureDynastyLegacyContent(conn, userID, historyID, payload.GameMinute); err != nil {
		return authoritativeMutation{}, err
	}
	leads, quests, err := unlockDynastyLegacyContent(conn, userID, historyID, level, payload.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"history_id":           historyID,
		"incarnation_number":   i64(r[1]),
		"source_family":        fmt.Sprint(r[2]),
		"source_world":         fmt.Sprint(r[3]),
		"destination_family":   fmt.Sprint(r[4]),
		"destination_world":    fmt.Sprint(r[5]),
		"lineage_status":       fmt.Sprint(r[6]),
		"event_kind":           fmt.Sprint(r[8]),
		"summary":              fmt.Sprint(r[9]),
		"investigation_level":  level,
		"investigation_state":  dynastyInvestigationState(level),
		"investigation_count":  count,
		"evidence":             revealed,
		"blood_continuity":     continuity,
		"fully_investigated":   level >= maxDynastyInvestigationLevel,
		"ancestral_leads":      leads,
		"investigation_quests": quests,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:      "family",
			EventType:   "family.lineage.investigate",
			EntityType:  "samsara_dynasty_history",
			EntityID:    fmt.Sprint(historyID),
			SubjectType: "character",
			SubjectID:   fmt.Sprint(userID),
			GameMinute:  payload.GameMinute,
			Payload:     result,
		},
	}, nil
}
