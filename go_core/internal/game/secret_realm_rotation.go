package game

import (
	"encoding/json"
	"fmt"
	"sort"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Secret realms on rotation (v0.39.0). Before this a realm opened only when
// a player exploring its very entrance rolled the event for it; a realm at
// a ruin nobody walked to never opened at all. The world tick now opens the
// realms in turn - one every secretRealmRotationMinutes of game time, in
// catalogue order, at its own entrance, for its own open hours - so every
// world always has one coming. The rumours carry it: the opening is a
// public world_history row at the entrance.

const secretRealmRotationMinutes int64 = 3 * 24 * 60

const secretRealmRotationKey = "secret_realm_rotation"

type secretRealmRotationState struct {
	Index          int    `json:"index"`
	LastGameMinute int64  `json:"last_game_minute"`
	LastRealmID    string `json:"last_realm_id"`
}

func readSecretRealmRotationTx(conn *storage.Conn) (secretRealmRotationState, error) {
	var state secretRealmRotationState
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{secretRealmRotationKey})
	if err != nil {
		return state, err
	}
	if len(res.Rows) > 0 && len(res.Rows[0]) > 0 {
		_ = json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &state)
	}
	return state, nil
}

func writeSecretRealmRotationTx(conn *storage.Conn, state secretRealmRotationState, now float64) error {
	enc, _ := json.Marshal(state)
	_, err := conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, []any{secretRealmRotationKey, string(enc), now})
	return err
}

func secretRealmIDs(catalog worlddata.Catalog) []string {
	ids := make([]string, 0, len(catalog.SecretRealms))
	for id := range catalog.SecretRealms {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// RotateSecretRealms opens the next realm in the rotation when the interval
// has passed. It returns how many realms it opened this call (0 or 1). A
// realm already open skips its turn rather than extending; the rotation
// still advances so the next tick tries the next one. The caller owns the
// transaction.
func RotateSecretRealms(conn *storage.Conn, catalog worlddata.Catalog, gm int64) (int64, error) {
	ids := secretRealmIDs(catalog)
	if len(ids) == 0 || !tableExistsTx(conn, "world_events") {
		return 0, nil
	}
	state, err := readSecretRealmRotationTx(conn)
	if err != nil {
		return 0, err
	}
	if state.LastGameMinute > 0 && gm-state.LastGameMinute < secretRealmRotationMinutes {
		return 0, nil
	}
	now := nowSeconds()
	if _, err := conn.Execute(`UPDATE world_events SET active=0 WHERE active=1 AND ends_at<=?`, []any{now}); err != nil {
		return 0, err
	}
	idx := state.Index % len(ids)
	id := ids[idx]
	realm := catalog.SecretRealms[id]
	state.Index = idx + 1
	state.LastGameMinute = gm
	state.LastRealmID = id
	dedupe := "secret_realm:" + id
	existing, err := conn.Execute(`SELECT 1 FROM world_events WHERE dedupe_key=? AND active=1 AND ends_at>? LIMIT 1`, []any{dedupe, now})
	if err != nil {
		return 0, err
	}
	if len(existing.Rows) > 0 {
		return 0, writeSecretRealmRotationTx(conn, state, now)
	}
	ends := now + float64(maxI64(1, realm.OpenHours))*3600
	eventKey := fmt.Sprintf("secret_realm_rotation:%s:%d", id, gm)
	payload, _ := json.Marshal(map[string]any{"definition_id": "rotation", "category": "Rotation", "realm_id": id})
	if _, err := conn.Execute(`INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(event_key) DO UPDATE SET active=1,dedupe_key=excluded.dedupe_key,payload_json=excluded.payload_json,ends_at=excluded.ends_at`, []any{eventKey, dedupe, "secret_realm", realm.Name, realm.Location, string(payload), now, ends}); err != nil {
		return 0, err
	}
	summary := fmt.Sprintf("%s has opened at %s. The entrance holds for about %d hours.", realm.Name, realm.Location, maxI64(1, realm.OpenHours))
	if err := recordWorldHistoryTx(conn, "world_event:"+eventKey+":history", "secret_realm_opened", realm.Name+" opens", summary, 60, "public", realm.Location, "", "world", id, "World phenomenon", "location", realm.Location, realm.Location, nil, "", []string{"secret realm", "rotation", id}, gm, map[string]any{"realm_id": id, "open_hours": realm.OpenHours}, now); err != nil {
		return 0, err
	}
	return 1, writeSecretRealmRotationTx(conn, state, now)
}

// SecretRealmRotationView is what the dashboard and the rumours ask: which
// realm opened last, which is next, and when.
func SecretRealmRotationView(conn *storage.Conn, catalog worlddata.Catalog, gm int64) (map[string]any, error) {
	ids := secretRealmIDs(catalog)
	state, err := readSecretRealmRotationTx(conn)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"realms": len(ids), "interval_minutes": secretRealmRotationMinutes, "last_realm_id": state.LastRealmID, "last_game_minute": state.LastGameMinute, "next_realm_id": "", "next_game_minute": state.LastGameMinute + secretRealmRotationMinutes}
	if len(ids) > 0 {
		out["next_realm_id"] = ids[state.Index%len(ids)]
	}
	if state.LastGameMinute == 0 {
		out["next_game_minute"] = gm
	}
	return out, nil
}
