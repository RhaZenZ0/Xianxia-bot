package game

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A cultivator's own stall in a city's street (v1.5.0).
//
// The fixture is setupPropertyTypesDB - characters 42 and 43 in Greenriver
// Town, the bags, the purse, a homestead row with `merchant_level` - plus the
// three stall tables copied verbatim from migration 65, foreign keys included,
// because a fixture that cannot fail the way production fails is not testing
// production (CLAUDE.md, npc_consignments).

const stallSchema = `
CREATE TABLE IF NOT EXISTS player_stalls (
    user_id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    name TEXT NOT NULL,
    currency_id TEXT NOT NULL,
    opened_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS stall_listings (
    listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    city TEXT NOT NULL,
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    currency_id TEXT NOT NULL,
    listed_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, item_id),
    FOREIGN KEY(user_id) REFERENCES player_stalls(user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS stall_sales (
    sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    currency_id TEXT NOT NULL,
    fee INTEGER NOT NULL DEFAULT 0,
    buyer_user_id INTEGER,
    buyer_npc_name TEXT NOT NULL DEFAULT '',
    sold_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
    FOREIGN KEY(buyer_user_id) REFERENCES characters(user_id) ON DELETE SET NULL
);
`

func setupStallDB(t *testing.T) string {
	t.Helper()
	path := setupPropertyTypesDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(stallSchema); err != nil {
		t.Fatal(err)
	}
	// Foundation Establishment for both, which is what a stall asks for.
	if _, err := conn.Execute(`UPDATE characters SET realm_index=2 WHERE user_id IN (42,43)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

var stallSeq int

func stallAct(t *testing.T, path, op string, actor int64, payload map[string]any) (map[string]any, error) {
	t.Helper()
	stallSeq++
	raw, _ := json.Marshal(payload)
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("stall-%d", stallSeq), Operation: op, ActorID: actor, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func stallMust(t *testing.T, path, op string, actor int64, payload map[string]any) map[string]any {
	t.Helper()
	result, err := stallAct(t, path, op, actor, payload)
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	return result
}

func stallQuery(t *testing.T, path, op string, actor int64) map[string]any {
	t.Helper()
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{Operation: op, ActorID: actor, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	result, _ := out.Result.(map[string]any)
	return result
}

func stallCount(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	return i64(scalar(t, path, sql, args...))
}

func TestAStallOpensOnlyInACityAtFoundation(t *testing.T) {
	path := setupStallDB(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=1 WHERE user_id=42`)
	if _, err := stallAct(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"}); err == nil || !strings.Contains(err.Error(), "asks for") {
		t.Fatalf("Qi Condensation opened a stall: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET realm_index=2, location='abode:42' WHERE user_id=42`)
	if _, err := stallAct(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"}); err == nil || !strings.Contains(err.Error(), "city's street") {
		t.Fatalf("a stall opened inside a private room: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Moonfen Marsh' WHERE user_id=42`)
	if _, err := stallAct(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"}); err == nil || !strings.Contains(err.Error(), "city's street") {
		t.Fatalf("a stall opened in the marsh: %v", err)
	}
	// A gate is its city (v1.0.9): the stall stands in the town, in the
	// town's own money.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town East Gate' WHERE user_id=42`)
	opened := stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	if opened["city"] != "Greenriver Town" || opened["currency_id"] != "low_spirit_stone" {
		t.Fatalf("opened %v", opened)
	}
	if _, err := stallAct(t, path, "stall.open", 42, map[string]any{"name": "Another"}); err == nil || !strings.Contains(err.Error(), "already keep a stall") {
		t.Fatalf("a second stall was allowed: %v", err)
	}
}

func TestListingEscrowsTheGoodsAndWithdrawReturnsThem(t *testing.T) {
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',5)`)
	stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	listed := stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 3, "unit_price": 8})
	if got := stallCount(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`); got != 2 {
		t.Fatalf("the bag holds %d after listing 3 of 5; the goods were not taken into escrow", got)
	}
	// The cheapest Mortal shelf sells the pill at 11, so the town pays up to 10.
	if i64(listed["npc_ceiling"]) != 10 || listed["npc_may_buy"] != true {
		t.Fatalf("listing result %v", listed)
	}
	if _, err := stallAct(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8}); err == nil || !strings.Contains(err.Error(), "already on your stall") {
		t.Fatalf("the same item was listed twice: %v", err)
	}
	if _, err := stallAct(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 0}); err == nil {
		t.Fatal("a free listing was accepted")
	}
	status := stallQuery(t, path, "stall.status", 42)
	listings, _ := status["listings"].([]map[string]any)
	if len(listings) != 1 || i64(listings[0]["quantity"]) != 3 {
		t.Fatalf("status %v", status)
	}
	stallMust(t, path, "stall.withdraw", 42, map[string]any{"listing_id": listed["listing_id"]})
	if got := stallCount(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`); got != 5 {
		t.Fatalf("the bag holds %d after withdrawing; the goods did not come back", got)
	}
	if n := stallCount(t, path, `SELECT COUNT(*) FROM stall_listings`); n != 0 {
		t.Fatalf("%d listings left after withdraw", n)
	}
	// Close returns everything still on the stall.
	stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 4, "unit_price": 8})
	closed := stallMust(t, path, "stall.close", 42, nil)
	if got := stallCount(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`); got != 5 {
		t.Fatalf("the bag holds %d after closing: %v", got, closed)
	}
	if n := stallCount(t, path, `SELECT COUNT(*) FROM player_stalls`); n != 0 {
		t.Fatal("the stall row survived close")
	}
}

func TestTendingAStallTakesStandingInItsCity(t *testing.T) {
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',5)`)
	stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	batch4Exec(t, path, `UPDATE characters SET location='Moonfen Marsh' WHERE user_id=42`)
	if _, err := stallAct(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8}); err == nil || !strings.Contains(err.Error(), "stands in Greenriver Town") {
		t.Fatalf("goods were laid on a stall from the marsh: %v", err)
	}
	if _, err := stallAct(t, path, "stall.close", 42, nil); err == nil || !strings.Contains(err.Error(), "stands in Greenriver Town") {
		t.Fatalf("a stall was closed from the marsh: %v", err)
	}
}

