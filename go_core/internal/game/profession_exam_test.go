package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// examDB is the crossing fixture plus the two tables an examination reads and
// writes that it does not already carry. `character_recipes` and
// `faction_reputation` are production's own DDL, primary keys included - a
// fixture that took the rank recipes twice would not fail the way production
// fails.
func examDB(t *testing.T) string {
	t.Helper()
	path := crossingDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',5000)
		ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance`)
	return path
}

// oneHallOf is a shop of a kind in the Mortal World, and the counter its
// keeper stands behind.
//
// The world matters, and the first version of this helper taught that the hard
// way by taking the lowest-named hall of the kind - which is in the Immortal
// World - and then failing on "the examination fee is 40 low immortal stone,
// which you do not have". That is the engine being right: since v1.0.0-rc.44 a
// fee is charged in the money of the world the candidate is standing in, and a
// rank-1 candidate is standing in the Mortal World. A fixture that shops for a
// hall without asking which world it is in is a fixture that cannot model the
// character it is testing.
func oneHallOf(t *testing.T, catalog worlddata.Catalog, kind string) worlddata.Shop {
	t.Helper()
	best := worlddata.Shop{}
	for _, shop := range catalog.Shops {
		if shop.Kind != kind || shop.World != "Mortal World" {
			continue
		}
		if best.Location == "" || shop.Location < best.Location {
			best = shop
		}
	}
	if best.Location == "" {
		t.Fatalf("the Mortal World carries no %s to sit an examination at", kind)
	}
	return best
}

func sitExam(t *testing.T, path string, catalog worlddata.Catalog, trade string, gameMinute int64) (map[string]any, error) {
	t.Helper()
	var out map[string]any
	payload, _ := json.Marshal(map[string]any{"profession": trade, "game_minute": gameMinute})
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		mutation, err := professionExamAction(conn, catalog, 42, payload)
		out, _ = mutation.Result.(map[string]any)
		return err
	})
	return out, err
}

// The content's ladder: every trade examines every rank it has recipes for,
// and every examination is sat somewhere the world actually has.
func TestEveryExaminationHasAHallAndSomethingToTeach(t *testing.T) {
	catalog := crossingCatalog(t)
	if len(catalog.ProfessionExams) != 4 {
		t.Fatalf("four trades examine, got %d", len(catalog.ProfessionExams))
	}
	for trade, exams := range catalog.ProfessionExams {
		for _, exam := range exams {
			t.Run(trade+"/"+exam.RankName, func(t *testing.T) {
				if exam.QuestKey == "" || exam.Title == "" || exam.Opening == "" {
					t.Fatalf("an examination with no quest or no prose: %+v", exam)
				}
				if exam.TN <= 0 || exam.Fee < 0 {
					t.Fatalf("TN %d fee %d", exam.TN, exam.Fee)
				}
				if _, ok := tradeAttribute[trade]; !ok {
					t.Fatalf("%s lives on no attribute", trade)
				}
				halls := 0
				for _, shop := range catalog.Shops {
					if shop.Kind == exam.HallKind {
						halls++
					}
				}
				if halls == 0 {
					t.Fatalf("no %s stands anywhere in the world", exam.HallKind)
				}
				// An examination that teaches nothing is a fee for a
				// certificate, which is not what this is for.
				taught := 0
				for _, recipe := range catalog.Recipes {
					if recipe.Profession == trade && recipe.MinLevel == exam.Rank {
						taught++
					}
				}
				if taught == 0 {
					t.Fatalf("%s rank %d certifies recipes that do not exist", trade, exam.Rank)
				}
			})
		}
	}
}

func TestAnExaminationIsSatAtItsOwnHall(t *testing.T) {
	path := examDB(t)
	catalog := crossingCatalog(t)

	// No rank at all: there is nothing to certify.
	if _, err := sitExam(t, path, catalog, "Forging", 100); err == nil || !strings.Contains(err.Error(), "has not reached the first rank") {
		t.Fatalf("an unranked candidate: err=%v", err)
	}
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
		VALUES(42,'Forging',1,0,0,0,0,0) ON CONFLICT(user_id,profession) DO UPDATE SET level=1`)

	// Standing in the street rather than at a counter, and then at the wrong
	// trade's counter: the refusal names where to go rather than merely saying no.
	forge := oneHallOf(t, catalog, "weaponsmith")
	apothecary := oneHallOf(t, catalog, "apothecary")
	for _, where := range []string{"Greenriver Town", apothecary.Location} {
		batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, where)
		_, err := sitExam(t, path, catalog, "Forging", 100)
		if err == nil || !strings.Contains(err.Error(), "is sat at a") {
			t.Fatalf("sitting Forging at %s: err=%v", where, err)
		}
	}
	// And nothing was charged for a refusal.
	if balance := walletOf(t, path, "low_spirit_stone", 42); balance != 5000 {
		t.Fatalf("a refused examination took %d stones", 5000-balance)
	}

	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, forge.Location)
	defer gamerng.UseRoller(func(int) int { return 9 })() // every die its highest face
	out, err := sitExam(t, path, catalog, "Forging", 100)
	if err != nil {
		t.Fatal(err)
	}
	if out["passed"] != true {
		t.Fatalf("two tens against TN 11 did not pass: %+v", out)
	}
	if out["examiner"] != forge.Keeper || out["shop"] != forge.Name {
		t.Fatalf("the hall's own keeper examines: %+v", out)
	}
	// The Apprentice fee is 40, taken from the world's own money.
	if balance := walletOf(t, path, "low_spirit_stone", 42); balance != 4960 {
		t.Fatalf("balance after the fee: %d", balance)
	}
	// Passing teaches exactly that rank's recipes, and they are the trade's.
	taught := queryRows(t, path, `SELECT recipe FROM character_recipes WHERE user_id=42 AND source='exam' ORDER BY recipe`)
	if len(taught) == 0 {
		t.Fatal("a passed examination taught nothing")
	}
	for _, name := range taught {
		recipe, ok := catalog.Recipes[name]
		if !ok || recipe.Profession != "Forging" || recipe.MinLevel != 1 {
			t.Fatalf("%q is not a rank-1 Forging method", name)
		}
	}
	if standing := i64(actionScalar(t, path, `SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key='craft_hall:Forging'`)); standing != professionExamStanding {
		t.Fatalf("standing with the halls: %d", standing)
	}
	// A pass is held for this life, so the hall will not sit you twice.
	if _, err := sitExam(t, path, catalog, "Forging", 200); err == nil || !strings.Contains(err.Error(), "already hold") {
		t.Fatalf("a second sitting after a pass: err=%v", err)
	}
}

