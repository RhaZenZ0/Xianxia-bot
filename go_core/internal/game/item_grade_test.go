package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// gradeCatalog is the shipped content, read with a Fatalf: the file is in the
// repository, so a read that fails means these tests cannot do their job.
func gradeCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load("../../../content/world.json")
	if err != nil {
		t.Fatalf("the shipped content did not load: %v", err)
	}
	if len(catalog.ItemGrades.Grades) != 5 {
		t.Fatalf("the grade ladder has %d rungs, want 5; the parse is broken, not the tree", len(catalog.ItemGrades.Grades))
	}
	return catalog
}

func TestAGradedIdResolvesToItsBase(t *testing.T) {
	catalog := gradeCatalog(t)
	base, rung, ok := itemDef(catalog, "qi_pill@high")
	if !ok || base.Name != catalog.Items["qi_pill"].Name || rung.Key != "high" {
		t.Fatalf("qi_pill@high resolved to %q at %q (ok=%v)", base.Name, rung.Key, ok)
	}
	if _, bare, ok := itemDef(catalog, "qi_pill"); !ok || bare.Key != "low" {
		t.Fatalf("the bare id must be the first rung, got %q (ok=%v)", bare.Key, ok)
	}
	for _, id := range []string{"qi_pill@legendary", "qi_pill@low", "spirit_herb@high", "no_such_item@mid"} {
		if _, _, ok := itemDef(catalog, id); ok {
			t.Errorf("%s resolved; an unknown grade, a spelled-out first rung and an ungraded item must all be refused", id)
		}
	}
	if got := itemDisplayName(catalog, "qi_pill@superior"); !strings.HasSuffix(got, "(Superior)") {
		t.Errorf("a graded item is shown as %q; the grade must be on it", got)
	}
	if got := itemDisplayName(catalog, "qi_pill"); strings.Contains(got, "(") {
		t.Errorf("a Low item is shown as %q; the first rung carries no label", got)
	}
}

func TestEveryRecipeOutputIsGradedAndNothingElseIs(t *testing.T) {
	catalog := gradeCatalog(t)
	outputs := map[string]bool{}
	for _, r := range catalog.Recipes {
		for id := range r.Output {
			outputs[id] = true
		}
	}
	if len(outputs) == 0 {
		t.Fatal("no recipe outputs; the reader is broken, not the tree")
	}
	for id := range catalog.Items {
		if got := isGradedItem(catalog, id); got != outputs[id] {
			t.Errorf("%s graded=%v, but recipe output=%v", id, got, outputs[id])
		}
	}
}

func TestTheCraftGradeFollowsTheRollAndStopsAtTheRank(t *testing.T) {
	catalog := gradeCatalog(t)
	cases := []struct {
		quality       string
		margin, rank  int64
		want, reached int
	}{
		{"ordinary", 1, 9, 0, 0},
		{"refined", 2, 9, 1, 1},
		{"fine", 3, 9, 1, 1},
		{"superior", 6, 9, 2, 2},
		{"flawless", 9, 9, 3, 3},
		{"masterwork", 12, 9, 4, 4},
		{"masterwork", 12, 5, 3, 4},
		{"superior", 6, 2, 1, 2},
		{"refined", 2, 0, 0, 1},
	}
	for _, c := range cases {
		got, reached := craftGradeIndex(catalog, c.quality, c.margin, c.rank)
		if got != c.want || reached != c.reached {
			t.Errorf("%s at margin %d, rank %d: grade %d (reached %d), want %d (reached %d)", c.quality, c.margin, c.rank, got, reached, c.want, c.reached)
		}
	}
}

func TestTheWorthOfAGradeDoublesEachRung(t *testing.T) {
	catalog := gradeCatalog(t)
	low := itemBasePrice(catalog, "qi_pill")
	if low <= 0 {
		t.Fatal("qi_pill has no base price; the reader is broken, not the tree")
	}
	for i, key := range []string{"mid", "high", "superior", "transcendent"} {
		want := low << (i + 1)
		if got := itemBasePrice(catalog, "qi_pill@"+key); got != want {
			t.Errorf("qi_pill@%s is worth %d, want %d", key, got, want)
		}
	}
	if itemEffectMult(catalog, "qi_pill@high") != 1.5 || itemEffectMult(catalog, "qi_pill") != 1 {
		t.Error("the effect multiplier is not the ladder's")
	}
}

