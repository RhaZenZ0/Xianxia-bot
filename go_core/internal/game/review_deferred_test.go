package game

// The v1.2.1 review's deferred claims, each confirmed and held (v1.2.3).

import (
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A qi-gathering array is qi-path weather at both doors: the sect manor's
// array counts for nothing on a body retreat, and a deployed array counts for
// nothing in a hand-sat body session.
func TestAQiGatheringArrayIsQiPathWeatherAtBothDoors(t *testing.T) {
	path := setupSectResidenceDB(t)
	world := batch4WorldPath(t)
	setMembership(t, path, "Deacon", 40, 0)
	batch4Exec(t, path, `UPDATE sect_abodes SET cultivation_level=3 WHERE user_id=42`)
	batch4Exec(t, path, `UPDATE characters SET location='sect_abode:42' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO sect_manors(sect_name,name,base_location,qi_array_level) VALUES('Azure Cloud Sect','Azure Hall','Cloudspine Foothills',1)`)
	result := batch4Result(t, batch4Apply(t, path, world, "seclusion.start", 1, map[string]any{"mode": "body", "duration_real_minutes": 120, "game_minute": 1000}))
	if got := parseFloat(result["environment_mult"]); !nearly(got, 1.20) {
		t.Fatalf("a body retreat under the manor's qi array is priced at %v, want 1.20: a hand-sat body session applies no manor", got)
	}

	conn := mustOpen(t, path)
	defer conn.Close()
	if _, err := conn.Execute(`CREATE TABLE IF NOT EXISTS deployed_location_arrays(location TEXT,item_id TEXT,name TEXT,effect_json TEXT,starts_game_minute INTEGER,ends_game_minute INTEGER)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO deployed_location_arrays(location,item_id,name,effect_json,starts_game_minute,ends_game_minute) VALUES('Spirit Jade Capital','minor_qi_gathering_array_disk','Minor Qi Gathering Array','{"modifiers":[{"stat":"cultivation_gain","operation":"mul","value":1.10}]}',0,5000)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		body bool
		want float64
	}{{false, 1.10}, {true, 1.0}} {
		name, mult, err := placeMultiplierForPath(conn, shopCatalog(t), 42, "Spirit Jade Capital", 1000, tc.body)
		if err != nil {
			t.Fatal(err)
		}
		if !nearly(mult, tc.want) {
			t.Fatalf("body=%v: the ground is priced at %v (%q), want %v: a deployed qi array is qi-path weather at the seclusion door", tc.body, mult, name, tc.want)
		}
	}
}

// Both of a player's turns defend against the counter-attack with one TN.
func TestBothTurnsDefendWithOneCounterTN(t *testing.T) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "combat_actions.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	sites := map[string]int{}
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || (fn.Name.Name != "combatTurnAction" && fn.Name.Name != "combatTechniqueAction") {
			continue
		}
		ast.Inspect(fn, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "roll2d10" && len(call.Args) == 2 {
				if inner, ok := call.Args[1].(*ast.CallExpr); ok {
					if fid, ok := inner.Fun.(*ast.Ident); ok && fid.Name == "counterDefenceTN" && len(inner.Args) == 6 {
						sites[fn.Name.Name]++
					}
				}
			}
			return true
		})
	}
	for _, name := range []string{"combatTurnAction", "combatTechniqueAction"} {
		if sites[name] == 0 {
			t.Fatalf("%s rolls its counter-attack against a TN of its own rather than counterDefenceTN", name)
		}
	}
}

// Undo, redo, undo: the chain is walked to the original, in the direction its
// length says.
func TestAnUndoCanBeUndoneAgain(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.karma", map[string]any{"user_id": 42, "delta": 10, "reason": "reward"})
	karma := func() int64 {
		return storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42"))
	}
	undoLast(t, path)
	if got := karma(); got != 5 {
		t.Fatalf("after undo karma=%d want 5", got)
	}
	undoLast(t, path)
	if got := karma(); got != 15 {
		t.Fatalf("after redo karma=%d want 15", got)
	}
	if err := applyAdminErr(t, path, "admin.audit.undo_last", map[string]any{"reason": "GM undo"}); err != nil {
		t.Fatalf("a third undo refused: %v", err)
	}
	if got := karma(); got != 5 {
		t.Fatalf("after undo, redo, undo karma=%d want 5", got)
	}
	if got := lastAuditAction(t, path); got != "admin.audit.undo_last" {
		t.Fatalf("last audit action=%q", got)
	}
	if !strings.Contains(scalar(t, path, "SELECT before_json FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1").(string), "undo again") {
		t.Fatal("the audit row does not say the original was undone again")
	}
}

// A travelling merchant's wares are a shelf the rank ceiling must see.
func TestAMerchantsWaresAreAShelfToo(t *testing.T) {
	catalog := shopCatalog(t)
	checked := 0
	for key, merchant := range catalog.Merchants {
		for _, ware := range merchant.Wares {
			if itemTrade(catalog, ware.ItemID) == "" {
				continue
			}
			for _, shop := range catalog.Shops {
				base, buys := shop.Buys[ware.ItemID]
				if !buys || shop.Currency != merchant.Currency {
					continue
				}
				paid := tradeRankSellPrice(catalog, ware.ItemID, shop.Currency, max64(1, base), saintRank)
				if paid >= ware.Price {
					t.Fatalf("a keeper pays a Saint %d for %s, and %s sells it for %d", paid, ware.ItemID, key, ware.Price)
				}
				checked++
			}
		}
	}
	if checked == 0 {
		t.Fatal("no merchant ware is bought by any keeper; the walk sees nothing")
	}
}
