package game

import (
	"testing"

	"xianxia/core/internal/storage"
)

// What a keeper pays a craftsman (v1.0.17): two of the shop's coin per rank in
// the trade that makes the thing, and never enough to make buying and
// reselling pay.

// saintRank is the top of the profession ladder: Novice 0 to Saint 6.
const saintRank = int64(6)

// TestARankRaisesWhatTheKeeperPays drives a real sale through the production
// dispatch. Drill: have shopSellAction use `shop.Buys` again and this fails
// with "a Journeyman alchemist was paid what anybody is".
func TestARankRaisesWhatTheKeeperPays(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := shopCatalog(t)
	shop := catalog.Shops["greenriver_apothecary"]
	base, wanted := shop.Buys["qi_pill"]
	if !wanted {
		t.Fatal("greenriver_apothecary no longer buys the Qi Nourishing Pill; pick another fixture")
	}
	batch4SetCanonicalGameMinute(t, path, 1000)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, shop.Location)
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp) VALUES(42,'Alchemy',2,0)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'qi_pill',1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=1`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=1`)

	want := tradeRankSellPrice(catalog, "qi_pill", shop.Currency, base, 2)
	if want != base+2*tradeRankSellStep {
		t.Fatalf("the fixture's ceiling bites at Journeyman (%d from %d); pick a cheaper item", want, base)
	}
	browse := shopQuery(t, path, world, "shop.browse")
	board := map[string]int64{}
	for _, row := range browse["buys"].([]map[string]any) {
		board[row["item_id"].(string)] = storage.ParseInt(row["price"])
	}
	if board["qi_pill"] != want {
		t.Fatalf("the keeper's board shows %d for a Journeyman's pill and the sale pays %d; they must agree", board["qi_pill"], want)
	}

	out := batch4Result(t, batch4Apply(t, path, world, "shop.sell", 31, map[string]any{"item_id": "qi_pill", "quantity": 1}))
	if got := storage.ParseInt(out["unit_price"]); got != want {
		t.Fatalf("a Journeyman alchemist was paid what anybody is: %d, want %d (base %d + 2 a rank)", got, want, base)
	}
	if out["trade"] != "Alchemy" || storage.ParseInt(out["trade_rank"]) != 2 || storage.ParseInt(out["base_price"]) != base {
		t.Fatalf("the sale must say which rank paid more: %v", out)
	}

	// A raw material belongs to no trade: a herb fetches the third, whoever sells it.
	out = batch4Result(t, batch4Apply(t, path, world, "shop.sell", 32, map[string]any{"item_id": "spirit_herb", "quantity": 1}))
	if got := storage.ParseInt(out["unit_price"]); got != shop.Buys["spirit_herb"] {
		t.Fatalf("a herb no recipe makes was paid %d, want %d", got, shop.Buys["spirit_herb"])
	}
	if out["trade"] != "" {
		t.Fatalf("a herb has no trade: %v", out["trade"])
	}
}

func TestARankIsTwoACoinAStep(t *testing.T) {
	catalog := shopCatalog(t)
	shelf, ok := cheapestShelfPrice(catalog, "qi_pill", "low_spirit_stone")
	if !ok {
		t.Fatal("no shop shelves the Qi Nourishing Pill for low spirit stones")
	}
	for rank := int64(0); rank <= saintRank; rank++ {
		got := tradeRankSellPrice(catalog, "qi_pill", "low_spirit_stone", 4, rank)
		want := minI64(4+2*rank, shelf-1)
		if got != want {
			t.Fatalf("rank %d fetches %d, want %d (4 + 2 a rank, below the %d shelf)", rank, got, want, shelf)
		}
	}
}

// TestNoRankTurnsAShopIntoAMint is the ceiling, over the whole shipped
// catalogue at the top rank: nothing a Saint is paid for reaches the cheapest
// shelf price in the same coin, so buying off one shelf and selling to another
// counter never pays. Drill: take the ceiling out of tradeRankSellPrice and
// this names the first line that would.
//
// What it deliberately does not refuse is a craft that turns a profit. Making
// crafting pay is the point of the rank: four talismans turn a stone or two
// from Journeyman up out of shop-bought paper and ink, which costs a craft
// action, the roll and the shelf's own stock, and refills on the shop's clock.
// The first version of this test forbade it and failed on the Hearth-Return
// Talisman at Saint - a gate encoding a claim, not a rule.
func TestNoRankTurnsAShopIntoAMint(t *testing.T) {
	catalog := shopCatalog(t)
	checked := 0
	for key, shop := range catalog.Shops {
		for itemID, base := range shop.Buys {
			if itemTrade(catalog, itemID) == "" {
				continue
			}
			paid := tradeRankSellPrice(catalog, itemID, shop.Currency, max64(1, base), saintRank)
			if shelf, ok := cheapestShelfPrice(catalog, itemID, shop.Currency); ok && paid >= shelf {
				t.Fatalf("%s pays a Saint %d for %s, and it sells for %d on a shelf: buying and reselling prints money", key, paid, itemID, shelf)
			}
			checked++
		}
	}
	if checked < 30 {
		t.Fatalf("only %d crafted lines were checked; the walk has stopped seeing the catalogue", checked)
	}
}
