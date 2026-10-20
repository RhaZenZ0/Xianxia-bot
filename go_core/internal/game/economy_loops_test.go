package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"math/big"
	"os"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The economy's loops (v1.34.0). Goods cross a world boundary untouched and
// money converts at a hundred to one a rung, so every ceiling that holds a
// price under a shelf has to hold it under the cheapest shelf in the whole
// world, read in the ceiling's own coin; a caravan, a merchant and an auction
// floor each had a way of paying above a shelf of their own.

// stonesPerCoin is what one unit of a world's tier-1 coin is worth in the
// Mortal World's stone, worked out here off the raw currency table: the
// worlds are stood in order of their floors and each rung is the lower world's
// own tier-2 base_ratio. It is a third statement of the ladder, beside the
// engine's ladderRate and the content, so the gates below cannot agree with a
// wrong exchange rate by reading the same wrong one.
func stonesPerCoin(t *testing.T, catalog worlddata.Catalog) map[string]*big.Int {
	t.Helper()
	worldByIndex := map[int64]string{}
	rungOf := map[string]int64{}
	for _, def := range catalog.Currencies {
		worldByIndex[worldTierIndex(catalog, def.World)] = def.World
		if def.Tier == 2 {
			rungOf[def.World] = def.BaseRatio
		}
	}
	valueOfWorld := map[string]*big.Int{}
	running := big.NewInt(1)
	for index := int64(0); index <= 3; index++ {
		world, ok := worldByIndex[index]
		if !ok {
			t.Fatalf("no world stands at floor index %d; the ladder reader is broken, not the tree", index)
		}
		valueOfWorld[world] = new(big.Int).Set(running)
		rung := rungOf[world]
		if rung < 1 {
			t.Fatalf("%s has no tier-2 base_ratio", world)
		}
		running = new(big.Int).Mul(running, big.NewInt(rung))
	}
	out := map[string]*big.Int{}
	for id, def := range catalog.Currencies {
		if def.Tier == 1 {
			out[id] = valueOfWorld[def.World]
		}
	}
	return out
}

// worldArbitrageCeilings is every price at which something in the world buys
// an item off a cultivator, in stones: a keeper at the top rank of the trade
// (tradeRankSellPrice), a market counter however high its base (marketUnitPrice
// selling), the town at a stall (NPCStallCeiling) and a sect's points for a
// donation (sectDonationCap, in points a stone buys).
type arbitrageCeiling struct {
	who    string
	amount int64
	coin   string
	stones *big.Int
}

func arbitrageCeilings(t *testing.T, catalog worlddata.Catalog, itemID string, value map[string]*big.Int, coins []string) []arbitrageCeiling {
	t.Helper()
	var out []arbitrageCeiling
	add := func(who, coin string, amount int64) {
		if amount < 1 {
			return
		}
		out = append(out, arbitrageCeiling{who, amount, coin, new(big.Int).Mul(big.NewInt(amount), value[coin])})
	}
	keys := make([]string, 0, len(catalog.Shops))
	for key := range catalog.Shops {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		shop := catalog.Shops[key]
		base, pays := keeperBuyBase(catalog, shop, itemID)
		if !pays {
			continue
		}
		price := base
		if itemTrade(catalog, itemID) != "" {
			price = tradeRankSellPrice(catalog, itemID, shop.Currency, base, tradeTopRank)
		}
		add("keeper "+key, shop.Currency, price)
	}
	for _, coin := range coins {
		add("a market counter", coin, marketUnitPrice(catalog, itemID, coin, 1<<40, 5.0, false))
		if ceiling, ok := NPCStallCeiling(catalog, itemID, coin); ok {
			add("the town at a stall", coin, ceiling)
		}
		// An underworld broker buying ordinary goods (v1.35.0), per unit.
		if fence, ok := blackMarketOrdinaryFence(catalog, itemID, coin); ok {
			add("an underworld broker", coin, fence)
		}
	}
	if cap, ok := sectDonationCap(catalog, itemID); ok {
		add("a sect's donation points", "low_spirit_stone", cap)
	}
	// The merchants on the auction floors (v1.34.0): a merchant's bid and
	// its purchase of an unsold lot are both held to merchantValuation, so
	// that is the ceiling, per unit, in the merchant's own coin.
	merchants := make([]string, 0, len(catalog.Merchants))
	for key := range catalog.Merchants {
		merchants = append(merchants, key)
	}
	sort.Strings(merchants)
	for _, key := range merchants {
		m := catalog.Merchants[key]
		add("the merchant "+key, merchantCurrency(m), merchantValuation(catalog, m, itemID, 1))
	}
	return out
}

