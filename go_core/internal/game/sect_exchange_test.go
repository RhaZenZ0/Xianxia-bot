package game

import (
	"encoding/json"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The sect exchange (v1.8.0) is driven against the shipped catalogue, because
// the stock, the prices and the promotion ladder are all content.
const sectExchangeSchema = `
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, name TEXT, location TEXT, updated_at REAL);
CREATE TABLE sects(sect_name TEXT PRIMARY KEY, prestige INTEGER NOT NULL DEFAULT 0, treasury_stones INTEGER NOT NULL DEFAULT 0,
	policy_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL);
CREATE TABLE sect_membership(user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL, rank_name TEXT NOT NULL DEFAULT 'Disciple',
	rank_level INTEGER NOT NULL DEFAULT 0, joined_at REAL NOT NULL, contribution_points INTEGER NOT NULL DEFAULT 0,
	influence INTEGER NOT NULL DEFAULT 0, contribution_earned INTEGER NOT NULL DEFAULT 0,
	FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE sect_treasury(sect_name TEXT NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY(sect_name, item_id), FOREIGN KEY(sect_name) REFERENCES sects(sect_name) ON DELETE CASCADE);
CREATE TABLE sect_politics_state(sect_name TEXT PRIMARY KEY, resources INTEGER NOT NULL DEFAULT 50, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE sect_lineage(disciple_user_id INTEGER PRIMARY KEY, master_user_id INTEGER NOT NULL, accepted_at REAL NOT NULL,
	attention INTEGER NOT NULL DEFAULT 0,
	FOREIGN KEY (disciple_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
	FOREIGN KEY (master_user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id));
CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT NOT NULL,
	payload_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE world_events(event_key TEXT PRIMARY KEY, location TEXT NOT NULL);
INSERT INTO characters(user_id,name,location,updated_at) VALUES(1,'Disciple','Greenriver Town',0),(2,'Elder','Greenriver Town',0),(3,'Outsider','Greenriver Town',0);
INSERT INTO sects(sect_name,updated_at) VALUES('Azure Cloud Sect',0);
INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at,contribution_points) VALUES(1,'Azure Cloud Sect','Outer Disciple',10,0,0);
INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at,contribution_points) VALUES(2,'Azure Cloud Sect','Elder',50,0,0);
`

func sectExchangeWorld(t *testing.T) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(filepath.Join(t.TempDir(), "exchange.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if err := conn.ExecScript(sectExchangeSchema); err != nil {
		t.Fatal(err)
	}
	return conn
}

func sectOp(t *testing.T, conn *storage.Conn, op string, userID int64, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(payload)
	mut, err := sectEconomyActionGo(conn, shippedCatalog(t), userID, raw, op)
	res, _ := mut.Result.(map[string]any)
	return res, err
}

func memberRow(t *testing.T, conn *storage.Conn, userID int64) map[string]any {
	t.Helper()
	row, err := sectMembershipRow(conn, userID)
	if err != nil || row == nil {
		t.Fatalf("no membership for %d: %v", userID, err)
	}
	return row
}

func setPoints(t *testing.T, conn *storage.Conn, userID, points int64) {
	t.Helper()
	if _, err := conn.Execute(`UPDATE sect_membership SET contribution_points=? WHERE user_id=?`, []any{points, userID}); err != nil {
		t.Fatal(err)
	}
}

func TestIssuedStockCostsPointsAndNeverTouchesTheTreasury(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	setPoints(t, conn, 1, 100)
	out, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "swift_wind_talisman", "quantity": 1, "source": "issued"})
	if err != nil {
		t.Fatal(err)
	}
	want := catalog.SectExchange().PointsPerSectValue * itemSectValue(catalog, "swift_wind_talisman")
	if i64(out["unit_cost"]) != want || i64(out["remaining_points"]) != 100-want {
		t.Fatalf("an issued talisman cost %v leaving %v, want %d leaving %d", out["unit_cost"], out["remaining_points"], want, 100-want)
	}
	if carriedBy(t, conn, 1, "swift_wind_talisman") != 1 {
		t.Fatal("the issued talisman did not reach the bag")
	}
	r, _ := conn.Execute(`SELECT COUNT(*) FROM sect_treasury`, nil)
	if i64(r.Rows[0][0]) != 0 {
		t.Fatal("issuing stock wrote to the treasury")
	}
}

func TestIssuedStockWaitsForTheRankThatUnlocksIt(t *testing.T) {
	conn := sectExchangeWorld(t)
	setPoints(t, conn, 1, 10000)
	_, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "marrow_tempering_pill", "quantity": 1, "source": "issued"})
	if err == nil || !strings.Contains(err.Error(), "Inner Disciple") {
		t.Fatalf("an Outer Disciple was issued an Inner Disciple's pill: %v", err)
	}
	if carriedBy(t, conn, 1, "marrow_tempering_pill") != 0 {
		t.Fatal("the refused pill reached the bag")
	}
}

