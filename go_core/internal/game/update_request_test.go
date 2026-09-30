package game

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

func storedUpdateRow(t *testing.T, path, key string) map[string]any {
	t.Helper()
	raw := scalar(t, path, `SELECT value_json FROM world_state WHERE key=?`, key)
	if raw == nil {
		return nil
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(stringOf(raw))), &out); err != nil {
		t.Fatalf("unreadable %s blob %v: %v", key, raw, err)
	}
	return out
}

func requestAnUpdate(t *testing.T, path string) string {
	t.Helper()
	result := applyAdminAs(t, path, "admin.server.request_update", 7, map[string]any{
		"channel": "stable", "reason": "the dashboard asked",
	}).(map[string]any)
	nonce, _ := result["nonce"].(string)
	if nonce == "" {
		t.Fatalf("a request carries no nonce: %#v", result)
	}
	return nonce
}

func TestARequestIsWrittenAndAudited(t *testing.T) {
	path := setupAdminDB(t)
	nonce := requestAnUpdate(t, path)
	row := storedUpdateRow(t, path, "update_request")
	if row["status"] != "requested" || row["nonce"] != nonce || row["channel"] != "stable" || row["requested_by"] != float64(7) {
		t.Fatalf("stored request=%#v", row)
	}
	if got := storage.ParseInt(scalar(t, path,
		`SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.server.request_update' AND admin_user_id=7`)); got != 1 {
		t.Fatalf("audit rows=%d, want 1", got)
	}
}

func TestASecondRequestIsRefusedWhileOneIsOpen(t *testing.T) {
	path := setupAdminDB(t)
	requestAnUpdate(t, path)
	if _, err := applyAdminRaw(t, path, "admin.server.request_update", 7, map[string]any{"channel": "stable"}); err == nil {
		t.Fatal("a second request was accepted while the first was still open; the watcher would run it twice")
	}
}

func TestAReportMustNameTheOpenRequest(t *testing.T) {
	path := setupAdminDB(t)
	nonce := requestAnUpdate(t, path)
	if _, err := applyAdminRaw(t, path, "admin.server.update_status", 0, map[string]any{
		"nonce": "not-the-one", "status": "acked",
	}); err == nil {
		t.Fatal("a report with the wrong nonce was accepted")
	}
	if _, err := applyAdminRaw(t, path, "admin.server.update_status", 0, map[string]any{
		"nonce": nonce, "status": "acked",
	}); err != nil {
		t.Fatalf("the right nonce was refused: %v", err)
	}
	if row := storedUpdateRow(t, path, "update_request"); row["status"] != "acked" {
		t.Fatalf("the report did not land: %#v", row)
	}
}

func TestATerminalReportWritesTheResultAndClosesTheRequest(t *testing.T) {
	path := setupAdminDB(t)
	nonce := requestAnUpdate(t, path)
	applyAdmin(t, path, "admin.server.update_status", map[string]any{"nonce": nonce, "status": "fetching"})
	applyAdmin(t, path, "admin.server.update_status", map[string]any{
		"nonce": nonce, "status": "done", "installed_version": "1.4.0", "detail": "installed",
	})
	result := storedUpdateRow(t, path, "update_result")
	if result["status"] != "done" || result["installed_version"] != "1.4.0" || result["requested_by"] != float64(7) {
		t.Fatalf("result=%#v", result)
	}
	// Closed: a late or repeated report is refused, so a watcher that
	// restarted cannot re-report against a request already settled.
	if _, err := applyAdminRaw(t, path, "admin.server.update_status", 0, map[string]any{
		"nonce": nonce, "status": "failed", "detail": "a stale watcher",
	}); err == nil {
		t.Fatal("a report against a closed request was accepted")
	}
	if got := storedUpdateRow(t, path, "update_result"); got["status"] != "done" {
		t.Fatalf("the stale report overwrote the result: %#v", got)
	}
	// And the next request can be made, leaving the last result standing.
	requestAnUpdate(t, path)
	if got := storedUpdateRow(t, path, "update_result"); got["status"] != "done" {
		t.Fatalf("a new request erased the last outcome: %#v", got)
	}
	if got := storage.ParseInt(scalar(t, path,
		`SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.server.update_status' AND admin_user_id=0`)); got != 2 {
		t.Fatalf("watcher audit rows=%d, want 2 (fetching, done)", got)
	}
}

