package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Travelling merchants (v0.34.1): they walk their loop on the tick, take an
// unsold lot at its starting bid, and sell it back at a markup to a player
// in the same city or on the same stretch of road.

const merchantTestTables = `
CREATE TABLE merchant_state(
	merchant TEXT PRIMARY KEY, location TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
	depart_game_minute INTEGER NOT NULL DEFAULT 0, arrive_game_minute INTEGER NOT NULL DEFAULT 0,
	dwell_until_game_minute INTEGER NOT NULL DEFAULT 0, budget INTEGER NOT NULL DEFAULT 0,
	route_index INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE merchant_stock(
	merchant TEXT NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
	cost INTEGER NOT NULL DEFAULT 0, price INTEGER NOT NULL DEFAULT 0,
	acquired_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0,
	PRIMARY KEY(merchant,item_id)
);
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,world_name TEXT,updated_at REAL);
`

func setupMerchantDB(t *testing.T) string {
	t.Helper()
	path := setupEscrowDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(merchantTestTables + `
ALTER TABLE auctions ADD COLUMN merchant_buyer TEXT NOT NULL DEFAULT '';
ALTER TABLE auctions ADD COLUMN merchant_bidder TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS auction_bids(bid_id INTEGER PRIMARY KEY AUTOINCREMENT, auction_id INTEGER NOT NULL, bidder_user_id INTEGER NOT NULL, amount INTEGER NOT NULL, created_at REAL NOT NULL DEFAULT 0);
INSERT INTO npc_civilization_state VALUES('Old Hu the Peddler','Greenriver Town','Greenriver Town','Mortal World',0);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func merchantCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(catalog.Merchants) == 0 {
		t.Fatal("world content ships no merchants")
	}
	return catalog
}

func withMerchantConn(t *testing.T, path string, fn func(conn *storage.Conn)) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fn(conn)
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestMerchantsWalkTheirLoopOnTheTick(t *testing.T) {
	path := setupMerchantDB(t)
	catalog := merchantCatalog(t)
	hu := catalog.Merchants["old_hu_the_peddler"]
	// First tick: every merchant is seeded at home with its purse.
	withMerchantConn(t, path, func(conn *storage.Conn) {
		moved, err := AdvanceMerchants(conn, catalog, 1000)
		if err != nil {
			t.Fatal(err)
		}
		if moved != int64(len(catalog.Merchants)) {
			t.Fatalf("seeded=%d want %d", moved, len(catalog.Merchants))
		}
	})
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM merchant_state WHERE merchant='old_hu_the_peddler'`)); got != hu.Home {
		t.Fatalf("seeded location=%q want home %q", got, hu.Home)
	}
	if got := escrowScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant='old_hu_the_peddler'`); got != hu.Budget {
		t.Fatalf("budget=%d want %d", got, hu.Budget)
	}
	// Before the dwell runs out nothing moves.
	withMerchantConn(t, path, func(conn *storage.Conn) {
		moved, err := AdvanceMerchants(conn, catalog, 1000+hu.DwellMinutes-1)
		if err != nil {
			t.Fatal(err)
		}
		if moved != 0 {
			t.Fatalf("moved=%d before the dwell ran out", moved)
		}
	})
	// After it, Old Hu sets off for the next stop and the NPC is on the road.
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := AdvanceMerchants(conn, catalog, 1000+hu.DwellMinutes); err != nil {
			t.Fatal(err)
		}
	})
	next := hu.Route[(merchantRouteIndex(hu, hu.Home)+1)%int64(len(hu.Route))]
	if got := fmt.Sprint(actionScalar(t, path, `SELECT destination FROM merchant_state WHERE merchant='old_hu_the_peddler'`)); got != next {
		t.Fatalf("destination=%q want %q", got, next)
	}
	arrive := escrowScalar(t, path, `SELECT arrive_game_minute FROM merchant_state WHERE merchant='old_hu_the_peddler'`)
	if arrive <= 1000+hu.DwellMinutes {
		t.Fatalf("arrive=%d is not after departure", arrive)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT current_location FROM npc_civilization_state WHERE npc_name='Old Hu the Peddler'`)); !strings.HasPrefix(got, "On the road: ") {
		t.Fatalf("npc location=%q, want on the road", got)
	}
	// Arrival puts him in the next city and the NPC with him.
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := AdvanceMerchants(conn, catalog, arrive); err != nil {
			t.Fatal(err)
		}
	})
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location||'|'||destination FROM merchant_state WHERE merchant='old_hu_the_peddler'`)); got != next+"|" {
		t.Fatalf("after arrival=%q want %q", got, next+"|")
	}
	// In a city with an inn (v0.38.0) the merchant is found at its corner table.
	wantNPC := next
	if inn := cityInn(catalog, next); inn != "" {
		wantNPC = inn
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT current_location FROM npc_civilization_state WHERE npc_name='Old Hu the Peddler'`)); got != wantNPC {
		t.Fatalf("npc location=%q want %q", got, wantNPC)
	}
}

