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

// Which coin a raid pays is a fact about the raid, not about the raider. A
// claim is not tied to a place or a time, so reading the money of wherever the
// raider happens to stand paid a Mortal raid in crystals when it was claimed on
// the steppe, a hundred times too much, and a steppe raid claimed from home in
// stones, a hundredth. These tests claim every raid from places that are not
// its lair, which is the one thing the lair-side test cannot see: it passes
// against the broken tree because the raider there stands in the right world.

const raidAwayAtHome = "birth_family:1" // a place with no world: the Mortal stone by fallback

// stageRaidClaim plants one won raid and one unclaimed reward for user 42, who
// is standing at claimAt. The encounter is stored at lairAt under templateKey,
// which is what boss.start writes, so a test can plant a lair the template does
// not name. Production's foreign keys are in the fixture (setupSoloRaidDB).
func stageRaidClaim(t *testing.T, path, claimAt, lairAt, templateKey, bossName string, amount int64, item string, quantity int64) {
	t.Helper()
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, claimAt)
	batch4Exec(t, path, `INSERT INTO parties(party_id,leader_user_id,name,status,created_at,updated_at) VALUES(7,42,'x','finished',0,0)`)
	batch4Exec(t, path, `INSERT INTO boss_encounters(encounter_id,party_id,template_key,location,boss_name,boss_hp,boss_hp_max,status,created_at,updated_at) VALUES(9,7,?,?,?,0,1,'victory',0,0)`, templateKey, lairAt, bossName)
	batch4Exec(t, path, `INSERT INTO boss_reward_claims(encounter_id,user_id,currency_amount,item_id,item_quantity,created_at) VALUES(9,42,?,?,?,0)`, amount, item, quantity)
}

func claimRaid(t *testing.T, path string, catalog worlddata.Catalog) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"id": 9})
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := bossClaimActionGo(conn, catalog, 42, raw)
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

func purseOf(t *testing.T, path, currency string) int64 {
	t.Helper()
	return storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(balance),0) FROM currency_wallets WHERE user_id=42 AND currency_id=?`, currency))
}

func sheetStones(t *testing.T, path string) int64 {
	t.Helper()
	return storage.ParseInt(actionScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`))
}

