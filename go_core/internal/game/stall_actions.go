package game

// A cultivator's own market stall in a city's street (v1.5.0).
//
// Player-to-player trade needed both cultivators at one inn at one time
// (`trade.offer`), and the auction floor needs bids and a timer. On a small
// server with players in different time zones neither is "leave it and go".
// A stall is a standing counter: goods laid on it sell while the owner is
// away, to other cultivators and - bounded, see npc_stalls.go - to the world's
// own people.
//
// It is also the reader a homestead facility had been waiting for. The
// `merchant` facility (`cave_abodes.merchant_level`, "Merchant Pavilion") was
// authored in `abode_system.facilities`, buildable and raisable to level 9 for
// a rising stone cost, printed to the narrator - and read by no rule at all.
// `stallSlotsAndFee` is that rule, and the only one: the hall grows the stall.
//
// Six rules, stated once:
//
//  1. A stall is not a shelf. `cheapestShelfPrice` and `highestKeeperBuy` take
//     a catalogue and no connection, so nothing a player asks at a stall can
//     ever enter the band the rank ceiling, the market counter and a
//     merchant's valuation are held inside.
//  2. An NPC buys with its own money, below the shelf, and never the last one
//     (`NPCStallCeiling` here; the rest in the simulation package).
//  3. Merchants never touch a stall: `MerchantsBid` reads `auctions` only.
//  4. One payout. `stallSaleTx` is the only function that turns a listing
//     decrement into seller stones, a fee, a sale row and a prosperity nudge;
//     a player's buy and an NPC's buy both call it (the PayLotSellerTx
//     precedent).
//  5. Priced in the money of the world the stall stands in (rc.44), fixed at
//     `stall.open`; every wallet move goes through `walletDeltaTx`.
//  6. The goods are held in escrow the way an auction lot is: `stall.list`
//     takes them out of the bag, withdraw and close put them back.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// stallRules is `stall_system` with its defaults filled in, so a content file
// that carries no roster still opens a stall at the shipped shape rather than
// at zero slots and no fee.
type stallRules struct {
	MinRealmIndex         int64
	BaseSlots             int64
	SlotsPerMerchantLevel int64
	FeePercent            int64
	FeeDiscountPerLevel   int64
	MinFeePercent         int64
	NPCBuysPerCityPerDay  int64
}

func stallRulesFor(catalog worlddata.Catalog) stallRules {
	s := catalog.StallSystem
	r := stallRules{
		MinRealmIndex:         s.MinRealmIndex,
		BaseSlots:             s.BaseSlots,
		SlotsPerMerchantLevel: s.SlotsPerMerchantLevel,
		FeePercent:            s.FeePercent,
		FeeDiscountPerLevel:   s.FeeDiscountPerLevel,
		MinFeePercent:         s.MinFeePercent,
		NPCBuysPerCityPerDay:  s.NPCBuysPerCityPerDay,
	}
	if r.MinRealmIndex <= 0 {
		r.MinRealmIndex = 2
	}
	if r.BaseSlots <= 0 {
		r.BaseSlots = 2
	}
	if r.SlotsPerMerchantLevel <= 0 {
		r.SlotsPerMerchantLevel = 1
	}
	if r.FeePercent <= 0 {
		r.FeePercent = 10
	}
	if r.FeeDiscountPerLevel <= 0 {
		r.FeeDiscountPerLevel = 1
	}
	if r.MinFeePercent <= 0 {
		r.MinFeePercent = 2
	}
	if r.NPCBuysPerCityPerDay <= 0 {
		r.NPCBuysPerCityPerDay = 3
	}
	return r
}

// ItemTrade is itemTrade for the simulation package: the trade whose recipe
// makes an item, or "" for a raw material.
func ItemTrade(catalog worlddata.Catalog, itemID string) string { return itemTrade(catalog, itemID) }

// StallNPCBuysPerCityPerDay is the simulation's bound on how many purchases
// the town makes at one city's stalls on one tick.
func StallNPCBuysPerCityPerDay(catalog worlddata.Catalog) int64 {
	return stallRulesFor(catalog).NPCBuysPerCityPerDay
}

