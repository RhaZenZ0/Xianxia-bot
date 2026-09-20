package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The modifier vocabulary nothing was holding (v1.0.0-rc.58).
//
// `active_effects` modifiers are a vocabulary with no gate: any string may be
// written as a `stat`, `loadEffectModifiers` sums it into `mods`, and nothing
// asks whether a rule ever reads it back. Content authored twenty distinct
// stats and production Go wrote twelve; seven had no reader anywhere in the
// tree. Two of the seven were near-miss names - `sense_power_bonus` on
// `space_domain` and `sense_precision_bonus` in this package's own
// `conditionEffectGo` - where the author reached for the `characters` column
// of nearly the same name instead of the modifier the reader asks for. The
// other five were simply never wired: `escape_bonus`, `detox_power`,
// `fire_resistance`, `insight_gain` and `heart_demon_resistance`.
//
// That is the mirror of the scar `property_storage_actions.go` already carries
// from rc.19, where `formation_bonus` was "a stat no rule could ever grant".
// Here it was the other way round: a stat every rule could grant and none read.
//
// **This gate has to be behavioural, and the tree contains the proof.**
// `sense_precision_bonus` appears in three production Go files - once as a
// modifier this package writes, and twice as the `characters` column of the
// same name, which is a different channel entirely. A substring grep finds all
// three and calls the stat read. So does an AST read of identifiers. Only
// driving the number says which channel an identifier belongs to. That is
// rc.52's "read calls by AST, not by substring" one level deeper.

// unreadModifierStats is a stat that reaches no rule, with the reason it is
// allowed none *for now*. Empty, and empty the day it was written: an entry
// here is a new decision, never a backlog inherited from this one.
var unreadModifierStats = map[string]string{}

// authoredModifierStats is every stat anything writes as a modifier: the
// shipped content file, walked as raw JSON so a block no Go struct parses
// still counts, plus production Go's own composite literals.
func authoredModifierStats(t *testing.T) map[string]string {
	t.Helper()
	stats := map[string]string{}

	raw, err := os.ReadFile("../../../content/world.json")
	if err != nil {
		// Not a skip. The content file is in the repository and always
		// present, so a read that fails means this gate cannot do its job -
		// and a gate that goes quiet instead of red is the decoration rc.47
		// and rc.52 each caught. Its own drill is what found this: pointing
		// the path at nothing made the whole test SKIP, green and useless.
		t.Fatalf("the vocabulary walk cannot read the content file: %v", err)
	}
	var content any
	if err := json.Unmarshal(raw, &content); err != nil {
		t.Fatalf("content/world.json does not parse: %v", err)
	}
	var walk func(node any)
	walk = func(node any) {
		switch value := node.(type) {
		case map[string]any:
			_, hasStat := value["stat"]
			_, hasOperation := value["operation"]
			if hasStat && hasOperation {
				if stat := strings.TrimSpace(fmt.Sprint(value["stat"])); stat != "" {
					stats[stat] = "content/world.json"
				}
			}
			for _, child := range value {
				walk(child)
			}
		case []any:
			for _, child := range value {
				walk(child)
			}
		}
	}
	walk(content)

	for _, path := range productionGoFiles(t) {
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		ast.Inspect(file, func(node ast.Node) bool {
			composite, ok := node.(*ast.CompositeLit)
			if !ok {
				return true
			}
			for _, element := range composite.Elts {
				kv, ok := element.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				key := ""
				switch k := kv.Key.(type) {
				case *ast.BasicLit: // map literal: {"stat": "agility", ...}
					if unquoted, err := strconv.Unquote(k.Value); err == nil {
						key = unquoted
					}
				case *ast.Ident: // struct literal: worlddata.Modifier{Stat: "agility"}
					key = k.Name
				}
				if key != "stat" && key != "Stat" {
					continue
				}
				literal, ok := kv.Value.(*ast.BasicLit)
				if !ok || literal.Kind != token.STRING {
					continue
				}
				if stat, err := strconv.Unquote(literal.Value); err == nil && stat != "" {
					if _, already := stats[stat]; !already {
						stats[stat] = filepath.Base(path)
					}
				}
			}
			return true
		})
	}

	// The self-check, before any per-stat assertion. A reader that silently
	// finds nothing makes every assertion after it vacuous (rc.57).
	if len(stats) < 18 {
		t.Fatalf("the vocabulary walk found %d modifier stats; it has stopped seeing the tree", len(stats))
	}
	for _, expected := range []string{"combat_bonus", "sense_precision", "cultivation_gain", "alchemy_bonus"} {
		if _, ok := stats[expected]; !ok {
			t.Fatalf("the vocabulary walk lost %q; it is not seeing what it thinks it is", expected)
		}
	}
	return stats
}

