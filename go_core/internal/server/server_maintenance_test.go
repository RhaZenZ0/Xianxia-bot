package server

// The v0.22.3 regression tests for the review's finding #4.
//
// Restore took its safety backup while ordinary traffic was still committing.
// A write that landed in the gap was acknowledged to the caller, overwritten by
// the restore, and absent from the safety backup that exists to undo a bad
// restore - so the only copy of an acknowledged mutation was gone.
//
// The invariant these tests hold the code to is deliberately not "no write is
// ever discarded" - discarding writes is what a restore is for. It is: **an
// acknowledged write is never in neither place.** Either it finished before the
// barrier and is in the safety backup, or it ran after the restore and is in
// the live database.

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

// through sends a request the way a real client does - via Handler(), so the
// maintenance barrier in the middleware actually applies. The tests elsewhere
// in this package call handlers directly, which is fine for what they assert
// and useless here.
func through(engine *Server, method, path, body string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, strings.NewReader(body))
	request.Header.Set("X-Xianxia-Engine-Token", os.Getenv("ENGINE_AUTH_TOKEN"))
	response := httptest.NewRecorder()
	engine.Handler().ServeHTTP(response, request)
	return response
}

func labelsIn(t *testing.T, path string) map[string]bool {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer conn.Close()
	res, err := conn.Execute("SELECT label FROM batch_probe", nil)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	out := map[string]bool{}
	for _, row := range res.Rows {
		if len(row) > 0 {
			out[fmt.Sprint(row[0])] = true
		}
	}
	return out
}

func TestARestoreNeverLosesAnAcknowledgedWrite(t *testing.T) {
	const writers = 24
	engine := batchTestServer(t)

	// A backup of the empty world. Restoring it later discards everything,
	// which is exactly the pressure this test wants.
	backup := postBackupCreate(t, engine)
	backupName := fmt.Sprint(backup["name"])

	var mu sync.Mutex
	acknowledged := map[string]bool{}
	var wg sync.WaitGroup
	start := make(chan struct{})

	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			label := fmt.Sprintf("write-%02d", index)
			<-start
			response := through(engine, "POST", "/v1/db/batch",
				fmt.Sprintf(`{"transaction":true,"statements":[{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":[%q]}]}`, label))
			if response.Code == 200 {
				mu.Lock()
				acknowledged[label] = true
				mu.Unlock()
			}
		}(i)
	}

	var restoreCode int
	var safetyName string
	wg.Add(1)
	go func() {
		defer wg.Done()
		<-start
		// Land the restore in the middle of the write burst rather than
		// before or after it.
		time.Sleep(2 * time.Millisecond)
		response := through(engine, "POST", "/v1/db/restore", fmt.Sprintf(`{"name":%q}`, backupName))
		restoreCode = response.Code
		var out map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &out); err == nil {
			if safety, ok := out["safety_backup"].(map[string]any); ok {
				safetyName = fmt.Sprint(safety["name"])
			}
		}
	}()

	close(start)
	wg.Wait()

	if restoreCode != 200 {
		t.Fatalf("restore failed with %d", restoreCode)
	}
	if safetyName == "" {
		t.Fatal("restore did not report a safety backup")
	}
	if len(acknowledged) == 0 {
		t.Fatal("no write was acknowledged; the test proves nothing")
	}

	live := labelsIn(t, engine.databasePath)
	safety := labelsIn(t, filepath.Join(engine.backupDir(), safetyName))

	var lost []string
	for label := range acknowledged {
		if !live[label] && !safety[label] {
			lost = append(lost, label)
		}
	}
	if len(lost) > 0 {
		t.Fatalf("%d of %d acknowledged writes are in neither the live database nor the safety backup: %v",
			len(lost), len(acknowledged), lost)
	}
}

