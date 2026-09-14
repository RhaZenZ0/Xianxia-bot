package gamerng

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"strings"
	"testing"
)

// The dice, and the one seam in them.

func TestIntnRefusesAnImpossibleBound(t *testing.T) {
	for _, n := range []int{0, -1} {
		if _, err := Intn(n); err == nil {
			t.Fatalf("Intn(%d) should be an error, not a roll", n)
		}
	}
}

func TestTheRealDiceStayInsideTheirBounds(t *testing.T) {
	for i := 0; i < 500; i++ {
		v, err := Intn(6)
		if err != nil {
			t.Fatal(err)
		}
		if v < 0 || v > 5 {
			t.Fatalf("Intn(6) rolled %d", v)
		}
	}
	for i := 0; i < 200; i++ {
		v, err := D10()
		if err != nil {
			t.Fatal(err)
		}
		if v < 1 || v > 10 {
			t.Fatalf("D10 rolled %d", v)
		}
	}
}

func TestUseRollerAnswersByBoundAndGivesTheDiceBack(t *testing.T) {
	restore := UseRoller(func(n int) int {
		if n == 100 {
			return 0
		}
		return n - 1
	})
	if v, _ := Intn(100); v != 0 {
		t.Fatalf("the loaded 1-in-100 rolled %d", v)
	}
	if v, _ := Intn(3); v != 2 {
		t.Fatalf("the loaded pick rolled %d", v)
	}
	restore()
	// Back to crypto/rand: not a fixed answer any more. Five hundred rolls of
	// a d100 all coming up 0 is not something that happens.
	same := 0
	for i := 0; i < 500; i++ {
		if v, _ := Intn(100); v == 0 {
			same++
		}
	}
	if same >= 500 {
		t.Fatal("the dice were never given back")
	}
}

func TestALoadedRollerCannotProduceAnImpossibleDie(t *testing.T) {
	defer UseRoller(func(int) int { return 999 })()
	if v, _ := Intn(4); v != 3 {
		t.Fatalf("a roller answering out of range must be clamped into the die, got %d", v)
	}
	defer UseRoller(func(int) int { return -7 })()
	if v, _ := Intn(4); v != 0 {
		t.Fatalf("a negative answer must be clamped to 0, got %d", v)
	}
	// And the bound check still runs ahead of the roller.
	if _, err := Intn(0); err == nil {
		t.Fatal("a loaded roller must not make Intn(0) legal")
	}
}

// The seam exists for tests and must stay there. A production caller could
// pin the world's dice for everybody, which is the one thing this package
// promises it will not do.
func TestOnlyTestsBorrowTheDice(t *testing.T) {
	root := filepath.Join("..", "..")
	fset := token.NewFileSet()
	offenders := []string{}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		if filepath.Base(path) == "rng.go" {
			return nil // the seam's own definition
		}
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		ast.Inspect(file, func(n ast.Node) bool {
			sel, ok := n.(*ast.SelectorExpr)
			if ok && sel.Sel.Name == "UseRoller" {
				offenders = append(offenders, path)
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) > 0 {
		t.Fatalf("UseRoller is test-only; production callers: %v", offenders)
	}
}
