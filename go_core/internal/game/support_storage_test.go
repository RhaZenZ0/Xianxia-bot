package game

// v0.23.1 regression tests for two findings from the second external review:
// a family paying out of an empty purse, and an "upgrade" that downgrades.
//
// Both actions had no Go tests at all before this, which is why both survived.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func setupSupportStorageDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// birth_families and character_birth_family come from the base fixture with
	// only the columns its own tests need; these are the rest.
	if err := conn.ExecScript(`
ALTER TABLE characters ADD COLUMN spirit_stones INTEGER NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN wealth INTEGER NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN retainers INTEGER NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN prestige INTEGER NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN clan_name TEXT NOT NULL DEFAULT '';
ALTER TABLE birth_families ADD COLUMN updated_at REAL NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN tier INTEGER NOT NULL DEFAULT 1;
ALTER TABLE birth_families ADD COLUMN influence INTEGER NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN bloodline_purity INTEGER NOT NULL DEFAULT 0;
ALTER TABLE character_birth_family ADD COLUMN birth_order INTEGER NOT NULL DEFAULT 1;
ALTER TABLE character_birth_family ADD COLUMN generation INTEGER NOT NULL DEFAULT 1;
ALTER TABLE character_birth_family ADD COLUMN last_support_game_minute INTEGER NOT NULL DEFAULT -999999999;
CREATE TABLE martial_clan_branches(family_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active', loyalty INTEGER NOT NULL DEFAULT 0);
CREATE TABLE martial_clan_retainers(family_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active', loyalty INTEGER NOT NULL DEFAULT 0, members INTEGER NOT NULL DEFAULT 0);
CREATE TABLE martial_clan_relations(family_id INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, relation_type TEXT NOT NULL DEFAULT '', relation_score INTEGER NOT NULL DEFAULT 0);
CREATE TABLE storage_containers(
	user_id INTEGER PRIMARY KEY, container_id TEXT NOT NULL, name TEXT NOT NULL, grade TEXT NOT NULL,
	slot_capacity INTEGER NOT NULL, living_space INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
);
CREATE TABLE storage_inventory(
	user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY(user_id,item_id)
);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func supportStorageScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}

// ------------------------------------------------------ family.support

func seedFamily(t *testing.T, path string, wealth int64) {
	t.Helper()
	batch4Exec(t, path,
		`INSERT INTO birth_families(family_id,archetype,wealth,retainers,prestige,updated_at) VALUES(1,'merchant_house',?,4,10,0)`,
		wealth)
	batch4Exec(t, path,
		`INSERT INTO character_birth_family(user_id,family_id) VALUES(42,1)`)
}

func familySupportApply(t *testing.T, path string) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(map[string]any{"game_minute": 5000})
	if err != nil {
		t.Fatal(err)
	}
	mut, actionErr := familySupportActionGo(conn, worlddata.Catalog{}, 42, raw)
	if conn.InTransaction() {
		if actionErr != nil {
			if err := conn.Rollback(); err != nil {
				t.Fatal(err)
			}
		} else if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	if actionErr != nil {
		return nil, actionErr
	}
	result, _ := mut.Result.(map[string]any)
	return result, nil
}

func TestAFamilyCannotSupportBeyondItsWealth(t *testing.T) {
	// The bug in its plainest form: wealth 1 passed `wealth <= 0`, the update
	// floored at zero, and the player walked away with the full package.
	path := setupSupportStorageDB(t)
	seedFamily(t, path, 1)

	_, err := familySupportApply(t, path)
	if err == nil {
		t.Fatal("a family with 1 stone paid for a full support package")
	}
	if !strings.Contains(err.Error(), "cannot spare") {
		t.Fatalf("refusal was %q", err)
	}
	if got := supportStorageScalar(t, path, `SELECT wealth FROM birth_families WHERE family_id=1`); got != 1 {
		t.Fatalf("wealth=%d; the refused support was still charged", got)
	}
	if got := supportStorageScalar(t, path,
		`SELECT COALESCE(SUM(balance),0) FROM currency_wallets WHERE user_id=42`); got != 0 {
		t.Fatalf("wallet=%d; the player was paid from an empty family purse", got)
	}
	if got := supportStorageScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42`); got != 0 {
		t.Fatalf("inventory rows=%d after a refused support", got)
	}
}

