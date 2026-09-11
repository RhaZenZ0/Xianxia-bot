package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Travelling merchants (v0.34.1). A merchant is content: a name, a world, a
// loop of cities and a purse. The engine walks them along the loop on the
// simulation tick, lets them buy what an auction floor could not sell, and
// sells that stock back at a markup to any player who is in the same city
// while the merchant dwells there - or on the same stretch of road while
// both are travelling it. Nothing here is narrated into being: the stock a
// merchant carries is exactly what players failed to sell.

// merchantFallbackTravelMinutes is the leg time between two route stops that
// the road graph does not join directly. Routes are content and may skip
// across a region; a merchant is not a player and does not need a road plan
// to be somewhere in four hours.
const merchantFallbackTravelMinutes = int64(240)

// merchantScoutRealmIndex is the realm index handed to the road planner when
// a merchant looks for a road: every public road, whatever its gate.
const merchantScoutRealmIndex = int64(99)

type merchantState struct {
	Merchant    string
	Location    string
	Destination string
	Depart      int64
	Arrive      int64
	DwellUntil  int64
	Budget      int64
	RouteIndex  int64
}

func merchantTablesExist(conn *storage.Conn) (bool, error) {
	res, err := conn.Execute(`SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name IN ('merchant_state','merchant_stock')`, nil)
	if err != nil {
		return false, err
	}
	row := firstRowMap(res)
	return row != nil && i64(row["n"]) == 2, nil
}