// The row the review found, stated: Madam Wen values the Nine-Echo Spatial
// Token at its sect value of 900 while the Stoneback Array Workshop sells it
// for 340, so a lot listed at 900 was bought at 900. Drill: take the shelf
// read out of merchantValuation and this prints the 900.
func TestAMerchantPaysNoMoreThanTheCheapestShelfForALot(t *testing.T) {
	catalog := shippedCatalog(t)
	m, ok := catalog.Merchants["madam_wen_of_the_silk_road"]
	if !ok {
		t.Fatal("madam_wen_of_the_silk_road is no longer in the catalogue; pick another merchant for this row")
	}
	shelf, ok := cheapestShelfPrice(catalog, "nine_echo_spatial_token", merchantCurrency(m))
	if !ok || shelf < 2 {
		t.Fatalf("the Nine-Echo Spatial Token has no shelf in %s (%d, %v); the row cannot be stated", merchantCurrency(m), shelf, ok)
	}
	if value := itemSectValue(catalog, "nine_echo_spatial_token"); value <= shelf {
		t.Fatalf("the token's sect value (%d) no longer sits above its cheapest shelf (%d); the row no longer tests the cap", value, shelf)
	}
	got := merchantValuation(catalog, m, "nine_echo_spatial_token", 1)
	if got >= shelf || got < 1 {
		t.Fatalf("Madam Wen values one Nine-Echo Spatial Token at %d stones while the cheapest shelf sells it for %d: list it at that and the merchant buys it, which prints money", got, shelf)
	}
	if got3 := merchantValuation(catalog, m, "nine_echo_spatial_token", 3); got3 != got*3 {
		t.Fatalf("a lot of three is valued at %d, not three times the unit %d", got3, got)
	}
}