func TestAFamilyThatCanAffordItStillPays(t *testing.T) {
	path := setupSupportStorageDB(t)
	seedFamily(t, path, 500)
	before := supportStorageScalar(t, path, `SELECT wealth FROM birth_families WHERE family_id=1`)

	result, err := familySupportApply(t, path)
	if err != nil {
		t.Fatal(err)
	}
	after := supportStorageScalar(t, path, `SELECT wealth FROM birth_families WHERE family_id=1`)
	if after >= before {
		t.Fatalf("wealth %d -> %d; the support cost nothing", before, after)
	}
	if after < 0 {
		t.Fatalf("wealth went negative: %d", after)
	}
	// And what the family spent is what the action said it would.
	if cost := storage.ParseInt(result["cost"]); cost > 0 && before-after != cost {
		t.Fatalf("wealth fell by %d, the action reported a cost of %d", before-after, cost)
	}
}

func TestFamilyWealthNeverGoesNegativeAcrossRepeatedSupport(t *testing.T) {
	// The floor used to be `MAX(0, ...)`, which hid every overdraft. With the
	// guard the floor is the guard, so draining a family ends in a refusal
	// rather than a silent zero.
	path := setupSupportStorageDB(t)
	seedFamily(t, path, 60)
	for i := 0; i < 12; i++ {
		batch4Exec(t, path, `UPDATE character_birth_family SET last_support_game_minute=-999999999 WHERE user_id=42`)
		if _, err := familySupportApply(t, path); err != nil {
			break
		}
		if got := supportStorageScalar(t, path, `SELECT wealth FROM birth_families WHERE family_id=1`); got < 0 {
			t.Fatalf("wealth went negative after %d supports: %d", i+1, got)
		}
	}
}

// ------------------------------------------------------ storage.upgrade

func storageCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		Items: map[string]worlddata.Item{
			"common_pouch": {
				Name:           "Common Spatial Pouch",
				StorageUpgrade: map[string]any{"container_id": "common_pouch", "grade": "Mortal", "slot_capacity": int64(24)},
			},
			"earth_ring": {
				Name:           "Earth Spatial Ring",
				StorageUpgrade: map[string]any{"container_id": "earth_ring", "grade": "Earth", "slot_capacity": int64(80)},
			},
			"living_world_ring": {
				Name:           "Living World Ring",
				StorageUpgrade: map[string]any{"container_id": "living_world_ring", "grade": "Heaven", "slot_capacity": int64(500), "living_space": true},
			},
		},
	}
}

func storageUpgradeApply(t *testing.T, path, itemID string) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(map[string]any{"item_id": itemID, "game_minute": 5000})
	if err != nil {
		t.Fatal(err)
	}
	mut, actionErr := storageUpgradeActionGo(conn, storageCatalog(), 42, raw)
	if conn.InTransaction() {
		if actionErr != nil {
			if err := conn.Rollback(); err != nil {
				t.Fatal(err)
			}
		} else if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	if actionErr != nil {
		return nil, actionErr
	}
	result, _ := mut.Result.(map[string]any)
	return result, nil
}

func giveItem(t *testing.T, path, itemID string) {
	t.Helper()
	batch4Exec(t, path,
		`INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,?,1)
		 ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+1`, itemID)
}

func TestAnUpgradeFromNothingIsAccepted(t *testing.T) {
	path := setupSupportStorageDB(t)
	giveItem(t, path, "earth_ring")
	result, err := storageUpgradeApply(t, path, "earth_ring")
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["slot_capacity"]); got != 80 {
		t.Fatalf("slot_capacity=%d, want 80", got)
	}
}

