package server

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func postBackupCreate(t *testing.T, engine *Server) map[string]any {
	t.Helper()
	request := httptest.NewRequest("POST", "/v1/db/backups", strings.NewReader("{}"))
	request.Header.Set("X-Xianxia-Engine-Token", os.Getenv("ENGINE_AUTH_TOKEN"))
	response := httptest.NewRecorder()
	engine.dbBackups(response, request)
	if response.Code != 201 {
		t.Fatalf("backup create: expected 201, got %d: %s", response.Code, response.Body.String())
	}
	var out map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &out); err != nil {
		t.Fatalf("backup create: bad JSON: %v", err)
	}
	return out
}

func postRestore(t *testing.T, engine *Server, body string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest("POST", "/v1/db/restore", strings.NewReader(body))
	request.Header.Set("X-Xianxia-Engine-Token", os.Getenv("ENGINE_AUTH_TOKEN"))
	response := httptest.NewRecorder()
	engine.dbRestore(response, request)
	return response
}

// TestDbRestoreRevertsLiveDatabaseToNamedBackup proves the endpoint is a real
// reverse of dbBackups: back up the current state, mutate further, restore
// from the named backup, and confirm the post-backup mutation is gone while
// the pre-backup row survives. It also proves the endpoint's core safety
// property - it takes its own "just in case" backup of the CURRENT (about to
// be overwritten) state before restoring, so the restore itself is
// recoverable.
func TestDbRestoreRevertsLiveDatabaseToNamedBackup(t *testing.T) {
	engine := batchTestServer(t)
	postBatch(t, engine, `{"statements":[{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["keep"]}]}`)
	if got := countProbeRows(t, engine); got != 1 {
		t.Fatalf("pre-backup row count=%d, want 1", got)
	}

	backup := postBackupCreate(t, engine)
	backupName, _ := backup["name"].(string)
	if backupName == "" {
		t.Fatalf("backup create returned no name: %v", backup)
	}

	postBatch(t, engine, `{"statements":[{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["should_vanish"]}]}`)
	if got := countProbeRows(t, engine); got != 2 {
		t.Fatalf("post-mutation row count=%d, want 2", got)
	}

	before, err := os.ReadDir(engine.backupDir())
	if err != nil {
		t.Fatalf("could not list backup dir: %v", err)
	}
	beforeCount := len(before)

	payload, _ := json.Marshal(map[string]any{"name": backupName})
	response := postRestore(t, engine, string(payload))
	if response.Code != 200 {
		t.Fatalf("restore: expected 200, got %d: %s", response.Code, response.Body.String())
	}
	var out map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &out); err != nil {
		t.Fatalf("restore: bad JSON: %v", err)
	}
	if got, _ := out["restored_from"].(string); got != backupName {
		t.Fatalf("restored_from=%q, want %q", got, backupName)
	}
	safety, ok := out["safety_backup"].(map[string]any)
	if !ok || safety["name"] == "" || safety["name"] == nil {
		t.Fatalf("safety_backup missing or empty in response: %v", out)
	}
	// reserveBackupPath always creates a zero-byte placeholder file as part of
	// claiming the name, so a file merely existing there proves nothing - the
	// safety backup must actually have been written to, and must actually
	// contain the pre-restore state (both rows, since the mutation happened
	// before this safety snapshot was taken).
	safetyName, _ := safety["name"].(string)
	safetyConn, err := storage.Open(filepath.Join(engine.backupDir(), safetyName))
	if err != nil {
		t.Fatalf("could not open safety backup %q: %v", safetyName, err)
	}
	result, err := safetyConn.Execute("SELECT COUNT(*) FROM batch_probe", nil)
	safetyConn.Close()
	if err != nil {
		t.Fatalf("could not query safety backup: %v", err)
	}
	if len(result.Rows) == 0 || storage.ParseInt(result.Rows[0][0]) != 2 {
		t.Fatalf("safety backup row count=%v, want 2 (must capture the pre-restore state, not an empty placeholder)", result.Rows)
	}

	if got := countProbeRows(t, engine); got != 1 {
		t.Fatalf("post-restore row count=%d, want 1 (the post-backup insert must be gone)", got)
	}

	after, err := os.ReadDir(engine.backupDir())
	if err != nil {
		t.Fatalf("could not list backup dir after restore: %v", err)
	}
	// Only the automatic safety backup should be a new entry - the named
	// backup used as the restore source already existed. Restoring from it
	// (read-only) must not leave -wal/-shm sidecar files cluttering the
	// backups directory either.
	if len(after) != beforeCount+1 {
		names := make([]string, 0, len(after))
		for _, e := range after {
			names = append(names, e.Name())
		}
		t.Fatalf("backup dir has %d entries after restore, want %d (safety backup must be taken, no sidecar clutter): %v", len(after), beforeCount+1, names)
	}
	for _, e := range after {
		if strings.HasSuffix(e.Name(), "-wal") || strings.HasSuffix(e.Name(), "-shm") {
			t.Fatalf("restore left a sidecar file behind: %s", e.Name())
		}
	}
}

