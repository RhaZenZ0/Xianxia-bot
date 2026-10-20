package game

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Which coin a quest pays is a fact about the quest, not about where the
// player stands when its last objective is reported (v1.35.0). A commission
// pays in its giver's world's coin and a realm-road stage in the coin of the
// world its realm is in; a quest with no world of its own pays where the
// player stands, as every quest did before. The raid had the same fault and
// the same shape of test (raid_reward_currency_test.go): the pay is driven
// from places that are not the quest's own world, which is the one thing a
// test that stands the player in the right world cannot see.

// spiritualGiver is the first commission giver, by name, who stands in the
// Spiritual World, and a Spiritual place to stand a player at.
func spiritualGiver(t *testing.T, catalog worlddata.Catalog) (giver, place string) {
	t.Helper()
	names := make([]string, 0, len(catalog.CommissionGivers))
	for name := range catalog.CommissionGivers {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if catalog.Locations[commissionGiverHome(catalog, name)].World == "Spiritual World" {
			giver = name
			break
		}
	}
	places := make([]string, 0, len(catalog.Locations))
	for name, loc := range catalog.Locations {
		if loc.World == "Spiritual World" {
			places = append(places, name)
		}
	}
	sort.Strings(places)
	if giver == "" || len(places) == 0 {
		t.Fatalf("the shipped content has no Spiritual commission giver (%q) or no Spiritual place (%d); the check is broken, not the tree", giver, len(places))
	}
	return giver, places[0]
}

func setupQuestCoinDB(t *testing.T) (string, worlddata.Catalog, string, string) {
	t.Helper()
	path := setupCommissionDB(t)
	syncPurse(t, path)
	catalog := crossingCatalog(t)
	mortal, spiritual := worldBaseCurrency(catalog, "Mortal World"), worldBaseCurrency(catalog, "Spiritual World")
	if mortal == spiritual || !strings.Contains(spiritual, "spirit_crystal") {
		t.Fatalf("the Mortal coin (%q) and the Spiritual coin (%q) are not two different coins; the check is broken, not the tree", mortal, spiritual)
	}
	return path, catalog, mortal, spiritual
}

