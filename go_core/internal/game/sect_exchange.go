package game

// The sect exchange (v1.8.0): what a sect issues its own members, how they
// earn the points to pay for it, and the ranks those points carry them to.
//
// Until now `sect.redeem` could hand back only what somebody had donated into
// `sect_treasury`, points came from donations and a master's share of a
// disciple's breakthrough and nothing else, and a player's sect rank never
// moved at all: joining wrote Outer Disciple and only a GM's lever wrote
// anything after it. Each of those is fixed here, and all of it is content -
// the stock, the multiplier, the earning ratios and the promotion thresholds
// live in `sect_system.exchange`.
//
// Four rules hold it.
//
//   - Issued stock is priced at points_per_sect_value times the item's sect
//     value at its grade (itemDef), the number a donation of it earns, so an
//     item is worth the same to a sect whichever way it moves and a redeem
//     followed by a donation always loses points.
//   - creditSectContributionTx is the one door points come in by. It writes
//     the balance and the lifetime count together, and promotes. Spending
//     writes only the balance, so it can never cost a rank.
//   - Promotion only raises, and only to a rank the content's ladder lists;
//     a rank above the ladder's top (a GM's Elder) is never touched.
//   - A missing ratio pays nothing, never a zero read as a value, and a
//     world where migration 68 has not run yet credits points and promotes
//     nobody rather than failing the action.