func TestAnUnsoldLotIsTakenByAMerchantAtItsStartingBid(t *testing.T) {
	path := setupMerchantDB(t)
	catalog := merchantCatalog(t)
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,active,created_at,ends_at) VALUES(7,'golden_pavilion',42,'bone_comb',2,'low_spirit_stone',30,0,NULL,1,0,0)`)
	var merchant string
	var bought bool
	withMerchantConn(t, path, func(conn *storage.Conn) {
		res, err := conn.Execute(`SELECT * FROM auctions WHERE auction_id=7`, nil)
		if err != nil {
			t.Fatal(err)
		}
		merchant, bought, err = MerchantBuysUnsoldLot(conn, catalog, firstRowMap(res), 2000)
		if err != nil {
			t.Fatal(err)
		}
	})
	if !bought || merchant == "" {
		t.Fatalf("no merchant took the lot (bought=%v merchant=%q)", bought, merchant)
	}
	m := catalog.Merchants[merchant]
	if !merchantRouteHas(m, "Greenriver Town") {
		t.Fatalf("%s bought at Greenriver Town but does not pass it: %v", merchant, m.Route)
	}
	if got := escrowScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`); got != 30 {
		t.Fatalf("seller paid %d, want the starting bid 30", got)
	}
	if got := escrowScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant=?`, merchant); got != m.Budget-30 {
		t.Fatalf("budget=%d want %d", got, m.Budget-30)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM merchant_stock WHERE merchant=? AND item_id='bone_comb'`, merchant); got != 2 {
		t.Fatalf("stock quantity=%d want 2", got)
	}
	price := escrowScalar(t, path, `SELECT price FROM merchant_stock WHERE merchant=? AND item_id='bone_comb'`, merchant)
	if price < 15 {
		t.Fatalf("resale price=%d is below the unit cost", price)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT merchant_buyer FROM auctions WHERE auction_id=7`)); got != merchant {
		t.Fatalf("merchant_buyer=%q want %q", got, merchant)
	}
	// An empty purse leaves the lot alone.
	batch4Exec(t, path, `UPDATE merchant_state SET budget=0`)
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,active,created_at,ends_at) VALUES(8,'golden_pavilion',42,'bone_comb',1,'low_spirit_stone',30,0,NULL,1,0,0)`)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		res, _ := conn.Execute(`SELECT * FROM auctions WHERE auction_id=8`, nil)
		_, bought, err := MerchantBuysUnsoldLot(conn, catalog, firstRowMap(res), 2000)
		if err != nil {
			t.Fatal(err)
		}
		if bought {
			t.Fatal("a merchant with no purse bought a lot")
		}
	})
}

func merchantBuy(t *testing.T, path, world string, seq int, payload map[string]any) (ActionResponse, error) {
	t.Helper()
	raw, _ := json.Marshal(payload)
	return ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("merchant-buy-%d", seq), Operation: "merchant.buy", ActorID: 42, Payload: raw})
}

