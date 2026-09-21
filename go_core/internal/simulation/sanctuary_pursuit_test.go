package simulation

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A hunter cannot take you off an auction floor (v1.0.6).
//
// `advanceHunters` raised pressure, engaged and captured a fugitive without
// consulting `characters.location` anywhere in it - the words "location" and
// "Location" appeared nowhere in either hunter function. So a cultivator with
// a bounty on their head was captured inside the Golden Pavilion, whose own
// description reads "Violence inside is forbidden; the protection ends at the
// front doors", while `protected_interior` sat on all 48 houses read by
// nothing.
//
// The two halves are asserted apart deliberately. Capture is what a sanctuary
// stops. **Pressure is not**, because the hunter is waiting at the doors
// either way and standing still is not escaping - and a test that only
// asserted "nothing happened inside" would pass just as well for a rule that
// froze the pursuit outright, which would make an auction floor a place to
// park a fugitive for ever.

// sanctuaryWorld is the shipped shape in miniature: a protected floor, an
// unprotected one, and open country. The unprotected floor is the
// counterfactual the content file does not contain, and without it every
// assertion here would also pass for a rule that answered "sanctuary" to any
// auction interior whatever `protected_interior` said.
func sanctuaryWorld() worlddata.Catalog {
	return worlddata.Catalog{
		Locations: map[string]worlddata.LocationDefinition{
			"Ash Wolf Hunting Ground": {World: "Mortal World"},
			"Golden Pavilion":         {World: "Mortal World", SafeZone: true, AuctionHouse: "golden_pavilion"},
			"Unguarded Stalls":        {World: "Mortal World", SafeZone: true, AuctionHouse: "open_yard"},
		},
		AuctionHouses: map[string]worlddata.AuctionHouse{
			"golden_pavilion": {Name: "Golden Pavilion Auction House", Location: "Golden Pavilion", ProtectedInterior: true},
			"open_yard":       {Name: "Open Yard", Location: "Unguarded Stalls", ProtectedInterior: false},
		},
	}
}

// cornered is a fugitive already at the pressure the capture step needs, so a
// single sweep separates "capture advanced" from "capture did not".
func cornered(t *testing.T, path string, userID int64, location string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO characters(user_id,location) VALUES(?,?)`, []any{userID, location}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO bounties(user_id,jurisdiction,amount,status,updated_at) VALUES(?,'Greenriver',500,'active',0)`, []any{userID}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO bounty_hunter_pursuits(bounty_id,user_id,hunter_name,hunter_power,status,pressure,capture_progress,next_action_game_minute,updated_at)
		VALUES((SELECT MAX(bounty_id) FROM bounties),?,'Iron Badge Constable',6,'engaged',70,0,0,0)`, []any{userID}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func sweepHunters(t *testing.T, path string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	r := &Runner{World: sanctuaryWorld()}
	if _, err := r.advanceHunters(conn, minutesPerDay); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
}

func TestAProtectedFloorStopsTheCaptureAndNotThePressure(t *testing.T) {
	path := setupSimulationDB(t, trackingPursuitSchema)
	cornered(t, path, 42, "Ash Wolf Hunting Ground") // open country
	cornered(t, path, 43, "Golden Pavilion")         // protected floor
	cornered(t, path, 44, "Unguarded Stalls")        // a floor that claims nothing
	sweepHunters(t, path)

	capture := func(userID int64) int64 {
		return storage.ParseInt(simScalar(t, path, `SELECT capture_progress FROM bounty_hunter_pursuits WHERE user_id=?`, userID))
	}
	pressure := func(userID int64) int64 {
		return storage.ParseInt(simScalar(t, path, `SELECT pressure FROM bounty_hunter_pursuits WHERE user_id=?`, userID))
	}

	if capture(42) <= 0 {
		t.Fatalf("a fugitive in open country was not closed on at all (capture %d); the sweep is broken, not the tree", capture(42))
	}
	if capture(44) <= 0 {
		t.Fatalf("a floor with protected_interior false gave sanctuary anyway (capture %d)", capture(44))
	}
	if got := capture(43); got != 0 {
		t.Fatalf("a hunter took a fugitive off a protected auction floor: capture_progress is %d, "+
			"and the house's own description forbids violence on it", got)
	}
	if got := pressure(43); got <= 70 {
		t.Fatalf("pressure did not rise on the protected floor (%d): the hunter is at the doors either "+
			"way, and a floor that freezes a pursuit is somewhere to park a fugitive for ever", got)
	}
}

func TestTheSweepReachesTheOneSanctuaryRule(t *testing.T) {
	// `game.WalletDeltaTx` is the precedent: a tick step calls into `game` for
	// a rule rather than keeping a second copy. A sanctuary read that grew its
	// own `ProtectedInterior` lookup here would be the "four copies of one
	// rule" this tree removed for the world clock and the world currencies.
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "advanced_maintenance.go", nil, 0)
	if err != nil {
		t.Fatalf("cannot parse the tick: %v; the gate is broken, not the tree", err)
	}
	calls, ownLookup := false, false
	ast.Inspect(file, func(n ast.Node) bool {
		sel, ok := n.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		if sel.Sel.Name == "LocationIsSanctuary" {
			calls = true
		}
		if sel.Sel.Name == "ProtectedInterior" {
			ownLookup = true
		}
		return true
	})
	if !calls {
		t.Fatal("the tick no longer reaches game.LocationIsSanctuary; a fugitive is captured on an auction floor again")
	}
	if ownLookup {
		t.Fatal("the tick reads ProtectedInterior itself; the rule has two copies now, which is how they drift")
	}
	source, err := os.ReadFile("advanced_maintenance.go")
	if err != nil {
		t.Fatalf("cannot read the tick: %v; the gate is broken, not the tree", err)
	}
	if !strings.Contains(string(source), "LEFT JOIN characters") {
		t.Fatal("the pursuit sweep no longer LEFT JOINs the quarry's row; an INNER JOIN silently drops " +
			"a pursuit whose character is gone, which used to advance exactly as any other")
	}
}
