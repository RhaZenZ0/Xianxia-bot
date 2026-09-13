package game

// The roster in cooldown_status.go claims to be every cooldown the engine
// writes. Nothing enforced that claim before - `admin.player.reset_cooldowns`
// deletes rows wholesale precisely because no list existed - so this scans the
// package's own source for the call sites and compares.
//
// It is an ast walk rather than a regex over quoted strings on purpose: a
// regex would pick up `key := "orthodox_public_use"` in
// manual_forbidden_actions.go, which is a world-rules lookup and not a
// cooldown at all, and an afternoon would go into working out why the roster
// wanted a family nobody can wait on.

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// A key this scanner cannot resolve to a literal - one built entirely from
// runtime values with no literal prefix at all. Empty is the goal: a cooldown
// nobody can name is a cooldown nobody can reset, label or explain. An entry
// needs its call site and a reason.
var cooldownScanAllowlist = map[string]string{}

type cooldownScan struct {
	keys      map[string]string // literal key or "prefix:" -> where it was found
	callSites int
	rawInsert int
}

func packageDir(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	return filepath.Dir(file)
}

// resolveCooldownKey turns the expression passed as an action name into the
// literal key, or the literal prefix of a composite key, that it can produce.
// One expression can produce several: a key chosen by an if/else above the
// call yields both branches.
func resolveCooldownKey(expr ast.Expr, consts map[string]string, fn *ast.FuncDecl) []string {
	switch node := expr.(type) {
	case *ast.BasicLit:
		if node.Kind == token.STRING {
			if value, err := strconv.Unquote(node.Value); err == nil {
				return []string{value}
			}
		}
	case *ast.Ident:
		if value, ok := consts[node.Name]; ok {
			return []string{value}
		}
		// A local: every assignment to that name inside this function is a
		// candidate. This is what makes one call site yield both `cultivate`
		// and `body_cultivate`.
		return assignmentsTo(node.Name, consts, fn)
	case *ast.BinaryExpr:
		// "manual:" + p.ManualID - the literal half is the prefix.
		if node.Op == token.ADD {
			if lit, ok := node.X.(*ast.BasicLit); ok && lit.Kind == token.STRING {
				if value, err := strconv.Unquote(lit.Value); err == nil {
					return []string{value}
				}
			}
		}
	case *ast.CallExpr:
		// fmt.Sprintf("beast_feed:%d", id) - the format up to the first verb.
		if sel, ok := node.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Sprintf" && len(node.Args) > 0 {
			if lit, ok := node.Args[0].(*ast.BasicLit); ok && lit.Kind == token.STRING {
				if value, err := strconv.Unquote(lit.Value); err == nil {
					if at := strings.Index(value, "%"); at >= 0 {
						return []string{value[:at]}
					}
					return []string{value}
				}
			}
		}
	}
	return nil
}

func assignmentsTo(name string, consts map[string]string, fn *ast.FuncDecl) []string {
	if fn == nil {
		return nil
	}
	var out []string
	ast.Inspect(fn, func(n ast.Node) bool {
		assign, ok := n.(*ast.AssignStmt)
		if !ok {
			return true
		}
		for i, lhs := range assign.Lhs {
			ident, ok := lhs.(*ast.Ident)
			if !ok || ident.Name != name || i >= len(assign.Rhs) {
				continue
			}
			// Guard against a self-referential resolve (x = x + "…").
			if rhsIdent, ok := assign.Rhs[i].(*ast.Ident); ok && rhsIdent.Name == name {
				continue
			}
			out = append(out, resolveCooldownKey(assign.Rhs[i], consts, fn)...)
		}
		return true
	})
	return out
}