func TestAHeartbeatIsNotAReportAndIsNotAudited(t *testing.T) {
	path := setupAdminDB(t)
	// No request open at all: a heartbeat still lands, because it is about
	// the watcher and not about any update.
	applyAdmin(t, path, "admin.server.update_status", map[string]any{"status": "heartbeat"})
	if row := storedUpdateRow(t, path, "update_watcher_heartbeat"); row == nil || row["at"] == nil {
		t.Fatalf("heartbeat=%#v", row)
	}
	if got := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM admin_audit_log`)); got != 0 {
		t.Fatalf("a heartbeat wrote %d audit row(s)", got)
	}
}

func TestTheReadAnswersAllThreeRowsAndABrokenBlobIsNoRequest(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES('update_request','not json',0)`, nil,
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	read := applyAdmin(t, path, "admin.server.update_request", map[string]any{}).(map[string]any)
	if read["request"] != nil {
		t.Fatalf("an unreadable blob must read as no request, got %#v", read["request"])
	}
	// And a broken blob does not block a fresh request.
	nonce := requestAnUpdate(t, path)
	read = applyAdmin(t, path, "admin.server.update_request", map[string]any{}).(map[string]any)
	request := read["request"].(map[string]any)
	if request["nonce"] != nonce {
		t.Fatalf("the read does not carry the request: %#v", read)
	}
	if got := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.server.update_request'`)); got != 0 {
		t.Fatalf("the read wrote %d audit row(s)", got)
	}
}

// -- v1.12.3: an open request can be closed, and the read answers flat values --

func heartbeatAt(t *testing.T, path string, secondsAgo float64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	now := float64(time.Now().UnixNano()) / 1e9
	if err := writeWorldStateTx(conn, updateHeartbeatKey, map[string]any{"at": now - secondsAgo}, now); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestARequestNobodyPickedUpCanBeCancelledAndAnotherAsked(t *testing.T) {
	path := setupAdminDB(t)
	nonce := requestAnUpdate(t, path)
	out := applyAdminAs(t, path, "admin.server.cancel_update", 7, map[string]any{"reason": "asked by mistake"}).(map[string]any)
	if out["status"] != "cancelled" || out["previous_status"] != "requested" || out["nonce"] != nonce {
		t.Fatalf("cancel answered %#v", out)
	}
	row := storedUpdateRow(t, path, "update_request")
	if row["status"] != "cancelled" || row["detail"] != "asked by mistake" {
		t.Fatalf("stored request=%#v", row)
	}
	if got := storage.ParseInt(scalar(t, path,
		`SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.server.cancel_update' AND admin_user_id=7`)); got != 1 {
		t.Fatalf("cancel audit rows=%d, want 1", got)
	}
	// Terminal: the Request button returns, and a late report from a watcher
	// that still held the old nonce is refused.
	if _, err := applyAdminRaw(t, path, "admin.server.update_status", 0, map[string]any{"nonce": nonce, "status": "acked"}); err == nil {
		t.Fatal("a report was accepted against a cancelled request")
	}
	if nonce2 := requestAnUpdate(t, path); nonce2 == nonce {
		t.Fatal("a fresh request reused the cancelled nonce")
	}
}

func TestNothingOpenIsNothingToCancel(t *testing.T) {
	path := setupAdminDB(t)
	if _, err := applyAdminRaw(t, path, "admin.server.cancel_update", 7, map[string]any{}); err == nil {
		t.Fatal("a cancel with no request was accepted")
	}
	nonce := requestAnUpdate(t, path)
	applyAdmin(t, path, "admin.server.update_status", map[string]any{"nonce": nonce, "status": "done"})
	if _, err := applyAdminRaw(t, path, "admin.server.cancel_update", 7, map[string]any{}); err == nil {
		t.Fatal("a finished request was cancellable")
	}
	if got := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.server.cancel_update'`)); got != 0 {
		t.Fatalf("a refused cancel wrote %d audit row(s)", got)
	}
}