func merchantKeys(catalog worlddata.Catalog) []string {
	keys := make([]string, 0, len(catalog.Merchants))
	for key := range catalog.Merchants {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func merchantRouteHas(m worlddata.Merchant, location string) bool {
	for _, stop := range m.Route {
		if stop == location {
			return true
		}
	}
	return false
}

// merchantWare is the content line for an item the merchant always stocks,
// or false when the item is only ever an auction leftover in this pack.
func merchantWare(m worlddata.Merchant, itemID string) (worlddata.MerchantWare, bool) {
	for _, ware := range m.Wares {
		if ware.ItemID == itemID {
			return ware, true
		}
	}
	return worlddata.MerchantWare{}, false
}

// restockMerchantWaresTx fills the merchant's shop back up to its content
// quantities at content prices. A line that still has more than the content
// quantity (bought cheap off a floor) keeps what it has.
func restockMerchantWaresTx(conn *storage.Conn, m worlddata.Merchant, key string, gm int64, now float64) error {
	for _, ware := range m.Wares {
		if strings.TrimSpace(ware.ItemID) == "" || ware.Quantity <= 0 {
			continue
		}
		if _, err := conn.Execute(`INSERT INTO merchant_stock(merchant,item_id,quantity,cost,price,acquired_game_minute,updated_at) VALUES(?,?,?,?,?,?,?)
		ON CONFLICT(merchant,item_id) DO UPDATE SET quantity=MAX(merchant_stock.quantity,excluded.quantity),price=excluded.price,updated_at=excluded.updated_at`,
			[]any{key, ware.ItemID, ware.Quantity, 0, max64(1, ware.Price), gm, now}); err != nil {
			return err
		}
	}
	return nil
}

func merchantRouteIndex(m worlddata.Merchant, location string) int64 {
	for i, stop := range m.Route {
		if stop == location {
			return int64(i)
		}
	}
	return 0
}

// merchantNPCName is the name of the catalog NPC that fronts this merchant,
// or the merchant's own name when no NPC entry claims it.
func merchantNPCName(catalog worlddata.Catalog, key string) string {
	names := make([]string, 0, 1)
	for name, npc := range catalog.NPCs {
		if npc.Merchant == key {
			names = append(names, name)
		}
	}
	if len(names) == 0 {
		return catalog.Merchants[key].Name
	}
	sort.Strings(names)
	return names[0]
}

func readMerchantState(conn *storage.Conn, key string) (merchantState, bool, error) {
	res, err := conn.Execute(`SELECT * FROM merchant_state WHERE merchant=?`, []any{key})
	if err != nil {
		return merchantState{}, false, err
	}
	row := firstRowMap(res)
	if row == nil {
		return merchantState{}, false, nil
	}
	return merchantState{
		Merchant:    key,
		Location:    fmt.Sprint(row["location"]),
		Destination: fmt.Sprint(row["destination"]),
		Depart:      i64(row["depart_game_minute"]),
		Arrive:      i64(row["arrive_game_minute"]),
		DwellUntil:  i64(row["dwell_until_game_minute"]),
		Budget:      i64(row["budget"]),
		RouteIndex:  i64(row["route_index"]),
	}, true, nil
}

func writeMerchantState(conn *storage.Conn, s merchantState, now float64) error {
	_, err := conn.Execute(`INSERT INTO merchant_state(merchant,location,destination,depart_game_minute,arrive_game_minute,dwell_until_game_minute,budget,route_index,updated_at)
		VALUES(?,?,?,?,?,?,?,?,?)
		ON CONFLICT(merchant) DO UPDATE SET location=excluded.location,destination=excluded.destination,depart_game_minute=excluded.depart_game_minute,
		arrive_game_minute=excluded.arrive_game_minute,dwell_until_game_minute=excluded.dwell_until_game_minute,budget=excluded.budget,route_index=excluded.route_index,updated_at=excluded.updated_at`,
		[]any{s.Merchant, s.Location, s.Destination, s.Depart, s.Arrive, s.DwellUntil, s.Budget, s.RouteIndex, now})
	return err
}

// ensureMerchantState reads a merchant's row, seeding it at home with the
// content purse the first time the engine meets it.
func ensureMerchantState(conn *storage.Conn, catalog worlddata.Catalog, key string, gm int64, now float64) (merchantState, error) {
	state, ok, err := readMerchantState(conn, key)
	if err != nil {
		return merchantState{}, err
	}
	if ok {
		return state, nil
	}
	m := catalog.Merchants[key]
	state = merchantState{Merchant: key, Location: m.Home, DwellUntil: gm + m.DwellMinutes, Budget: m.Budget, RouteIndex: merchantRouteIndex(m, m.Home)}
	if err := writeMerchantState(conn, state, now); err != nil {
		return merchantState{}, err
	}
	if err := restockMerchantWaresTx(conn, m, key, gm, now); err != nil {
		return merchantState{}, err
	}
	return state, nil
}

// merchantWhereabouts is the display form of where a merchant is.
func merchantWhereabouts(s merchantState) string {
	if s.Destination != "" {
		return fmt.Sprintf("On the road: %s → %s", s.Location, s.Destination)
	}
	return s.Location
}

func relocateMerchantNPC(conn *storage.Conn, catalog worlddata.Catalog, key string, where string, now float64) error {
	probe, err := conn.Execute(`SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name='npc_civilization_state'`, nil)
	if err != nil {
		return err
	}
	if firstRowMap(probe) == nil {
		return nil
	}
	_, err = conn.Execute(`UPDATE npc_civilization_state SET current_location=?,updated_at=? WHERE npc_name=?`, []any{where, now, merchantNPCName(catalog, key)})
	return err
}

// AdvanceMerchants moves every catalog merchant one step along its loop when
// the clock says so: a merchant on the road arrives once the arrival minute
// passes, and a merchant whose dwell has run out sets off for the next stop.
// Leg time is the road planner's when the two stops are joined by road and a
// fixed four hours when they are not. The caller owns the transaction; the
// count is how many merchants changed state.
func AdvanceMerchants(conn *storage.Conn, catalog worlddata.Catalog, gm int64) (int64, error) {
	ok, err := merchantTablesExist(conn)
	if err != nil || !ok {
		return 0, err
	}
	now := nowSeconds()
	moved := int64(0)
	for _, key := range merchantKeys(catalog) {
		m := catalog.Merchants[key]
		if len(m.Route) == 0 || m.Home == "" {
			continue
		}
		state, ok, err := readMerchantState(conn, key)
		if err != nil {
			return moved, err
		}
		if !ok {
			if _, err := ensureMerchantState(conn, catalog, key, gm, now); err != nil {
				return moved, err
			}
			moved++
			continue
		}
		switch {
		case state.Destination != "":
			if gm < state.Arrive {
				continue
			}
			state.Location = state.Destination
			state.Destination = ""
			state.RouteIndex = merchantRouteIndex(m, state.Location)
			state.DwellUntil = gm + m.DwellMinutes
			if state.Location == m.Home {
				// Home is where the shop is restocked (v0.34.2).
				if err := restockMerchantWaresTx(conn, m, key, gm, now); err != nil {
					return moved, err
				}
			}
		case gm >= state.DwellUntil && len(m.Route) > 1:
			next := m.Route[(state.RouteIndex+1)%int64(len(m.Route))]
			if next == state.Location {
				continue
			}
			minutes := merchantFallbackTravelMinutes
			if plan, found := canonicalRoadRoute(catalog, state.Location, next, merchantScoutRealmIndex); found && plan.TravelMinutes > 0 {
				minutes = plan.TravelMinutes
			}
			state.Destination = next
			state.Depart = gm
			state.Arrive = gm + minutes
		default:
			continue
		}
		if err := writeMerchantState(conn, state, now); err != nil {
			return moved, err
		}
		if err := relocateMerchantNPC(conn, catalog, key, merchantWhereabouts(state), now); err != nil {
			return moved, err
		}
		moved++
	}
	return moved, nil
}

// merchantResalePrice is what a merchant asks per unit for stock bought at
// unitCost: the content markup, and never below the item's base price.
func merchantResalePrice(catalog worlddata.Catalog, m worlddata.Merchant, itemID string, unitCost int64) int64 {
	markup := m.MarkupPercent
	if markup <= 0 {
		markup = 150
	}
	price := (unitCost*markup + 99) / 100
	if base := catalog.Items[itemID].BasePrice; price < base {
		price = base
	}
	return max64(1, price)
}

// MerchantBuysUnsoldLot is the auction floor's last bidder. When a lot ends
// with no bid, a merchant whose loop passes the house's city and whose purse
// covers the starting bid takes it at that price: the seller is paid, the
// merchant's purse shrinks and its pack grows. It returns the merchant key
// and true when a merchant bought; false leaves the lot to be returned to
// the seller. The caller owns the transaction.
func MerchantBuysUnsoldLot(conn *storage.Conn, catalog worlddata.Catalog, auction map[string]any, gm int64) (string, bool, error) {
	ok, err := merchantTablesExist(conn)
	if err != nil || !ok {
		return "", false, err
	}
	house, found := catalog.AuctionHouses[fmt.Sprint(auction["house_id"])]
	if !found {
		return "", false, nil
	}
	city := house.EntranceLocation
	if city == "" {
		city = house.Location
	}
	currency := strings.TrimSpace(fmt.Sprint(auction["currency_id"]))
	price := max64(1, i64(auction["starting_bid"]))
	quantity := max64(1, i64(auction["quantity"]))
	itemID := fmt.Sprint(auction["item_id"])
	now := nowSeconds()
	// The merchant standing in the city takes it first; otherwise the first
	// (by key) whose loop passes through, buying through an agent.
	var chosen string
	var chosenState merchantState
	for pass := 0; pass < 2 && chosen == ""; pass++ {
		for _, key := range merchantKeys(catalog) {
			m := catalog.Merchants[key]
			if m.Currency != currency || !merchantRouteHas(m, city) {
				continue
			}
			state, err := ensureMerchantState(conn, catalog, key, gm, now)
			if err != nil {
				return "", false, err
			}
			if state.Budget < price {
				continue
			}
			if pass == 0 && (state.Location != city || state.Destination != "") {
				continue
			}
			chosen, chosenState = key, state
			break
		}
	}
	if chosen == "" {
		return "", false, nil
	}
	m := catalog.Merchants[chosen]
	if _, err := walletDeltaTx(conn, i64(auction["seller_user_id"]), currency, price, now); err != nil {
		return "", false, err
	}
	chosenState.Budget -= price
	if err := writeMerchantState(conn, chosenState, now); err != nil {
		return "", false, err
	}
	unitCost := (price + quantity - 1) / quantity
	resale := merchantResalePrice(catalog, m, itemID, unitCost)
	if ware, ok := merchantWare(m, itemID); ok {
		// The shop's own line keeps the shop's price.
		resale = max64(1, ware.Price)
	}
	if _, err := conn.Execute(`INSERT INTO merchant_stock(merchant,item_id,quantity,cost,price,acquired_game_minute,updated_at) VALUES(?,?,?,?,?,?,?)
		ON CONFLICT(merchant,item_id) DO UPDATE SET quantity=merchant_stock.quantity+excluded.quantity,cost=excluded.cost,price=excluded.price,acquired_game_minute=excluded.acquired_game_minute,updated_at=excluded.updated_at`,
		[]any{chosen, itemID, quantity, unitCost, resale, gm, now}); err != nil {
		return "", false, err
	}
	if _, err := conn.Execute(`UPDATE auctions SET merchant_buyer=?,current_bid=? WHERE auction_id=?`, []any{chosen, price, i64(auction["auction_id"])}); err != nil {
		return "", false, err
	}
	return chosen, true, nil
}

// actorWhereabouts is where a player is for the purpose of meeting a
// merchant: their city when standing still, or the road leg they are on.
// An arrived transit whose record has not been cleared yet counts as the
// destination, which is what every other action would clear it to.
func actorWhereabouts(conn *storage.Conn, userID int64, gm int64) (location string, from string, to string, err error) {
	res, err := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return "", "", "", err
	}
	row := firstRowMap(res)
	if row == nil {
		return "", "", "", errors.New("character not found")
	}
	location = fmt.Sprint(row["location"])
	tr, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{roadTransitStateKey(userID)})
	if err != nil {
		return "", "", "", err
	}
	if len(tr.Rows) == 0 || len(tr.Rows[0]) == 0 {
		return location, "", "", nil
	}
	var state roadTransitState
	if err := json.Unmarshal([]byte(fmt.Sprint(tr.Rows[0][0])), &state); err != nil {
		return "", "", "", fmt.Errorf("invalid road transit state: %w", err)
	}
	if gm >= state.ArrivalGameMinute {
		return state.Destination, "", "", nil
	}
	// The player is somewhere along the route; the leg they are on is the
	// one whose share of the journey covers the elapsed time, which without
	// per-leg timing is approximated by elapsed share of the whole.
	route := state.Route
	if len(route) < 2 {
		route = []string{state.Origin, state.Destination}
	}
	total := state.ArrivalGameMinute - state.DepartureGameMinute
	legs := int64(len(route) - 1)
	leg := int64(0)
	if total > 0 && legs > 0 {
		leg = (gm - state.DepartureGameMinute) * legs / total
		if leg >= legs {
			leg = legs - 1
		}
		if leg < 0 {
			leg = 0
		}
	}
	return "", route[leg], route[leg+1], nil
}

