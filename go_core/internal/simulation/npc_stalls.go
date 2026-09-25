package simulation

// The town shops at the stalls (v1.5.0).
//
// A player's stall (game/stall_actions.go) sells to other cultivators while
// its owner is away, and on a small server that is often nobody for a day. So
// the world's own people buy too - a step of `dynamic_economy`, daily and on
// unless a GM switches the system off - and four bounds keep that from being
// a stone printer, which is what an NPC purse buying at a player's asking
// price would otherwise be:
//
//   - **Never the last one.** Only a listing with two or more units is
//     considered, and a purchase is one unit, so the last of anything on a
//     stall is left for a cultivator. The listing is re-read after every buy,
//     so 2 -> 1 drops it from the candidates on the same tick. (The owner's
//     own bound.)
//   - **Below the shelf.** `game.NPCStallCeiling`: one coin under the
//     cheapest content shelf price for that item in that coin, and an item no
//     shop sells is never bought - no reference means no mint guard.
//   - **Its own money.** Wealth comes off `npc_civilization_state`, finite
//     and guarded (`AND wealth>=?`), and no player is ever debited here.
//   - **So many a day.** `stall_system.npc_buys_per_city_per_day` per city per
//     day of steps, at most three days caught up at once.
//
// Everything after the roll goes through `game.StallSaleTx` - the one payout,
// which the player's buy also uses - so the town and a cultivator pay a seller
// by exactly the same rule (the WalletDeltaTx precedent, rc.43).
//
// The step never returns an error over a listing: one system's error ends the
// tick (CLAUDE.md, npc_consignments), so a bad row is skipped and only a
// genuine SQL failure comes back.

import (
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// npcStallBuyChance is the roll a browsing townsperson buys on, out of a
	// hundred; npcStallAffinityBonus is what a trade that makes the item adds.
	npcStallBuyChance     = 40
	npcStallAffinityBonus = 30
	// npcStallMaxCatchUpDays bounds how many days of buying one long-overdue
	// tick performs at once.
	npcStallMaxCatchUpDays = 3
)

// npcStallTradeWords is what a profession string says about a trade: a smith
// buys swords, an apothecary pills. A tiebreak, never a requirement.
var npcStallTradeWords = map[string][]string{
	"forging":     {"smith", "forge", "armour", "armor", "weapon"},
	"alchemy":     {"alchem", "apothec", "herb", "physician", "healer", "pill"},
	"inscription": {"talisman", "scribe", "inscri", "calligraph"},
	"formation":   {"array", "formation", "geoman"},
}

func npcStallAffinity(profession, trade string) bool {
	trade = strings.ToLower(strings.TrimSpace(trade))
	profession = strings.ToLower(profession)
	for _, word := range npcStallTradeWords[trade] {
		if strings.Contains(profession, word) {
			return true
		}
	}
	return false
}

type stallShopper struct {
	Name       string
	Profession string
	Wealth     int64
}

// npcStallPurchases is the step. It returns how many units the town bought.
func (r *Runner) npcStallPurchases(conn *storage.Conn, steps, gm int64) (int64, error) {
	for _, table := range []string{"player_stalls", "stall_listings", "stall_sales", "npc_civilization_state"} {
		if !simTableExists(conn, table) {
			return 0, nil
		}
	}
	cityRows, err := conn.Execute(`SELECT DISTINCT city FROM stall_listings WHERE quantity>=2 ORDER BY city`, nil)
	if err != nil {
		return 0, err
	}
	days := steps
	if days < 1 {
		days = 1
	}
	if days > npcStallMaxCatchUpDays {
		days = npcStallMaxCatchUpDays
	}
	budget := game.StallNPCBuysPerCityPerDay(r.World) * days
	now := nowFloat()
	bought := int64(0)
	for _, cityRow := range cityRows.Rows {
		city := fmt.Sprint(cityRow[0])
		shoppers, err := r.stallShoppersIn(conn, city)
		if err != nil {
			return bought, err
		}
		if len(shoppers) == 0 {
			continue
		}
		for slot := int64(0); slot < budget; slot++ {
			listings, err := r.stallListingsForTheTown(conn, city)
			if err != nil {
				return bought, err
			}
			if len(listings) == 0 {
				break
			}
			listing := listings[int(hash64(city, fmt.Sprint(gm), fmt.Sprint(slot))%uint64(len(listings)))]
			price := i64(listing["unit_price"])
			trade := game.ItemTrade(r.World, fmt.Sprint(listing["item_id"]))
			shopper, affinity, ok := pickStallShopper(shoppers, price, trade, city, gm, slot)
			if !ok {
				break
			}
			chance := npcStallBuyChance
			if affinity {
				chance += npcStallAffinityBonus
			}
			roll, err := gamerng.Intn(100)
			if err != nil {
				return bought, err
			}
			if roll >= chance {
				continue
			}
			res, err := conn.Execute(`UPDATE npc_civilization_state SET wealth=wealth-?,updated_at=? WHERE npc_name=? AND wealth>=?`,
				[]any{price, now, shopper.Name, price})
			if err != nil {
				return bought, err
			}
			if res.RowsAffected == 0 {
				continue
			}
			if _, _, err := game.StallSaleTx(conn, r.World, listing, 1, nil, shopper.Name, gm, now); err != nil {
				return bought, err
			}
			for i := range shoppers {
				if shoppers[i].Name == shopper.Name {
					shoppers[i].Wealth -= price
				}
			}
			bought++
		}
	}
	return bought, nil
}