func seedPayingQuest(t *testing.T, path, key, giver string, seed map[string]any) {
	t.Helper()
	seedJSON, _ := json.Marshal(seed)
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,rewards_json,giver_npc,seed_json,created_at,updated_at) VALUES(?,?,'approved','{"spirit_stones":40}',?,?,0,0)`, key, key, giver, string(seedJSON))
}

func payQuest(t *testing.T, path string, catalog worlddata.Catalog, key string, commission bool) map[string]any {
	t.Helper()
	var granted map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		var err error
		if commission {
			granted, err = payCommissionRewardTx(conn, catalog, 42, key, map[string]any{"spirit_stones": 40})
		} else {
			granted, err = grantQuestRewardTx(conn, catalog, 42, key, map[string]any{"spirit_stones": 40})
		}
		return err
	})
	if err != nil {
		t.Fatalf("paying %s: %v", key, err)
	}
	return granted
}

func TestACommissionPaysInItsGiversWorldsCoinWhereverItIsFinished(t *testing.T) {
	path, catalog, _, spiritual := setupQuestCoinDB(t)
	giver, steppe := spiritualGiver(t, catalog)
	seedPayingQuest(t, path, "crystal_errand", giver, nil)
	sheetBefore := sheetStones(t, path)

	// Finished from the Mortal starting town, where the player stands.
	granted := payQuest(t, path, catalog, "crystal_errand", true)
	if fmt.Sprint(granted["currency"]) != spiritual {
		t.Fatalf("%s's commission finished in Greenriver Town paid %v; the giver stands in the Spiritual World and pays %s", giver, granted["currency"], spiritual)
	}
	if got := purseOf(t, path, spiritual); got != 40 {
		t.Fatalf("the purse holds %d %s, want 40", got, spiritual)
	}
	// The sheet shows the money of the world the player stands in (rc.44), so
	// a crystal payment landing in the purse leaves it alone.
	if got := sheetStones(t, path); got != sheetBefore {
		t.Fatalf("the sheet moved from %d to %d on a payment in another world's coin", sheetBefore, got)
	}
	// And finished in the giver's own world, the same coin: the player's
	// position never enters into it.
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, steppe)
	granted = payQuest(t, path, catalog, "crystal_errand", true)
	if fmt.Sprint(granted["currency"]) != spiritual || purseOf(t, path, spiritual) != 80 {
		t.Fatalf("finished at %s the commission paid %v (purse %d)", steppe, granted["currency"], purseOf(t, path, spiritual))
	}
}

func TestARealmRoadStagePaysInItsRealmsWorldsCoin(t *testing.T) {
	path, catalog, mortal, spiritual := setupQuestCoinDB(t)
	// Realm 11 is a Spiritual realm; the player is in the Mortal World.
	if realmWorldGo(catalog, 11) != "Spiritual World" {
		t.Fatalf("realm 11 is in %q; the check wants a Spiritual realm", realmWorldGo(catalog, 11))
	}
	seedPayingQuest(t, path, "realm_road_11_test", "", map[string]any{"realm_index": 11, "follow_on": ""})
	granted := payQuest(t, path, catalog, "realm_road_11_test", false)
	if fmt.Sprint(granted["currency"]) != spiritual || purseOf(t, path, spiritual) != 40 {
		t.Fatalf("a Spiritual realm's stage finished in the Mortal World paid %v (purse %d %s)", granted["currency"], purseOf(t, path, spiritual), spiritual)
	}
	// Realm 0 is a realm: the key is asked for, not its value (rc.28).
	seedPayingQuest(t, path, "realm_road_0_test", "", map[string]any{"realm_index": 0})
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, func() string { _, p := spiritualGiver(t, catalog); return p }())
	granted = payQuest(t, path, catalog, "realm_road_0_test", false)
	if fmt.Sprint(granted["currency"]) != mortal {
		t.Fatalf("a Mortal realm's stage finished in the Spiritual World paid %v, want %s", granted["currency"], mortal)
	}
}

func TestAQuestWithNoWorldOfItsOwnPaysWhereThePlayerStands(t *testing.T) {
	path, catalog, mortal, spiritual := setupQuestCoinDB(t)
	_, steppe := spiritualGiver(t, catalog)
	seedPayingQuest(t, path, "first_steps_test", "", map[string]any{"follow_on": "x"})
	if granted := payQuest(t, path, catalog, "first_steps_test", false); fmt.Sprint(granted["currency"]) != mortal {
		t.Fatalf("a giver-less quest finished in the Mortal World paid %v, want %s", granted["currency"], mortal)
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, steppe)
	if granted := payQuest(t, path, catalog, "first_steps_test", false); fmt.Sprint(granted["currency"]) != spiritual {
		t.Fatalf("a giver-less quest finished in the Spiritual World paid %v, want %s", granted["currency"], spiritual)
	}
	// A quest the definitions table does not know at all pays the same way.
	if granted := payQuest(t, path, catalog, "no_such_quest", false); fmt.Sprint(granted["currency"]) != spiritual {
		t.Fatalf("an unknown quest paid %v, want the coin where the player stands", granted["currency"])
	}
}

// Through the production dispatch: the report that completes a Spiritual
// stage, made in the Mortal World, pays crystals and says so.
func TestQuestProgressPaysTheStagesCoinThroughTheDispatch(t *testing.T) {
	path, catalog, _, spiritual := setupQuestCoinDB(t)
	_ = catalog
	objectives, _ := json.Marshal([]map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}})
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,objectives_json,rewards_json,seed_json,created_at,updated_at) VALUES('realm_road_11_e2e','x','approved',?,'{"spirit_stones":40}','{"realm_index":11}',0,0)`, string(objectives))
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at) VALUES(42,'realm_road_11_e2e','active','{}',0,0,0,0,0)`)
	batch4SetCanonicalGameMinute(t, path, 130)
	raw := payloadJSON(t, map[string]any{"quest_key": "realm_road_11_e2e", "objective_type": "talk", "target": "elder pine", "amount": 1})
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "quest-coin-e2e", Operation: "quest.progress", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	transition, _ := out.Result.(map[string]any)
	granted, _ := transition["rewards_granted"].(map[string]any)
	if fmt.Sprint(granted["currency"]) != spiritual || i64(granted["spirit_stones"]) != 40 {
		t.Fatalf("rewards_granted=%v, want 40 %s", granted, spiritual)
	}
	if got := purseOf(t, path, spiritual); got != 40 {
		t.Fatalf("the purse holds %d %s", got, spiritual)
	}
}

// The content gate: every giver stands in a world with a coin of its own and
// every realm-road stage's realm is in one, so no shipped quest falls through
// to "where the player stands" by accident.
func TestEveryShippedQuestGiverStandsInAWorldWithItsOwnCoin(t *testing.T) {
	catalog := shippedCatalog(t)
	if len(catalog.CommissionGivers) < 10 || len(catalog.RealmRoad) < 10 {
		t.Fatalf("%d givers and %d stages; the reader found too little to be trusted", len(catalog.CommissionGivers), len(catalog.RealmRoad))
	}
	var wrong []string
	coinOf := func(world string) bool {
		def, ok := catalog.Currencies[worldBaseCurrency(catalog, world)]
		return ok && def.World == world
	}
	for name := range catalog.CommissionGivers {
		home := commissionGiverHome(catalog, name)
		if world := catalog.Locations[home].World; home == "" || !coinOf(world) {
			wrong = append(wrong, fmt.Sprintf("giver %s at %q (world %q)", name, home, world))
		}
	}
	for _, stage := range catalog.RealmRoad {
		if !coinOf(realmWorldGo(catalog, stage.RealmIndex)) {
			wrong = append(wrong, fmt.Sprintf("stage %s (realm %d)", stage.QuestKey, stage.RealmIndex))
		}
	}
	sort.Strings(wrong)
	if len(wrong) != 0 {
		t.Fatalf("these quests have no coin of their own to pay in and would pay where the player stands:\n  %s", strings.Join(wrong, "\n  "))
	}
}
