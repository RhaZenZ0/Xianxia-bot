package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The world's own people put things under the hammer (v1.0.0-rc.15).
//
// Forty-eight auction houses, a steward standing in every one of them, and the
// only lot that ever appeared on any floor was one a player walked in and
// listed. Merchants were wired to the auctions in the buy direction only, so
// the world could consume treasure and never produce any.

const npcFindsSchema = `
CREATE TABLE npc_civilization_state(
    npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL, current_location TEXT NOT NULL,
    world_name TEXT NOT NULL, profession TEXT NOT NULL, faction TEXT NOT NULL DEFAULT 'Independent',
    wealth INTEGER NOT NULL DEFAULT 20, influence INTEGER NOT NULL DEFAULT 10,
    ambition INTEGER NOT NULL DEFAULT 50, realm_index INTEGER NOT NULL DEFAULT 0,
    phase INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'alive',
    activity TEXT NOT NULL DEFAULT '', missing_since_game_minute INTEGER NOT NULL DEFAULT 0, last_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE auctions(
    auction_id INTEGER PRIMARY KEY AUTOINCREMENT, house_id TEXT NOT NULL, seller_user_id INTEGER NOT NULL DEFAULT 0,
    seller_npc_name TEXT NOT NULL DEFAULT '', item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
    currency_id TEXT NOT NULL, starting_bid INTEGER NOT NULL, current_bid INTEGER NOT NULL DEFAULT 0,
    current_bidder_user_id INTEGER, anonymous INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    appraised INTEGER NOT NULL DEFAULT 1, grade_band TEXT NOT NULL DEFAULT '',
    merchant_buyer TEXT NOT NULL DEFAULT '', merchant_bidder TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, ends_at REAL NOT NULL);
-- Production shapes, copied from app/database/core.py:279-300. An earlier
-- version of this fixture invented location/price columns on the stock table
-- and left out the NOT NULL currency_id/unit_price, which made a broken
-- INSERT pass its own test.
CREATE TABLE black_market_posts(
    world_name TEXT PRIMARY KEY, location TEXT NOT NULL, heat INTEGER NOT NULL DEFAULT 0,
    opens_game_minute INTEGER NOT NULL DEFAULT 0, closes_game_minute INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE black_market_stock(
    world_name TEXT NOT NULL, item_id TEXT NOT NULL, currency_id TEXT NOT NULL,
    unit_price INTEGER NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
    legal_status TEXT NOT NULL DEFAULT 'forbidden', updated_at REAL NOT NULL,
    PRIMARY KEY(world_name,item_id));
CREATE TABLE world_history_events(
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, significance INTEGER NOT NULL, visibility TEXT NOT NULL,
    location TEXT NOT NULL, world_name TEXT NOT NULL, faction TEXT NOT NULL, actor_type TEXT NOT NULL,
    actor_key TEXT NOT NULL, actor_name TEXT NOT NULL, target_type TEXT NOT NULL, target_key TEXT NOT NULL,
    target_name TEXT NOT NULL, related_user_id INTEGER, related_npc_name TEXT NOT NULL, tags TEXT NOT NULL,
    game_minute INTEGER NOT NULL, metadata_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
`

// A town with a floor in it, one legal treasure and one that is not.
func findsRunner() *Runner {
	return &Runner{World: worlddata.Catalog{
		Locations: map[string]worlddata.LocationDefinition{
			"Greenriver Town":  {World: "Mortal World"},
			"Golden Pavilion":  {World: "Mortal World"},
			"Greenriver Alley": {World: "Mortal World", OutsideLocation: "Greenriver Town"},
		},
		AuctionHouses: map[string]worlddata.AuctionHouse{
			"golden_pavilion": {Name: "Golden Pavilion", Location: "Golden Pavilion",
				EntranceLocation: "Greenriver Town", MaxActiveLots: 6, MaxLotMinutes: 360,
				DefaultCurrency: "low_spirit_stone"},
		},
		Items: map[string]worlddata.Item{
			"sword_tablet": {Name: "Sword Tablet", AuctionInterest: "legendary", SectValue: 480, BasePrice: 3900},
			"ghost_sutra":  {Name: "Ghost Sutra", AuctionInterest: "legendary", SectValue: 450, BasePrice: 3650, LegalStatus: "forbidden"},
		},
	}}
}

func findsDB(t *testing.T) string {
	t.Helper()
	return setupSimulationDB(t, npcFindsSchema)
}

func addFinder(t *testing.T, path, name, where, profession string, realmIndex int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state
        (npc_name,home_location,current_location,world_name,profession,realm_index,status,updated_at)
        VALUES(?,?,?, 'Mortal World',?,?, 'alive',0)`,
		[]any{name, where, where, profession, realmIndex}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func runFinds(t *testing.T, path string, r *Runner, steps, gm int64) string {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	summary, err := r.npcConsignments(conn, steps, gm)
	if err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	return summary
}

func TestGraveRobbersPutThingsUnderTheHammer(t *testing.T) {
	path := findsDB(t)
	r := findsRunner()
	for i := 0; i < 30; i++ {
		addFinder(t, path, fmt.Sprintf("Grave-Robber %02d", i), "Greenriver Town", "grave-robber", 0)
	}
	// Loaded dice, because the dice are not what this is about. A robber
	// finds something 22% of the time and half of what this world holds is
	// contraband no legal floor will take, so thirty of them came up empty
	// about one run in fifty - and the assertion that caught it could only
	// ever be "not zero", which is a weak thing to know. Every robber finds,
	// and finds the legal treasure, so what the cap does is exact.
	defer gamerng.UseRoller(func(n int) int {
		if n == 100 {
			return 0 // the find roll: everybody turns something up
		}
		return n - 1 // the item pick: the last of the sorted pool is the legal one
	})()
	runFinds(t, path, r, 1, 1440)
	lots := i64(simScalar(t, path, `SELECT COUNT(*) FROM auctions WHERE active=1`))
	if lots != findCap {
		t.Fatalf("thirty finders and a cap of %d produced %d lot(s)", findCap, lots)
	}
	// The lot is the world's, not a player's, and it is on the right floor.
	if seller := i64(simScalar(t, path, `SELECT seller_user_id FROM auctions LIMIT 1`)); seller != 0 {
		t.Fatalf("a consignment must not claim a character: seller_user_id=%d", seller)
	}
	if npc := fmt.Sprint(simScalar(t, path, `SELECT seller_npc_name FROM auctions LIMIT 1`)); npc == "" {
		t.Fatal("the lot names nobody")
	}
	if house := fmt.Sprint(simScalar(t, path, `SELECT house_id FROM auctions LIMIT 1`)); house != "golden_pavilion" {
		t.Fatalf("house_id=%q", house)
	}
	// And the town talks about it.
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='treasure_found'`)); n == 0 {
		t.Fatal("a treasure surfaced and no rumour with it")
	}
}

