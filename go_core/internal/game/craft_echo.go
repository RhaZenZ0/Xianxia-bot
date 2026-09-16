package game

import (
	"encoding/json"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
)

// The craft echo (v1.0.0-rc.32).
//
// Samsara clears `profession_progress` before the new household tutors the
// new life, so until now a past life's crafting left no trace at all - while
// everything else the soul did survived as an echo: realm resets to 0 but
// `law_echo` lends comprehension rolls a bonus, talent and insight carry as
// percentages, and `memory_seed` is a ceiling that `awakened_memory` climbs
// toward through breakthroughs and law insight. Crafting was the one thing a
// soul had done that it could not remember.
//
// Two halves. `pastLifeProfessionsTx` reads the dying life's trades into the
// past-life record, ahead of the wipe (the record is written first, so the
// rows are still there). `craftEchoTx` reads them back: the best level a
// profession reached in any recorded life, scaled by how much of the soul's
// memory has woken - the same fraction `/soul` uses to decide what is
// visible at all - and capped, so an echo is a nudge, never a substitute for
// practising. A fresh rebirth remembers nothing (`awakened_memory` is reset
// to 0); as memory wakes, the hands remember.

// craftEchoCap is the most a past life's hands can lend a roll.
const craftEchoCap = 3

// pastLifeProfessionsTx is every trade this incarnation practised past level
// 0, read before samsara clears profession_progress.
func pastLifeProfessionsTx(conn *storage.Conn, userID int64) (map[string]int64, error) {
	if !tableExistsTx(conn, "profession_progress") {
		return map[string]int64{}, nil
	}
	res, err := conn.Execute(`SELECT profession,level FROM profession_progress WHERE user_id=? AND level>0 ORDER BY profession`, []any{userID})
	if err != nil {
		return nil, err
	}
	out := map[string]int64{}
	for _, row := range res.Rows {
		out[fmt.Sprint(row[0])] = i64(row[1])
	}
	return out, nil
}

// craftEchoTx is the bonus a soul's own past-life mastery lends a craft:
// min(cap, bestLevel * awakened / seed). It names the life that had the
// hands, the most recent one when two reached the same level.
func craftEchoTx(conn *storage.Conn, userID int64, profession string) (bonus int64, life string, level int64, err error) {
	profession = strings.TrimSpace(profession)
	if profession == "" || !tableExistsTx(conn, "soul_legacy") {
		return 0, "", 0, nil
	}
	res, err := conn.Execute(`SELECT memory_seed,awakened_memory,past_lives_json FROM soul_legacy WHERE user_id=?`, []any{userID})
	if err != nil || len(res.Rows) == 0 {
		return 0, "", 0, err
	}
	row := res.Rows[0]
	seed, awakened := i64(row[0]), i64(row[1])
	if seed <= 0 || awakened <= 0 {
		return 0, "", 0, nil
	}
	var lives []map[string]any
	_ = json.Unmarshal([]byte(fmt.Sprint(row[2])), &lives)
	best, bestLife := int64(0), ""
	// Oldest first in storage; `>=` lets a later life with the same level
	// take the name, so the echo is of the hands the soul used last.
	for _, entry := range lives {
		professions, _ := entry["professions"].(map[string]any)
		if professions == nil {
			continue
		}
		lvl := int64(0)
		for name, v := range professions {
			if strings.EqualFold(name, profession) {
				lvl = i64(v)
			}
		}
		if lvl > 0 && lvl >= best {
			best, bestLife = lvl, fmt.Sprint(entry["name"])
		}
	}
	if best <= 0 {
		return 0, "", 0, nil
	}
	if awakened > seed {
		awakened = seed
	}
	return minI64(craftEchoCap, best*awakened/seed), bestLife, best, nil
}