// The merchant hall (`cave_abodes.merchant_level`) was buildable, raisable
// and printed to the narrator since the homestead was written, and read by no
// rule. This is the rule.
func TestTheMerchantHallIsReadBySomething(t *testing.T) {
	catalog := mustLoadCatalog(t, batch4WorldPath(t))
	if slots, fee := stallSlotsAndFee(catalog, 0); slots != 2 || fee != 10 {
		t.Fatalf("no hall: slots=%d fee=%d", slots, fee)
	}
	if slots, fee := stallSlotsAndFee(catalog, 3); slots != 5 || fee != 7 {
		t.Fatalf("hall 3: slots=%d fee=%d", slots, fee)
	}
	if _, fee := stallSlotsAndFee(catalog, 9); fee != 2 {
		t.Fatalf("hall 9 fee=%d, want the floor 2", fee)
	}
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',5),(42,'spirit_herb',5),(42,'qi_pill',5)`)
	stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8})
	stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "spirit_herb", "quantity": 1, "unit_price": 2})
	if _, err := stallAct(t, path, "stall.list", 42, map[string]any{"item_id": "qi_pill", "quantity": 1, "unit_price": 5}); err == nil || !strings.Contains(err.Error(), "merchant hall") {
		t.Fatalf("a third listing with no hall: %v", err)
	}
	batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,property_type,cultivation_level,merchant_level) VALUES(42,'abode:42','Greenriver Town','Lin Homestead','homestead',1,1)`)
	stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "qi_pill", "quantity": 1, "unit_price": 5})
	status := stallQuery(t, path, "stall.status", 42)
	if i64(status["slots_total"]) != 3 || i64(status["fee_percent"]) != 9 || i64(status["merchant_level"]) != 1 {
		t.Fatalf("status did not read the hall: %v", status)
	}
}

func TestABuyerPaysAndTheSellerIsPaidInTheStallsWorldCurrency(t *testing.T) {
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',4)`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(43,'low_spirit_stone',100)`)
	stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	listed := stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 4, "unit_price": 20})
	listing := listed["listing_id"]
	if _, err := stallAct(t, path, "stall.buy", 42, map[string]any{"listing_id": listing, "quantity": 1}); err == nil || !strings.Contains(err.Error(), "own stall") {
		t.Fatalf("the owner bought from their own stall: %v", err)
	}
	if _, err := stallAct(t, path, "stall.buy", 43, map[string]any{"listing_id": listing, "quantity": 9}); err == nil || !strings.Contains(err.Error(), "only 4 left") {
		t.Fatalf("more than the listing holds was sold: %v", err)
	}
	bought := stallMust(t, path, "stall.buy", 43, map[string]any{"listing_id": listing, "quantity": 1})
	if i64(bought["total"]) != 20 || i64(bought["balance"]) != 80 || i64(bought["fee"]) != 2 || i64(bought["seller_paid"]) != 18 {
		t.Fatalf("buy %v", bought)
	}
	if got := stallCount(t, path, `SELECT quantity FROM inventory WHERE user_id=43 AND item_id='recovery_pill'`); got != 1 {
		t.Fatalf("the buyer carries %d", got)
	}
	if got := stallCount(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`); got != 18 {
		t.Fatalf("the seller's purse holds %d, want 18 (20 less the city's tenth)", got)
	}
	if got := stallCount(t, path, `SELECT quantity FROM stall_listings WHERE listing_id=?`, listing); got != 3 {
		t.Fatalf("the listing holds %d after one sold", got)
	}
	if got := stallCount(t, path, `SELECT COUNT(*) FROM stall_sales WHERE user_id=42 AND buyer_user_id=43 AND fee=2`); got != 1 {
		t.Fatal("the seller's ledger did not record the sale")
	}
	// The seller travels up a world; the stall still pays in the money of
	// the world it stands in (rc.44), and the far-away seller is credited it.
	batch4Exec(t, path, `UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`)
	stallMust(t, path, "stall.buy", 43, map[string]any{"listing_id": listing, "quantity": 1})
	if got := stallCount(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`); got != 36 {
		t.Fatalf("the seller's Mortal purse holds %d after a second sale from another world, want 36", got)
	}
	if got := stallCount(t, path, `SELECT COUNT(*) FROM currency_wallets WHERE user_id=42 AND currency_id<>'low_spirit_stone'`); got != 0 {
		t.Fatal("the sale was paid in the seller's current world's money rather than the stall's")
	}
	// A stall is in reach from anywhere (v1.6.0), and distance is priced, not
	// refused. The marsh is in Greenriver's world and on no road, so it counts
	// as half the cross-world distance: 10 roads, +50%. On a 20-stone pill
	// that is 10 more: a third to the seller, a third to the city's cut, and
	// the rest is the courier's, which goes into nobody's purse.
	batch4Exec(t, path, `UPDATE characters SET location='Moonfen Marsh' WHERE user_id=43`)
	far := stallMust(t, path, "stall.buy", 43, map[string]any{"listing_id": listing, "quantity": 1})
	if storage.ParseInt(far["hops"]) != 10 || storage.ParseInt(far["surcharge"]) != 10 || storage.ParseInt(far["total"]) != 30 {
		t.Fatalf("a pill bought from the marsh cost %v (%v roads, %v surcharge); want 30 = 20 + 10 over 10 roads", far["total"], far["hops"], far["surcharge"])
	}
	if storage.ParseInt(far["fee"]) != 2+10/3 || storage.ParseInt(far["seller_paid"]) != 18+10/3 {
		t.Fatalf("the far sale split the courier's fee wrongly: %v", far)
	}
	if got := stallCount(t, path, `SELECT balance FROM currency_wallets WHERE user_id=43 AND currency_id='low_spirit_stone'`); got != 100-20-20-30 {
		t.Fatalf("the far buyer holds %d, want %d", got, 100-20-20-30)
	}
	if got := stallCount(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`); got != 36+18+10/3 {
		t.Fatalf("the seller holds %d after the far sale, want %d: their price less the cut, and a third of the courier's fee", got, 36+18+10/3)
	}
}

