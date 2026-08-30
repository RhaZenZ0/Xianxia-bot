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

func birthFamilyHouseholdLocation(familyID int64) string {
	return fmt.Sprintf("birth_family:%d", familyID)
}

func familyHouseholdEnterAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p struct {
		GameMinute int64 `json:"game_minute"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(`SELECT c.name,c.life_status,c.location,cbf.family_id,f.family_name,f.location FROM characters c JOIN character_birth_family cbf ON cbf.user_id=c.user_id JOIN birth_families f ON f.family_id=cbf.family_id WHERE c.user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(res.Rows) == 0 {
		return authoritativeMutation{}, errors.New("character has no birth household")
	}
	r := res.Rows[0]
	if fmt.Sprint(r[1]) != "alive" {
		return authoritativeMutation{}, errors.New("only a living character can enter the birth household")
	}
	familyID := storage.ParseInt(r[3])
	familyName := strings.TrimSpace(fmt.Sprint(r[4]))
	baseLocation := strings.TrimSpace(fmt.Sprint(r[5]))
	if familyID <= 0 || familyName == "" {
		return authoritativeMutation{}, errors.New("birth household is invalid")
	}
	locationKey := birthFamilyHouseholdLocation(familyID)
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{locationKey, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	metadata, _ := json.Marshal(map[string]any{"family_id": familyID, "family_name": familyName, "base_location": baseLocation})
	if _, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type=excluded.scene_type,scene_key=excluded.scene_key,scene_label=excluded.scene_label,channel_id=NULL,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at`, []any{userID, baseLocation, "birth_family_household", locationKey, familyName + " Household", nil, string(metadata), now}); err != nil {
		return authoritativeMutation{}, err
	}
	occupants, err := conn.Execute(`SELECT c.user_id,c.name FROM characters c JOIN character_birth_family cbf ON cbf.user_id=c.user_id WHERE cbf.family_id=? AND c.location=? AND c.life_status='alive' ORDER BY c.name,c.user_id`, []any{familyID, locationKey})
	if err != nil {
		return authoritativeMutation{}, err
	}
	players := make([]map[string]any, 0, len(occupants.Rows))
	for _, row := range occupants.Rows {
		players = append(players, map[string]any{"user_id": storage.ParseInt(row[0]), "name": fmt.Sprint(row[1])})
	}
	result := map[string]any{"entered": true, "family_id": familyID, "family_name": familyName, "location": locationKey, "base_location": baseLocation, "players_present": players}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "family", EventType: "birth_household_entered", EntityType: "birth_family", EntityID: fmt.Sprint(familyID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func familyHouseholdLeaveAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p struct {
		GameMinute int64 `json:"game_minute"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(`SELECT c.location,cbf.family_id,f.family_name,f.location FROM characters c JOIN character_birth_family cbf ON cbf.user_id=c.user_id JOIN birth_families f ON f.family_id=cbf.family_id WHERE c.user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(res.Rows) == 0 {
		return authoritativeMutation{}, errors.New("character has no birth household")
	}
	r := res.Rows[0]
	familyID := storage.ParseInt(r[1])
	familyName := strings.TrimSpace(fmt.Sprint(r[2]))
	baseLocation := strings.TrimSpace(fmt.Sprint(r[3]))
	locationKey := birthFamilyHouseholdLocation(familyID)
	if fmt.Sprint(r[0]) != locationKey {
		return authoritativeMutation{}, errors.New("character is not inside their birth household")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{baseLocation, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type='world',scene_key='',scene_label=excluded.scene_label,channel_id=NULL,metadata_json='{}',updated_at=excluded.updated_at`, []any{userID, baseLocation, "world", "", baseLocation, nil, "{}", now}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"left": true, "family_id": familyID, "family_name": familyName, "location": baseLocation, "household_location": locationKey}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "family", EventType: "birth_household_left", EntityType: "birth_family", EntityID: fmt.Sprint(familyID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