// TestDbRestoreRejectsUnsafeOrUnknownNames proves the endpoint can only ever
// target a file that dbBackups' own listing would have offered: a name
// carrying a path-traversal attempt is collapsed to a bare filename and then
// rejected as not matching the backup naming convention, and a
// well-formed-but-nonexistent name 404s rather than silently no-op'ing.
func TestDbRestoreRejectsUnsafeOrUnknownNames(t *testing.T) {
	engine := batchTestServer(t)

	cases := []struct {
		name       string
		payload    string
		wantStatus int
	}{
		{"path traversal", `{"name":"../../../etc/passwd"}`, 400},
		{"wrong extension", `{"name":"xianxia-not-a-backup.txt"}`, 400},
		{"missing prefix", `{"name":"not-xianxia-20260101-000000.000.sqlite3"}`, 400},
		{"empty name", `{"name":""}`, 400},
		{"well-formed but nonexistent", `{"name":"xianxia-20260101-000000.000.sqlite3"}`, 404},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			response := postRestore(t, engine, tc.payload)
			if response.Code != tc.wantStatus {
				t.Fatalf("%s: expected %d, got %d: %s", tc.name, tc.wantStatus, response.Code, response.Body.String())
			}
		})
	}

	// None of the rejected attempts may have mutated the live database.
	if got := countProbeRows(t, engine); got != 0 {
		t.Fatalf("row count=%d after rejected restores, want 0 (nothing should have touched the live db)", got)
	}
}

// TestDbRestoreClosesOpenSessionsFirst proves a restore forces every
// long-lived db-session closed before it touches the database file - a
// session left open would otherwise hold a connection the backup step has to
// fight for the write lock, or could overwrite the just-restored state with
// stale in-flight data on its next write.
func TestDbRestoreClosesOpenSessionsFirst(t *testing.T) {
	engine := batchTestServer(t)
	backup := postBackupCreate(t, engine)
	backupName, _ := backup["name"].(string)
	if backupName == "" {
		t.Fatalf("backup create returned no name: %v", backup)
	}

	session, err := engine.sessions.Open()
	if err != nil {
		t.Fatalf("could not open session: %v", err)
	}
	if _, err := engine.sessions.Get(session.ID); err != nil {
		t.Fatalf("session should be open before restore: %v", err)
	}

	payload, _ := json.Marshal(map[string]any{"name": backupName})
	response := postRestore(t, engine, string(payload))
	if response.Code != 200 {
		t.Fatalf("restore: expected 200, got %d: %s", response.Code, response.Body.String())
	}

	if _, err := engine.sessions.Get(session.ID); err == nil {
		t.Fatalf("session %s is still open after restore, want closed", session.ID)
	}
}
