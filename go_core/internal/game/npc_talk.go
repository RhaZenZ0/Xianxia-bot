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

// A talk is worth one trust a day (v1.37.0).
//
// /talk used to send `trust: 1` through relationship.update on every exchange,
// so the bot decided what a conversation was worth and a player who talked to
// an Elder sixty times in an afternoon was trusted like an old friend. The
// lever is a GM's now (admin_player_levers.go) and the rule is here: the engine
// counts the encounter and keeps the summary every time, and raises trust by
// one the first time a cultivator speaks with an NPC in a world day, read off
// `npc_relationships.last_talk_game_minute` (schema 82). On a database that has
// not migrated yet the column is absent and every talk pays its point, which
// is what every talk did before; a refused talk would be the worse failure.
type npcTalkPayload struct {
	NPCName    string `json:"npc_name"`
	Summary    string `json:"summary"`
	GameMinute int64  `json:"game_minute"`
}

const talkTrustGameMinutes = gameMinutesPerDay

func npcTalkAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p npcTalkPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.NPCName = strings.TrimSpace(p.NPCName)
	if p.NPCName == "" {
		return authoritativeMutation{}, errors.New("npc_name is required")
	}
	dayGated, err := tableHasColumns(conn, "npc_relationships", "last_talk_game_minute")
	if err != nil {
		return authoritativeMutation{}, err
	}
	columns := `trust,respect,fear,affection,debt,grudge,encounter_count`
	if dayGated {
		columns += `,last_talk_game_minute`
	}
	res, err := conn.Execute(`SELECT `+columns+` FROM npc_relationships WHERE user_id=? AND npc_name=?`, []any{userID, p.NPCName})
	if err != nil {
		return authoritativeMutation{}, err
	}
	current := map[string]int64{"encounter_count": 0, "last_talk_game_minute": 0}
	for _, dim := range relationshipDimensions {
		current[dim] = 0
	}
	if row := firstRowMap(res); row != nil {
		for k := range current {
			if v, ok := row[k]; ok {
				current[k] = i64(v)
			}
		}
	}
	gained := true
	if dayGated {
		last := current["last_talk_game_minute"]
		if last > 0 && p.GameMinute-last < talkTrustGameMinutes {
			gained = false
		}
	}
	trust := current["trust"]
	paidAt := current["last_talk_game_minute"]
	if gained {
		trust = clamp(trust+1, -100, 100)
		paidAt = p.GameMinute
	}
	encounter := current["encounter_count"] + 1
	summary := []rune(p.Summary)
	if len(summary) > 800 {
		summary = summary[:800]
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if dayGated {
		_, err = conn.Execute(`INSERT INTO npc_relationships(user_id,npc_name,trust,respect,fear,affection,debt,grudge,encounter_count,last_summary,updated_at,last_talk_game_minute)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,npc_name) DO UPDATE SET trust=excluded.trust,encounter_count=excluded.encounter_count,last_summary=excluded.last_summary,updated_at=excluded.updated_at,last_talk_game_minute=excluded.last_talk_game_minute`,
			[]any{userID, p.NPCName, trust, current["respect"], current["fear"], current["affection"], current["debt"], current["grudge"], encounter, string(summary), now, paidAt})
	} else {
		_, err = conn.Execute(`INSERT INTO npc_relationships(user_id,npc_name,trust,respect,fear,affection,debt,grudge,encounter_count,last_summary,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,npc_name) DO UPDATE SET trust=excluded.trust,encounter_count=excluded.encounter_count,last_summary=excluded.last_summary,updated_at=excluded.updated_at`,
			[]any{userID, p.NPCName, trust, current["respect"], current["fear"], current["affection"], current["debt"], current["grudge"], encounter, string(summary), now})
	}
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"user_id": userID, "npc_name": p.NPCName, "trust": trust, "respect": current["respect"], "fear": current["fear"],
		"affection": current["affection"], "debt": current["debt"], "grudge": current["grudge"],
		"encounter_count": encounter, "last_summary": string(summary), "trust_gained": gained,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "npc", EventType: "npc.talk", EntityType: "npc", EntityID: p.NPCName, SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
