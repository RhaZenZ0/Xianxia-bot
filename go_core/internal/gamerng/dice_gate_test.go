package gamerng

// The other direction (v1.0.0-rc.42).
//
// `TestOnlyTestsBorrowTheDice` proves production never borrows the dice. It is
// one-directional, and the direction it cannot see is the one that keeps
// costing: a *test* that drives a real crypto/rand path and then asserts the
// roll landed. CLAUDE.md has forbidden that since September and the rule has
// been broken three times since, because prose is not a gate:
//
//   - September: a sect war at 12% and a grave-robber at 22%, one run in 2,000
//     and one in 50. Both fixed; the rest of the class was never swept.
//   - v1.0.0-rc.41: a realm crossing at 0.82^30, one run in 385. It went red on
//     an unrelated pull request and cost a diagnosis before anyone could read
//     the change it had stopped.
//
// This is the sweep's guard, and it is a **shape detector, not a proof**. It
// asks two questions of every test in `simulation` and `game`: can this reach a
// crypto/rand draw at all, and does it assert that something happened? The
// first half is a real call graph - the production functions that touch
// `gamerng`, closed over same-package calls - which is what keeps the second
// half, a list of phrasings, from flagging a suite full of tests that never
// roll anything. It cannot compute a probability and it only knows the
// phrasings below; what it does is move the common case from "somebody
// remembers the rule" to "CI says so", the same bargain
// OPERATIONAL_REQUIRED_TABLES makes.
//
// A test it flags must either lend the dice, be made certain by its scenario,
// or be named in `diceAllowed` with the reason it is safe.

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Tests that reach a rolled path, assert on it, and still do not lend the dice
// - each with the reason it is allowed to. A reason is required, and an entry
// naming a test that no longer exists fails below, so this can only shrink
// honestly.
var diceAllowed = map[string]string{
	// Content, counted before or beside anything rolled. The detector cannot
	// tell a zero-check on a catalogue read from a zero-check on a tally the
	// tick produced, so each of these says which it is.
	"TestBootstrapOwnsSimulationAndClanSeedWrites":                    "counts civilization_regions, which bootstrap seeds one per content location with no roll anywhere on the path",
	"TestBootstrapSkipsHiddenSectsAndKeepsManualsOffTheMarket":        "counts the manual items the content file declares, derived from the catalogue rather than from anything the tick rolled",
	"TestAMerchantsOwnShopIsStockedAtSeedAndRestockedAtHome":          "the zero-check is len(hu.Wares) on the merchant the content file authors, read before any action runs",
	"TestFamilyCityRoadGraphIsBidirectionalConnectedAndWorldLocal":    "the zero-check is len(location.Roads) on the authored map, which is the property under test and is not rolled",
	"TestFamilyRoadNeighborCanBeTravelledAndIsPersistentlyDiscovered": "the same len(location.Roads) on the authored map, read out of the catalogue before the travel it sets up",
	"TestStarterHouseholdsAreSharedStableAndCoLocatedPlayersCanMeet":  "counts the starter household offers, which are the content file's own list and are the same list twice by design",
	"TestAWaystationKeepsAStallAndStandsOnTheMerchantsRoad":           "the stall's stock is ensureShopStockTx writing max64(1, line.Quantity) per authored ware, so an empty stall is a content fault and not a miss",

	// A floor in the production code makes the zero the test guards against
	// unreachable, whatever the dice say.
	"TestBatch4LawComprehendPersistsLawDaoCooldownAndReceipt": "law.comprehend clamps the roll with `if gain < 1 { gain = 1 }`, so the gain this reads can never come back below one",
	"TestSeclusionStartDerivesTheEnvironmentFromState":        "projected_daily_gain is seclusionDailyGainGo, arithmetic over pace and attributes returning max64(1, ...), with no draw on the path",

	// Deterministic writes on a path that happens to roll elsewhere.
	"TestTheWorldOpensWithHouseholdsAndChildrenInIt":        "bootstrap_households.go names gamerng nowhere: every household, marriage and child is keyed off hash64 so the same content makes the same world twice",
	"TestTheWorldOpensWithEldersInIt":                       "the same hash64-keyed seeding, counting the people it places near the end of the span their realm allows",
	"TestExpiryFailsPastDeadlineCommissionsOnceOnTheTick":   "the cooldown it reads is written unconditionally as gameMinute + rule.CooldownMinutes when a commission expires",
	"TestTheGiftArrivesAsTheMaterialTheCultivatorsPathUses": "the quantity is gift.ItemQty straight off the content entry, which the support action copies into the inventory without a roll",
}

