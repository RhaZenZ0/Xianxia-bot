package game

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// Ascension (飞升) carries a cultivator up (v1.0.0-rc.15).
//
// Every piece of the crossing existed: the tribulation, its three waves, the
// gate on the breakthrough, the world-history row reading "ascended to the
// Spiritual World". And then the character was still standing in Greenriver
// Town, with a free `/realmhub go` as the only way to actually be there - the
// heavens took nobody anywhere.

// ascensionReady puts a character one breakthrough below a world boundary
// with the tribulation cleared and the realm gate open, so the crossing
// itself is the only thing left to decide.
func ascensionReady(t *testing.T, path string, realm int64) {
	t.Helper()
	batch4Exec(t, path, fmt.Sprintf(
		`UPDATE characters SET realm_index=%d,phase=9,cultivation=1000000,qi=100000,qi_max=100000,location='Greenriver Town' WHERE user_id=42`, realm))
	batch4Exec(t, path, `INSERT INTO tribulation_state(user_id,gate_realm_index,preparation,attempts,cleared,last_result,updated_game_minute,updated_at) VALUES(42,?,5,1,1,'cleared',0,0)`, realm)
	batch4Exec(t, path, `INSERT INTO realm_perfection(user_id,realm_index,active,completed,updated_at) VALUES(42,?,0,1,0)`, realm)
}

func TestAscensionSetsTheCultivatorDownInTheNewWorld(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	ascensionReady(t, path, 7) // Ascension Realm, phase 9: the Mortal boundary.

	out := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 1, map[string]any{"confirm": true}))
	if out["success"] != true || out["ascended"] != true {
		t.Fatalf("the crossing did not land: %v", out)
	}
	if fmt.Sprint(out["to_world"]) != "Spiritual World" {
		t.Fatalf("to_world=%v", out["to_world"])
	}
	if fmt.Sprint(out["ascended_from_location"]) != "Greenriver Town" {
		t.Fatalf("ascended_from_location=%v", out["ascended_from_location"])
	}
	if fmt.Sprint(out["ascended_to_location"]) != "Spirit Jade Capital" {
		t.Fatalf("ascended_to_location=%v", out["ascended_to_location"])
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != "Spirit Jade Capital" {
		t.Fatalf("the heavens left them where they were: %q", got)
	}
	// And where they were set down is a place they now know.
	if got := i64(actionScalar(t, path,
		`SELECT COUNT(*) FROM character_location_discoveries WHERE user_id=42 AND location='Spirit Jade Capital'`)); got != 1 {
		t.Fatalf("arrival is not on their map: %d", got)
	}
}

func TestAnOrdinaryBreakthroughMovesNobody(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	// Realm 1 to 2 is inside the Mortal World: nothing should relocate.
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=9,cultivation=1000000,qi=100000,qi_max=100000,location='Greenriver Town' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO realm_perfection(user_id,realm_index,active,completed,updated_at) VALUES(42,1,0,1,0)`)

	out := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 1, map[string]any{"confirm": true}))
	if out["success"] != true || out["ascended"] != false {
		t.Fatalf("not an ascension: %v", out)
	}
	if _, moved := out["ascended_to_location"]; moved {
		t.Fatalf("an ordinary breakthrough must not move anyone: %v", out["ascended_to_location"])
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != "Greenriver Town" {
		t.Fatalf("location=%q", got)
	}
}

func TestTheUnclearedTribulationStillRefusesTheCrossing(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	ascensionReady(t, path, 7)
	batch4Exec(t, path, `UPDATE tribulation_state SET cleared=0 WHERE user_id=42 AND gate_realm_index=7`)

	_, err := batch4ApplyErr(path, world, "cultivation.breakthrough", 42, 1, map[string]any{"confirm": true})
	if err == nil || !strings.Contains(err.Error(), "world-crossing tribulation") {
		t.Fatalf("an uncleared tribulation must refuse the crossing, got %v", err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != "Greenriver Town" {
		t.Fatalf("a refused crossing moved them anyway: %q", got)
	}
}

// Either ladder opens a place. The world-crossing tribulation is gated on qi
// *or* body, so a body cultivator who crosses must not then find the world
// they crossed into shut against them.
func TestEitherLadderOpensAWorld(t *testing.T) {
	path := setupCultivationDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,body_realm_index=8 WHERE user_id=42`)
	c, err := loadMechanicsCharacter(conn, 42)
	if err != nil {
		t.Fatal(err)
	}
	if c.accessRealmIndex() != 8 {
		t.Fatalf("a body cultivator's own ladder must answer: %d", c.accessRealmIndex())
	}
	catalog := districtCatalog(t)
	if worldMinRealm(catalog, "Spiritual World") > c.accessRealmIndex() {
		t.Fatal("a body cultivator who ascended is locked out of the world they ascended into")
	}
}