func TestAnInstallInProgressIsNotCancellableWhileTheWatcherLives(t *testing.T) {
	path := setupAdminDB(t)
	nonce := requestAnUpdate(t, path)
	applyAdmin(t, path, "admin.server.update_status", map[string]any{"nonce": nonce, "status": "fetching"})
	heartbeatAt(t, path, 30)
	if _, err := applyAdminRaw(t, path, "admin.server.cancel_update", 7, map[string]any{}); err == nil {
		t.Fatal("an install the watcher is running was cancelled under it")
	}
	if row := storedUpdateRow(t, path, "update_request"); row["status"] != "fetching" {
		t.Fatalf("the refused cancel still changed the request: %#v", row)
	}
	// Just inside the window it is still running; just past it, it is not.
	heartbeatAt(t, path, updateWatcherStaleSeconds-5)
	if _, err := applyAdminRaw(t, path, "admin.server.cancel_update", 7, map[string]any{}); err == nil {
		t.Fatal("a watcher heard from inside the window was treated as gone")
	}
	heartbeatAt(t, path, updateWatcherStaleSeconds+5)
	out := applyAdminAs(t, path, "admin.server.cancel_update", 7, map[string]any{}).(map[string]any)
	if out["previous_status"] != "fetching" {
		t.Fatalf("cancel answered %#v", out)
	}
	if row := storedUpdateRow(t, path, "update_request"); row["detail"] != "cancelled by a GM" {
		t.Fatalf("a cancel with no reason stored %#v", row)
	}
}

func TestARequestPastRequestedWithNoHeartbeatAtAllIsCancellable(t *testing.T) {
	path := setupAdminDB(t)
	nonce := requestAnUpdate(t, path)
	applyAdmin(t, path, "admin.server.update_status", map[string]any{"nonce": nonce, "status": "acked"})
	// No heartbeat row: nobody has ever been heard from, which is "not
	// running", never a watcher of age zero.
	applyAdminAs(t, path, "admin.server.cancel_update", 7, map[string]any{})
	if row := storedUpdateRow(t, path, "update_request"); row["status"] != "cancelled" {
		t.Fatalf("stored request=%#v", row)
	}
}

func TestTheReadCarriesFlatValuesSoTheWatcherNeverCutsJSON(t *testing.T) {
	path := setupAdminDB(t)
	read := applyAdmin(t, path, "admin.server.update_request", map[string]any{}).(map[string]any)
	for _, key := range []string{"request_nonce", "request_status", "request_channel"} {
		if v, ok := read[key]; !ok || v != "" {
			t.Fatalf("with no request %s must be the empty string, got %#v (present=%v)", key, v, ok)
		}
	}
	if read["maintenance_enabled"] != false {
		t.Fatalf("maintenance_enabled=%#v", read["maintenance_enabled"])
	}
	result := applyAdminAs(t, path, "admin.server.request_update", 7, map[string]any{
		"channel": "beta", "reason": `fix {bug} and "quote"`,
	}).(map[string]any)
	read = applyAdmin(t, path, "admin.server.update_request", map[string]any{}).(map[string]any)
	if read["request_nonce"] != result["nonce"] || read["request_status"] != "requested" || read["request_channel"] != "beta" {
		t.Fatalf("flat values do not match the request: %#v", read)
	}
	applyAdmin(t, path, "admin.server.maintenance_mode", map[string]any{"enabled": true, "reason": "GM closed it"})
	read = applyAdmin(t, path, "admin.server.update_request", map[string]any{}).(map[string]any)
	if read["maintenance_enabled"] != true {
		t.Fatalf("a closed world must read as closed: %#v", read["maintenance_enabled"])
	}
}