func TestARaidIsPaidInItsLairsCoinWhereverItIsClaimed(t *testing.T) {
	catalog := crossingCatalog(t)
	mortal, spiritual := worldBaseCurrency(catalog, "Mortal World"), worldBaseCurrency(catalog, "Spiritual World")
	if mortal == spiritual || !strings.Contains(spiritual, "spirit_crystal") {
		t.Fatalf("the Mortal coin (%q) and the Spiritual coin (%q) are not two different coins; the check is broken, not the tree", mortal, spiritual)
	}
	keys := make([]string, 0, len(bossTemplatesGo))
	for k := range bossTemplatesGo {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	lairOf := func(key string) string {
		place, _ := bossLair(catalog, bossTemplatesGo[key])
		return place
	}
	worldOf := func(place string) string { return catalog.Locations[place].World }
	worlds := map[string]bool{}
	for _, k := range keys {
		worlds[worldOf(lairOf(k))] = true
	}
	if len(worlds) != 4 {
		t.Fatalf("the raids stand in %d worlds (%v); the sweep is meant to cover all four, so it is broken or a world lost its raid", len(worlds), worlds)
	}

	var wrong []string
	for _, key := range keys {
		tpl := bossTemplatesGo[key]
		lair := lairOf(key)
		want := worldBaseCurrency(catalog, worldOf(lair))
		// Home, which has no world, and the lair of the first raid standing in
		// some other world: the claim place is never the raid's own world.
		away := ""
		for _, other := range keys {
			if worldOf(lairOf(other)) != worldOf(lair) {
				away = lairOf(other)
				break
			}
		}
		for _, at := range []string{raidAwayAtHome, away} {
			path := setupSoloRaidDB(t)
			stageRaidClaim(t, path, at, lair, key, tpl.Name, tpl.RewardCurrency, tpl.RewardItem, tpl.RewardQuantity)
			out, err := claimRaid(t, path, catalog)
			if err != nil {
				wrong = append(wrong, fmt.Sprintf("%s claimed at %q was refused: %v", key, at, err))
				continue
			}
			if got := fmt.Sprint(out["currency"]); got != want {
				wrong = append(wrong, fmt.Sprintf("%s (lair %q, %s) claimed at %q says it paid %q, want %q", key, lair, worldOf(lair), at, got, want))
			}
			if got := purseOf(t, path, want); got != tpl.RewardCurrency {
				wrong = append(wrong, fmt.Sprintf("%s claimed at %q: the purse holds %d %s, want %d", key, at, got, want, tpl.RewardCurrency))
			}
			var strays int64
			for _, coin := range []string{mortal, spiritual, "low_spirit_crystal"} {
				if coin != want {
					strays += purseOf(t, path, coin)
				}
			}
			if strays != 0 {
				wrong = append(wrong, fmt.Sprintf("%s claimed at %q left %d in a coin of another world", key, at, strays))
			}
		}
	}
	if len(wrong) != 0 {
		t.Fatalf("a raid is paid in its lair's coin whoever claims it and wherever they stand:\n  %s", strings.Join(wrong, "\n  "))
	}
}

// The sheet shows the money of the world the raider stands in and nothing else
// (rc.44), so a Mortal raid claimed on the steppe lands in the purse, where
// crossWorldsPurseTx converts it at the next crossing, and leaves the sheet's
// one number alone. A claim that moved the sheet would show a hundred stones
// of money the Spiritual World does not use.
func TestAClaimAwayFromTheLairLeavesTheSheetAlone(t *testing.T) {
	catalog := crossingCatalog(t)
	boar := bossTemplatesGo["iron_tusk_boar_king"]
	steppe, _ := bossLair(catalog, bossTemplatesGo["hundred_horn_ancestor_stag"])
	greenriver, _ := bossLair(catalog, boar)
	if catalog.Locations[steppe].World == catalog.Locations[greenriver].World {
		t.Fatalf("the steppe and the Boar King's lair are in one world; the check is broken, not the tree")
	}
	path := setupSoloRaidDB(t)
	syncPurse(t, path)
	stageRaidClaim(t, path, steppe, greenriver, "iron_tusk_boar_king", boar.Name, boar.RewardCurrency, boar.RewardItem, boar.RewardQuantity)
	before := sheetStones(t, path)
	out, err := claimRaid(t, path, catalog)
	if err != nil {
		t.Fatalf("the claim was refused: %v", err)
	}
	if got, want := fmt.Sprint(out["currency"]), worldBaseCurrency(catalog, "Mortal World"); got != want {
		t.Fatalf("the Boar King claimed on the steppe says it paid %q, want %q", got, want)
	}
	if got := purseOf(t, path, "low_spirit_stone"); got != boar.RewardCurrency {
		t.Fatalf("the purse holds %d Mortal stones, want %d", got, boar.RewardCurrency)
	}
	if got := sheetStones(t, path); got != before {
		t.Fatalf("the sheet moved from %d to %d for a coin the raider's world does not use", before, got)
	}
}

// The stored lair is the first answer and the template's is the second, and a
// sweep that claims every raid at its own lair cannot tell which one paid
// (rc.53): so each branch is planted where only it can be the answer.
func TestARaidPaysTheEncountersLairBeforeTheTemplates(t *testing.T) {
	catalog := crossingCatalog(t)
	spiritual := worldBaseCurrency(catalog, "Spiritual World")
	steppe, _ := bossLair(catalog, bossTemplatesGo["hundred_horn_ancestor_stag"])
	boar := bossTemplatesGo["iron_tusk_boar_king"]
	stag := bossTemplatesGo["hundred_horn_ancestor_stag"]

	// The Boar King's template stands in the Mortal World; this encounter was
	// fought on the steppe, and the encounter's own place wins.
	path := setupSoloRaidDB(t)
	stageRaidClaim(t, path, raidAwayAtHome, steppe, "iron_tusk_boar_king", boar.Name, 50, "", 0)
	out, err := claimRaid(t, path, catalog)
	if err != nil {
		t.Fatalf("the claim was refused: %v", err)
	}
	if got := fmt.Sprint(out["currency"]); got != spiritual {
		t.Fatalf("a Boar King fought on the steppe paid %q; the encounter's lair is the steppe's, so %q", got, spiritual)
	}

	// An encounter whose stored place has left the catalogue falls back to the
	// template's lair, which here is the steppe.
	path = setupSoloRaidDB(t)
	stageRaidClaim(t, path, raidAwayAtHome, "A Lair No Map Carries", "hundred_horn_ancestor_stag", stag.Name, 50, "", 0)
	out, err = claimRaid(t, path, catalog)
	if err != nil {
		t.Fatalf("a stag whose stored lair left the map was refused though its template still has one: %v", err)
	}
	if got := fmt.Sprint(out["currency"]); got != spiritual {
		t.Fatalf("a stag whose stored lair left the map paid %q; its template's lair is the steppe, so %q", got, spiritual)
	}
}

// With no lair that resolves there is no world to name a coin for, and the
// Mortal stone is the one answer that would look like a value. The claim is
// refused and the reward is left to be claimed again.
func TestARaidWithNoWorldIsRefusedNotPaidInStones(t *testing.T) {
	catalog := crossingCatalog(t)
	path := setupSoloRaidDB(t)
	stageRaidClaim(t, path, raidAwayAtHome, "A Lair No Map Carries", "a_boss_no_one_wrote", "The Unwritten", 50, "beast_core", 2)
	out, err := claimRaid(t, path, catalog)
	if err == nil {
		t.Fatalf("a raid with no lair the world carries was answered %v; it has no coin to be paid in", out)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT claimed FROM boss_reward_claims WHERE encounter_id=9 AND user_id=42`)); n != 0 {
		t.Fatalf("a refused claim marked the reward claimed (%d); it must stay claimable", n)
	}
	if got := purseOf(t, path, "low_spirit_stone"); got != 0 {
		t.Fatalf("a refused claim paid %d Mortal stones", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='beast_core'`)); got != 0 {
		t.Fatalf("a refused claim handed over %d beast cores", got)
	}

	// An item needs no coin, so a reward that is only an item is not stranded
	// by a lair the map lost.
	path = setupSoloRaidDB(t)
	stageRaidClaim(t, path, raidAwayAtHome, "A Lair No Map Carries", "a_boss_no_one_wrote", "The Unwritten", 0, "beast_core", 2)
	if _, err := claimRaid(t, path, catalog); err != nil {
		t.Fatalf("an item-only reward was refused for want of a coin it does not use: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='beast_core'`)); got != 2 {
		t.Fatalf("the item-only claim handed over %d beast cores, want 2", got)
	}
}

// A place the catalogue carries in a world that has no tier-1 coin of its own
// is as unresolved as one it does not carry: worldBaseCurrency would answer the
// Mortal stone for it, which is what the lookup is there to refuse.
func TestAWorldWithNoCoinOfItsOwnPaysNothingByFallback(t *testing.T) {
	catalog := worlddata.Catalog{
		Locations: map[string]worlddata.LocationDefinition{
			"Coinless Hollow": {World: "A World Without Money"},
			"Nameless Hollow": {},
		},
		Currencies: map[string]worlddata.CurrencyDefinition{
			"low_spirit_stone": {Name: "Low-Grade Spirit Stone", World: "Mortal World", Tier: 1},
		},
	}
	for _, place := range []string{"Coinless Hollow", "Nameless Hollow", "Nowhere At All", ""} {
		if coin, ok := raidRewardCurrency(catalog, place, "no_such_template"); ok {
			t.Errorf("%q was answered %q; it names no world with a coin of its own", place, coin)
		}
	}
	catalog.Locations["Mortal Hollow"] = worlddata.LocationDefinition{World: "Mortal World"}
	if coin, ok := raidRewardCurrency(catalog, "Mortal Hollow", "no_such_template"); !ok || coin != "low_spirit_stone" {
		t.Errorf("a place in the Mortal World was answered %q, %v; the check is broken, not the tree", coin, ok)
	}
}
