package game

import (
	"encoding/json"
	"errors"
	"fmt"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
)

// Moving in and out of a sect abode (v0.23.0, the v0.21 Authority I backlog).
//
// The cave-abode equivalents (abode.enter / abode.leave, v0.21.3) have been in
// the engine for two releases; the sect abode kept writing characters.location
// from Discord because it lives in its own table and nobody had moved it. The
// shape here deliberately mirrors abodeMoveActionGo: the adjacency rule - you
// must be standing at the sect gate to go in, and inside to come out - is the
// whole point of the action, and it is not a rule Discord should be enforcing
// on its own read of the character row.

type sectAbodeMovePayload struct {
	GameMinute int64 `json:"game_minute"`
}

func sectAbodeMoveAction(conn *storage.Conn, userID int64, raw json.RawMessage, mode string) (authoritativeMutation, error) {
	var p sectAbodeMovePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(
		`SELECT location FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	character := firstRowMap(res)
	if character == nil {
		return authoritativeMutation{}, errors.New("living character not found")
	}
	current := fmt.Sprint(character["location"])

	res, err = conn.Execute(
		`SELECT user_id,sect_name,name,location_key,base_location FROM sect_abodes WHERE user_id=?`,
		[]any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	abode := firstRowMap(res)
	if abode == nil {
		return authoritativeMutation{}, errors.New("no sect abode is assigned")
	}
	locationKey := fmt.Sprint(abode["location_key"])
	baseLocation := fmt.Sprint(abode["base_location"])

	var destination string
	switch mode {
	case "enter":
		if current == locationKey {
			return authoritativeMutation{}, errors.New("you are already inside your sect abode")
		}
		if current != baseLocation {
			return authoritativeMutation{}, fmt.Errorf("travel to %s first", baseLocation)
		}
		destination = locationKey
	case "leave":
		if current != locationKey {
			return authoritativeMutation{}, errors.New("you are not inside your sect abode")
		}
		destination = baseLocation
	default:
		return authoritativeMutation{}, errors.New("unknown sect abode movement")
	}

	if _, err = conn.Execute(
		`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`,
		[]any{destination, nowSeconds(), userID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"abode":         abode,
		"location":      destination,
		"base_location": baseLocation,
		"location_key":  locationKey,
		"mode":          mode,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:     "sect",
			EventType:  "sect_abode." + mode,
			EntityType: "character",
			EntityID:   fmt.Sprint(userID),
			GameMinute: p.GameMinute,
			Payload:    result,
		},
	}, nil
}
