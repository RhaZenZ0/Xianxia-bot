package game

import (
	"encoding/json"
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
