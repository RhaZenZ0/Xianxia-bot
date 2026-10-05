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
//     value at its grade (itemDef) - three times what a donation of it earns
//     (one times the sect value, more for a craftsman's own trade) - so a
//     redeem followed by a donation always loses points.
//   - creditSectContributionTx is the one door points come in by. It writes
//     the balance and the lifetime count together, and says when the count
//     has crossed the next rung (v1.25.0: it no longer promotes). An issued
//     redeem writes only the balance, so it can never cost a rank; a treasury
//     redeem also takes its cost off the lifetime count (v1.12.3), because
//     what comes back out of the treasury was not a contribution.
//   - Promotion is granted by asking (`sect.promote`, v1.25.0): one rung at a
//     time, only to a rank the content's ladder lists, and only when the
//     lifetime count reaches it. A rank a GM has taken away is not given back
//     by the next donation, and a rank above the ladder's top is never touched.
//   - A missing ratio pays nothing, never a zero read as a value, and a
//     world where migration 69 has not run yet credits points and makes
//     nobody eligible rather than failing the action.

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// sectIssuedMaxQuantity is the most one issued redeem may ask for (v1.12.3).
// It is the engine's bound and not the bot's: the cost is unit times quantity,
// and a client sending 683212743470724134 of a two-point item paid two points
// and was handed that many talismans when the product wrapped.
const sectIssuedMaxQuantity = int64(99)

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

// sectTreasuryUnitCost is what one unit handed back out of a sect's treasury
// costs in points (v1.12.3): the sect value times the greater of the sect's
// scarcity multiplier and the crafted multiplier. The second is the floor
// that matters - a donation of a craftsman's own trade earns that multiple, so
// a redeem priced at the plain sect value paid 120 points for a Longevity
// Pill that had been donated for 180, and the loop netted 60 a turn with the
// pill kept. A redeem costs at least what any donation of the unit could have
// earned, rounded the way the donation rounds, so the two can never part.
func sectTreasuryUnitCost(catalog worlddata.Catalog, itemID string, resources int64) int64 {
	mult := math.Max(sectRedeemPressureMult(resources), math.Max(1, catalog.SectExchange().Earning.CraftedMultiplier))
	return max64(1, int64(math.Round(float64(max64(1, itemSectValue(catalog, itemID)))*mult)))
}

// sectEarnedColumn reports whether migration 69 has run.
func sectEarnedColumn(conn *storage.Conn) bool {
	ok, err := tableHasColumns(conn, "sect_membership", "contribution_earned")
	return err == nil && ok
}

