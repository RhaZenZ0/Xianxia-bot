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

type secretRealmPayload struct {
	RealmID         string `json:"realm_id"`
	GameMinute      int64  `json:"game_minute"`
	CooldownSeconds int64  `json:"cooldown_seconds"`
}

func normalizeSecretRealm(catalog worlddata.Catalog, raw string) (string, bool) {
	needle := strings.TrimSpace(strings.ToLower(raw))
	for id, realm := range catalog.SecretRealms {
		if strings.ToLower(id) == needle || strings.ToLower(realm.Name) == needle {
			return id, true
		}
	}
	return "", false
}

func secretRealmStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	out := map[string]any{"location": c.Location, "active": false, "available": []map[string]any{}}
	r, err := conn.Execute(`SELECT realm_id,event_key,room_index,danger,active,entered_at,expires_at FROM secret_realm_runs WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if len(r.Rows) > 0 {
		row := r.Rows[0]
		active := storage.ParseInt(row[4]) == 1 && float64Value(row[6]) > now
		if active {
			realmID := fmt.Sprint(row[0])
			realm, ok := catalog.SecretRealms[realmID]
			if !ok {
				return nil, errors.New("active secret realm definition is missing")
			}
			idx := storage.ParseInt(row[2])
			var room any
			if idx >= 0 && idx < int64(len(realm.Rooms)) {
				room = realm.Rooms[idx]
			}
			out["active"] = true
			out["run"] = map[string]any{"realm_id": realmID, "realm": realm, "event_key": fmt.Sprint(row[1]), "room_index": idx, "room": room, "danger": storage.ParseInt(row[3]), "entered_at": float64Value(row[5]), "expires_at": float64Value(row[6])}
			return out, nil
		}
	}
	events, err := conn.Execute(`SELECT event_key,title,payload_json,ends_at,thread_id FROM world_events WHERE active=1 AND ends_at>? AND location=? AND event_type='secret_realm' ORDER BY ends_at`, []any{now, c.Location})
	if err != nil {
		return nil, err
	}
	available := []map[string]any{}
	for _, row := range events.Rows {
		if len(row) < 5 {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(fmt.Sprint(row[2])), &payload) != nil {
			continue
		}
		rid := fmt.Sprint(payload["realm_id"])
		realm, ok := catalog.SecretRealms[rid]
		if !ok {
			continue
		}
		available = append(available, map[string]any{"event_key": fmt.Sprint(row[0]), "title": fmt.Sprint(row[1]), "realm_id": rid, "realm": realm, "ends_at": float64Value(row[3]), "thread_id": row[4]})
	}
	out["available"] = available
	// The rotation (v1.0.0-rc.2): which realm opened last and which is
	// next, so a player can be standing at the ruin when it does.
	gm, gmErr := canonicalWorldGameMinute(conn)
	if gmErr != nil {
		gm = 0
	}
	if rotation, rotErr := SecretRealmRotationView(conn, catalog, gm); rotErr == nil {
		out["rotation"] = rotation
	}
	return out, nil
}

func secretRealmEnterAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p secretRealmPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	rid, ok := normalizeSecretRealm(catalog, p.RealmID)
	if !ok {
		return authoritativeMutation{}, errors.New("unknown secret realm")
	}
	realm := catalog.SecretRealms[rid]
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living incarnation can enter a secret realm")
	}
	if c.Location != realm.Location {
		return authoritativeMutation{}, fmt.Errorf("secret realm entrance is in %s", realm.Location)
	}
	if c.RealmIndex < realm.MinRealmIndex {
		return authoritativeMutation{}, errors.New("realm pressure rejects this cultivation level")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	er, err := conn.Execute(`SELECT event_key,ends_at,thread_id FROM world_events WHERE active=1 AND ends_at>? AND location=? AND event_type='secret_realm' ORDER BY ends_at`, []any{now, c.Location})
	if err != nil {
		return authoritativeMutation{}, err
	}
	eventKey := ""
	expires := float64(0)
	var thread any
	for _, row := range er.Rows {
		if len(row) < 3 {
			continue
		}
		pr, err := conn.Execute(`SELECT payload_json FROM world_events WHERE event_key=?`, []any{fmt.Sprint(row[0])})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if len(pr.Rows) == 0 {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(fmt.Sprint(pr.Rows[0][0])), &payload) != nil {
			continue
		}
		if fmt.Sprint(payload["realm_id"]) == rid {
			eventKey = fmt.Sprint(row[0])
			expires = float64Value(row[1])
			thread = row[2]
			break
		}
	}
	if eventKey == "" {
		return authoritativeMutation{}, errors.New("that secret realm is not currently open")
	}
	existing, err := conn.Execute(`SELECT active,expires_at FROM secret_realm_runs WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(existing.Rows) > 0 && storage.ParseInt(existing.Rows[0][0]) == 1 && float64Value(existing.Rows[0][1]) > now {
		return authoritativeMutation{}, errors.New("already inside an active secret realm")
	}
	if _, err = conn.Execute(`INSERT INTO secret_realm_runs(user_id,realm_id,event_key,room_index,danger,active,entered_at,expires_at) VALUES(?,?,?,0,0,1,?,?) ON CONFLICT(user_id) DO UPDATE SET realm_id=excluded.realm_id,event_key=excluded.event_key,room_index=0,danger=0,active=1,entered_at=excluded.entered_at,expires_at=excluded.expires_at`, []any{userID, rid, eventKey, now, expires}); err != nil {
		return authoritativeMutation{}, err
	}
	first := any(nil)
	if len(realm.Rooms) > 0 {
		first = realm.Rooms[0]
	}
	result := map[string]any{"entered": true, "realm_id": rid, "realm": realm, "first_room": first, "event_key": eventKey, "expires_at": expires, "thread_id": thread}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "secret_realm", EventType: "secret_realm_entered", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func grantInheritanceTx(conn *storage.Conn, userID int64, realmID string, inheritance worlddata.Inheritance, inheritanceID string, now float64) (map[string]any, error) {
	existing, err := conn.Execute(`SELECT 1 FROM inheritances WHERE user_id=? AND inheritance_id=?`, []any{userID, inheritanceID})
	if err != nil {
		return nil, err
	}
	if len(existing.Rows) > 0 {
		return map[string]any{"gained": false, "inheritance_id": inheritanceID, "name": inheritance.Name, "description": inheritance.Description, "bonuses": inheritance.Bonuses, "item": inheritance.Item}, nil
	}
	if _, err = conn.Execute(`INSERT INTO inheritances(user_id,inheritance_id,source_realm_id,acquired_at) VALUES(?,?,?,?)`, []any{userID, inheritanceID, realmID, now}); err != nil {
		return nil, err
	}
	qi := inheritance.Bonuses["qi_max"]
	vit := inheritance.Bonuses["vitality_max"]
	insight := inheritance.Bonuses["insight_xp"]
	if _, err = conn.Execute(`UPDATE characters SET qi_max=qi_max+?,qi=qi+?,vitality_max=vitality_max+?,vitality=vitality+?,insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`, []any{qi, qi, vit, vit, insight, now, userID}); err != nil {
		return nil, err
	}
	if inheritance.Item != "" {
		if _, err = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+1`, []any{userID, inheritance.Item}); err != nil {
			return nil, err
		}
	}
	payload, _ := json.Marshal(map[string]any{"inheritance_id": inheritanceID, "source_realm_id": realmID, "bonuses": inheritance.Bonuses, "item": inheritance.Item})
	if _, err = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, "inheritance_obtained", string(payload), now}); err != nil {
		return nil, err
	}
	return map[string]any{"gained": true, "inheritance_id": inheritanceID, "name": inheritance.Name, "description": inheritance.Description, "bonuses": inheritance.Bonuses, "item": inheritance.Item}, nil
}

func secretRealmExploreAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p secretRealmPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living incarnation can explore a secret realm")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	rr, err := conn.Execute(`SELECT realm_id,event_key,room_index,danger,active,expires_at FROM secret_realm_runs WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(rr.Rows) == 0 || storage.ParseInt(rr.Rows[0][4]) != 1 {
		return authoritativeMutation{}, errors.New("not inside an active secret realm")
	}
	row := rr.Rows[0]
	if float64Value(row[5]) <= now {
		if _, e := conn.Execute(`UPDATE secret_realm_runs SET active=0 WHERE user_id=?`, []any{userID}); e != nil {
			return authoritativeMutation{}, e
		}
		return authoritativeMutation{}, errors.New("the secret realm entrance has already sealed")
	}
	remaining, err := cooldownRemaining(conn, userID, "secret_realm", now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	rid := fmt.Sprint(row[0])
	realm, ok := catalog.SecretRealms[rid]
	if !ok {
		return authoritativeMutation{}, errors.New("secret realm definition is missing")
	}
	idx := storage.ParseInt(row[2])
	danger := storage.ParseInt(row[3])
	if idx < 0 || idx >= int64(len(realm.Rooms)) {
		return authoritativeMutation{}, errors.New("secret realm run has already reached its end")
	}
	room := realm.Rooms[idx]
	attr, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, room.Attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	modifier := attr + 2
	if stringInList(room.PreferredPaths, c.Path) {
		modifier += 2
	}
	if stringInList(room.PreferredRoots, c.SpiritualRoot) {
		modifier += 1
	}
	roll, err := rollCheck(modifier, room.TN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, "secret_realm", p.CooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"realm_id": rid, "realm_name": realm.Name, "room_index": idx, "room": room, "roll": roll, "success": roll["success"], "danger_before": danger}
	if roll["success"].(bool) {
		awarded, err := applyCanonicalRewardTx(conn, catalog, userID, c, canonicalReward{Cultivation: room.Cultivation, SpiritStones: room.SpiritStones, InsightXP: room.InsightXP, Items: room.Items}, "secret_realm_room", now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		newDanger := danger - 1
		if newDanger < 0 {
			newDanger = 0
		}
		final := idx == int64(len(realm.Rooms)-1)
		result["cultivation_awarded"] = awarded
		result["spirit_stones"] = room.SpiritStones
		result["insight_xp"] = room.InsightXP
		result["items"] = room.Items
		result["danger"] = newDanger
		result["final_room"] = final
		if final {
			inheritance, ok := catalog.Inheritances[realm.InheritanceID]
			if !ok {
				return authoritativeMutation{}, errors.New("secret realm inheritance definition is missing")
			}
			inheritanceResult, err := grantInheritanceTx(conn, userID, rid, inheritance, realm.InheritanceID, now)
			if err != nil {
				return authoritativeMutation{}, err
			}
			if _, err = conn.Execute(`UPDATE secret_realm_runs SET room_index=room_index+1,danger=?,active=0 WHERE user_id=?`, []any{newDanger, userID}); err != nil {
				return authoritativeMutation{}, err
			}
			result["inheritance"] = inheritanceResult
			result["active"] = false
		} else {
			if _, err = conn.Execute(`UPDATE secret_realm_runs SET room_index=room_index+1,danger=? WHERE user_id=? AND active=1`, []any{newDanger, userID}); err != nil {
				return authoritativeMutation{}, err
			}
			result["next_room"] = realm.Rooms[idx+1]
			result["active"] = true
		}
	} else {
		newDanger := danger + 1
		result["danger"] = newDanger
		if newDanger >= 3 {
			if _, err = conn.Execute(`UPDATE secret_realm_runs SET danger=?,active=0 WHERE user_id=?`, []any{newDanger, userID}); err != nil {
				return authoritativeMutation{}, err
			}
			result["ejected"] = true
			result["active"] = false
		} else {
			if _, err = conn.Execute(`UPDATE secret_realm_runs SET danger=? WHERE user_id=? AND active=1`, []any{newDanger, userID}); err != nil {
				return authoritativeMutation{}, err
			}
			result["ejected"] = false
			result["active"] = true
		}
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "secret_realm", EventType: "secret_realm_room_resolved", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func secretRealmLeaveAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p secretRealmPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	r, err := conn.Execute(`SELECT realm_id,active FROM secret_realm_runs WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	left := false
	realmID := ""
	if len(r.Rows) > 0 {
		realmID = fmt.Sprint(r.Rows[0][0])
		left = storage.ParseInt(r.Rows[0][1]) == 1
	}
	if _, err = conn.Execute(`UPDATE secret_realm_runs SET active=0 WHERE user_id=?`, []any{userID}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"left": left, "realm_id": realmID}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "secret_realm", EventType: "secret_realm_left", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