// stallSlotsAndFee is the one rule that reads the merchant hall: how many
// listings a stall holds and what share of each sale the city takes.
func stallSlotsAndFee(catalog worlddata.Catalog, merchantLevel int64) (slots, feePercent int64) {
	r := stallRulesFor(catalog)
	if merchantLevel < 0 {
		merchantLevel = 0
	}
	slots = r.BaseSlots + r.SlotsPerMerchantLevel*merchantLevel
	feePercent = max64(r.MinFeePercent, r.FeePercent-r.FeeDiscountPerLevel*merchantLevel)
	return slots, feePercent
}

// stallMerchantLevelTx is the homestead's merchant hall, 0 for somebody with
// no property.
func stallMerchantLevelTx(conn *storage.Conn, userID int64) (int64, error) {
	abode, err := abodeByOwnerGo(conn, userID)
	if err != nil {
		return 0, err
	}
	if abode == nil {
		return 0, nil
	}
	return i64(abode["merchant_level"]), nil
}

var stallTables = []string{"player_stalls", "stall_listings", "stall_sales"}

// stallTablesExist guards every reader: in the compose stack the engine is
// healthy before db-init migrates, and a request in that window is answered
// "not open yet" rather than with a SQL error.
func stallTablesExist(conn *storage.Conn) bool {
	for _, table := range stallTables {
		if !tableExistsTx(conn, table) {
			return false
		}
	}
	return true
}

var errStallsNotOpen = errors.New("stalls are not open in this world yet")

// stallPrivatePrefixes are the places that are somebody's own rather than the
// world's; a stall is set up in a city's street and never inside one of these.
var stallPrivatePrefixes = []string{"birth_family:", "sect_abode:", "abode:", "personal_world:"}

// stallCityAt is the city a stall stands in for somebody standing at
// `location`: the city itself, or its gate, district, shop or auction floor
// (`cityOf`), and only a place with shops in it counts as a city - the rule
// `shopHereQuery` already states with `is_city`.
func stallCityAt(catalog worlddata.Catalog, location string) (string, bool) {
	for _, prefix := range stallPrivatePrefixes {
		if strings.HasPrefix(location, prefix) {
			return "", false
		}
	}
	city := cityOf(catalog, location)
	if len(cityShopKeys(catalog, city)) == 0 {
		return "", false
	}
	return city, true
}

// NPCStallCeiling is the most one of the world's own people will pay at a
// stall: one coin under the cheapest shelf price for that item in that coin.
// No shelf means no reference, and no reference means no mint guard, so an
// item no shop sells is never bought by an NPC.
func NPCStallCeiling(catalog worlddata.Catalog, itemID, currency string) (int64, bool) {
	shelf, ok := cheapestShelfPrice(catalog, itemID, currency)
	if !ok || shelf <= 1 {
		return 0, false
	}
	return shelf - 1, true
}

func stallByOwnerTx(conn *storage.Conn, userID int64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT user_id,city,name,currency_id,opened_game_minute FROM player_stalls WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	return firstRowMap(res), nil
}

