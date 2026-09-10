package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

// A mute given a duration stores its expiry, reports it, and audits it - the
// audit row is what admin.audit.undo_last reads, so expiry has to be in it.
func TestAdminSetModerationWithDurationStoresAndAuditsExpiry(t *testing.T) {
	path := setupAdminDB(t)
	before := float64(time.Now().UnixNano()) / 1e9
	result := applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": true, "muted_seconds": 3600, "moderation_reason": "cooling off", "reason": "test"})
	data := result.(map[string]any)
	until := toFloat(data["muted_until"])
	if until < before+3599 || until > before+3601+5 {
		t.Fatalf("muted_until=%v, want about now+3600 (now=%v)", until, before)
	}
	stored := toFloat(scalar(t, path, "SELECT muted_until FROM characters WHERE user_id=42"))
	if stored != until {
		t.Fatalf("stored muted_until=%v, reply said %v", stored, until)
	}
	if got := toFloat(scalar(t, path, "SELECT frozen_until FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("frozen_until=%v, want untouched 0", got)
	}
	afterJSON := fmt.Sprint(scalar(t, path, "SELECT after_json FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1"))
	var after map[string]any
	if err := json.Unmarshal([]byte(afterJSON), &after); err != nil {
		t.Fatalf("after_json is not JSON: %v", err)
	}
	for _, key := range []string{"is_muted", "muted_until", "is_frozen", "frozen_until", "is_banned", "moderation_reason"} {
		if _, ok := after[key]; !ok {
			t.Fatalf("audit after_json lacks %q: %s", key, afterJSON)
		}
	}
	if toFloat(after["muted_until"]) != until {
		t.Fatalf("audited muted_until=%v, want %v", after["muted_until"], until)
	}
	// Lifting the mute clears the expiry with it.
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": false, "reason": "test"})
	if got := toFloat(scalar(t, path, "SELECT muted_until FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("muted_until after unmute=%v, want 0", got)
	}
}

func TestAdminSetModerationRejectsAbsurdDurations(t *testing.T) {
	path := setupAdminDB(t)
	for _, seconds := range []any{-1, 400 * 24 * 3600} {
		raw, _ := json.Marshal(map[string]any{"user_id": 42, "frozen": true, "frozen_seconds": seconds, "reason": "test"})
		if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_moderation", Payload: raw}); err == nil {
			t.Fatalf("frozen_seconds=%v was accepted", seconds)
		}
	}
}

// A ban is its own flag: it does not disturb the mute/freeze pair, and
// lifting it leaves them as they were.
func TestAdminSetModerationBanIsIndependentOfTheOtherFlags(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": true, "reason": "test"})
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "banned": true, "moderation_reason": "gone", "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT is_banned FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("is_banned=%d, want 1", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_muted FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("is_muted=%d, want still 1", got)
	}
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "banned": false, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT is_banned FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("is_banned=%d, want 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_muted FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("is_muted=%d, want still 1 after unban", got)
	}
}

// The tick clears exactly the flags whose expiry has passed: a lapsed mute
// goes, a freeze still running stays, an indefinite one is never touched, and
// a ban has no expiry to lapse.
func TestExpireDueModerationsClearsOnlyLapsedFlags(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`INSERT INTO characters(user_id,name,life_status,is_muted,muted_until,is_frozen,frozen_until,is_banned,moderation_reason) VALUES
		(1,'lapsed mute','alive',1,?,0,0,0,'spam'),
		(2,'running freeze','alive',0,0,1,?,0,'hold'),
		(3,'indefinite mute','alive',1,0,0,0,0,'forever'),
		(4,'lapsed freeze, banned','alive',0,0,1,?,1,'banned'),
		(5,'lapsed mute, running freeze','alive',1,?,1,?,0,'both')`,
		[]any{now - 10, now + 3600, now - 10, now - 10, now + 3600}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	cleared, err := ExpireDueModerations(conn, now)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(cleared) != "[1 4 5]" {
		t.Fatalf("cleared=%v, want [1 4 5]", cleared)
	}
	check := func(uid int64, muted, frozen, banned int64, reason string) {
		t.Helper()
		row := fmt.Sprint(scalar(t, path, fmt.Sprintf("SELECT is_muted||','||is_frozen||','||is_banned||','||moderation_reason||','||muted_until||','||frozen_until FROM characters WHERE user_id=%d", uid)))
		want := fmt.Sprintf("%d,%d,%d,%s,", muted, frozen, banned, reason)
		if !strings.HasPrefix(row, want) {
			t.Fatalf("user %d: got %q, want prefix %q", uid, row, want)
		}
	}
	check(1, 0, 0, 0, "")
	check(2, 0, 1, 0, "hold")
	check(3, 1, 0, 0, "forever")
	check(4, 0, 0, 1, "banned")
	check(5, 0, 1, 0, "both")
	if got := toFloat(scalar(t, path, "SELECT muted_until FROM characters WHERE user_id=5")); got != 0 {
		t.Fatalf("user 5 muted_until=%v, want 0", got)
	}
	if got := toFloat(scalar(t, path, "SELECT frozen_until FROM characters WHERE user_id=5")); got <= now {
		t.Fatalf("user 5 frozen_until=%v, want untouched future", got)
	}
	// Idempotent: a second pass finds nothing.
	again, err := ExpireDueModerations(conn, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(again) != 0 {
		t.Fatalf("second pass cleared %v, want nothing", again)
	}
}

// Undo restores the expiry and the ban flag, not just the two old bits.
func TestAdminUndoLastRestoresModerationExpiryAndBan(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": true, "muted_seconds": 1800, "reason": "first"})
	until := toFloat(scalar(t, path, "SELECT muted_until FROM characters WHERE user_id=42"))
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": false, "banned": true, "reason": "second"})
	undoLast(t, path)
	if got := toFloat(scalar(t, path, "SELECT muted_until FROM characters WHERE user_id=42")); got != until {
		t.Fatalf("muted_until=%v, want restored %v", got, until)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_banned FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("is_banned=%d, want restored 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_muted FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("is_muted=%d, want restored 1", got)
	}
}
