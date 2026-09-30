package game

import (
	"sort"
	"testing"

	"xianxia/core/internal/worlddata"
)

// The market counter is a mint when it sells for less than any NPC pays
// (v1.12.3). v1.2.3 floored what a market sells for on the plain third a
// keeper pays, and two counters had since learned to pay more: a certified
// craftsman's rank price (v1.0.17) and the town buying at a stall (v1.5.0).
// Measured before it was believed: a Hearth-Return Talisman bought at a
// market for 6 sold to a keeper for 17 at the top rank, and an Azure Flying
// Sword bought for 91 was bought off a stall by the town for 258.
//
// TestNothingBoughtAtAMarketSellsToAnNPCForMore walks every market-tradeable
// item at every world's base currency at a low index, and holds the price a
// market sells at to what any keeper pays a Saint and what the town pays at
// a stall. Drill: floor marketUnitPrice on highestKeeperBuy again and it
// prints the item and the two prices.
func TestNothingBoughtAtAMarketSellsToAnNPCForMore(t *testing.T) {
	catalog := shopCatalog(t)
	if len(catalog.Recipes) == 0 || len(catalog.Currencies) == 0 {
		t.Fatal("the catalogue carries no recipes or no currencies; the reader is broken, not the tree")
	}
	worlds := map[string]bool{}
	for _, def := range catalog.Currencies {
		worlds[def.World] = true
	}
	currencies := []string{}
	for world := range worlds {
		currencies = append(currencies, worldBaseCurrency(catalog, world))
	}
	sort.Strings(currencies)
	ids := make([]string, 0, len(catalog.Items))
	for id := range catalog.Items {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	shopKeys := make([]string, 0, len(catalog.Shops))
	for key := range catalog.Shops {
		shopKeys = append(shopKeys, key)
	}
	sort.Strings(shopKeys)
	const lowIndex = 0.5
	checked, floored := 0, 0
	for _, itemID := range ids {
		item := catalog.Items[itemID]
		if !worlddata.MarketTradeable(item.MarketExcluded, item.SpatialKey != nil, item.AuctionInterest) {
			continue
		}
		base := max64(5, item.SectValue)
		for _, currency := range currencies {
			bought := marketUnitPrice(catalog, itemID, currency, base, lowIndex, true)
			for _, key := range shopKeys {
				shop := catalog.Shops[key]
				pays, wanted := shop.Buys[itemID]
				if shop.Currency != currency || !wanted || pays <= 0 {
					continue
				}
				if itemTrade(catalog, itemID) != "" {
					pays = tradeRankSellPrice(catalog, itemID, currency, pays, tradeTopRank)
				}
				if pays > bought {
					t.Fatalf("buy %s at a market for %d %s and %s pays a rank-%d craftsman %d for it: the market is a mint",
						itemID, bought, currency, key, tradeTopRank, pays)
				}
				checked++
			}
			if town, ok := NPCStallCeiling(catalog, itemID, currency); ok {
				if town > bought {
					t.Fatalf("buy %s at a market for %d %s, list it on a stall, and the town pays %d for it: the market is a mint",
						itemID, bought, currency, town)
				}
				checked++
			}
			if floor, ok := highestNPCPays(catalog, itemID, currency); ok && bought == floor {
				floored++
			}
		}
	}
	if checked < 100 {
		t.Fatalf("only %d NPC prices were checked; the walk has stopped seeing the catalogue", checked)
	}
	if floored == 0 {
		t.Fatal("no market price sat on the NPC floor at index 0.5; the floor is not being applied")
	}
}

// highestNPCPays is the one statement, and it is the greatest of the three
// counters, never one of them alone. Held on the two reported items so the
// finding stays legible.
func TestHighestNPCPaysIsTheGreatestOfTheThreeCounters(t *testing.T) {
	catalog := shopCatalog(t)
	for _, itemID := range []string{"hearth_return_talisman", "azure_flying_sword"} {
		keeper, ok := highestKeeperBuy(catalog, itemID, "low_spirit_stone")
		if !ok {
			t.Fatalf("no keeper buys %s for stones; pick another fixture item", itemID)
		}
		want := keeper
		if itemTrade(catalog, itemID) != "" {
			want = max64(want, tradeRankSellPrice(catalog, itemID, "low_spirit_stone", keeper, tradeTopRank))
		}
		if town, ok := NPCStallCeiling(catalog, itemID, "low_spirit_stone"); ok {
			want = max64(want, town)
		}
		got, ok := highestNPCPays(catalog, itemID, "low_spirit_stone")
		if !ok || got != want {
			t.Fatalf("%s: highestNPCPays answered %d (%v), want %d (keeper %d)", itemID, got, ok, want, keeper)
		}
		if got <= keeper {
			t.Fatalf("%s: the plain third (%d) is already the most any NPC pays; this item no longer shows the fault", itemID, keeper)
		}
	}
}
