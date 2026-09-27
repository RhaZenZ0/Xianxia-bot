package game

import (
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The method you practise and the array you raise (v1.0.0-rc.6).

func manualOfGrade(t *testing.T, catalog worlddata.Catalog, grade string) string {
	t.Helper()
	best := ""
	for id, definition := range catalog.TechniqueSystem.Manuals {
		if definition.Grade == grade && (best == "" || id < best) {
			best = id
		}
	}
	if best == "" {
		t.Fatalf("the catalogue has no %s-grade manual", grade)
	}
	return best
}

func TestTheMethodYouPractiseMultipliesWhatYouGather(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0 WHERE user_id=42`)

	// No method learned: no multiplier, and nothing breaks.
	plain := trainOnce(t, path, world, 500)
	if m, _ := plain["manual_mult"].(float64); m != 1.0 || plain["manual_name"] != "" {
		t.Fatalf("no method: %v x%v", plain["manual_name"], plain["manual_mult"])
	}

	mortal, dao := manualOfGrade(t, catalog, "Mortal"), manualOfGrade(t, catalog, "Dao")
	if _, err := batch4ApplyErr(path, world, "cultivation.manual", 42, 1, map[string]any{"manual_id": dao}); err == nil || !strings.Contains(err.Error(), "has not been learned") {
		t.Fatalf("an unlearned method must be refused, got %v", err)
	}
	batch4Exec(t, path, `INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(42,?,0,0,0,0),(42,?,4,40,0,0)`, mortal, dao)

	// Chosen nothing: the best learned method is the one they gather by.
	best := trainOnce(t, path, world, 501)
	if best["manual_grade"] != "Dao" || best["manual_chosen"] != false {
		t.Fatalf("the default is the best method: %v", best["manual_grade"])
	}
	daoMult, _ := best["manual_mult"].(float64)
	if daoMult <= 1.4 {
		t.Fatalf("a perfected Dao method should be worth half again: %v", daoMult)
	}

	// Choosing the lesser method is allowed, and it gathers less.
	set := batch4Result(t, batch4Apply(t, path, world, "cultivation.manual", 2, map[string]any{"manual_id": mortal}))
	if set["manual_grade"] != "Mortal" || set["changed"] != true {
		t.Fatalf("set: %v", set)
	}
	lesser := trainOnce(t, path, world, 502)
	mortalMult, _ := lesser["manual_mult"].(float64)
	if lesser["manual_chosen"] != true || mortalMult >= daoMult {
		t.Fatalf("a mortal method gathers less: %v vs %v", mortalMult, daoMult)
	}
	status := cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["manual_grade"] != "Mortal" || status["manual_chosen"] != true {
		t.Fatalf("the sheet names the method practised: %v", status["manual_name"])
	}
}

func TestMasteryDeepensTheMethod(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	heaven := manualOfGrade(t, catalog, "Heaven")
	batch4Exec(t, path, `INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(42,?,0,0,0,0)`, heaven)
	opened := batch4Result(t, batch4Apply(t, path, world, "cultivation.manual", 1, map[string]any{"manual_id": heaven}))
	batch4Exec(t, path, `UPDATE character_manuals SET mastery=4 WHERE user_id=42 AND manual_id=?`, heaven)
	perfected := batch4Result(t, batch4Apply(t, path, world, "cultivation.manual", 2, map[string]any{"manual_id": heaven}))
	first, _ := opened["manual_mult"].(float64)
	last, _ := perfected["manual_mult"].(float64)
	if last <= first {
		t.Fatalf("mastery must deepen the method: %v -> %v", first, last)
	}
	if last != round4(manualGradeMultiplier("Heaven")*(1+4*masteryGatheringShare)) {
		t.Fatalf("perfected heaven-grade: %v", last)
	}
}

func TestTheGatheringArrayARaisedInYourOwnHome(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,herb_garden_level,cultivation_level,formation_level) VALUES(42,'abode:42','Greenriver Town','Quiet Cave',0,1,0)`)
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0,location='abode:42' WHERE user_id=42`)
	bare := trainOnce(t, path, world, 600)
	bareMult, _ := bare["place_mult"].(float64)

	batch4Exec(t, path, `UPDATE cave_abodes SET formation_level=5 WHERE user_id=42`)
	arrayed := trainOnce(t, path, world, 601)
	arrayedMult, _ := arrayed["place_mult"].(float64)
	if arrayedMult <= bareMult {
		t.Fatalf("the array must raise the ground: %v -> %v", bareMult, arrayedMult)
	}
	if want := round4(bareMult * (1 + 5*abodeArrayPerLevel)); arrayedMult != want {
		t.Fatalf("array multiplier %v want %v", arrayedMult, want)
	}
	if !strings.Contains(strings.TrimSpace(sprint(arrayed["place_name"])), "gathering array") {
		t.Fatalf("the result names the array: %v", arrayed["place_name"])
	}
	// Closed-door seclusion reads the same array.
	if got := abodeArrayMultiplier(map[string]any{"formation_level": int64(9)}); got != round4(1+9*abodeArrayPerLevel) {
		t.Fatalf("a finished array: %v", got)
	}
	if got := abodeArrayMultiplier(map[string]any{"formation_level": int64(0)}); got != 1 {
		t.Fatalf("no array: %v", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT formation_level FROM cave_abodes WHERE user_id=42`)); got != 5 {
		t.Fatalf("formation_level=%d", got)
	}
}

// Cultivating by a method practises it (v1.11.0). Mastery used to rise only by
// studying the manual again or fighting with its techniques, so weeks of
// sessions with one method left it exactly as mastered as the day it was read.
func TestCultivatingByAMethodPractisesIt(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0 WHERE user_id=42`)
	mortal, dao := manualOfGrade(t, catalog, "Mortal"), manualOfGrade(t, catalog, "Dao")
	batch4Exec(t, path, `INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(42,?,0,2,0,0),(42,?,0,5,0,0)`, mortal, dao)
	batch4Apply(t, path, world, "cultivation.manual", 1, map[string]any{"manual_id": mortal})

	session := trainOnce(t, path, world, 2)
	practised, _ := session["manual_practice"].(map[string]any)
	if practised == nil {
		t.Fatalf("a session by a method must practise it: %v", session)
	}
	if practised["manual_id"] != mortal || storage.ParseInt(practised["practice"]) != 3 || practised["mastery_rose"] != true {
		t.Fatalf("practice 2 -> 3 crosses the first mastery threshold: %v", practised)
	}
	practice := func(id string) int64 {
		t.Helper()
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		res, err := conn.Execute(`SELECT practice FROM character_manuals WHERE user_id=42 AND manual_id=?`, []any{id})
		if err != nil || len(res.Rows) != 1 {
			t.Fatalf("read practice: %v", err)
		}
		return storage.ParseInt(res.Rows[0][0])
	}
	if got := practice(dao); got != 5 {
		t.Fatalf("only the method cultivated by is practised; the Dao manual moved to %d", got)
	}

	// A full stage gathers nothing, so it practises nothing.
	batch4Exec(t, path, `UPDATE characters SET cultivation=100000000 WHERE user_id=42`)
	full := trainOnce(t, path, world, 3)
	if _, ok := full["manual_practice"]; ok {
		t.Fatalf("a session that gathered nothing practised the method: %v", full["manual_practice"])
	}
	if got := practice(mortal); got != 3 {
		t.Fatalf("a full stage must not be a mastery farm: practice %d", got)
	}
}
