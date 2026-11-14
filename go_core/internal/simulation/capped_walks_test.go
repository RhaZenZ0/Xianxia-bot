package simulation

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
)

// Every capped walk over a name-ordered roster starts somewhere new each tick
// (v1.34.0), held over the class since v1.41.
//
// roster_turn_test.go holds five walks one at a time, each driven through its
// real system. What nothing asked was whether there were other walks: the
// v1.41 inventory read every ORDER BY and every cap in this package and found
// four more of the same shape - the one who goes missing (cap 1), the couples
// who begin courting (cap 8), the disciples who leave a crumbling sect (cap
// 12), and the childbirth walk itself, which turned its roster only after a
// `LIMIT 150` had cut it in name order - and a fifth that is the same fault by
// another door, the coming-of-age window. The gate at the bottom of this file
// reads the shape; the tests above it hold three of the fixes by what happens.

// A wanderer far down the alphabet can be the one who vanishes. With every
// roll landing, the one who goes missing is whoever the tick starts at, so a
// walk that starts at the top names the alphabet's first wanderer every time.
func TestTheOneWhoVanishesIsWhereTheRosterTurns(t *testing.T) {
	path := romanceDB(t)
	r := romanceRunner()
	names := make([]string, 30)
	for i := range names {
		names[i] = fmt.Sprintf("Wanderer %02d", i)
		missingNPC(t, path, names[i], "Greenriver Town", "Riverguard City", 0, "alive")
	}
	defer gamerng.UseRoller(func(int) int { return 0 })()
	const gm = int64(50000)
	start := int(hash64("roster-turn", fmt.Sprint(gm)) % uint64(len(names)))
	if start == 0 {
		t.Fatalf("minute %d turns the roster to its top, where an unturned walk also starts; pick another minute", gm)
	}
	conn := livesConn(t, path)
	vanished, _, err := r.npcDisappearances(conn, gm)
	if err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if vanished != missingCap {
		t.Fatalf("thirty wanderers and a die that always lands lost %d, cap is %d", vanished, missingCap)
	}
	gone := romanceStr(t, path, `SELECT npc_name FROM npc_civilization_state WHERE status='missing'`)
	if gone != names[start] {
		t.Fatalf("the tick at minute %d starts at %s (roster position %d) and %s vanished - the walk is not turning",
			gm, names[start], start, gone)
	}
}

// A couple past the hundred-and-fiftieth by name bears. The roster turned, but
// only inside the first hundred and fifty names the query's LIMIT let through.
func TestACouplePastTheOldLimitBears(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	couples := make([]string, 160)
	for i := range couples {
		a, b := fmt.Sprintf("Chen%03d A", i), fmt.Sprintf("Chen%03d B", i)
		couples[i] = a
		addNPC(t, path, a, "Greenriver Town", "Greenriver Town", "Mortal World", "Farmer", 50, "Independent")
		addNPC(t, path, b, "Greenriver Town", "Greenriver Town", "Mortal World", "Farmer", 50, "Independent")
		addLife(t, path, a, map[string]any{"relationship_status": "married", "spouse_name": b, "health": 90})
		addLife(t, path, b, map[string]any{"relationship_status": "married", "spouse_name": a, "health": 90})
	}
	defer gamerng.UseRoller(func(int) int { return 0 })()
	// A minute whose turn starts past the old limit, found rather than
	// hard-coded so the test says what it needs of the minute.
	gm, start := int64(0), 0
	for candidate := int64(50000); candidate < 51000; candidate++ {
		at := int(hash64("roster-turn", fmt.Sprint(candidate)) % uint64(len(couples)))
		if at >= 150 {
			gm, start = candidate, at
			break
		}
	}
	if gm == 0 {
		t.Fatal("no minute in a thousand turns the roster past position 150; the turn has changed")
	}
	conn := livesConn(t, path)
	if _, err := r.npcChildbirth(conn, 4, gm); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	res, err := conn.Execute(`SELECT children_count FROM npc_life_state WHERE npc_name=?`, []any{couples[start]})
	if err != nil || len(res.Rows) == 0 {
		t.Fatalf("read %s: %v", couples[start], err)
	}
	if i64(res.Rows[0][0]) == 0 {
		t.Fatalf("the tick at minute %d starts at %s (roster position %d of %d) and that couple did not bear: the roster is cut before it turns",
			gm, couples[start], start, len(couples))
	}
}

