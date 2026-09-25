package simulation

import (
	"os"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The town shops at the stalls (v1.5.0), and the four bounds on it.
//
// The fixture carries what production carries where the step can touch it:
// the stall tables verbatim from migration 65 with their foreign keys, a
// `characters` table with the purse's mirror and the location the wallet door
// reads, and `currency_wallets`. The dice are lent by every test, because a
// townsperson buys on a roll and none of these tests is about the roll.

const npcStallsSchema = `
CREATE TABLE npc_civilization_state(
    npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL, current_location TEXT NOT NULL,
    world_name TEXT NOT NULL, profession TEXT NOT NULL, faction TEXT NOT NULL DEFAULT 'Independent',
    wealth INTEGER NOT NULL DEFAULT 20, influence INTEGER NOT NULL DEFAULT 10,
    ambition INTEGER NOT NULL DEFAULT 50, realm_index INTEGER NOT NULL DEFAULT 0,
    phase INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'alive',
    activity TEXT NOT NULL DEFAULT '', last_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT 'Greenriver Town',
    spirit_stones INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE currency_wallets(user_id INTEGER NOT NULL, currency_id TEXT NOT NULL, balance INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id,currency_id), FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE player_stalls (
    user_id INTEGER PRIMARY KEY, city TEXT NOT NULL, name TEXT NOT NULL, currency_id TEXT NOT NULL,
    opened_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE stall_listings (
    listing_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, city TEXT NOT NULL, item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL, currency_id TEXT NOT NULL,
    listed_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(user_id, item_id), FOREIGN KEY(user_id) REFERENCES player_stalls(user_id) ON DELETE CASCADE);
CREATE TABLE stall_sales (
    sale_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL,
    unit_price INTEGER NOT NULL, currency_id TEXT NOT NULL, fee INTEGER NOT NULL DEFAULT 0, buyer_user_id INTEGER,
    buyer_npc_name TEXT NOT NULL DEFAULT '', sold_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
    FOREIGN KEY(buyer_user_id) REFERENCES characters(user_id) ON DELETE SET NULL);
-- Production always has the homestead table (base DDL); the fee reads the
-- seller's merchant hall off it, so the fixture carries it too.
CREATE TABLE cave_abodes(user_id INTEGER PRIMARY KEY, location_key TEXT NOT NULL DEFAULT '', base_location TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '', property_type TEXT NOT NULL DEFAULT 'homestead', merchant_level INTEGER NOT NULL DEFAULT 0);
INSERT INTO characters(user_id,name) VALUES(42,'Lin Test');
INSERT INTO player_stalls(user_id,city,name,currency_id,created_at,updated_at) VALUES(42,'Greenriver Town','Lin''s Table','low_spirit_stone',0,0);
`

// A town with one apothecary whose shelf sells the pill at 10, so the town
// pays up to 9 for one at a stall; a comb no shop sells.
func stallsRunner() *Runner {
	return &Runner{World: worlddata.Catalog{
		Locations: map[string]worlddata.LocationDefinition{
			"Greenriver Town":  {World: "Mortal World"},
			"Greenriver Alley": {World: "Mortal World", OutsideLocation: "Greenriver Town", District: "alley"},
			"Moonfen Marsh":    {World: "Mortal World"},
		},
		Currencies: map[string]worlddata.CurrencyDefinition{"low_spirit_stone": {Name: "Low Spirit Stone", World: "Mortal World", Tier: 1}},
		Items:      map[string]worlddata.Item{"recovery_pill": {Name: "Recovery Pill", BasePrice: 18}, "bone_comb": {Name: "Bone Comb", BasePrice: 4}},
		Shops: map[string]worlddata.Shop{
			"greenriver_apothecary": {Name: "Greenriver Apothecary", City: "Greenriver Town", Currency: "low_spirit_stone",
				Sells: []worlddata.ShopLine{{ItemID: "recovery_pill", Quantity: 3, Price: 10}}, Buys: map[string]int64{"recovery_pill": 3}},
		},
		StallSystem: worlddata.StallSystem{NPCBuysPerCityPerDay: 3},
	}}
}

func stallsDB(t *testing.T) string {
	t.Helper()
	return setupSimulationDB(t, npcStallsSchema)
}

func stallsExec(t *testing.T, path, sql string, args ...any) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(sql, args); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func stallsScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		return -1
	}
	return i64(res.Rows[0][0])
}

