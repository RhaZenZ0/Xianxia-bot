package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func birthFamilyHouseholdLocation(familyID int64) string {
	return fmt.Sprintf("birth_family:%d", familyID)
}

// enterHouseholdTx puts a living character inside their birth household.
// `returnLocation` is where a Hearth-Return Talisman found them: remembered
// on the scene as the mark a Waymark Talisman returns to. Walking in from
// the town stores nothing, and leaving on foot is always the street.
func enterHouseholdTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, returnLocation string) (map[string]any, error) {
	res, err := conn.Execute(`SELECT c.name,c.life_status,c.location,cbf.family_id,f.family_name,f.location FROM characters c JOIN character_birth_family cbf ON cbf.user_id=c.user_id JOIN birth_families f ON f.family_id=cbf.family_id WHERE c.user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return nil, errors.New("character has no birth household")
	}
	r := res.Rows[0]
	if fmt.Sprint(r[1]) != "alive" {
		return nil, errors.New("only a living character can enter the birth household")
	}
	familyID := storage.ParseInt(r[3])
	familyName := strings.TrimSpace(fmt.Sprint(r[4]))
	baseLocation := strings.TrimSpace(fmt.Sprint(r[5]))
	if familyID <= 0 || familyName == "" {
		return nil, errors.New("birth household is invalid")
	}
	locationKey := birthFamilyHouseholdLocation(familyID)
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = moveCharacterTx(conn, catalog, userID, locationKey, now); err != nil {
		return nil, err
	}
	meta := map[string]any{"family_id": familyID, "family_name": familyName, "base_location": baseLocation}
	if returnLocation = strings.TrimSpace(returnLocation); returnLocation != "" && returnLocation != locationKey {
		meta["return_location"] = returnLocation
	}
	metadata, _ := json.Marshal(meta)
	if _, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type=excluded.scene_type,scene_key=excluded.scene_key,scene_label=excluded.scene_label,channel_id=NULL,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at`, []any{userID, baseLocation, "birth_family_household", locationKey, familyName + " Household", nil, string(metadata), now}); err != nil {
		return nil, err
	}
	occupants, err := conn.Execute(`SELECT c.user_id,c.name FROM characters c JOIN character_birth_family cbf ON cbf.user_id=c.user_id WHERE cbf.family_id=? AND c.location=? AND c.life_status='alive' ORDER BY c.name,c.user_id`, []any{familyID, locationKey})
	if err != nil {
		return nil, err
	}
	players := make([]map[string]any, 0, len(occupants.Rows))
	for _, row := range occupants.Rows {
		players = append(players, map[string]any{"user_id": storage.ParseInt(row[0]), "name": fmt.Sprint(row[1])})
	}
	result := map[string]any{"entered": true, "family_id": familyID, "family_name": familyName, "location": locationKey, "base_location": baseLocation, "players_present": players}
	if meta["return_location"] != nil {
		result["return_location"] = meta["return_location"]
	}
	return result, nil
}

// familyHouseholdEnterAction is the door: it opens for a character standing
// in the family's own town (v1.0.0-rc.32 - it used to be a teleport from
// anywhere), or already inside. From anywhere else the way home is the road,
// or a Hearth-Return Talisman.
func familyHouseholdEnterAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
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
	here := strings.TrimSpace(fmt.Sprint(r[0]))
	familyID := storage.ParseInt(r[1])
	town := strings.TrimSpace(fmt.Sprint(r[3]))
	if here != town && here != birthFamilyHouseholdLocation(familyID) {
		return authoritativeMutation{}, fmt.Errorf("the %s household stands in %s and you are in %s — travel there first, or use a Hearth-Return Talisman", strings.TrimSpace(fmt.Sprint(r[2])), town, here)
	}
	entered, err := enterHouseholdTx(conn, catalog, userID, "")
	if err != nil {
		return authoritativeMutation{}, err
	}
	// Spelled out rather than passed through: the result-key contract reads
	// what an action returns off the action itself.
	result := map[string]any{"entered": true, "family_id": entered["family_id"], "family_name": entered["family_name"], "location": entered["location"], "base_location": entered["base_location"], "players_present": entered["players_present"]}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "family", EventType: "birth_household_entered", EntityType: "birth_family", EntityID: fmt.Sprint(result["family_id"]), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func familyHouseholdLeaveAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
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
	if _, err = moveCharacterTx(conn, catalog, userID, baseLocation, now); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type='world',scene_key='',scene_label=excluded.scene_label,channel_id=NULL,metadata_json='{}',updated_at=excluded.updated_at`, []any{userID, baseLocation, "world", "", baseLocation, nil, "{}", now}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"left": true, "family_id": familyID, "family_name": familyName, "location": baseLocation, "household_location": locationKey}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "family", EventType: "birth_household_left", EntityType: "birth_family", EntityID: fmt.Sprint(familyID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// returnToWaymarkTx is the Waymark Talisman's half of the round trip: from
// inside the household, back to wherever the Hearth-Return Talisman found
// you. Somebody who walked in has no mark, and the road back is the door.
func returnToWaymarkTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT c.location,cbf.family_id,f.family_name,f.location,COALESCE(s.metadata_json,'{}') FROM characters c JOIN character_birth_family cbf ON cbf.user_id=c.user_id JOIN birth_families f ON f.family_id=cbf.family_id LEFT JOIN player_scene_state s ON s.user_id=c.user_id WHERE c.user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return nil, errors.New("character has no birth household")
	}
	r := res.Rows[0]
	familyID := storage.ParseInt(r[1])
	locationKey := birthFamilyHouseholdLocation(familyID)
	if fmt.Sprint(r[0]) != locationKey {
		return nil, errors.New("a Waymark Talisman is read inside your birth household, where the mark was left")
	}
	var meta struct {
		ReturnLocation string `json:"return_location"`
	}
	_ = json.Unmarshal([]byte(fmt.Sprint(r[4])), &meta)
	mark := strings.TrimSpace(meta.ReturnLocation)
	if mark == "" || mark == locationKey || mark == strings.TrimSpace(fmt.Sprint(r[3])) {
		return nil, errors.New("there is no mark to return to: you walked here, and the road back is the door (/family → Leave)")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = moveCharacterTx(conn, catalog, userID, mark, now); err != nil {
		return nil, err
	}
	if _, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type='world',scene_key='',scene_label=excluded.scene_label,channel_id=NULL,metadata_json='{}',updated_at=excluded.updated_at`, []any{userID, mark, "world", "", mark, nil, "{}", now}); err != nil {
		return nil, err
	}
	return map[string]any{"returned": true, "family_id": familyID, "family_name": strings.TrimSpace(fmt.Sprint(r[2])), "location": mark, "household_location": locationKey}, nil
}
