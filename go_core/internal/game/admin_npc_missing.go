package game

// The disappearance a GM can stage (v1.0.0-rc.38).
//
// Until now nothing but the `npc_life` batch could lose an NPC: three in a
// hundred of the people away from home, at most one a tick, and no lever.
// A GM with a story to tell - the herbalist who never came back from the
// marsh - had to wait for the dice, and the engine playtest could not drive
// `npc.found` at all. `admin.npc.set_missing` is that lever. It writes the
// batch's exact row and the batch's exact history entry, through one helper
// the batch now calls too, so a staged disappearance reaches the Quest Forge
// the way a rolled one does and the two cannot drift.
//
// `missing:false` is the GM bringing somebody home off-screen. It is not
// `npc.found`: nobody stood where they were, so no search is recorded and
// the row it writes is a quieter one.

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"xianxia/core/internal/storage"
)

const (
	// Above the Forge's default bar of 80, which nothing else the
	// simulation writes has ever cleared. See simulation/npc_missing.go.
	NPCMissingSignificance int64 = 82
	// A return the GM staged is news, not a quest.
	npcReturnedSignificance int64 = 40
)

// RecordNPCMissingTx writes the public history row a disappearance leaves:
// where they were last seen (home) rather than where they are (`where`
// stays on the NPC's own row), because the first is what the world knows and
// the second is what a searcher has to work out.
func RecordNPCMissingTx(conn *storage.Conn, name, home, where string, gm int64, now float64) error {
	return recordWorldHistoryTx(conn, fmt.Sprintf("npc_missing:%s:%d", name, gm), "npc_missing",
		name+" has not come home",
		fmt.Sprintf("%s left %s and never arrived. The last anyone can place them is the road out of %s, and nobody at %s has seen them since.",
			name, home, where, home),
		NPCMissingSignificance, "public", home, "", "npc", name, name, "npc", name, name, nil, name,
		[]string{"npc_missing"}, gm, map[string]any{}, now)
}

func adminNpcSetMissing(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	name := stringField(p, "npc_name")
	if name == "" {
		return nil, errors.New("npc_name is required")
	}
	missing, ok := p["missing"].(bool)
	if !ok {
		return nil, errors.New("missing must be true (lose them) or false (bring them back)")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT status,missing_since_game_minute,home_location,current_location FROM npc_civilization_state WHERE npc_name=?`, []any{name})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("npc not found")
	}
	status := fmt.Sprint(row["status"])
	since := i64(row["missing_since_game_minute"])
	home, where := fmt.Sprint(row["home_location"]), fmt.Sprint(row["current_location"])
	gm, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	before := map[string]any{"status": status, "missing_since_game_minute": since}
	var after map[string]any
	if missing {
		switch status {
		case "alive":
		case "dead":
			return nil, errors.New("npc is dead")
		case "missing":
			return nil, errors.New("npc is already missing")
		default:
			return nil, fmt.Errorf("npc is %s, not alive", status)
		}
		if _, err = conn.Execute(`UPDATE npc_civilization_state
            SET status='missing',missing_since_game_minute=?,activity='Whereabouts unknown',
                last_game_minute=?,updated_at=?
            WHERE npc_name=? AND status='alive'`, []any{gm, gm, now, name}); err != nil {
			return nil, err
		}
		if err = RecordNPCMissingTx(conn, name, home, where, gm, now); err != nil {
			return nil, err
		}
		after = map[string]any{"status": "missing", "missing_since_game_minute": gm}
	} else {
		if status != "missing" {
			return nil, errors.New("npc is not missing")
		}
		if _, err = conn.Execute(`UPDATE npc_civilization_state
            SET status='alive',missing_since_game_minute=0,activity='Returned, and in no hurry to explain',
                last_game_minute=?,updated_at=?
            WHERE npc_name=? AND status='missing'`, []any{gm, now, name}); err != nil {
			return nil, err
		}
		days := int64(0)
		if since > 0 && gm > since {
			days = (gm - since) / 1440
		}
		if err = recordWorldHistoryTx(conn, fmt.Sprintf("npc_returned:%s:%d", name, gm), "npc_returned",
			name+" is home",
			fmt.Sprintf("%s has come back to %s after %d day(s), and says nothing of where they were.", name, home, days),
			npcReturnedSignificance, "public", home, "", "npc", name, name, "npc", name, name, nil, name,
			[]string{"npc_returned"}, gm, map[string]any{"days_missing": days}, now); err != nil {
			return nil, err
		}
		after = map[string]any{"status": "alive", "missing_since_game_minute": int64(0)}
	}
	if err := auditAdmin(conn, adminUserID, "admin.npc.set_missing", fmt.Sprintf("npc:%s", name), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{
		"npc_name": name, "status": after["status"], "missing_since_game_minute": after["missing_since_game_minute"],
		"location": where, "home_location": home, "game_minute": gm,
	}, nil
}