func TestAPlayerBuysFromAMerchantOnlyWhereTheyMeet(t *testing.T) {
	path := setupMerchantDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	batch4Exec(t, path, `INSERT INTO merchant_state(merchant,location,destination,dwell_until_game_minute,budget,route_index) VALUES('old_hu_the_peddler','Riverguard City','',9000,100,2)`)
	batch4Exec(t, path, `INSERT INTO merchant_stock(merchant,item_id,quantity,cost,price,acquired_game_minute) VALUES('old_hu_the_peddler','spirit_herb',3,10,16,2000)`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',40)`)
	// The player is in Greenriver Town; Old Hu is in Riverguard City.
	_, err := merchantBuy(t, path, world, 1, map[string]any{"merchant": "old_hu_the_peddler", "item_id": "spirit_herb", "quantity": 1})
	if err == nil || !strings.Contains(err.Error(), "not within reach") {
		t.Fatalf("buying from a merchant in another city should be refused, got %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	out, err := merchantBuy(t, path, world, 2, map[string]any{"merchant": "old_hu_the_peddler", "item_id": "spirit_herb", "quantity": 2})
	if err != nil {
		t.Fatal(err)
	}
	result := batch4Result(t, out)
	if got := storage.ParseInt(result["total"]); got != 32 {
		t.Fatalf("total=%d want 32", got)
	}
	if got := storage.ParseInt(result["balance"]); got != 8 {
		t.Fatalf("balance=%d want 8", got)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`); got != 2 {
		t.Fatalf("inventory=%d want 2", got)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM merchant_stock WHERE merchant='old_hu_the_peddler' AND item_id='spirit_herb'`); got != 1 {
		t.Fatalf("stock left=%d want 1", got)
	}
	if got := escrowScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant='old_hu_the_peddler'`); got != 132 {
		t.Fatalf("budget=%d want 132", got)
	}
	// More than the pack holds, or more than the purse holds, is refused whole.
	_, err = merchantBuy(t, path, world, 3, map[string]any{"merchant": "old_hu_the_peddler", "item_id": "spirit_herb", "quantity": 2})
	if err == nil || !strings.Contains(err.Error(), "only 1") {
		t.Fatalf("over-buying should name what is left, got %v", err)
	}
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=0 WHERE user_id=42`)
	_, err = merchantBuy(t, path, world, 4, map[string]any{"merchant": "old_hu_the_peddler", "item_id": "spirit_herb", "quantity": 1})
	if err == nil || !strings.Contains(err.Error(), "not enough") {
		t.Fatalf("an empty wallet should be refused, got %v", err)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM merchant_stock WHERE merchant='old_hu_the_peddler' AND item_id='spirit_herb'`); got != 1 {
		t.Fatalf("a refused sale changed the stock: %d", got)
	}
}

func TestARoadsideTradeIsTheOneThingATravellerMayDo(t *testing.T) {
	path := setupMerchantDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	// The player is mid-journey from Riverguard City to Azure Crown; Old Hu
	// walks the same leg the other way.
	transit, _ := json.Marshal(roadTransitState{Origin: "Riverguard City", Destination: "Azure Crown Imperial City", Route: []string{"Riverguard City", "Azure Crown Imperial City"}, DepartureGameMinute: 2900, ArrivalGameMinute: 3500})
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,0)`, roadTransitStateKey(42), string(transit))
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO merchant_state(merchant,location,destination,depart_game_minute,arrive_game_minute,budget,route_index) VALUES('old_hu_the_peddler','Azure Crown Imperial City','Riverguard City',2800,3600,100,1)`)
	batch4Exec(t, path, `INSERT INTO merchant_stock(merchant,item_id,quantity,cost,price,acquired_game_minute) VALUES('old_hu_the_peddler','spirit_herb',3,10,16,2000)`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',40)`)
	// Any other action waits for arrival...
	raw, _ := json.Marshal(map[string]any{"item_id": "spirit_herb", "quantity": 1, "currency_id": "low_spirit_stone", "starting_bid": 5, "ends_at": nowSeconds() + 3600})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "merchant-road-sell", Operation: "auction.sell", ActorID: 42, Payload: raw}); err == nil {
		t.Fatal("an auction listing mid-journey should be refused by the transit gate")
	}
	// ...but the roadside trade goes through.
	out, err := merchantBuy(t, path, world, 10, map[string]any{"merchant": "old_hu_the_peddler", "item_id": "spirit_herb", "quantity": 1})
	if err != nil {
		t.Fatalf("roadside trade refused: %v", err)
	}
	result := batch4Result(t, out)
	if result["on_the_road"] != true {
		t.Fatalf("the trade should say it happened on the road: %v", result)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`); got != 1 {
		t.Fatalf("inventory=%d want 1", got)
	}
	// The status query agrees on who is within reach.
	status, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "merchant-road-status", Operation: "merchant.status", ActorID: 42, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	view := batch4Result(t, status)
	road, _ := view["actor_road"].(map[string]any)
	if road == nil || fmt.Sprint(road["from"]) != "Riverguard City" {
		t.Fatalf("actor_road=%v", view["actor_road"])
	}
	merchants, _ := view["merchants"].([]map[string]any)
	met := 0
	for _, row := range merchants {
		if row["meetable"] == true {
			met++
			if fmt.Sprint(row["merchant"]) != "old_hu_the_peddler" {
				t.Fatalf("unexpected meetable merchant %v", row["merchant"])
			}
		}
	}
	if met != 1 {
		t.Fatalf("meetable merchants=%d want 1: %v", met, view)
	}
}

func TestATravellerIsToldWhichMerchantsTheRoadHolds(t *testing.T) {
	path := setupMerchantDB(t)
	catalog := merchantCatalog(t)
	batch4Exec(t, path, `INSERT INTO merchant_state(merchant,location,destination,depart_game_minute,arrive_game_minute,budget,route_index) VALUES('old_hu_the_peddler','Azure Crown Imperial City','Riverguard City',2800,3600,100,1)`)
	batch4Exec(t, path, `INSERT INTO merchant_state(merchant,location,destination,dwell_until_game_minute,budget,route_index) VALUES('madam_wen_of_the_silk_road','Jadewood Medicine City','',9000,100,0)`)
	var encounters []map[string]any
	withMerchantConn(t, path, func(conn *storage.Conn) {
		var err error
		encounters, err = merchantEncountersOnRoute(conn, catalog, []string{"Riverguard City", "Azure Crown Imperial City"}, 3000)
		if err != nil {
			t.Fatal(err)
		}
	})
	if len(encounters) != 1 || fmt.Sprint(encounters[0]["merchant"]) != "old_hu_the_peddler" || fmt.Sprint(encounters[0]["met"]) != "road" {
		t.Fatalf("encounters=%v", encounters)
	}
	withMerchantConn(t, path, func(conn *storage.Conn) {
		var err error
		encounters, err = merchantEncountersOnRoute(conn, catalog, []string{"Riverguard City", "Jadewood Medicine City"}, 3000)
		if err != nil {
			t.Fatal(err)
		}
	})
	if len(encounters) != 1 || fmt.Sprint(encounters[0]["merchant"]) != "madam_wen_of_the_silk_road" || fmt.Sprint(encounters[0]["met"]) != "city" {
		t.Fatalf("encounters=%v", encounters)
	}
}

func TestAMerchantsOwnShopIsStockedAtSeedAndRestockedAtHome(t *testing.T) {
	path := setupMerchantDB(t)
	catalog := merchantCatalog(t)
	hu := catalog.Merchants["old_hu_the_peddler"]
	if len(hu.Wares) == 0 {
		t.Fatal("Old Hu ships no wares")
	}
	first := hu.Wares[0]
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := AdvanceMerchants(conn, catalog, 1000); err != nil {
			t.Fatal(err)
		}
	})
	if got := escrowScalar(t, path, `SELECT quantity FROM merchant_stock WHERE merchant='old_hu_the_peddler' AND item_id=?`, first.ItemID); got != first.Quantity {
		t.Fatalf("seeded %s=%d want %d", first.ItemID, got, first.Quantity)
	}
	if got := escrowScalar(t, path, `SELECT price FROM merchant_stock WHERE merchant='old_hu_the_peddler' AND item_id=?`, first.ItemID); got != first.Price {
		t.Fatalf("price=%d want the content price %d", got, first.Price)
	}
	// Sold down to one; away from home nothing refills; back home it does.
	batch4Exec(t, path, `UPDATE merchant_stock SET quantity=1 WHERE merchant='old_hu_the_peddler' AND item_id=?`, first.ItemID)
	away := hu.Route[(merchantRouteIndex(hu, hu.Home)+1)%int64(len(hu.Route))]
	batch4Exec(t, path, `UPDATE merchant_state SET location=?,destination=?,depart_game_minute=2000,arrive_game_minute=2100 WHERE merchant='old_hu_the_peddler'`, hu.Home, away)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := AdvanceMerchants(conn, catalog, 2100); err != nil {
			t.Fatal(err)
		}
	})
	if got := escrowScalar(t, path, `SELECT quantity FROM merchant_stock WHERE merchant='old_hu_the_peddler' AND item_id=?`, first.ItemID); got != 1 {
		t.Fatalf("arriving at %s restocked the shop: %d", away, got)
	}
	batch4Exec(t, path, `UPDATE merchant_state SET location=?,destination=?,depart_game_minute=3000,arrive_game_minute=3100 WHERE merchant='old_hu_the_peddler'`, away, hu.Home)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := AdvanceMerchants(conn, catalog, 3100); err != nil {
			t.Fatal(err)
		}
	})
	if got := escrowScalar(t, path, `SELECT quantity FROM merchant_stock WHERE merchant='old_hu_the_peddler' AND item_id=?`, first.ItemID); got != first.Quantity {
		t.Fatalf("coming home should restock %s to %d, got %d", first.ItemID, first.Quantity, got)
	}
	// The status query tells the shop from the floor finds.
	batch4Exec(t, path, `INSERT INTO merchant_stock(merchant,item_id,quantity,cost,price,acquired_game_minute) VALUES('old_hu_the_peddler','bone_comb',1,40,64,3000)`)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, hu.Home)
	batch4Exec(t, path, `UPDATE merchant_state SET destination='' WHERE merchant='old_hu_the_peddler'`)
	batch4SetCanonicalGameMinute(t, path, 3200)
	status, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "merchant-wares-status", Operation: "merchant.status", ActorID: 42, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range batch4Result(t, status)["merchants"].([]map[string]any) {
		if row["merchant"] != "old_hu_the_peddler" {
			continue
		}
		stock := row["stock"].([]map[string]any)
		if len(stock) != len(hu.Wares)+1 {
			t.Fatalf("stock lines=%d want %d", len(stock), len(hu.Wares)+1)
		}
		if stock[0]["source"] != "wares" || stock[len(stock)-1]["source"] != "auction" || stock[len(stock)-1]["item_id"] != "bone_comb" {
			t.Fatalf("shop should list first and the floor find last: %v", stock)
		}
		return
	}
	t.Fatal("Old Hu missing from status")
}

// Merchants bid (v0.37.0): on the tick a merchant bids the next minimum on
// a lot it values, its purse paying as escrow; a player who outbids it gets
// the purse refunded to it, and a merchant left holding the high bid at the
// close wins the lot.
func TestAMerchantBidsOnALotItValuesAndIsRefundedWhenOutbid(t *testing.T) {
	path := setupMerchantDB(t)
	world := batch4WorldPath(t)
	catalog := merchantCatalog(t)
	batch4SetCanonicalGameMinute(t, path, 2000)
	// A spirit-iron sword (sect value 8) on the Golden Pavilion floor, an
	// hour to run, starting at 3.
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,active,created_at,ends_at) VALUES(9,'golden_pavilion',43,'spirit_iron_sword',1,'low_spirit_stone',3,0,NULL,1,0,?)`, nowSeconds()+3600)
	var placed int64
	withMerchantConn(t, path, func(conn *storage.Conn) {
		var err error
		placed, err = MerchantsBid(conn, catalog, 2000)
		if err != nil {
			t.Fatal(err)
		}
	})
	if placed != 1 {
		t.Fatalf("bids placed=%d want 1", placed)
	}
	bidder := fmt.Sprint(actionScalar(t, path, `SELECT merchant_bidder FROM auctions WHERE auction_id=9`))
	if bidder == "" || bidder == "<nil>" {
		t.Fatal("no merchant bid")
	}
	if got := escrowScalar(t, path, `SELECT current_bid FROM auctions WHERE auction_id=9`); got != 3 {
		t.Fatalf("current_bid=%d want the starting bid 3", got)
	}
	budget := catalog.Merchants[bidder].Budget
	if got := escrowScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant=?`, bidder); got != budget-3 {
		t.Fatalf("budget=%d want %d (escrowed)", got, budget-3)
	}
	// The same tick does not bid twice; the next tick raises no further while
	// the merchant already holds the lot (a rival could, at the next minimum).
	withMerchantConn(t, path, func(conn *storage.Conn) {
		placed, _ = MerchantsBid(conn, catalog, 2001)
	})
	if got := fmt.Sprint(actionScalar(t, path, `SELECT merchant_bidder FROM auctions WHERE auction_id=9`)); got == bidder && placed > 0 {
		t.Fatalf("the holder bid against itself: placed=%d", placed)
	}
	// A player outbids: the merchant's purse is refunded and it is cleared.
	current := escrowScalar(t, path, `SELECT current_bid FROM auctions WHERE auction_id=9`)
	holder := fmt.Sprint(actionScalar(t, path, `SELECT merchant_bidder FROM auctions WHERE auction_id=9`))
	holderBudget := escrowScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant=?`, holder)
	batch4Exec(t, path, `UPDATE characters SET location='Golden Pavilion Auction House' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',100)`)
	raw, _ := json.Marshal(map[string]any{"auction_id": 9, "amount": current + 10})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "outbid-merchant", Operation: "auction.bid", ActorID: 42, Payload: raw}); err != nil {
		t.Fatal(err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT merchant_bidder FROM auctions WHERE auction_id=9`)); got != "" {
		t.Fatalf("merchant_bidder after a player outbid=%q", got)
	}
	if got := escrowScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant=?`, holder); got != holderBudget+current {
		t.Fatalf("holder budget=%d want %d refunded", got, holderBudget+current)
	}
	// A merchant never pays above its valuation: at 8 (the sword's value)
	// the next minimum is 9, so no bid is placed.
	withMerchantConn(t, path, func(conn *storage.Conn) {
		placed, _ = MerchantsBid(conn, catalog, 2002)
	})
	if placed != 0 {
		t.Fatalf("a merchant bid above its valuation: %d", placed)
	}
}