// TestTheCatalogueIsReadByOneDoor holds production Go to itemDef. A bare
// `catalog.Items[id]` answers "unknown" for every graded id, so one missed site
// would quietly treat a High pill as nothing at all. `reward.Items` is a
// reward's own map, not the catalogue.
func TestTheCatalogueIsReadByOneDoor(t *testing.T) {
	roots := []string{".", filepath.Join("..", "simulation")}
	offenders := []string{}
	scanned := 0
	for _, root := range roots {
		entries, err := os.ReadDir(root)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			name := entry.Name()
			if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") || name == "item_grade.go" {
				continue
			}
			path := filepath.Join(root, name)
			fset := token.NewFileSet()
			file, err := parser.ParseFile(fset, path, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			scanned++
			ast.Inspect(file, func(n ast.Node) bool {
				ix, ok := n.(*ast.IndexExpr)
				if !ok {
					return true
				}
				sel, ok := ix.X.(*ast.SelectorExpr)
				if !ok || sel.Sel.Name != "Items" {
					return true
				}
				if id, ok := sel.X.(*ast.Ident); ok && id.Name == "reward" {
					return true
				}
				offenders = append(offenders, fset.Position(ix.Pos()).String())
				return true
			})
		}
	}
	if scanned < 50 {
		t.Fatalf("scanned %d files; the walk is broken, not the tree", scanned)
	}
	if len(offenders) > 0 {
		t.Fatalf("these read the item catalogue directly instead of through itemDef, so a graded id "+
			"(\"qi_pill@high\") reads as unknown there:\n  %s", strings.Join(offenders, "\n  "))
	}
}

// craftSword forges one Spirit-Iron Sword with the highest dice at the given
// Forging rank and returns the craft's result.
func craftSword(t *testing.T, rank int64) (string, map[string]any) {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	batch4TeachRecipe(t, path, 42, "Spirit-Iron Sword")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron',3),(42,'beast_core',1)`)
	batch4Exec(t, path, `INSERT OR REPLACE INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,'Forging',?,0,0)`, rank)
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	raw, _ := json.Marshal(map[string]any{"recipe": "Spirit-Iron Sword"})
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("grade-craft-%d", rank), Operation: "craft.resolve", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	return path, out.Result.(map[string]any)
}

func TestACraftStampsItsGradeAndTheRankHoldsItBack(t *testing.T) {
	path, top := craftSword(t, 7)
	if top["success"] != true || top["grade"] != "Transcendent" {
		t.Fatalf("a giant's best roll at Tier 7 made grade %v: %+v", top["grade"], top)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_iron_sword@transcendent'`)); n != 1 {
		t.Fatalf("the bag holds %d Transcendent swords, want 1", n)
	}
	_, capped := craftSword(t, 2)
	if capped["grade"] != "Mid" || capped["grade_reached"] != "Transcendent" || storage.ParseInt(capped["grade_reached_rank"]) != 7 {
		t.Fatalf("the same roll at Tier 2 must be held to Mid and say Transcendent needs Tier 7: %v / %v / %v",
			capped["grade"], capped["grade_reached"], capped["grade_reached_rank"])
	}
	if _, has := capped["output_multiplier"]; has {
		t.Fatal("the result still reports a batch multiplier; quality is spent on grade now")
	}
}

