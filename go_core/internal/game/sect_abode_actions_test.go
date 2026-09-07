package game

// v0.23.0 regression tests for sect.abode.enter / sect.abode.leave.
//
// The rule under test is adjacency: a sect abode is reached from its gate and
// left back to it. Discord used to check the character's location itself and
// then write the new one, which is two reads of a value that can change in
// between - and, more to the point, put a movement rule in the presentation
// layer where nothing else could enforce it.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupSectAbodeDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE sect_abodes(
	user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL, name TEXT NOT NULL,
	location_key TEXT NOT NULL UNIQUE, base_location TEXT NOT NULL,
	thread_id INTEGER, thread_channel_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
INSERT INTO sect_abodes(user_id,sect_name,name,location_key,base_location,created_at,updated_at)
VALUES(42,'Azure Cloud Sect','Lin Test''s Cloud Loft','sect_abode:42','Greenriver Town',0,0);
`); err != nil {
		t.Fatal(err)
	}
	batch4SetCanonicalGameMinute(t, path, 4000)
	return path
}

func sectAbodeApply(t *testing.T, path string, actor int64, op, actionID string) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, "", ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   actionID,
		Operation:  op,
		ActorID:    actor,
		Payload:    raw,
	})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func characterLocation(t *testing.T, path string, userID int64) string {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		t.Fatal(err)
	}
	return fmt.Sprint(firstRowMap(res)["location"])
}

func TestEnteringAndLeavingASectAbodeMovesTheCharacter(t *testing.T) {
	path := setupSectAbodeDB(t)
	if got := characterLocation(t, path, 42); got != "Greenriver Town" {
		t.Fatalf("fixture location=%q", got)
	}
	if _, err := sectAbodeApply(t, path, 42, "sect.abode.enter", "abode-in"); err != nil {
		t.Fatal(err)
	}
	if got := characterLocation(t, path, 42); got != "sect_abode:42" {
		t.Fatalf("after entering location=%q", got)
	}
	if _, err := sectAbodeApply(t, path, 42, "sect.abode.leave", "abode-out"); err != nil {
		t.Fatal(err)
	}
	if got := characterLocation(t, path, 42); got != "Greenriver Town" {
		t.Fatalf("after leaving location=%q, want the sect gate", got)
	}
}

func TestEnteringFromSomewhereOtherThanTheGateIsRefused(t *testing.T) {
	path := setupSectAbodeDB(t)
	batch4Exec(t, path, `UPDATE characters SET location='Blackstone Pass' WHERE user_id=42`)
	if _, err := sectAbodeApply(t, path, 42, "sect.abode.enter", "abode-far"); err == nil {
		t.Fatal("a cultivator entered their sect abode from another region")
	}
	if got := characterLocation(t, path, 42); got != "Blackstone Pass" {
		t.Fatalf("location=%q; the refused move happened anyway", got)
	}
}

func TestLeavingWhenNotInsideIsRefused(t *testing.T) {
	path := setupSectAbodeDB(t)
	if _, err := sectAbodeApply(t, path, 42, "sect.abode.leave", "abode-nope"); err == nil {
		t.Fatal("a cultivator left an abode they were not in")
	}
	if got := characterLocation(t, path, 42); got != "Greenriver Town" {
		t.Fatalf("location=%q", got)
	}
}

func TestEnteringTwiceSaysSoRatherThanSendingThePlayerToTheGate(t *testing.T) {
	// The gate check alone would already refuse this - someone inside their
	// abode is not standing at the gate - but it refuses with "travel to
	// Greenriver Town first", which is nonsense advice for a player who is
	// standing in the room they just asked to enter. The dedicated check
	// exists for the message, so the message is what this asserts.
	path := setupSectAbodeDB(t)
	if _, err := sectAbodeApply(t, path, 42, "sect.abode.enter", "abode-1"); err != nil {
		t.Fatal(err)
	}
	_, err := sectAbodeApply(t, path, 42, "sect.abode.enter", "abode-2")
	if err == nil {
		t.Fatal("entering an abode the cultivator was already inside succeeded")
	}
	if !strings.Contains(err.Error(), "already inside") {
		t.Fatalf("refusal was %q; a player standing in their abode should be told that, not sent to the gate", err)
	}
}

func TestACultivatorWithNoSectAbodeCannotEnterOne(t *testing.T) {
	path := setupSectAbodeDB(t)
	batch4Exec(t, path, `DELETE FROM sect_abodes WHERE user_id=42`)
	if _, err := sectAbodeApply(t, path, 42, "sect.abode.enter", "abode-none"); err == nil {
		t.Fatal("a cultivator with no abode entered one")
	}
}

func TestARetriedAbodeMoveReplays(t *testing.T) {
	path := setupSectAbodeDB(t)
	first, err := sectAbodeApply(t, path, 42, "sect.abode.enter", "abode-retry")
	if err != nil {
		t.Fatal(err)
	}
	second, err := sectAbodeApply(t, path, 42, "sect.abode.enter", "abode-retry")
	if err != nil {
		t.Fatalf("the retry failed instead of replaying: %v", err)
	}
	if fmt.Sprint(first["location"]) != fmt.Sprint(second["location"]) {
		t.Fatalf("replay location=%v, original %v", second["location"], first["location"])
	}
}