// merchantMeetable says whether a player at these whereabouts can trade with
// this merchant: both in the same city, or both on the same road leg in
// either direction.
func merchantMeetable(s merchantState, location, from, to string) bool {
	if s.Destination == "" {
		return location != "" && s.Location == location
	}
	if from == "" || to == "" {
		return false
	}
	return (s.Location == from && s.Destination == to) || (s.Location == to && s.Destination == from)
}

func merchantStockRows(conn *storage.Conn, catalog worlddata.Catalog, key string) ([]map[string]any, error) {
	res, err := conn.Execute(`SELECT item_id,quantity,price,cost,acquired_game_minute FROM merchant_stock WHERE merchant=? AND quantity>0 ORDER BY item_id`, []any{key})
	if err != nil {
		return nil, err
	}
	m := catalog.Merchants[key]
	rows := []map[string]any{}
	for _, row := range rowsToMaps(res) {
		itemID := fmt.Sprint(row["item_id"])
		name := catalog.Items[itemID].Name
		if name == "" {
			name = itemID
		}
		// "wares" is the shop's own line; "auction" is a leftover bought
		// off a floor and carried along to resell.
		source := "auction"
		if _, ok := merchantWare(m, itemID); ok {
			source = "wares"
		}
		rows = append(rows, map[string]any{"item_id": itemID, "name": name, "quantity": i64(row["quantity"]), "price": i64(row["price"]), "source": source, "acquired_game_minute": i64(row["acquired_game_minute"])})
	}
	// The shop first, then the floor finds.
	sort.SliceStable(rows, func(i, j int) bool {
		return rows[i]["source"] == "wares" && rows[j]["source"] != "wares"
	})
	return rows, nil
}