func TestAMerchantHoldingTheHighBidWinsTheLot(t *testing.T) {
	path := setupMerchantDB(t)
	catalog := merchantCatalog(t)
	batch4Exec(t, path, `INSERT INTO merchant_state(merchant,location,destination,dwell_until_game_minute,budget,route_index) VALUES('old_hu_the_peddler','Greenriver Town','',9000,390,0)`)
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,merchant_bidder,active,created_at,ends_at) VALUES(10,'golden_pavilion',43,'bone_comb',1,'low_spirit_stone',5,10,NULL,'old_hu_the_peddler',1,0,0)`)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		res, _ := conn.Execute(`SELECT * FROM auctions WHERE auction_id=10`, nil)
		key, won, err := MerchantWinsLot(conn, catalog, firstRowMap(res), 3000)
		if err != nil {
			t.Fatal(err)
		}
		if !won || key != "old_hu_the_peddler" {
			t.Fatalf("won=%v key=%q", won, key)
		}
	})
	if got := escrowScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=43 AND currency_id='low_spirit_stone'`); got != 10 {
		t.Fatalf("seller paid %d want the hammer price 10", got)
	}
	if got := escrowScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant='old_hu_the_peddler'`); got != 390 {
		t.Fatalf("budget=%d: the purse paid at bidding time and must not pay twice", got)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM merchant_stock WHERE merchant='old_hu_the_peddler' AND item_id='bone_comb'`); got != 1 {
		t.Fatalf("pack=%d want 1", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT merchant_buyer||'|'||merchant_bidder FROM auctions WHERE auction_id=10`)); got != "old_hu_the_peddler|" {
		t.Fatalf("lot record=%q", got)
	}
}
