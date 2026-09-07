package simulation

// The v0.22.2 regression test for the review's finding #1.
//
// RunDue used to read every system's anchor at the top of the call and only
// then open the write transaction that applied and advanced it. SQLite
// serialised the writes correctly and it did not help: both callers had
// already decided, from the same stale anchor, that one interval was due. At
// 32 concurrent callers the reviewer measured the same interval applied seven
// to fourteen times.
//
// The fix is that the read now happens under the write lock, so the second
// caller sees the first caller's anchor and finds nothing due. This test is
// the proof, and it is written to fail loudly against the old code rather than
// to pass quietly against the new: it asserts on the anchor, on the run
// counter, and on the number of callers that believed they had work.

import (
	"sync"
	"testing"

	"xianxia/core/internal/storage"
)

const concurrencySchema = `
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE economy_markets(location TEXT,item_id TEXT,world_name TEXT,currency_id TEXT,base_price INTEGER,supply INTEGER,demand INTEGER,price_index REAL,last_game_minute INTEGER,updated_at REAL,PRIMARY KEY(location,item_id));
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY,world_name TEXT,population INTEGER,prosperity INTEGER,security INTEGER,spirit_resources INTEGER,food_supply INTEGER,migration_pressure INTEGER,unrest INTEGER,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE world_eras(era_id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,description TEXT,active INTEGER,modifiers_json TEXT,started_game_minute INTEGER,ends_game_minute INTEGER,updated_at REAL);
INSERT INTO world_simulation_state VALUES('dynamic_economy',0,720,0,0);
INSERT INTO civilization_regions VALUES('Greenriver Town','Mortal World',1000,50,50,50,50,0,0,0,0);
INSERT INTO economy_markets VALUES('Greenriver Town','spirit_herb','Mortal World','low_spirit_stone',100,50,50,1.0,0,0);
`

// runDueConcurrently fires n RunDue calls at once and reports how many of them
// believed a `dynamic_economy` interval was due.
func runDueConcurrently(t *testing.T, path string, n int) int {
	t.Helper()
	automation := map[string]bool{"dynamic_economy": true}
	var wg sync.WaitGroup
	var mu sync.Mutex
	claimed := 0
	start := make(chan struct{})
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			runner, err := NewRunner(path, "")
			if err != nil {
				return
			}
			<-start // release them together, to make the window as wide as it gets
			runs, err := runner.RunDue(RunDueRequest{Automation: automation})
			if err != nil {
				// A loser that finds the anchor moved under it is a correct
				// outcome, not a claimed run.
				return
			}
			for _, run := range runs {
				if run.System == "dynamic_economy" && run.AppliedSteps > 0 {
					mu.Lock()
					claimed++
					mu.Unlock()
				}
			}
		}()
	}
	close(start)
	wg.Wait()
	return claimed
}

func TestOneDueIntervalIsAppliedExactlyOnceUnderConcurrency(t *testing.T) {
	path := setupSimulationDB(t, concurrencySchema)
	// One interval due, and one only: 720 minutes at a 720-minute interval.
	setSimulationGameMinute(t, path, 720)

	claimed := runDueConcurrently(t, path, 32)
	if claimed != 1 {
		t.Fatalf("%d of 32 concurrent callers applied the same interval; exactly one may", claimed)
	}
	// The anchor moved exactly one interval, and the run counter agrees. The
	// counter is the load-bearing assertion: a double application that happened
	// to land on the same anchor would still show up here.
	if got := storage.ParseInt(simScalar(t, path, "SELECT last_game_minute FROM world_simulation_state WHERE system='dynamic_economy'")); got != 720 {
		t.Fatalf("last_game_minute=%d, want 720", got)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT runs FROM world_simulation_state WHERE system='dynamic_economy'")); got != 1 {
		t.Fatalf("runs=%d, want 1 - the interval was applied more than once", got)
	}
}

func TestConcurrentCallersDoNotSkipABacklog(t *testing.T) {
	// The mirror of the test above: fixing the race must not make a legitimate
	// catch-up disappear. Five intervals are due; one caller should take all
	// five, and nobody should take them twice.
	path := setupSimulationDB(t, concurrencySchema)
	setSimulationGameMinute(t, path, 5*720)

	claimed := runDueConcurrently(t, path, 16)
	if claimed != 1 {
		t.Fatalf("%d callers claimed the backlog; exactly one may", claimed)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT last_game_minute FROM world_simulation_state WHERE system='dynamic_economy'")); got != 5*720 {
		t.Fatalf("last_game_minute=%d, want %d", got, 5*720)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT runs FROM world_simulation_state WHERE system='dynamic_economy'")); got != 5 {
		t.Fatalf("runs=%d, want 5", got)
	}
}

func TestNothingDueMeansNothingIsWritten(t *testing.T) {
	path := setupSimulationDB(t, concurrencySchema)
	setSimulationGameMinute(t, path, 719) // one minute short of the first interval

	if claimed := runDueConcurrently(t, path, 8); claimed != 0 {
		t.Fatalf("%d callers ran a system that was not due", claimed)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT runs FROM world_simulation_state WHERE system='dynamic_economy'")); got != 0 {
		t.Fatalf("runs=%d, want 0", got)
	}
}

func TestTheWorldClockIsNotARequestParameter(t *testing.T) {
	// Finding #6. A caller that says the world is at minute 50,000,000 used to
	// get 120 intervals of catch-up and an anchor far in the future; now the
	// request field is inert and Go reads the clock itself.
	path := setupSimulationDB(t, concurrencySchema)
	setSimulationGameMinute(t, path, 720)

	runner, err := NewRunner(path, "")
	if err != nil {
		t.Fatal(err)
	}
	runs, err := runner.RunDue(RunDueRequest{GameMinute: 50_000_000, Automation: map[string]bool{"dynamic_economy": true}})
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 1 || runs[0].AppliedSteps != 1 {
		t.Fatalf("a caller-supplied game_minute changed the work done: %+v", runs)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT last_game_minute FROM world_simulation_state WHERE system='dynamic_economy'")); got != 720 {
		t.Fatalf("last_game_minute=%d - the caller moved the world clock", got)
	}
}