func productionGoFiles(t *testing.T) []string {
	t.Helper()
	var files []string
	err := filepath.Walk("..", func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		files = append(files, path)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(files)
	if len(files) < 50 {
		t.Fatalf("the production sweep found %d Go files", len(files))
	}
	return files
}

// goProductionIndexesOf reports every `x.<field>[...]` index expression in
// production Go outside the named function - the shape `TestThePurseHasOneDoor`
// uses to hold a rule to one door.
func goProductionIndexesOf(t *testing.T, field, allowedFunc string) []string {
	t.Helper()
	var offenders []string
	found := 0
	for _, path := range productionGoFiles(t) {
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			ast.Inspect(fn, func(node ast.Node) bool {
				index, ok := node.(*ast.IndexExpr)
				if !ok {
					return true
				}
				selector, ok := index.X.(*ast.SelectorExpr)
				if !ok || selector.Sel.Name != field {
					return true
				}
				found++
				if fn.Name.Name != allowedFunc {
					offenders = append(offenders, fmt.Sprintf("%s:%d in %s()",
						path, fset.Position(index.Pos()).Line, fn.Name.Name))
				}
				return true
			})
		}
	}
	if found == 0 {
		t.Fatalf("no %s index found anywhere; this gate would pass on anything", field)
	}
	return offenders
}

// modifierReaders are the four functions that fetch a modifier stat by name,
// and the position the stat sits in. Everything in the engine that reads an
// `active_effects` modifier goes through one of them.
var modifierReaders = map[string]int{
	"canonicalAdditiveEffectBonus": 5,
	"multiplicativeEffectStat":     3,
	"senseExtraModifier":           4,
	"additiveEffectJSONStat":       1,
	"additiveWorldModifiersStat":   1,
	"multiplicativeEffectJSONStat": 1,
	"mulOrOne":                     1,
}

