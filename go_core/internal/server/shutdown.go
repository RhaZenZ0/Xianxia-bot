package server

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

// Graceful shutdown (v0.22.4, review finding #9).
//
// The engine used to answer a signal with http.Server.Close(), which severs
// every open connection at once - including one whose transaction has already
// committed but whose response has not been written yet. The caller is then
// left unable to tell "it did not happen" from "it happened and I did not
// hear", which for a mutation is the worst of the two ambiguities.
//
// Since v0.22.2 that is recoverable for authoritative actions: retrying with
// the same action_id replays the original result rather than failing. Legacy
// mutation paths have no receipt, so it is still better not to create the
// ambiguity at all. And draining matters more since v0.22.3 - a restore holds
// the maintenance barrier for its whole duration, and cutting one halfway is
// precisely what nobody wants.

// DefaultShutdownGrace is how long Drain waits for in-flight requests. Keep it
// comfortably under the orchestrator's own kill timeout or the grace period is
// a fiction; docker-compose.yml sets stop_grace_period to 30s for the engine.
const DefaultShutdownGrace = 20 * time.Second

// ShutdownGraceFromEnv reads ENGINE_SHUTDOWN_GRACE_SECONDS, falling back to the
// default on anything unparseable rather than shutting down instantly - a
// typo in a deployment variable should not cost a committed response.
func ShutdownGraceFromEnv() time.Duration {
	raw := os.Getenv("ENGINE_SHUTDOWN_GRACE_SECONDS")
	if raw == "" {
		return DefaultShutdownGrace
	}
	seconds, err := strconv.Atoi(raw)
	if err != nil || seconds < 0 {
		log.Printf("ENGINE_SHUTDOWN_GRACE_SECONDS=%q is not a whole number of seconds; using %s", raw, DefaultShutdownGrace)
		return DefaultShutdownGrace
	}
	return time.Duration(seconds) * time.Second
}

// Drain stops the server accepting new connections and waits for the requests
// already running, up to `grace`. It returns nil when everything finished on
// its own, and an error when the grace expired with work still in flight - in
// which case the remaining connections are closed, because a shutdown that
// waits forever is not a shutdown.
//
// Callers must not close storage until this returns: http.Server.Shutdown
// makes ListenAndServe return as soon as it is *called*, so a caller that
// treats that as "done" pulls the database out from under handlers that are
// still using it.
func Drain(srv *http.Server, grace time.Duration) error {
	ctx, cancel := context.WithTimeout(context.Background(), grace)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		if closeErr := srv.Close(); closeErr != nil && !errors.Is(closeErr, http.ErrServerClosed) {
			log.Printf("closing remaining connections after an expired shutdown grace: %v", closeErr)
		}
		return err
	}
	return nil
}