func TestAFailedExaminationCostsTheFeeAndAWorldDay(t *testing.T) {
	path := examDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
		VALUES(42,'Alchemy',1,0,0,0,0,0) ON CONFLICT(user_id,profession) DO UPDATE SET level=1,profession='Alchemy'`)
	hall := oneHallOf(t, catalog, "apothecary")
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, hall.Location)
	// The scenario does most of the work and the dice do the rest, which is
	// the order this repo prefers. The shared fixture character carries a
	// hundred in every attribute - modifier 101 against TN 11 - so even two
	// ones passed by ninety-two and the first version of this test asserted a
	// failure that could not happen. A candidate who is barely there is what
	// makes the refusal certain.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":1,"agility":1,"spirit":1,"insight":1,"will":1,"presence":1}' WHERE user_id=42`)
	restore := gamerng.UseRoller(func(int) int { return 0 })
	out, err := sitExam(t, path, catalog, "Alchemy", 100)
	restore()
	if err != nil {
		t.Fatal(err)
	}
	if out["passed"] != false {
		t.Fatalf("two ones did not fail: %+v", out)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_recipes WHERE user_id=42`)); n != 0 {
		t.Fatalf("a failed examination taught %d methods", n)
	}
	if balance := walletOf(t, path, "low_spirit_stone", 42); balance != 4960 {
		t.Fatalf("the hall charges for the keeper's afternoon either way: %d", balance)
	}
	// Same world day: refused, and told how long.
	if _, err := sitExam(t, path, catalog, "Alchemy", 700); err == nil || !strings.Contains(err.Error(), "look at you again") {
		t.Fatalf("an immediate retry: err=%v", err)
	}
	// A world day later it may be sat again.
	defer gamerng.UseRoller(func(int) int { return 9 })()
	again, err := sitExam(t, path, catalog, "Alchemy", 100+professionExamRetryGameMinutes)
	if err != nil {
		t.Fatal(err)
	}
	if again["passed"] != true {
		t.Fatalf("the retry after the wait: %+v", again)
	}
}

// seedExamDefinitions seeds the quests the halls hand over, the way the bot's
// seeder does from the same file: approved, giver-less, with the objective list
// the content authors. `stripTargets` takes the target off every objective,
// which is the content as it stood before an examination named its own quest -
// the reader the targeted case is held against.
func seedExamDefinitions(t *testing.T, path string, stripTargets bool, trades ...string) {
	t.Helper()
	catalog := crossingCatalog(t)
	for _, trade := range trades {
		for _, exam := range catalog.ProfessionExams[trade] {
			objectives := make([]any, 0, len(exam.Objectives))
			for _, raw := range exam.Objectives {
				objective, _ := raw.(map[string]any)
				copied := map[string]any{}
				for key, value := range objective {
					if stripTargets && key == "target" {
						continue
					}
					copied[key] = value
				}
				objectives = append(objectives, copied)
			}
			encodedObjectives, _ := json.Marshal(objectives)
			encodedRewards, _ := json.Marshal(exam.Rewards)
			batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,source_type,source_key,objectives_json,rewards_json,created_at,updated_at) VALUES(?,?,'approved','system',?,?,?,0,0)`,
				exam.QuestKey, exam.Title, "profession_exam:"+trade, string(encodedObjectives), string(encodedRewards))
		}
	}
}

