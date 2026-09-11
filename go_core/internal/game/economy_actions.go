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
	r, err := conn.Execute(`SELECT user_id,name,realm_index,phase,vitality,vitality_max,location,karma_score,attributes_json FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
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
	if _, err = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{house.Location, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"house_id": key, "name": house.Name, "location": house.Location, "outside": house.EntranceLocation}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "auction.enter", EntityType: "auction_house", EntityID: key, GameMinute: p.GameMinute, Payload: out}}, nil
}

var auctionPursuerNames = []string{"Black Veil Enforcer", "Silent Fang Cultivator", "Rival Young Master's Guard", "Shadow Market Hunter", "Masked Treasure Seeker"}

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
	if _, err = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{house.EntranceLocation, now, userID}); err != nil {
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
		incident := map[string]any{"risk_id": i64(risk["risk_id"]), "auction_id": i64(risk["auction_id"]), "item_id": fmt.Sprint(risk["item_id"]), "triggered": int64(roll) < chance}
		if int64(roll) < chance {
			itemID := fmt.Sprint(risk["item_id"])
			item := catalog.Items[itemID]
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
		if _, err = walletDeltaTx(conn, old, fmt.Sprint(a["currency_id"]), i64(a["current_bid"]), now); err != nil {
			return authoritativeMutation{}, err
		}
	} else if err := refundMerchantBidderTx(conn, catalog, a, p.GameMinute, now); err != nil {
		// A merchant held the lot (v0.37.0): its purse gets the bid back.
		return authoritativeMutation{}, err
	}
	bal, err := walletDeltaTx(conn, userID, fmt.Sprint(a["currency_id"]), -p.Amount, now)
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
	if rep >= 15 {
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
	return "", rep, nil
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
	if reason == "" {
		return authoritativeMutation{}, errors.New("underworld brokers do not recognize this character")
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
		return authoritativeMutation{}, errors.New("that item is not traded by this underworld post")
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
		balance, err = walletDeltaTx(conn, userID, currency, -total, now)
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
		_, err = conn.Execute(`INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, []any{userID, p.ItemID, p.Quantity, "black_market", fmt.Sprint(post["world_name"]), "underworld broker", fmt.Sprint(stock["legal_status"]), 100, max64(5, i64(post["heat"])/4), p.GameMinute, now, now})
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
		unit = max64(1, int64(math.Round(float64(unit)*0.55)))
		total = unit * p.Quantity
		_, err = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE black_market_stock SET quantity=quantity+?,updated_at=? WHERE world_name=? AND item_id=?`, []any{p.Quantity, now, fmt.Sprint(post["world_name"]), p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		balance, err = walletDeltaTx(conn, userID, currency, total, now)
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
	out := map[string]any{"buy": p.Buy, "item_id": p.ItemID, "quantity": p.Quantity, "currency_id": currency, "unit_price": unit, "total": total, "balance": balance, "heat": i64(post["heat"]), "access": reason, "underworld_reputation": rep, "detected": detected, "crime": crime}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "black_market.trade", EntityType: "black_market", EntityID: fmt.Sprint(post["world_name"]), GameMinute: p.GameMinute, Payload: out}}, nil
}

func marketTradeAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
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
	unit := max64(1, int64(math.Round(float64(i64(m["base_price"]))*parseFloat(m["price_index"]))))
	currency := fmt.Sprint(m["currency_id"])
	now := nowSeconds()
	total := int64(0)
	bal := int64(0)
	if p.Buy {
		if i64(m["supply"]) < p.Quantity {
			return authoritativeMutation{}, errors.New("market does not have that many in stock")
		}
		total = unit * p.Quantity
		bal, err = walletDeltaTx(conn, userID, currency, -total, now)
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
		unit = max64(1, int64(math.Round(float64(unit)*0.70)))
		total = unit * p.Quantity
		_, err = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		bal, err = walletDeltaTx(conn, userID, currency, total, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(`UPDATE economy_markets SET supply=MIN(9999,supply+?),demand=MAX(1,demand-?),updated_at=? WHERE location=? AND item_id=?`, []any{p.Quantity, max64(1, p.Quantity/2), now, p.Location, p.ItemID})
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	out := map[string]any{"item_id": p.ItemID, "quantity": p.Quantity, "unit_price": unit, "total": total, "currency_id": currency, "buy": p.Buy, "balance": bal}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "market.trade", EntityType: "market", EntityID: p.Location, GameMinute: p.GameMinute, Payload: out}}, nil
}

func equipmentPowerGo(conn *storage.Conn, userID int64) (map[string]int64, error) {
	out := map[string]int64{"attack": 0, "defense": 0, "spirit": 0, "agility": 0}
	r, err := conn.Execute(`SELECT item_id,durability,max_durability,quality,equipped FROM equipment_instances WHERE user_id=? AND equipped=1`, []any{userID})
	if err != nil {
		return out, err
	}
	defs := equipmentDefinitionsGo()
	for _, row := range rowsToMaps(r) {
		if i64(row["equipped"]) == 0 || i64(row["durability"]) <= 0 {
			continue
		}
		d, ok := defs[fmt.Sprint(row["item_id"])]
		if !ok {
			continue
		}
		condition := math.Max(.25, math.Min(1, float64(i64(row["durability"]))/float64(max64(1, i64(row["max_durability"])))))
		quality := math.Max(.5, 1+(float64(i64(row["quality"])-100)/200))
		vals := map[string]int64{"attack": d.Attack, "defense": d.Defense, "spirit": d.Spirit, "agility": d.Agility}
		for _, k := range []string{"attack", "defense", "spirit", "agility"} {
			out[k] += int64(math.Round(float64(vals[k]) * condition * quality))
		}
	}
	return out, nil
}

func bountyHunterActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
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
	} else {
		c, err := characterLocationPower(conn, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		attrs := decodeJSONMap(c["attributes_json"])
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
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "crime", EventType: "bounty_hunter.action", EntityType: "bounty_hunter_pursuit", EntityID: fmt.Sprint(p.PursuitID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func min64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