func TestTheBoardShowsEveryStallPricedFromWhereYouStand(t *testing.T) {
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',4)`)
	stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 4, "unit_price": 6})
	board := stallQuery(t, path, "stall.board", 43)
	stalls, _ := board["stalls"].([]map[string]any)
	if board["city"] != "Greenriver Town" || len(stalls) != 1 || stalls[0]["owner_name"] != "Lin Test" || storage.ParseInt(stalls[0]["hops"]) != 0 {
		t.Fatalf("board %v", board)
	}
	if price := storage.ParseInt(stalls[0]["listings"].([]map[string]any)[0]["price_here"]); price != 6 {
		t.Fatalf("in the stall's own city a 6-stone pill is quoted %d", price)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Moonfen Marsh' WHERE user_id=43`)
	board = stallQuery(t, path, "stall.board", 43)
	stalls, _ = board["stalls"].([]map[string]any)
	if len(stalls) != 1 || storage.ParseInt(stalls[0]["hops"]) != 10 {
		t.Fatalf("the marsh's board must still list Greenriver's stall, 10 roads off: %v", board)
	}
	if price := storage.ParseInt(stalls[0]["listings"].([]map[string]any)[0]["price_here"]); price != 9 {
		t.Fatalf("from the marsh the pill is quoted %d, want 9", price)
	}
}

func TestDistanceIsCountedInRoads(t *testing.T) {
	catalog := shopCatalog(t)
	cases := []struct {
		from, to string
		want     int64
	}{
		{"Greenriver Town", "Greenriver Town", 0},
		{"Riverguard City", "Greenriver Town", 1},
		{"Moonfen Marsh", "Greenriver Town", 10},
		{"Spirit Jade Capital", "Greenriver Town", 20},
		{"Dust Road Waystation", "Immortal River City", 1},
	}
	for _, c := range cases {
		if got := stallDistanceHops(catalog, c.from, c.to); got != c.want {
			t.Errorf("%s to %s is %d roads, want %d", c.from, c.to, got, c.want)
		}
	}
}