// Six children with nowhere to grow up do not stop the seventh. They are left
// in place for a later tick, which is right, and they used to fill the whole
// six-row window the query read, every tick, for ever.
func TestSixOrphansDoNotStopTheNextChildGrowingUp(t *testing.T) {
	path := maturationDB(t)
	r := maturationRunner()
	born := int64(1000)
	for i := 0; i < int(maturityCap); i++ {
		bornTo(t, path, fmt.Sprintf("Nobody's Child %d", i), "A Parent Who Does Not Exist", born)
	}
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 40, "married")
	bornTo(t, path, "Bao Xiu", "Bao Lin", born+1)
	conn := livesConn(t, path)
	got, err := r.npcMaturation(conn, born+1+maturityYears*minutesPerYear)
	if err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got != 1 {
		t.Fatalf("%d came of age behind six orphans, wanted the one child with a household", got)
	}
	if s := romanceStr(t, path, `SELECT generated_as_npc FROM npc_descendants WHERE child_name='Bao Xiu'`); s != "1" {
		t.Fatalf("Bao Xiu is grown and has a home and did not come of age: generated_as_npc=%q", s)
	}
}

// ---- the gate -------------------------------------------------------------

// nameOrderColumns are the columns whose order is a stable roster rather than
// a priority: sorting by one and cutting at a cap gives the same people every
// tick. A score, a date or a grudge is a priority, and its top N is the design.
var nameOrderColumns = map[string]bool{
	"npc_name": true, "child_name": true, "name": true, "sect_name": true,
	"family_name": true, "family_id": true, "user_id": true,
}

var (
	orderByFirstKey = regexp.MustCompile(`ORDER BY\s+(?:\w+\.)?(\w+)`)
	sqlLimit        = regexp.MustCompile(`LIMIT\s+(\?|\d+)`)
)

// cappedWalk describes one function: whether it reads a roster in name order,
// whether something cuts the walk short, and whether it turns the roster.
type cappedWalk struct {
	NameOrdered bool
	Capped      bool
	Turns       bool
}

func readCappedWalk(fn *ast.FuncDecl) cappedWalk {
	var walk cappedWalk
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.BasicLit:
			if node.Kind != token.STRING {
				return true
			}
			if m := orderByFirstKey.FindStringSubmatch(node.Value); m != nil && nameOrderColumns[m[1]] {
				walk.NameOrdered = true
			}
			if m := sqlLimit.FindStringSubmatch(node.Value); m != nil {
				// `LIMIT 1` is a probe or a single pick, not a walk.
				if n, err := strconv.Atoi(m[1]); err != nil || n > 1 {
					walk.Capped = true
				}
			}
		case *ast.CallExpr:
			fun := node.Fun
			if index, ok := fun.(*ast.IndexExpr); ok {
				fun = index.X
			}
			if id, ok := fun.(*ast.Ident); ok && id.Name == "rosterTurn" {
				walk.Turns = true
			}
		case *ast.RangeStmt:
			if breaksOnACap(node.Body) {
				walk.Capped = true
			}
		case *ast.ForStmt:
			if breaksOnACap(node.Body) {
				walk.Capped = true
			}
		}
		return true
	})
	return walk
}

// breaksOnACap is a loop body that carries `if made >= cap { break }` among
// its own statements - the Go-side cut every capped walk here uses.
func breaksOnACap(body *ast.BlockStmt) bool {
	for _, stmt := range body.List {
		cond, ok := stmt.(*ast.IfStmt)
		if !ok || len(cond.Body.List) != 1 {
			continue
		}
		branch, ok := cond.Body.List[0].(*ast.BranchStmt)
		if !ok || branch.Tok != token.BREAK {
			continue
		}
		if cmp, ok := cond.Cond.(*ast.BinaryExpr); ok && (cmp.Op == token.GEQ || cmp.Op == token.GTR) {
			return true
		}
	}
	return false
}

