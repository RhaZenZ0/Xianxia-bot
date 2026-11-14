package game

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// The one-door walks (v1.41, docs/ROADMAP_2_0.md). Three rules say a value
// is written through one function - the purse through walletDeltaTx (rc.43),
// insight XP through grantInsightXPTx (rc.58), the game minute through the
// clock doors in effect_authority.go (rc.39) - and until v1.41 each was held
// by a text sweep with a per-*file* allowlist: a second write inside an
// allowlisted file passed unseen, the purse sweep could not see a DELETE or
// an INSERT INTO characters, the insight sweep never failed a stale entry,
// and the Go clock had no gate at all. These walk every function by AST and
// allowlist per *function*, with the reason, and fail an entry that no longer
// writes. The reader of each is drilled against planted source before it
// reads the tree (rc.57).

// goFunc is one production function with the file it lives in.
type goFunc struct {
	File string // the file's base name
	Path string
	Decl *ast.FuncDecl
}

// walkedFuncs parses every non-test Go file under the given directories
// (relative to this package) and returns their top-level functions.
func walkedFuncs(t *testing.T, dirs ...string) []goFunc {
	t.Helper()
	var out []goFunc
	for _, dir := range dirs {
		entries, err := os.ReadDir(dir)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			name := entry.Name()
			if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
				continue
			}
			path := filepath.Join(dir, name)
			parsed, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			for _, decl := range parsed.Decls {
				if fn, ok := decl.(*ast.FuncDecl); ok && fn.Body != nil {
					out = append(out, goFunc{File: name, Path: path, Decl: fn})
				}
			}
		}
	}
	if len(out) < 200 {
		t.Fatalf("the walk parsed only %d functions; the reader is broken, not the tree", len(out))
	}
	return out
}

// parseFuncs is walkedFuncs for planted source, for the reader drills.
func parseFuncs(t *testing.T, src string) []goFunc {
	t.Helper()
	parsed, err := parser.ParseFile(token.NewFileSet(), "planted.go", src, 0)
	if err != nil {
		t.Fatal(err)
	}
	var out []goFunc
	for _, decl := range parsed.Decls {
		if fn, ok := decl.(*ast.FuncDecl); ok && fn.Body != nil {
			out = append(out, goFunc{File: "planted.go", Path: "planted.go", Decl: fn})
		}
	}
	return out
}

// stringLiterals is every string literal in a function body, unquoted as far
// as the quote style allows (a raw string keeps its content as written).
func stringLiterals(fn *ast.FuncDecl) []string {
	var out []string
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if lit, ok := n.(*ast.BasicLit); ok && lit.Kind == token.STRING {
			out = append(out, strings.Trim(lit.Value, "`\""))
		}
		return true
	})
	return out
}

// callsNamed says whether a function body calls one of the named functions
// by bare identifier (the one-door functions are package-local).
func callsNamed(fn *ast.FuncDecl, names ...string) bool {
	found := false
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if call, ok := n.(*ast.CallExpr); ok {
			if id, ok := call.Fun.(*ast.Ident); ok {
				for _, name := range names {
					if id.Name == name {
						found = true
					}
				}
			}
		}
		return true
	})
	return found
}

// oneDoorWalk is the shape all three gates share: `writes` says whether a
// function writes the value, `door` is the function that may, `allowed` names
// the others with the reason each may, and the walk fails a writer outside
// both and an allowlist entry that no longer writes.
func oneDoorWalk(t *testing.T, what string, funcs []goFunc, writes func(goFunc) bool, door string, allowed map[string]string, mustSee ...string) {
	t.Helper()
	seen := map[string]bool{}
	var offenders []string
	for _, fn := range funcs {
		if !writes(fn) {
			continue
		}
		name := fn.Decl.Name.Name
		seen[name] = true
		if name == door {
			continue
		}
		if reason, ok := allowed[name]; ok {
			if len(strings.TrimSpace(reason)) < 20 {
				t.Errorf("%s: the allowlist entry for %s needs a reason worth reading", what, name)
			}
			continue
		}
		offenders = append(offenders, fn.File+": "+name)
	}
	sort.Strings(offenders)
	for _, name := range mustSee {
		if !seen[name] {
			t.Fatalf("%s: the walk did not see %s write; the reader is broken, not the tree", what, name)
		}
	}
	if len(offenders) > 0 {
		t.Fatalf("%s is written outside its one door (%s) by these functions; go through the door, or name the function\n"+
			"in the allowlist with the reason it may not:\n  %s", what, door, strings.Join(offenders, "\n  "))
	}
	for name := range allowed {
		if !seen[name] {
			t.Errorf("%s: the allowlist names %s, which no longer writes it; remove the entry", what, name)
		}
	}
}