// Rule 1: nothing a player asks at a stall enters the shops' band. The two
// band functions take a catalogue and no connection, so they cannot read a
// listing, and the listing table is named by the two stall files and nothing
// else in production.
func TestAStallIsNotAShelf(t *testing.T) {
	for name, fn := range map[string]any{"cheapestShelfPrice": cheapestShelfPrice, "highestKeeperBuy": highestKeeperBuy} {
		typ := reflect.TypeOf(fn)
		for i := 0; i < typ.NumIn(); i++ {
			if typ.In(i) == reflect.TypeOf((*storage.Conn)(nil)) {
				t.Fatalf("%s takes a *storage.Conn, so it could read a stall's asking price into the band", name)
			}
		}
	}
	allowed := map[string]bool{"stall_actions.go": true, "npc_stalls.go": true}
	for _, dir := range []string{".", "../simulation"} {
		entries, err := os.ReadDir(dir)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			name := entry.Name()
			if !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
				continue
			}
			src, err := os.ReadFile(filepath.Join(dir, name))
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(src), "stall_listings") && !allowed[name] {
				t.Fatalf("%s names stall_listings; a stall's prices are read by the two stall files and nothing else", name)
			}
		}
	}
}

// Rule 3: the travelling merchants buy on the auction floor and nowhere else.
func TestAMerchantNeverBidsOnAStall(t *testing.T) {
	src, err := os.ReadFile("merchant_actions.go")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.ToLower(string(src)), "stall") {
		t.Fatal("merchant_actions.go names a stall; an NPC purse buying at a player's asking price is a stone printer")
	}
}

// An erased or reset seller takes the stall and its goods with them, exactly
// as an auction seller takes their lots; a buyer's purchases are the seller's
// ledger, so only the link to the buyer goes.
func TestAnErasedSellerTakesTheStallWithThemAndABuyerIsOnlyUnlinked(t *testing.T) {
	path := setupStallDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	targets, err := erasureTargets(conn)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]erasureDisposition{
		"player_stalls.user_id":     erasureDelete,
		"stall_listings.user_id":    erasureDelete,
		"stall_sales.user_id":       erasureDelete,
		"stall_sales.buyer_user_id": erasureAnonymiseRow,
	}
	seen := map[string]erasureDisposition{}
	for _, target := range targets {
		key := erasureKey(target.Table, target.Column)
		if _, ok := want[key]; ok {
			seen[key] = target.Disposition
		}
	}
	if !reflect.DeepEqual(seen, want) {
		t.Fatalf("erasure classifies the stall tables as %v, want %v", seen, want)
	}
	if _, released := characterResetReleased["stall_sales.buyer_user_id"]; !released {
		t.Fatal("a reset would refuse over a purchase at somebody else's stall")
	}
	// The cascade under foreign_keys=ON is the same shape production runs.
	if _, err := conn.Execute(`INSERT INTO player_stalls(user_id,city,name,currency_id,created_at,updated_at) VALUES(42,'Greenriver Town','Lin','low_spirit_stone',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO stall_listings(user_id,city,item_id,quantity,unit_price,currency_id,created_at,updated_at) VALUES(42,'Greenriver Town','recovery_pill',2,5,'low_spirit_stone',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO stall_sales(user_id,item_id,quantity,unit_price,currency_id,buyer_user_id,created_at) VALUES(42,'recovery_pill',1,5,'low_spirit_stone',43,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`DELETE FROM characters WHERE user_id=43`, nil); err != nil {
		t.Fatal(err)
	}
	if n := i64(firstRowMap(mustExec(t, conn, `SELECT COUNT(*) AS n FROM stall_sales WHERE user_id=42 AND buyer_user_id IS NULL`))["n"]); n != 1 {
		t.Fatalf("the seller's ledger row after the buyer went: %d rows unlinked, want 1", n)
	}
	if _, err := conn.Execute(`DELETE FROM characters WHERE user_id=42`, nil); err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"player_stalls", "stall_listings", "stall_sales"} {
		if n := i64(firstRowMap(mustExec(t, conn, `SELECT COUNT(*) AS n FROM `+table))["n"]); n != 0 {
			t.Fatalf("%s kept %d row(s) after the seller went", table, n)
		}
	}
}

func mustExec(t *testing.T, conn *storage.Conn, sql string) storage.Result {
	t.Helper()
	res, err := conn.Execute(sql, nil)
	if err != nil {
		t.Fatal(err)
	}
	return res
}