// stallShoppersIn is everybody alive and standing in the city or one of its
// parts, in a fixed order.
func (r *Runner) stallShoppersIn(conn *storage.Conn, city string) ([]stallShopper, error) {
	places := []string{city}
	for name, loc := range r.World.Locations {
		if loc.OutsideLocation == city && name != city {
			places = append(places, name)
		}
	}
	sort.Strings(places)
	marks := make([]string, len(places))
	args := make([]any, len(places))
	for i, place := range places {
		marks[i] = "?"
		args[i] = place
	}
	res, err := conn.Execute(`SELECT npc_name,profession,wealth FROM npc_civilization_state WHERE status='alive' AND current_location IN (`+
		strings.Join(marks, ",")+`) ORDER BY npc_name`, args)
	if err != nil {
		return nil, err
	}
	out := []stallShopper{}
	for _, row := range res.Rows {
		out = append(out, stallShopper{Name: fmt.Sprint(row[0]), Profession: fmt.Sprint(row[1]), Wealth: i64(row[2])})
	}
	return out, nil
}

// stallListingsForTheTown is what the town may buy in a city right now: two
// or more left, and asked at or under the shelf ceiling.
func (r *Runner) stallListingsForTheTown(conn *storage.Conn, city string) ([]map[string]any, error) {
	res, err := conn.Execute(`SELECT listing_id,user_id,city,item_id,quantity,unit_price,currency_id FROM stall_listings WHERE city=? AND quantity>=2 ORDER BY listing_id`, []any{city})
	if err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for _, row := range res.Rows {
		listing := map[string]any{
			"listing_id": row[0], "user_id": row[1], "city": row[2], "item_id": row[3],
			"quantity": row[4], "unit_price": row[5], "currency_id": row[6],
		}
		ceiling, ok := game.NPCStallCeiling(r.World, fmt.Sprint(row[3]), fmt.Sprint(row[6]))
		if !ok || i64(row[5]) > ceiling {
			continue
		}
		out = append(out, listing)
	}
	return out, nil
}

// pickStallShopper is who steps up to the listing: somebody who can pay,
// preferring a trade that makes the thing, chosen by hash so the same tick on
// the same world picks the same person.
func pickStallShopper(shoppers []stallShopper, price int64, trade, city string, gm, slot int64) (stallShopper, bool, bool) {
	able := []stallShopper{}
	fond := []stallShopper{}
	for _, s := range shoppers {
		if s.Wealth < price {
			continue
		}
		able = append(able, s)
		if npcStallAffinity(s.Profession, trade) {
			fond = append(fond, s)
		}
	}
	if len(able) == 0 {
		return stallShopper{}, false, false
	}
	pool, affinity := able, false
	if len(fond) > 0 {
		pool, affinity = fond, true
	}
	pick := pool[int(hash64(city, fmt.Sprint(gm), fmt.Sprint(slot), "shopper")%uint64(len(pool)))]
	return pick, affinity, true
}
