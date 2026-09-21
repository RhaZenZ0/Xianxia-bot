package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// A Discord id is too big for a float, and the payload was decoded into one
// (v1.0.12).
//
// `decodeMap` is `json.Unmarshal` into `map[string]any`, which turns every JSON
// number into a **float64**. A Discord snowflake is about 1.4e18 and float64
// carries 2^53 ≈ 9.0e15 exactly, so every id a payload names came back off by a
// digit or two: 1456074443989188610 decodes as ...608.
//
// Only the untyped path is affected, which is why the game works. `ActionRequest`
// declares `ActorID int64`, and `encoding/json` parses a number straight into a
// typed field with no float in between - so every player action, which addresses
// the actor, is exact. What goes through `decodeMap` is the **payload**, and the
// operations that carry a `user_id` there are the GM's: `admin.player.set_realm`,
// `karma`, `teleport`, `grant`, `set_sect`, `erase` and the rest of the console.
//
// `storage.ParseInt` has carried a `case json.Number` since it was written, and
// nothing could ever produce one - the reader was correct and the decoder never
// handed it the type it was written for. That is the `npc_consignments` shape
// (rc.28): the fix is the value, not the readers.
//
// Found by the Discord playtest, which asked the engine to raise a character's
// realm and was told "character not found" about a character the panel three
// lines above had just drawn.

func snowflakeDB(t *testing.T, uid int64) string {
	t.Helper()
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(
		`INSERT INTO characters(user_id,name,life_status,location,karma_score,vitality,vitality_max,qi,qi_max,spirit_stones,realm_index,phase,updated_at)
		 VALUES(?,'Snowflake','alive','Greenriver Town',0,20,20,30,30,10,0,1,0)`, []any{uid}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

// The smallest id that float64 cannot hold exactly, and a real Discord one.
const (
	firstLossyID  int64 = 1 << 53
	realDiscordID int64 = 1456074443989188610
)

func TestAPayloadCarriesADiscordIdExactly(t *testing.T) {
	for _, uid := range []int64{firstLossyID + 1, realDiscordID} {
		t.Run(fmt.Sprint(uid), func(t *testing.T) {
			path := snowflakeDB(t, uid)
			// A Go int64 marshals as a JSON number, which is exactly what the
			// Python client sends for a Discord id.
			applyAdmin(t, path, "admin.player.set_realm", map[string]any{
				"user_id": uid, "realm_index": 5, "phase": 1, "reason": "test",
			})
			got := storage.ParseInt(scalar(t, path, "SELECT realm_index FROM characters WHERE user_id=?", uid))
			if got != 5 {
				t.Fatalf("realm_index is %d for user %d, want 5. The payload decoded the id as a "+
					"float64, which cannot hold a Discord snowflake: it addressed %d instead.",
					got, uid, int64(float64(uid)))
			}
		})
	}
}

func TestAnIdInsideAFloatIsStillExact(t *testing.T) {
	// The other direction: ids small enough for a float must keep working, so
	// the fix is not a new class of refusal. 42 is what every other admin test
	// in this package uses.
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_realm", map[string]any{
		"user_id": 42, "realm_index": 3, "phase": 2, "reason": "test",
	})
	if got := storage.ParseInt(scalar(t, path, "SELECT realm_index FROM characters WHERE user_id=42")); got != 3 {
		t.Fatalf("realm_index is %d, want 3", got)
	}
}

func TestTheAuditRowNamesTheIdItActuallyWrote(t *testing.T) {
	// The audit trail is the half a GM reads back, and a target naming an id
	// nobody holds is worse than a refusal: it says the action landed on
	// somebody.
	path := snowflakeDB(t, realDiscordID)
	applyAdmin(t, path, "admin.player.set_realm", map[string]any{
		"user_id": realDiscordID, "realm_index": 4, "phase": 1, "reason": "test",
	})
	want := fmt.Sprintf("user:%d", realDiscordID)
	if got := fmt.Sprint(scalar(t, path, "SELECT target FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != want {
		t.Fatalf("the audit row's target is %q, want %q", got, want)
	}
}
