package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A caravan that arrives has to pay whoever sent it (v1.0.0-rc.18).
//
// The resolver's payout was one `if owner_type == "player"` with nothing on
// its other side. That was not a missing branch so much as a missing half of
// the world: nothing ever wrote a caravan that was not a player's, so the
// roads carried only what players put on them. Now that the merchants send
// their own, an arrival has to reach a purse, and an NPC's purse is not a
// `currency_wallets` row - the same problem `payAuctionSeller` solved when the
// world's own people started consigning to the auction floors.

const npcCaravanSchema = `
CREATE TABLE caravans(
    caravan_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL DEFAULT 'npc', owner_key TEXT NOT NULL,
    origin TEXT NOT NULL, destination TEXT NOT NULL, cargo_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'traveling', risk INTEGER NOT NULL DEFAULT 10,
    depart_game_minute INTEGER NOT NULL DEFAULT 0, arrive_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE caravan_operations(
    caravan_id INTEGER PRIMARY KEY, escort_strength INTEGER NOT NULL DEFAULT 0, concealment INTEGER NOT NULL DEFAULT 0,
    smuggling INTEGER NOT NULL DEFAULT 0, tax_rate INTEGER NOT NULL DEFAULT 8, toll_paid INTEGER NOT NULL DEFAULT 0,
    intercepted INTEGER NOT NULL DEFAULT 0, seized INTEGER NOT NULL DEFAULT 0, payout_final INTEGER NOT NULL DEFAULT 0,
    losses_json TEXT NOT NULL DEFAULT '{}', outcome TEXT NOT NULL DEFAULT 'traveling', resolved_game_minute INTEGER,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE caravan_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, caravan_id INTEGER NOT NULL, event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}', game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE merchant_state(
    merchant TEXT PRIMARY KEY, location TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
    depart_game_minute INTEGER NOT NULL DEFAULT 0, arrive_game_minute INTEGER NOT NULL DEFAULT 0,
    dwell_until_game_minute INTEGER NOT NULL DEFAULT 0, budget INTEGER NOT NULL DEFAULT 0,
    route_index INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE npc_civilization_state(
    npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL DEFAULT '', current_location TEXT NOT NULL DEFAULT '',
    world_name TEXT NOT NULL DEFAULT '', profession TEXT NOT NULL DEFAULT '', wealth INTEGER NOT NULL DEFAULT 20,
    status TEXT NOT NULL DEFAULT 'alive', updated_at REAL NOT NULL DEFAULT 0);
-- Production shapes (app/database/core.py). currency_wallets deliberately has
-- no updated_at: a fixture that invents one is how a broken INSERT passes its
-- own test, which is the fault v1.0.0-rc.15 shipped and then had to hunt down.
CREATE TABLE currency_wallets(
    user_id INTEGER NOT NULL, currency_id TEXT NOT NULL, balance INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id,currency_id));
CREATE TABLE characters(
    user_id INTEGER PRIMARY KEY, spirit_stones INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE world_eras(
    era_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    modifiers_json TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1,
    started_game_minute INTEGER NOT NULL DEFAULT 0, ends_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
`

// caravanRunner is one merchant with a name the world knows them by, which is
// what the payout has to resolve to reach their wealth.
func caravanRunner() *Runner {
	return &Runner{World: worlddata.Catalog{
		Merchants: map[string]worlddata.Merchant{
			"old_hu_the_peddler": {
				Name: "Old Hu the Peddler", World: "Mortal World", Home: "Greenriver Town",
				Route: []string{"Greenriver Town", "Riverguard City"}, Budget: 400,
				Currency: "low_spirit_stone",
			},
		},
	}}
}