func TestTheCappedWalkReaderSeesTheShapeItForbids(t *testing.T) {
	planted := "package simulation\n" +
		"func stuck(c C) { rows := c.Q(`SELECT npc_name FROM t ORDER BY npc_name`); for _, r := range rows { if made >= cap { break }; use(r) } }\n" +
		"func cut(c C) { rows := c.Q(`SELECT npc_name FROM t ORDER BY c.npc_name LIMIT 120`); for _, r := range rows { use(r) } }\n" +
		"func turns(c C) { rows := c.Q(`SELECT npc_name FROM t ORDER BY npc_name`); for _, r := range rosterTurn(rows, gm) { if made >= cap { break }; use(r) } }\n" +
		"func priority(c C) { rows := c.Q(`SELECT npc_name FROM t ORDER BY grudge DESC LIMIT 40`); for _, r := range rows { if made >= cap { break }; use(r) } }\n" +
		"func whole(c C) { rows := c.Q(`SELECT npc_name FROM t ORDER BY npc_name`); for _, r := range rows { use(r) } }\n" +
		"func probe(c C) { c.Q(`SELECT npc_name FROM t ORDER BY npc_name LIMIT 1`) }\n"
	parsed, err := parser.ParseFile(token.NewFileSet(), "planted.go", planted, 0)
	if err != nil {
		t.Fatal(err)
	}
	var stuck []string
	for _, decl := range parsed.Decls {
		fn := decl.(*ast.FuncDecl)
		walk := readCappedWalk(fn)
		if walk.NameOrdered && walk.Capped && !walk.Turns {
			stuck = append(stuck, fn.Name.Name)
		}
	}
	if strings.Join(stuck, ",") != "stuck,cut" {
		t.Fatalf("the reader called %v stuck; wanted the Go-side cap and the SQL LIMIT, and not the turned walk, the priority order, the whole walk or the probe", stuck)
	}
}

func TestEveryCappedNameOrderedWalkTurns(t *testing.T) {
	// A capped walk in name order that does not turn, with the reason it need
	// not: the walk drains, so the people it reaches leave the roster.
	allowed := map[string]string{}
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	var stuck []string
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		parsed, err := parser.ParseFile(token.NewFileSet(), name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			walk := readCappedWalk(fn)
			if !walk.NameOrdered || !walk.Capped {
				continue
			}
			seen[fn.Name.Name] = true
			if walk.Turns {
				continue
			}
			if _, ok := allowed[fn.Name.Name]; ok {
				continue
			}
			stuck = append(stuck, name+": "+fn.Name.Name)
		}
	}
	for _, want := range []string{"npcTravel", "npcChildbirth", "npcDisappearances", "beginCourtships", "npcSectChanges"} {
		if !seen[want] {
			t.Fatalf("the walk did not see %s as a capped walk in name order; the reader is broken, not the tree (saw %v)", want, keysOf(seen))
		}
	}
	sort.Strings(stuck)
	if len(stuck) > 0 {
		t.Fatalf("these read a roster in name order and cut it at a cap without turning it, so the same names take every turn\n"+
			"and the rest of the world never acts - walk rosterTurn(rows, gm), or name the function in `allowed` with why it drains:\n  %s",
			strings.Join(stuck, "\n  "))
	}
	for name, reason := range allowed {
		if !seen[name] {
			t.Errorf("`allowed` names %s, which is no longer a capped walk in name order; remove the entry", name)
		}
		if len(strings.TrimSpace(reason)) < 20 {
			t.Errorf("`allowed[%s]` needs a reason worth reading", name)
		}
	}
}

// ---- a batch never returns an error over one row's data --------------------

// `RunDue` returns on the first error and takes every system ordered after it
// (rc.28), so an error a step *makes* inside its row loop - a refusal about
// one row, as opposed to a database error it passes on - ends the tick over
// that row, every tick. The v1.41 inventory read every row loop in the eight
// systems: the returns inside them pass on a failed write, a failed roll or an
// engine call, and one constructed its own, a clan relation with no opening
// score. This reads the class: no function in the package returns an error it
// constructed (errors.New, or fmt.Errorf with no %w) from inside a loop, nor
// calls, inside a loop, a function of this package that constructs one.
//
// It is a shape detector with a stated reach: it follows one call, within
// this package. A refusal made two calls down, or in the game package, is not
// seen, and a failed write over one row's data (a foreign key, as
// npc_consignments was) cannot be told from a failed connection by reading.

