package game

import (
	"encoding/json"
	"fmt"

	"xianxia/core/internal/storage"
)

// Good deeds are worth karma (v1.9.1, on the owner's call). Until now karma
// moved for a forbidden art, a robbery, an execution and a personal event's
// authored delta, and almost nothing a cultivator did for somebody else raised
// it. Four deeds do now, each a small, capped amount, because karma gates the
// hidden sect's initiation (at or below -200) and a sect's trial preference,
// so a deed that could be repeated without limit would be a way to buy either.
//
//   - a successful good deed in a world event (Aid Locals, Support Response,
//     Stabilize, Evacuate, Defend): +1, at most three per event;
//   - a successful Resolve scene action against something other than the
//     cultivator themself (which always succeeds): +1, once per world day;
//   - helping a personal exploration event to its end: +1, once per event;
//   - taking the last of a world event's site: +3, once per event.
//
// The count is event_log rows of one type keyed on the deed, so the record is
// the memory and a reset or erasure takes it with the character.
const (
	deedKarmaEventLogType = "karma_deed"

	worldEventDeedKarma              = int64(1)
	worldEventDeedKarmaCap           = int64(3)
	sceneResolveDeedKarma            = int64(1)
	personalEventDeedKarma           = int64(1)
	eventSiteClearedKarma            = int64(3)
	deedKarmaDayGameMinutes          = int64(1440)
	deedKarmaFloor, deedKarmaCeiling = int64(-1000), int64(1000)
)

// worldEventGoodDeeds are the world-event actions that are somebody helping.
// Interfere, Infiltrate and Exploit Opportunity are not, and nor is watching,
// gathering or competing.
var worldEventGoodDeeds = map[string]bool{
	"aid": true, "support": true, "stabilize": true, "evacuate": true, "defend": true,
}

// grantDeedKarmaTx pays `delta` karma for the deed named `key`, unless the
// cultivator already holds `limit` payments for that key. It answers what it
// paid and the karma after, and never refuses the action it rides on: a table
// the fixture lacks, or any read that fails, pays nothing rather than erroring.
func grantDeedKarmaTx(conn *storage.Conn, userID int64, deed, key string, delta, limit int64, now float64) (int64, int64) {
	if delta == 0 || limit <= 0 {
		return 0, 0
	}
	counted, err := conn.Execute(`SELECT COUNT(*) FROM event_log WHERE user_id=? AND event_type=? AND json_extract(payload_json,'$.key')=?`,
		[]any{userID, deedKarmaEventLogType, key})
	if err != nil || len(counted.Rows) == 0 || storage.ParseInt(counted.Rows[0][0]) >= limit {
		return 0, 0
	}
	if _, err = conn.Execute(`UPDATE characters SET karma_score=MAX(?,MIN(?,karma_score+?)),updated_at=? WHERE user_id=?`,
		[]any{deedKarmaFloor, deedKarmaCeiling, delta, now, userID}); err != nil {
		return 0, 0
	}
	after, err := conn.Execute(`SELECT karma_score FROM characters WHERE user_id=?`, []any{userID})
	if err != nil || len(after.Rows) == 0 {
		return 0, 0
	}
	karma := storage.ParseInt(after.Rows[0][0])
	payload, _ := json.Marshal(map[string]any{"deed": deed, "key": key, "delta": delta, "score": karma})
	if _, err = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, deedKarmaEventLogType, string(payload), now}); err != nil {
		return 0, 0
	}
	return delta, karma
}

// deedKarmaResult is the shape every deed reports, so the bot reads one.
func deedKarmaResult(deed string, paid, karma int64) map[string]any {
	if paid == 0 {
		return nil
	}
	return map[string]any{"deed": deed, "karma_delta": paid, "karma_score": karma}
}

// eventSiteClearedKarmaTx pays whoever took the last unit of a world event's
// whole site - every node at zero - once per event.
func eventSiteClearedKarmaTx(conn *storage.Conn, userID int64, eventKey string, now float64) map[string]any {
	left, err := conn.Execute(`SELECT COALESCE(SUM(remaining),0), COUNT(*) FROM world_event_nodes WHERE event_key=?`, []any{eventKey})
	if err != nil || len(left.Rows) == 0 || storage.ParseInt(left.Rows[0][1]) == 0 || storage.ParseInt(left.Rows[0][0]) > 0 {
		return nil
	}
	paid, karma := grantDeedKarmaTx(conn, userID, "event_site_cleared", "site_cleared:"+eventKey, eventSiteClearedKarma, 1, now)
	return deedKarmaResult("event_site_cleared", paid, karma)
}

func sceneResolveDeedKey(gameMinute int64) string {
	return fmt.Sprintf("scene_resolve:day:%d", gameMinute/deedKarmaDayGameMinutes)
}