func TestAnUpgradeCannotShrinkTheContainer(t *testing.T) {
	// 500 slots and a living world, then a 24-slot pouch. The command calls
	// this an upgrade, so a player reaching for the wrong item lost both -
	// and the pouch was consumed on the way.
	path := setupSupportStorageDB(t)
	giveItem(t, path, "living_world_ring")
	if _, err := storageUpgradeApply(t, path, "living_world_ring"); err != nil {
		t.Fatal(err)
	}
	giveItem(t, path, "common_pouch")

	_, err := storageUpgradeApply(t, path, "common_pouch")
	if err == nil {
		t.Fatal("a 500-slot living ring was replaced by a 24-slot pouch")
	}
	if !strings.Contains(err.Error(), "stacks") {
		t.Fatalf("refusal was %q; it should say what would be lost", err)
	}
	if got := supportStorageScalar(t, path, `SELECT slot_capacity FROM storage_containers WHERE user_id=42`); got != 500 {
		t.Fatalf("slot_capacity=%d, want the 500 they had", got)
	}
	if got := supportStorageScalar(t, path, `SELECT living_space FROM storage_containers WHERE user_id=42`); got != 1 {
		t.Fatalf("living_space=%d; the living world was lost", got)
	}
	// And the refusal must give the pouch back - the consume and the check are
	// one transaction, so a rejected upgrade cannot eat the item.
	if got := supportStorageScalar(t, path,
		`SELECT quantity FROM inventory WHERE user_id=42 AND item_id='common_pouch'`); got != 1 {
		t.Fatalf("pouch quantity=%d; a refused upgrade consumed the item", got)
	}
}

func TestAnUpgradeCannotSilentlyRemoveALivingSpace(t *testing.T) {
	// Same capacity or more, but no living world. Capacity alone would let
	// this through, which is why living space is checked on its own.
	path := setupSupportStorageDB(t)
	batch4Exec(t, path,
		`INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at)
		 VALUES(42,'living_world_ring','Living World Ring','Heaven',24,1,0)`)
	giveItem(t, path, "common_pouch")

	_, err := storageUpgradeApply(t, path, "common_pouch")
	if err == nil {
		t.Fatal("a living space was traded away for equal capacity")
	}
	if !strings.Contains(err.Error(), "living space") {
		t.Fatalf("refusal was %q", err)
	}
	if got := supportStorageScalar(t, path, `SELECT living_space FROM storage_containers WHERE user_id=42`); got != 1 {
		t.Fatalf("living_space=%d", got)
	}
}

func TestARealUpgradeStillReplacesTheContainer(t *testing.T) {
	path := setupSupportStorageDB(t)
	giveItem(t, path, "earth_ring")
	if _, err := storageUpgradeApply(t, path, "earth_ring"); err != nil {
		t.Fatal(err)
	}
	giveItem(t, path, "living_world_ring")
	result, err := storageUpgradeApply(t, path, "living_world_ring")
	if err != nil {
		t.Fatalf("80 -> 500 with living space was refused: %v", err)
	}
	if got := storage.ParseInt(result["slot_capacity"]); got != 500 {
		t.Fatalf("slot_capacity=%d, want 500", got)
	}
	if living, _ := result["living_space"].(bool); !living {
		t.Fatalf("living_space=%v, want true", result["living_space"])
	}
}

func TestTheOccupiedSlotFloorStillApplies(t *testing.T) {
	// The pre-existing rule, which the new check must not replace: a container
	// is never smaller than what is already inside it.
	path := setupSupportStorageDB(t)
	for i := 0; i < 90; i++ {
		batch4Exec(t, path,
			`INSERT INTO storage_inventory(user_id,item_id,quantity) VALUES(42,?,1)`,
			fmt.Sprintf("stored_%d", i))
	}
	giveItem(t, path, "earth_ring")
	result, err := storageUpgradeApply(t, path, "earth_ring")
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["slot_capacity"]); got != 90 {
		t.Fatalf("slot_capacity=%d, want the 90 slots in use", got)
	}
}
