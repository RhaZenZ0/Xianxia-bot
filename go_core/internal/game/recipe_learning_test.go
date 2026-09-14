package game

import (
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The learning step (v1.0.0-rc.20).
//
// Every recipe in the game used to be craftable by anyone from character
// creation, materials permitting, while `/craft`'s own description said "from a
// known recipe" and nothing tracked knowledge. A method is now two things - one
// you have been taught, and work you are good enough to do - and they are
// checked separately because the refusals mean different things: a player told
// the wrong one goes looking in the wrong place.

func TestAnUntaughtMethodIsRefused(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',9)`)

	err := stage4ApplyError(t, path, world, "craft.resolve", 1, map[string]any{"recipe": "Recovery Pill"})
	if err == nil {
		t.Fatal("an untaught method was worked anyway")
	}
	if got := err.Error(); !strings.Contains(got, "do not know the method") {
		t.Fatalf("refusal reads %q; it must say the method is unknown, not that the level is short", got)
	}
}

func TestAKnownMethodBelowItsLevelIsRefusedDifferently(t *testing.T) {
	// The second half, and the reason the two are separate: this cultivator has
	// been taught the method and simply is not good enough yet. Telling them
	// they "do not know" it would send them shopping for a slip they own.
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Starlight Dao Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'heavenpetal_herb',9)`)

	err := stage4ApplyError(t, path, world, "craft.resolve", 1, map[string]any{"recipe": "Starlight Dao Pill"})
	if err == nil {
		t.Fatal("a level-4 method was worked at level 0")
	}
	if got := err.Error(); !strings.Contains(got, "asks for") {
		t.Fatalf("refusal reads %q; it must name the level, not the knowledge", got)
	}
}

func TestReadingASlipTeachesTheMethodAndKeepsTheSlip(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'starlight_dao_pill_method',1)`)

	result := batch4Result(t, batch4Apply(t, path, world, "recipe.learn", 1,
		map[string]any{"item_id": "starlight_dao_pill_method"}))
	if got := result["recipe"]; got != "Starlight Dao Pill" {
		t.Fatalf("the slip taught %v", got)
	}
	if result["already_known"] != false {
		t.Fatalf("a first reading reported already_known=%v", result["already_known"])
	}
	// Knowledge is recorded...
	if got := storage.ParseInt(actionScalar(t, path,
		`SELECT COUNT(*) FROM character_recipes WHERE user_id=42 AND recipe='Starlight Dao Pill'`)); got != 1 {
		t.Fatalf("character_recipes rows=%d", got)
	}
	// ...and the slip is still theirs, exactly as a manual is. A method passed
	// around a sect is a thing this genre does; destroying it on reading would
	// make a shared inheritance impossible.
	if got := storage.ParseInt(actionScalar(t, path,
		`SELECT quantity FROM inventory WHERE user_id=42 AND item_id='starlight_dao_pill_method'`)); got != 1 {
		t.Fatalf("the slip was consumed: quantity=%d", got)
	}
	// And it says the hands are not ready, without refusing the lesson: a
	// method can be studied long before it can be used.
	if result["ready"] != false {
		t.Fatalf("ready=%v for a level-0 cultivator holding a level-4 method", result["ready"])
	}
}

func TestASlipYouDoNotHoldTeachesNothing(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	err := stage4ApplyError(t, path, world, "recipe.learn", 1,
		map[string]any{"item_id": "starlight_dao_pill_method"})
	if err == nil {
		t.Fatal("a slip nobody holds was read")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_recipes WHERE user_id=42`)); got != 0 {
		t.Fatalf("%d methods learned from a slip that was not held", got)
	}
}

func TestAnOrdinaryItemIsNotAMethodSlip(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',1)`)
	if err := stage4ApplyError(t, path, world, "recipe.learn", 1,
		map[string]any{"item_id": "spirit_herb"}); err == nil {
		t.Fatal("a spirit herb taught a method")
	}
}

// The end-to-end "taught, at level, and it crafts" case is already covered by
// TestCraftResolveDerivesCanonicalAlchemyContextAndTime and
// TestCraftResolveDerivesForgingAndFormationFacilities, which build the
// per-profession facility tables this file deliberately does not, and which
// now seed knowledge through batch4TeachRecipe. Duplicating them here only
// duplicated their fixtures.
