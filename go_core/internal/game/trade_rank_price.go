package game

import (
	"sort"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What a keeper pays a craftsman (v1.0.17). A keeper pays about a third of
// the shelf price for anything handed over the counter, and until now the
// same third whoever handed it: a Saint alchemist's pill fetched exactly what
// a beggar's did. Three Mortal recipes - the Qi Nourishing Pill, the
// Spirit-Iron Sword and the Spirit-Iron Lamellar - therefore sold back for
// less than their own ingredients, so making them destroyed value.
//
// On the owner's call, a rank in the trade that makes a thing raises what the
// keeper pays for it by two of the shop's own coin per rank above Novice, and
// raises nothing else: the shelf price is untouched, so buying stays the sink
// it is. Raw materials belong to no trade and keep the third.

const tradeRankSellStep = int64(2)

// itemTrade is the profession whose recipe makes an item, or "" for anything
// no recipe makes. Recipe ids are walked sorted so the answer cannot depend
// on a map range; the shipped content has no item two trades both make.
func itemTrade(catalog worlddata.Catalog, itemID string) string {
	ids := make([]string, 0, len(catalog.Recipes))
	for id := range catalog.Recipes {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		recipe := catalog.Recipes[id]
		if recipe.Output[itemID] > 0 && recipe.Profession != "" {
			return recipe.Profession
		}
	}
	return ""
}

// cheapestShelfPrice is the lowest price any shop in the catalogue asks for an
// item in one currency - and any travelling merchant's own wares, which are a
// shelf that moves (v1.2.3): Madam Wen sells a Spirit Focus Talisman at 15
// against a cheapest shelf of 17, so a ceiling read off the shops alone let a
// rank sell it back at 16.
func cheapestShelfPrice(catalog worlddata.Catalog, itemID, currency string) (int64, bool) {
	best, found := int64(0), false
	for _, merchant := range catalog.Merchants {
		if merchant.Currency != currency {
			continue
		}
		for _, ware := range merchant.Wares {
			if ware.ItemID != itemID || ware.Price <= 0 {
				continue
			}
			if !found || ware.Price < best {
				best, found = ware.Price, true
			}
		}
	}
	for _, shop := range catalog.Shops {
		if shop.Currency != currency {
			continue
		}
		for _, line := range shop.Sells {
			if line.ItemID != itemID || line.Price <= 0 {
				continue
			}
			if !found || line.Price < best {
				best, found = line.Price, true
			}
		}
	}
	return best, found
}

// tradeRankSellPrice is what a keeper pays somebody of this rank for one of
// the item, given what they pay anybody.
//
// It never reaches the cheapest shelf price anywhere in the same coin. Without
// that ceiling a high enough rank would make buying off one shelf and selling
// to the next counter a way to print money, which is the one thing a sell
// price must never do. It never falls below what anybody is paid, either.
func tradeRankSellPrice(catalog worlddata.Catalog, itemID, currency string, base, rank int64) int64 {
	if rank <= 0 {
		return base
	}
	price := base + tradeRankSellStep*rank
	if shelf, ok := cheapestShelfPrice(catalog, itemID, currency); ok && price >= shelf {
		price = shelf - 1
	}
	return maxI64(base, price)
}

// tradeSellQuote is one keeper's price for one item, to one cultivator: the
// price paid, what anybody would be paid, and the trade and rank that made the
// difference ("" and 0 when nothing did).
type tradeSellQuote struct {
	Unit, Base int64
	Trade      string
	Rank       int64
}

func tradeSellQuoteTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, shop worlddata.Shop, itemID string) (tradeSellQuote, error) {
	base := max64(1, shop.Buys[itemID])
	quote := tradeSellQuote{Unit: base, Base: base}
	trade := itemTrade(catalog, itemID)
	if trade == "" {
		return quote, nil
	}
	rank, err := professionLevelTx(conn, userID, trade)
	if err != nil {
		return quote, err
	}
	quote.Unit = tradeRankSellPrice(catalog, itemID, shop.Currency, base, rank)
	if quote.Unit > base {
		quote.Trade, quote.Rank = trade, rank
	}
	return quote, nil
}
