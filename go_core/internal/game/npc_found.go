package game

// Finding somebody the world had given up on (v1.0.0-rc.23).
//
// The simulation records a disappearance at a significance the Quest Forge
// reads, so the world asks a player to go and find them. This is the other end
// of it, and the reason the search is real rather than theatre: the only way
// to clear a disappearance is to be standing where the missing person actually
// is. Nothing here takes the searcher's word for that - the engine reads the
// NPC's own location and compares it to the character's.
//
// It is idempotent and quiet. Speaking to somebody who was never missing, or
// who has already been found, is not an error; it simply reports that there
// was nothing to find.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

type npcFoundPayload struct {
	NPCName    string `json:"npc_name"`
	Location   string `json:"location"`
	GameMinute int64  `json:"game_minute"`
}

func npcFound(conn *storage.Conn, userID int64, raw json.RawMessage) (any, error) {
	var p npcFoundPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	name := strings.TrimSpace(p.NPCName)
	if name == "" {
		return nil, errors.New("npc_name is required")
	}
	if !tableExistsTx(conn, "npc_civilization_state") {
		return map[string]any{"found": false, "was_missing": false}, nil
	}
	res, err := conn.Execute(`SELECT current_location,home_location,status,missing_since_game_minute
        FROM npc_civilization_state WHERE npc_name=?`, []any{name})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return map[string]any{"found": false, "was_missing": false}, nil
	}
	row := res.Rows[0]
	current := strings.TrimSpace(fmt.Sprint(row[0]))
	home := strings.TrimSpace(fmt.Sprint(row[1]))
	status := strings.TrimSpace(fmt.Sprint(row[2]))
	since := storage.ParseInt(row[3])
	if status != "missing" {
		return map[string]any{"found": false, "was_missing": false}, nil
	}
	if !strings.EqualFold(current, strings.TrimSpace(p.Location)) {
		// Somebody looking in the wrong place learns nothing, and the world
		// does not quietly find them on their behalf.
		return map[string]any{"found": false, "was_missing": true, "elsewhere": true}, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`UPDATE npc_civilization_state
        SET status='alive',missing_since_game_minute=0,activity='Found, and in no hurry to explain',
            last_game_minute=?,updated_at=?
        WHERE npc_name=? AND status='missing'`,
		[]any{p.GameMinute, now, name}); err != nil {
		return nil, err
	}
	daysGone := int64(0)
	if since > 0 && p.GameMinute > since {
		daysGone = (p.GameMinute - since) / 1440
	}
	uid := userID
	if err := recordWorldHistoryTx(conn,
		fmt.Sprintf("npc_found:%s:%d", name, p.GameMinute),
		"npc_found",
		name+" is found",
		fmt.Sprintf("%s was found alive at %s after %d day(s) unaccounted for, and sent word back to %s.",
			name, current, daysGone, home),
		foundBySearchSignificance, "public", current, "",
		"player", fmt.Sprint(userID), fmt.Sprint(userID),
		"npc", name, name,
		&uid, name, []string{"npc_found", "search"}, p.GameMinute,
		map[string]any{"days_missing": daysGone, "last_known_home": home}, now); err != nil {
		return nil, err
	}
	return map[string]any{
		"found": true, "was_missing": true, "npc_name": name,
		"location": current, "days_missing": daysGone, "home_location": home,
	}, nil
}

// A player closing a disappearance is worth more than the world noticing one
// resolved itself, and less than the disappearance was.
const foundBySearchSignificance = 68
