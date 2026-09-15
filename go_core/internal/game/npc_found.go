package game

// Finding somebody the world had given up on (v1.0.0-rc.24).
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
		// Not missing, but possibly buried. A search that arrives too late
		// still arrives: the grave is the answer, and it is one that can be
		// carried back.
		return claimGraveResult(conn, userID, name, p.Location, p.GameMinute)
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

// claimGraveResult is the other ending of a search. It grants what the dead were
// carrying - their own purse and one thing off their trade, both recorded when
// the grave was dug rather than invented now - and marks the grave visited, so
// the answer is carried back by the first person to reach it and not by
// everybody afterwards.
func claimGraveResult(conn *storage.Conn, userID int64, name, location string, gameMinute int64) (any, error) {
	miss := map[string]any{"found": false, "was_missing": false}
	if !tableExistsTx(conn, "npc_graves") {
		return miss, nil
	}
	// `claimed_game_minute` is what says the grave has been emptied, not
	// `claimed_by_user_id`. Who reached it first is personal and is erased on
	// request (it anonymises - see erasureAnonymise); *that* it was reached is
	// world canon and stays. Testing the id would mean an erasure refilled the
	// grave and its keepsake could be taken a second time.
	res, err := conn.Execute(`SELECT location,home_location,days_missing,keepsake_item,keepsake_stones,claimed_game_minute
        FROM npc_graves WHERE npc_name=?`, []any{name})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return miss, nil
	}
	row := res.Rows[0]
	where := strings.TrimSpace(fmt.Sprint(row[0]))
	home := strings.TrimSpace(fmt.Sprint(row[1]))
	days := storage.ParseInt(row[2])
	item := strings.TrimSpace(fmt.Sprint(row[3]))
	stones := storage.ParseInt(row[4])
	if row[5] != nil {
		// Somebody has already been here. The grave stays, and so does what
		// it says; it simply has nothing left to hand over.
		return map[string]any{"grave": true, "claimed": false, "already_claimed": true,
			"npc_name": name, "location": where, "days_missing": days, "home_location": home}, nil
	}
	if !strings.EqualFold(where, strings.TrimSpace(location)) {
		return map[string]any{"grave": true, "claimed": false, "elsewhere": true}, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`UPDATE npc_graves
        SET claimed_by_user_id=?,claimed_game_minute=?,updated_at=?
        WHERE npc_name=? AND claimed_game_minute IS NULL`,
		[]any{userID, gameMinute, now, name}); err != nil {
		return nil, err
	}
	if item != "" {
		if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,1)
            ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+1`, []any{userID, item}); err != nil {
			return nil, err
		}
	}
	if stones > 0 {
		if _, err := conn.Execute(`UPDATE characters SET spirit_stones=spirit_stones+?,updated_at=? WHERE user_id=?`,
			[]any{stones, now, userID}); err != nil {
			return nil, err
		}
	}
	uid := userID
	if err := recordWorldHistoryTx(conn,
		fmt.Sprintf("npc_grave_found:%s:%d", name, gameMinute),
		"npc_grave_found",
		"What became of "+name,
		fmt.Sprintf("%s was found at %s, %d day(s) after they stopped being anywhere. Word of it can be carried back to %s.",
			name, where, days, home),
		graveFoundSignificance, "public", where, "",
		"player", fmt.Sprint(userID), fmt.Sprint(userID),
		"npc", name, name,
		&uid, name, []string{"npc_grave_found", "search"}, gameMinute,
		map[string]any{"days_missing": days, "home_location": home, "keepsake_item": item, "keepsake_stones": stones},
		now); err != nil {
		return nil, err
	}
	return map[string]any{
		"grave": true, "claimed": true, "npc_name": name, "location": where,
		"home_location": home, "days_missing": days,
		"keepsake_item": item, "keepsake_stones": stones,
	}, nil
}

// Finding a grave answers the question the disappearance asked, which is worth
// as much as closing it alive was.
const graveFoundSignificance = 68
