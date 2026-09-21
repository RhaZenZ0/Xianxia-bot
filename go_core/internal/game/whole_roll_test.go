package game

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// A result that reports a roll reports the whole roll (v1.0.3).
//
// `/craft` raised `AttributeError: no attribute 'die1'` on every craft that got
// past the materials check, because `craftResolveAction` shipped the flattened
// `d1`/`d2` and no `degree`, while `roll_line` in the bot reads
// `die1`/`die2`/`degree`. The raise happened *after* `applyAuthoritative` had
// committed - so the materials were spent, the output granted, the profession
// XP credited, and the player was shown a wiring failure and told nothing had
// happened. "It doesn't let you craft but also takes your items."
//
// v1.0.1 found and fixed exactly this for `forageResolveAction`, in this same
// file, and did not carry it across to the craft twenty lines up.
//
// The rule: shipping the flat dice is fine, shipping *only* them is not.
func TestAResultThatReportsARollReportsTheWholeRoll(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatalf("the sweep cannot list the package, so it proves nothing: %v", err)
	}
	fset := token.NewFileSet()
	flat, whole, scanned := []string{}, 0, 0
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		src, err := os.ReadFile(filepath.Clean(name))
		if err != nil {
			t.Fatalf("cannot read %s: %v", name, err)
		}
		file, err := parser.ParseFile(fset, name, src, 0)
		if err != nil {
			t.Fatalf("cannot parse %s: %v", name, err)
		}
		scanned++
		ast.Inspect(file, func(n ast.Node) bool {
			lit, ok := n.(*ast.CompositeLit)
			if !ok {
				return true
			}
			keys := map[string]bool{}
			for _, elt := range lit.Elts {
				kv, ok := elt.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				k, ok := kv.Key.(*ast.BasicLit)
				if !ok || k.Kind != token.STRING {
					continue
				}
				if unquoted, err := strconv.Unquote(k.Value); err == nil {
					keys[unquoted] = true
				}
			}
			if !keys["d1"] {
				return true
			}
			if keys["roll"] {
				whole++
			} else {
				flat = append(flat, fset.Position(lit.Pos()).String())
			}
			return true
		})
	}

	if scanned == 0 || whole+len(flat) == 0 {
		t.Fatalf("the sweep scanned %d files and found %d result maps reporting dice; it is broken, not the tree",
			scanned, whole+len(flat))
	}
	if len(flat) > 0 {
		t.Fatalf("%v ship the flattened dice and not the roll map, so `roll_line` in the bot raises on the reply "+
			"*after* the action has committed: the cost is paid and the player is told it failed", flat)
	}
}