// No world crossing turns a shop into a mint. Drill: read cheapestShelfPrice
// in one coin again (return false from shelfPriceAcross for a different coin)
// and it prints the Azure Flying Sword pair: sold for 260 Low-Grade Spirit
// Stones on a Mortal shelf and paid for by an upper-world keeper in a coin a
// hundred or a million times dearer.
func TestNoWorldCrossingTurnsAShopIntoAMint(t *testing.T) {
	catalog := shippedCatalog(t)
	value := stonesPerCoin(t, catalog)
	coins := make([]string, 0, len(value))
	for coin := range value {
		coins = append(coins, coin)
	}
	sort.Strings(coins)

	type shelf struct {
		where  string
		coin   string
		price  int64
		stones *big.Int
	}
	shelves := map[string][]shelf{}
	for key, shop := range catalog.Shops {
		for _, line := range shop.Sells {
			if line.Price > 0 {
				shelves[line.ItemID] = append(shelves[line.ItemID], shelf{"the " + shop.Name + " (" + key + ")", shop.Currency, line.Price, new(big.Int).Mul(big.NewInt(line.Price), value[shop.Currency])})
			}
		}
	}
	for key, merchant := range catalog.Merchants {
		for _, ware := range merchant.Wares {
			if ware.Price > 0 {
				shelves[ware.ItemID] = append(shelves[ware.ItemID], shelf{"the merchant " + key, merchant.Currency, ware.Price, new(big.Int).Mul(big.NewInt(ware.Price), value[merchant.Currency])})
			}
		}
	}
	items := make([]string, 0, len(shelves))
	for id := range shelves {
		items = append(items, id)
	}
	sort.Strings(items)
	pairs, crossWorld := 0, 0
	// Every item that is sold above a shelf is counted, and the pair that is
	// printed is the Azure Flying Sword's when it is one of them, since the
	// sword is the pair the finding was measured on.
	violations, shown := 0, ""
	for _, itemID := range items {
		for _, ceiling := range arbitrageCeilings(t, catalog, itemID, value, coins) {
			for _, s := range shelves[itemID] {
				pairs++
				if catalog.Currencies[s.coin].World != catalog.Currencies[ceiling.coin].World {
					crossWorld++
				}
				if ceiling.stones.Cmp(s.stones) > 0 {
					violations++
					if shown == "" || itemID == "azure_flying_sword" {
						shown = fmt.Sprintf("%s sells at %s for %d %s (%s stones) and %s pays %d %s (%s stones) for it: buy it on the lowest shelf, carry it across, and the sale prints money",
							itemID, s.where, s.price, catalog.Currencies[s.coin].Name, s.stones, ceiling.who, ceiling.amount, catalog.Currencies[ceiling.coin].Name, ceiling.stones)
					}
				}
			}
		}
	}
	if violations > 0 {
		t.Fatalf("%d (shelf, ceiling) pairs print money; e.g. %s", violations, shown)
	}
	if len(items) < 100 || pairs < 10000 || crossWorld < 8000 {
		t.Fatalf("only %d items, %d pairs and %d cross-world pairs were walked; the reader has stopped seeing the catalogue", len(items), pairs, crossWorld)
	}
}

// The three rows the cap changes, stated so a reader sees the rule and not
// only the walk: a keeper in the Spiritual World no longer pays crystals for a
// Mortal sword that costs stones; one under a whole crystal is not a price.
func TestAnUpperWorldKeeperHoldsItsLineUnderTheLowestShelfAnywhere(t *testing.T) {
	catalog := shippedCatalog(t)
	var spiritual worlddata.Shop
	for _, shop := range catalog.Shops {
		if catalog.Currencies[shop.Currency].World == "Spiritual World" {
			if _, listed := shop.Buys["azure_flying_sword"]; listed {
				spiritual = shop
				break
			}
		}
	}
	if spiritual.Name == "" {
		t.Fatal("no Spiritual keeper lists the Azure Flying Sword; pick another line that shows the cap")
	}
	shelf, ok := cheapestShelfPrice(catalog, "azure_flying_sword", spiritual.Currency)
	if !ok || shelf != 2 {
		t.Fatalf("260 Low-Grade Spirit Stones read as %d (%v) Low Spirit Crystals, want 2 (rounded down)", shelf, ok)
	}
	if base, pays := keeperBuyBase(catalog, spiritual, "azure_flying_sword"); !pays || base != 1 {
		t.Fatalf("%s pays %d (%v) for an Azure Flying Sword, want one crystal under the 2-crystal shelf", spiritual.Name, base, pays)
	}
	if shelf, ok := cheapestShelfPrice(catalog, "spirit_herb", spiritual.Currency); !ok || shelf != 0 {
		t.Fatalf("a 3-stone herb reads as %d (%v) crystals, want 0", shelf, ok)
	}
	// The counter agrees: a market in a crystal city pays nothing for the herb
	// (0 is the refusal marketTradeAction reads) and a coin for the sword.
	if got := marketUnitPrice(catalog, "spirit_herb", spiritual.Currency, 1<<40, 5.0, false); got != 0 {
		t.Fatalf("a market in %s pays %d for a Mortal herb, want 0", spiritual.Currency, got)
	}
	if got := marketUnitPrice(catalog, "azure_flying_sword", spiritual.Currency, 1<<40, 5.0, false); got != 1 {
		t.Fatalf("a market in %s pays %d for a sword the cheapest shelf sells for 2, want 1", spiritual.Currency, got)
	}
	herb := spiritual
	herb.Buys = map[string]int64{"spirit_herb": 40}
	if base, pays := keeperBuyBase(catalog, herb, "spirit_herb"); pays {
		t.Fatalf("a Spiritual keeper pays %d for a herb the cheapest shelf sells for under a crystal", base)
	}
}