func TestAHighPillDoesOneAndAHalfTimesEverythingButPoison(t *testing.T) {
	path := setupItemUseDB(t)
	catalog := itemUseCatalog()
	catalog.ItemGrades = gradeCatalog(t).ItemGrades
	catalog.Recipes = map[string]worlddata.Recipe{"Qi Pill": {Profession: "Alchemy", Output: map[string]int64{"qi_pill": 1}}}
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(101,'qi_pill@high',1)`)
	batch4Exec(t, path, `UPDATE characters SET qi=0,qi_max=100 WHERE user_id=101`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"item_id": "qi_pill@high", "game_minute": 100})
	mut, err := itemUseActionGo(conn, catalog, 101, raw)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	out := mut.Result.(map[string]any)
	if storage.ParseInt(out["qi_restore"]) != 15 {
		t.Fatalf("a High pill restored %v qi; the Low one restores 10, so want 15", out["qi_restore"])
	}
	if !strings.HasSuffix(fmt.Sprint(out["item_name"]), "(High)") {
		t.Fatalf("the reply names the pill %q; the grade must be on it", out["item_name"])
	}
	if ends := storage.ParseInt(actionScalar(t, path, `SELECT ends_game_minute FROM active_effects WHERE user_id=101 AND source_id='qi_pill'`)); ends != 100+360 {
		t.Fatalf("the effect ends at %d; 240 minutes at x1.5 from minute 100 is 460 (and it is keyed on the base id)", ends)
	}
	var payload map[string]any
	_ = json.Unmarshal([]byte(fmt.Sprint(actionScalar(t, path, `SELECT effect_json FROM active_effects WHERE user_id=101`))), &payload)
	mod := payload["modifiers"].([]any)[0].(map[string]any)
	if v := mod["value"].(float64); v < 1.299 || v > 1.301 {
		t.Fatalf("a x1.2 modifier at x1.5 is x%v, want x1.3", v)
	}
	if storage.ParseInt(out["toxicity_gain"]) != 12 {
		t.Fatalf("a High pill is %v toxic; a stronger pill is not a cleaner one, want the base 12", out["toxicity_gain"])
	}
}

func TestBoundGearHitsAtItsGrade(t *testing.T) {
	if got := equipmentQualityMult(gradeEquipmentQuality(1.5)); got != 1.5 {
		t.Fatalf("a High item binds at a quality worth x%v, want x1.5", got)
	}
	if equipmentQualityMult(100) != 1 {
		t.Fatal("an ungraded item's quality 100 must stay x1")
	}
}

// A capital's keeper buys and sells Mid; no keeper deals above it.
func TestAKeeperDealsInLowAndMidAndRefusesTheRest(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := shopCatalog(t)
	key := "azure_crown_forge"
	shop, ok := catalog.Shops[key]
	if !ok || shop.Buys["spirit_iron_sword@mid"] != 2*shop.Buys["spirit_iron_sword"] || shop.Buys["spirit_iron_sword"] <= 0 {
		t.Fatalf("the capital forge must buy a Mid sword at twice the Low price: %v", shop.Buys)
	}
	batch4SetCanonicalGameMinute(t, path, 1000)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, shop.Location)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,?,500)`, shop.Currency)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron_sword@mid',1),(42,'spirit_iron_sword@high',1)`)

	out := batch4Result(t, batch4Apply(t, path, world, "shop.sell", 31, map[string]any{"item_id": "spirit_iron_sword@mid", "quantity": 1}))
	if storage.ParseInt(out["total"]) < shop.Buys["spirit_iron_sword@mid"] {
		t.Fatalf("a Mid sword fetched %v; the capital buys it at %d", out["total"], shop.Buys["spirit_iron_sword@mid"])
	}
	raw, _ := json.Marshal(map[string]any{"item_id": "spirit_iron_sword@high", "quantity": 1})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "grade-keeper-high", Operation: "shop.sell", ActorID: 42, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "market stall") {
		t.Fatalf("a keeper took a High sword, or refused it without saying where it sells: %v", err)
	}
	browse := shopQuery(t, path, world, "shop.browse")
	for _, line := range browse["stock"].([]map[string]any) {
		if itemGradeIndex(catalog, fmt.Sprint(line["item_id"])) >= keeperGradeCeiling {
			t.Fatalf("a capital shelf offers %v", line["item_id"])
		}
	}
}
