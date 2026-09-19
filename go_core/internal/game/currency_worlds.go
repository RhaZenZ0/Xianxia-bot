package game

import (
	"fmt"
	"sort"

	"xianxia/core/internal/storage"
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

// characterBaseCurrencyTx is the money of the world a character is standing in.
//
// Every price in the content file is denominated in its own world's tier-1
// currency - the shops, the arrays, the black market, the caravan since rc.43 -
// and until rc.44 almost every *reward* credited `low_spirit_stone` whatever
// world it was earned in. So a cultivator in the Spiritual World was paid in
// Mortal stones and charged in spirit crystals: the teleport arrays' old fault,
// "payable only by somebody who had already arrived", spread across the whole
// upper-world economy.
func characterBaseCurrencyTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (string, error) {
	res, err := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return fallbackBaseCurrency, err
	}
	row := firstRowMap(res)
	if row == nil {
		return fallbackBaseCurrency, nil
	}
	// Somewhere the catalogue does not carry - a household interior, a personal
	// world, a scene key - has no world of its own, and falls back to the
	// Mortal stone, which is the currency every one of these paths used before
	// there was a question.
	return worldBaseCurrency(catalog, catalog.Locations[fmt.Sprint(row["location"])].World), nil
}

// characterWalletDeltaTx moves a character's own money, and is the door every
// reward and every ordinary spend goes through. Naming a currency explicitly
// (walletDeltaTx) is for the prices content sets: a shop's, an array's, the
// departure world's at a tribulation gate.
func characterWalletDeltaTx(conn *storage.Conn, catalog worlddata.Catalog, userID, delta int64, now float64) (int64, error) {
	currency, err := characterBaseCurrencyTx(conn, catalog, userID)
	if err != nil {
		return 0, err
	}
	return walletDeltaTx(conn, catalog, userID, currency, delta, now)
}

// characterWalletBalanceTx is what they can spend where they stand.
func characterWalletBalanceTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (int64, error) {
	currency, err := characterBaseCurrencyTx(conn, catalog, userID)
	if err != nil {
		return 0, err
	}
	return walletBalanceTx(conn, userID, currency)
}
