package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// v1.31.0: every room a home can build grants a focus. Five of the nine -
// the Defensive Formation, the storehouse, the herb garden, the beast pen and
// the merchant hall - spent the press and applied nothing from rc.36 to
// v1.30.0, while the picker offered all nine.
func TestEveryFacilityFocusGrantsAnEffect(t *testing.T) {
	catalog := shippedCatalog(t)
	rooms := homesteadFacilities(catalog)
	if len(rooms) < 9 {
		t.Fatalf("the homestead roster all but disappeared: %v", rooms)
	}
	for _, room := range rooms {
		effectID := abodeFacilityEffects[room]
		if effectID == "" {
			t.Errorf("focusing the %s grants nothing", room)
			continue
		}
		if _, _, err := specialEffectPayload(catalog, effectID); err != nil {
			t.Errorf("the %s focus names %q: %v", room, effectID, err)
		}
	}
}

func setFocusClock(t *testing.T, path string, scale int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	state, _ := json.Marshal(map[string]any{"anchor_game_minute": 1000, "anchor_real_ts": nowSeconds(), "scale": scale})
	if _, err := conn.Execute(`CREATE TABLE IF NOT EXISTS world_state(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL DEFAULT 0)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json`, []any{string(state)}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func focusRoom(t *testing.T, path, world, room string) (map[string]any, error) {
	t.Helper()
	establishSeq++
	raw, _ := json.Marshal(map[string]any{"facility": room})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("focus-%s-%d", room, establishSeq), Operation: "abode.focus", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

// v1.31.0, on the owner's call: a focus lasts four real hours, then one more
// before the next - one wait across every room.
func TestAFocusLastsFourRealHoursThenWaitsOne(t *testing.T) {
	for _, scale := range []int64{2, 4} {
		path := setupPropertyTypesDB(t)
		world := batch4WorldPath(t)
		setFocusClock(t, path, scale)
		deaconMembership(t, path)
		home, err := establishProperty(t, path, world, "homestead")
		if err != nil {
			t.Fatal(err)
		}
		key := fmt.Sprint(home["location_key"])
		if key == "" || key == "<nil>" {
			key = fmt.Sprint(scalar(t, path, `SELECT location_key FROM cave_abodes WHERE user_id=42`))
		}
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		mustExec(t, conn, `UPDATE cave_abodes SET defense_level=1,merchant_level=1 WHERE user_id=42`)
		if _, err := conn.Execute(`UPDATE characters SET location=? WHERE user_id=42`, []any{key}); err != nil {
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		conn.Close()

		result, err := focusRoom(t, path, world, "defense")
		if err != nil {
			t.Fatalf("scale %d: focusing the Defensive Formation: %v", scale, err)
		}
		if got := fmt.Sprint(result["effect_id"]); got != "abode_ward_focus" {
			t.Fatalf("the Defensive Formation granted %q", got)
		}
		span := storage.ParseInt(scalar(t, path, `SELECT ends_game_minute-starts_game_minute FROM active_effects WHERE user_id=42 AND effect_key='abode_ward_focus'`))
		if want := abodeFocusRealMinutes * scale; span != want {
			t.Fatalf("at scale %d a focus lasts %d game minutes; four real hours is %d", scale, span, want)
		}
		if got := storage.ParseInt(result["duration_real_minutes"]); got != 240 {
			t.Fatalf("the result says %d real minutes", got)
		}
		if _, err := focusRoom(t, path, world, "merchant"); err == nil || !strings.Contains(err.Error(), "home focus cooldown remaining") {
			t.Fatalf("a second room was focused inside the wait: %v", err)
		}
		wait := storage.ParseInt(scalar(t, path, `SELECT CAST(available_at AS INTEGER) FROM cooldowns WHERE user_id=42 AND action='abode_focus'`)) - int64(nowSeconds())
		if wait < 299*60 || wait > 300*60 {
			t.Fatalf("the next focus opens in %d seconds; four hours of focus and one more is 18000", wait)
		}
		conn, err = storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		mustExec(t, conn, `DELETE FROM cooldowns WHERE user_id=42`)
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		conn.Close()
		if result, err := focusRoom(t, path, world, "merchant"); err != nil || fmt.Sprint(result["effect_id"]) != "abode_merchant_focus" {
			t.Fatalf("after the wait the merchant hall did not focus: %v %#v", err, result)
		}
	}
}
