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

// tradeTopRank is the highest rank a trade reaches (advanceProfessionTx stops
// raising a level here), and so the most any rank can add to a keeper's
// price. It is stated once because two prices are computed against it: what
// a Saint is paid, and what a market must never sell for less than.
const tradeTopRank = int64(6)

// highestNPCPays is the most any of the world's own people will hand a
// cultivator for one of an item in one coin, whoever the cultivator is:
// the plain third the best keeper pays; that keeper's price to a craftsman
// at the top rank of the trade that makes the item (tradeRankSellPrice, which
// climbs with the base, so the best keeper's counter is the best rank price);
// and what the town pays at a stall (NPCStallCeiling). The market counter is
// floored on it (v1.12.3): floored on the plain third alone, a market sold a
// Hearth-Return Talisman for 6 that a Saint scribe sold back to a keeper for
// 17, and an Azure Flying Sword for 91 that the town bought off a stall for
// 258. A price a player can be paid is the floor under every price a player
// can buy at, or the gap between them is a mint.
func highestNPCPays(catalog worlddata.Catalog, itemID, currency string) (int64, bool) {
	best, found := highestKeeperBuy(catalog, itemID, currency)
	if found && itemTrade(catalog, itemID) != "" {
		best = max64(best, tradeRankSellPrice(catalog, itemID, currency, best, tradeTopRank))
	}
	if town, ok := NPCStallCeiling(catalog, itemID, currency); ok && (!found || town > best) {
		best, found = town, true
	}
	return best, found
}

// itemTrade is the profession whose recipe makes an item, or "" for anything
// no recipe makes. Recipe ids are walked sorted so the answer cannot depend
// on a map range; the shipped content has no item two trades both make.
func itemTrade(catalog worlddata.Catalog, itemID string) string {
	itemID = itemBaseID(itemID)
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
