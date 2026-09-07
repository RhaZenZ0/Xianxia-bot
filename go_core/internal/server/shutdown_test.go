package server

// The v0.22.4 regression tests for the review's finding #9.
//
// The property under test is the one the old code broke: a request that has
// already done its work must be allowed to finish writing its response, even
// though a shutdown has begun. The failure mode was not a crash - it was a
// caller who cannot tell whether the mutation it asked for happened.

import (
	"io"
	"net"
	"net/http"
	"os"
	"sync"
	"testing"
	"time"
)

// listeningServer starts a real HTTP server on a loopback port with the given
// handler. httptest.Server has its own close semantics, so this uses the plain
// http.Server that main.go actually runs.
func listeningServer(t *testing.T, handler http.Handler) (*http.Server, string) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = srv.Serve(listener) }()
	return srv, "http://" + listener.Addr().String()
}

func TestDrainLetsAnInFlightRequestFinishItsResponse(t *testing.T) {
	// The handler stands in for a mutation that has committed and is about to
	// write its answer. Severing the connection here is exactly the bug.
	started := make(chan struct{})
	srv, url := listeningServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		time.Sleep(150 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"committed":true}`))
	}))

	var body string
	var status int
	var requestErr error
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		response, err := http.Get(url + "/mutate")
		if err != nil {
			requestErr = err
			return
		}
		defer response.Body.Close()
		status = response.StatusCode
		raw, err := io.ReadAll(response.Body)
		requestErr = err
		body = string(raw)
	}()

	<-started
	drainErr := Drain(srv, 5*time.Second)
	wg.Wait()

	if drainErr != nil {
		t.Fatalf("drain reported %v; the request had time to finish", drainErr)
	}
	if requestErr != nil {
		t.Fatalf("the in-flight request was cut off: %v", requestErr)
	}
	if status != http.StatusOK || body != `{"committed":true}` {
		t.Fatalf("status=%d body=%q; the response did not survive the shutdown", status, body)
	}
}

func TestDrainStopsAcceptingNewRequests(t *testing.T) {
	// Draining is not the same as staying open. Once it has begun, a fresh
	// request must be refused rather than admitted into a dying process.
	srv, url := listeningServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	if err := Drain(srv, time.Second); err != nil {
		t.Fatal(err)
	}
	if response, err := http.Get(url + "/anything"); err == nil {
		response.Body.Close()
		t.Fatal("the server accepted a request after draining")
	}
}

func TestDrainGivesUpAfterTheGraceAndSaysSo(t *testing.T) {
	// A shutdown that waits forever is not a shutdown. The grace is bounded,
	// and exceeding it is reported rather than swallowed - it is the one path
	// that can still cut a response, so the operator should see it in the log.
	release := make(chan struct{})
	started := make(chan struct{})
	srv, url := listeningServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		<-release
		w.WriteHeader(http.StatusOK)
	}))
	go func() {
		response, err := http.Get(url + "/slow")
		if err == nil {
			response.Body.Close()
		}
	}()
	<-started

	begun := time.Now()
	err := Drain(srv, 100*time.Millisecond)
	elapsed := time.Since(begun)
	close(release)

	if err == nil {
		t.Fatal("drain claimed a clean finish while a handler was still running")
	}
	if elapsed > 2*time.Second {
		t.Fatalf("drain waited %s despite a 100ms grace", elapsed)
	}
}

func TestTheShutdownGraceIsConfigurableAndFailsSafe(t *testing.T) {
	t.Setenv("ENGINE_SHUTDOWN_GRACE_SECONDS", "")
	if got := ShutdownGraceFromEnv(); got != DefaultShutdownGrace {
		t.Fatalf("unset: %s", got)
	}
	t.Setenv("ENGINE_SHUTDOWN_GRACE_SECONDS", "5")
	if got := ShutdownGraceFromEnv(); got != 5*time.Second {
		t.Fatalf("set: %s", got)
	}
	// A typo in a deployment variable must not turn into an instant kill.
	for _, bad := range []string{"twenty", "-3", "5s"} {
		t.Setenv("ENGINE_SHUTDOWN_GRACE_SECONDS", bad)
		if got := ShutdownGraceFromEnv(); got != DefaultShutdownGrace {
			t.Fatalf("%q gave %s, want the default", bad, got)
		}
	}
	_ = os.Unsetenv("ENGINE_SHUTDOWN_GRACE_SECONDS")
}

func TestTheEntrypointDrainsBeforeClosingStorage(t *testing.T) {
	// Ordering that only main.go can get wrong, and that no runtime test can
	// see: http.Server.Shutdown makes ListenAndServe return as soon as it is
	// *called*, so treating that return as "everything has stopped" closes the
	// database while handlers are still using it. Read in source, because the
	// alternative is no check at all.
	main, err := os.ReadFile("../../cmd/xianxia-core/main.go")
	if err != nil {
		t.Fatal(err)
	}
	source := string(main)
	if !contains(source, "server.Drain(httpServer, grace)") {
		t.Fatal("main.go does not drain through the tested helper")
	}
	if contains(source, "defer engine.Close()") {
		t.Fatal("storage is closed by a defer, which runs before the drain has finished")
	}
	drainWait := indexOf(source, "<-drained")
	closeStorage := indexOf(source, "engine.Close()\n\tlog.Print")
	if drainWait < 0 || closeStorage < 0 || drainWait > closeStorage {
		t.Fatal("main.go closes storage without waiting for the drain to finish")
	}
	if contains(source, "_ = httpServer.Close()") {
		t.Fatal("main.go still severs connections directly; Drain owns that fallback")
	}
}

func contains(haystack, needle string) bool { return indexOf(haystack, needle) >= 0 }

func indexOf(haystack, needle string) int {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}