func constructsAnError(expr ast.Expr) bool {
	call, ok := expr.(*ast.CallExpr)
	if !ok {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	pkg, ok := sel.X.(*ast.Ident)
	if !ok {
		return false
	}
	if pkg.Name == "errors" && sel.Sel.Name == "New" {
		return true
	}
	if pkg.Name == "fmt" && sel.Sel.Name == "Errorf" && len(call.Args) > 0 {
		if lit, ok := call.Args[0].(*ast.BasicLit); ok && !strings.Contains(lit.Value, "%w") {
			return true
		}
	}
	return false
}

// returnsAnErrorItMade says whether a block returns a constructed error,
// without descending into a function literal.
func returnsAnErrorItMade(n ast.Node) bool {
	found := false
	ast.Inspect(n, func(x ast.Node) bool {
		if _, ok := x.(*ast.FuncLit); ok {
			return false
		}
		if ret, ok := x.(*ast.ReturnStmt); ok {
			for _, result := range ret.Results {
				if constructsAnError(result) {
					found = true
				}
			}
		}
		return true
	})
	return found
}

// batchRefusals lists, for a set of parsed functions, every "caller" or
// "caller -> callee" that makes an error over one row inside a loop.
func batchRefusals(funcs map[string]*ast.FuncDecl) []string {
	refuses := map[string]bool{}
	for name, fn := range funcs {
		if returnsAnErrorItMade(fn.Body) {
			refuses[name] = true
		}
	}
	found := map[string]bool{}
	for name, fn := range funcs {
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			var body *ast.BlockStmt
			switch loop := n.(type) {
			case *ast.RangeStmt:
				body = loop.Body
			case *ast.ForStmt:
				body = loop.Body
			}
			if body == nil {
				return true
			}
			if returnsAnErrorItMade(body) {
				found[name] = true
			}
			ast.Inspect(body, func(x ast.Node) bool {
				call, ok := x.(*ast.CallExpr)
				if !ok {
					return true
				}
				callee := ""
				switch fun := call.Fun.(type) {
				case *ast.Ident:
					callee = fun.Name
				case *ast.SelectorExpr:
					// A method on the runner: `r.step(...)`.
					if recv, ok := fun.X.(*ast.Ident); ok && recv.Name == "r" {
						callee = fun.Sel.Name
					}
				}
				if callee != "" && refuses[callee] {
					found[name+" -> "+callee] = true
				}
				return true
			})
			return true
		})
	}
	return keysOf(found)
}

func parseSimulationFuncs(t *testing.T, sources map[string]string) map[string]*ast.FuncDecl {
	t.Helper()
	funcs := map[string]*ast.FuncDecl{}
	for name, src := range sources {
		var parsed *ast.File
		var err error
		if src == "" {
			parsed, err = parser.ParseFile(token.NewFileSet(), name, nil, 0)
		} else {
			parsed, err = parser.ParseFile(token.NewFileSet(), name, src, 0)
		}
		if err != nil {
			t.Fatal(err)
		}
		for _, decl := range parsed.Decls {
			if fn, ok := decl.(*ast.FuncDecl); ok && fn.Body != nil {
				funcs[fn.Name.Name] = fn
			}
		}
	}
	return funcs
}

func TestTheBatchRefusalReaderSeesTheShapeItForbids(t *testing.T) {
	planted := "package simulation\n" +
		"func direct(rows []R) error { for _, row := range rows { if row.bad { return fmt.Errorf(\"row %v is bad\", row) } }; return nil }\n" +
		"func refuses(row R) error { if row.bad { return errors.New(\"bad row\") }; return nil }\n" +
		"func through(r *Runner, rows []R) error { for _, row := range rows { if err := r.refuses(row); err != nil { return err } }; return nil }\n" +
		"func passesOn(c C, rows []R) error { for _, row := range rows { if _, err := c.Execute(row.sql); err != nil { return fmt.Errorf(\"write: %w\", err) } }; return nil }\n" +
		"func skips(rows []R) error { for _, row := range rows { if row.bad { continue } }; return nil }\n"
	got := batchRefusals(parseSimulationFuncs(t, map[string]string{"planted.go": planted}))
	if strings.Join(got, "; ") != "direct; through -> refuses" {
		t.Fatalf("the reader saw %v; wanted the error made in the loop and the one made one call down, and not the wrapped write or the skip", got)
	}
}

func TestNoBatchReturnsAnErrorOverOneRow(t *testing.T) {
	// The loops that may, each with the reason.
	allowed := map[string]string{
		"RunDue -> runDueSystem": "the tick itself: an unknown system or a refused anchor is the caller's error, and the first error ending the tick is the rule this gate serves",
	}
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	sources := map[string]string{}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		sources[name] = ""
	}
	funcs := parseSimulationFuncs(t, sources)
	if len(funcs) < 150 {
		t.Fatalf("the walk parsed only %d functions; the reader is broken, not the tree", len(funcs))
	}
	seen := map[string]bool{}
	var offenders []string
	for _, refusal := range batchRefusals(funcs) {
		seen[refusal] = true
		if _, ok := allowed[refusal]; !ok {
			offenders = append(offenders, refusal)
		}
	}
	if len(offenders) > 0 {
		t.Fatalf("these make an error over one row inside a loop, so one bad row ends the tick and every system ordered after it,\n"+
			"every tick - skip the row (continue, or answer \"not done\"), or name the loop in `allowed` with the reason:\n  %s",
			strings.Join(offenders, "\n  "))
	}
	for name := range allowed {
		if !seen[name] {
			t.Errorf("`allowed` names %q, which no longer makes an error inside a loop; remove the entry", name)
		}
	}
}