// ---- the purse ----------------------------------------------------------

// A money write is a SQL string that sets the sheet's mirror on characters
// (in an UPDATE's SET list or an INSERT's column list) or touches the purse
// table at all. The old regex saw only `UPDATE characters SET ... spirit_stones`
// and `INSERT INTO|UPDATE currency_wallets`.
var (
	sheetMirrorInsertRE = regexp.MustCompile(`INSERT INTO characters\s*\([^)]*\bspirit_stones\b`)
	purseTableRE        = regexp.MustCompile(`(?:INSERT INTO|UPDATE|DELETE FROM)\s+currency_wallets\b`)
)

// setsCharacterColumn says whether a SQL string assigns the column in the SET
// list of an `UPDATE characters`. The list is cut at WHERE, so a condition
// (`WHERE spirit_stones=?`) is not an assignment, and the boundary before the
// name keeps `previous_insight_xp=` from counting as `insight_xp=` - the two
// shapes the world-exit gate's reader learned in v1.40
// (characterLocationWrites), read here through the same statement matcher.
func setsCharacterColumn(sql, column string) bool {
	assignment := regexp.MustCompile(`(?:^|[,\s])` + regexp.QuoteMeta(column) + `\s*=`)
	for _, match := range characterUpdate.FindAllStringSubmatch(sql, -1) {
		clause := match[1]
		if at := strings.Index(clause, " WHERE"); at >= 0 {
			clause = clause[:at]
		}
		if assignment.MatchString(clause) {
			return true
		}
	}
	return false
}

func writesMoney(fn goFunc) bool {
	for _, lit := range stringLiterals(fn.Decl) {
		if setsCharacterColumn(lit, "spirit_stones") || sheetMirrorInsertRE.MatchString(lit) || purseTableRE.MatchString(lit) {
			return true
		}
	}
	return false
}

// Every function that may write money outside walletDeltaTx, with the reason.
var purseWritersAllowed = map[string]string{
	"adminGrantCurrency":           "the GM's grant clamps the mirror at zero rather than refusing an overdraft, which is the lever's designed behaviour and not a player's",
	"adminBulkGrantCurrency":       "the bulk grant, the same lever over every character",
	"reverseGrantCurrency":         "an undo of the GM's grant takes back the amount it gave, clamped at zero rather than refused when play has spent below it; walletDeltaTx would refuse the overdraft and leave the lever un-undoable",
	"walletDeltaClamped":           "the clamped copy of the door the undo levers use, for the same reason",
	"reincarnateAction":            "samsara writes the whole new body from scratch, purse and mirror together, twenty-five stones in the household's coin",
	"createCharacterAuthoritative": "the twenty-five stones a new character starts with, inserted beside the creation row before any door exists to go through",
}

// One write this walk cannot see, named so it is not mistaken for held: the
// rebirth's wipe (clearIncarnationStateTx) deletes the old body's wallet rows
// through a table list (incarnationScopedTables) and a formatted DELETE, so
// no string literal in it names the purse. The list itself is held by
// TestReincarnationClearsThroughTheOneWipe and the samsara table gate.

