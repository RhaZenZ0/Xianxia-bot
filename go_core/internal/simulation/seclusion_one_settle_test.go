package simulation

// One settle (v1.0.0-rc.56).
//
// `advanceSeclusions` was a second implementation of the retreat settlement:
// a pre-rc.5 flat rate (`base := 8 + will + insight/2 + realm/2`, times a
// hardcoded .60), its own `minutesPerDay`, its own `.5`/`1.75` clamps, and
// none of the multipliers rc.55 gave a retreat. So which rate a retreat was
// paid at depended on whether this sweep reached it before the player came
// back - and it had no tests at all, which is why nobody noticed that the two
// had drifted twenty releases apart.
//
// This is the gate on the fold. It cannot compute a rate, so what it holds is
// the thing that made the drift possible: that this function does no
// arithmetic of its own and reaches the one rule in `game`. The rate itself
// is held across every world time scale by `game/seclusion_rate_test.go`.

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
)

func advanceSeclusionsBody(t *testing.T) (*ast.FuncDecl, *token.FileSet) {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	path := filepath.Join(filepath.Dir(file), "advanced_maintenance.go")
	fset := token.NewFileSet()
	parsed, err := parser.ParseFile(fset, path, nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if ok && fn.Name.Name == "advanceSeclusions" {
			return fn, fset
		}
	}
	t.Fatal("advanced_maintenance.go no longer declares advanceSeclusions")
	return nil, nil
}

func TestTheSweepPaysThroughTheOneSettle(t *testing.T) {
	fn, _ := advanceSeclusionsBody(t)

	// Every call the function makes, by name. Read as calls rather than as
	// text, so a `game.SettleSeclusionTx` left in a comment does not pass and
	// a rate restored in code cannot hide behind one.
	calls := map[string]bool{}
	ast.Inspect(fn, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		switch f := call.Fun.(type) {
		case *ast.Ident:
			calls[f.Name] = true
		case *ast.SelectorExpr:
			if pkg, ok := f.X.(*ast.Ident); ok {
				calls[pkg.Name+"."+f.Sel.Name] = true
			} else {
				calls[f.Sel.Name] = true
			}
		}
		return true
	})

	if !calls["game.SettleSeclusionTx"] {
		t.Fatal("the sweep no longer pays through game.SettleSeclusionTx; it has a settle of its own again")
	}
	// The rules it used to keep copies of. Each was a real divergence: the
	// simulation's own soul multiplier, its own phase cap, its own day.
	for _, gone := range []string{"soulMultSim", "phaseCapSim", "r.phaseCapSim"} {
		if calls[gone] {
			t.Errorf("the sweep calls %s again: the rule lives in game now", gone)
		}
	}

	// And no arithmetic: a rate is made of numbers, so a function with none
	// cannot have one. `changed++` is a tally and is not an arithmetic
	// expression node.
	var operators []string
	ast.Inspect(fn, func(n ast.Node) bool {
		binary, ok := n.(*ast.BinaryExpr)
		if !ok {
			return true
		}
		switch binary.Op {
		case token.ADD, token.SUB, token.MUL, token.QUO, token.REM:
			operators = append(operators, binary.Op.String())
		}
		return true
	})
	sort.Strings(operators)
	if len(operators) > 0 {
		t.Fatalf("the sweep does arithmetic again (%s); the rate belongs to game.SettleSeclusionTx",
			strings.Join(operators, " "))
	}
}

// The other half: nothing else in the package kept a piece of the old copy.
// `minutesPerDay` is still used by six other batches and stays; what must be
// gone is the rate's own constants.
func TestTheOldSeclusionRateIsGoneFromThePackage(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	source := filepath.Join(filepath.Dir(file), "advanced_maintenance.go")
	fset := token.NewFileSet()
	parsed, err := parser.ParseFile(fset, source, nil, parser.ParseComments)
	if err != nil {
		t.Fatal(err)
	}
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok {
			continue
		}
		if fn.Name.Name == "soulMultSim" || fn.Name.Name == "phaseCapSim" {
			t.Errorf("%s is back: it existed only for the second settle", fn.Name.Name)
		}
	}
}
