package game

// The world_state rows that belong to one person (v1.34.0).
//
// `world_state` is the engine's small key/value shelf, and most of what sits on
// it is the world's: the clock, the automation switches, the travel pace. Seven
// keys are a player's, spelled `<prefix>:<discord user id>` - the insight a
// cultivator banked, the stance they hold, the manual they chose, the failed
// breakthrough they may seize, the reroll they spent, the clock their lifespan
// counts from, and the road they are on.
//
// None of them is a column with a user id in it, so `erasureTargets` - which
// reads the live schema for a Discord id - cannot see them, and nothing else
// swept them either: a rebirth carried the old body's banked insight and
// `PausedGameMinutes` into the next life (the second is the real leak: a body
// that was paused for a retreat is younger than its years), and a reset or an
// erasure left rows that name a Discord account in a table that has no owner to
// ask. This list is the one place those keys are named for clearing. Each
// writer keeps its own key function; `TestEveryPlayerWorldStateKeyIsInTheList`
// holds the two together from this side, and
// tests/python/contracts/test_player_world_state.py from the other, by finding
// every `Sprintf("<prefix>:%d", userID)` in the engine - so a new writer fails
// the gate until its prefix is on this list and in the three doors that clear it.

import (
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
)

// playerWorldStatePrefixes are the `<prefix>:<user_id>` keys a player owns.
var playerWorldStatePrefixes = []string{
	"cultivation_insight",
	"cultivation_last_fail",
	"cultivation_manual",
	"cultivation_reroll",
	"cultivation_stance",
	"player_lifespan_clock",
	"road_transit",
}

// clearPlayerWorldStateTx deletes every player-keyed world_state row for one
// person and says how many there were. A database without the table (a narrow
// fixture, a migration window) has nothing to clear.
func clearPlayerWorldStateTx(conn *storage.Conn, userID int64) (int64, error) {
	if !tableExistsTx(conn, "world_state") {
		return 0, nil
	}
	keys := make([]any, 0, len(playerWorldStatePrefixes))
	marks := make([]string, 0, len(playerWorldStatePrefixes))
	for _, prefix := range playerWorldStatePrefixes {
		keys = append(keys, fmt.Sprintf("%s:%d", prefix, userID))
		marks = append(marks, "?")
	}
	res, err := conn.Execute(
		`DELETE FROM world_state WHERE key IN (`+strings.Join(marks, ",")+`)`, keys)
	if err != nil {
		return 0, err
	}
	return res.RowsAffected, nil
}
