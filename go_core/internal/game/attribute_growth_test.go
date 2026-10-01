package game

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strings"
	"testing"
)

// Attributes grow every qi stage (v1.14.0). These drive the shipped
// catalogue: the numbers are content, and a fixture's own would prove itself.

const growthBase = `{"body":2,"agility":3,"spirit":2,"insight":1,"will":3,"presence":1}`

// TestTheSheetShowsEveryStageAndThePairTwice: nine stages behind realm 1 stage
// 1, so +9 to all six and +18 to a Sword Cultivator's agility and will.
func TestTheSheetShowsEveryStageAndThePairTwice(t *testing.T) {
	catalog := shippedCatalog(t)
	sheet := characterSheetAttributes(catalog, growthBase, pathSword, 1, 1)
	want := map[string]int64{"body": 11, "agility": 21, "spirit": 11, "insight": 10, "will": 21, "presence": 10}
	for name, n := range want {
		if sheet[name] != n {
			t.Fatalf("the sheet at realm 1 stage 1 reads %v, want %v", sheet, want)
		}
	}
	if fresh := characterSheetAttributes(catalog, growthBase, pathSword, 0, 1); fresh["will"] != 3 {
		t.Fatalf("a fresh cultivator's sheet grew already: %v", fresh)
	}
}

// TestThePathsEdgeOnARollIsCapped is the owner's call: the pair grows +2 a
// stage on the sheet, and on a roll its lead counts for at most the cap.
// Drill: drop the minI64 in pathEdge and realm 5 reads an edge of 45.
func TestThePathsEdgeOnARollIsCapped(t *testing.T) {
	catalog := shippedCatalog(t)
	capacity := catalog.AttributeGrowth.PathEdgeCap
	if capacity <= 0 {
		t.Fatal("the content carries no path_edge_cap; the reader is broken, not the tree")
	}
	for _, c := range []struct{ realm, phase, want int64 }{{0, 1, 0}, {0, 3, 2}, {0, 6, capacity}, {5, 1, capacity}, {25, 9, capacity}} {
		if got := pathEdge(catalog, c.realm, c.phase); got != c.want {
			t.Errorf("the path's edge at realm %d stage %d is %d, want %d", c.realm, c.phase, got, c.want)
		}
		roll := characterAttributes(catalog, growthBase, pathSword, c.realm, c.phase)
		kept := keptGrowth(catalog, c.realm, c.phase)
		if kept != c.realm*catalog.AttributeGrowth.KeptPerRealm || roll["will"] != 3+kept+c.want || roll["body"] != 2+kept {
			t.Errorf("a roll at realm %d stage %d reads %v; every attribute keeps %d and the pair the capped edge on top", c.realm, c.phase, roll, kept)
		}
	}
}

// TestALeadIsWhatYouHaveOutgrown: zero against your own stage, a stage a
// stage, negative against something above you.
func TestALeadIsWhatYouHaveOutgrown(t *testing.T) {
	catalog := shippedCatalog(t)
	if got := stageLead(catalog, 4, 5, 4, 5); got != 0 {
		t.Fatalf("a lead against your own stage is %d", got)
	}
	// Nine stages a realm, less the point a realm the attribute keeps.
	if got := stageLead(catalog, 4, 1, 3, 1); got != 8 {
		t.Fatalf("a realm's lead is %d, want 8", got)
	}
	if got := stageLead(catalog, 3, 1, 4, 1); got != -8 {
		t.Fatalf("a realm's deficit is %d, want -8", got)
	}
}

// TestTheOddsHoldAsAttributesGrow measures what the change does rather than
// arguing it. Written out in full - the attribute grown a stage at a time
// against a TN risen the same - a breakthrough's odds at every depth are the
// relative form's, and the path's edge is never worth more than the cap.
func TestTheOddsHoldAsAttributesGrow(t *testing.T) {
	catalog := shippedCatalog(t)
	capacity := catalog.AttributeGrowth.PathEdgeCap
	rows := []string{"realm  off-path(full)  off-path(relative)  path(relative)  path(uncapped)"}
	shallow := int64(-1)
	for _, realm := range []int64{0, 5, 12, 25} {
		tn := breakthroughTN(catalog.Realms, realm, 5)
		base := int64(3)
		d := challengeDifficulty(catalog, realm, 5)
		grown := catalog.AttributeGrowth.PerStage * qiStagesCrossed(catalog, realm, 5)
		full := breakthroughOdds(base+grown+2, tn+d)
		relative := breakthroughOdds(base+keptGrowth(catalog, realm, 5)+2, tn)
		path := breakthroughOdds(base+keptGrowth(catalog, realm, 5)+pathEdge(catalog, realm, 5)+2, tn)
		uncapped := breakthroughOdds(base+grown+2, tn)
		rows = append(rows, fmt.Sprintf("%5d  %13d%%  %17d%%  %13d%%  %13d%%", realm, full, relative, path, uncapped))
		// The content's breakthrough TNs climb a point a realm; the growth a
		// cultivator keeps must keep up, or the ladder closes as they climb.
		if shallow < 0 {
			shallow = relative
		} else if relative < shallow-10 {
			t.Fatalf("an off-path breakthrough at realm %d is %d%%, against %d%% at realm 0; the growth kept against the stage no longer keeps pace with the TN", realm, relative, shallow)
		}
		if full != relative {
			t.Fatalf("at realm %d the full form gives %d%% and the relative %d%%; they are one rule", realm, full, relative)
		}
		if path-relative > breakthroughOdds(base+keptGrowth(catalog, realm, 5)+capacity+2, tn)-relative || path < relative {
			t.Fatalf("at realm %d the path's edge is worth %d points of chance, more than the cap allows", realm, path-relative)
		}
	}
	t.Log("\n" + strings.Join(rows, "\n"))
}