// fetchedModifierStats is every stat production Go actually asks for: a string
// literal in the `stat` argument of a reader above, or a key on the resolved
// bundle (`mods.Add["x"]`, `mods.Mul["x"]`, `mods.value(base, "x")`).
//
// **This is why the gate is not a grep and not a name scan.**
// `sense_precision_bonus` occurs three times in production Go: once as a
// modifier this package wrote, and twice as the `characters` column of nearly
// the same name, inside SQL strings. A substring search finds all three and
// calls the stat read; so does a scan for the identifier. An argument position
// and a map key cannot be satisfied by a column in a SQL string, which is
// exactly the distinction the two rc.58 typos turned on.
func fetchedModifierStats(t *testing.T) map[string]string {
	t.Helper()
	fetched := map[string]string{}
	note := func(stat, where string) {
		if stat == "" {
			return
		}
		if _, already := fetched[stat]; !already {
			fetched[stat] = where
		}
	}
	literal := func(expr ast.Expr) string {
		basic, ok := expr.(*ast.BasicLit)
		if !ok || basic.Kind != token.STRING {
			return ""
		}
		value, err := strconv.Unquote(basic.Value)
		if err != nil {
			return ""
		}
		return value
	}

	for _, path := range productionGoFiles(t) {
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		base := filepath.Base(path)
		ast.Inspect(file, func(node ast.Node) bool {
			switch expr := node.(type) {
			case *ast.CallExpr:
				name := ""
				switch fn := expr.Fun.(type) {
				case *ast.Ident:
					name = fn.Name
				case *ast.SelectorExpr:
					name = fn.Sel.Name
				}
				if position, ok := modifierReaders[name]; ok && len(expr.Args) > position {
					note(literal(expr.Args[position]), fmt.Sprintf("%s -> %s", base, name))
				}
				// mods.value(base, "stat")
				if selector, ok := expr.Fun.(*ast.SelectorExpr); ok && selector.Sel.Name == "value" && len(expr.Args) == 2 {
					note(literal(expr.Args[1]), base+" -> mods.value")
				}
			case *ast.IndexExpr:
				// mods.Add["stat"] / mods.Mul["stat"] / mods.Set["stat"]
				if selector, ok := expr.X.(*ast.SelectorExpr); ok {
					switch selector.Sel.Name {
					case "Add", "Mul", "Set":
						note(literal(expr.Index), base+" -> mods."+selector.Sel.Name)
					}
				}
				// mods["stat"], where `mods` is the resolved bundle itself.
				if ident, ok := expr.X.(*ast.Ident); ok && (ident.Name == "mods" || ident.Name == "modifiers") {
					note(literal(expr.Index), base+" -> "+ident.Name+"[]")
				}
			}
			return true
		})
	}

	// Two indirections, each named rather than guessed at.
	//
	// `craftEffectStat(profession)` returns the stat and the result is handed
	// straight to `canonicalAdditiveEffectBonus` - so the literal is a return
	// value, not an argument. `statChoosers` is the list of functions allowed
	// to do that; an entry is a decision, and the count is checked below so
	// the list cannot silently stop matching.
	//
	// `canonicalAttribute` is the attribute channel: it ends in
	// `mods.value(base, attr)`, so every name its own `allowed` map admits is
	// fetched by construction. Reading that map is how `presence` and `heart`
	// count without anybody writing them out a second time here.
	for _, path := range productionGoFiles(t) {
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		base := filepath.Base(path)
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			if _, chooser := statChoosers[fn.Name.Name]; chooser {
				ast.Inspect(fn, func(node ast.Node) bool {
					ret, ok := node.(*ast.ReturnStmt)
					if !ok {
						return true
					}
					for _, result := range ret.Results {
						note(literal(result), fmt.Sprintf("%s -> %s()", base, fn.Name.Name))
					}
					return true
				})
			}
			if fn.Name.Name == "canonicalAttribute" {
				ast.Inspect(fn, func(node ast.Node) bool {
					composite, ok := node.(*ast.CompositeLit)
					if !ok {
						return true
					}
					for _, element := range composite.Elts {
						kv, ok := element.(*ast.KeyValueExpr)
						if !ok {
							continue
						}
						note(literal(kv.Key), base+" -> canonicalAttribute")
					}
					return true
				})
			}
		}
	}

	// The self-check again: a reader that finds nothing makes the gate vacuous.
	if len(fetched) < 10 {
		t.Fatalf("the fetch walk found %d stats; it has stopped seeing the readers", len(fetched))
	}
	// One name per shape the walk recognises, so a shape that stops matching
	// fails here rather than quietly making every stat look unread.
	for _, expected := range []string{
		"combat_bonus",     // mods.Add["..."]
		"agility",          // mods.value(base, "...")
		"cultivation_gain", // mulOrOne(mods, "...")
		"sense_precision",  // senseExtraModifier(..., "...")
		"detox_power",      // canonicalAdditiveEffectBonus(..., "...")
		"forging_bonus",    // craftEffectStat() -> a reader
		"presence",         // canonicalAttribute's own allowed map
	} {
		if _, ok := fetched[expected]; !ok {
			t.Fatalf("the fetch walk lost %q; one of its shapes has stopped matching", expected)
		}
	}
	return fetched
}

// statChoosers are the functions allowed to *return* a modifier stat rather
// than name one at a reader. Each one's result is handed straight to a reader
// in the same expression; an entry here is a decision, not a convenience.
var statChoosers = map[string]string{
	"craftEffectStat": "returns a trade's bonus stat into canonicalAdditiveEffectBonus (crafting_actions.go)",
}

// TestEveryAuthoredModifierStatIsFetchedByARule is the gate. Seven stats
// failed it before v1.0.0-rc.58, and `unreadModifierStats` is empty.
func TestEveryAuthoredModifierStatIsFetchedByARule(t *testing.T) {
	authored := authoredModifierStats(t)
	fetched := fetchedModifierStats(t)

	stats := make([]string, 0, len(authored))
	for stat := range authored {
		stats = append(stats, stat)
	}
	sort.Strings(stats)

	var orphans []string
	for _, stat := range stats {
		if reason, allowed := unreadModifierStats[stat]; allowed {
			t.Logf("%s: allowed to reach no rule - %s", stat, reason)
			continue
		}
		if _, ok := fetched[stat]; !ok {
			orphans = append(orphans, fmt.Sprintf("%s (written by %s)", stat, authored[stat]))
		}
	}
	if len(orphans) > 0 {
		t.Fatalf("modifier stats no rule ever asks for:\n  %s", strings.Join(orphans, "\n  "))
	}
}

