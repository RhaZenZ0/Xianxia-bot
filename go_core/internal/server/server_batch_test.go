package server

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

// batchTestServer builds a Server backed by a throwaway on-disk database with a
// single table, which is all these tests need to prove durability.
func batchTestServer(t *testing.T) *Server {
	t.Helper()
	databasePath := filepath.Join(t.TempDir(), "batch.sqlite3")
	engine, err := New(databasePath, "")
	if err != nil {
		t.Fatalf("could not create engine: %v", err)
	}
	conn, err := storage.Open(databasePath)
	if err != nil {
		t.Fatalf("could not open database: %v", err)
	}
	if err := conn.ExecScript("CREATE TABLE IF NOT EXISTS batch_probe(id INTEGER PRIMARY KEY, label TEXT);"); err != nil {
		conn.Close()
		t.Fatalf("could not create probe table: %v", err)
	}
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatalf("could not commit schema: %v", err)
	}
	conn.Close()
	return engine
}

// countProbeRows reopens the database from scratch, which is the whole point:
// a write that was rolled back on connection close is invisible to a fresh
// connection even though the HTTP call reported success.
func countProbeRows(t *testing.T, engine *Server) int {
	t.Helper()
	conn, err := storage.Open(engine.databasePath)
	if err != nil {
		t.Fatalf("could not reopen database: %v", err)
	}
	defer conn.Close()
	result, err := conn.Execute("SELECT COUNT(*) FROM batch_probe", nil)
	if err != nil {
		t.Fatalf("could not count rows: %v", err)
	}
	if len(result.Rows) == 0 || len(result.Rows[0]) == 0 {
		t.Fatal("count query returned no rows")
	}
	return int(storage.ParseInt(result.Rows[0][0]))
}

func postBatch(t *testing.T, engine *Server, body string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest("POST", "/v1/db/batch", strings.NewReader(body))
	response := httptest.NewRecorder()
	engine.dbBatch(response, request)
	return response
}

// Regression: storage.Conn opens an implicit BEGIN before any INSERT and
// Conn.Close rolls back whatever is still open, so dbBatch used to answer 200
// and then throw non-transactional writes away.
func TestBatchWithoutTransactionCommitsWrites(t *testing.T) {
	engine := batchTestServer(t)
	response := postBatch(t, engine, `{"transaction":false,"statements":[
		{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["first"]},
		{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["second"]}
	]}`)
	if response.Code != 200 {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	if got := countProbeRows(t, engine); got != 2 {
		t.Fatalf("non-transactional batch reported success but persisted %d of 2 rows", got)
	}
}

// The HTTP field defaults to Go's zero value, so a client that simply omits
// "transaction" takes the same path and must not lose data either.
func TestBatchWithOmittedTransactionFlagCommitsWrites(t *testing.T) {
	engine := batchTestServer(t)
	response := postBatch(t, engine, `{"statements":[
		{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["only"]}
	]}`)
	if response.Code != 200 {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	if got := countProbeRows(t, engine); got != 1 {
		t.Fatalf("batch with no transaction flag persisted %d of 1 rows", got)
	}
}

// transaction:false means each statement stands alone, so the statements that
// already succeeded must survive a later failure and "completed" must be true.
func TestBatchWithoutTransactionKeepsCompletedWritesAfterFailure(t *testing.T) {
	engine := batchTestServer(t)
	response := postBatch(t, engine, `{"transaction":false,"statements":[
		{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["kept"]},
		{"sql":"INSERT INTO no_such_table(label) VALUES(?)","params":["boom"]}
	]}`)
	if response.Code != 400 {
		t.Fatalf("expected 400 for the failing statement, got %d: %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatalf("could not decode error body: %v", err)
	}
	if completed, _ := payload["completed"].(float64); int(completed) != 1 {
		t.Fatalf("expected completed=1, got %v", payload["completed"])
	}
	if got := countProbeRows(t, engine); got != 1 {
		t.Fatalf("reported completed=1 but persisted %d rows", got)
	}
}

// transaction:true keeps all-or-nothing semantics.
func TestBatchWithTransactionRollsBackEverythingOnFailure(t *testing.T) {
	engine := batchTestServer(t)
	response := postBatch(t, engine, `{"transaction":true,"statements":[
		{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["discarded"]},
		{"sql":"INSERT INTO no_such_table(label) VALUES(?)","params":["boom"]}
	]}`)
	if response.Code != 400 {
		t.Fatalf("expected 400, got %d: %s", response.Code, response.Body.String())
	}
	if got := countProbeRows(t, engine); got != 0 {
		t.Fatalf("transactional batch should have rolled back, but %d rows survived", got)
	}
}

func TestBatchWithTransactionCommitsWrites(t *testing.T) {
	engine := batchTestServer(t)
	response := postBatch(t, engine, `{"transaction":true,"statements":[
		{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["a"]},
		{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["b"]}
	]}`)
	if response.Code != 200 {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	if got := countProbeRows(t, engine); got != 2 {
		t.Fatalf("transactional batch persisted %d of 2 rows", got)
	}
}

// A read-only batch never opens an implicit transaction, so the unconditional
// commit must stay a no-op rather than erroring.
func TestReadOnlyBatchStillSucceeds(t *testing.T) {
	engine := batchTestServer(t)
	response := postBatch(t, engine, `{"transaction":false,"statements":[
		{"sql":"SELECT COUNT(*) FROM batch_probe","params":[]}
	]}`)
	if response.Code != 200 {
		t.Fatalf("expected 200 for a read-only batch, got %d: %s", response.Code, response.Body.String())
	}
}

// Regression: backup filenames used second resolution, so two backups taken in
// the same second resolved to one filename and the second overwrote the first.
func TestReserveBackupPathNeverCollidesWithinOneSecond(t *testing.T) {
	dir := t.TempDir()
	fixed := time.Date(2026, 9, 1, 12, 0, 0, 0, time.UTC)
	seen := map[string]bool{}
	for i := 0; i < 5; i++ {
		// The same instant every time is the worst case the old name could not
		// survive; millisecond resolution alone would not save it here.
		path, err := reserveBackupPath(dir, fixed)
		if err != nil {
			t.Fatalf("reserveBackupPath failed on attempt %d: %v", i, err)
		}
		if seen[path] {
			t.Fatalf("reserveBackupPath handed out %q twice", filepath.Base(path))
		}
		seen[path] = true
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("could not read backup dir: %v", err)
	}
	if len(entries) != 5 {
		t.Fatalf("expected 5 reserved backup files, found %d", len(entries))
	}
}

// Distinct instants keep their timestamp in the name and stay sortable by age.
func TestReserveBackupPathKeepsTimestampsSortable(t *testing.T) {
	dir := t.TempDir()
	base := time.Date(2026, 9, 1, 12, 0, 0, 0, time.UTC)
	first, err := reserveBackupPath(dir, base)
	if err != nil {
		t.Fatalf("first reserve failed: %v", err)
	}
	second, err := reserveBackupPath(dir, base.Add(1500*time.Millisecond))
	if err != nil {
		t.Fatalf("second reserve failed: %v", err)
	}
	if filepath.Base(first) >= filepath.Base(second) {
		t.Fatalf("backup names should sort by age: %q !< %q", filepath.Base(first), filepath.Base(second))
	}
}
