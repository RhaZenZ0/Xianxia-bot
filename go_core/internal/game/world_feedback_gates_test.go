package game

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"
)

// callsIn answers, for each named function in a file, the names of the
// functions it calls in source order - a plain identifier or a selector's
// last part.
func callsIn(t *testing.T, file string, funcs ...string) map[string][]string {
	t.Helper()
	parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
	if err != nil {
		t.Fatalf("cannot parse %s: %v", file, err)
	}
	want := map[string]bool{}
	for _, f := range funcs {
		want[f] = true
	}
	out := map[string][]string{}
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || !want[fn.Name.Name] {
			continue
		}
		out[fn.Name.Name] = []string{}
		ast.Inspect(fn, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			switch f := call.Fun.(type) {
			case *ast.Ident:
				out[fn.Name.Name] = append(out[fn.Name.Name], f.Name)
			case *ast.SelectorExpr:
				out[fn.Name.Name] = append(out[fn.Name.Name], f.Sel.Name)
			}
			return true
		})
	}
	for _, f := range funcs {
		if _, ok := out[f]; !ok {
			t.Fatalf("%s no longer declares %s; the gate is broken, not the tree", file, f)
		}
	}
	return out
}

func indexOf(calls []string, name string) int {
	for i, c := range calls {
		if c == name {
			return i
		}
	}
	return -1
}

// v1.29.0: a manor lends its sect nothing while a rival holds its ground, at
// every door a manor bonus is read through. A helper's own test passes against
// a tree nothing calls it from (TestEveryGoodDeedIsPaidWhereItHappens).
func TestEveryManorBonusAsksWhoHoldsTheGround(t *testing.T) {
	for file, fn := range map[string]string{
		"cultivation_actions.go":   "manorCultivationMultiplier",
		"crafting_actions.go":      "canonicalCraftManorBonus",
		"seclusion_environment.go": "seclusionEnvironmentGo",
	} {
		if indexOf(callsIn(t, file, fn)[fn], "ManorGroundTakenTx") < 0 {
			t.Errorf("%s (%s) reads a manor's bonus without asking whether a rival holds its ground", fn, file)
		}
	}
}

// v1.29.0: a player's kill marks the place and the sect through the one
// statement the world's own killings use, and reads the dead's kin before the
// widowing takes the spouse's name off the row it reads.
func TestAKillReadsTheKinBeforeTheWidowing(t *testing.T) {
	calls := callsIn(t, "combat_aftermath.go", "applyCombatAftermathTx")["applyCombatAftermathTx"]
	kin, release, remember := indexOf(calls, "kinOfTx"), indexOf(calls, "ReleaseNPCBondsTx"), indexOf(calls, "RememberTheKillerTx")
	if kin < 0 || remember < 0 {
		t.Fatal("a player's kill no longer tells the dead's kin who killed them")
	}
	if release < 0 || kin > release {
		t.Fatal("the kin are read after ReleaseNPCBondsTx, which has already widowed the spouse - so a widow never remembers")
	}
	if indexOf(calls, "MarkKillingTx") < 0 {
		t.Fatal("a player's kill marks its region and sect without MarkKillingTx; the world's own killings and a player's would drift")
	}
}

// v1.29.0: a market counter is trade like any shelf, and moves its city.
func TestAMarketTradeMovesItsCity(t *testing.T) {
	if indexOf(callsIn(t, "economy_actions.go", "marketTradeAction")["marketTradeAction"], "nudgeCityProsperityTx") < 0 {
		t.Fatal("market.trade sells and buys without moving the city's prosperity, the one sale in the game that does not")
	}
}