// The other direction: a rule asking for a stat nothing writes is a rule that
// can never fire. `sense_range` and `heart` are the safe cases - read, never
// authored - so this only reports, it does not fail.
func TestARuleAskingForAStatNothingWritesIsReported(t *testing.T) {
	authored := authoredModifierStats(t)
	for stat, where := range fetchedModifierStats(t) {
		if _, ok := authored[stat]; !ok {
			t.Logf("%s is read by %s and written by nothing", stat, where)
		}
	}
}

// And the behavioural half, for the five stats v1.0.0-rc.58 wired. A fetch
// proves the number is asked for; these prove it changes an answer. Each is a
// pure rule function, so none of them rolls anything.
func TestTheWiredStatsChangeTheirRule(t *testing.T) {
	// escape_bonus rides the flee roll's modifier beside agility. The wiring
	// itself is held by the fetch gate; this is the arithmetic.
	if alchemyScorchModifier(8, 8, 0) >= alchemyScorchModifier(8, 8, 10) {
		t.Error("fire_resistance does not make the purge's flame easier to hold")
	}
	if alchemyScorchModifier(8, 8, -5) >= alchemyScorchModifier(8, 8, 0) {
		t.Error("a fire-vulnerable physique does not make it harder")
	}
	if alchemyPurgeAmount(100, 0, 0, 0) >= alchemyPurgeAmount(100, 0, 0, 40) {
		t.Error("detox_power does not burn off more")
	}
	if alchemyPurgeAmount(5, 0, 0, 40) != 5 {
		t.Error("detox_power purged more than was there")
	}
	// The scorch only exists above saturation, which is what keeps a light
	// purge exactly as free as it has always been.
	if alchemyScorchTN(pillToxicitySaturated) != alchemyScorchBaseTN {
		t.Error("the flame does not start at saturation")
	}
	if alchemyScorchTN(100) <= alchemyScorchTN(60) {
		t.Error("a heavier dose is not harder to purge")
	}
	// heart_demon_resistance is scaled, not raw: 25 on the pill is +2 on a
	// 2d10 wave, not +25.
	if got := int64(25) / heartDemonResistanceScale; got != 2 {
		t.Errorf("the Heart Calming Pill is worth %d on the wave, want 2", got)
	}
}

// insight_gain multiplies through the one door, and a positive grant never
// rounds away to nothing.
func TestInsightGainMultipliesThroughTheOneDoor(t *testing.T) {
	path := setupModifierProbeDB(t)

	plain, err := insightAfterGrant(t, path, 10)
	if err != nil {
		t.Fatal(err)
	}
	if plain != 10 {
		t.Fatalf("an ungated grant of 10 paid %d", plain)
	}
	writeProbeEffect(t, path, "insight_gain", "mul", 1.5)
	boosted, err := insightAfterGrant(t, path, 10)
	if err != nil {
		t.Fatal(err)
	}
	if boosted != 15 {
		t.Fatalf("insight_gain x1.5 on a grant of 10 paid %d, want 15", boosted)
	}

	// A multiplier must never take a reward away.
	batch4Exec(t, path, `DELETE FROM active_effects WHERE user_id=42`)
	writeProbeEffect(t, path, "insight_gain", "mul", 0.01)
	floored, err := insightAfterGrant(t, path, 1)
	if err != nil {
		t.Fatal(err)
	}
	if floored != 1 {
		t.Fatalf("a grant of 1 under x0.01 paid %d, want 1", floored)
	}
}