func scanCooldownWrites(t *testing.T) cooldownScan {
	t.Helper()
	dir := packageDir(t)
	fset := token.NewFileSet()
	// One file at a time rather than parser.ParseDir, which is deprecated
	// since Go 1.25 (and staticcheck says so).
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string]*ast.File{}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		parsed, parseErr := parser.ParseFile(fset, filepath.Join(dir, name), nil, 0)
		if parseErr != nil {
			t.Fatal(parseErr)
		}
		files[name] = parsed
	}
	pkgs := map[string]map[string]*ast.File{"game": files}
	scan := cooldownScan{keys: map[string]string{}}
	consts := map[string]string{}
	for _, pkg := range pkgs {
		// Package-level string constants first: `supportVoteAction` is one.
		for _, file := range pkg {
			for _, decl := range file.Decls {
				gen, ok := decl.(*ast.GenDecl)
				if !ok || (gen.Tok != token.CONST && gen.Tok != token.VAR) {
					continue
				}
				for _, spec := range gen.Specs {
					value, ok := spec.(*ast.ValueSpec)
					if !ok {
						continue
					}
					for i, name := range value.Names {
						if i >= len(value.Values) {
							continue
						}
						if lit, ok := value.Values[i].(*ast.BasicLit); ok && lit.Kind == token.STRING {
							if text, err := strconv.Unquote(lit.Value); err == nil {
								consts[name.Name] = text
							}
						}
					}
				}
			}
		}
		for name, file := range pkg {
			for _, decl := range file.Decls {
				fn, ok := decl.(*ast.FuncDecl)
				if !ok || fn.Name.Name == "setCooldown" {
					continue // the declaration's own args are parameters
				}
				ast.Inspect(fn, func(n ast.Node) bool {
					call, ok := n.(*ast.CallExpr)
					if !ok {
						return true
					}
					if ident, ok := call.Fun.(*ast.Ident); ok && ident.Name == "setCooldown" && len(call.Args) >= 3 {
						scan.callSites++
						where := fmt.Sprintf("%s:%d", filepath.Base(name), fset.Position(call.Pos()).Line)
						for _, key := range resolveCooldownKey(call.Args[2], consts, fn) {
							scan.keys[key] = where
						}
					}
					// Raw SQL: the two writers that never call setCooldown.
					for _, arg := range call.Args {
						lit, ok := arg.(*ast.BasicLit)
						if !ok || lit.Kind != token.STRING {
							continue
						}
						text, err := strconv.Unquote(lit.Value)
						if err != nil || !strings.Contains(strings.ToUpper(text), "INSERT INTO COOLDOWNS") {
							continue
						}
						scan.rawInsert++
						where := fmt.Sprintf("%s:%d", filepath.Base(name), fset.Position(call.Pos()).Line)
						// The key is either inlined in the statement...
						for _, quoted := range inlineSQLStrings(text) {
							scan.keys[quoted] = where
						}
						// ...or bound as a parameter beside it.
						for _, other := range call.Args {
							composite, ok := other.(*ast.CompositeLit)
							if !ok {
								continue
							}
							for _, element := range composite.Elts {
								for _, key := range resolveCooldownKey(element, consts, fn) {
									if key != "" && !strings.ContainsAny(key, " ?()") {
										scan.keys[key] = where
									}
								}
							}
						}
					}
					return true
				})
			}
		}
	}
	return scan
}

// inlineSQLStrings pulls 'quoted' literals out of a SQL statement - the shape
// `VALUES(?,'dao_dual_cultivation',?)`.
func inlineSQLStrings(sql string) []string {
	var out []string
	for {
		start := strings.Index(sql, "'")
		if start < 0 {
			return out
		}
		rest := sql[start+1:]
		end := strings.Index(rest, "'")
		if end < 0 {
			return out
		}
		if value := rest[:end]; value != "" {
			out = append(out, value)
		}
		sql = rest[end+1:]
	}
}

func rosterKeys() map[string]bool {
	out := map[string]bool{}
	for _, family := range cooldownFamilies {
		if family.Key != "" {
			out[family.Key] = true
		}
		if family.Prefix != "" {
			out[family.Prefix] = true
		}
	}
	return out
}