// A finder who cannot read their own find consigns it blind, and the discount
// is what makes the gamble worth taking.
func TestWhatTheFinderCannotReadGoesUpBlind(t *testing.T) {
	if knowsWhatTheyFound("grave-robber", 0, "legendary") {
		t.Fatal("a realm-0 scavenger should not read a legendary relic")
	}
	if !knowsWhatTheyFound("merchant", 8, "legendary") {
		t.Fatal("a merchant of eight realms prices things for a living")
	}
	if knowsWhatTheyFound("gate guard", 2, "special") {
		t.Fatal("a guard two realms up is not an appraiser")
	}
	item := worlddata.Item{AuctionInterest: "legendary", BasePrice: 3900}
	open, blind := auctionReserve(item, true), auctionReserve(item, false)
	if !(blind < open) {
		t.Fatalf("a lot nobody can vouch for must open lower: %d vs %d", blind, open)
	}
	if gradeBandFor("legendary") == gradeBandFor("special") {
		t.Fatal("the two grades read the same to a bidder")
	}
}

func TestAScavengersLegendaryGoesUpUnappraised(t *testing.T) {
	path := findsDB(t)
	r := findsRunner()
	// One item in the pool is contraband, so pool the legal one only.
	delete(r.World.Items, "ghost_sutra")
	for i := 0; i < 30; i++ {
		addFinder(t, path, fmt.Sprintf("Scavenger %02d", i), "Greenriver Town", "scavenger", 0)
	}
	runFinds(t, path, r, 1, 1440)
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM auctions WHERE appraised=0 AND grade_band<>''`)); n == 0 {
		t.Fatal("a realm-0 scavenger's legendary find went up fully identified")
	}
}

// Contraband never reaches a legal floor; it reaches the night market.
func TestContrabandGoesToTheNightMarketInstead(t *testing.T) {
	path := findsDB(t)
	r := findsRunner()
	delete(r.World.Items, "sword_tablet")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO black_market_posts(world_name,location,heat,opens_game_minute,closes_game_minute,active,created_at,updated_at) VALUES('Mortal World','Greenriver Alley',40,0,9999999,1,0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	for i := 0; i < 30; i++ {
		addFinder(t, path, fmt.Sprintf("Digger %02d", i), "Greenriver Town", "tomb digger", 0)
	}
	runFinds(t, path, r, 1, 1440)
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM auctions`)); n != 0 {
		t.Fatalf("a forbidden scripture was listed on a legal floor: %d lots", n)
	}
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM black_market_stock WHERE item_id='ghost_sutra'`)); n == 0 {
		t.Fatal("contraband reached neither the floor nor the night market")
	}
	// The row has to be one the buy path can actually read: economy_actions.go
	// takes `currency_id` and `unit_price` off it.
	if c := fmt.Sprint(simScalar(t, path, `SELECT currency_id FROM black_market_stock WHERE item_id='ghost_sutra'`)); c != "low_spirit_stone" {
		t.Fatalf("currency_id=%q", c)
	}
	if u := i64(simScalar(t, path, `SELECT unit_price FROM black_market_stock WHERE item_id='ghost_sutra'`)); u <= 0 {
		t.Fatalf("unit_price=%d", u)
	}
}

// A floor that is already full takes nothing more, exactly as it refuses a
// player's seventh lot.
func TestAFullFloorTakesNoConsignment(t *testing.T) {
	path := findsDB(t)
	r := findsRunner()
	delete(r.World.Items, "ghost_sutra")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 6; i++ {
		if _, err := conn.Execute(`INSERT INTO auctions(house_id,seller_user_id,item_id,currency_id,starting_bid,active,created_at,ends_at) VALUES('golden_pavilion',42,'sword_tablet','low_spirit_stone',10,1,0,9e9)`, nil); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	for i := 0; i < 30; i++ {
		addFinder(t, path, fmt.Sprintf("Robber %02d", i), "Greenriver Town", "grave-robber", 0)
	}
	runFinds(t, path, r, 1, 1440)
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM auctions`)); n != 6 {
		t.Fatalf("the floor took more than it holds: %d lots", n)
	}
}

// A gate guard is not out looking for treasure.
func TestARootedTradeFindsAlmostNothing(t *testing.T) {
	if npcFindChance("gate guard") >= npcFindChance("tomb robber") {
		t.Fatal("a guard should not turn up as much as a tomb robber")
	}
	if npcFindChance("beast hunter") <= npcFindChance("gate guard") {
		t.Fatal("a hunter walks the wilds and a guard does not")
	}
}
