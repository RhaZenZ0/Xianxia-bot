package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// City shops (v0.35.0): found by exploring the city, entered by travelling
// to them, left back onto the street; a shelf to buy from and a board of
// what the keeper pays for, refilled on the shop's clock.

func setupShopDB(t *testing.T) string {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE shop_state(shop TEXT PRIMARY KEY, last_restock_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE shop_stock(shop TEXT NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, price INTEGER NOT NULL DEFAULT 0, made_here INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(shop,item_id));
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func shopCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(catalog.Shops) == 0 {
		t.Fatal("world content ships no shops")
	}
	return catalog
}

var shopQuerySeq int

func shopQuery(t *testing.T, path, world, op string) map[string]any {
	t.Helper()
	shopQuerySeq++
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("shop-q-%s-%d", op, shopQuerySeq), Operation: op, ActorID: 42, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	return batch4Result(t, out)
}

func TestExploringACityFindsItsShopsOneAtATime(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := shopCatalog(t)
	keys := cityShopKeys(catalog, "Greenriver Town")
	if len(keys) < 2 {
		t.Fatalf("Greenriver Town shops=%v", keys)
	}
	previous := shopDiscoveryIntn
	shopDiscoveryIntn = func(n int) (int, error) { return 0, nil }
	defer func() { shopDiscoveryIntn = previous }()
	roads := locationDiscoveryIntn
	locationDiscoveryIntn = func(n int) (int, error) { return n - 1, nil } // no road found this turn
	defer func() { locationDiscoveryIntn = roads }()

	here := shopQuery(t, path, world, "shop.here")
	if storage.ParseInt(here["found"]) != 0 || storage.ParseInt(here["total"]) != int64(len(keys)) {
		t.Fatalf("before exploring: %v", here)
	}
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", 1, map[string]any{"game_minute": 600, "cooldown_seconds": 0, "unexpected_events_enabled": false, "unexpected_event_chance_percent": 0}))
	found, _ := result["discovered_shop"].(map[string]any)
	if found == nil || fmt.Sprint(found["shop"]) != keys[0] {
		t.Fatalf("discovered_shop=%v want %s", result["discovered_shop"], keys[0])
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT discovery_kind FROM character_location_discoveries WHERE user_id=42 AND location=?`, catalog.Shops[keys[0]].Location)); got != "shop" {
		t.Fatalf("discovery kind=%q", got)
	}
	// The next walk finds the next one, never the same twice.
	result = batch4Result(t, batch4Apply(t, path, world, "exploration.explore", 2, map[string]any{"game_minute": 700, "cooldown_seconds": 0, "unexpected_events_enabled": false, "unexpected_event_chance_percent": 0}))
	found, _ = result["discovered_shop"].(map[string]any)
	if found == nil || fmt.Sprint(found["shop"]) != keys[1] {
		t.Fatalf("second discovered_shop=%v want %s", result["discovered_shop"], keys[1])
	}
	here = shopQuery(t, path, world, "shop.here")
	if storage.ParseInt(here["found"]) != 2 {
		t.Fatalf("after two walks: %v", here)
	}
}

func TestAShopIsEnteredFromItsStreetAndLeftOntoIt(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := shopCatalog(t)
	keys := cityShopKeys(catalog, "Greenriver Town")
	shop := catalog.Shops[keys[0]]
	batch4SetCanonicalGameMinute(t, path, 1000)
	// Not found yet: the picker would not offer it, and the engine refuses it.
	raw, _ := json.Marshal(map[string]any{"destination": shop.Location, "mode": "known"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "shop-enter-0", Operation: "exploration.travel", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "not been discovered") {
		t.Fatalf("an unfound shop should be refused, got %v", err)
	}
	batch4Exec(t, path, `INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,?,'shop',900,0)`, shop.Location)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 11, map[string]any{"destination": shop.Location, "mode": "known"}))
	if result["traveling"] != false || fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)) != shop.Location {
		t.Fatalf("entering a shop should be instant: %v", result)
	}
	// From inside, the door opens onto the street and nowhere else.
	raw, _ = json.Marshal(map[string]any{"destination": "Riverguard City", "mode": "known"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "shop-leave-0", Operation: "exploration.travel", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "door opens onto Greenriver Town") {
		t.Fatalf("leaving a shop for another city should be refused, got %v", err)
	}
	batch4Apply(t, path, world, "exploration.travel", 12, map[string]any{"destination": "Greenriver Town", "mode": "known"})
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != "Greenriver Town" {
		t.Fatalf("after leaving: %q", got)
	}
	// A shop of another city cannot be entered from here even once found.
	other := ""
	for _, key := range cityShopKeys(catalog, "Riverguard City") {
		other = catalog.Shops[key].Location
		break
	}
	batch4Exec(t, path, `INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,?,'shop',900,0)`, other)
	raw, _ = json.Marshal(map[string]any{"destination": other, "mode": "known"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "shop-enter-far", Operation: "exploration.travel", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "is in Riverguard City") {
		t.Fatalf("a far shop should send you to its city first, got %v", err)
	}
}

func TestBuyingSellingAndRestockingAtAShop(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := shopCatalog(t)
	key := "greenriver_apothecary"
	shop, ok := catalog.Shops[key]
	if !ok {
		t.Fatal("no greenriver_apothecary in content")
	}
	batch4SetCanonicalGameMinute(t, path, 1000)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, shop.Location)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,?,200)`, shop.Currency)
	browse := shopQuery(t, path, world, "shop.browse")
	stock := browse["stock"].([]map[string]any)
	if len(stock) != len(shop.Sells) || fmt.Sprint(browse["keeper"]) != shop.Keeper {
		t.Fatalf("browse=%v", browse)
	}
	if stock[0]["made_here"] != true {
		t.Fatalf("the keeper's own craft should list first: %v", stock)
	}
	// Buy two recovery pills.
	var pill worlddata.ShopLine
	for _, line := range shop.Sells {
		if line.ItemID == "recovery_pill" {
			pill = line
		}
	}
	out := batch4Result(t, batch4Apply(t, path, world, "shop.buy", 21, map[string]any{"item_id": "recovery_pill", "quantity": 2}))
	if storage.ParseInt(out["total"]) != 2*pill.Price || storage.ParseInt(out["balance"]) != 200-2*pill.Price {
		t.Fatalf("buy=%v", out)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`); got != 2 {
		t.Fatalf("inventory=%d", got)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM shop_stock WHERE shop=? AND item_id='recovery_pill'`, key); got != pill.Quantity-2 {
		t.Fatalf("shelf=%d want %d", got, pill.Quantity-2)
	}
	// Sell three spirit herbs the shop wants; they go back on its shelf.
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',3) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=3`)
	before := escrowScalar(t, path, `SELECT quantity FROM shop_stock WHERE shop=? AND item_id='spirit_herb'`, key)
	out = batch4Result(t, batch4Apply(t, path, world, "shop.sell", 22, map[string]any{"item_id": "spirit_herb", "quantity": 3}))
	if storage.ParseInt(out["total"]) != 3*shop.Buys["spirit_herb"] || out["on_the_shelf"] != true {
		t.Fatalf("sell=%v", out)
	}
	if got := escrowScalar(t, path, `SELECT quantity FROM shop_stock WHERE shop=? AND item_id='spirit_herb'`, key); got != before+3 {
		t.Fatalf("shelf after selling=%d want %d", got, before+3)
	}
	if got := escrowScalar(t, path, `SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`); got != 0 {
		t.Fatalf("herbs left=%d", got)
	}
	// What the keeper does not want is refused whole.
	raw, _ := json.Marshal(map[string]any{"item_id": "array_disk_blank", "quantity": 1})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "shop-sell-no", Operation: "shop.sell", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "does not buy") {
		t.Fatalf("unwanted goods should be refused, got %v", err)
	}
	// The shelf refills on the shop's clock, not before: an empty line stays
	// empty a minute short of the refill, and a buy just after it finds the
	// content quantity back (the refill is written by the buy itself).
	batch4Exec(t, path, `UPDATE shop_stock SET quantity=0 WHERE shop=? AND item_id='recovery_pill'`, key)
	batch4SetCanonicalGameMinute(t, path, 1000+shop.RestockMinutes-1)
	raw, _ = json.Marshal(map[string]any{"item_id": "recovery_pill", "quantity": 1})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "shop-buy-early", Operation: "shop.buy", ActorID: 42, Payload: raw}); err == nil || !strings.Contains(err.Error(), "on the shelf") {
		t.Fatalf("an empty line should stay empty before the refill, got %v", err)
	}
	batch4SetCanonicalGameMinute(t, path, 1000+shop.RestockMinutes)
	browse = shopQuery(t, path, world, "shop.browse")
	for _, line := range browse["stock"].([]map[string]any) {
		if line["item_id"] == "recovery_pill" && storage.ParseInt(line["quantity"]) != pill.Quantity {
			t.Fatalf("browse after the refill shows %v, want %d", line["quantity"], pill.Quantity)
		}
	}
	batch4Apply(t, path, world, "shop.buy", 23, map[string]any{"item_id": "recovery_pill", "quantity": 1})
	if got := escrowScalar(t, path, `SELECT quantity FROM shop_stock WHERE shop=? AND item_id='recovery_pill'`, key); got != pill.Quantity-1 {
		t.Fatalf("after refill and one buy=%d want %d", got, pill.Quantity-1)
	}
}