// A keeper is refused by the counter exactly where its board stops listing,
// and a board never lists a line the counter would refuse.
func TestAKeeperBoardListsOnlyWhatTheCounterWillPay(t *testing.T) {
	catalog := shippedCatalog(t)
	listed, hidden := 0, 0
	for _, shop := range catalog.Shops {
		if catalog.Currencies[shop.Currency].World == "Mortal World" {
			continue
		}
		for itemID := range shop.Buys {
			if _, pays := keeperBuyBase(catalog, shop, itemID); pays {
				listed++
			} else {
				hidden++
			}
		}
	}
	if listed == 0 || hidden == 0 {
		t.Fatalf("%d lines listed and %d hidden in the upper worlds; the cap has stopped separating them", listed, hidden)
	}
}

// A caravan pays whole coins of the world it leaves, never above the cheapest
// shelf anywhere. Drill: drop the `min64(unit, ...)` from caravanUnitPayout
// and it prints the Void-Stride Talisman: 10,350 Low Celestial Crystals a
// unit against a shelf of 1,800, five and three-quarters times what buying it
// cost.
func TestACaravanPaysNoMoreThanTheCheapestShelf(t *testing.T) {
	catalog := shippedCatalog(t)
	value := stonesPerCoin(t, catalog)
	shelfStones := map[string]*big.Int{}
	for _, shop := range catalog.Shops {
		for _, line := range shop.Sells {
			note := new(big.Int).Mul(big.NewInt(line.Price), value[shop.Currency])
			if line.Price > 0 && (shelfStones[line.ItemID] == nil || note.Cmp(shelfStones[line.ItemID]) < 0) {
				shelfStones[line.ItemID] = note
			}
		}
	}
	for _, merchant := range catalog.Merchants {
		for _, ware := range merchant.Wares {
			note := new(big.Int).Mul(big.NewInt(ware.Price), value[merchant.Currency])
			if ware.Price > 0 && (shelfStones[ware.ItemID] == nil || note.Cmp(shelfStones[ware.ItemID]) < 0) {
				shelfStones[ware.ItemID] = note
			}
		}
	}
	coins := make([]string, 0, len(value))
	for coin := range value {
		coins = append(coins, coin)
	}
	sort.Strings(coins)
	ids := make([]string, 0, len(catalog.Items))
	for id := range catalog.Items {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	checked, unshelved, violations, shown := 0, 0, 0, ""
	for _, coin := range coins {
		for _, id := range ids {
			item, _, _ := itemDef(catalog, id)
			for _, smuggle := range []bool{false, true} {
				unit, payable := caravanUnitPayout(catalog, item, id, coin, smuggle)
				if !payable {
					continue
				}
				checked++
				cheapest := shelfStones[id]
				if cheapest == nil {
					unshelved++
					continue
				}
				paid := new(big.Int).Mul(big.NewInt(unit), value[coin])
				if paid.Cmp(cheapest) > 0 {
					shelfInCoin := new(big.Int).Div(cheapest, value[coin])
					violations++
					// The Void-Stride Talisman is the row the finding was measured on.
					if shown == "" || id == "void_stride_talisman" {
						shown = fmt.Sprintf("%s: a caravan leaving a city of the %s pays %d for each %s and the cheapest shelf in the world asks %s of that coin (%.2fx): buy it there, freight it, and the margin is the profit",
							coin, catalog.Currencies[coin].World, unit, id, shelfInCoin, float64(unit)/float64(max64(1, shelfInCoin.Int64())))
					}
				}
			}
		}
	}
	if violations > 0 {
		t.Fatalf("%d (coin, item) rows pay above the cheapest shelf; e.g. %s", violations, shown)
	}
	if checked < 1000 || unshelved < 100 {
		t.Fatalf("only %d (coin, item, margin) rows were walked, %d of them unshelved; the reader has stopped seeing the catalogue", checked, unshelved)
	}
	// An item no shop sells keeps its worth and the trader's margin.
	for _, id := range ids {
		if shelfStones[id] != nil {
			continue
		}
		item, _, _ := itemDef(catalog, id)
		base := max64(5, item.BasePrice)
		if base <= 5 {
			base = max64(base, item.SectValue)
		}
		if unit, payable := caravanUnitPayout(catalog, item, id, "low_spirit_stone", false); !payable || unit != max64(1, int64(float64(base)*1.15+0.5)) {
			t.Fatalf("%s has no shelf and a caravan pays %d (%v) for it, want its worth with the margin", id, unit, payable)
		}
		break
	}
}

func TestACaravanOutOfAnUpperWorldRefusesCargoWorthLessThanACoin(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	batch4SetCanonicalGameMinute(t, path, 7000)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	caravanTables(t, conn)
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	capital, destination := "Spirit Jade Capital", "Jadeflow Spirit City"
	placeFloorChar(t, path, capital, 3, catalog.Locations[capital].MinRealmIndex)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',10) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=10`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'azure_flying_sword',10) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=10`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_crystal',5000) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=5000`)
	_, err = batch4ApplyErr(path, world, "caravan.dispatch", 42, 1, map[string]any{"destination": destination, "item_id": "spirit_herb", "quantity": 10, "escort": 0})
	if err == nil || !strings.Contains(err.Error(), "a caravan will not carry Spirit Herb") {
		t.Fatalf("ten Mortal herbs went out of a crystal city: err=%v", err)
	}
	if got := escrowScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_crystal'`); got != 5000 {
		t.Fatalf("a refused dispatch left the purse at %d, want 5000", got)
	}
	out, err := batch4ApplyErr(path, world, "caravan.dispatch", 42, 2, map[string]any{"destination": destination, "item_id": "azure_flying_sword", "quantity": 4, "escort": 0})
	if err != nil {
		t.Fatalf("a cargo worth more than a crystal was refused: %v", err)
	}
	if result := batch4Result(t, out); fmt.Sprint(result["payout"]) != "4" {
		t.Fatalf("four swords fetch %v, want one crystal each (the shelf is two)", result["payout"])
	}
}