func addShopper(t *testing.T, path, name, where, profession string, wealth int64) {
	t.Helper()
	stallsExec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,wealth,status,updated_at)
        VALUES(?,?,?,'Mortal World',?,?,'alive',0)`, name, where, where, profession, wealth)
}

func addListing(t *testing.T, path, item string, quantity, price int64) {
	t.Helper()
	stallsExec(t, path, `INSERT INTO stall_listings(user_id,city,item_id,quantity,unit_price,currency_id,created_at,updated_at)
        VALUES(42,'Greenriver Town',?,?,?,'low_spirit_stone',0,0)`, item, quantity, price)
}

// theTownAlwaysBuys lends the dice: every roll is 0, under any chance.
func theTownAlwaysBuys(t *testing.T) {
	t.Helper()
	restore := gamerng.UseRoller(func(int) int { return 0 })
	t.Cleanup(restore)
}

func runStalls(t *testing.T, path string, r *Runner, steps, gm int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	bought, err := r.npcStallPurchases(conn, steps, gm)
	if err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	return bought
}

func TestAnNPCNeverTakesTheLastUnit(t *testing.T) {
	theTownAlwaysBuys(t)
	path := stallsDB(t)
	addShopper(t, path, "Aunt Bo", "Greenriver Town", "innkeeper", 100)
	addListing(t, path, "recovery_pill", 2, 5)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 1 {
		t.Fatalf("the town bought %d of a listing of 2 with a budget of 3; it may take one and must leave the last", bought)
	}
	if left := stallsScalar(t, path, `SELECT quantity FROM stall_listings`); left != 1 {
		t.Fatalf("%d left on the stall, want the last one", left)
	}
	// And a listing already down to one is never touched, tick after tick.
	if bought := runStalls(t, path, stallsRunner(), 1, 2440); bought != 0 {
		t.Fatalf("the town took the last unit: bought %d", bought)
	}
}

func TestAnNPCNeverPaysMoreThanTheShelf(t *testing.T) {
	theTownAlwaysBuys(t)
	path := stallsDB(t)
	addShopper(t, path, "Aunt Bo", "Greenriver Town", "innkeeper", 100)
	addListing(t, path, "recovery_pill", 5, 10) // the shelf's own price
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 0 {
		t.Fatalf("the town paid the shelf price at a stall: bought %d; buy off one shelf and sell to the town is a loop", bought)
	}
	stallsExec(t, path, `UPDATE stall_listings SET unit_price=9`)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 3 {
		t.Fatalf("one coin under the shelf, budget 3: bought %d", bought)
	}
}

func TestAnItemNoShelfSellsIsNotBoughtByNPCs(t *testing.T) {
	theTownAlwaysBuys(t)
	path := stallsDB(t)
	addShopper(t, path, "Aunt Bo", "Greenriver Town", "innkeeper", 100)
	addListing(t, path, "bone_comb", 5, 1)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 0 {
		t.Fatalf("the town bought %d of an item no shop sells; with no shelf there is no mint guard", bought)
	}
}

func TestAnNPCPaysFromItsOwnWealthAndTheSellerIsPaidThroughTheOneDoor(t *testing.T) {
	theTownAlwaysBuys(t)
	path := stallsDB(t)
	addShopper(t, path, "Aunt Bo", "Greenriver Alley", "innkeeper", 3)
	addListing(t, path, "recovery_pill", 5, 4)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 0 {
		t.Fatalf("a townsperson with 3 stones bought a 4-stone pill: %d", bought)
	}
	stallsExec(t, path, `UPDATE stall_listings SET unit_price=2`)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 1 {
		t.Fatalf("with 3 stones and a 2-stone pill, bought %d (one, then broke)", bought)
	}
	if wealth := stallsScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Aunt Bo'`); wealth != 1 {
		t.Fatalf("Aunt Bo has %d stones after paying 2 of 3", wealth)
	}
	if purse := stallsScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`); purse != 2 {
		t.Fatalf("the seller's purse holds %d, want the 2 paid (the tenth of 2 rounds to nothing)", purse)
	}
	if mirror := stallsScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`); mirror != 2 {
		t.Fatalf("the sheet's mirror reads %d; the payout did not go through the wallet door", mirror)
	}
	if n := stallsScalar(t, path, `SELECT COUNT(*) FROM stall_sales WHERE user_id=42 AND buyer_npc_name='Aunt Bo' AND buyer_user_id IS NULL`); n != 1 {
		t.Fatalf("%d ledger rows name Aunt Bo", n)
	}
}

func TestNPCBuysAreBoundedPerCityPerDay(t *testing.T) {
	theTownAlwaysBuys(t)
	path := stallsDB(t)
	addShopper(t, path, "Aunt Bo", "Greenriver Town", "innkeeper", 1000)
	addListing(t, path, "recovery_pill", 50, 1)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 3 {
		t.Fatalf("one day: bought %d, want the roster's 3", bought)
	}
	if bought := runStalls(t, path, stallsRunner(), 10, 2000); bought != 9 {
		t.Fatalf("ten days overdue: bought %d, want 3 days' worth (9) and no more", bought)
	}
}

func TestNobodyInTownMeansNoSale(t *testing.T) {
	theTownAlwaysBuys(t)
	path := stallsDB(t)
	addShopper(t, path, "Aunt Bo", "Moonfen Marsh", "innkeeper", 1000)
	addListing(t, path, "recovery_pill", 5, 1)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 0 {
		t.Fatalf("somebody in the marsh bought at a Greenriver stall: %d", bought)
	}
}

func TestTheStepIsQuietBeforeTheMigration(t *testing.T) {
	theTownAlwaysBuys(t)
	path := setupSimulationDB(t, `CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY, current_location TEXT NOT NULL, profession TEXT NOT NULL, wealth INTEGER NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL DEFAULT 0);`)
	if bought := runStalls(t, path, stallsRunner(), 1, 1000); bought != 0 {
		t.Fatalf("bought %d with no stall tables", bought)
	}
}

// Rule 4 from the simulation's side: the step pays through game.StallSaleTx
// and keeps no arithmetic of the payout - no wallet write, no ledger insert.
func TestTheTownPaysThroughTheOnePayout(t *testing.T) {
	src, err := os.ReadFile("npc_stalls.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(src)
	if !strings.Contains(text, "game.StallSaleTx(") {
		t.Fatal("npc_stalls.go no longer pays through game.StallSaleTx")
	}
	for _, forbidden := range []string{"currency_wallets", "INSERT INTO stall_sales", "spirit_stones", "DELETE FROM stall_listings", "UPDATE stall_listings"} {
		if strings.Contains(text, forbidden) {
			t.Fatalf("npc_stalls.go carries its own copy of the payout: %q", forbidden)
		}
	}
}
