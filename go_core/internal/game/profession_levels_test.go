package game

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A profession level has to change something (v1.0.0-rc.19).
//
// "Beast Taming" and "Artifact Refining" were written by six call sites in
// `beast_artifact_actions.go` and read by none: every read of the row went
// straight into the response payload, so the number was printed on the card and
// consulted by no rule. A Grandmaster tamer tamed no better than a first-timer.
//
// `ProfessionRosterTests` in tests/python/unit/test_world_content_gate.py holds
// the structural half - that each profession's level is read by name somewhere.
// These are the behavioural half: the same action, run twice against the same
// fixture, differing only in the practitioner's level.

// proficiencyDB is the companion fixture with one practitioner at `level` in
// `profession`, or no row at all when level is 0.
func proficiencyDB(t *testing.T, profession string, level int64) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	// The fixture's character carries 100 in every attribute, which saturates
	// the training clamp (6 + 100/3 is already past its ceiling of 25) and
	// would hide the level under it. An ordinary cultivator is what these
	// rules are for, so the arithmetic is made visible rather than assumed.
	batch4Exec(t, path, `UPDATE characters
		SET attributes_json='{"body":10,"agility":10,"spirit":6,"insight":10,"will":10,"presence":10}'
		WHERE user_id=42`)
	if level > 0 {
		batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
			VALUES(42,?,?,0,0,0,0,0)`, profession, level)
	}
	return path
}

func TestATrainedHandTamesBetter(t *testing.T) {
	world := batch4WorldPath(t)
	modifierAt := func(level int64) int64 {
		path := proficiencyDB(t, beastTamingProfession, level)
		batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at)
			VALUES('world_clock','{"anchor_game_minute":1000,"anchor_real_ts":1,"scale":0}',0)`)
		batch4Exec(t, path, `INSERT INTO wild_beast_encounters(
			user_id,species,rank,element,intelligence,temperament,bloodline,taming_tn,
			location,expires_game_minute,status,created_game_minute,created_at,updated_at
		) VALUES(42,'Sunmane Lynx',1,'Fire',12,'wary','Solar Lynx',8,'Greenriver Town',1100,'available',900,0,0)`)
		result := batch4Result(t, batch4Apply(t, path, world, "beast.tame", 1, map[string]any{"encounter_id": 1}))
		return storage.ParseInt(result["modifier"])
	}
	novice, master := modifierAt(0), modifierAt(5)
	if master != novice+5 {
		t.Fatalf("a level-5 tamer rolled %d against a novice's %d; the profession is decoration again", master, novice)
	}
}

func TestATrainedHandTrainsBetter(t *testing.T) {
	world := batch4WorldPath(t)
	gainAt := func(level int64) int64 {
		path := proficiencyDB(t, beastTamingProfession, level)
		batch4Exec(t, path, `INSERT INTO spirit_beasts(
			user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
			loyalty,contract_type,active,techniques_json,created_at,updated_at
		) VALUES(42,'Cloudpaw','Wind Lynx',1,'Wind',10,'bonded','Common',0,30,'equality',1,'[]',0,0)`)
		result := batch4Result(t, batch4Apply(t, path, world, "beast.train", 1, map[string]any{"beast_id": 1}))
		return storage.ParseInt(result["gain"])
	}
	novice, master := gainAt(0), gainAt(4)
	if master != novice+4 {
		t.Fatalf("a level-4 trainer gained %d against a novice's %d", master, novice)
	}
}

func TestARefinersLevelDrawsTheBondTighter(t *testing.T) {
	// Bonding is deterministic - there is no roll for a level to enter - so the
	// level moves the rate instead: the resonance each bonding gains, which is
	// what awakening gates on. A refiner who knows the work reaches an awakened
	// artifact sooner; they do not reach a different one.
	world := batch4WorldPath(t)
	resonanceAt := func(level int64) int64 {
		path := proficiencyDB(t, artifactRefiningProfession, level)
		batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron',1)`)
		batch4Apply(t, path, world, "artifact.bond", 1, map[string]any{"item_id": "spirit_iron"})
		return storage.ParseInt(actionScalar(t, path,
			`SELECT resonance FROM artifact_bonds WHERE user_id=42 AND item_id='spirit_iron'`))
	}
	novice, master := resonanceAt(0), resonanceAt(6)
	if master <= novice {
		t.Fatalf("a level-6 refiner bonded to %d resonance against a novice's %d", master, novice)
	}
	if master != novice+6 {
		t.Fatalf("resonance=%d, want the novice's %d plus the level", master, novice)
	}
}

func TestAnUnpractisedCultivatorIsUnaffected(t *testing.T) {
	// Every one of these rules must cost nothing to someone who has never
	// practised: a missing row reads as level 0, not as an error and not as a
	// penalty. This is what lets the fix ship without renumbering the game.
	world := batch4WorldPath(t)
	path := proficiencyDB(t, beastTamingProfession, 0)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',1,'Wind',10,'bonded','Common',0,30,'equality',1,'[]',0,0)`)
	result := batch4Result(t, batch4Apply(t, path, world, "beast.train", 1, map[string]any{"beast_id": 1}))
	// spirit 6, no abode: 6 + 6/3 + 0 context + 0 level = 8.
	if got := storage.ParseInt(result["gain"]); got != 8 {
		t.Fatalf("gain=%d for an unpractised trainer, want the unchanged 8", got)
	}
}

// The forager's world (v1.0.0-rc.19).
//
// The common drop was the literal "spirit_herb" in all four worlds, so a
// Celestial forager gathered Mortal weeds - the one profession whose whole
// output ignored the tier it was practised in. It resolves through the same
// `EventSites.Material` the world-event sites, the birth-family send-off and
// the sect tribute use.
func TestAForagerGathersTheWorldTheyStandIn(t *testing.T) {
	world := batch4WorldPath(t)
	herbFor := func(worldName, location string) string {
		path := setupBatch4AuthorityDB(t)
		setupForageEffectAuthorityTables(t, path)
		batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES(?,?,90)`, location, worldName)
		batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, location)
		result := batch4Result(t, batch4Apply(t, path, world, "forage.resolve", 1, map[string]any{}))
		loot, _ := result["loot"].(map[string]int64)
		for item := range loot {
			// The common drop is the bulk of the plan; the rare pool adds at
			// most one extra and is named separately in the result.
			if strings.HasSuffix(item, "herb") && fmt.Sprint(result["rare_found"]) != item {
				return item
			}
		}
		return ""
	}
	// Greenriver Town is the Mortal World's own; Froststar Border City is a
	// Celestial city, and its forager should not be bringing back Mortal weeds.
	mortal := herbFor("Mortal World", "Greenriver Town")
	if mortal != "spirit_herb" {
		t.Fatalf("a Mortal forager gathered %q, want spirit_herb", mortal)
	}
	celestial := herbFor("Celestial World", "Froststar Border City")
	if celestial == "spirit_herb" {
		t.Fatal("a Celestial forager is still gathering Mortal weeds")
	}
	if celestial != "heavenpetal_herb" {
		t.Fatalf("a Celestial forager gathered %q, want the world's own heavenpetal_herb", celestial)
	}
}
