package simulation

import (
	"go/ast"
	"go/parser"
	"go/token"
	"strconv"
	"testing"

	"xianxia/core/internal/worlddata"
)

// v1.29.0: the era modifiers reached a player's rules and three sweeps, and
// nothing the world's own people did. Each NPC roll now asks the age of the
// world it is rolled in, through eraChance, under the key its era is about.
func TestEveryNPCRollAsksItsEra(t *testing.T) {
	want := map[string]string{
		"npcSectClaims":    "war_pressure",
		"npcSectWars":      "war_pressure",
		"npcCrimes":        "crime_pressure",
		"npcBeastHunts":    "beast_encounter_rate",
		"npcBreakthroughs": "cultivation_gain",
	}
	found := map[string]string{}
	for _, file := range []string{"sect_claims.go", "sect_wars.go", "npc_deeds.go", "npc_lives.go"} {
		parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
		if err != nil {
			t.Fatalf("cannot parse %s: %v", file, err)
		}
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			if _, wanted := want[fn.Name.Name]; !wanted {
				continue
			}
			ast.Inspect(fn, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				sel, ok := call.Fun.(*ast.SelectorExpr)
				if !ok || sel.Sel.Name != "eraChance" || len(call.Args) < 3 {
					return true
				}
				if lit, ok := call.Args[2].(*ast.BasicLit); ok {
					found[fn.Name.Name], _ = strconv.Unquote(lit.Value)
				}
				return true
			})
		}
	}
	for fn, key := range want {
		if found[fn] != key {
			t.Errorf("%s asks the era for %q, want %q", fn, found[fn], key)
		}
	}
}

func TestAnEraMovesAChanceButNeverEndsIt(t *testing.T) {
	r := &Runner{World: worlddata.Catalog{Locations: map[string]worlddata.LocationDefinition{"Town": {World: "Mortal World"}}}}
	byWorld := map[string]map[string]float64{"Mortal World": {"war_pressure": 1.5, "crime_pressure": 0.01}}
	if got := r.eraChance(byWorld, "Town", "war_pressure", 12); got != 18 {
		t.Fatalf("an era of open war took a 12%% chance to %d, want 18", got)
	}
	if got := r.eraChance(byWorld, "Town", "crime_pressure", 20); got != 5 {
		t.Fatalf("a damping era took a 20%% chance to %d, want the floor's 5", got)
	}
	if got := r.eraChance(byWorld, "Town", "cultivation_gain", 18); got != 18 {
		t.Fatalf("an era with no term moved a chance to %d", got)
	}
}

// v1.29.0: a death at the world's own hands marks its region and sect - the
// robbery that ends in a body and the feud settled with a blade.
func TestTheWorldsOwnKillingsLeaveAMark(t *testing.T) {
	want := map[string]string{"npcCrimes": "npc_deeds.go", "npcFeuds": "npc_lives.go"}
	for fn, file := range want {
		parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
		if err != nil {
			t.Fatalf("cannot parse %s: %v", file, err)
		}
		seen, marked := false, false
		for _, decl := range parsed.Decls {
			f, ok := decl.(*ast.FuncDecl)
			if !ok || f.Name.Name != fn {
				continue
			}
			seen = true
			ast.Inspect(f, func(n ast.Node) bool {
				if call, ok := n.(*ast.CallExpr); ok {
					if sel, ok := call.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "markNPCKilling" {
						marked = true
					}
				}
				return true
			})
		}
		if !seen {
			t.Fatalf("%s no longer declares %s; the gate is broken, not the tree", file, fn)
		}
		if !marked {
			t.Errorf("%s (%s) kills somebody and marks neither their region nor their sect", fn, file)
		}
	}
}

// v1.29.0: a city's prosperity scales the town's chance to buy at a stall.
func TestAThrivingCityBuysMoreAtItsStalls(t *testing.T) {
	if got := stallChanceAtProsperity(40, 50); got != 40 {
		t.Fatalf("a city at the middle buys at %d, want the old 40", got)
	}
	if got := stallChanceAtProsperity(40, 90); got != 72 {
		t.Fatalf("a thriving city buys at %d, want 72", got)
	}
	if got := stallChanceAtProsperity(40, 10); got != 8 {
		t.Fatalf("a failing city buys at %d, want 8", got)
	}
	if got := stallChanceAtProsperity(70, 95); got != 95 {
		t.Fatalf("a chance went past certain: %d", got)
	}
}