// gatedPackages are the two packages whose rules are rolled. `gamerng` itself
// is excluded: its own tests drive `roll` deliberately.
var gatedPackages = []string{
	filepath.Join("..", "simulation"),
	filepath.Join("..", "game"),
}

// seamVar spots the `game` package's second seam - package vars like
// `roadEncounterIntn` and `shopDiscoveryIntn`, all `= gamerng.Intn` by default,
// which travel and shop tests reassign instead of calling `UseRoller`. Calling
// one is a draw; assigning one is lending the dice.
func seamVar(name string) bool {
	return strings.HasSuffix(name, "Intn") && name != "Intn"
}

// callGraph reads every `.go` file matching want in dir and returns, per
// function name, the same-package names it calls, plus the ones that draw from
// crypto/rand directly.
func callGraph(dir string, wantTests bool, draws func(*ast.SelectorExpr) bool) (map[string]map[string]bool, map[string]bool, error) {
	fset := token.NewFileSet()
	callees := map[string]map[string]bool{}
	direct := map[string]bool{}

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, nil, err
	}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") {
			continue
		}
		if strings.HasSuffix(name, "_test.go") != wantTests {
			continue
		}
		file, err := parser.ParseFile(fset, filepath.Join(dir, name), nil, 0)
		if err != nil {
			return nil, nil, err
		}
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			fname := fn.Name.Name
			if callees[fname] == nil {
				callees[fname] = map[string]bool{}
			}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				switch fun := call.Fun.(type) {
				case *ast.Ident:
					if seamVar(fun.Name) {
						direct[fname] = true
					}
					callees[fname][fun.Name] = true
				case *ast.SelectorExpr:
					if draws(fun) {
						direct[fname] = true
					}
					callees[fname][fun.Sel.Name] = true
				}
				return true
			})
		}
	}
	return callees, direct, nil
}

// closure marks every name that reaches a marked name through the call graph.
func closure(callees map[string]map[string]bool, marked map[string]bool) map[string]bool {
	reaches := map[string]bool{}
	for name := range marked {
		reaches[name] = true
	}
	for changed := true; changed; {
		changed = false
		for name, called := range callees {
			if reaches[name] {
				continue
			}
			for callee := range called {
				if reaches[callee] {
					reaches[name] = true
					changed = true
					break
				}
			}
		}
	}
	return reaches
}

// rollers is the set of functions in dir that can reach a crypto/rand draw:
// the production ones, and then the package's own test helpers that drive
// them, because a test that runs the tick through `runFinds(t, path)` is
// driving the dice exactly as much as one that calls the batch itself. A test
// that calls none of these cannot fail on the dice, whatever it asserts.
func rollers(dir string) (map[string]bool, error) {
	draws := func(sel *ast.SelectorExpr) bool {
		pkg, ok := sel.X.(*ast.Ident)
		return ok && pkg.Name == "gamerng" && sel.Sel.Name != "UseRoller"
	}
	callees, direct, err := callGraph(dir, false, draws)
	if err != nil {
		return nil, err
	}
	rolling := closure(callees, direct)

	helpers, helperDraws, err := callGraph(dir, true, draws)
	if err != nil {
		return nil, err
	}
	for name := range helperDraws {
		rolling[name] = true
	}
	return closure(helpers, rolling), nil
}

// lenders is the set of helpers in dir's own test files that hand the dice out
// - `alwaysRob()`, `everyFinderFinds()`, `highDice` - so a test that calls one
// is lending even though `UseRoller` is nowhere in its body.
func lenders(dir string) (map[string]bool, error) {
	callees, direct, err := callGraph(dir, true, func(sel *ast.SelectorExpr) bool {
		pkg, ok := sel.X.(*ast.Ident)
		return ok && pkg.Name == "gamerng" && sel.Sel.Name == "UseRoller"
	})
	if err != nil {
		return nil, err
	}
	return closure(callees, direct), nil
}

// reachesDice reports whether the test drives any function that can roll.
func reachesDice(fn *ast.FuncDecl, rolling map[string]bool) bool {
	found := false
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return !found
		}
		switch fun := call.Fun.(type) {
		case *ast.Ident:
			if rolling[fun.Name] || seamVar(fun.Name) {
				found = true
			}
		case *ast.SelectorExpr:
			if rolling[fun.Sel.Name] {
				found = true
			}
		}
		return !found
	})
	return found
}

