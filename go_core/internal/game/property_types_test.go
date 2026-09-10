package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func mustLoadCatalog(t *testing.T, world string) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	return catalog
}

func setupPropertyTypesDB(t *testing.T) string {
	t.Helper()
	path := setupAuthority2DB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// setupBatch4AuthorityDB and setupAuthority2DB between them give
	// cave_abodes its key, name, type, cultivation and herb-garden columns;
	// founding writes every facility column.
	if err := conn.ExecScript(`
ALTER TABLE cave_abodes ADD COLUMN alchemy_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN forge_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN formation_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN defense_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN storage_level INTEGER NOT NULL DEFAULT 1;
ALTER TABLE cave_abodes ADD COLUMN beast_pen_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN merchant_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN created_at REAL NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN updated_at REAL NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN spirit_stones INTEGER NOT NULL DEFAULT 0;
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func establishProperty(t *testing.T, path, world, propertyType string) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"name": "Test Holding", "property_type": propertyType})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "prop-" + propertyType, Operation: "abode.establish", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("result type %T", out.Result)
	}
	return result, nil
}

func TestTheOneBuildablePropertyTypeIsTheHomestead(t *testing.T) {
	catalog := mustLoadCatalog(t, batch4WorldPath(t))
	if got := buildablePropertyTypes(catalog); strings.Join(got, ",") != "homestead" {
		t.Fatalf("buildable=%v want [homestead]", got)
	}
	for _, retired := range []string{"cave_abode", "alchemy_estate", "spirit_herb_estate", "spirit_beast_ranch", "merchant_pavilion", "clan_estate"} {
		if propertyTypeBuildable(catalog, retired) {
			t.Fatalf("%s is founded by nobody now", retired)
		}
		if _, defined := propertyTypeDefinition(catalog, retired); !defined {
			t.Fatalf("%s must stay defined so existing rows keep their label", retired)
		}
	}
}

func TestEstablishFoundsTheHomesteadBareWhenNoTypeIsNamed(t *testing.T) {
	world := batch4WorldPath(t)
	for _, propertyType := range []string{"", "homestead"} {
		t.Run("type="+propertyType, func(t *testing.T) {
			path := setupPropertyTypesDB(t)
			result, err := establishProperty(t, path, world, propertyType)
			if err != nil {
				t.Fatal(err)
			}
			if result["property_type"] != "homestead" {
				t.Fatalf("property_type=%v", result["property_type"])
			}
			want := map[string]int64{"cultivation_level": 1, "storage_level": 1, "alchemy_level": 0, "forge_level": 0, "formation_level": 0, "defense_level": 0, "herb_garden_level": 0, "beast_pen_level": 0, "merchant_level": 0}
			for column, level := range want {
				if got := storage.ParseInt(result[column]); got != level {
					t.Fatalf("%s=%d want %d", column, got, level)
				}
			}
		})
	}
}

func TestEstablishRefusesRetiredAndUnknownTypes(t *testing.T) {
	world := batch4WorldPath(t)
	for _, propertyType := range []string{"cave_abode", "alchemy_estate", "sky_palace"} {
		t.Run("type="+propertyType, func(t *testing.T) {
			path := setupPropertyTypesDB(t)
			_, err := establishProperty(t, path, world, propertyType)
			if err == nil {
				t.Fatalf("%q was founded", propertyType)
			}
			if !strings.Contains(err.Error(), "homestead") || strings.Contains(err.Error(), "cave_abode") {
				t.Fatalf("the refusal should name the homestead and nothing retired: %v", err)
			}
			if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM cave_abodes")); got != 0 {
				t.Fatalf("rows=%d want 0", got)
			}
		})
	}
}

func TestUpgradeBuildsAFacilityTheHomeLacks(t *testing.T) {
	world := batch4WorldPath(t)
	path := setupPropertyTypesDB(t)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',250)`)
	if _, err := establishProperty(t, path, world, ""); err != nil {
		t.Fatal(err)
	}
	upgrade := func(seq int) (map[string]any, error) {
		raw, _ := json.Marshal(map[string]any{"facility": "herb_garden"})
		out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("prop-upgrade-%d", seq), Operation: "abode.upgrade", ActorID: 42, Payload: raw})
		if err != nil {
			return nil, err
		}
		return out.Result.(map[string]any), nil
	}
	// Level 0 -> 1 is the build: base cost times one squared.
	built, err := upgrade(1)
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(built["level"]) != 1 || storage.ParseInt(built["cost"]) != 100 || storage.ParseInt(built["balance"]) != 150 {
		t.Fatalf("build=%#v", built)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT herb_garden_level FROM cave_abodes WHERE user_id=42")); got != 1 {
		t.Fatalf("herb_garden_level=%d want 1", got)
	}
	// Level 1 -> 2 costs four times as much, which this purse cannot pay.
	if _, err := upgrade(2); err == nil || !strings.Contains(err.Error(), "insufficient") {
		t.Fatalf("second upgrade: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42")); got != 150 {
		t.Fatalf("balance=%d want 150 (a refused upgrade must not spend)", got)
	}
}
