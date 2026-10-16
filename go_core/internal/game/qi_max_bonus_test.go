package game

import (
	"fmt"
	"strings"
	"testing"
	"xianxia/core/internal/storage"
)

// A permanent qi_max reward outlives the next settle (schema 81).
//
// settleQi rebuilds the pool from the body - realm, spirit, channels, method -
// and writes it back to `qi_max` on every read. A perfected realm's tenth, a
// secret realm's inheritance and a GM's cap were all written onto `qi_max`
// alone, so each lasted until the cultivator next looked at their qi. These
// tests settle on a fresh connection after each reward, because a test that
// reads `qi_max` straight after the write is the test that passed.

const qiMaxBonusColumnDDL = `ALTER TABLE characters ADD COLUMN qi_max_bonus INTEGER NOT NULL DEFAULT 0`

// settledCapacity settles the cultivator on a fresh connection and returns the
// capacity the engine now holds them to, with `qi_max` as it was written back.
func settledCapacity(t *testing.T, path string, minute int64) (capacity, column int64) {
	t.Helper()
	catalog := qiBodyCatalog(t)
	conn := mustOpen(t, path)
	st, err := settleQi(conn, catalog, 42, minute, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		t.Fatal(err)
	}
	res, err := conn.Execute(`SELECT qi_max FROM characters WHERE user_id=42`, nil)
	if err != nil {
		t.Fatal(err)
	}
	return st.Capacity, storage.ParseInt(res.Rows[0][0])
}

func TestAPerfectedRealmsQiOutlivesTheNextSettle(t *testing.T) {
	world := batch4WorldPath(t)
	for _, tc := range []struct{ track, trialOp string }{
		{"cultivation", "perfection.trial"},
		{"body", "perfection.body_trial"},
	} {
		t.Run(tc.track, func(t *testing.T) {
			path := setupBatch4AuthorityDB(t)
			batch4Exec(t, path, qiMaxBonusColumnDDL)
			batch4Exec(t, path, perfectionAuditDDL)
			applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": tc.track, "realm_index": 0, "progress": 100, "reason": "story"})
			base, _ := settledCapacity(t, path, 300)

			passThePerfectionTrial(t, path, world, tc.trialOp, 2)
			raised := storage.ParseInt(actionScalar(t, path, "SELECT qi_max FROM characters WHERE user_id=42"))
			if raised <= base {
				t.Fatalf("the trial raised qi_max to %d from %d; the reward was not paid", raised, base)
			}
			capacity, column := settledCapacity(t, path, 301)
			if capacity != raised || column != raised {
				t.Fatalf("qi_max fell back to %d (capacity %d) on the next settle after the trial raised it to %d", column, capacity, raised)
			}
			if bonus := storage.ParseInt(actionScalar(t, path, "SELECT qi_max_bonus FROM characters WHERE user_id=42")); bonus != raised-base {
				t.Fatalf("qi_max_bonus=%d, want the %d the trial paid", bonus, raised-base)
			}
		})
	}
}

func TestAnInheritancesQiOutlivesTheNextSettle(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, qiMaxBonusColumnDDL)
	batch4Exec(t, path, "UPDATE characters SET location='Cloudspine Foothills' WHERE user_id=42")
	base, _ := settledCapacity(t, path, 300)
	batch4Exec(t, path, "INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at,thread_id) VALUES(?,?,?,?,?,?,1,?,?,?)",
		"qi-bonus-secret-open", "secret_realm:sword_grave_nine_echoes", "secret_realm", "Sword Grave of Nine Echoes", "Cloudspine Foothills", `{"realm_id":"sword_grave_nine_echoes"}`, 1.0, 1e12, 987654321)
	batch4Result(t, batch4Apply(t, path, world, "secret_realm.enter", 1, map[string]any{"realm_id": "sword_grave_nine_echoes", "game_minute": 631}))
	for i := 0; i < 4; i++ {
		clearCooldowns(t, path, 42)
		batch4Result(t, batch4Apply(t, path, world, "secret_realm.explore", 10+i, map[string]any{"game_minute": 633 + i}))
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM inheritances WHERE user_id=42")); got != 1 {
		t.Fatalf("inheritance rows=%d; the realm was not cleared", got)
	}
	raised := storage.ParseInt(actionScalar(t, path, "SELECT qi_max FROM characters WHERE user_id=42"))
	capacity, column := settledCapacity(t, path, 700)
	if raised <= base || capacity != raised || column != raised {
		t.Fatalf("qi_max fell back to %d (capacity %d) on the next settle after the inheritance raised it to %d from %d", column, capacity, raised, base)
	}
}

func TestAGMsQiCapOutlivesTheNextSettle(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, qiMaxBonusColumnDDL)
	batch4Exec(t, path, perfectionAuditDDL)
	base, _ := settledCapacity(t, path, 300)

	for _, want := range []int64{base + 500, base - 10} {
		applyAdmin(t, path, "admin.player.set_resource_caps", map[string]any{"user_id": 42, "qi_max": want, "reason": "story"})
		capacity, column := settledCapacity(t, path, 301)
		if capacity != want || column != want {
			t.Fatalf("qi_max fell back to %d (capacity %d) on the next settle after a GM set the cap to %d", column, capacity, want)
		}
	}
	// The audit row says what the lever wrote, bonus included.
	after := fmt.Sprint(actionScalar(t, path, "SELECT after_json FROM admin_audit_log WHERE action='admin.player.set_resource_caps' ORDER BY audit_id DESC LIMIT 1"))
	if want := fmt.Sprintf(`"qi_max_bonus":%d`, -10); !strings.Contains(after, want) {
		t.Fatalf("the audit row %s does not carry %s", after, want)
	}
}

// Before schema 81 the engine is healthy and the column is not there: the
// reward is paid as it always was and nothing refuses (v1.1.0's rule).
func TestTheQiBonusDegradesWhereTheColumnIsNotThereYet(t *testing.T) {
	world := batch4WorldPath(t)
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, perfectionAuditDDL)
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	base, _ := settledCapacity(t, path, 300)
	passThePerfectionTrial(t, path, world, "perfection.trial", 2)
	if got := storage.ParseInt(actionScalar(t, path, "SELECT qi_max FROM characters WHERE user_id=42")); got <= base {
		t.Fatalf("qi_max=%d, the trial did not pay without the column", got)
	}
	applyAdmin(t, path, "admin.player.set_resource_caps", map[string]any{"user_id": 42, "qi_max": base + 5, "reason": "window"})
	if capacity, _ := settledCapacity(t, path, 301); capacity != base {
		t.Fatalf("capacity=%d without the column, want the body's own %d", capacity, base)
	}
}