// arrivedCaravan is a load that has reached its destination, with the risk set
// so the roll cannot take any of it: this test is about the payout, not the
// ambush.
func arrivedCaravan(t *testing.T, path, ownerType, ownerKey string, payout int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	cargo := fmt.Sprintf(`{"spirit_herb":2,"_payout":%d,"_currency":"low_spirit_stone"}`, payout)
	if _, err := conn.Execute(`INSERT INTO caravans(owner_type,owner_key,origin,destination,cargo_json,status,risk,depart_game_minute,arrive_game_minute,updated_at)
        VALUES(?,?, 'Greenriver Town','Riverguard City',?, 'traveling',0,0,10,0)`, []any{ownerType, ownerKey, cargo}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO caravan_operations(caravan_id,escort_strength,concealment,smuggling,tax_rate,updated_at)
        VALUES((SELECT MAX(caravan_id) FROM caravans),4,2,0,0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func runCaravans(t *testing.T, path string, r *Runner, gm int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	n, err := r.advanceCaravans(conn, gm)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestAnArrivedNPCCaravanPaysTheMerchantThatSentIt(t *testing.T) {
	path := setupSimulationDB(t, npcCaravanSchema)
	r := caravanRunner()
	seedMerchantPurse(t, path, "old_hu_the_peddler", 400, "Old Hu the Peddler", 20)
	arrivedCaravan(t, path, "npc", "old_hu_the_peddler", 200)

	if n := runCaravans(t, path, r, 10); n != 1 {
		t.Fatalf("resolved %d caravans, want 1", n)
	}
	if got := simText(t, path, `SELECT status FROM caravans WHERE caravan_id=1`); got != "arrived" {
		t.Fatalf("status=%q", got)
	}
	final := storage.ParseInt(simScalar(t, path, `SELECT payout_final FROM caravan_operations WHERE caravan_id=1`))
	if final <= 0 {
		t.Fatalf("payout_final=%d: the load arrived and was worth nothing", final)
	}
	// The trading purse: what the merchant bids and buys with.
	if got := storage.ParseInt(simScalar(t, path, `SELECT budget FROM merchant_state WHERE merchant='old_hu_the_peddler'`)); got != 400+final {
		t.Fatalf("budget=%d, want %d", got, 400+final)
	}
	// And the purse the world reads, which is where `payAuctionSeller` pays
	// an NPC too - at the same eighth of the takings.
	wealth := storage.ParseInt(simScalar(t, path, `SELECT wealth FROM npc_civilization_state WHERE npc_name='Old Hu the Peddler'`))
	if wealth <= 20 {
		t.Fatalf("wealth=%d: the sender is no richer for the journey", wealth)
	}
	if wealth != 20+maxSim(1, final/8) {
		t.Fatalf("wealth=%d, want %d", wealth, 20+maxSim(1, final/8))
	}
	// Nobody's wallet was written: an NPC has no character behind it, and
	// user 0 is not a character.
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM currency_wallets`)); got != 0 {
		t.Fatalf("%d wallet rows written for a caravan with no player in it", got)
	}
}

func TestAPlayersCaravanStillPaysIntoTheirWallet(t *testing.T) {
	path := setupSimulationDB(t, npcCaravanSchema)
	r := caravanRunner()
	arrivedCaravan(t, path, "player", "42", 200)
	if n := runCaravans(t, path, r, 10); n != 1 {
		t.Fatalf("resolved %d caravans, want 1", n)
	}
	final := storage.ParseInt(simScalar(t, path, `SELECT payout_final FROM caravan_operations WHERE caravan_id=1`))
	if got := storage.ParseInt(simScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)); got != final {
		t.Fatalf("wallet=%d, want the payout %d", got, final)
	}
}

func TestACaravanWhoseOwnerKeyIsNotAUserIDPaysNobody(t *testing.T) {
	// A row labelled 'player' whose key is not a number is not a player's
	// caravan however it is labelled, and paying it would have credited user
	// 0 - a character that does not exist.
	path := setupSimulationDB(t, npcCaravanSchema)
	r := caravanRunner()
	arrivedCaravan(t, path, "player", "not-a-user", 200)
	if n := runCaravans(t, path, r, 10); n != 1 {
		t.Fatalf("resolved %d caravans, want 1", n)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT COUNT(*) FROM currency_wallets`)); got != 0 {
		t.Fatalf("%d wallet rows written, want none", got)
	}
}

func TestAnUnknownNPCSenderIsNotAFatalArrival(t *testing.T) {
	// A caravan sent by something that is not in the merchant catalogue (an
	// older row, a merchant content has since retired) still resolves: the
	// arrival is the world's, not the sender's.
	path := setupSimulationDB(t, npcCaravanSchema)
	r := caravanRunner()
	arrivedCaravan(t, path, "npc", "a_merchant_content_retired", 200)
	if n := runCaravans(t, path, r, 10); n != 1 {
		t.Fatalf("resolved %d caravans, want 1", n)
	}
	if got := simText(t, path, `SELECT status FROM caravans WHERE caravan_id=1`); got != "arrived" {
		t.Fatalf("status=%q", got)
	}
}

func seedMerchantPurse(t *testing.T, path, key string, budget int64, npcName string, wealth int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO merchant_state(merchant,location,budget,updated_at) VALUES(?,'Greenriver Town',?,0)`, []any{key, budget}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state(npc_name,current_location,world_name,profession,wealth,status,updated_at)
        VALUES(?,'Greenriver Town','Mortal World','Travelling Merchant',?, 'alive',0)`, []any{npcName, wealth}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func simText(t *testing.T, path, sql string) string {
	t.Helper()
	value := simScalar(t, path, sql)
	if value == nil {
		return ""
	}
	if s, ok := value.(string); ok {
		return s
	}
	return ""
}
