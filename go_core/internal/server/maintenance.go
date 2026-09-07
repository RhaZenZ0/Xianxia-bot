package server

import "sync"

// The maintenance barrier (v0.22.3, review finding #4).
//
// Restore replaces the live database. It was careful about the things that are
// easy to be careful about - path traversal, a safety backup first - and not
// about the one that matters: nothing stopped ordinary traffic from committing
// while it worked. The window was real and it was the worst possible shape:
//
//	restore:  take the safety backup      ← snapshot of the world
//	action:   commit a reward, return 200 ← the player is told it happened
//	restore:  overwrite the live database ← the reward is gone
//
// and because the write landed *after* the snapshot, the safety backup that
// exists precisely to undo a bad restore does not contain it either. The
// player was told yes and there is no copy of the yes anywhere.
//
// So: every request that can write takes the barrier shared, and restore takes
// it exclusively. Ordinary traffic runs fully concurrently with itself, since
// a shared lock is uncontended among readers; a restore waits for every
// in-flight request to finish, and every request that arrives during a restore
// waits for it. The safety backup is then taken *after* the barrier is held
// and after existing db sessions are closed, so it is a snapshot of a world
// that nothing is still writing to.
//
// This is a process-wide barrier, which is the right scope: SQLite is the
// serialisation point between processes, and a second engine writing to the
// same file during a restore is a deployment mistake rather than a race this
// code can fix. What it can fix is its own traffic, and that was the leak.
type maintenanceBarrier struct {
	mu sync.RWMutex
}

// begin is taken by anything that may write. The deferred function releases it.
func (b *maintenanceBarrier) begin() func() {
	b.mu.RLock()
	return b.mu.RUnlock
}

// enter is taken by maintenance that must own the database alone.
func (b *maintenanceBarrier) enter() func() {
	b.mu.Lock()
	return b.mu.Unlock
}

// busy reports whether maintenance currently holds the barrier. It is only ever
// a hint - by the time a caller reads it the answer may have changed - so it is
// used for health reporting and never to decide whether a write may proceed.
func (b *maintenanceBarrier) busy() bool {
	if b.mu.TryRLock() {
		b.mu.RUnlock()
		return false
	}
	return true
}

// maintenancePaths own the database exclusively while they run: a restore
// replaces the file, and VACUUM needs the whole database to itself.
var maintenancePaths = map[string]bool{
	"/v1/db/restore":     true,
	"/v1/db/maintenance": true,
}

// barrierFor decides which side of the barrier a path sits on. Deliberately a
// default-deny list rather than an opt-in on each handler: a new endpoint that
// forgets to take the barrier is exactly the bug this exists to prevent, so
// everything under /v1/ is treated as a possible writer unless it is
// maintenance. `/livez` and `/readyz` take nothing, so health checks keep
// answering while a restore runs.
func barrierFor(path string) (exclusive bool, guarded bool) {
	if maintenancePaths[path] {
		return true, true
	}
	if len(path) >= 4 && path[:4] == "/v1/" {
		return false, true
	}
	return false, false
}
