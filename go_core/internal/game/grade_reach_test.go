package game

import (
	"encoding/json"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Where a grade reaches (v1.12.3).
//
// v1.7.0 gave a crafted item a grade and said what a grade does: it scales an
// item's use where the item is used. Four places were left reading the base
// item: a disk's deployment wrote the definition's own effect and duration, a
// trade offer named the base item, a recovery item's reply printed the base qi
// figure and the ungraded name, and the capped-grade reply named Tier 7 - a
// rank no trade reaches - when the road to the top grade is rank 6 with an
// opener. Every test here is driven against the shipped content: whether
// `minor_warding_array_disk@high` names anything is the recipe roster's to say.

func deployADisk(t *testing.T, itemID string) (modifiers map[string]float64, ends int64) {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL DEFAULT '')`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,?,1)`, itemID)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"item_id": itemID, "game_minute": 1000})
	mut, err := deployArrayActionGo(conn, gradeCatalog(t), 42, raw)
	if err != nil {
		t.Fatalf("%s was not deployed: %v", itemID, err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	out := mut.Result.(map[string]any)
	stored := actionScalar(t, path, `SELECT effect_json FROM deployed_location_arrays LIMIT 1`).(string)
	var effect struct {
		Modifiers []struct {
			Stat  string  `json:"stat"`
			Value float64 `json:"value"`
		} `json:"modifiers"`
	}
	if err := json.Unmarshal([]byte(stored), &effect); err != nil {
		t.Fatal(err)
	}
	modifiers = map[string]float64{}
	for _, m := range effect.Modifiers {
		modifiers[m.Stat] = m.Value
	}
	return modifiers, i64(out["ends_game_minute"])
}

// A High disk is one and a half times a Low one, and lasts one and a half times
// as long - the rule item use applies (effect multiplier on a modifier and on
// the duration). Deploying the High one first also proves the shared
// definition is copied rather than scaled in place: the Low disk after it must
// still read the authored numbers.
func TestAHighArrayDiskDeploysStrongerAndLongerThanALowOne(t *testing.T) {
	high, highEnds := deployADisk(t, "minor_warding_array_disk@high")
	low, lowEnds := deployADisk(t, "minor_warding_array_disk")
	if low["will"] != 1 || low["combat_bonus"] != 1 {
		t.Fatalf("a Low disk deployed at %v, want the authored will +1 and combat +1 (the shared definition was scaled in place?)", low)
	}
	if high["will"] != 1.5 || high["combat_bonus"] != 1.5 {
		t.Fatalf("a High disk deployed at %v; its grade did nothing (want will +1.5, combat +1.5)", high)
	}
	if lowEnds != 1000+360 || highEnds != 1000+540 {
		t.Fatalf("the Low disk ends at %d and the High at %d, want 1360 and 1540: the duration is not scaled by the grade", lowEnds, highEnds)
	}
}

func TestAnArrayDisksMultiplierIsScaledByItsDistanceFromOne(t *testing.T) {
	high, _ := deployADisk(t, "minor_qi_gathering_array_disk@high")
	// x1.10 at grade x1.5 is x1.15: a multiplier's distance from 1 is scaled,
	// the rule gradedEffectPayload states for an item's effect.
	if got := high["cultivation_gain"]; got < 1.149 || got > 1.151 {
		t.Fatalf("a High qi-gathering disk multiplies cultivation by %v, want 1.15", got)
	}
}

func TestATradeOfferNamesTheGradeOfWhatIsOffered(t *testing.T) {
	catalog := gradeCatalog(t)
	view := tradeItemsView(catalog, map[string]int64{"qi_pill": 2, "qi_pill@high": 1})
	names := map[string]string{}
	for _, row := range view {
		names[row["item_id"].(string)] = row["name"].(string)
	}
	if names["qi_pill"] == names["qi_pill@high"] || names["qi_pill@high"] != itemDisplayName(catalog, "qi_pill@high") {
		t.Fatalf("an offer names a Low and a High pill %q and %q; the grade is hidden from the person being asked to accept it", names["qi_pill"], names["qi_pill@high"])
	}
}

func TestARecoveryItemReplyReportsTheGradedFiguresAndName(t *testing.T) {
	path := setupCombatRecoveryItemDB(t)
	catalog := combatRecoveryItemCatalog()
	catalog.ItemGrades = gradeCatalog(t).ItemGrades
	catalog.Items["healing_pill"] = worlddata.Item{
		Name: "Healing Pill",
		Use:  worlddata.ItemUse{Instant: worlddata.ItemInstantUse{VitalityRestore: 12, QiRestore: 4}},
	}
	catalog.Recipes = map[string]worlddata.Recipe{"Healing Pill": {Profession: "Alchemy", Output: map[string]int64{"healing_pill": 1}}}
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(101,'healing_pill@high',1)`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 9, "item_id": "healing_pill@high", "game_minute": 100})
	mut, err := combatRecoveryItemAction(conn, catalog, 101, raw)
	if err != nil {
		t.Fatalf("a High pill was refused: %v", err)
	}
	out := mut.Result.(map[string]any)
	if i64(out["vitality_restore"]) != 18 || i64(out["qi_restore"]) != 6 {
		t.Fatalf("a High pill reported +%v vitality and +%v qi, want +18 and +6 (x1.5 of 12 and 4): the reply printed the base figure",
			out["vitality_restore"], out["qi_restore"])
	}
	if out["item_name"] != "Healing Pill (High)" {
		t.Fatalf("the reply named the pill %q, want its name at its grade", out["item_name"])
	}
}

// The top grade asks rank 7 and a trade stops rising at rank 6, so a reply that
// said "needs Tier 7" named a rank nobody reaches. The real road is the lower
// rank with something that opens the grade.
func TestTheTopGradeNamesTheRankAndOpenerThatReallyMakeIt(t *testing.T) {
	catalog := gradeCatalog(t)
	top := len(catalog.ItemGrades.Grades) - 1
	rank, needsOpener := craftGradeRequirement(catalog, top)
	if rank != 6 || !needsOpener {
		t.Fatalf("Transcendent reads as rank %d (opener=%v); a trade stops at rank 6 and the road is rank 6 with an opener", rank, needsOpener)
	}
	if rank, needsOpener := craftGradeRequirement(catalog, 2); rank != catalog.ItemGrades.Grades[2].MinRank || needsOpener {
		t.Fatalf("a rung with no opener reads as rank %d (opener=%v), want its own min_rank and none", rank, needsOpener)
	}
	for trade, want := range map[string]string{
		"Alchemy": "a fully refined flame", "Forging": "a fully refined flame",
		"Formation": "a fully built spirit sense", "Inscription": "a fully built spirit sense", "Mining": "",
	} {
		if got := craftGradeOpener(catalog, trade); got != want {
			t.Errorf("%s is opened by %q, want %q", trade, got, want)
		}
	}
}
