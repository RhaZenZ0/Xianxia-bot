package game

import (
	"fmt"
	"math"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// What a spiritual root is worth (v1.0.0-rc.55).
//
// `spiritual_root_system.grades` authored a cultivation multiplier and a
// breakthrough bonus for every rung and the engine read neither, so the grade
// on a cultivator's sheet decided how they were made and nothing about what
// they were. These hold the wiring behaviourally: the arithmetic is checked
// against the shipped catalogue, and the session is driven through the real
// action, because a grep cannot see a condition somebody disabled.

func rootAt(grade string, purity int) SpiritualRootState {
	return SpiritualRootState{Grade: grade, Purity: purity, Elements: []string{"Fire"}}
}

func TestTheGradeLadderIsWorthWhatTheContentSays(t *testing.T) {
	catalog := qiBodyCatalog(t)
	ladder := catalog.SpiritualRootSystem.Grades
	if len(ladder) < 2 {
		t.Fatalf("the ladder has %d rungs", len(ladder))
	}

	// Common at no purity is the baseline the whole ten-factor product is
	// calibrated against, so it is exact rather than approximate.
	if got := rootWorthMultiplier(catalog, rootAt("Common", 0)); got != 1.0 {
		t.Fatalf("Common at purity 0 is %v, not the 1.0 every other term is priced against", got)
	}

	previous := 0.0
	for _, rung := range ladder {
		worth := rootWorthMultiplier(catalog, rootAt(rung.Name, 0))
		if worth <= previous {
			t.Fatalf("%s is worth %v, no more than the rung below (%v)", rung.Name, worth, previous)
		}
		previous = worth
		if got := rootGradeBreakthroughBonus(catalog, rootAt(rung.Name, 0)); got != rung.BreakthroughBonus {
			t.Fatalf("%s breakthrough bonus reads %d, content says %d", rung.Name, got, rung.BreakthroughBonus)
		}
	}

	// Purity deepens the grade by exactly the figure the root system carries.
	full := rootWorthMultiplier(catalog, rootAt("Heaven", 100))
	none := rootWorthMultiplier(catalog, rootAt("Heaven", 0))
	if want := none * (1 + catalog.SpiritualRootSystem.PurityBonusAtFull); math.Abs(full-want) > 0.0001 {
		t.Fatalf("a pure Heaven root is worth %v, wanted %v", full, want)
	}

	// A grade the ladder does not carry is worth nothing, never the bottom
	// rung: admin.player.set_spiritual_root writes the column unvalidated, and
	// gradeDef would hand such a character Mortal's 0.88 and -1. 'Heavenly'
	// is the real case - it sat in this package's own fixture for releases.
	for _, unknown := range []string{"Heavenly", "", "Divine"} {
		if got := rootWorthMultiplier(catalog, rootAt(unknown, 100)); got != 1.0 {
			t.Fatalf("grade %q is off the ladder and was priced at %v, not 1", unknown, got)
		}
		if got := rootGradeBreakthroughBonus(catalog, rootAt(unknown, 0)); got != 0 {
			t.Fatalf("grade %q is off the ladder and was given %+d to breakthroughs", unknown, got)
		}
	}
}

func TestABetterRootGathersMoreOnBothPaths(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := qiBodyCatalog(t)
	// The session's variance is a d7 (cultivation_actions.go). Lend the dice
	// so the two roots are compared on the same day's work.
	defer gamerng.UseRoller(func(int) int { return 0 })()

	gather := func(seq int, grade string, purity int, body bool) map[string]any {
		batch4Exec(t, path, `UPDATE character_spiritual_roots SET grade=?,purity=? WHERE user_id=42`, grade, purity)
		batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0,body_cultivation=0 WHERE user_id=42`)
		batch4Exec(t, path, `DELETE FROM cooldowns WHERE user_id=42`)
		payload := map[string]any{"cooldown_seconds": 1, "game_minute": 600}
		if body {
			payload["mode"] = "body"
		}
		return batch4Result(t, batch4Apply(t, path, world, "cultivation.train", seq, payload))
	}

	for _, leg := range []struct {
		name string
		body bool
		seq  int
	}{{"qi", false, 7100}, {"body", true, 7200}} {
		mortal := gather(leg.seq, "Mortal", 0, leg.body)
		immortal := gather(leg.seq+1, "Immortal", 100, leg.body)

		low, high := storage.ParseInt(mortal["gain"]), storage.ParseInt(immortal["gain"])
		if high <= low {
			t.Fatalf("%s: an Immortal root and a Mortal root both gathered %d - the grade on the sheet does nothing", leg.name, low)
		}
		wantLow := rootWorthMultiplier(catalog, rootAt("Mortal", 0))
		wantHigh := rootWorthMultiplier(catalog, rootAt("Immortal", 100))
		if got := toFloat(mortal["root_mult"]); math.Abs(got-wantLow) > 0.0001 {
			t.Fatalf("%s: a Mortal root reported %v, content says %v", leg.name, got, wantLow)
		}
		if got := toFloat(immortal["root_mult"]); math.Abs(got-wantHigh) > 0.0001 {
			t.Fatalf("%s: an Immortal root reported %v, content says %v", leg.name, got, wantHigh)
		}

		// The anti-double-count guard. The root's worth used to live inside
		// the element multiplier; if it is ever put back while rootMult
		// stands, element_mult stops being the content's own relation figure
		// and the grade is paid twice.
		if !leg.body {
			for _, result := range []map[string]any{mortal, immortal} {
				relation, _ := result["element_relation"].(string)
				if relation == "" {
					continue
				}
				want := catalog.ElementalQi.Relations[relation].Mult
				if want == 0 {
					want = 1
				}
				if got := toFloat(result["element_mult"]); math.Abs(got-want) > 0.0001 {
					t.Fatalf("element_mult is %v but %q is worth %v in the content - the root is folded in twice", got, relation, want)
				}
			}
		}
	}
}

func TestTheGradeReachesTheBreakthroughRoll(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := qiBodyCatalog(t)

	modifierFor := func(grade string) int64 {
		batch4Exec(t, path, `UPDATE character_spiritual_roots SET grade=?,purity=0 WHERE user_id=42`, grade)
		// cultivation.status is a query, so it carries no state version and
		// goes through the query helper rather than batch4Apply.
		status := cultivationQuery(t, path, world, "cultivation.status", 42)
		odds, _ := status["odds"].(map[string]any)
		if odds == nil {
			t.Fatalf("cultivation.status carried no odds: %v", status)
		}
		return storage.ParseInt(odds["modifier"])
	}

	mortal := modifierFor("Mortal")
	immortal := modifierFor("Immortal")
	spread := int64(rootGradeBreakthroughBonus(catalog, rootAt("Immortal", 0)) - rootGradeBreakthroughBonus(catalog, rootAt("Mortal", 0)))
	if immortal-mortal != spread {
		t.Fatalf("a Mortal root breaks through at %d and an Immortal at %d: a spread of %d, content says %d",
			mortal, immortal, immortal-mortal, spread)
	}
}

// A fixture must carry the constraints production carries. The canonical
// character in this package was seeded with grade 'Heavenly' for releases -
// a name no rung of the ladder has - and nothing noticed, because until
// v1.0.0-rc.55 nothing read a grade for anything. `gradeIndex` answers 0 for a
// name it does not know, so that character silently read as the bottom rung
// the moment the ladder went live. This is the gate on the fixture itself.
func TestEveryFixtureRootStandsOnTheLadder(t *testing.T) {
	path := setupCultivationDB(t)
	catalog := qiBodyCatalog(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT user_id,grade FROM character_spiritual_roots`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		t.Fatal("the cultivation fixture seeds no spiritual root at all")
	}
	for _, row := range res.Rows {
		grade := fmt.Sprint(row[1])
		if _, known := rootGradeDefinition(catalog, grade); !known {
			names := []string{}
			for _, rung := range catalog.SpiritualRootSystem.Grades {
				names = append(names, rung.Name)
			}
			t.Fatalf("user %v is seeded with root grade %q, which is not on the ladder %v - production would read it as worth nothing",
				row[0], grade, names)
		}
	}
}

// A retreat carries what holds for its whole length (v1.0.0-rc.55).
//
// seclusionDailyGainGo called itself "the one copy of the background-
// cultivation rate" while applying six of the ten terms a hand-sat session
// applies. The root was one of the four it dropped, so a cultivator gathered
// at one rate sitting down and another behind a closed door.
// TestSeclusionProjectionMatchesWhatSettlePays holds the two halves of the
// retreat to each other; this holds the retreat to the root.
func TestARetreatCarriesWhatTheRootIsWorth(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location='abode:42' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,property_type,cultivation_level) VALUES(42,'abode:42','Greenriver Town','Quiet Cave','cave_abode',2)`)

	project := func(seq int, grade string, purity int) (int64, float64) {
		batch4Exec(t, path, `UPDATE character_spiritual_roots SET grade=?,purity=? WHERE user_id=42`, grade, purity)
		batch4Exec(t, path, `DELETE FROM seclusion_sessions WHERE user_id=42`)
		start := batch4Result(t, batch4Apply(t, path, world, "seclusion.start", seq,
			map[string]any{"mode": "qi", "duration_game_minutes": 3 * 1440, "game_minute": 1000}))
		return storage.ParseInt(start["projected_daily_gain"]), toFloat(start["root_mult"])
	}

	poor, poorMult := project(8100, "Mortal", 0)
	fine, fineMult := project(8101, "Immortal", 100)
	if fine <= poor {
		t.Fatalf("a Mortal root and an Immortal root both seclude for %d a day - the retreat drops the root", poor)
	}
	if poorMult >= fineMult {
		t.Fatalf("the retreat reported root_mult %v for a Mortal root and %v for an Immortal one", poorMult, fineMult)
	}
}