// A merchant buys an unsold lot only where it would have bid. Drill: take the
// merchantValuation check out of MerchantBuysUnsoldLot and it prints the lot
// and the two numbers.
func TestAMerchantDoesNotBuyAnUnsoldLotAboveItsValuation(t *testing.T) {
	path := setupMerchantDB(t)
	catalog := merchantCatalog(t)
	worth, room := int64(0), int64(1<<62)
	for _, m := range catalog.Merchants {
		worth = max64(worth, merchantValuation(catalog, m, "bone_comb", 1))
		room = min64(room, m.Budget)
	}
	bid := worth + 1
	if bid >= room {
		t.Fatalf("a bid of %d does not fit inside the thinnest purse (%d); the fixture cannot tell valuation from budget", bid, room)
	}
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,active,created_at,ends_at) VALUES(9,'golden_pavilion',42,'bone_comb',1,'low_spirit_stone',?,0,NULL,1,0,0)`, bid)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		res, err := conn.Execute(`SELECT * FROM auctions WHERE auction_id=9`, nil)
		if err != nil {
			t.Fatal(err)
		}
		merchant, bought, err := MerchantBuysUnsoldLot(conn, catalog, firstRowMap(res), 2000)
		if err != nil {
			t.Fatal(err)
		}
		if bought {
			t.Fatalf("%s bought one bone_comb for the seller's starting bid of %d when the most any merchant values it at is %d", merchant, bid, worth)
		}
	})
	if got := escrowScalar(t, path, `SELECT COALESCE(SUM(balance),0) FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`); got != 0 {
		t.Fatalf("the seller was paid %d for a lot nobody bought", got)
	}
}

// The floor's two caller-owned fields. Drill: delete the minimum-length
// refusal and a one-second lot is listed; delete the currency check and a
// Mortal floor lists a lot asked in crystals.
func TestAuctionSellRefusesAShortLotAndAnotherWorldsCoin(t *testing.T) {
	path := setupAuctionSizeDB(t)
	world := batch4WorldPath(t)
	for _, seconds := range []float64{1, 60, 240} {
		err := sellOneLot(t, path, world, seconds/60)
		if err == nil || !strings.Contains(err.Error(), fmt.Sprintf("at least %d minutes", auctionMinLotMinutes)) {
			t.Fatalf("a lot of %.0f seconds was listed on a floor whose minimum is %d minutes: err=%v", seconds, auctionMinLotMinutes, err)
		}
	}
	if err := sellOneLot(t, path, world, auctionMinLotMinutes); err != nil {
		t.Fatalf("the picker's own minimum was refused: %v", err)
	}
	raw := func(coin string) []byte {
		return []byte(fmt.Sprintf(`{"item_id":"nine_yang_fragment","quantity":1,"currency_id":%q,"starting_bid":5,"ends_at":%f}`, coin, nowSeconds()+3600))
	}
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "auction-coin-1", Operation: "auction.sell", ActorID: 42, Payload: raw("low_spirit_crystal")})
	if err == nil || !strings.Contains(err.Error(), "deals in Low-Grade Spirit Stone") {
		t.Fatalf("a Mortal floor listed a lot in crystals: err=%v", err)
	}
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "auction-coin-2", Operation: "auction.sell", ActorID: 42, Payload: raw("low_spirit_stone")}); err != nil {
		t.Fatalf("the floor's own coin was refused: %v", err)
	}
}

// Every house's default currency is its world's own, or the picker offers a
// coin the engine refuses.
func TestEveryHousesDefaultCurrencyIsItsWorldsOwn(t *testing.T) {
	catalog := shippedCatalog(t)
	if len(catalog.AuctionHouses) < 10 {
		t.Fatalf("%d auction houses; the reader has stopped seeing the catalogue", len(catalog.AuctionHouses))
	}
	for key, house := range catalog.AuctionHouses {
		if want := worldBaseCurrency(catalog, catalog.Locations[house.Location].World); house.DefaultCurrency != want {
			t.Errorf("%s defaults to %s and stands in a world whose coin is %s", key, house.DefaultCurrency, want)
		}
	}
}

// A seat is its sect's alone while it lies neutral. Drill: delete the SectHomes
// refusal in territoryClaimActionGo and the claim lands, naming Riverguard
// City and the sect that is seated there.
func TestAPlayerCannotClaimARivalsSeat(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog := eventScopeCatalog(t)
	if owner := SectHomes(catalog)["Riverguard City"]; owner != "Blood River Sect" {
		t.Fatalf("Riverguard City is the seat of %q; the fixture needs a rival's seat", owner)
	}
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL,contribution_points INTEGER NOT NULL DEFAULT 0,influence INTEGER NOT NULL DEFAULT 0,contribution_earned INTEGER NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS territory_state(territory_key TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50, defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS world_history_events(history_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT UNIQUE, event_type TEXT, title TEXT, summary TEXT, significance INTEGER, visibility TEXT, location TEXT, world_name TEXT, faction TEXT, actor_type TEXT, actor_key TEXT, actor_name TEXT, target_type TEXT, target_key TEXT, target_name TEXT, related_user_id INTEGER, related_npc_name TEXT, tags TEXT, game_minute INTEGER, metadata_json TEXT, created_at REAL, updated_at REAL)`)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,'Azure Cloud Sect','Deacon',40,0)`)
	batch4Exec(t, path, `INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES('Riverguard City','Riverguard City','Riverguard City',0)`)
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	_, err := batch4ApplyErr(path, world, "territory.claim", 42, 1, map[string]any{"territory_key": "Riverguard City"})
	if err == nil || !strings.Contains(err.Error(), "Riverguard City is the seat of Blood River Sect") {
		t.Fatalf("a Deacon of Azure Cloud Sect claimed Blood River Sect's neutral seat: err=%v", err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT controller_type||':'||controller_key FROM territory_state WHERE territory_key='Riverguard City'`)); got != "neutral:" {
		t.Fatalf("a refused claim left the seat held as %q", got)
	}
	// The seat's own sect raises its banner there as before.
	batch4Exec(t, path, `UPDATE sect_membership SET sect_name='Blood River Sect' WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "territory.claim", 42, 2, map[string]any{"territory_key": "Riverguard City"}); err != nil {
		t.Fatalf("a sect was refused its own seat: %v", err)
	}
}

// One door for "a seat is its sect's": the world's claim step and a player's
// claim read SectHomes, read as call expressions rather than as text.
func TestAPlayersClaimAndTheWorldsReadTheSameSeats(t *testing.T) {
	for _, file := range []string{"territory_actions.go", "../simulation/sect_claims.go"} {
		src, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := parser.ParseFile(token.NewFileSet(), file, src, 0)
		if err != nil {
			t.Fatal(err)
		}
		called := false
		ast.Inspect(parsed, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			switch fn := call.Fun.(type) {
			case *ast.Ident:
				called = called || fn.Name == "SectHomes"
			case *ast.SelectorExpr:
				called = called || fn.Sel.Name == "SectHomes"
			}
			return true
		})
		if !called {
			t.Errorf("%s does not call SectHomes: the rule that a neutral seat is its sect's alone is written there a second time", file)
		}
	}
}

// An upper-world keeper's board lists what its counter will pay for and the
// counter refuses the rest by name. Drill: take the keeperBuyBase check out
// of shopBuysRows and the board offers a Mortal herb at a Spiritual counter.
func TestAnUpperWorldCounterRefusesWhatItsBoardDoesNotList(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := shopCatalog(t)
	var key string
	keys := make([]string, 0, len(catalog.Shops))
	for k := range catalog.Shops {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		shop := catalog.Shops[k]
		if catalog.Currencies[shop.Currency].World != "Spiritual World" {
			continue
		}
		if _, listed := shop.Buys["spirit_herb"]; listed {
			key = k
			break
		}
	}
	if key == "" {
		t.Fatal("no Spiritual keeper lists the spirit herb; pick another item that shows the cap")
	}
	shop := catalog.Shops[key]
	batch4SetCanonicalGameMinute(t, path, 1000)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, shop.Location)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',3) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=3`)
	browse := shopQuery(t, path, world, "shop.browse")
	listed := map[string]bool{}
	for _, line := range browse["buys"].([]map[string]any) {
		listed[fmt.Sprint(line["item_id"])] = true
	}
	if listed["spirit_herb"] {
		t.Fatalf("%s lists a Mortal herb at a counter that pays crystals", shop.Name)
	}
	want := 0
	for itemID := range shop.Buys {
		if _, pays := keeperBuyBase(catalog, shop, itemID); pays {
			want++
		}
	}
	if len(listed) != want || want == 0 {
		t.Fatalf("the board lists %d lines and the counter pays for %d", len(listed), want)
	}
	raw, _ := json.Marshal(map[string]any{"item_id": "spirit_herb", "quantity": 1})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "upper-herb-sell", Operation: "shop.sell", ActorID: 42, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "will not pay for Spirit Herb") {
		t.Fatalf("a Spiritual counter bought a Mortal herb: err=%v", err)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`); got != 3 {
		t.Fatalf("a refused sale took herbs: %d left", got)
	}
}