// insight_xp has one door, and every other writer is a named, deliberate
// absolute. `TestThePurseHasOneDoor` is the shape.
func TestInsightXPHasOneDoor(t *testing.T) {
	// Each entry is a statement that writes `characters.insight_xp` without
	// going through `grantInsightXPTx`, with the reason it may.
	insightWritersAllowed := map[string]string{
		"actions.go":             "admin.player.cultivation_reward is a GM lever: a grant is the number the GM typed, the same exemption purseWritersAllowed makes for admin.player.grant_currency",
		"cultivation_actions.go": "rewardMasterGo pays a *disciple's* breakthrough to a third party, and the reroll is a spend; reading the master's own effect rows inside a disciple's action is a different rule",
		"law_actions.go":         "a spend - a multiplier must never touch a debit",
		"cultivation_stance.go":  "the insight gate's spend, as above",
		"qi_body.go":             "the channel-mending spend, as above",
		"lifecycle_actions.go":   "samsara resets the column to 0 outright; an absolute write, not a grant",
		"authoritative.go":       "character creation starts the column at 0; an absolute write, not a grant",
		"effect_authority.go":    "the door itself",
	}
	var offenders []string
	for _, path := range productionGoFiles(t) {
		body, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(body), "insight_xp=insight_xp") &&
			!strings.Contains(string(body), "insight_xp=0") {
			continue
		}
		base := filepath.Base(path)
		if _, allowed := insightWritersAllowed[base]; !allowed {
			offenders = append(offenders, base)
		}
	}
	if len(offenders) > 0 {
		t.Fatalf("insight_xp is written outside its one door by: %s", strings.Join(offenders, ", "))
	}
	if len(insightWritersAllowed) == 0 {
		t.Fatal("the allowlist is empty; this gate would pass on anything")
	}
}

func insightAfterGrant(t *testing.T, path string, base int64) (int64, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	before := insightXPOn(t, conn)
	if _, err := grantInsightXPTx(conn, 42, base, 0); err != nil {
		return 0, err
	}
	after := insightXPOn(t, conn)
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return after - before, nil
}

func insightXPOn(t *testing.T, conn *storage.Conn) int64 {
	t.Helper()
	res, err := conn.Execute(`SELECT insight_xp FROM characters WHERE user_id=42`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		t.Fatal("no character")
	}
	return storage.ParseInt(res.Rows[0][0])
}

func setupModifierProbeDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `UPDATE characters SET insight_xp=0 WHERE user_id=42`)
	return path
}

func writeProbeEffect(t *testing.T, path, stat, operation string, value float64) {
	t.Helper()
	payload, err := json.Marshal(map[string]any{
		"name":      "probe",
		"modifiers": []map[string]any{{"stat": stat, "operation": operation, "value": value}},
	})
	if err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path,
		`INSERT INTO active_effects(user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at)
		 VALUES(42,'probe','probe','probe','probe',?,1,0,NULL,0)`, string(payload))
}

// A Soul Wound dulls the sense it is named for (v1.0.0-rc.58).
//
// `conditionEffectGo` authored `sense_precision_bonus` - the `characters`
// column - where `senseExtraModifier` asks for the bare `sense_precision`, so
// the third penalty of the one condition whose whole point is a dulled
// spiritual sense had never reached a reading. `applyCombatCondition` writes
// both `character_conditions` and an `active_effects` row carrying the same
// `effect_json`, which is why the corrected stat is live rather than merely
// spelled right.
//
// The drill for this one is the typo itself: put `sense_precision_bonus` back
// in `conditionEffectGo` and the precision below stops moving.
func TestASoulWoundDullsTheSenseItIsNamedFor(t *testing.T) {
	catalog := shippedCatalog(t)
	path := setupModifierProbeDB(t)

	precision := func() int64 {
		t.Helper()
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		got, err := senseExtraModifier(conn, catalog, 42, 0, "sense_precision")
		if err != nil {
			t.Fatal(err)
		}
		return got
	}

	before := precision()

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := applyCombatCondition(conn, 42, "soul_wound", 3, "test", "drill", 0); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			conn.Close()
			t.Fatal(err)
		}
	}
	conn.Close()

	after := precision()
	if after >= before {
		t.Fatalf("a Soul Wound left sense precision at %d (was %d): the condition's third penalty reaches no reading", after, before)
	}
	if want := before - 6; after != want {
		t.Fatalf("sense precision = %d after a severity-3 Soul Wound, want %d (-2 per severity)", after, want)
	}
}
