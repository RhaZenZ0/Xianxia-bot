package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Two doors on the panel opened for nobody (v1.17.1). The homestead asked
// sect rank 40 and the promotion ladder stopped at 30; the sect manor asked
// rank 70 as a Go literal beside a ladder that is content; and a personal
// world asked realm 30 and Space Law 100 as two more literals, under a page the
// curriculum opened at realm 5. Each floor is content now, read by one Go
// helper, and these tests drive the shipped catalogue and a copy of it with
// the floor moved - because a test against the shipped numbers alone passes
// identically against the literals it replaces (the rc.47 shape).

// withSectAbodeRule copies the catalogue with one `sect_abode_system` key
// changed; the maps are shared with the memoised parse, so they are copied
// rather than written into.
func withSectAbodeRule(catalog worlddata.Catalog, key string, value any) worlddata.Catalog {
	out := catalog
	out.SectAbodeSystem = map[string]any{}
	for k, v := range catalog.SectAbodeSystem {
		out.SectAbodeSystem[k] = v
	}
	out.SectAbodeSystem[key] = value
	return out
}

func withPersonalWorldRule(catalog worlddata.Catalog, key string, value any) worlddata.Catalog {
	out := catalog
	out.PersonalWorldSystem = map[string]any{}
	for k, v := range catalog.PersonalWorldSystem {
		out.PersonalWorldSystem[k] = v
	}
	out.PersonalWorldSystem[key] = value
	return out
}

func manorEstablish(t *testing.T, path string, catalog worlddata.Catalog, userID int64) error {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"name": "The Hall of Tested Floors", "game_minute": 100})
	return crossingApply(t, path, func(conn *storage.Conn) error {
		_, err := sectManorActionGo(conn, catalog, userID, raw, "sect.manor.establish")
		return err
	})
}

func TestTheManorsRankIsTheContents(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level) VALUES(42,'Azure Cloud Sect','Elder',50)`)
	floor := manorFoundingRankGo(catalog)
	if floor <= 50 {
		t.Fatalf("the shipped manor_founding_rank_level is %d; this test stands an Elder (50) below it and needs it above", floor)
	}
	err := manorEstablish(t, path, catalog, 42)
	if err == nil || !strings.Contains(err.Error(), sectRankName(catalog, floor)) {
		t.Fatalf("an Elder establishing the manor was not refused by the content's rank (%s): %v", sectRankName(catalog, floor), err)
	}
	// The same Elder, with the content lowered to their rank: the rank no
	// longer refuses. What refuses next is the treasury, which is the point -
	// the rank check read the file, not a number in the code.
	lowered := withSectAbodeRule(catalog, "manor_founding_rank_level", float64(50))
	err = manorEstablish(t, path, lowered, 42)
	if err != nil && strings.Contains(err.Error(), "asks for") {
		t.Fatalf("with manor_founding_rank_level lowered to 50 an Elder was still refused by rank: %v - the rank is a literal again", err)
	}
}

func TestTheManorsConstructionRankIsTheContents(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level) VALUES(42,'Azure Cloud Sect','Deacon',40)`)
	batch4Exec(t, path, `INSERT INTO sect_manors(sect_name,name,base_location) VALUES('Azure Cloud Sect','Azure Hall','Greenriver Town')`)
	raw, _ := json.Marshal(map[string]any{"facility": "qi_array", "game_minute": 100})
	upgrade := func(c worlddata.Catalog) error {
		return crossingApply(t, path, func(conn *storage.Conn) error {
			_, err := sectManorActionGo(conn, c, 42, raw, "sect.manor.upgrade")
			return err
		})
	}
	floor := manorConstructionRankGo(catalog)
	if floor <= 40 {
		t.Fatalf("the shipped manor_construction_rank_level is %d; this test stands a Deacon (40) below it", floor)
	}
	if err := upgrade(catalog); err == nil || !strings.Contains(err.Error(), sectRankName(catalog, floor)) {
		t.Fatalf("a Deacon directing construction was not refused by the content's rank: %v", err)
	}
	if err := upgrade(withSectAbodeRule(catalog, "manor_construction_rank_level", float64(40))); err != nil && strings.Contains(err.Error(), "asks for") {
		t.Fatalf("with the construction rank lowered to 40 a Deacon was still refused by rank: %v", err)
	}
}

func personalWorldDB(t *testing.T) string {
	t.Helper()
	path := setupLawControlDB(t, true)
	batch4Exec(t, path, `CREATE TABLE personal_worlds (
		user_id INTEGER PRIMARY KEY, location_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, stability INTEGER NOT NULL DEFAULT 1,
		laws_json TEXT NOT NULL DEFAULT '{}', access_mode TEXT NOT NULL DEFAULT 'private', created_at REAL NOT NULL, updated_at REAL NOT NULL,
		FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE)`)
	return path
}

func createWorld(t *testing.T, path string, catalog worlddata.Catalog) error {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"name": "Folded Seam", "game_minute": 100})
	return crossingApply(t, path, func(conn *storage.Conn) error {
		_, err := personalWorldCreateActionGo(conn, catalog, 901, raw)
		return err
	})
}

func TestAPersonalWorldsFloorIsTheContents(t *testing.T) {
	path := personalWorldDB(t)
	catalog := crossingCatalog(t)
	floorRealm, floorLaw := personalWorldFloorGo(catalog)
	if floorRealm <= 5 || floorLaw <= 50 {
		t.Fatalf("the shipped personal_world_system asks realm %d and Space Law %d; this test stands a realm-5 cultivator at Space Law 50 below both", floorRealm, floorLaw)
	}
	batch4Exec(t, path, `UPDATE characters SET realm_index=5 WHERE user_id=901`)
	err := createWorld(t, path, catalog)
	if err == nil || !strings.Contains(err.Error(), realmNameGo(catalog, floorRealm)) {
		t.Fatalf("a realm-5 cultivator was not refused by the content's realm (%s): %v", realmNameGo(catalog, floorRealm), err)
	}
	// The content lowered to where they stand: the floor reads the file.
	lowered := withPersonalWorldRule(withPersonalWorldRule(catalog, "min_realm_index", float64(5)), "space_law_comprehension", float64(50))
	if err := createWorld(t, path, lowered); err != nil {
		t.Fatalf("with personal_world_system lowered to realm 5 and Space Law 50 the creation was still refused: %v - the floors are literals again", err)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM personal_worlds WHERE user_id=901`)); n != 1 {
		t.Fatalf("personal_worlds rows = %d, want 1", n)
	}
}

// The homestead's founding rank is a rung the promotion ladder reaches: a
// door a member can earn rather than one only a GM's lever opens.
func TestTheHomesteadsRankIsOnThePromotionLadder(t *testing.T) {
	catalog := crossingCatalog(t)
	floor := homesteadFoundingRankGo(catalog)
	if floor <= 0 {
		t.Fatal("the homestead asks no sect rank; the check is broken, not the tree")
	}
	for _, rung := range catalog.SectExchange().Promotion {
		if rung.RankLevel == floor {
			return
		}
	}
	t.Fatalf("abode_system.founding_rank_level is %d (%s) and no rung of sect_system.exchange.promotion reaches it: a player can be shown Establish and never open it", floor, sectRankName(catalog, floor))
}
