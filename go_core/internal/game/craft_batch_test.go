package game

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// A batch (v1.21.0) is N crafts in one action: the materials for all of them
// are taken first, every unit rolls on its own, and each one leaves the record
// a single press would have left.

func craftBatchApply(t *testing.T, path, world, id string, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(payload)
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: id, Operation: "craft.resolve", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	return out.Result.(map[string]any), nil
}

func TestABatchIsThatManyCraftsAndEachIsRecorded(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',7),(42,'beast_core',4)`)

	// The fixture's sheet is a giant's, so every unit lands against TN 12.
	result, err := craftBatchApply(t, path, world, "batch-three", map[string]any{"recipe": "Recovery Pill", "quantity": 3})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["successes"]); got != 3 {
		t.Fatalf("a batch of three giants' crafts landed %d: %+v", got, result)
	}
	if crafts, _ := result["crafts"].([]map[string]any); len(crafts) != 3 {
		t.Fatalf("a batch of three reported %d units", len(crafts))
	}
	output, _ := result["output"].(map[string]int64)
	if output["recovery_pill"] != 3 {
		t.Fatalf("the batch reported output %v, want 3 recovery pills", output)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`)); got != 3 {
		t.Fatalf("the bag holds %d recovery pills after a batch of three", got)
	}
	if herb := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`)); herb != 1 {
		t.Fatalf("the bag holds %d spirit herb; three units of two each from seven leaves one", herb)
	}
	if rows := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM alchemy_batches WHERE user_id=42`)); rows != 3 {
		t.Fatalf("%d alchemy records for a batch of three; each unit is a refinement", rows)
	}
	if total := storage.ParseInt(actionScalar(t, path, `SELECT total_refinements FROM alchemy_state WHERE user_id=42`)); total != 3 {
		t.Fatalf("total_refinements=%d after a batch of three", total)
	}
}

func TestABatchTheBagsCannotPayForCostsNothing(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',5),(42,'beast_core',5)`)

	_, err := craftBatchApply(t, path, world, "batch-short", map[string]any{"recipe": "Recovery Pill", "quantity": 3})
	if err == nil || !strings.Contains(err.Error(), "missing materials for a batch of 3") {
		t.Fatalf("five herbs paid for three units of two: %v", err)
	}
	if herb := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`)); herb != 5 {
		t.Fatalf("a refused batch left %d spirit herb of 5; a refusal must cost nothing", herb)
	}
	if rows := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM alchemy_batches WHERE user_id=42`)); rows != 0 {
		t.Fatalf("a refused batch wrote %d alchemy records", rows)
	}
}

func TestABatchMissesAreEachRefunded(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Spirit-Iron Sword")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron',6),(42,'beast_core',2)`)
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":2,"agility":2,"spirit":2,"insight":2,"will":2,"presence":2}' WHERE user_id=42`)
	defer gamerng.UseRoller(func(int) int { return 0 })() // every die its lowest face: every unit misses

	result, err := craftBatchApply(t, path, world, "batch-miss", map[string]any{"recipe": "Spirit-Iron Sword", "quantity": 2})
	if err != nil {
		t.Fatal(err)
	}
	if result["success"] != false || storage.ParseInt(result["successes"]) != 0 {
		t.Fatalf("two ones against TN 14 landed: %+v", result)
	}
	returned, _ := result["returned"].(map[string]int64)
	if returned["spirit_iron"] != 2 || returned["beast_core"] != 0 {
		t.Fatalf("two misses on 3 iron and 1 core returned %v; one iron each, summed", returned)
	}
	if iron := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_iron'`)); iron != 2 {
		t.Fatalf("the bag holds %d spirit iron after two refunded misses", iron)
	}
}

func TestABatchIsOneToTheEnginesCap(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',100),(42,'beast_core',100)`)
	for i, quantity := range []int64{0, -2, craftBatchMax + 1} {
		_, err := craftBatchApply(t, path, world, "batch-bound-"+string(rune('a'+i)), map[string]any{"recipe": "Recovery Pill", "quantity": quantity})
		if err == nil || !strings.Contains(err.Error(), "a batch is 1 to") {
			t.Fatalf("a batch of %d was not refused: %v", quantity, err)
		}
	}
	if herb := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`)); herb != 100 {
		t.Fatalf("a refused batch size spent herbs (%d left)", herb)
	}
	// And the cap itself is a batch.
	result, err := craftBatchApply(t, path, world, "batch-cap", map[string]any{"recipe": "Recovery Pill", "quantity": craftBatchMax})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["quantity"]); got != craftBatchMax {
		t.Fatalf("a batch at the cap reported quantity %d", got)
	}
}

// Craft all (v1.21.0) makes as many as the bags pay for, counted in the
// transaction that spends them.
func TestCraftAllMakesWhatTheBagsPayFor(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	// Two herbs a pill: 9 herbs pay for 4, and the 5 cores are not the limit.
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',9),(42,'beast_core',5)`)

	result, err := craftBatchApply(t, path, world, "craft-all", map[string]any{"recipe": "Recovery Pill", "all": true})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["quantity"]); got != 4 {
		t.Fatalf("craft all on 9 herbs at 2 a pill made a batch of %d, want 4", got)
	}
	if result["capped"] != false {
		t.Fatalf("a batch of 4 reported capped: %+v", result["capped"])
	}
	if herb := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`)); herb != 1 {
		t.Fatalf("craft all left %d spirit herb of 9; it should spend 8 and keep the odd one", herb)
	}
}

func TestCraftAllStopsAtTheCapAndSaysSo(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',1000),(42,'beast_core',1000)`)

	result, err := craftBatchApply(t, path, world, "craft-all-cap", map[string]any{"recipe": "Recovery Pill", "all": true})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["quantity"]); got != craftBatchMax || result["capped"] != true {
		t.Fatalf("craft all on bags for 500 made %d, capped=%v; want %d and capped", got, result["capped"], craftBatchMax)
	}
}

func TestCraftAllWithNothingAffordableIsTheSingleRefusal(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',1),(42,'beast_core',3)`)

	_, err := craftBatchApply(t, path, world, "craft-all-none", map[string]any{"recipe": "Recovery Pill", "all": true})
	if err == nil || !strings.HasPrefix(err.Error(), "missing materials: ") {
		t.Fatalf("craft all with one herb was not refused with the single shortfall: %v", err)
	}
	if herb := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`)); herb != 1 {
		t.Fatalf("a refused craft all spent herbs (%d left)", herb)
	}
}

func TestCraftIsAllOrAQuantityNotBoth(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',10),(42,'beast_core',10)`)
	_, err := craftBatchApply(t, path, world, "craft-both", map[string]any{"recipe": "Recovery Pill", "all": true, "quantity": 2})
	if err == nil || !strings.Contains(err.Error(), "not both") {
		t.Fatalf("all and a quantity together were not refused: %v", err)
	}
}

// --- from whole_roll_test.go ---

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
