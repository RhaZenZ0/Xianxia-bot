package game

// The world's own goods on the world's own roads.
//
// `caravans` has carried an `owner_type` since it was built, defaulting to
// 'npc', and the resolver in the simulation has always had a branch reading
// `if owner_type == "player"` - the shape of code that expects a second kind
// of owner. There was never a second kind. The one production writer of the
// table is `caravanDispatchActionGo`, which hardcodes 'player', so the default
// was unreachable, the resolver's guard guarded nothing, and every road in the
// four worlds was empty of trade unless a player put something on it.
//
// This is the same fault the auction floors had before v1.0.0-rc.15: a system
// wired to consume what players produce and produce nothing itself. The
// travelling merchants are the natural senders - they already have a home, a
// route, a purse and a shelf of wares - so a merchant sitting out its dwell at
// a stop now sends a load ahead to the next one, and the same resolver that
// settles a player's caravan settles it.
//
// Nothing here needs a new table or column. A dispatch is one `caravans` row
// with the operations and departure event beside it, exactly as the player's
// is, so the resolver, the GM dashboard's caravan list and `caravan_events`
// all read it without knowing who sent it.

import (
	"encoding/json"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const (
	// No more than this many caravans leave in one tick. The reason is the
	// one `npc_finds.go` gives for capping finds: a world where every
	// merchant dispatches at once is a convoy, not a trade route.
	npcCaravanCapPerTick = 4
	// A merchant sends a load only this often, so a long tick (or a world
	// the GM has fast-forwarded) does not stack a merchant's whole year of
	// trade into one minute.
	npcCaravanIntervalMinutes = int64(2880)
	// What a load is worth to the sender, over the goods' shelf value. The
	// player's dispatch uses 1.15 for honest cargo; a merchant sending to
	// its own next stop takes the same margin.
	npcCaravanMarginPercent = int64(115)
)

// npcCaravanSender is a merchant that could send a load right now: at a stop,
// not already on the road itself, with somewhere to send it and something to
// send.
type npcCaravanSender struct {
	Key         string
	Merchant    worlddata.Merchant
	State       merchantState
	Destination string
}

// DispatchNPCCaravans sends the world's own trade down the roads.
//
// One caravan per merchant at a time, and one every `npcCaravanIntervalMinutes`
// at most: a merchant with a load still in transit waits for it. The caller
// owns the transaction; the count is how many set out.
func DispatchNPCCaravans(conn *storage.Conn, catalog worlddata.Catalog, gm int64) (int64, error) {
	if !caravanTablesExist(conn) {
		return 0, nil
	}
	ok, err := merchantTablesExist(conn)
	if err != nil || !ok {
		return 0, err
	}
	sent := int64(0)
	for _, key := range merchantKeys(catalog) {
		if sent >= npcCaravanCapPerTick {
			break
		}
		m := catalog.Merchants[key]
		if len(m.Route) < 2 || len(m.Wares) == 0 {
			continue
		}
		state, found, err := readMerchantState(conn, key)
		if err != nil {
			return sent, err
		}
		// A merchant the engine has not met yet is seeded by AdvanceMerchants
		// on this same tick; it can send next time, from a known stop.
		if !found || state.Destination != "" || state.Location == "" {
			continue
		}
		next := m.Route[(state.RouteIndex+1)%int64(len(m.Route))]
		if next == state.Location {
			continue
		}
		busy, err := merchantCaravanBusy(conn, key, gm)
		if err != nil {
			return sent, err
		}
		if busy {
			continue
		}
		did, err := dispatchOneNPCCaravan(conn, catalog, npcCaravanSender{Key: key, Merchant: m, State: state, Destination: next}, gm)
		if err != nil {
			return sent, err
		}
		if did {
			sent++
		}
	}
	return sent, nil
}

// caravanTablesExist keeps this a no-op on a database that predates the
// caravan tables, the way every other maintenance pass guards itself.
func caravanTablesExist(conn *storage.Conn) bool {
	res, err := conn.Execute(`SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name IN ('caravans','caravan_operations','caravan_events')`, nil)
	if err != nil {
		return false
	}
	row := firstRowMap(res)
	return row != nil && i64(row["n"]) == 3
}

// merchantCaravanBusy is both halves of the rate limit in one read: a load
// still on the road, or one sent too recently.
func merchantCaravanBusy(conn *storage.Conn, key string, gm int64) (bool, error) {
	res, err := conn.Execute(
		`SELECT COUNT(*) AS n FROM caravans WHERE owner_type='npc' AND owner_key=? AND (status='traveling' OR depart_game_minute>?)`,
		[]any{key, gm - npcCaravanIntervalMinutes})
	if err != nil {
		return false, err
	}
	row := firstRowMap(res)
	return row != nil && i64(row["n"]) > 0, nil
}

// npcCaravanWare is what this merchant puts in the packs this time: one of
// its own wares, chosen by a hash of the sender and the minute so the same
// tick replayed sends the same goods.
func npcCaravanWare(m worlddata.Merchant, key string, gm int64) (worlddata.MerchantWare, bool) {
	if len(m.Wares) == 0 {
		return worlddata.MerchantWare{}, false
	}
	pick := stablePercentGo(key, gm, "ware") % int64(len(m.Wares))
	ware := m.Wares[pick]
	if ware.ItemID == "" {
		return worlddata.MerchantWare{}, false
	}
	return ware, true
}

// dispatchOneNPCCaravan writes the three rows a caravan is: the caravan, the
// operations beside it, and the departure in its own event log.
func dispatchOneNPCCaravan(conn *storage.Conn, catalog worlddata.Catalog, s npcCaravanSender, gm int64) (bool, error) {
	ware, ok := npcCaravanWare(s.Merchant, s.Key, gm)
	if !ok {
		return false, nil
	}
	// The road is the same road a player's caravan takes, planned by the same
	// planner, at the scouting realm a merchant travels at - not a second
	// copy of the map rule.
	plan, found := canonicalRoadRoute(catalog, s.State.Location, s.Destination, merchantScoutRealmIndex)
	if !found {
		return false, nil
	}
	quantity := clamp(stablePercentGo(s.Key, gm, "quantity")%3+1, 1, 3)
	if ware.Quantity > 0 && quantity > ware.Quantity {
		quantity = ware.Quantity
	}
	currency := s.Merchant.Currency
	if currency == "" {
		currency = "low_spirit_stone"
	}
	unit := ware.Price
	if unit <= 0 {
		unit = merchantValuation(catalog, s.Merchant, ware.ItemID, 1)
	}
	payout := max64(1, unit*quantity*npcCaravanMarginPercent/100)
	travelMinutes := max64(30, (plan.TravelMinutes*5+3)/4)
	risk := clamp(plan.MaxDanger+10, 5, 85)
	// A house with a purse hires guards. Escort and concealment are what keep
	// the row out of the resolver's `legacy` branch (an all-zero operations
	// row is read as a caravan from before the system had one), so they are
	// written for the same reason a player's are.
	escort := clamp(s.Merchant.Budget/500, 1, 8)
	conceal := clamp(s.Merchant.Budget/1200, 0, 5)
	now := nowSeconds()
	cargo, _ := json.Marshal(map[string]any{
		ware.ItemID:       quantity,
		"_payout":         payout,
		"_currency":       currency,
		"_route":          plan.Nodes,
		"_travel_minutes": travelMinutes,
		"_sender_npc":     merchantNPCName(catalog, s.Key),
	})
	curs, err := conn.Execute(`INSERT INTO caravans(owner_type,owner_key,origin,destination,cargo_json,status,risk,depart_game_minute,arrive_game_minute,updated_at) VALUES('npc',?,?,?,?,'traveling',?,?,?,?)`,
		[]any{s.Key, s.State.Location, s.Destination, string(cargo), risk, gm, gm + travelMinutes, now})
	if err != nil {
		return false, err
	}
	cid := curs.LastInsertID
	const npcCaravanTaxRate = int64(8)
	if _, err = conn.Execute(`INSERT INTO caravan_operations(caravan_id,escort_strength,concealment,smuggling,tax_rate,toll_paid,intercepted,seized,payout_final,losses_json,outcome,resolved_game_minute,updated_at) VALUES(?,?,?,0,?,0,0,0,0,'{}','traveling',NULL,?)`,
		[]any{cid, escort, conceal, npcCaravanTaxRate, now}); err != nil {
		return false, err
	}
	detail, _ := json.Marshal(map[string]any{
		"escort_strength": escort,
		"concealment":     conceal,
		"smuggling":       false,
		"tax_rate":        npcCaravanTaxRate,
		"route":           plan.Nodes,
		"road_hops":       len(plan.Legs),
		"travel_minutes":  travelMinutes,
		"sender":          merchantNPCName(catalog, s.Key),
		"item_id":         ware.ItemID,
		"quantity":        quantity,
	})
	if _, err = conn.Execute(`INSERT INTO caravan_events(caravan_id,event_type,detail_json,game_minute,created_at) VALUES(?,'departed',?,?,?)`,
		[]any{cid, string(detail), gm, now}); err != nil {
		return false, err
	}
	return true, nil
}

// NPCCaravanSenderName is the person behind an NPC caravan's `owner_key`, for
// whoever has to say who sent it. The key is the merchant's catalogue id; the
// name is what a town would call them.
func NPCCaravanSenderName(catalog worlddata.Catalog, ownerKey string) string {
	if _, ok := catalog.Merchants[ownerKey]; !ok {
		return ownerKey
	}
	return merchantNPCName(catalog, ownerKey)
}
