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
// item, in one currency - and any travelling merchant's own wares, which are a
// shelf that moves (v1.2.3): Madam Wen sells a Spirit Focus Talisman at 15
// against a cheapest shelf of 17, so a ceiling read off the shops alone let a
// rank sell it back at 16.
//
// "Anywhere" is every world, read across the ladder (v1.34.0). Goods cross a
// world boundary untouched while money converts at a hundred to one a rung, so
// a ceiling kept in one coin was a ceiling nobody upstairs was held to: a
// Mortal herb at five stones sold to a Spiritual keeper for forty crystals,
// which is four thousand stones, and the same goods bought off the lowest
// shelf paid for the fare and the world. A shelf in another world's coin is
// therefore carried into this one through ladderRate - the arithmetic the
// purse crosses on - and rounded down, so a price is never read as higher
// than the shelf it came from. It can come out as 0: the cheapest shelf in the
// world asks less than one coin of this one, and no whole-coin price is under
// it. A coin the content does not tier (a mid or high stone, or a currency the
// catalogue does not carry) is compared only with itself, as it always was.
func cheapestShelfPrice(catalog worlddata.Catalog, itemID, currency string) (int64, bool) {
	best, found := int64(0), false
	rates := map[string]shelfRate{}
	consider := func(coin string, price int64) {
		if price <= 0 {
			return
		}
		converted, ok := shelfPriceAcross(catalog, rates, coin, currency, price)
		if ok && (!found || converted < best) {
			best, found = converted, true
		}
	}
	for _, merchant := range catalog.Merchants {
		for _, ware := range merchant.Wares {
			if ware.ItemID == itemID {
				consider(merchant.Currency, ware.Price)
			}
		}
	}
	for _, shop := range catalog.Shops {
		for _, line := range shop.Sells {
			if line.ItemID == itemID {
				consider(shop.Currency, line.Price)
			}
		}
	}
	return best, found
}

// coinName is a currency as a sentence names it; an id the catalogue does not
// carry is named by its id.
func coinName(catalog worlddata.Catalog, currency string) string {
	if def, ok := catalog.Currencies[currency]; ok && def.Name != "" {
		return def.Name
	}
	return currency
}

// shelfRate is one coin's way into another: how many rungs apart and the rate
// across them. A coin that cannot be placed on the ladder has ok false.
type shelfRate struct {
	steps, rate int64
	ok          bool
}

// shelfPriceAcross carries one shelf price from the coin it is asked in to the
// coin a ceiling is wanted in. `rates` memoises the rung walk per source coin,
// because worldTierIndex reads every location in the catalogue and a shelf
// scan meets the same dozen coins hundreds of times.
func shelfPriceAcross(catalog worlddata.Catalog, rates map[string]shelfRate, from, to string, price int64) (int64, bool) {
	if from == to {
		return price, true
	}
	r, seen := rates[from]
	if !seen {
		fromDef, fromOK := catalog.Currencies[from]
		toDef, toOK := catalog.Currencies[to]
		if fromOK && toOK && fromDef.Tier == 1 && toDef.Tier == 1 && fromDef.World != "" && toDef.World != "" {
			r.steps, r.rate = ladderRate(catalog, fromDef.World, toDef.World)
			r.ok = true
		}
		rates[from] = r
	}
	if !r.ok {
		return 0, false
	}
	converted, _ := ladderConvert(price, r.steps, r.rate)
	return converted, true
}

// keeperBuyBase is what a keeper pays anybody for one of an item: the
// authored line, held under the cheapest shelf in the world (cheapestShelfPrice)
// by one coin. A line that no whole coin of the keeper's currency sits under -
// the cheapest shelf asks less than one - is not a price at all, and the
// keeper does not buy (false), as a board that listed it would offer what the
// counter refuses. An item the keeper never listed is also false; the caller
// tells the two apart by asking the shop's own Buys.
func keeperBuyBase(catalog worlddata.Catalog, shop worlddata.Shop, itemID string) (int64, bool) {
	authored, listed := shop.Buys[itemID]
	if !listed {
		return 0, false
	}
	base := max64(1, authored)
	if shelf, ok := cheapestShelfPrice(catalog, itemID, shop.Currency); ok {
		if shelf < 1 {
			return 0, false
		}
		if base >= shelf {
			base = max64(1, shelf-1)
		}
	}
	return base, true
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
	base, buys := keeperBuyBase(catalog, shop, itemID)
	if !buys {
		return tradeSellQuote{}, nil
	}
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