func TestThePurseReaderSeesTheShapeItForbids(t *testing.T) {
	planted := "package game\n" +
		"func a(c C) { c.Execute(`UPDATE characters SET name=?,spirit_stones=? WHERE user_id=?`) }\n" +
		"func b(c C) { c.Execute(`INSERT INTO characters(user_id,spirit_stones) VALUES(?,?)`) }\n" +
		"func d(c C) { c.Execute(`DELETE FROM currency_wallets WHERE user_id=?`) }\n" +
		"func e(c C) { c.Execute(`SELECT spirit_stones FROM characters WHERE user_id=?`) }\n" +
		"func f(c C) { c.Execute(`UPDATE characters SET updated_at=? WHERE spirit_stones=?`) }\n"
	var saw []string
	for _, fn := range parseFuncs(t, planted) {
		if writesMoney(fn) {
			saw = append(saw, fn.Decl.Name.Name)
		}
	}
	if strings.Join(saw, ",") != "a,b,d" {
		t.Fatalf("the reader saw %v writing money; wanted a, b and d (the SET list, the INSERT list, the DELETE) and not the reads", saw)
	}
}

func TestThePurseHasOneDoor(t *testing.T) {
	funcs := walkedFuncs(t, ".", filepath.Join("..", "simulation"))
	oneDoorWalk(t, "a player's money", funcs, writesMoney, "walletDeltaTx", purseWritersAllowed,
		"walletDeltaTx", "adminGrantCurrency", "reincarnateAction", "createCharacterAuthoritative")
}

// ---- insight XP ---------------------------------------------------------

// An insight write is `insight_xp=` as an assignment in an UPDATE (never
// `previous_insight_xp=`, a different column) or `insight_xp` in an INSERT's
// column list on characters.
var insightInsertRE = regexp.MustCompile(`INSERT INTO characters\s*\([^)]*(?:^|[,\s(])insight_xp\b`)

func writesInsightXP(fn goFunc) bool {
	for _, lit := range stringLiterals(fn.Decl) {
		if setsCharacterColumn(lit, "insight_xp") || insightInsertRE.MatchString(lit) {
			return true
		}
	}
	return false
}

// Every function that may write insight_xp outside grantInsightXPTx: a GM
// lever, a third party's pay, the spends (a multiplier must never touch a
// debit), and the two absolute resets.
var insightWritersAllowed = map[string]string{
	"adminPlayerCultivationReward": "admin.player.cultivation_reward is a GM lever (v1.37.0): a grant is the number the GM typed, the exemption purseWritersAllowed makes for the GM's grant",
	"rewardMasterGo":               "pays a disciple's breakthrough to a third party, the master, inside the disciple's action; reading the master's own effect rows there is a different rule",
	"cultivationBreakthrough":      "the seized moment's reroll is a spend - a multiplier must never touch a debit",
	"lawComprehendAction":          "a spend - a multiplier must never touch a debit",
	"cultivationInsightAction":     "the insight gate's spend, as above",
	"meridianOpenAction":           "the channel-mending spend, as above",
	"reincarnateAction":            "samsara resets the column to 0 outright; an absolute write, not a grant",
	"createCharacterAuthoritative": "character creation starts the column at 0; an absolute write, not a grant",
}

func TestTheInsightReaderSeesTheShapeItForbids(t *testing.T) {
	planted := "package game\n" +
		"func a(c C) { c.Execute(`UPDATE characters SET insight_xp=insight_xp+? WHERE user_id=?`) }\n" +
		"func b(c C) { c.Execute(`UPDATE characters SET realm_index=0,insight_xp=0 WHERE user_id=?`) }\n" +
		"func d(c C) { c.Execute(`INSERT INTO characters(user_id,insight_xp) VALUES(?,0)`) }\n" +
		"func e(c C) { c.Execute(`UPDATE reincarnation_state SET previous_insight_xp=? WHERE user_id=?`) }\n" +
		"func f(c C) { c.Execute(`SELECT insight_xp FROM characters WHERE user_id=?`) }\n"
	var saw []string
	for _, fn := range parseFuncs(t, planted) {
		if writesInsightXP(fn) {
			saw = append(saw, fn.Decl.Name.Name)
		}
	}
	if strings.Join(saw, ",") != "a,b,d" {
		t.Fatalf("the reader saw %v writing insight_xp; wanted a, b and d and not the other column or the read", saw)
	}
}