import (
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// sectRedeemPressureMult is what a sect's own scarcity adds to a treasury
// redeem: a sect short of resources charges more for what its members hand
// back. Issued stock is not drawn from resources and does not pay it.
func sectRedeemPressureMult(resources int64) float64 {
	switch {
	case resources < 25:
		return 1.60
	case resources < 50:
		return 1.35
	case resources < 80:
		return 1.20
	}
	return 1.0
}

// sectEarnedColumn reports whether migration 68 has run.
func sectEarnedColumn(conn *storage.Conn) bool {
	ok, err := tableHasColumns(conn, "sect_membership", "contribution_earned")
	return err == nil && ok
}

// creditSectContributionTx is the one door contribution points come in by:
// the balance and the lifetime count move together, then the member is
// promoted if the count has passed a rung. It returns the rank reached, or ""
// when nothing changed. A caller with no membership row credits nobody.
func creditSectContributionTx(conn *storage.Conn, catalog worlddata.Catalog, userID, points, influence int64) (string, error) {
	if points <= 0 && influence <= 0 {
		return "", nil
	}
	points = max64(0, points)
	earned := sectEarnedColumn(conn)
	if earned {
		if _, err := conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points+?,contribution_earned=contribution_earned+?,influence=influence+? WHERE user_id=?`, []any{points, points, max64(0, influence), userID}); err != nil {
			return "", err
		}
	} else if _, err := conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points+?,influence=influence+? WHERE user_id=?`, []any{points, max64(0, influence), userID}); err != nil {
		return "", err
	}
	if !earned {
		return "", nil
	}
	return promoteSectMemberTx(conn, catalog, userID)
}

// promoteSectMemberTx raises a member to the highest rung of the content's
// ladder their lifetime contribution has passed, if that is above where they
// stand and the rank they hold is one the ladder reaches.
func promoteSectMemberTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (string, error) {
	ladder := catalog.SectExchange().Promotion
	if len(ladder) == 0 {
		return "", nil
	}
	r, err := conn.Execute(`SELECT rank_level,contribution_earned FROM sect_membership WHERE user_id=?`, []any{userID})
	if err != nil {
		return "", err
	}
	row := firstRowMap(r)
	if row == nil {
		return "", nil
	}
	rank, total := i64(row["rank_level"]), i64(row["contribution_earned"])
	top, target := int64(0), rank
	for _, rung := range ladder {
		top = max64(top, rung.RankLevel)
		if total >= rung.Earned && rung.RankLevel > target {
			target = rung.RankLevel
		}
	}
	if rank >= top || target <= rank {
		return "", nil
	}
	name := sectRankName(catalog, target)
	if _, err := conn.Execute(`UPDATE sect_membership SET rank_level=?,rank_name=? WHERE user_id=? AND rank_level<?`, []any{target, name, userID, target}); err != nil {
		return "", err
	}
	return name, nil
}

// sectIssuedPrice is what one of an issued item costs, in points.
func sectIssuedPrice(catalog worlddata.Catalog, itemID string) int64 {
	ex := catalog.SectExchange()
	return max64(1, ex.PointsPerSectValue*itemSectValue(catalog, itemID))
}

// sectIssuedLot finds itemID in what sectName issues.
func sectIssuedLot(catalog worlddata.Catalog, sectName, itemID string) (worlddata.SectExchangeLot, bool) {
	for _, lot := range catalog.SectExchange().Lots(sectName) {
		if lot.ItemID == itemID {
			return lot, true
		}
	}
	return worlddata.SectExchangeLot{}, false
}

// sectIssuedRedeemTx issues stock to a member for points. It never touches
// the treasury: issued stock is the sect's to make, and never runs out.
func sectIssuedRedeemTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, mem map[string]any, itemID string, quantity int64) (map[string]any, error) {
	sect := fmt.Sprint(mem["sect_name"])
	lot, ok := sectIssuedLot(catalog, sect, itemID)
	if !ok {
		return nil, fmt.Errorf("%s does not issue that item", sect)
	}
	if _, _, known := itemDef(catalog, itemID); !known {
		return nil, errors.New("unknown item")
	}
	if rank := i64(mem["rank_level"]); rank < lot.MinRankLevel {
		return nil, fmt.Errorf("that is issued to a %s or above; you are a %s", sectRankName(catalog, lot.MinRankLevel), sectRankName(catalog, rank))
	}
	unit := sectIssuedPrice(catalog, itemID)
	cost := unit * quantity
	points := i64(mem["contribution_points"])
	if points < cost {
		return nil, fmt.Errorf("not enough sect contribution points: %d needed, %d held", cost, points)
	}
	if _, err := conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points-? WHERE user_id=?`, []any{cost, userID}); err != nil {
		return nil, err
	}
	if err := addInventoryTx(conn, userID, map[string]int64{itemID: quantity}); err != nil {
		return nil, err
	}
	return map[string]any{"sect_name": sect, "item_id": itemID, "quantity": quantity, "source": "issued",
		"unit_cost": unit, "cost": cost, "remaining_points": points - cost}, nil
}

// sectExchangeQuery is what a member may redeem and at what price, read by
// the bot so it never restates a price or a rank of its own.
func sectExchangeQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	mem, err := sectMembershipRow(conn, userID)
	if err != nil {
		return nil, err
	}
	if mem == nil {
		return map[string]any{"member": false, "issued": []any{}, "treasury": []any{}}, nil
	}
	sect := fmt.Sprint(mem["sect_name"])
	rank := i64(mem["rank_level"])
	out := map[string]any{
		"member": true, "sect_name": sect, "rank_level": rank, "rank_name": sectRankName(catalog, rank),
		"contribution_points": i64(mem["contribution_points"]),
	}
	if sectEarnedColumn(conn) {
		out["contribution_earned"] = i64(mem["contribution_earned"])
	}
	// The next rung and what it asks, so the page can say how far away it is.
	for _, rung := range catalog.SectExchange().Promotion {
		if rung.RankLevel > rank {
			out["next_rank"] = map[string]any{"rank_level": rung.RankLevel, "rank_name": sectRankName(catalog, rung.RankLevel), "earned": rung.Earned}
			break
		}
	}
	issued := []map[string]any{}
	ownStock := map[string]bool{}
	for _, lot := range catalog.SectExchange().SectStock[sect] {
		ownStock[lot.ItemID] = true
	}
	for _, lot := range catalog.SectExchange().Lots(sect) {
		if _, _, ok := itemDef(catalog, lot.ItemID); !ok {
			continue
		}
		issued = append(issued, map[string]any{
			"item_id": lot.ItemID, "name": itemDisplayName(catalog, lot.ItemID), "points": sectIssuedPrice(catalog, lot.ItemID),
			"min_rank_level": lot.MinRankLevel, "min_rank_name": sectRankName(catalog, lot.MinRankLevel),
			"eligible": rank >= lot.MinRankLevel, "sect_only": ownStock[lot.ItemID],
		})
	}
	out["issued"] = issued
	resources := int64(50)
	if r, e := conn.Execute(`SELECT resources FROM sect_politics_state WHERE sect_name=?`, []any{sect}); e == nil {
		if x := firstRowMap(r); x != nil {
			resources = i64(x["resources"])
		}
	}
	mult := sectRedeemPressureMult(resources)
	out["pressure_mult"] = mult
	treasury := []map[string]any{}
	if r, e := conn.Execute(`SELECT item_id,quantity FROM sect_treasury WHERE sect_name=? AND quantity>0`, []any{sect}); e == nil {
		rows := rowsToMaps(r)
		sort.Slice(rows, func(i, j int) bool { return fmt.Sprint(rows[i]["item_id"]) < fmt.Sprint(rows[j]["item_id"]) })
		for _, row := range rows {
			id := fmt.Sprint(row["item_id"])
			treasury = append(treasury, map[string]any{"item_id": id, "name": itemDisplayName(catalog, id), "quantity": i64(row["quantity"]),
				"unit_cost": max64(1, int64(math.Round(float64(max64(1, itemSectValue(catalog, id)))*mult)))})
		}
	}
	out["treasury"] = treasury
	return out, nil
}

// sectCraftedDonation is the multiplier a donation earns when the donor is
// certified in the trade that makes it - the stall rule of v1.7.1, because an
// inventory row carries no record of who made it. 1 when it does not apply.
func sectCraftedDonation(conn *storage.Conn, catalog worlddata.Catalog, userID int64, itemID string) (float64, error) {
	mult := catalog.SectExchange().Earning.CraftedMultiplier
	if mult <= 1 {
		return 1, nil
	}
	trade := itemTrade(catalog, itemID)
	if trade == "" {
		return 1, nil
	}
	ok, err := tradeCertifiedTx(conn, userID, soulLifeTx(conn, userID), trade)
	if err != nil || !ok {
		return 1, err
	}
	return mult, nil
}

// sectCommissionPointsTx pays a member for finishing their own sect's work,
// in points per stone of the commission's reward.
func sectCommissionPointsTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, requiresSect string, stones int64) (map[string]any, error) {
	ratio := catalog.SectExchange().Earning.CommissionPointsPerStone
	if ratio <= 0 || stones <= 0 || strings.TrimSpace(requiresSect) == "" {
		return nil, nil
	}
	mem, err := sectMembershipRow(conn, userID)
	if err != nil || mem == nil || fmt.Sprint(mem["sect_name"]) != requiresSect {
		return nil, err
	}
	points := max64(1, int64(math.Round(float64(stones)*ratio)))
	promoted, err := creditSectContributionTx(conn, catalog, userID, points, max64(1, points/10))
	if err != nil {
		return nil, err
	}
	out := map[string]any{"sect": requiresSect, "points": points}
	if promoted != "" {
		out["promoted_to"] = promoted
	}
	return out, nil
}

// sectEventPointsTx pays a member for a world event in their sect's own
// world: what the action added to their event contribution, up to the cap
// per event. newTotal is the contribution after the action.
func sectEventPointsTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, eventKey string, delta, newTotal int64) (map[string]any, error) {
	limit := catalog.SectExchange().Earning.EventPointsCap
	if limit <= 0 || delta <= 0 {
		return nil, nil
	}
	points := min64(limit, newTotal) - min64(limit, max64(0, newTotal-delta))
	if points <= 0 {
		return nil, nil
	}
	mem, err := sectMembershipRow(conn, userID)
	if err != nil || mem == nil {
		return nil, err
	}
	sect := fmt.Sprint(mem["sect_name"])
	gate := sectGate(catalog, sect)
	if gate == "" {
		return nil, nil
	}
	r, err := conn.Execute(`SELECT location FROM world_events WHERE event_key=?`, []any{eventKey})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(r)
	if row == nil || EraWorldOf(catalog, fmt.Sprint(row["location"])) != EraWorldOf(catalog, gate) {
		return nil, nil
	}
	promoted, err := creditSectContributionTx(conn, catalog, userID, points, max64(1, points/10))
	if err != nil {
		return nil, err
	}
	out := map[string]any{"sect": sect, "points": points}
	if promoted != "" {
		out["promoted_to"] = promoted
	}
	return out, nil
}
