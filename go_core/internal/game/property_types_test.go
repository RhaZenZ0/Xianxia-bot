package game

import (
	"encoding/json"
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

func TestBuildablePropertyTypesAreTheEstatesNotTheCaveAbode(t *testing.T) {
	world := batch4WorldPath(t)
	catalog := mustLoadCatalog(t, world)
	got := buildablePropertyTypes(catalog)
	want := []string{"alchemy_estate", "clan_estate", "merchant_pavilion", "spirit_beast_ranch", "spirit_herb_estate"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("buildable=%v want %v", got, want)
	}
	if propertyTypeBuildable(catalog, "cave_abode") {
		t.Fatal("the cave abode is assigned by a sect, not founded")
	}
	if _, defined := propertyTypeDefinition(catalog, "cave_abode"); !defined {
		t.Fatal("the cave abode must stay defined so existing rows keep their label")
	}
}

func TestEstablishFoundsEachEstateWithItsDefaults(t *testing.T) {
	world := batch4WorldPath(t)
	cases := map[string]map[string]int64{
		"alchemy_estate":     {"alchemy_level": 1, "herb_garden_level": 1, "storage_level": 1, "cultivation_level": 1},
		"spirit_herb_estate": {"herb_garden_level": 2, "alchemy_level": 1},
		"spirit_beast_ranch": {"beast_pen_level": 2, "defense_level": 1},
		"merchant_pavilion":  {"merchant_level": 1, "storage_level": 2, "defense_level": 1},
		"clan_estate":        {"formation_level": 1, "defense_level": 1, "storage_level": 2},
	}
	for propertyType, levels := range cases {
		t.Run(propertyType, func(t *testing.T) {
			path := setupPropertyTypesDB(t)
			result, err := establishProperty(t, path, world, propertyType)
			if err != nil {
				t.Fatal(err)
			}
			if result["property_type"] != propertyType {
				t.Fatalf("property_type=%v", result["property_type"])
			}
			for column, want := range levels {
				if got := storage.ParseInt(result[column]); got != want {
					t.Fatalf("%s=%d want %d", column, got, want)
				}
			}
		})
	}
}

func TestEstablishRefusesRetiredUnknownAndEmptyTypes(t *testing.T) {
	world := batch4WorldPath(t)
	for _, propertyType := range []string{"cave_abode", "sky_palace", ""} {
		t.Run("type="+propertyType, func(t *testing.T) {
			path := setupPropertyTypesDB(t)
			_, err := establishProperty(t, path, world, propertyType)
			if err == nil {
				t.Fatalf("%q was founded", propertyType)
			}
			if !strings.Contains(err.Error(), "alchemy_estate") || strings.Contains(err.Error(), "cave_abode") {
				t.Fatalf("the refusal should name what can be founded and not the cave abode: %v", err)
			}
			if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM cave_abodes")); got != 0 {
				t.Fatalf("rows=%d want 0", got)
			}
		})
	}
}