func merchantView(catalog worlddata.Catalog, key string, s merchantState, stock []map[string]any, gm int64, meetable bool) map[string]any {
	m := catalog.Merchants[key]
	out := map[string]any{
		"merchant":    key,
		"name":        m.Name,
		"npc":         merchantNPCName(catalog, key),
		"world":       m.World,
		"home":        m.Home,
		"route":       m.Route,
		"location":    s.Location,
		"destination": s.Destination,
		"whereabouts": merchantWhereabouts(s),
		"on_the_road": s.Destination != "",
		"budget":      s.Budget,
		"currency_id": m.Currency,
		"stock":       stock,
		"meetable":    meetable,
	}
	if s.Destination != "" {
		out["arrives_in_minutes"] = max64(0, s.Arrive-gm)
	} else {
		out["departs_in_minutes"] = max64(0, s.DwellUntil-gm)
		if len(m.Route) > 1 {
			out["next_stop"] = m.Route[(s.RouteIndex+1)%int64(len(m.Route))]
		}
	}
	return out
}

// merchantStatusQuery lists every merchant with where it is, what it
// carries and whether this player can reach it right now.
func merchantStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	gm, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	ok, err := merchantTablesExist(conn)
	if err != nil {
		return nil, err
	}
	result := map[string]any{"game_minute": gm, "merchants": []map[string]any{}}
	if !ok {
		return result, nil
	}
	location, from, to, err := actorWhereabouts(conn, userID, gm)
	if err != nil {
		return nil, err
	}
	now := nowSeconds()
	views := []map[string]any{}
	for _, key := range merchantKeys(catalog) {
		state, err := ensureMerchantState(conn, catalog, key, gm, now)
		if err != nil {
			return nil, err
		}
		stock, err := merchantStockRows(conn, catalog, key)
		if err != nil {
			return nil, err
		}
		views = append(views, merchantView(catalog, key, state, stock, gm, merchantMeetable(state, location, from, to)))
	}
	result["merchants"] = views
	result["actor_location"] = location
	if from != "" {
		result["actor_road"] = map[string]any{"from": from, "to": to}
	}
	return result, nil
}

