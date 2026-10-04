package game

// An ordinary explore searches the ground it stands on (v1.22.1).
//
// Until now a disappearance could be closed one way: `/talk` to the missing
// person by name while standing where they are. That asked a searcher to
// already know who they were looking for, and the talk picker lists a missing
// person only where the searcher stands - so the search was a question nobody
// could ask without the answer. Exploring a place is what looking around it
// means, so an explore now turns up whoever has gone missing *here*, and the
// grave of anybody who died out here and was never reached.
//
// The rule is `npc.found`'s, unchanged: the engine reads the NPC's own
// location and compares it to where the explorer stands, exactly, and nothing
// is found from anywhere else. The writes are `markNPCFoundTx` and
// `claimGraveTx`, the same two statements the conversation path runs, inside
// the explore's own transaction.

import (
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// searchHereTx finds every missing person standing at `location` and empties
// every unclaimed grave there. It returns what it found, in name order so a
// reply reads the same way twice. A world without the tables finds nothing.
func searchHereTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, location string, gameMinute int64) (found []map[string]any, graves []map[string]any, err error) {
	location = strings.TrimSpace(location)
	if location == "" {
		return nil, nil, nil
	}
	if tableExistsTx(conn, "npc_civilization_state") {
		res, err := conn.Execute(`SELECT npc_name,current_location,home_location,missing_since_game_minute
            FROM npc_civilization_state
            WHERE status='missing' AND current_location=? COLLATE NOCASE
            ORDER BY npc_name`, []any{location})
		if err != nil {
			return nil, nil, err
		}
		for _, row := range res.Rows {
			name := strings.TrimSpace(nullableText(row[0]))
			if name == "" {
				continue
			}
			out, err := markNPCFoundTx(conn, userID, name, strings.TrimSpace(nullableText(row[1])),
				strings.TrimSpace(nullableText(row[2])), storage.ParseInt(row[3]), gameMinute)
			if err != nil {
				return nil, nil, err
			}
			if ok, _ := out["found"].(bool); ok {
				found = append(found, out)
			}
		}
	}
	if tableExistsTx(conn, "npc_graves") {
		res, err := conn.Execute(`SELECT npc_name,location,home_location,days_missing,keepsake_item,keepsake_stones
            FROM npc_graves
            WHERE location=? COLLATE NOCASE AND claimed_game_minute IS NULL
            ORDER BY npc_name`, []any{location})
		if err != nil {
			return nil, nil, err
		}
		for _, row := range res.Rows {
			name := strings.TrimSpace(nullableText(row[0]))
			if name == "" {
				continue
			}
			out, err := claimGraveTx(conn, catalog, userID, name, strings.TrimSpace(nullableText(row[1])),
				strings.TrimSpace(nullableText(row[2])), storage.ParseInt(row[3]),
				strings.TrimSpace(nullableText(row[4])), storage.ParseInt(row[5]), gameMinute)
			if err != nil {
				return nil, nil, err
			}
			if ok, _ := out["claimed"].(bool); ok {
				graves = append(graves, out)
			}
		}
	}
	return found, graves, nil
}

// nullableText reads a column that may be NULL as "", where fmt.Sprint would
// answer "<nil>".
func nullableText(v any) string {
	if v == nil {
		return ""
	}
	if b, ok := v.([]byte); ok {
		return string(b)
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
