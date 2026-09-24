package game

import (
	"encoding/json"
	"fmt"
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

func TestAnExaminationIsSatAtItsOwnHallAndAtTheRankYouHold(t *testing.T) {
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

// The offer, which is the half that makes any of it reachable.
func TestReachingARankOffersItsExamination(t *testing.T) {
	path := examDB(t)
	catalog := crossingCatalog(t)
	key := ""
	for _, exam := range catalog.ProfessionExams["Forging"] {
		if exam.Rank == 1 {
			key = exam.QuestKey
		}
	}
	if key == "" {
		t.Fatal("content authors no Forging Apprentice examination")
	}
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,created_at,updated_at) VALUES(?,'The Apprentice''s Billet',0,0)`, key)

	var offered string
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		var err error
		offered, err = offerProfessionExamTx(conn, catalog, 42, "Forging", 1, 500)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if offered != key {
		t.Fatalf("reaching Apprentice offered %q", offered)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND quest_key=?`, key)); n != 1 {
		t.Fatalf("character_quests rows=%d", n)
	}
	// Offered once: the row is the memory, as it is for every other quest a
	// path hands over without asking.
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		var err error
		offered, err = offerProfessionExamTx(conn, catalog, 42, "Forging", 1, 900)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if offered != "" {
		t.Fatalf("offered twice: %q", offered)
	}
	// A rank the content examines nobody at is silence, not an error - which
	// is what keeps a craft from failing because a quest could not be handed
	// over.
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		var err error
		offered, err = offerProfessionExamTx(conn, catalog, 42, "Forging", 6, 900)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if offered != "" {
		t.Fatalf("an unexamined rank offered %q", offered)
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