func stallListingTx(conn *storage.Conn, listingID int64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT listing_id,user_id,city,item_id,quantity,unit_price,currency_id,listed_game_minute FROM stall_listings WHERE listing_id=?`, []any{listingID})
	if err != nil {
		return nil, err
	}
	return firstRowMap(res), nil
}

func stallListingRows(conn *storage.Conn, catalog worlddata.Catalog, userID int64) ([]map[string]any, error) {
	res, err := conn.Execute(`SELECT listing_id,item_id,quantity,unit_price,currency_id,listed_game_minute FROM stall_listings WHERE user_id=? ORDER BY listing_id`, []any{userID})
	if err != nil {
		return nil, err
	}
	rows := []map[string]any{}
	for _, row := range rowsToMaps(res) {
		itemID := fmt.Sprint(row["item_id"])
		currency := fmt.Sprint(row["currency_id"])
		ceiling, _ := NPCStallCeiling(catalog, itemID, currency)
		rows = append(rows, map[string]any{
			"listing_id": i64(row["listing_id"]), "item_id": itemID, "name": itemDisplayName(catalog, itemID),
			"quantity": i64(row["quantity"]), "unit_price": i64(row["unit_price"]), "currency_id": currency,
			"listed_game_minute": i64(row["listed_game_minute"]), "npc_ceiling": ceiling,
		})
	}
	return rows, nil
}

type stallOpenPayload struct {
	Name       string `json:"name"`
	GameMinute int64  `json:"game_minute"`
}

func stallOpenAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p stallOpenPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if !stallTablesExist(conn) {
		return authoritativeMutation{}, errStallsNotOpen
	}
	p.Name = strings.TrimSpace(p.Name)
	if p.Name == "" {
		return authoritativeMutation{}, errors.New("a stall needs a name")
	}
	if len([]rune(p.Name)) > 40 {
		return authoritativeMutation{}, errors.New("a stall's name is at most 40 characters")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	rules := stallRulesFor(catalog)
	if c.accessRealmIndex() < rules.MinRealmIndex {
		return authoritativeMutation{}, fmt.Errorf("a stall asks for %s; you stand at %s",
			realmNameGo(catalog, rules.MinRealmIndex), realmNameGo(catalog, c.accessRealmIndex()))
	}
	city, ok := stallCityAt(catalog, c.Location)
	if !ok {
		return authoritativeMutation{}, errors.New("a stall is set up in a city's street; travel to one")
	}
	existing, err := stallByOwnerTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if existing != nil {
		return authoritativeMutation{}, fmt.Errorf("you already keep a stall in %s; close it before opening another", existing["city"])
	}
	currency := worldBaseCurrency(catalog, catalog.Locations[city].World)
	now := nowSeconds()
	if _, err := conn.Execute(`INSERT INTO player_stalls(user_id,city,name,currency_id,opened_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?)`,
		[]any{userID, city, p.Name, currency, p.GameMinute, now, now}); err != nil {
		return authoritativeMutation{}, err
	}
	level, err := stallMerchantLevelTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	slots, fee := stallSlotsAndFee(catalog, level)
	out := map[string]any{"city": city, "name": p.Name, "currency_id": currency, "slots": slots, "fee_percent": fee, "merchant_level": level}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "stall.open", EntityType: "stall", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// stallOwnerHereTx is the stall of the caller, refused unless they stand in
// the city it is kept in: goods are laid on it and taken off it in person.
func stallOwnerHereTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, mechanicsCharacter, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, c, err
	}
	stall, err := stallByOwnerTx(conn, userID)
	if err != nil {
		return nil, c, err
	}
	if stall == nil {
		return nil, c, errors.New("you keep no stall yet; open one in a city's street")
	}
	city, ok := stallCityAt(catalog, c.Location)
	if !ok || city != fmt.Sprint(stall["city"]) {
		return nil, c, fmt.Errorf("your stall stands in %s; travel there to tend it", stall["city"])
	}
	return stall, c, nil
}

type stallListPayload struct {
	ItemID     string `json:"item_id"`
	Quantity   int64  `json:"quantity"`
	UnitPrice  int64  `json:"unit_price"`
	GameMinute int64  `json:"game_minute"`
}

func stallListAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p stallListPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if !stallTablesExist(conn) {
		return authoritativeMutation{}, errStallsNotOpen
	}
	p.ItemID = strings.TrimSpace(p.ItemID)
	if p.ItemID == "" {
		return authoritativeMutation{}, errors.New("item_id is required")
	}
	p.Quantity = clamp(p.Quantity, 1, 99)
	if p.UnitPrice < 1 {
		return authoritativeMutation{}, errors.New("a listing asks at least one coin")
	}
	stall, _, err := stallOwnerHereTx(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	level, err := stallMerchantLevelTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	slots, _ := stallSlotsAndFee(catalog, level)
	res, err := conn.Execute(`SELECT COUNT(*) AS n FROM stall_listings WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row := firstRowMap(res); row != nil && i64(row["n"]) >= slots {
		return authoritativeMutation{}, fmt.Errorf("your stall holds %d listings; raise the merchant hall of your homestead for more, or withdraw one", slots)
	}
	dup, err := conn.Execute(`SELECT listing_id FROM stall_listings WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if firstRowMap(dup) != nil {
		return authoritativeMutation{}, fmt.Errorf("%s is already on your stall; withdraw that listing first", itemDisplayName(catalog, p.ItemID))
	}
	missing, err := consumeInventoryTx(conn, userID, map[string]int64{p.ItemID: p.Quantity})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(missing) > 0 {
		return authoritativeMutation{}, fmt.Errorf("you do not carry %d %s", p.Quantity, itemDisplayName(catalog, p.ItemID))
	}
	currency := fmt.Sprint(stall["currency_id"])
	city := fmt.Sprint(stall["city"])
	now := nowSeconds()
	ins, err := conn.Execute(`INSERT INTO stall_listings(user_id,city,item_id,quantity,unit_price,currency_id,listed_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)`,
		[]any{userID, city, p.ItemID, p.Quantity, p.UnitPrice, currency, p.GameMinute, now, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	ceiling, hasShelf := NPCStallCeiling(catalog, p.ItemID, currency)
	out := map[string]any{
		"listing_id": ins.LastInsertID, "city": city, "item_id": p.ItemID, "item_name": itemDisplayName(catalog, p.ItemID),
		"quantity": p.Quantity, "unit_price": p.UnitPrice, "currency_id": currency,
		"npc_ceiling": ceiling, "npc_may_buy": hasShelf && p.UnitPrice <= ceiling, "slots": slots,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "stall.list", EntityType: "stall", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

type stallListingPayload struct {
	ListingID  int64 `json:"listing_id"`
	Quantity   int64 `json:"quantity"`
	GameMinute int64 `json:"game_minute"`
}

func stallWithdrawAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p stallListingPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if !stallTablesExist(conn) {
		return authoritativeMutation{}, errStallsNotOpen
	}
	if _, _, err := stallOwnerHereTx(conn, catalog, userID); err != nil {
		return authoritativeMutation{}, err
	}
	listing, err := stallListingTx(conn, p.ListingID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if listing == nil || i64(listing["user_id"]) != userID {
		return authoritativeMutation{}, errors.New("that listing is not on your stall")
	}
	itemID := fmt.Sprint(listing["item_id"])
	qty := i64(listing["quantity"])
	if err := addInventoryTx(conn, userID, map[string]int64{itemID: qty}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`DELETE FROM stall_listings WHERE listing_id=?`, []any{p.ListingID}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"listing_id": p.ListingID, "item_id": itemID, "item_name": itemDisplayName(catalog, itemID), "quantity": qty}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "stall.withdraw", EntityType: "stall", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func stallBuyAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p stallListingPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if !stallTablesExist(conn) {
		return authoritativeMutation{}, errStallsNotOpen
	}
	p.Quantity = clamp(p.Quantity, 1, 99)
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	listing, err := stallListingTx(conn, p.ListingID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if listing == nil {
		return authoritativeMutation{}, errors.New("that has been sold, or taken off the stall")
	}
	sellerID := i64(listing["user_id"])
	if sellerID == userID {
		return authoritativeMutation{}, errors.New("you cannot buy from your own stall")
	}
	city := fmt.Sprint(listing["city"])
	if here, ok := stallCityAt(catalog, c.Location); !ok || here != city {
		return authoritativeMutation{}, fmt.Errorf("that stall stands in %s; travel there to buy from it", city)
	}
	if have := i64(listing["quantity"]); have < p.Quantity {
		return authoritativeMutation{}, fmt.Errorf("only %d left on that stall", have)
	}
	currency := fmt.Sprint(listing["currency_id"])
	unit := i64(listing["unit_price"])
	total := unit * p.Quantity
	now := nowSeconds()
	balance, err := walletDeltaTx(conn, catalog, userID, currency, -total, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	itemID := fmt.Sprint(listing["item_id"])
	if err := addInventoryTx(conn, userID, map[string]int64{itemID: p.Quantity}); err != nil {
		return authoritativeMutation{}, err
	}
	buyer := userID
	paid, fee, err := stallSaleTx(conn, catalog, listing, p.Quantity, &buyer, "", p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	stall, err := stallByOwnerTx(conn, sellerID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	stallName := ""
	if stall != nil {
		stallName = fmt.Sprint(stall["name"])
	}
	out := map[string]any{
		"listing_id": p.ListingID, "seller_user_id": sellerID, "stall_name": stallName, "city": city,
		"item_id": itemID, "item_name": itemDisplayName(catalog, itemID), "quantity": p.Quantity,
		"unit_price": unit, "total": total, "currency_id": currency, "balance": balance,
		"seller_paid": paid, "fee": fee,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "stall.buy", EntityType: "stall", EntityID: fmt.Sprint(sellerID), GameMinute: p.GameMinute, Payload: out}}, nil
}

type stallClosePayload struct {
	GameMinute int64 `json:"game_minute"`
}

func stallCloseAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p stallClosePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if !stallTablesExist(conn) {
		return authoritativeMutation{}, errStallsNotOpen
	}
	stall, _, err := stallOwnerHereTx(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	rows, err := stallListingRows(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	returned := map[string]int64{}
	for _, row := range rows {
		returned[fmt.Sprint(row["item_id"])] += i64(row["quantity"])
	}
	if len(returned) > 0 {
		if err := addInventoryTx(conn, userID, returned); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if _, err := conn.Execute(`DELETE FROM stall_listings WHERE user_id=?`, []any{userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`DELETE FROM player_stalls WHERE user_id=?`, []any{userID}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"city": stall["city"], "name": stall["name"], "returned": returned, "listings_returned": len(rows)}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "stall.close", EntityType: "stall", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// stallSaleTx is the one payout (rule 4). It takes `quantity` off the listing
// (deleting it at zero), takes the city's cut off the total, pays the seller
// the rest in the listing's own currency, writes the seller's ledger row and
// moves the city's prosperity the point every other sale moves. The cut rounds
// down, so a one-coin sale is the seller's whole. It never debits anybody: the
// buyer's side - a cultivator's purse or an NPC's wealth - is the caller's.
func stallSaleTx(conn *storage.Conn, catalog worlddata.Catalog, listing map[string]any, quantity int64, buyerUserID *int64, buyerNPC string, gm int64, now float64) (paid, fee int64, err error) {
	listingID := i64(listing["listing_id"])
	sellerID := i64(listing["user_id"])
	have := i64(listing["quantity"])
	if quantity < 1 || quantity > have {
		return 0, 0, fmt.Errorf("listing %d holds %d, not %d", listingID, have, quantity)
	}
	currency := fmt.Sprint(listing["currency_id"])
	unit := i64(listing["unit_price"])
	total := unit * quantity
	level, err := stallMerchantLevelTx(conn, sellerID)
	if err != nil {
		return 0, 0, err
	}
	_, feePercent := stallSlotsAndFee(catalog, level)
	fee = total * feePercent / 100
	paid = total - fee
	if have == quantity {
		if _, err = conn.Execute(`DELETE FROM stall_listings WHERE listing_id=?`, []any{listingID}); err != nil {
			return 0, 0, err
		}
	} else {
		if _, err = conn.Execute(`UPDATE stall_listings SET quantity=quantity-?,updated_at=? WHERE listing_id=?`, []any{quantity, now, listingID}); err != nil {
			return 0, 0, err
		}
	}
	if _, err = walletDeltaTx(conn, catalog, sellerID, currency, paid, now); err != nil {
		return 0, 0, err
	}
	var buyer any
	if buyerUserID != nil {
		buyer = *buyerUserID
	}
	if _, err = conn.Execute(`INSERT INTO stall_sales(user_id,item_id,quantity,unit_price,currency_id,fee,buyer_user_id,buyer_npc_name,sold_game_minute,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)`,
		[]any{sellerID, fmt.Sprint(listing["item_id"]), quantity, unit, currency, fee, buyer, buyerNPC, gm, now}); err != nil {
		return 0, 0, err
	}
	if err = nudgeCityProsperityTx(conn, catalog, fmt.Sprint(listing["city"]), 1); err != nil {
		return 0, 0, err
	}
	return paid, fee, nil
}

// StallSaleTx is stallSaleTx for the simulation package, which must not keep
// a copy of the payout (the WalletDeltaTx precedent, rc.43).
func StallSaleTx(conn *storage.Conn, catalog worlddata.Catalog, listing map[string]any, quantity int64, buyerUserID *int64, buyerNPC string, gm int64, now float64) (paid, fee int64, err error) {
	return stallSaleTx(conn, catalog, listing, quantity, buyerUserID, buyerNPC, gm, now)
}

// stallBoardQuery is every stall in the city the caller stands in, with its
// listings. It never refuses: somewhere with no stalls is an empty board.
func stallBoardQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	city, isCity := stallCityAt(catalog, c.Location)
	out := map[string]any{"city": city, "is_city": isCity, "available": stallTablesExist(conn), "stalls": []map[string]any{}}
	if !isCity || !stallTablesExist(conn) {
		return out, nil
	}
	out["currency_id"] = worldBaseCurrency(catalog, catalog.Locations[city].World)
	res, err := conn.Execute(`SELECT s.user_id,s.name,COALESCE(c.name,'') AS owner_name FROM player_stalls s LEFT JOIN characters c ON c.user_id=s.user_id WHERE s.city=? ORDER BY s.name,s.user_id`, []any{city})
	if err != nil {
		return nil, err
	}
	stalls := []map[string]any{}
	for _, row := range rowsToMaps(res) {
		owner := i64(row["user_id"])
		listings, err := stallListingRows(conn, catalog, owner)
		if err != nil {
			return nil, err
		}
		stalls = append(stalls, map[string]any{
			"owner_user_id": owner, "owner_name": fmt.Sprint(row["owner_name"]), "name": fmt.Sprint(row["name"]),
			"mine": owner == userID, "listings": listings,
		})
	}
	out["stalls"] = stalls
	return out, nil
}

// stallStatusQuery is the caller's own stall: its shape, its listings and the
// last ten sales. It never refuses either; no stall is `stall: null`.
func stallStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	rules := stallRulesFor(catalog)
	out := map[string]any{"available": stallTablesExist(conn), "stall": nil, "min_realm_index": rules.MinRealmIndex,
		"realm_index": c.accessRealmIndex(), "listings": []map[string]any{}, "recent_sales": []map[string]any{}}
	here, isCity := stallCityAt(catalog, c.Location)
	out["here_city"] = here
	out["here_is_city"] = isCity
	if !stallTablesExist(conn) {
		return out, nil
	}
	level, err := stallMerchantLevelTx(conn, userID)
	if err != nil {
		return nil, err
	}
	slots, fee := stallSlotsAndFee(catalog, level)
	out["merchant_level"] = level
	out["slots_total"] = slots
	out["fee_percent"] = fee
	stall, err := stallByOwnerTx(conn, userID)
	if err != nil {
		return nil, err
	}
	if stall != nil {
		out["stall"] = map[string]any{"city": fmt.Sprint(stall["city"]), "name": fmt.Sprint(stall["name"]),
			"currency_id": fmt.Sprint(stall["currency_id"]), "opened_game_minute": i64(stall["opened_game_minute"])}
		listings, err := stallListingRows(conn, catalog, userID)
		if err != nil {
			return nil, err
		}
		out["listings"] = listings
		out["slots_used"] = len(listings)
	}
	res, err := conn.Execute(`SELECT item_id,quantity,unit_price,currency_id,fee,buyer_user_id,buyer_npc_name,sold_game_minute FROM stall_sales WHERE user_id=? ORDER BY sale_id DESC LIMIT 10`, []any{userID})
	if err != nil {
		return nil, err
	}
	sales := []map[string]any{}
	for _, row := range rowsToMaps(res) {
		itemID := fmt.Sprint(row["item_id"])
		sale := map[string]any{
			"item_id": itemID, "name": itemDisplayName(catalog, itemID), "quantity": i64(row["quantity"]),
			"unit_price": i64(row["unit_price"]), "currency_id": fmt.Sprint(row["currency_id"]), "fee": i64(row["fee"]),
			"buyer_npc_name": fmt.Sprint(row["buyer_npc_name"]), "sold_game_minute": i64(row["sold_game_minute"]),
			"buyer_is_npc": strings.TrimSpace(fmt.Sprint(row["buyer_npc_name"])) != "",
		}
		sales = append(sales, sale)
	}
	out["recent_sales"] = sales
	return out, nil
}