// productionFuncs walks this package's non-test files once.
func productionFuncs(t *testing.T) map[string]*ast.FuncDecl {
	t.Helper()
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	fset := token.NewFileSet()
	out := map[string]*ast.FuncDecl{}
	for _, file := range files {
		if strings.HasSuffix(file, "_test.go") {
			continue
		}
		parsed, err := parser.ParseFile(fset, file, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		for _, decl := range parsed.Decls {
			if fn, ok := decl.(*ast.FuncDecl); ok {
				out[file+":"+fn.Name.Name] = fn
			}
		}
	}
	if len(out) < 500 {
		t.Fatalf("found %d production functions; the walk is broken, not the tree", len(out))
	}
	return out
}

func callsAny(fn *ast.FuncDecl, names ...string) bool {
	found := false
	ast.Inspect(fn, func(n ast.Node) bool {
		if call, ok := n.(*ast.CallExpr); ok {
			if ident, ok := call.Fun.(*ast.Ident); ok {
				for _, name := range names {
					if ident.Name == name {
						found = true
					}
				}
			}
		}
		return !found
	})
	return found
}

// TestAttributesHaveOneDoor: every function that SELECTs attributes_json reads
// it through the door. A second decoder would hand a rule the stored base
// without the path's edge - or, written back, store a computed number.
// Drill: put a bare json.Unmarshal back in canonicalAttribute and it is named.
func TestAttributesHaveOneDoor(t *testing.T) {
	doors := []string{"characterAttributes", "characterSheetAttributes", "rowAttributes", "decodeStoredAttributes"}
	// A reader that only SELECTs the row and hands it on, named with the
	// function that decodes it - which must itself go through the door.
	handsOn := map[string]string{
		"economy_actions.go:characterLocationPower":    "economy_actions.go:bountyHunterActionGo",
		"family_dao_actions.go:seclusionStartActionGo": "seclusion_environment.go:seclusionDailyGainGo",
		"family_dao_actions.go:SettleSeclusionTx":      "seclusion_environment.go:seclusionDailyGainGo",
	}
	funcs := productionFuncs(t)
	for from, to := range handsOn {
		if fn, ok := funcs[to]; !ok || !callsAny(fn, doors...) {
			t.Errorf("%s hands its row to %s, which no longer reads it through the door", from, to)
		}
	}
	readers := 0
	for name, fn := range funcs {
		if _, ok := handsOn[name]; ok {
			continue
		}
		if strings.HasPrefix(name, "attribute_growth.go:") {
			continue
		}
		selects := false
		ast.Inspect(fn, func(n ast.Node) bool {
			if lit, ok := n.(*ast.BasicLit); ok && lit.Kind == token.STRING {
				upper := strings.ToUpper(lit.Value)
				if strings.Contains(upper, "SELECT") && strings.Contains(lit.Value, "attributes_json") {
					selects = true
				}
			}
			return true
		})
		if !selects {
			continue
		}
		readers++
		if !callsAny(fn, doors...) {
			t.Errorf("%s reads attributes_json without going through the door (attribute_growth.go)", name)
		}
	}
	if readers < 8 {
		t.Fatalf("found %d readers of attributes_json; the walk is broken, not the tree", readers)
	}
}

// TestEveryFacedRollCarriesTheLead: a roll against an opponent or a content
// floor must add what the cultivator has outgrown, or a realm-20 cultivator
// would face a realm-0 beast at the odds of a realm-0 one. A roll against the
// cultivator's own stage needs nothing - the two sides cancel - and is not
// listed. Drill: drop the lead from any of these and it is named.
func TestEveryFacedRollCarriesTheLead(t *testing.T) {
	faced := map[string]string{
		"combat_actions.go:combatTurnAction":               "a battle opponent",
		"combat_actions.go:combatTechniqueAction":          "a battle opponent",
		"group_combat_actions.go:bossActActionGo":          "a raid boss",
		"pvp_actions.go:pvpActAction":                      "another cultivator",
		"secret_realm_actions.go:secretRealmExploreAction": "a realm's floor",
		"flames.go:flameCaptureAction":                     "a flame's floor",
		"law_actions.go:lawComprehendAction":               "a Law's floor",
	}
	funcs := productionFuncs(t)
	for name, what := range faced {
		fn, ok := funcs[name]
		if !ok {
			t.Errorf("%s is gone; the roll against %s moved and this list did not", name, what)
			continue
		}
		if !callsAny(fn, "stageLead") {
			t.Errorf("%s rolls against %s without stageLead, so the stages outgrown count for nothing", name, what)
		}
	}
}
