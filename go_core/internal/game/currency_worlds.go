package game

import (
	"sort"

	"xianxia/core/internal/worlddata"
)

// What an ordinary price is denominated in (v1.0.0-rc.43).
//
// Every price in `content/world.json` - all 120 shops, all 8 teleport arrays,
// all 8 merchants - is a tier-1 currency. Exactly one price in the game was
// not, and it was wrong twice over: caravan dispatch charged
// `low_spirit_stone`, a *Mortal World* currency, to a cultivator standing in
// any of the four worlds, and escalated it to `mid_spirit_stone` at realm 4 and
// `high_spirit_stone` at realm 7.
//
// Nothing in the game has ever credited a tier above 1 - those two ids appeared
// at exactly the two lines that spent them - and `walletDeltaTx` refuses a
// debit beyond the balance, so `/economy -> Caravans -> Dispatch` was dead from
// realm 4 upward against a wallet that could never hold what it asked for.
//
// The tiers above 1 are not removed from the content: each carries a
// `base_ratio` saying what it is worth in tier-1 units, and
// `TestTheStoneLadderIsWholeInEveryWorld` holds that ladder to its shape. What
// is deliberately not built is an exchange between tiers, which is a mechanic
// rather than a parse, and which nothing needs while every price is tier 1.
const fallbackBaseCurrency = "low_spirit_stone"

// WorldBaseCurrency is worldBaseCurrency for the simulation package, which
// kept its own copy of the mapping (`worldCurrency`) - one of four in Go and a
// fifth in Python, all four-way switches over the same content the file already
// declares (v1.0.0-rc.44).
func WorldBaseCurrency(catalog worlddata.Catalog, worldName string) string {
	return worldBaseCurrency(catalog, worldName)
}

// worldBaseCurrency is the tier-1 currency of one world. The ids are sorted
// rather than taken off a map range, because a Go map range is randomised and a
// world that somehow carried two tier-1 currencies would otherwise charge a
// different one on different days.
func worldBaseCurrency(catalog worlddata.Catalog, worldName string) string {
	ids := make([]string, 0, len(catalog.Currencies))
	for id := range catalog.Currencies {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		if def := catalog.Currencies[id]; def.World == worldName && def.Tier == 1 {
			return id
		}
	}
	// Content that names no world, or a world with no tier-1 currency, keeps
	// the Mortal stone rather than charging nothing: a price that silently
	// costs an empty currency id is refused by walletDeltaTx anyway, and this
	// is the value every caller used before there was a choice.
	return fallbackBaseCurrency
}
