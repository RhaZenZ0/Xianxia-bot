package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type auctionSellPayload struct {
	HouseID     string  `json:"house_id"`
	ItemID      string  `json:"item_id"`
	Quantity    int64   `json:"quantity"`
	CurrencyID  string  `json:"currency_id"`
	StartingBid int64   `json:"starting_bid"`
	Anonymous   bool    `json:"anonymous"`
	EndsAt      float64 `json:"ends_at"`
	GameMinute  int64   `json:"game_minute"`
}

type auctionBidPayload struct {
	AuctionID  int64 `json:"auction_id"`
	Amount     int64 `json:"amount"`
	GameMinute int64 `json:"game_minute"`
}

type locationMinutePayload struct {
	GameMinute int64 `json:"game_minute"`
}

type marketTradePayload struct {
	Location   string `json:"location"`
	ItemID     string `json:"item_id"`
	Quantity   int64  `json:"quantity"`
	Buy        bool   `json:"buy"`
	GameMinute int64  `json:"game_minute"`
}

type hunterActionPayload struct {
	PursuitID  int64  `json:"pursuit_id"`
	Action     string `json:"action"`
	GameMinute int64  `json:"game_minute"`
}

func characterLocationPower(conn *storage.Conn, userID int64) (map[string]any, error) {
	r, err := conn.Execute(`SELECT user_id,name,realm_index,phase,vitality,vitality_max,location,karma_score,attributes_json,path FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(r)
	if row == nil {
		return nil, errors.New("living character not found")
	}
	return row, nil
}

func catalogHouseAt(catalog worlddata.Catalog, location string) (string, worlddata.AuctionHouse, bool) {
	for key, house := range catalog.AuctionHouses {
		if house.Location == location {
			return key, house, true
		}
	}
	return "", worlddata.AuctionHouse{}, false
}

func auctionEnterAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p struct {
		HouseID    string `json:"house_id"`
		GameMinute int64  `json:"game_minute"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := characterLocationPower(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	var key string
	var house worlddata.AuctionHouse
	var ok bool
	if strings.TrimSpace(p.HouseID) != "" {
		house, ok = catalog.AuctionHouses[p.HouseID]
		key = p.HouseID
	}
	// The hall's door is on the street, reached from anywhere in the city
	// (v0.36.0: a gate or district too) except from inside a shop.
	here := cityOf(catalog, fmt.Sprint(c["location"]))
	if catalog.Locations[fmt.Sprint(c["location"])].Shop != "" {
		here = ""
	}
	if !ok {
		for k, h := range catalog.AuctionHouses {
			if h.EntranceLocation == here {
				key, house, ok = k, h, true
				break
			}
		}
	}
	if !ok || house.EntranceLocation != here {
		return authoritativeMutation{}, errors.New("no recognized auction-house entrance at current location")
	}
	now := nowSeconds()
	if _, err = moveCharacterTx(conn, catalog, userID, house.Location, now); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"house_id": key, "name": house.Name, "location": house.Location, "outside": house.EntranceLocation}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "auction.enter", EntityType: "auction_house", EntityID: key, GameMinute: p.GameMinute, Payload: out}}, nil
}

var auctionPursuerNames = []string{"Black Veil Enforcer", "Silent Fang Cultivator", "Rival Young Master's Guard", "Shadow Market Hunter", "Masked Treasure Seeker"}

// auctionDoorstep is where an auction-door ambush stands: on the house's own
// doorstep, where its protection has already ended. The one statement of it,
// read by the leave result and by the narrator through the battle's source.
func auctionDoorstep(house string) string {
	return "on the doorstep of " + house + ", where its protection ends"
}

func auctionLeaveAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p locationMinutePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := characterLocationPower(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	houseID, house, ok := catalogHouseAt(catalog, fmt.Sprint(c["location"]))
	if !ok {
		return authoritativeMutation{}, errors.New("character is not inside a registered auction house")
	}
	now := nowSeconds()
	if _, err = moveCharacterTx(conn, catalog, userID, house.EntranceLocation, now); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"house_id": houseID, "name": house.Name, "outside": house.EntranceLocation, "incident": nil}
	risks, err := conn.Execute(`SELECT * FROM auction_door_risks WHERE user_id=? AND consumed_at IS NULL AND created_at>=? ORDER BY chance_percent DESC,created_at DESC LIMIT 1`, []any{userID, now - 86400})
	if err != nil {
		return authoritativeMutation{}, err
	}
	risk := firstRowMap(risks)
	if risk != nil {
		_, err = conn.Execute(`UPDATE auction_door_risks SET consumed_at=? WHERE risk_id=? AND consumed_at IS NULL`, []any{now, i64(risk["risk_id"])})
		if err != nil {
			return authoritativeMutation{}, err
		}
		chance := clamp(i64(risk["chance_percent"]), 0, 100)
		roll, e := gamerng.Intn(100)
		if e != nil {
			return authoritativeMutation{}, e
		}
		// Where it happens is the house's own step (v1.3.3): every house says
		// its protection ends at the front doors, and this is the one fight
		// the safe-zone rule lets through on the street outside - so the
		// result says so, and the narrator is told the same rather than that
		// violence cannot begin here.
		incident := map[string]any{"risk_id": i64(risk["risk_id"]), "auction_id": i64(risk["auction_id"]), "item_id": fmt.Sprint(risk["item_id"]), "triggered": int64(roll) < chance,
			"where": auctionDoorstep(house.Name)}
		if int64(roll) < chance {
			itemID := fmt.Sprint(risk["item_id"])
			item, _, _ := itemDef(catalog, itemID)
			baseRealm, baseStage := i64(c["realm_index"]), i64(c["phase"])
			driftChoices := []int64{-1, 0, 0, 1}
			if strings.EqualFold(item.AuctionInterest, "legendary") {
				driftChoices = []int64{0, 1, 1, 2}
			}
			di, e := gamerng.Intn(len(driftChoices))
			if e != nil {
				return authoritativeMutation{}, e
			}
			realm := clamp(baseRealm+item.HunterRealmBonus+driftChoices[di], 0, int64(len(catalog.Realms)-1))
			stage := int64(1)
			if realm == baseRealm {
				choices := []int64{-2, -1, 1, 2, 3}
				si, e := gamerng.Intn(len(choices))
				if e != nil {
					return authoritativeMutation{}, e
				}
				stage = clamp(baseStage+choices[si], 1, 9)
			} else {
				si, e := gamerng.Intn(9)
				if e != nil {
					return authoritativeMutation{}, e
				}
				stage = int64(si + 1)
			}
			ni, e := gamerng.Intn(len(auctionPursuerNames))
			if e != nil {
				return authoritativeMutation{}, e
			}
			npc := auctionPursuerNames[ni]
			stronger := realm > baseRealm || (realm == baseRealm && stage > baseStage)
			incident["hunter_name"], incident["hunter_realm_index"], incident["hunter_stage"], incident["stronger"] = npc, realm, stage, stronger
			if stronger {
				source := fmt.Sprintf("auction:%d:%s", i64(risk["auction_id"]), itemID)
				conflict, e := conn.Execute(`SELECT battle_id FROM battles WHERE target_key=? AND status='active' AND user_id<>? LIMIT 1`, []any{source, userID})
				if e != nil {
					return authoritativeMutation{}, e
				}
				if firstRowMap(conflict) == nil {
					_, e = conn.Execute(`UPDATE battles SET status='abandoned',version=version+1,updated_at=? WHERE user_id=? AND status='active'`, []any{now, userID})
					if e != nil {
						return authoritativeMutation{}, e
					}
					playerHP := max64(1, i64(c["vitality"]))
					playerMax := max64(playerHP, i64(c["vitality_max"]))
					npcHP := max64(10, 12+realm*3+stage)
					ins, e := conn.Execute(`INSERT INTO battles(user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,status,location,source,target_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'active',?,?,?,?,?)`, []any{userID, npc, realm, stage, playerHP, playerMax, npcHP, npcHP, house.EntranceLocation, source, source, now, now})
					if e != nil {
						return authoritativeMutation{}, e
					}
					incident["battle_id"] = ins.LastInsertID
				} else {
					incident["battle_conflict"] = true
				}
			}
		}
		out["incident"] = incident
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "auction.leave", EntityType: "auction_house", EntityID: houseID, GameMinute: p.GameMinute, Payload: out}}, nil
}

// auctionMinLotMinutes is the shortest a lot may run. The bot's duration
// picker floors at the same number (`/auction sell`'s Range), and a test reads
// both: a bound the client states and the engine does not is not a bound. A
// lot shorter than a merchant's snipe guard (merchantBidMinimumSecondsLeft) is
// a lot nobody can answer, and the engine took one-second lots. The slack is
// the time a request spends on the road: the picker's end time is its own
// clock plus the minutes, and arrives a moment short of them.
const (
	auctionMinLotMinutes          = 5
	auctionLotArrivalSlackSeconds = 30.0
)

func auctionSellAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p auctionSellPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := characterLocationPower(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	houseID, house, ok := catalogHouseAt(catalog, fmt.Sprint(c["location"]))
	if !ok {
		return authoritativeMutation{}, errors.New("must be inside an auction house")
	}
	if p.HouseID != "" && p.HouseID != houseID {
		return authoritativeMutation{}, errors.New("auction house mismatch")
	}
	// A smaller city has a smaller house (v0.33.1): the floor holds only so
	// many lots at once, and none for longer than the house's limit. Both
	// come from content; a house without them is uncapped.
	if house.MaxActiveLots > 0 {
		open, err := conn.Execute(`SELECT COUNT(*) AS n FROM auctions WHERE house_id=? AND active=1`, []any{houseID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if row := firstRowMap(open); row != nil && i64(row["n"]) >= house.MaxActiveLots {
			return authoritativeMutation{}, fmt.Errorf("%s holds at most %d lots at once and its floor is full; wait for a lot to close or list at a larger house", house.Name, house.MaxActiveLots)
		}
	}
	if house.MaxLotMinutes > 0 && p.EndsAt > nowSeconds()+float64(house.MaxLotMinutes)*60+1 {
		return authoritativeMutation{}, fmt.Errorf("%s runs a lot for at most %d minutes; a longer sale belongs at a capital's house", house.Name, house.MaxLotMinutes)
	}
	p.Quantity = clamp(p.Quantity, 1, 999999)
	p.StartingBid = max64(1, p.StartingBid)
	p.ItemID = strings.TrimSpace(p.ItemID)
	p.CurrencyID = strings.TrimSpace(p.CurrencyID)
	if p.ItemID == "" || p.CurrencyID == "" {
		return authoritativeMutation{}, errors.New("item_id and currency_id are required")
	}
	if _, ok := catalog.Currencies[p.CurrencyID]; !ok {
		return authoritativeMutation{}, errors.New("unknown auction currency")
	}
	// A floor deals in the money of the world it stands in (v1.2.3 made the
	// bot send it): the currency is the seller's to name only in the sense that
	// the engine names it back. A lot asked in another world's coin is a lot
	// whose starting bid is a hundred times or a hundredth of what its seller
	// thinks, and the unsold-lot buyer paid it out of a purse in that coin.
	if want := worldBaseCurrency(catalog, catalog.Locations[house.Location].World); p.CurrencyID != want {
		return authoritativeMutation{}, fmt.Errorf("%s deals in %s; a lot listed there is priced in it", house.Name, coinName(catalog, want))
	}
	qty, err := inventoryQuantityTx(conn, userID, p.ItemID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if qty < p.Quantity {
		return authoritativeMutation{}, errors.New("not enough of that item to list")
	}
	if p.EndsAt <= nowSeconds() {
		return authoritativeMutation{}, errors.New("auction end time must be in the future")
	}
	if p.EndsAt < nowSeconds()+float64(auctionMinLotMinutes)*60-auctionLotArrivalSlackSeconds {
		return authoritativeMutation{}, fmt.Errorf("a lot runs for at least %d minutes", auctionMinLotMinutes)
	}
	now := nowSeconds()
	_, err = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	ins, err := conn.Execute(`INSERT INTO auctions(house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,anonymous,active,created_at,ends_at) VALUES(?,?,?,?,?,?,0,?,1,?,?)`, []any{houseID, userID, p.ItemID, p.Quantity, p.CurrencyID, p.StartingBid, p.Anonymous, now, p.EndsAt})
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"auction_id": ins.LastInsertID, "house_id": houseID, "item_id": p.ItemID, "quantity": p.Quantity, "currency_id": p.CurrencyID, "starting_bid": p.StartingBid, "anonymous": p.Anonymous, "ends_at": p.EndsAt}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "auction.sell", EntityType: "auction", EntityID: fmt.Sprint(ins.LastInsertID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func auctionBidAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p auctionBidPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.AuctionID <= 0 || p.Amount <= 0 {
		return authoritativeMutation{}, errors.New("auction_id and amount must be positive")
	}
	c, err := characterLocationPower(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	houseID, _, ok := catalogHouseAt(catalog, fmt.Sprint(c["location"]))
	if !ok {
		return authoritativeMutation{}, errors.New("must be inside an auction house")
	}
	r, err := conn.Execute(`SELECT * FROM auctions WHERE auction_id=?`, []any{p.AuctionID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	a := firstRowMap(r)
	if a == nil {
		return authoritativeMutation{}, errors.New("auction not found")
	}
	now := nowSeconds()
	if i64(a["active"]) == 0 || parseFloat(a["ends_at"]) <= now {
		return authoritativeMutation{}, errors.New("that auction has ended")
	}
	if fmt.Sprint(a["house_id"]) != houseID {
		return authoritativeMutation{}, errors.New("auction belongs to a different house")
	}
	if i64(a["seller_user_id"]) == userID {
		return authoritativeMutation{}, errors.New("cannot bid on own lot")
	}
	minimum := max64(i64(a["starting_bid"]), i64(a["current_bid"])+1)
	if p.Amount < minimum {
		return authoritativeMutation{}, fmt.Errorf("minimum bid is %d", minimum)
	}
	if old := i64(a["current_bidder_user_id"]); old > 0 {
		if _, err = walletDeltaTx(conn, catalog, old, fmt.Sprint(a["currency_id"]), i64(a["current_bid"]), now); err != nil {
			return authoritativeMutation{}, err
		}
	} else if err := refundMerchantBidderTx(conn, catalog, a, p.GameMinute, now); err != nil {
		// A merchant held the lot (v0.37.0): its purse gets the bid back.
		return authoritativeMutation{}, err
	}
	bal, err := walletDeltaTx(conn, catalog, userID, fmt.Sprint(a["currency_id"]), -p.Amount, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	_, err = conn.Execute(`UPDATE auctions SET current_bid=?,current_bidder_user_id=? WHERE auction_id=?`, []any{p.Amount, userID, p.AuctionID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	_, err = conn.Execute(`INSERT INTO auction_bids(auction_id,bidder_user_id,amount,created_at) VALUES(?,?,?,?)`, []any{p.AuctionID, userID, p.Amount, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"auction_id": p.AuctionID, "current_bid": p.Amount, "current_bidder_user_id": userID, "currency_id": fmt.Sprint(a["currency_id"]), "balance": bal, "item_id": fmt.Sprint(a["item_id"])}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "auction.bid", EntityType: "auction", EntityID: fmt.Sprint(p.AuctionID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// blackMarketTrustReputation is the Underworld Contacts standing at which a
// broker sells to a cultivator (the Python twin is BLACK_MARKET_ACCESS_REPUTATION).
const blackMarketTrustReputation = int64(15)

func blackMarketAuthorized(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c map[string]any) (string, int64, error) {
	karma := i64(c["karma_score"])
	rep := int64(0)
	r, err := conn.Execute(`SELECT score FROM faction_reputation WHERE user_id=? AND LOWER(faction_key)=LOWER('Underworld Contacts')`, []any{userID})
	if err != nil {
		return "", 0, err
	}
	if row := firstRowMap(r); row != nil {
		rep = i64(row["score"])
	}
	if karma <= -40 {
		return "dark karma", rep, nil
	}
	if rep >= blackMarketTrustReputation {
		return "underworld contacts", rep, nil
	}
	m, err := conn.Execute(`SELECT sect_name FROM sect_membership WHERE user_id=?`, []any{userID})
	if err != nil {
		return "", rep, err
	}
	if row := firstRowMap(m); row != nil {
		if def, ok := catalog.Sects[fmt.Sprint(row["sect_name"])]; ok && strings.EqualFold(def.Alignment, "Demonic") {
			return "demonic sect", rep, nil
		}
	}
	// Two more doors a broker knows (v1.28.0): an initiate of the hidden sect,
	// whose membership is not `sect_membership` and so was never asked, and a
	// cultivator the Demonic Circles already count as one of theirs.
	if tableExistsTx(conn, "hidden_sect_membership") {
		h, err := conn.Execute(`SELECT 1 FROM hidden_sect_membership WHERE user_id=? AND status='active' LIMIT 1`, []any{userID})
		if err == nil && len(h.Rows) > 0 {
			return "hidden sect", rep, nil
		}
	}
	if standingTx(conn, userID, "Demonic Circles") >= blackMarketTrustReputation {
		return "demonic circles", rep, nil
	}
	return "", rep, nil
}

// blackMarketStockShare is what a broker pays for a good the post itself
// stocks, against the post's own asking price (v0.3x's 0.55); the pay is then
// held under the cheapest shelf by blackMarketStockPay.
const blackMarketStockShare = 0.55

// UnderworldGood is the one statement of what a post stocks and a finder
// smuggles (v1.36.0): the contraband legal statuses, a dark manual, and
// anything the auction floors call special or legendary. The rotation
// (simulation.blackMarkets) and the arbitrage gate read the same predicate.
func UnderworldGood(item worlddata.Item) bool {
	legal := strings.ToLower(strings.TrimSpace(item.LegalStatus))
	interest := strings.ToLower(strings.TrimSpace(item.AuctionInterest))
	typ := strings.ToLower(strings.TrimSpace(item.Type))
	name := strings.ToLower(item.Name)
	if legal == "forbidden" || legal == "contraband" || legal == "restricted" {
		return true
	}
	if typ == "manual" {
		for _, word := range []string{"demon", "blood", "soul", "nether", "bone", "venom"} {
			if strings.Contains(name, word) {
				return true
			}
		}
	}
	return interest == "special" || interest == "legendary"
}

// UnderworldBase is the price a post's arithmetic starts from, in the post's
// own coin: the item's base price (or eight times its sect value for an item
// that has none), which content authors in the Mortal stone, carried along the
// currency ladder into `currency` the way a shelf price is (shelfPriceAcross).
// An item worth less than one whole coin up there has no base and the post
// does not stock it (false) - the ceiling doing its job, as it does on the
// keepers' boards. (Rounding up, that is an item worth nothing at all; one
// worth any part of a coin asks the whole coin.) Until v1.36.0 the Mortal number was written raw into every
// world's coin, so a manual asking 133 stones at the Mortal post asked 133
// crystals at the Celestial one and the Celestial broker paid a million times
// the Mortal shelf to buy it back.
func UnderworldBase(catalog worlddata.Catalog, itemID, currency string) (int64, bool) {
	base := ItemBasePrice(catalog, itemID)
	if base <= 0 {
		base = max64(8, ItemSectValue(catalog, itemID)*8)
	}
	// Rounded up on the climb (v1.38.0, shelfPriceAcrossUp): a post never
	// asks less than the base below it is worth.
	converted, ok := shelfPriceAcrossUp(catalog, map[string]shelfRate{}, worldBaseCurrency(catalog, "Mortal World"), currency, base)
	if !ok || converted < 1 {
		return 0, false
	}
	return converted, true
}

// blackMarketDearestAsk is the most a post can ever ask for one of an item in
// its coin: the hottest rotation with one unit left, or the hottest smuggler's
// price, whichever is more. A stock row priced above it is stale or wrong (a
// row written before v1.36.0 in an upper world is both), and a broker's pay is
// bounded by it rather than by the row.
func blackMarketDearestAsk(base int64) int64 {
	return max64(BlackMarketStockAsk(base, BlackMarketHeatCeiling, 1), BlackMarketSmuggledAsk(base, BlackMarketHeatCeiling))
}

// A post's heat is set when it rotates and does not move between rotations:
// BlackMarketHeatFloor plus a hash's share of the span up to
// BlackMarketHeatCeiling (simulation.blackMarkets). The arbitrage gate reads
// the two ends: the dearest ask a post can set (for what a broker then pays)
// and the cheapest (for what a cultivator can buy there and carry away).
const (
	BlackMarketHeatFloor   = int64(20)
	BlackMarketHeatCeiling = int64(80)
	// How many units a rotation stocks of one good, at most; the fewest is one.
	BlackMarketStockMostUnits = int64(4)
)

// BlackMarketStockAsk is what a post asks for a unit it stocks at a rotation:
// the base, dearer for scarcity (few on hand) and for the danger of the post's
// heat (v1.36.0: the formula moved here from simulation.blackMarkets so the
// arbitrage gate can read the floor it never goes under).
func BlackMarketStockAsk(base, heat, quantity int64) int64 {
	scarcity := 1.0 + float64(max64(0, 8-quantity))*0.05
	danger := 1.15 + float64(heat)/250.0
	return max64(1, int64(math.Round(float64(base)*scarcity*danger)))
}

// BlackMarketSmuggledAsk is what a post asks for a unit a finder smuggled to
// it: the base plus the post's heat as a percentage (simulation.smuggleToNightMarket).
func BlackMarketSmuggledAsk(base, heat int64) int64 {
	return max64(1, base+base*heat/100)
}

// blackMarketStockPay is what a broker pays per unit for a good the post
// stocks, in the post's coin: blackMarketStockShare of the row's asking price,
// the row held under the dearest ask the engine's own arithmetic can set
// (blackMarketDearestAsk, so a stale or mispriced row never pays more than a
// real one), and never a whole shelf's worth - the cheapest shelf anywhere,
// read across the ladder, caps it at one coin under, and a shelf under two
// coins leaves nothing to pay (0, and the sale is refused). One door: the
// arbitrage gate walks it beside the keepers, the merchants and the ordinary
// fence, and the trade calls it.
func blackMarketStockPay(catalog worlddata.Catalog, itemID, currency string, rowAsk int64) int64 {
	base, ok := UnderworldBase(catalog, itemID, currency)
	if !ok {
		return 0
	}
	ask := min64(rowAsk, blackMarketDearestAsk(base))
	unit := max64(1, int64(math.Round(float64(ask)*blackMarketStockShare)))
	if shelf, ok := cheapestShelfPrice(catalog, itemID, currency); ok {
		if shelf < 2 {
			return 0
		}
		unit = min64(unit, shelf-1)
	}
	return unit
}

// blackMarketOrdinaryShare is what a broker pays for an ordinary good against
// the cheapest shelf anywhere, in the post's coin: less than the 0.55 of the
// post's own stock, and always under the shelf, so nothing bought on a shelf
// sells to a broker for more.
const blackMarketOrdinaryShare = 0.40

// underworldFenceEventType keys the one point of standing a day an ordinary
// fence earns, in event_log the way a battle's daily reward is keyed.
const underworldFenceEventType = "underworld_fence"

// blackMarketOrdinaryFence is what a broker pays per unit for an ordinary good -
// one a shelf sells (MarketTradeable) - in a currency, and whether it pays at
// all: a share of the cheapest shelf anywhere, read across the ladder, and 0
// when no whole coin of this currency sits under that shelf. It is one of the
// NPC buy prices the arbitrage gate walks.
func blackMarketOrdinaryFence(catalog worlddata.Catalog, itemID, currency string) (int64, bool) {
	item, _, ok := itemDef(catalog, itemID)
	if !ok || !item.MarketTradeable() {
		return 0, false
	}
	shelf, ok := cheapestShelfPrice(catalog, itemID, currency)
	if !ok || shelf < 2 {
		return 0, false
	}
	unit := max64(1, int64(math.Round(float64(shelf)*blackMarketOrdinaryShare)))
	return min64(unit, shelf-1), true
}

// blackMarketFenceOrdinaryTx is a broker buying ordinary goods (v1.35.0). A
// post stocks six rows drawn from the ninety rare items no shelf sells, so a
// stranger's way to Underworld Contacts 15 was fifteen rare items; a broker
// takes ordinary contraband too now, at blackMarketOrdinaryShare of the
// cheapest shelf, and the standing it earns is smaller: one point a world day,
// however much is fenced, where the post's own stock pays one a sale. The goods
// are moved on, not stocked.
func blackMarketFenceOrdinaryTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, post map[string]any, p marketTradePayload, reason string) (authoritativeMutation, error) {
	currency := worldBaseCurrency(catalog, fmt.Sprint(post["world_name"]))
	unit, ok := blackMarketOrdinaryFence(catalog, p.ItemID, currency)
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("no broker here will pay a whole %s for %s", coinName(catalog, currency), itemDisplayName(catalog, p.ItemID))
	}
	owned, err := inventoryQuantityTx(conn, userID, p.ItemID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if owned < p.Quantity {
		return authoritativeMutation{}, errors.New("not enough carried item")
	}
	now := nowSeconds()
	total := unit * p.Quantity
	if _, err = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID}); err != nil {
		return authoritativeMutation{}, err
	}
	balance, err := walletDeltaTx(conn, catalog, userID, currency, total, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// One point of standing a world day for ordinary goods, keyed in the
	// event log so the day's first fence earns it and the rest earn money.
	dayKey := fmt.Sprintf("%s:day:%d", underworldFenceEventType, p.GameMinute/gameMinutesPerDay)
	paid, err := conn.Execute(`SELECT COUNT(*) FROM event_log WHERE user_id=? AND event_type=? AND json_extract(payload_json,'$.key')=?`, []any{userID, underworldFenceEventType, dayKey})
	if err != nil {
		return authoritativeMutation{}, err
	}
	rep := standingTx(conn, userID, "Underworld Contacts")
	trustGained := storage.ParseInt(paid.Rows[0][0]) == 0
	if trustGained {
		payload, _ := json.Marshal(map[string]any{"key": dayKey, "item": p.ItemID})
		if _, err = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, underworldFenceEventType, string(payload), now}); err != nil {
			return authoritativeMutation{}, err
		}
		if rep, err = adjustReputationTx(conn, userID, "Underworld Contacts", 1, "black market fencing (ordinary goods)", now); err != nil {
			return authoritativeMutation{}, err
		}
	}
	out := map[string]any{"buy": false, "item_id": p.ItemID, "quantity": p.Quantity, "currency_id": currency, "unit_price": unit, "total": total, "balance": balance, "heat": i64(post["heat"]), "access": reason, "underworld_reputation": rep, "trust_reputation": blackMarketTrustReputation, "detected": false, "crime": nil, "ordinary": true, "trust_gained": trustGained}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "black_market.trade", EntityType: "black_market", EntityID: fmt.Sprint(post["world_name"]), GameMinute: p.GameMinute, Payload: out}}, nil
}

func blackMarketTradeAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p marketTradePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Quantity = clamp(p.Quantity, 1, 20)
	p.ItemID = strings.TrimSpace(p.ItemID)
	c, err := characterLocationPower(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if p.Location == "" {
		p.Location = fmt.Sprint(c["location"])
	}
	if p.Location != fmt.Sprint(c["location"]) {
		return authoritativeMutation{}, errors.New("black-market location mismatch")
	}
	reason, _, err := blackMarketAuthorized(conn, catalog, userID, c)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// A broker buys from a stranger (v1.11.1). Underworld Contacts had one
	// source, a trade at a post, and a trade needed the trust it was meant
	// to earn - so the door by reputation could never open. A cultivator the
	// brokers do not know may fence the goods a post trades, and each sale is
	// the +1 below; selling to them is still for the trusted alone.
	if reason == "" && p.Buy {
		return authoritativeMutation{}, fmt.Errorf("underworld brokers do not recognize this character; they will buy from a stranger, but sell only to Underworld Contacts %d+, dark karma or a demonic sect", blackMarketTrustReputation)
	}
	if reason == "" {
		reason = "fencing as a stranger"
	}
	postRes, err := conn.Execute(`SELECT * FROM black_market_posts WHERE location=? AND active=1 AND opens_game_minute<=? AND closes_game_minute>?`, []any{p.Location, p.GameMinute, p.GameMinute})
	if err != nil {
		return authoritativeMutation{}, err
	}
	post := firstRowMap(postRes)
	if post == nil {
		return authoritativeMutation{}, errors.New("no black-market trading post is active at this location")
	}
	stockRes, err := conn.Execute(`SELECT * FROM black_market_stock WHERE world_name=? AND item_id=?`, []any{fmt.Sprint(post["world_name"]), p.ItemID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	stock := firstRowMap(stockRes)
	if stock == nil {
		if p.Buy {
			return authoritativeMutation{}, errors.New("that item is not traded by this underworld post")
		}
		return blackMarketFenceOrdinaryTx(conn, catalog, userID, post, p, reason)
	}
	currency := fmt.Sprint(stock["currency_id"])
	unit := max64(1, i64(stock["unit_price"]))
	now := nowSeconds()
	total := int64(0)
	balance := int64(0)
	if p.Buy {
		if i64(stock["quantity"]) < p.Quantity {
			return authoritativeMutation{}, errors.New("broker does not have that many remaining")
		}
		if p.Quantity > 0 && unit > math.MaxInt64/p.Quantity {
			return authoritativeMutation{}, errors.New("trade total overflow")
		}
		total = unit * p.Quantity
		balance, err = walletDeltaTx(conn, catalog, userID, currency, -total, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE black_market_stock SET quantity=quantity-?,updated_at=? WHERE world_name=? AND item_id=?`, []any{p.Quantity, now, fmt.Sprint(post["world_name"]), p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if err = addInventoryTx(conn, userID, map[string]int64{p.ItemID: p.Quantity}); err != nil {
			return authoritativeMutation{}, err
		}
		// Some of what the underworld sells is fake, and until now the
		// provenance row said flatly that none of it was.
		authenticity, aerr := rollUnderworldAuthenticity()
		if aerr != nil {
			return authoritativeMutation{}, aerr
		}
		_, err = conn.Execute(`INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, []any{userID, p.ItemID, p.Quantity, "black_market", fmt.Sprint(post["world_name"]), "underworld broker", fmt.Sprint(stock["legal_status"]), authenticity, max64(5, i64(post["heat"])/4), p.GameMinute, now, now})
		if err != nil {
			return authoritativeMutation{}, err
		}
	} else {
		owned, err := inventoryQuantityTx(conn, userID, p.ItemID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if owned < p.Quantity {
			return authoritativeMutation{}, errors.New("not enough carried item")
		}
		unit = blackMarketStockPay(catalog, p.ItemID, currency, unit)
		if unit < 1 {
			return authoritativeMutation{}, fmt.Errorf("no broker here will pay a whole %s for %s", coinName(catalog, currency), itemDisplayName(catalog, p.ItemID))
		}
		total = unit * p.Quantity
		_, err = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE black_market_stock SET quantity=quantity+?,updated_at=? WHERE world_name=? AND item_id=?`, []any{p.Quantity, now, fmt.Sprint(post["world_name"]), p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		balance, err = walletDeltaTx(conn, catalog, userID, currency, total, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	rep, err := adjustReputationTx(conn, userID, "Underworld Contacts", 1, map[bool]string{true: "black market trade", false: "black market fencing"}[p.Buy], now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	detected := false
	var crime map[string]any
	if p.Buy && i64(post["heat"]) >= 40 {
		threshold := min64(45, max64(5, i64(post["heat"])/3))
		roll, e := gamerng.Intn(100)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if int64(roll) < threshold {
			detected = true
			crime, err = recordCrimeTx(conn, userID, p.Location, "contraband_trade", fmt.Sprintf("Suspected purchase of %s from an illicit broker", p.ItemID), "local_watch", p.Location, 2, min64(85, 30+i64(post["heat"])/2), p.GameMinute, now)
			if err != nil {
				return authoritativeMutation{}, err
			}
		}
	}
	out := map[string]any{"buy": p.Buy, "item_id": p.ItemID, "quantity": p.Quantity, "currency_id": currency, "unit_price": unit, "total": total, "balance": balance, "heat": i64(post["heat"]), "access": reason, "underworld_reputation": rep, "trust_reputation": blackMarketTrustReputation, "detected": detected, "crime": crime}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "black_market.trade", EntityType: "black_market", EntityID: fmt.Sprint(post["world_name"]), GameMinute: p.GameMinute, Payload: out}}, nil
}

// marketSellShare is what a regional market pays against its buy price,
// before the shelf ceiling. One copy: the quote and the trade both read it
// through marketUnitPrice.
const marketSellShare = 0.70

// marketUnitPrice is what one unit costs at a market counter, or what the
// counter pays for one, and it is held inside the band the shops set (v1.2.3).
// A market's base is the item's sect value times a world factor, which has
// nothing to do with the shelves: measured off the shipped content, a Wind
// Gourd cost 9 at a market and a provisioner paid 45 for it, and a Stygian
// Tomb Token was 420 on a shelf and 840 at the market's counter. Both loops
// are mints, so a market never sells below the most any NPC pays in that
// coin - a keeper's plain third, a keeper's price to the top rank of the
// trade, or the town at a stall, which is highestNPCPays (v1.12.3: floored on
// the plain third alone it undercut the other two) - and never pays as much
// as the cheapest shelf asks - the rank ceiling's rule (trade_rank_price.go),
// stated once more for the counter it forgot. The shelf is the cheapest in any
// world, carried into the counter's coin (v1.34.0), and a sale the counter
// cannot price under it answers 0. The quote (market.rows, market.quote)
// reads the same function (v1.35.0), so a card never promises what the
// counter then refuses.
func marketUnitPrice(catalog worlddata.Catalog, itemID, currency string, base int64, index float64, buy bool) int64 {
	unit := max64(1, int64(math.Round(float64(base)*index)))
	if buy {
		if floor, ok := highestNPCPays(catalog, itemID, currency); ok && unit < floor {
			unit = floor
		}
		return unit
	}
	unit = max64(1, int64(math.Round(float64(unit)*marketSellShare)))
	if shelf, ok := cheapestShelfPrice(catalog, itemID, currency); ok {
		// The cheapest shelf anywhere asks less than one coin of this world's
		// (cheapestShelfPrice reads it across the ladder): no whole-coin price
		// sits under it, so the counter pays nothing and the caller refuses.
		if shelf < 1 {
			return 0
		}
		if unit >= shelf {
			unit = max64(1, shelf-1)
		}
	}
	return unit
}

// highestKeeperBuy is the most any shop pays for an item in one currency.
func highestKeeperBuy(catalog worlddata.Catalog, itemID, currency string) (int64, bool) {
	best, found := int64(0), false
	for _, shop := range catalog.Shops {
		if shop.Currency != currency {
			continue
		}
		if price, ok := keeperBuyBase(catalog, shop, itemID); ok && shop.Buys[itemID] > 0 && (!found || price > best) {
			best, found = price, true
		}
	}
	return best, found
}

func marketTradeAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p marketTradePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Quantity = clamp(p.Quantity, 1, 100)
	p.ItemID = strings.TrimSpace(p.ItemID)
	c, err := characterLocationPower(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if p.Location == "" {
		p.Location = fmt.Sprint(c["location"])
	}
	if p.Location != fmt.Sprint(c["location"]) {
		return authoritativeMutation{}, errors.New("market location mismatch")
	}
	r, err := conn.Execute(`SELECT * FROM economy_markets WHERE location=? AND item_id=?`, []any{p.Location, p.ItemID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	m := firstRowMap(r)
	if m == nil {
		return authoritativeMutation{}, errors.New("that item is not traded in this market")
	}
	currency := fmt.Sprint(m["currency_id"])
	unit := marketUnitPrice(catalog, p.ItemID, currency, i64(m["base_price"]), parseFloat(m["price_index"]), p.Buy)
	now := nowSeconds()
	total := int64(0)
	bal := int64(0)
	if p.Buy {
		if i64(m["supply"]) < p.Quantity {
			return authoritativeMutation{}, errors.New("market does not have that many in stock")
		}
		total = unit * p.Quantity
		bal, err = walletDeltaTx(conn, catalog, userID, currency, -total, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if err = addInventoryTx(conn, userID, map[string]int64{p.ItemID: p.Quantity}); err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE economy_markets SET supply=supply-?,demand=MIN(500,demand+?),updated_at=? WHERE location=? AND item_id=?`, []any{p.Quantity, max64(1, p.Quantity/2), now, p.Location, p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
	} else {
		owned, err := inventoryQuantityTx(conn, userID, p.ItemID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if owned < p.Quantity {
			return authoritativeMutation{}, errors.New("not enough carried item")
		}
		if unit < 1 {
			return authoritativeMutation{}, fmt.Errorf("this market will not pay for %s: the cheapest shelf in the world asks less than a single %s for it", itemDisplayName(catalog, p.ItemID), coinName(catalog, currency))
		}
		total = unit * p.Quantity
		_, err = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		bal, err = walletDeltaTx(conn, catalog, userID, currency, total, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE economy_markets SET supply=MIN(9999,supply+?),demand=MAX(1,demand-?),updated_at=? WHERE location=? AND item_id=?`, []any{p.Quantity, max64(1, p.Quantity/2), now, p.Location, p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	// A market counter is trade like any shelf (v1.29.0): it moved the
	// market's own supply and demand and was the one sale in the game that
	// left the city's prosperity where it was.
	if err := nudgeCityProsperityTx(conn, catalog, p.Location, 1); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"item_id": p.ItemID, "quantity": p.Quantity, "unit_price": unit, "total": total, "currency_id": currency, "buy": p.Buy, "balance": bal}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "market.trade", EntityType: "market", EntityID: p.Location, GameMinute: p.GameMinute, Payload: out}}, nil
}

func equipmentPowerGo(conn *storage.Conn, userID int64) (map[string]int64, error) {
	out := map[string]int64{"attack": 0, "defense": 0, "agility": 0}
	r, err := conn.Execute(`SELECT item_id,durability,max_durability,quality,equipped FROM equipment_instances WHERE user_id=? AND equipped=1`, []any{userID})
	if err != nil {
		return out, err
	}
	defs := equipmentDefinitionsGo()
	for _, row := range rowsToMaps(r) {
		if i64(row["equipped"]) == 0 || i64(row["durability"]) <= 0 {
			continue
		}
		d, ok := defs[itemBaseID(fmt.Sprint(row["item_id"]))]
		if !ok {
			continue
		}
		condition := math.Max(.25, math.Min(1, float64(i64(row["durability"]))/float64(max64(1, i64(row["max_durability"])))))
		quality := equipmentQualityMult(i64(row["quality"]))
		vals := map[string]int64{"attack": d.Attack, "defense": d.Defense, "agility": d.Agility}
		for _, k := range []string{"attack", "defense", "agility"} {
			out[k] += int64(math.Round(float64(vals[k]) * condition * quality))
		}
	}
	return out, nil
}

func bountyHunterActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p hunterActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Action = strings.ToLower(strings.TrimSpace(p.Action))
	if p.PursuitID <= 0 || !(p.Action == "evade" || p.Action == "fight" || p.Action == "surrender") {
		return authoritativeMutation{}, errors.New("invalid bounty hunter action")
	}
	r, err := conn.Execute(`SELECT * FROM bounty_hunter_pursuits WHERE pursuit_id=? AND user_id=? AND status IN ('tracking','engaged')`, []any{p.PursuitID, userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	hunt := firstRowMap(r)
	if hunt == nil {
		return authoritativeMutation{}, errors.New("active bounty hunter pursuit not found")
	}
	now := nowSeconds()
	var surrenderSettlement map[string]any
	if p.Action == "surrender" {
		_, err = conn.Execute(`UPDATE bounty_hunter_pursuits SET status='surrendered',capture_progress=100,updated_game_minute=?,updated_at=? WHERE pursuit_id=?`, []any{p.GameMinute, now, p.PursuitID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE bounties SET status='resolved',updated_at=? WHERE bounty_id=? AND status='active'`, []any{now, i64(hunt["bounty_id"])})
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE crime_records SET status='surrendered',updated_at=? WHERE crime_id=(SELECT source_crime_id FROM bounties WHERE bounty_id=?) AND status='open'`, []any{now, i64(hunt["bounty_id"])})
		if err != nil {
			return authoritativeMutation{}, err
		}
		// A surrender pays the restitution atoning would have (v1.28.0).
		settlement, err := SettleBountyTx(conn, catalog, userID, i64(hunt["bounty_id"]), false, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		surrenderSettlement = settlement
	} else {
		c, err := characterLocationPower(conn, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		// Fighting a hunter is a fight you chose, so a protected interior
		// refuses it exactly as `combat.start` refuses a challenge (v1.0.6).
		// Evading and surrendering stay open: one is walking away and the
		// other is the way out, and a gate with no way out is a deadlock -
		// the rule rc.56 wrote for the seclusion lockout.
		if p.Action == "fight" {
			if house, sanctuary := LocationIsSanctuary(catalog, fmt.Sprint(c["location"])); sanctuary {
				return authoritativeMutation{}, fmt.Errorf("violence is suppressed on the floor of %s; evade, surrender, or take it outside", house)
			}
		}
		attrs := rowAttributes(catalog, c)
		equip, err := equipmentPowerGo(conn, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		power := int64(0)
		if p.Action == "evade" {
			power = i64(attrs["agility"])*3 + i64(c["realm_index"])*2 + equip["agility"]
		} else {
			power = max64(i64(attrs["body"]), i64(attrs["spirit"]))*3 + i64(c["realm_index"])*2 + equip["attack"]
		}
		gain := max64(5, 20+power-i64(hunt["hunter_power"])*2+stablePercentGo(p.PursuitID, userID, p.Action, p.GameMinute)/10)
		// What you are carrying is what they are following (v1.0.0-rc.18).
		// `item_provenance.tracking_strength` had five writers and no reader;
		// this is the half of it a fugitive can feel. The floor inside
		// trailEscapePenalty is the point: even carrying a hidden sect's
		// brand an evade still makes progress, it just takes more of them -
		// and the lever is to stop carrying the thing.
		trail, err := carriedTrailTx(conn, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		gain = max64(1, gain-trailEscapePenalty(gain, trail))
		escape := min64(100, i64(hunt["escape_progress"])+gain)
		pressure := max64(0, i64(hunt["pressure"])-gain/2)
		status := fmt.Sprint(hunt["status"])
		if escape >= 100 {
			status = "evaded"
			if p.Action == "fight" {
				status = "defeated"
			}
		}
		_, err = conn.Execute(`UPDATE bounty_hunter_pursuits SET status=?,escape_progress=?,pressure=?,next_action_game_minute=?,updated_game_minute=?,updated_at=? WHERE pursuit_id=?`, []any{status, escape, pressure, p.GameMinute + 1440, p.GameMinute, now, p.PursuitID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if p.Action == "fight" {
			// damageEquipmentGo also auto-unequips anything it wears down to
			// 0 durability, which this raw UPDATE never did - a pre-existing
			// gap this consolidation fixes as a side effect.
			if err = damageEquipmentGo(conn, userID, 1); err != nil {
				return authoritativeMutation{}, err
			}
		}
	}
	final, err := conn.Execute(`SELECT p.*,b.jurisdiction,b.amount,b.reason FROM bounty_hunter_pursuits p JOIN bounties b ON b.bounty_id=p.bounty_id WHERE p.pursuit_id=?`, []any{p.PursuitID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := firstRowMap(final)
	if surrenderSettlement != nil {
		out["settlement"] = surrenderSettlement
	}
	// Say what is giving them away. A rule the player cannot see is the fault
	// this one was written to fix - `tracking_strength` spent five releases
	// being printed as a number that decided nothing.
	trail, err := carriedTrailTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out["trail"] = trail
	out["trail_word"] = trailWord(trail)
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "crime", EventType: "bounty_hunter.action", EntityType: "bounty_hunter_pursuit", EntityID: fmt.Sprint(p.PursuitID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func min64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
