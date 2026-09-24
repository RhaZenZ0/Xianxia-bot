package server

// VACUUM and an open session (v1.2.3).
//
// A Python write is two requests: an execute, which opens an implicit BEGIN on
// the session's connection, and a commit. Between them nothing is in flight,
// so the barrier - which only drains in-flight requests - lets VACUUM through
// while that connection still holds SQLite's write lock. VACUUM then waited
// out the whole busy_timeout (ten seconds) and failed, and the session's
// commit sat behind the barrier for all of it. Restore closes every session
// first, because a restore replaces the world; VACUUM must not, because
// rolling back a half-written action to compact a file is worse than not
// compacting it. So it refuses at once, naming the reason, and the commit goes
// through.

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"
)

func openSession(t *testing.T, engine *Server) string {
	t.Helper()
	response := through(engine, "POST", "/v1/db/session", "{}")
	if response.Code != 201 {
		t.Fatalf("open session: %d %s", response.Code, response.Body.String())
	}
	var out map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &out); err != nil {
		t.Fatalf("open session: bad JSON: %v", err)
	}
	return fmt.Sprint(out["session_id"])
}

func TestVacuumDoesNotStallBehindAnOpenSessionTransaction(t *testing.T) {
	engine := batchTestServer(t)
	session := openSession(t, engine)

	// The first half of a Python write: the implicit BEGIN is now held by a
	// connection that no request is using.
	response := through(engine, "POST", "/v1/db/session/"+session+"/execute",
		`{"sql":"INSERT INTO batch_probe(label) VALUES('half-written')","params":[]}`)
	if response.Code != 200 {
		t.Fatalf("execute: %d %s", response.Code, response.Body.String())
	}

	started := time.Now()
	response = through(engine, "POST", "/v1/db/maintenance", `{"action":"vacuum"}`)
	took := time.Since(started)
	if response.Code != 409 {
		t.Fatalf("vacuum with a session mid-transaction: want 409, got %d %s", response.Code, response.Body.String())
	}
	var out map[string]any
	_ = json.Unmarshal(response.Body.Bytes(), &out)
	if fmt.Sprint(out["error"]) != "sessions_busy" {
		t.Fatalf("vacuum refused for the wrong reason: %s", response.Body.String())
	}
	if took > 2*time.Second {
		t.Fatalf("vacuum waited %s behind a session's open transaction; it must refuse at once rather than sit out busy_timeout", took)
	}

	// The second half of the write still lands: the session was not closed
	// under the caller.
	response = through(engine, "POST", "/v1/db/session/"+session+"/commit", "{}")
	if response.Code != 200 {
		t.Fatalf("commit after a refused vacuum: %d %s", response.Code, response.Body.String())
	}
	if rows := countProbeRows(t, engine); rows != 1 {
		t.Fatalf("the half-written row was lost to the vacuum: %d rows", rows)
	}

	// An idle session holds no lock and is no reason to refuse.
	response = through(engine, "POST", "/v1/db/maintenance", `{"action":"vacuum"}`)
	if response.Code != 200 {
		t.Fatalf("vacuum beside an idle session: %d %s", response.Code, response.Body.String())
	}
}