func TestASectIssuesOnlyItsOwnExclusive(t *testing.T) {
	conn := sectExchangeWorld(t)
	setPoints(t, conn, 2, 10000)
	if _, err := sectOp(t, conn, "sect.redeem", 2, map[string]any{"item_id": "cloud_sword_intent_talisman", "quantity": 1, "source": "issued"}); err != nil {
		t.Fatalf("the Azure Cloud Sect refused its own Elder its own talisman: %v", err)
	}
	if _, err := sectOp(t, conn, "sect.redeem", 2, map[string]any{"item_id": "furnace_heart_pill", "quantity": 1, "source": "issued"}); err == nil {
		t.Fatal("the Azure Cloud Sect issued the Crimson Furnace Sect's pill")
	}
}

func TestIssuedStockIsRefusedWithoutThePoints(t *testing.T) {
	conn := sectExchangeWorld(t)
	setPoints(t, conn, 1, 1)
	if _, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "swift_wind_talisman", "quantity": 1, "source": "issued"}); err == nil {
		t.Fatal("a member with one point was issued a talisman")
	}
	if i64(memberRow(t, conn, 1)["contribution_points"]) != 1 {
		t.Fatal("a refused redeem spent points")
	}
}

func TestTheTreasuryRedeemIsUnchanged(t *testing.T) {
	conn := sectExchangeWorld(t)
	setPoints(t, conn, 1, 100)
	if _, err := conn.Execute(`INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES('Azure Cloud Sect','spirit_herb',3)`, nil); err != nil {
		t.Fatal(err)
	}
	out, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "spirit_herb", "quantity": 1})
	if err != nil {
		t.Fatal(err)
	}
	if out["source"] != nil {
		t.Fatalf("a treasury redeem answered source %v", out["source"])
	}
	r, _ := conn.Execute(`SELECT quantity FROM sect_treasury WHERE item_id='spirit_herb'`, nil)
	if i64(r.Rows[0][0]) != 2 {
		t.Fatal("a treasury redeem did not draw from the treasury")
	}
}

func TestMembersRiseOnWhatTheyHaveEarnedAndSpendingNeverCostsARank(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	ladder := catalog.SectExchange().Promotion
	if len(ladder) < 2 {
		t.Fatalf("the content's promotion ladder has %d rungs, want Inner and Core", len(ladder))
	}
	inner, core := ladder[0], ladder[1]
	promoted, err := creditSectContributionTx(conn, catalog, 1, inner.Earned-1, 0)
	if err != nil || promoted != "" {
		t.Fatalf("one point short of %d promoted to %q (%v)", inner.Earned, promoted, err)
	}
	if promoted, _ = creditSectContributionTx(conn, catalog, 1, 1, 0); promoted != "Inner Disciple" {
		t.Fatalf("reaching %d earned promoted to %q, want Inner Disciple", inner.Earned, promoted)
	}
	// Spending every point never costs the rank.
	setPoints(t, conn, 1, 0)
	if promoted, _ = creditSectContributionTx(conn, catalog, 1, 1, 0); promoted != "" || i64(memberRow(t, conn, 1)["rank_level"]) != inner.RankLevel {
		t.Fatalf("spending points lost the rank: now %v", memberRow(t, conn, 1)["rank_level"])
	}
	if promoted, _ = creditSectContributionTx(conn, catalog, 1, core.Earned, 0); promoted != "Core Disciple" {
		t.Fatalf("reaching %d earned promoted to %q, want Core Disciple", core.Earned, promoted)
	}
	// An Elder is above the ladder, and a GM's rank is never touched.
	if promoted, _ = creditSectContributionTx(conn, catalog, 2, core.Earned*2, 0); promoted != "" || i64(memberRow(t, conn, 2)["rank_level"]) != 50 {
		t.Fatalf("an Elder was moved to %v", memberRow(t, conn, 2)["rank_level"])
	}
}

func TestADonationIsWorthNoMoreThanTheShelf(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	if err := addInventoryTx(conn, 1, map[string]int64{"stygian_tomb_token": 1}); err != nil {
		t.Fatal(err)
	}
	out, err := sectOp(t, conn, "sect.contribute", 1, map[string]any{"item_id": "stygian_tomb_token", "quantity": 1})
	if err != nil {
		t.Fatal(err)
	}
	shelf, ok := cheapestShelfPrice(catalog, "stygian_tomb_token", "low_spirit_stone")
	if !ok {
		t.Fatal("the shipped content no longer shelves the Stygian key; pick another capped item")
	}
	if i64(out["points"]) != shelf {
		t.Fatalf("a key that sells for %d earned %v points - a point is cheaper than a stone again", shelf, out["points"])
	}
}