func TestEveryCooldownKeyTheEngineWritesIsInTheRoster(t *testing.T) {
	scan := scanCooldownWrites(t)
	roster := rosterKeys()

	var missing []string
	for key, where := range scan.keys {
		if roster[key] {
			continue
		}
		if _, allowed := cooldownScanAllowlist[key]; allowed {
			continue
		}
		missing = append(missing, fmt.Sprintf("%q (%s)", key, where))
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Fatalf("cooldown keys the engine writes but nothing can name:\n  %s\n\n"+
			"Add each to cooldownFamilies in cooldown_status.go, and a label to "+
			"_FAMILY_LABELS in app/bot/commands/cooldowns.py.", strings.Join(missing, "\n  "))
	}

	// And the other direction: a family left behind by a deleted mechanic is
	// a row the card promises and the engine never sets.
	var orphaned []string
	for key := range roster {
		if _, found := scan.keys[key]; !found {
			orphaned = append(orphaned, key)
		}
	}
	sort.Strings(orphaned)
	if len(orphaned) > 0 {
		t.Fatalf("roster entries nothing in the engine writes any more: %s", strings.Join(orphaned, ", "))
	}
}

// A scanner that silently matches nothing passes forever. This is the check
// that says it is actually looking at the code.
func TestTheCooldownScannerFoundTheCallSites(t *testing.T) {
	scan := scanCooldownWrites(t)
	if scan.callSites < 20 {
		t.Fatalf("found only %d setCooldown call sites; the scan is not reaching the package", scan.callSites)
	}
	if scan.rawInsert < 2 {
		t.Fatalf("found %d raw INSERT INTO cooldowns sites, want the two that bypass setCooldown", scan.rawInsert)
	}
	// One of each shape the resolver has to handle, so a regression in any one
	// of them is loud rather than a quietly shrinking roster.
	for _, shape := range []struct{ key, why string }{
		{"explore", "a plain string literal"},
		{"support_vote", "a package-level constant"},
		{"cultivate", "a local assigned in one branch"},
		{"body_cultivate", "a local assigned in the other branch"},
		{"manual:", `a "prefix" + id concatenation`},
		{"beast_feed:", "an fmt.Sprintf format"},
		{"dao_dual_cultivation", "a key inlined in raw SQL"},
		{"war_action:", "a key bound as a parameter beside raw SQL"},
	} {
		if _, found := scan.keys[shape.key]; !found {
			t.Errorf("the scanner no longer resolves %s (%q)", shape.why, shape.key)
		}
	}
}

// The waits that live outside the cooldowns table have no setCooldown call to
// scan for, so the list of them is held the other way round: every family the
// external builder can emit must be named in cooldownExternalFamilies, and
// every name in that list must be one the builder can emit. Without this the
// list would be a comment that drifts, and the Python card would be holding
// its labels against something untrue.
func TestEveryExternalWaitTheBuilderEmitsIsNamed(t *testing.T) {
	source, err := os.ReadFile(filepath.Join(packageDir(t), "cooldown_status.go"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(source)
	body := text[strings.Index(text, "func cooldownExternalWaits"):]

	// Both row builders take (key, family, subject, …) as their first three
	// arguments, so the family is the second string literal of the call.
	emitted := map[string]bool{}
	for _, call := range []string{"fromGameMinute(", "cooldownRow("} {
		for _, fragment := range strings.Split(body, call)[1:] {
			parts := strings.SplitN(fragment, ",", 3)
			if len(parts) < 2 {
				continue
			}
			family := strings.Trim(strings.TrimSpace(parts[1]), `"`)
			if family != "" && !strings.ContainsAny(family, " .()") {
				emitted[family] = true
			}
		}
	}
	if len(emitted) < 5 {
		t.Fatalf("found only %d external waits (%v); the scan is not reading the builder", len(emitted), emitted)
	}
	named := map[string]bool{}
	for _, family := range cooldownExternalFamilies {
		named[family] = true
	}
	var missing, stale []string
	for family := range emitted {
		if !named[family] {
			missing = append(missing, family)
		}
	}
	for family := range named {
		if !emitted[family] {
			stale = append(stale, family)
		}
	}
	sort.Strings(missing)
	sort.Strings(stale)
	if len(missing) > 0 {
		t.Fatalf("external waits the card cannot name: %s - add them to cooldownExternalFamilies "+
			"and to _FAMILY_LABELS in app/bot/commands/cooldowns.py", strings.Join(missing, ", "))
	}
	if len(stale) > 0 {
		t.Fatalf("cooldownExternalFamilies names waits nothing emits any more: %s", strings.Join(stale, ", "))
	}
}