// markPassed writes a pass the way the hall does, so a test can stand a
// candidate at the point of a world that already holds one.
func markPassed(t *testing.T, path, trade string, rank, life int64) {
	t.Helper()
	payload, _ := json.Marshal(professionExamRecord{Trade: trade, Rank: rank, Life: life, GameMinute: 0, Passed: true})
	batch4Exec(t, path, `INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(42,?,?,0)`, professionExamEvent, string(payload))
}

func setProfessionLevel(t *testing.T, path, trade string, level int64) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
		VALUES(42,?,?,0,0,0,0,0) ON CONFLICT(user_id,profession) DO UPDATE SET level=excluded.level`, trade, level)
}

func offeredRanks(offered []map[string]any) []int64 {
	ranks := []int64{}
	for _, exam := range offered {
		ranks = append(ranks, i64(exam["rank"]))
	}
	return ranks
}

// One Craft All is up to fifty crafts, and a fresh crafter's fifty end at rank
// 4 to 6 - past all three ranks the content examines. The hall used to sit the
// rank held *now*, so the three examinations were handed over as quests, could
// never be sat, and left the trade uncertified for the life.
func TestOneCraftAllCannotStrandAnExamination(t *testing.T) {
	path := examDB(t)
	setupCraftAuthorityTables(t, path)
	catalog := crossingCatalog(t)
	world := batch4WorldPath(t)
	seedExamDefinitions(t, path, false, "Alchemy")
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',100),(42,'beast_core',50)`)
	defer gamerng.UseRoller(func(int) int { return 9 })()

	result, err := craftBatchApply(t, path, world, "f2-all", map[string]any{"recipe": "Recovery Pill", "all": true})
	if err != nil {
		t.Fatal(err)
	}
	prog, _ := result["profession_progress"].(map[string]any)
	held := i64(prog["level"])
	offered, _ := result["exams_offered"].([]map[string]any)
	if held < 4 {
		t.Fatalf("the setup did not carry the crafter past the last examined rank: level %d", held)
	}
	if got := offeredRanks(offered); len(got) != 3 || got[0] != 1 || got[1] != 2 || got[2] != 3 {
		t.Fatalf("one Craft All to rank %d reported the examinations it handed over as ranks %v, want [1 2 3]", held, got)
	}
	if result["exam_offered"] != "exam_alchemy_apprentice" {
		t.Fatalf("exam_offered is the first of them: %v", result["exam_offered"])
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND status='active' AND quest_key LIKE 'exam_alchemy_%'`)); n != 3 {
		t.Fatalf("the crafter holds %d of the three examination quests", n)
	}

	hall := oneHallOf(t, catalog, "apothecary")
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, hall.Location)
	for want := int64(1); want <= 3; want++ {
		out, err := sitExam(t, path, catalog, "Alchemy", 100*want)
		if err != nil {
			t.Fatalf("a crafter carried to rank %d by one Craft All could not sit the rank-%d examination: %v", held, want, err)
		}
		if i64(out["rank"]) != want || out["passed"] != true || i64(out["rank_held"]) != held {
			t.Fatalf("sitting %d: rank=%v held=%v passed=%v", want, out["rank"], out["rank_held"], out["passed"])
		}
	}
	if _, err := sitExam(t, path, catalog, "Alchemy", 900); err == nil || !strings.Contains(err.Error(), "already hold") {
		t.Fatalf("a fourth sitting after three passes: %v", err)
	}
	certified := false
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		var e error
		certified, e = tradeCertifiedTx(conn, 42, soulLifeTx(conn, 42), "Alchemy")
		return e
	}); err != nil {
		t.Fatal(err)
	}
	if !certified {
		t.Fatal("three passes and the trade is still uncertified")
	}
}

// The hall sits the lowest rank this life has reached and not passed, whatever
// rank the candidate holds, and the pass teaches that rank's methods and no
// other's.
func TestTheLowestUncertifiedRankIsSatFirst(t *testing.T) {
	path := examDB(t)
	catalog := crossingCatalog(t)
	// Rank 2 and not 3: the Mortal World's third Alchemy examination teaches
	// nothing at all (its makings are not on any Mortal shelf), so a drill
	// that taught the wrong rank would print an empty list and look green.
	setProfessionLevel(t, path, "Alchemy", 2)
	hall := oneHallOf(t, catalog, "apothecary")
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, hall.Location)
	// The shared fixture's character is a giant who passes on two ones.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":1,"agility":1,"spirit":1,"insight":1,"will":1,"presence":1}' WHERE user_id=42`)

	restore := gamerng.UseRoller(func(int) int { return 0 })
	first, err := sitExam(t, path, catalog, "Alchemy", 100)
	restore()
	if err != nil {
		t.Fatal(err)
	}
	if i64(first["rank"]) != 1 || i64(first["rank_held"]) != 2 || first["passed"] != false {
		t.Fatalf("a candidate holding rank 2 with nothing passed sat rank=%v (held %v, passed %v), want rank 1", first["rank"], first["rank_held"], first["passed"])
	}
	if i64(first["exams_remaining"]) != 2 {
		t.Fatalf("a failed sitting leaves both examinations owed: %v", first["exams_remaining"])
	}
	// The wait is the lowest rank's, and the higher rank is not a way round it.
	if _, err := sitExam(t, path, catalog, "Alchemy", 700); err == nil || !strings.Contains(err.Error(), "look at you again") {
		t.Fatalf("an immediate retry: %v", err)
	}

	defer gamerng.UseRoller(func(int) int { return 9 })()
	second, err := sitExam(t, path, catalog, "Alchemy", 100+professionExamRetryGameMinutes)
	if err != nil {
		t.Fatal(err)
	}
	if i64(second["rank"]) != 1 || second["passed"] != true || i64(second["exams_remaining"]) != 1 {
		t.Fatalf("the sitting a day later: rank=%v passed=%v remaining=%v", second["rank"], second["passed"], second["exams_remaining"])
	}
	// What a pass teaches is the rank passed, not the rank held.
	teachable, _ := rankRecipesWhereTheyCanBeMade(catalog, "Alchemy", 1, hall.World)
	higher, _ := rankRecipesWhereTheyCanBeMade(catalog, "Alchemy", 2, hall.World)
	if len(teachable) == 0 || len(higher) == 0 {
		t.Fatalf("the Mortal World teaches %v at rank 1 and %v at rank 2; the setup cannot tell the ranks apart", teachable, higher)
	}
	learned := queryRows(t, path, `SELECT recipe FROM character_recipes WHERE user_id=42 AND source='exam' ORDER BY recipe`)
	if strings.Join(learned, ",") != strings.Join(teachable, ",") {
		t.Fatalf("passing rank 1 while holding rank 2 taught %v, want exactly the rank-1 methods %v", learned, teachable)
	}
	// And the record is the rank passed: the next sitting is rank 2.
	third, err := sitExam(t, path, catalog, "Alchemy", 100+2*professionExamRetryGameMinutes)
	if err != nil {
		t.Fatal(err)
	}
	if i64(third["rank"]) != 2 || i64(third["exams_remaining"]) != 0 {
		t.Fatalf("after passing rank 1 the hall sat rank=%v (remaining %v), want rank 2 and nothing owed", third["rank"], third["exams_remaining"])
	}
	if _, err := sitExam(t, path, catalog, "Alchemy", 100+3*professionExamRetryGameMinutes); err == nil || !strings.Contains(err.Error(), "already hold every certificate") {
		t.Fatalf("a sitting with nothing owed: %v", err)
	}
}