// creditSectContributionTx is the one door contribution points come in by:
// the balance and the lifetime count move together. It returns the rank the
// credit has just made the member eligible for - the count crossed the next
// rung's mark - or "" when nothing changed. A caller with no membership row
// credits nobody.
//
// It promoted the member itself until v1.25.0. On the owner's call a rank is
// granted by somebody now (`sect.promote`, npc_master.go): the count only says
// a member may ask, so a credit that crosses a rung tells them so and moves no
// rank - which also leaves a GM's demotion standing, the v1.12.3 rule.
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
	if !earned || points <= 0 {
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
	level, need, ok := nextPromotionRung(catalog, i64(row["rank_level"]))
	total := i64(row["contribution_earned"])
	if !ok || total < need || total-points >= need {
		return "", nil
	}
	return sectRankName(catalog, level), nil
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
	if quantity < 1 || quantity > sectIssuedMaxQuantity {
		return nil, fmt.Errorf("a sect issues between 1 and %d of an item at a time", sectIssuedMaxQuantity)
	}
	unit := sectIssuedPrice(catalog, itemID)
	if unit > math.MaxInt64/quantity {
		return nil, errors.New("issue total overflow")
	}
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
	out["pressure_mult"] = sectRedeemPressureMult(resources)
	treasury := []map[string]any{}
	if r, e := conn.Execute(`SELECT item_id,quantity FROM sect_treasury WHERE sect_name=? AND quantity>0`, []any{sect}); e == nil {
		rows := rowsToMaps(r)
		sort.Slice(rows, func(i, j int) bool { return fmt.Sprint(rows[i]["item_id"]) < fmt.Sprint(rows[j]["item_id"]) })
		for _, row := range rows {
			id := fmt.Sprint(row["item_id"])
			treasury = append(treasury, map[string]any{"item_id": id, "name": itemDisplayName(catalog, id), "quantity": i64(row["quantity"]),
				"unit_cost": sectTreasuryUnitCost(catalog, id, resources)})
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

// sectDonationCap is the most points one unit of an item may earn: its
// cheapest shelf price in the Mortal World's own stone, at its grade (the
// Low shelf times the grade's price multiple, the reference NPCStallCeiling
// uses for a grade no shelf carries). Without it a point could be bought for
// a third of a stone - the three realm keys are worth 700 to 1,200 points
// and sell for 280 to 420 - and issued stock sold on to a keeper at a profit.
// An item no Mortal shelf sells has no cap; its price anywhere above is a
// hundred stones a unit or more.
func sectDonationCap(catalog worlddata.Catalog, itemID string) (int64, bool) {
	_, rung, ok := itemDef(catalog, itemID)
	if !ok {
		return 0, false
	}
	shelf, found := cheapestShelfPrice(catalog, itemBaseID(itemID), "low_spirit_stone")
	if !found {
		return 0, false
	}
	return shelf * max64(1, rung.PriceMult), true
}

// sectCommissionPointsTx pays a member for finishing their own sect's work,
// in points per stone of the commission's reward.
func sectCommissionPointsTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, requiresSect string, stones int64) (map[string]any, error) {
	ratio := catalog.SectExchange().Earning.CommissionPointsPerStone
	if ratio <= 0 || stones <= 0 || strings.TrimSpace(requiresSect) == "" || !tableExistsTx(conn, "sect_membership") {
		return nil, nil
	}
	mem, err := sectMembershipRow(conn, userID)
	if err != nil || mem == nil || fmt.Sprint(mem["sect_name"]) != requiresSect {
		return nil, err
	}
	points := max64(1, int64(math.Round(float64(stones)*ratio)))
	eligible, err := creditSectContributionTx(conn, catalog, userID, points, max64(1, points/10))
	if err != nil {
		return nil, err
	}
	out := map[string]any{"sect": requiresSect, "points": points}
	if eligible != "" {
		out["eligible_for"] = eligible
	}
	return out, nil
}

// sectEventPointsEventType is the event_log row that remembers what a member
// has been paid for one world event.
const sectEventPointsEventType = "sect_event_points"

// sectEventPointsTx pays a member for a world event in their sect's own
// world: what the action added to their event contribution, up to the cap
// per event. newTotal is the contribution after the action.
//
// The cap is held against a monotonic record (v1.12.3), not against the
// running contribution. Interfere takes a point off that total and has no
// wait, so at the cap three interferes and one support paid the same three
// points again, for ever. What has already been paid for this event is the
// sum of the member's own sect_event_points rows, and the most an action can
// pay is what it added, up to the room the cap still leaves under the highest
// total the contribution has reached. A row is written for what was paid, and
// a read that fails pays nothing: an exchange that cannot count does not pay.
func sectEventPointsTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, eventKey string, delta, newTotal int64) (map[string]any, error) {
	limit := catalog.SectExchange().Earning.EventPointsCap
	if limit <= 0 || delta <= 0 || !tableExistsTx(conn, "sect_membership") {
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
	paid, err := conn.Execute(`SELECT COALESCE(SUM(json_extract(payload_json,'$.points')),0) FROM event_log WHERE user_id=? AND event_type=? AND json_extract(payload_json,'$.key')=?`,
		[]any{userID, sectEventPointsEventType, eventKey})
	if err != nil || len(paid.Rows) == 0 {
		return nil, nil
	}
	points := min64(delta, min64(limit, max64(0, newTotal))-storage.ParseInt(paid.Rows[0][0]))
	if points <= 0 {
		return nil, nil
	}
	eligible, err := creditSectContributionTx(conn, catalog, userID, points, max64(1, points/10))
	if err != nil {
		return nil, err
	}
	record, _ := json.Marshal(map[string]any{"key": eventKey, "points": points, "total": newTotal})
	if _, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, sectEventPointsEventType, string(record), nowSeconds()}); err != nil {
		return nil, err
	}
	out := map[string]any{"sect": sect, "points": points}
	if eligible != "" {
		out["eligible_for"] = eligible
	}
	return out, nil
}
