package game

import (
	"strings"
	"testing"
)

// The spatial keys (v1.0.0-rc.15). Three items carried `spatial_key` blocks
// that `spatial_key.use` reads, and every one of them named a secret realm
// that does not exist - `sword_grave` against a catalogue holding
// `sword_grave_nine_echoes` - so the action could only ever answer "the
// token's coordinates no longer correspond to a known realm". Nothing sold
// them either, so nobody ever found out.

func TestEverySpatialKeyOpensARealmThatExists(t *testing.T) {
	catalog := districtCatalog(t)
	keys := 0
	for id, item := range catalog.Items {
		if len(item.SpatialKey) == 0 {
			continue
		}
		keys++
		rid, _ := item.SpatialKey["secret_realm_id"].(string)
		if _, ok := catalog.SecretRealms[rid]; !ok {
			t.Fatalf("%s opens %q, which is not a secret realm", id, rid)
		}
	}
	if keys < 3 {
		t.Fatalf("the world ships %d spatial keys", keys)
	}
}

func TestAKeyIsRefusedAwayFromItsEntrance(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	realm := catalog.SecretRealms[catalog.Items["verdant_grotto_key"].SpatialKey["secret_realm_id"].(string)]
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'verdant_grotto_key',1)`)

	// Standing in Greenriver Town, not at the grotto's mouth.
	_, err := batch4ApplyErr(path, world, "spatial_key.use", 42, 1, map[string]any{"item_id": "verdant_grotto_key"})
	if err == nil || !strings.Contains(err.Error(), realm.Location) {
		t.Fatalf("a key spent away from its entrance opens nothing and must say so, got %v", err)
	}
	if got := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='verdant_grotto_key'`)); got != 1 {
		t.Fatalf("the refused key was consumed anyway: %d", got)
	}

	// At the entrance it opens.
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, realm.Location)
	out := batch4Result(t, batch4Apply(t, path, world, "spatial_key.use", 2, map[string]any{"item_id": "verdant_grotto_key"}))
	if out["name"] != realm.Name || out["consumed"] != true {
		t.Fatalf("the key did not open its realm: %v", out)
	}
	if got := i64(actionScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42 AND item_id='verdant_grotto_key' AND quantity>0`)); got != 0 {
		t.Fatalf("a spent key is still in the bag: %d", got)
	}
}