// A rank reached hands over every examination it has opened, not only its own,
// and one this life has passed is never handed over again.
func TestReachingARankOffersEveryOpenExamination(t *testing.T) {
	path := examDB(t)
	catalog := crossingCatalog(t)
	seedExamDefinitions(t, path, false, "Alchemy")
	offerAt := func(trade string, held int64) []string {
		keys := []string{}
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			offered, err := offerProfessionExamsTx(conn, catalog, 42, trade, held, 500)
			for _, exam := range offered {
				keys = append(keys, exam.QuestKey)
			}
			return err
		}); err != nil {
			t.Fatal(err)
		}
		return keys
	}
	// A tutored Tier 1 rising to Tier 2 is owed both, because tutoring raised
	// the first rank without a craft that could have offered it.
	if got := offerAt("Alchemy", 2); strings.Join(got, ",") != "exam_alchemy_apprentice,exam_alchemy_journeyman" {
		t.Fatalf("reaching Tier 2 with nothing passed handed over %v", got)
	}
	// Offered once: the row is the memory.
	if got := offerAt("Alchemy", 2); len(got) != 0 {
		t.Fatalf("offered twice: %v", got)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42`)); n != 2 {
		t.Fatalf("character_quests rows=%d", n)
	}
	// A pass is never offered again, even where the quest row is gone.
	batch4Exec(t, path, `DELETE FROM character_quests WHERE user_id=42`)
	markPassed(t, path, "Alchemy", 1, 1)
	if got := offerAt("Alchemy", 3); strings.Join(got, ",") != "exam_alchemy_journeyman,exam_alchemy_expert" {
		t.Fatalf("with rank 1 passed, reaching Tier 3 handed over %v", got)
	}
	// Silence rather than an error where there is nothing to hand over: a trade
	// the content examines nobody in, and a definition that is not seeded.
	if got := offerAt("Foraging", 3); len(got) != 0 {
		t.Fatalf("a trade with no examination offered %v", got)
	}
	if got := offerAt("Forging", 3); len(got) != 0 {
		t.Fatalf("a definition not seeded yet offered %v", got)
	}
}

// A crafter whose rank came by another road - the household's tutoring, a GM's
// lever - raised nothing in a craft, so nothing was offered. The counter is the
// other door.
func TestTheCounterHandsOverItsExamination(t *testing.T) {
	path := examDB(t)
	catalog := crossingCatalog(t)
	seedExamDefinitions(t, path, false, "Alchemy")
	setProfessionLevel(t, path, "Alchemy", 1)
	hall := oneHallOf(t, catalog, "apothecary")
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, hall.Location)
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42`)); n != 0 {
		t.Fatalf("the setup already holds %d quests", n)
	}
	defer gamerng.UseRoller(func(int) int { return 9 })()
	out, err := sitExam(t, path, catalog, "Alchemy", 100)
	if err != nil {
		t.Fatal(err)
	}
	if out["passed"] != true {
		t.Fatalf("not passed: %+v", out)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND quest_key='exam_alchemy_apprentice' AND status='active'`)); n != 1 {
		t.Fatalf("the counter handed over %d quests; a pass has nothing to complete", n)
	}
	target := "exam_alchemy_apprentice"
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		_, _, e := questProgressTx(conn, catalog, 42, questPayload{QuestKey: target, ObjectiveType: "profession_exam", Target: &target}, false)
		return e
	}); err != nil {
		t.Fatal(err)
	}
	if status := fmt.Sprint(actionScalar(t, path, `SELECT status FROM character_quests WHERE user_id=42 AND quest_key=?`, target)); status != "completed" {
		t.Fatalf("the pass report left the quest %s", status)
	}
}

// What a pass reports is the examination it sat. Untargeted, one Tier 1 pass
// completed and paid every examination quest a crafter held - all three of a
// trade's, and the other trades' - which is how the engine half of this fix
// alone would have made it worse.
func TestAPassCompletesOnlyItsOwnExaminationQuest(t *testing.T) {
	trades := []string{"Alchemy", "Forging", "Inscription", "Formation"}
	catalog := crossingCatalog(t)
	grantAll := func(path string) []string {
		keys := []string{}
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			for _, trade := range trades {
				for _, exam := range catalog.ProfessionExams[trade] {
					if _, err := grantOrdinaryQuestTx(conn, 42, exam.QuestKey, 10); err != nil {
						return err
					}
					keys = append(keys, exam.QuestKey)
				}
			}
			return nil
		}); err != nil {
			t.Fatal(err)
		}
		return keys
	}
	report := func(path, passed string) {
		active := queryRows(t, path, `SELECT quest_key FROM character_quests WHERE user_id=42 AND status='active'`)
		for _, key := range active {
			held := key
			if err := crossingApply(t, path, func(conn *storage.Conn) error {
				_, _, e := questProgressTx(conn, catalog, 42, questPayload{QuestKey: held, ObjectiveType: "profession_exam", Target: &passed}, false)
				return e
			}); err != nil {
				t.Fatal(err)
			}
		}
	}
	completed := func(path string) []string {
		return queryRows(t, path, `SELECT quest_key FROM character_quests WHERE user_id=42 AND status='completed' ORDER BY quest_key`)
	}

	// The shipped content: each pass completes itself and nothing else, for
	// all twelve, one after another.
	path := examDB(t)
	seedExamDefinitions(t, path, false, trades...)
	keys := grantAll(path)
	if len(keys) != 12 {
		t.Fatalf("the content authors %d examinations, want 12", len(keys))
	}
	done := 0
	for _, key := range keys {
		report(path, key)
		done++
		if got := completed(path); len(got) != done {
			t.Fatalf("a pass of %s left %d quests completed, want %d: %v - its objective, or one still waiting, names no target", key, len(got), done, got)
		}
	}

	// The reader, held against the content as it was: with the targets taken
	// off, one Tier 1 pass completes all twelve. A gate that could not see
	// that would be green over a tree where the targets had never been needed.
	stripped := examDB(t)
	seedExamDefinitions(t, stripped, true, trades...)
	grantAll(stripped)
	report(stripped, "exam_forging_apprentice")
	if got := completed(stripped); len(got) != 12 {
		t.Fatalf("untargeted, one pass completed %d of 12 quests; the reader cannot see the fault it exists for", len(got))
	}
}

// calleesIn is every function a named function calls by bare name, read off
// the file's syntax rather than its spelling.
func calleesIn(t *testing.T, file, function string) map[string]bool {
	t.Helper()
	parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
	if err != nil {
		t.Fatalf("cannot parse %s: %v", file, err)
	}
	callees := map[string]bool{}
	found := false
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != function || fn.Body == nil {
			continue
		}
		found = true
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if call, ok := n.(*ast.CallExpr); ok {
				if ident, ok := call.Fun.(*ast.Ident); ok {
					callees[ident.Name] = true
				}
			}
			return true
		})
	}
	if !found || len(callees) == 0 {
		t.Fatalf("%s carries no %s, or one that calls nothing; the reader is broken, not the tree", file, function)
	}
	return callees
}

// The hall that sits an examination and the craft that offers one answer one
// question, so what is offered and what can be sat cannot disagree: that
// disagreement is the whole of what a stranded crafter was.
func TestTheHallAndTheCraftAskOneRule(t *testing.T) {
	hall := calleesIn(t, "profession_exam.go", "professionExamAction")
	offer := calleesIn(t, "profession_exam.go", "offerProfessionExamsTx")
	craft := calleesIn(t, "crafting_actions.go", "craftOneUnitTx")
	if !hall["professionExamsOpenTx"] {
		t.Fatal("the hall no longer asks professionExamsOpenTx which examination to sit")
	}
	if !offer["professionExamsOpenTx"] {
		t.Fatal("the offer no longer asks professionExamsOpenTx which examinations are open, so what is offered and what can be sat can disagree")
	}
	if !hall["offerProfessionExamsTx"] {
		t.Fatal("the counter hands nothing over, so a rank that came by another road is never caught up")
	}
	if !craft["offerProfessionExamsTx"] {
		t.Fatal("the craft that raises a rank offers no examination")
	}
	// The single-rank forms are gone: a second way to ask is a second rule.
	for _, name := range []string{"professionExamFor", "offerProfessionExamTx"} {
		for _, file := range []string{"profession_exam.go", "crafting_actions.go"} {
			parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			for _, decl := range parsed.Decls {
				if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == name {
					t.Fatalf("%s is back in %s: the examination of the rank held now is the rule that stranded three of them", name, file)
				}
			}
		}
	}
}

func queryRows(t *testing.T, path, query string) []string {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(query, nil)
	if err != nil {
		t.Fatal(err)
	}
	out := make([]string, 0, len(res.Rows))
	for _, row := range res.Rows {
		if len(row) > 0 {
			out = append(out, fmt.Sprint(row[0]))
		}
	}
	return out
}