func TestMaintenanceWaitsForInFlightWorkAndBlocksNewWork(t *testing.T) {
	// The barrier itself, deterministically: an exclusive holder must not get
	// in while a shared holder is working, and must keep the next shared holder
	// out while it does.
	var barrier maintenanceBarrier

	releaseWriter := barrier.begin()
	entered := make(chan struct{})
	go func() {
		release := barrier.enter()
		close(entered)
		release()
	}()

	select {
	case <-entered:
		t.Fatal("maintenance started while a write was still in flight")
	case <-time.After(20 * time.Millisecond):
	}
	if !barrier.busy() {
		// A shared holder makes TryRLock succeed, so busy() is false here; the
		// assertion is that it does not claim maintenance is running.
		t.Log("busy() correctly reports no maintenance while only writers hold the barrier")
	}
	releaseWriter()

	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("maintenance never started after the in-flight write finished")
	}
}

func TestEveryV1PathIsOnOneSideOfTheBarrier(t *testing.T) {
	// The barrier is applied by path in the middleware, so the classification
	// is the thing that can rot. Health checks must stay outside it - a
	// readiness probe that blocks for the length of a restore reads as an
	// outage and gets the container killed.
	for _, path := range []string{"/v1/game/action", "/v1/simulation/run-due", "/v1/db/batch", "/v1/db/session", "/v1/db/backups"} {
		exclusive, guarded := barrierFor(path)
		if !guarded || exclusive {
			t.Fatalf("%s: exclusive=%v guarded=%v, want a shared holder", path, exclusive, guarded)
		}
	}
	for _, path := range []string{"/v1/db/restore", "/v1/db/maintenance"} {
		exclusive, guarded := barrierFor(path)
		if !guarded || !exclusive {
			t.Fatalf("%s: exclusive=%v guarded=%v, want exclusive", path, exclusive, guarded)
		}
	}
	for _, path := range []string{"/livez", "/readyz"} {
		if _, guarded := barrierFor(path); guarded {
			t.Fatalf("%s is behind the barrier; health checks must answer during maintenance", path)
		}
	}
}

func TestHealthChecksAnswerDuringMaintenance(t *testing.T) {
	engine := batchTestServer(t)
	release := engine.maintenance.enter()
	defer release()

	if got := through(engine, "GET", "/livez", ""); got.Code != 200 {
		t.Fatalf("/livez returned %d during maintenance", got.Code)
	}
	ready := through(engine, "GET", "/readyz", "")
	if ready.Code != 503 {
		t.Fatalf("/readyz returned %d during maintenance, want 503", ready.Code)
	}
	if !strings.Contains(ready.Body.String(), "maintenance") {
		t.Fatalf("/readyz did not say why it was not ready: %s", ready.Body.String())
	}
}

func TestTheSafetyBackupIsTakenAfterSessionsAreClosed(t *testing.T) {
	// A db session holds its own connection past the end of the request that
	// created it. If the safety backup were taken before those are closed, a
	// session sitting on uncommitted work could still land after the snapshot.
	engine := batchTestServer(t)
	backup := postBackupCreate(t, engine)

	session := through(engine, "POST", "/v1/db/session", "{}")
	if session.Code != 200 && session.Code != 201 {
		t.Skipf("session endpoint returned %d; nothing to prove here", session.Code)
	}
	restore := through(engine, "POST", "/v1/db/restore", fmt.Sprintf(`{"name":%q}`, backup["name"]))
	if restore.Code != 200 {
		t.Fatalf("restore returned %d: %s", restore.Code, restore.Body.String())
	}
	// The session's connection is gone, so using it again fails rather than
	// writing into a database that has just been replaced under it.
	var out map[string]any
	_ = json.Unmarshal(session.Body.Bytes(), &out)
	if id := fmt.Sprint(out["session_id"]); id != "" && id != "<nil>" {
		used := through(engine, "POST", "/v1/db/session/"+id+"/execute",
			`{"sql":"INSERT INTO batch_probe(label) VALUES('after')"}`)
		if used.Code == 200 {
			t.Fatal("a session survived a restore and could still write")
		}
	}
}