func TestInsightXPHasOneDoor(t *testing.T) {
	var dirs []string
	entries, err := os.ReadDir("..")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.IsDir() {
			dirs = append(dirs, filepath.Join("..", entry.Name()))
		}
	}
	funcs := walkedFuncs(t, dirs...)
	oneDoorWalk(t, "insight_xp", funcs, writesInsightXP, "grantInsightXPTx", insightWritersAllowed,
		"grantInsightXPTx", "lawComprehendAction", "reincarnateAction", "createCharacterAuthoritative")
}

// ---- the clock ----------------------------------------------------------

// A clock read is a use of the stored anchor (`.AnchorRealTS` or
// `.AnchorGameMinute`), a use of the stored scale (`.Scale`), or a SQL string
// naming the `world_clock` row. The doors are every function in
// effect_authority.go: loadCanonicalWorldClock reads the row,
// worldClockGameMinute is the one copy of anchor-plus-elapsed-times-scale,
// realTimestampForGameMinute its inverse, and canonicalWorldGameMinute seeds.
func readsTheClock(fn goFunc) bool {
	found := false
	ast.Inspect(fn.Decl.Body, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.SelectorExpr:
			switch node.Sel.Name {
			case "AnchorRealTS", "AnchorGameMinute", "Scale":
				found = true
			}
		case *ast.BasicLit:
			if node.Kind == token.STRING && strings.Contains(node.Value, "world_clock") {
				found = true
			}
		}
		return true
	})
	return found
}

// Every function outside effect_authority.go that may touch the anchor, the
// scale or the row, with the reason. None of them computes "now": the GM
// lever repairs the row, the status query echoes it, and the rest size a
// span by the scale, which the stored real-time end then owns.
var clockReadersAllowed = map[string]string{
	"adminAdvanceTime":       "admin.world.advance_time reads the row leniently so a GM can repair a damaged one, then re-anchors through worldClockGameMinute",
	"applyWorldStatusQuery":  "world.clock echoes the anchor and the scale it read through loadCanonicalWorldClock, for the dashboard's clock card",
	"seclusionStartActionGo": "sizes a retreat's game-minute span from its real minutes and the scale; the stored ends_real_ts is the authority, so a GM's scale change never resizes a retreat (v1.0.0-rc.55)",
	"SettleSeclusionTx":      "passes the scale to the daily-gain rate and reads the retreat's end through worldClockGameMinute",
	"seclusionRealMinutes":   "the inverse span: a retreat asked for in game minutes, sized in real minutes by the scale",
	"abodeFocusActionGo":     "sizes a focus's four real hours in game minutes at the scale the focus was taken at (v1.31.0)",
}

func TestTheClockReaderSeesTheShapeItForbids(t *testing.T) {
	planted := "package game\n" +
		"func a(clock K) int64 { return clock.AnchorGameMinute + int64((now-clock.AnchorRealTS)*clock.Scale/60) }\n" +
		"func b(c C) { c.Execute(`SELECT value_json FROM world_state WHERE key='world_clock'`) }\n" +
		"func d(c C) { c.Execute(`SELECT value_json FROM world_state WHERE key='automation_settings'`) }\n"
	var saw []string
	for _, fn := range parseFuncs(t, planted) {
		if readsTheClock(fn) {
			saw = append(saw, fn.Decl.Name.Name)
		}
	}
	if strings.Join(saw, ",") != "a,b" {
		t.Fatalf("the reader saw %v reading the clock; wanted the arithmetic and the row and not another key", saw)
	}
}

func TestTheClockHasOneDoor(t *testing.T) {
	funcs := walkedFuncs(t, ".", filepath.Join("..", "simulation"))
	var outside []goFunc
	doors := 0
	for _, fn := range funcs {
		if fn.File == "effect_authority.go" {
			if readsTheClock(fn) {
				doors++
			}
			continue
		}
		outside = append(outside, fn)
	}
	if doors < 3 {
		t.Fatalf("effect_authority.go holds only %d clock readers; the doors have moved and this gate no longer knows where", doors)
	}
	// Outside the doors' file there is no door at all: every reader is an
	// allowlisted exception, so the "door" passed to the walk is a name
	// nothing carries.
	oneDoorWalk(t, "the world clock", outside, readsTheClock, "(effect_authority.go)", clockReadersAllowed,
		"adminAdvanceTime", "applyWorldStatusQuery")
}