type merchantBuyPayload struct {
	Merchant   string `json:"merchant"`
	ItemID     string `json:"item_id"`
	Quantity   int64  `json:"quantity"`
	GameMinute int64  `json:"game_minute"`
}

// merchantBuyAction sells one line of a merchant's pack to the player who
// can reach it: the wallet is charged the merchant's asking price, the
// items land in the inventory and the merchant's purse grows by the sale.
func merchantBuyAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p merchantBuyPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Merchant = strings.TrimSpace(p.Merchant)
	p.ItemID = strings.TrimSpace(p.ItemID)
	if p.Quantity <= 0 {
		p.Quantity = 1
	}
	m, ok := catalog.Merchants[p.Merchant]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown merchant")
	}
	if p.ItemID == "" {
		return authoritativeMutation{}, errors.New("item_id is required")
	}
	tables, err := merchantTablesExist(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !tables {
		return authoritativeMutation{}, errors.New("merchants are not open in this world yet")
	}
	now := nowSeconds()
	state, err := ensureMerchantState(conn, catalog, p.Merchant, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	location, from, to, err := actorWhereabouts(conn, userID, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !merchantMeetable(state, location, from, to) {
		return authoritativeMutation{}, fmt.Errorf("%s is not within reach: %s", m.Name, merchantWhereabouts(state))
	}
	res, err := conn.Execute(`SELECT quantity,price FROM merchant_stock WHERE merchant=? AND item_id=?`, []any{p.Merchant, p.ItemID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	line := firstRowMap(res)
	if line == nil || i64(line["quantity"]) <= 0 {
		return authoritativeMutation{}, fmt.Errorf("%s does not carry that", m.Name)
	}
	if have := i64(line["quantity"]); have < p.Quantity {
		return authoritativeMutation{}, fmt.Errorf("%s has only %d of those", m.Name, have)
	}
	unit := max64(1, i64(line["price"]))
	total := unit * p.Quantity
	balance, err := walletDeltaTx(conn, userID, m.Currency, -total, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err := addInventoryTx(conn, userID, map[string]int64{p.ItemID: p.Quantity}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE merchant_stock SET quantity=quantity-?,updated_at=? WHERE merchant=? AND item_id=?`, []any{p.Quantity, now, p.Merchant, p.ItemID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`DELETE FROM merchant_stock WHERE merchant=? AND item_id=? AND quantity<=0`, []any{p.Merchant, p.ItemID}); err != nil {
		return authoritativeMutation{}, err
	}
	state.Budget += total
	if err := writeMerchantState(conn, state, now); err != nil {
		return authoritativeMutation{}, err
	}
	name := catalog.Items[p.ItemID].Name
	if name == "" {
		name = p.ItemID
	}
	source := "auction"
	if _, ok := merchantWare(m, p.ItemID); ok {
		source = "wares"
	}
	out := map[string]any{
		"source":        source,
		"merchant":      p.Merchant,
		"merchant_name": m.Name,
		"item_id":       p.ItemID,
		"item_name":     name,
		"quantity":      p.Quantity,
		"unit_price":    unit,
		"total":         total,
		"currency_id":   m.Currency,
		"balance":       balance,
		"whereabouts":   merchantWhereabouts(state),
		"on_the_road":   state.Destination != "",
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "merchant.buy", EntityType: "merchant", EntityID: p.Merchant, GameMinute: p.GameMinute, Payload: out}}, nil
}

// merchantEncountersOnRoute lists the merchants a traveller will pass on a
// road journey: those walking a leg of the same route in either direction,
// and those dwelling at a city the route passes through or ends at. It is
// read-only and tolerant of a world without the merchant tables.
func merchantEncountersOnRoute(conn *storage.Conn, catalog worlddata.Catalog, route []string, gm int64) ([]map[string]any, error) {
	encounters := []map[string]any{}
	if len(route) < 2 || len(catalog.Merchants) == 0 {
		return encounters, nil
	}
	ok, err := merchantTablesExist(conn)
	if err != nil || !ok {
		return encounters, err
	}
	for _, key := range merchantKeys(catalog) {
		state, found, err := readMerchantState(conn, key)
		if err != nil {
			return nil, err
		}
		if !found {
			continue
		}
		where := ""
		if state.Destination != "" {
			for i := 0; i+1 < len(route); i++ {
				if merchantMeetable(state, "", route[i], route[i+1]) {
					where = "road"
					break
				}
			}
		} else {
			for _, stop := range route[1:] {
				if stop == state.Location {
					where = "city"
					break
				}
			}
		}
		if where == "" {
			continue
		}
		stock, err := merchantStockRows(conn, catalog, key)
		if err != nil {
			return nil, err
		}
		view := merchantView(catalog, key, state, stock, gm, true)
		view["met"] = where
		encounters = append(encounters, view)
	}
	return encounters, nil
}