func TestACertifiedCraftsmanDonatesForMore(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	give := func() int64 {
		if err := addInventoryTx(conn, 1, map[string]int64{"qi_pill": 1}); err != nil {
			t.Fatal(err)
		}
		out, err := sectOp(t, conn, "sect.contribute", 1, map[string]any{"item_id": "qi_pill", "quantity": 1})
		if err != nil {
			t.Fatal(err)
		}
		return i64(out["points"])
	}
	plain := give()
	if err := recordProfessionExamTx(conn, 1, professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 1, Passed: true}); err != nil {
		t.Fatal(err)
	}
	certified := give()
	if certified <= plain {
		t.Fatalf("a certified alchemist's pill earned %d, no more than an uncertified %d", certified, plain)
	}
	if limit, capped := sectDonationCap(catalog, "qi_pill"); capped && certified > limit {
		t.Fatalf("the crafted bonus took a pill past its shelf price: %d > %d", certified, limit)
	}
}

func TestOwnSectCommissionsPayPoints(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	out, err := sectCommissionPointsTx(conn, catalog, 1, "Azure Cloud Sect", 40)
	if err != nil || out == nil || i64(out["points"]) <= 0 {
		t.Fatalf("a member's own commission paid %v (%v)", out, err)
	}
	if out, _ = sectCommissionPointsTx(conn, catalog, 1, "Crimson Furnace Sect", 40); out != nil {
		t.Fatalf("another sect's commission paid points: %v", out)
	}
	if out, _ = sectCommissionPointsTx(conn, catalog, 3, "Azure Cloud Sect", 40); out != nil {
		t.Fatalf("an outsider was paid points: %v", out)
	}
}

func TestWorldEventsPayPointsInTheSectsOwnWorldUpToTheCap(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	home := EraWorldOf(catalog, sectGate(catalog, "Azure Cloud Sect"))
	var near, far string
	names := make([]string, 0, len(catalog.Locations))
	for name := range catalog.Locations {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if near == "" && EraWorldOf(catalog, name) == home {
			near = name
		}
		if far == "" && EraWorldOf(catalog, name) != home {
			far = name
		}
	}
	if _, err := conn.Execute(`INSERT INTO world_events(event_key,location) VALUES('near',?),('far',?)`, []any{near, far}); err != nil {
		t.Fatal(err)
	}
	limit := catalog.SectExchange().Earning.EventPointsCap
	paid := int64(0)
	for total := int64(10); total <= limit+30; total += 10 {
		out, err := sectEventPointsTx(conn, catalog, 1, "near", 10, total)
		if err != nil {
			t.Fatal(err)
		}
		if out != nil {
			paid += i64(out["points"])
		}
	}
	if paid != limit {
		t.Fatalf("an event paid %d points in all, want the cap of %d", paid, limit)
	}
	if out, _ := sectEventPointsTx(conn, catalog, 1, "far", 10, 10); out != nil {
		t.Fatalf("an event in another world paid points: %v", out)
	}
}

// Redeeming issued stock and selling it to a keeper must never turn a stone
// into more stones. A point costs at least a stone (the donation cap), so an
// item is safe while its points price is above the most any keeper pays for
// it, with a Saint's rank lift on top.
func TestNoIssuedItemTurnsPointsIntoStones(t *testing.T) {
	catalog := shippedCatalog(t)
	ex := catalog.SectExchange()
	if len(ex.Stock) == 0 {
		t.Fatal("the content issues no stock; the gate is broken, not the tree")
	}
	lots := append([]worlddata.SectExchangeLot{}, ex.Stock...)
	for _, own := range ex.SectStock {
		lots = append(lots, own...)
	}
	for _, lot := range lots {
		points := sectIssuedPrice(catalog, lot.ItemID)
		if points <= itemSectValue(catalog, lot.ItemID) {
			t.Errorf("%s is issued for %d points and donated back for %d", lot.ItemID, points, itemSectValue(catalog, lot.ItemID))
		}
		keeper, ok := highestKeeperBuy(catalog, lot.ItemID, "low_spirit_stone")
		if !ok {
			continue
		}
		saint := tradeRankSellPrice(catalog, lot.ItemID, "low_spirit_stone", keeper, 9)
		if points <= saint {
			t.Errorf("%s costs %d points and a keeper pays a Saint %d stones for it", lot.ItemID, points, saint)
		}
	}
	// And no donation can buy a point for less than a stone.
	for id := range catalog.Items {
		limit, capped := sectDonationCap(catalog, id)
		if !capped {
			continue
		}
		if shelf, _ := cheapestShelfPrice(catalog, id, "low_spirit_stone"); limit > shelf {
			t.Errorf("%s earns up to %d points and costs %d on a shelf", id, limit, shelf)
		}
	}
}

func carriedBy(t *testing.T, conn *storage.Conn, userID int64, itemID string) int64 {
	t.Helper()
	r, err := conn.Execute(`SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, itemID})
	if err != nil {
		t.Fatal(err)
	}
	return i64(r.Rows[0][0])
}