// lendsDice reports whether the test borrows the dice by either seam: the
// shared one (`UseRoller`, directly or through one of the file's helpers), or
// a reassignment of a `game` package `*Intn` var.
func lendsDice(fn *ast.FuncDecl, lending map[string]bool) bool {
	borrowed := false
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.AssignStmt:
			for _, lhs := range node.Lhs {
				if ident, ok := lhs.(*ast.Ident); ok && seamVar(ident.Name) {
					borrowed = true
				}
			}
		case *ast.CallExpr:
			switch fun := node.Fun.(type) {
			case *ast.Ident:
				if lending[fun.Name] {
					borrowed = true
				}
			case *ast.SelectorExpr:
				if fun.Sel.Name == "UseRoller" {
					borrowed = true
				}
			}
		}
		return !borrowed
	})
	return borrowed
}

// nothingHappened reports whether an if-condition is the shape that says a roll
// failed to land: a count that came back zero. `x == 0`, `len(x) == 0` and
// `x < 1`, and nothing else.
//
// A negation - `!found`, `!happened` - deliberately does not count, and that is
// the detector's largest blind spot stated plainly. In this tree `!` is almost
// always the `ok` of a type assertion or a map lookup, or a predicate about the
// fixture, and admitting it produced eleven false positives against one true
// one. A tally is what a gambling test reads, so a tally is what this reads.
func nothingHappened(cond ast.Expr) bool {
	switch c := cond.(type) {
	case *ast.BinaryExpr:
		if c.Op == token.LAND || c.Op == token.LOR {
			return nothingHappened(c.X) || nothingHappened(c.Y)
		}
		if lit, ok := c.Y.(*ast.BasicLit); ok {
			if c.Op == token.EQL && lit.Value == "0" {
				return true
			}
			if c.Op == token.LSS && lit.Value == "1" {
				return true
			}
		}
	case *ast.ParenExpr:
		return nothingHappened(c.X)
	}
	return false
}

// failsInside reports whether the branch ends the test or marks it failed.
func failsInside(body *ast.BlockStmt) bool {
	failed := false
	ast.Inspect(body, func(n ast.Node) bool {
		if sel, ok := n.(*ast.SelectorExpr); ok {
			switch sel.Sel.Name {
			case "Fatal", "Fatalf", "Error", "Errorf":
				failed = true
			}
		}
		return !failed
	})
	return failed
}

// assertsSomethingHappened reports whether the test fails when a count is zero,
// a flag is false or a list is empty - the phrasing of "the roll must have
// landed" - and returns the position of the first such assertion.
func assertsSomethingHappened(fn *ast.FuncDecl) (token.Pos, bool) {
	var at token.Pos
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if at != token.NoPos {
			return false
		}
		if ifStmt, ok := n.(*ast.IfStmt); ok && nothingHappened(ifStmt.Cond) && failsInside(ifStmt.Body) {
			at = ifStmt.Pos()
			return false
		}
		return true
	})
	return at, at != token.NoPos
}

func TestATestThatAssertsARollLandedLendsTheDice(t *testing.T) {
	seen := map[string]bool{}
	offenders := []string{}
	fset := token.NewFileSet()

	for _, dir := range gatedPackages {
		rolling, err := rollers(dir)
		if err != nil {
			t.Fatal(err)
		}
		lending, err := lenders(dir)
		if err != nil {
			t.Fatal(err)
		}
		entries, err := os.ReadDir(dir)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), "_test.go") {
				continue
			}
			path := filepath.Join(dir, entry.Name())
			file, err := parser.ParseFile(fset, path, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			for _, decl := range file.Decls {
				fn, ok := decl.(*ast.FuncDecl)
				if !ok || fn.Body == nil || !strings.HasPrefix(fn.Name.Name, "Test") {
					continue
				}
				seen[fn.Name.Name] = true
				if _, allowed := diceAllowed[fn.Name.Name]; allowed {
					continue
				}
				if lendsDice(fn, lending) || !reachesDice(fn, rolling) {
					continue
				}
				at, gambles := assertsSomethingHappened(fn)
				if !gambles {
					continue
				}
				offenders = append(offenders, fn.Name.Name+"  ("+fset.Position(at).String()+")")
			}
		}
	}

	if len(offenders) > 0 {
		t.Fatalf("these drive a rolled path and then assert it landed, without lending the dice.\n"+
			"Lend them (gamerng.UseRoller), make the outcome certain by the scenario, or\n"+
			"name them in diceAllowed with the reason they are safe:\n  %s",
			strings.Join(offenders, "\n  "))
	}
	for name, reason := range diceAllowed {
		if !seen[name] {
			t.Errorf("diceAllowed names %q, which no longer exists", name)
		}
		if len(strings.TrimSpace(reason)) < 20 {
			t.Errorf("diceAllowed[%q] needs a reason worth reading", name)
		}
	}
}
