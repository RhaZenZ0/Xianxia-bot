package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Player-to-player trade (v0.39.0): a direct exchange of items and spirit
// stones between two cultivators at the same inn, confirmed on both sides.
// One side offers, naming what they give and what they want; the other
// accepts or declines; either can walk away. Nothing moves until the
// accept, and the accept checks both hands again - an offer is a promise,
// not an escrow. Every trade is a world_history row so the inn's rumours
// can carry it.

const tradeOfferMinutes int64 = 120

type tradeOfferPayload struct {
	ToUserID   int64            `json:"to_user_id"`
	GiveItems  map[string]int64 `json:"give_items"`
	GiveStones int64            `json:"give_stones"`
	WantItems  map[string]int64 `json:"want_items"`
	WantStones int64            `json:"want_stones"`
	GameMinute int64            `json:"game_minute"`
}

type tradeResolvePayload struct {
	OfferID    int64 `json:"offer_id"`
	GameMinute int64 `json:"game_minute"`
}

type tradeParty struct {
	UserID       int64
	Name         string
	Location     string
	LifeStatus   string
	SpiritStones int64
}

func tradeTablesExist(conn *storage.Conn) bool { return tableExistsTx(conn, "trade_offers") }

func loadTradeParty(conn *storage.Conn, userID int64) (tradeParty, error) {
	res, err := conn.Execute(`SELECT user_id,name,location,life_status,spirit_stones FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return tradeParty{}, err
	}
	row := firstRowMap(res)
	if row == nil {
		return tradeParty{}, errors.New("character not found")
	}
	return tradeParty{UserID: i64(row["user_id"]), Name: fmt.Sprint(row["name"]), Location: fmt.Sprint(row["location"]), LifeStatus: fmt.Sprint(row["life_status"]), SpiritStones: i64(row["spirit_stones"])}, nil
}

// tradeInn is the inn both parties must share: a trade is struck at the
// long table, not shouted across a city.
func tradeInn(catalog worlddata.Catalog, location string) (string, bool) {
	loc, ok := catalog.Locations[location]
	if !ok || loc.District != "inn" {
		return "", false
	}
	return location, true
}

func cleanTradeItems(catalog worlddata.Catalog, raw map[string]int64) (map[string]int64, error) {
	out := map[string]int64{}
	for id, qty := range raw {
		id = strings.TrimSpace(id)
		if id == "" || qty <= 0 {
			continue
		}
		if _, ok := catalog.Items[id]; !ok {
			return nil, fmt.Errorf("unknown item %s", id)
		}
		out[id] += qty
	}
	return out, nil
}

func inventoryShortfallTx(conn *storage.Conn, userID int64, items map[string]int64) (string, error) {
	ids := make([]string, 0, len(items))
	for id := range items {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		res, err := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, id})
		if err != nil {
			return "", err
		}
		have := int64(0)
		if row := firstRowMap(res); row != nil {
			have = i64(row["quantity"])
		}
		if have < items[id] {
			return id, nil
		}
	}
	return "", nil
}

func moveItemsTx(conn *storage.Conn, from, to int64, items map[string]int64, now float64) error {
	ids := make([]string, 0, len(items))
	for id := range items {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		qty := items[id]
		if _, err := conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{qty, from, id}); err != nil {
			return err
		}
		if _, err := conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=? AND quantity<=0`, []any{from, id}); err != nil {
			return err
		}
		if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`, []any{to, id, qty}); err != nil {
			return err
		}
	}
	return nil
}

func tradeItemsView(catalog worlddata.Catalog, items map[string]int64) []map[string]any {
	ids := make([]string, 0, len(items))
	for id := range items {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]map[string]any, 0, len(ids))
	for _, id := range ids {
		name := id
		if it, ok := catalog.Items[id]; ok && it.Name != "" {
			name = it.Name
		}
		out = append(out, map[string]any{"item_id": id, "name": name, "quantity": items[id]})
	}
	return out
}

func tradeOfferResult(catalog worlddata.Catalog, row map[string]any, names map[int64]string) map[string]any {
	var give, want map[string]int64
	_ = json.Unmarshal([]byte(fmt.Sprint(row["give_json"])), &give)
	_ = json.Unmarshal([]byte(fmt.Sprint(row["want_json"])), &want)
	from, to := i64(row["from_user_id"]), i64(row["to_user_id"])
	return map[string]any{
		"offer_id":            i64(row["offer_id"]),
		"from_user_id":        from,
		"from_name":           names[from],
		"to_user_id":          to,
		"to_name":             names[to],
		"location":            fmt.Sprint(row["location"]),
		"give_items":          tradeItemsView(catalog, give),
		"give_stones":         i64(row["give_stones"]),
		"want_items":          tradeItemsView(catalog, want),
		"want_stones":         i64(row["want_stones"]),
		"status":              fmt.Sprint(row["status"]),
		"created_game_minute": i64(row["created_game_minute"]),
		"expires_game_minute": i64(row["expires_game_minute"]),
	}
}

func tradeNamesTx(conn *storage.Conn, ids ...int64) (map[int64]string, error) {
	names := map[int64]string{}
	for _, id := range ids {
		if _, done := names[id]; done {
			continue
		}
		res, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{id})
		if err != nil {
			return nil, err
		}
		if row := firstRowMap(res); row != nil {
			names[id] = fmt.Sprint(row["name"])
		}
	}
	return names, nil
}

func tradeOfferAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if !tradeTablesExist(conn) {
		return authoritativeMutation{}, errors.New("trade offers are not available on this database")
	}
	var p tradeOfferPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.ToUserID <= 0 || p.ToUserID == userID {
		return authoritativeMutation{}, errors.New("a trade needs another cultivator")
	}
	if p.GiveStones < 0 || p.WantStones < 0 {
		return authoritativeMutation{}, errors.New("spirit stones cannot be negative")
	}
	give, err := cleanTradeItems(catalog, p.GiveItems)
	if err != nil {
		return authoritativeMutation{}, err
	}
	want, err := cleanTradeItems(catalog, p.WantItems)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(give) == 0 && p.GiveStones == 0 && len(want) == 0 && p.WantStones == 0 {
		return authoritativeMutation{}, errors.New("offer something or ask for something")
	}
	me, err := loadTradeParty(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	other, err := loadTradeParty(conn, p.ToUserID)
	if err != nil {
		return authoritativeMutation{}, errors.New("the other cultivator has no character")
	}
	if me.LifeStatus != "alive" || other.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only the living trade")
	}
	inn, ok := tradeInn(catalog, me.Location)
	if !ok {
		return authoritativeMutation{}, errors.New("trades are struck at an inn's long table - go to the city's inn")
	}
	if other.Location != inn {
		return authoritativeMutation{}, fmt.Errorf("%s is not at %s", other.Name, inn)
	}
	if short, err := inventoryShortfallTx(conn, userID, give); err != nil {
		return authoritativeMutation{}, err
	} else if short != "" {
		return authoritativeMutation{}, fmt.Errorf("you do not carry enough %s", short)
	}
	if me.SpiritStones < p.GiveStones {
		return authoritativeMutation{}, errors.New("not enough spirit stones to offer")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	// One open offer per pair and direction: a new one replaces the old.
	if _, err := conn.Execute(`UPDATE trade_offers SET status='withdrawn',resolved_at=?,updated_at=? WHERE from_user_id=? AND to_user_id=? AND status='open'`, []any{now, now, userID, p.ToUserID}); err != nil {
		return authoritativeMutation{}, err
	}
	giveJSON, _ := json.Marshal(give)
	wantJSON, _ := json.Marshal(want)
	expires := p.GameMinute + tradeOfferMinutes
	r, err := conn.Execute(`INSERT INTO trade_offers(from_user_id,to_user_id,location,give_json,give_stones,want_json,want_stones,status,created_game_minute,expires_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?,?,?)`, []any{userID, p.ToUserID, inn, string(giveJSON), p.GiveStones, string(wantJSON), p.WantStones, p.GameMinute, expires, now, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	names := map[int64]string{userID: me.Name, p.ToUserID: other.Name}
	result := tradeOfferResult(catalog, map[string]any{"offer_id": r.LastInsertID, "from_user_id": userID, "to_user_id": p.ToUserID, "location": inn, "give_json": string(giveJSON), "want_json": string(wantJSON), "give_stones": p.GiveStones, "want_stones": p.WantStones, "status": "open", "created_game_minute": p.GameMinute, "expires_game_minute": expires}, names)
	result["offered"] = true
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "trade", EventType: "trade.offered", EntityType: "trade_offer", EntityID: fmt.Sprint(r.LastInsertID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func loadOpenTradeOfferTx(conn *storage.Conn, offerID int64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT * FROM trade_offers WHERE offer_id=?`, []any{offerID})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("no such trade offer")
	}
	if fmt.Sprint(row["status"]) != "open" {
		return nil, fmt.Errorf("that offer is %s", row["status"])
	}
	return row, nil
}

func tradeAcceptAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if !tradeTablesExist(conn) {
		return authoritativeMutation{}, errors.New("trade offers are not available on this database")
	}
	var p tradeResolvePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	row, err := loadOpenTradeOfferTx(conn, p.OfferID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if i64(row["to_user_id"]) != userID {
		return authoritativeMutation{}, errors.New("that offer was not made to you")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	fromID := i64(row["from_user_id"])
	// A lapsed offer is closed as a result, not an error: an error would
	// roll the closing back with it.
	if p.GameMinute > i64(row["expires_game_minute"]) {
		if _, err := conn.Execute(`UPDATE trade_offers SET status='expired',resolved_at=?,updated_at=? WHERE offer_id=?`, []any{now, now, p.OfferID}); err != nil {
			return authoritativeMutation{}, err
		}
		names, err := tradeNamesTx(conn, fromID, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		result := tradeOfferResult(catalog, row, names)
		result["status"] = "expired"
		result["accepted"] = false
		result["lapsed"] = true
		return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "trade", EventType: "trade.expired", EntityType: "trade_offer", EntityID: fmt.Sprint(p.OfferID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
	}
	from, err := loadTradeParty(conn, fromID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	to, err := loadTradeParty(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	inn := fmt.Sprint(row["location"])
	if from.LifeStatus != "alive" || to.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only the living trade")
	}
	if from.Location != inn || to.Location != inn {
		return authoritativeMutation{}, fmt.Errorf("both of you must be at %s to strike the trade", inn)
	}
	var give, want map[string]int64
	_ = json.Unmarshal([]byte(fmt.Sprint(row["give_json"])), &give)
	_ = json.Unmarshal([]byte(fmt.Sprint(row["want_json"])), &want)
	giveStones, wantStones := i64(row["give_stones"]), i64(row["want_stones"])
	// Both hands are checked again now: an offer promised, it did not hold.
	if short, err := inventoryShortfallTx(conn, fromID, give); err != nil {
		return authoritativeMutation{}, err
	} else if short != "" {
		return authoritativeMutation{}, fmt.Errorf("%s no longer carries enough %s", from.Name, short)
	}
	if short, err := inventoryShortfallTx(conn, userID, want); err != nil {
		return authoritativeMutation{}, err
	} else if short != "" {
		return authoritativeMutation{}, fmt.Errorf("you do not carry enough %s", short)
	}
	if from.SpiritStones < giveStones {
		return authoritativeMutation{}, fmt.Errorf("%s no longer has the spirit stones offered", from.Name)
	}
	if to.SpiritStones < wantStones {
		return authoritativeMutation{}, errors.New("you do not have the spirit stones asked")
	}
	if err := moveItemsTx(conn, fromID, userID, give, now); err != nil {
		return authoritativeMutation{}, err
	}
	if err := moveItemsTx(conn, userID, fromID, want, now); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE characters SET spirit_stones=spirit_stones-?+?,updated_at=? WHERE user_id=?`, []any{giveStones, wantStones, now, fromID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE characters SET spirit_stones=spirit_stones-?+?,updated_at=? WHERE user_id=?`, []any{wantStones, giveStones, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE trade_offers SET status='accepted',resolved_at=?,updated_at=? WHERE offer_id=?`, []any{now, now, p.OfferID}); err != nil {
		return authoritativeMutation{}, err
	}
	names := map[int64]string{fromID: from.Name, userID: to.Name}
	result := tradeOfferResult(catalog, row, names)
	result["status"] = "accepted"
	result["accepted"] = true
	result["lapsed"] = false
	uid := userID
	summary := fmt.Sprintf("%s and %s struck a trade at %s.", from.Name, to.Name, inn)
	if err := recordWorldHistoryTx(conn, fmt.Sprintf("trade:%d", p.OfferID), "trade", "A trade at the inn", summary, 20, "participant", inn, "", "character", fmt.Sprint(fromID), from.Name, "character", fmt.Sprint(userID), to.Name, &uid, "", []string{"trade", "inn"}, p.GameMinute, map[string]any{"offer_id": p.OfferID}, now); err != nil {
		return authoritativeMutation{}, err
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "trade", EventType: "trade.accepted", EntityType: "trade_offer", EntityID: fmt.Sprint(p.OfferID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func tradeDeclineAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if !tradeTablesExist(conn) {
		return authoritativeMutation{}, errors.New("trade offers are not available on this database")
	}
	var p tradeResolvePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	row, err := loadOpenTradeOfferTx(conn, p.OfferID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	fromID, toID := i64(row["from_user_id"]), i64(row["to_user_id"])
	status := ""
	switch userID {
	case toID:
		status = "declined"
	case fromID:
		status = "withdrawn"
	default:
		return authoritativeMutation{}, errors.New("that offer is not yours to close")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`UPDATE trade_offers SET status=?,resolved_at=?,updated_at=? WHERE offer_id=?`, []any{status, now, now, p.OfferID}); err != nil {
		return authoritativeMutation{}, err
	}
	names, err := tradeNamesTx(conn, fromID, toID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := tradeOfferResult(catalog, row, names)
	result["status"] = status
	result["closed"] = true
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "trade", EventType: "trade." + status, EntityType: "trade_offer", EntityID: fmt.Sprint(p.OfferID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// tradeStatusQuery lists the open offers the actor made and the open
// offers made to them, with the inn they stand at.
func tradeStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	me, err := loadTradeParty(conn, userID)
	if err != nil {
		return nil, err
	}
	inn, atInn := tradeInn(catalog, me.Location)
	out := map[string]any{"location": me.Location, "at_inn": atInn, "inn": inn, "offers_made": []map[string]any{}, "offers_received": []map[string]any{}, "available": tradeTablesExist(conn)}
	if !tradeTablesExist(conn) {
		return out, nil
	}
	gm, err := canonicalWorldGameMinute(conn)
	if err != nil {
		gm = 0
	}
	res, err := conn.Execute(`SELECT * FROM trade_offers WHERE status='open' AND (from_user_id=? OR to_user_id=?) ORDER BY offer_id`, []any{userID, userID})
	if err != nil {
		return nil, err
	}
	made, received := []map[string]any{}, []map[string]any{}
	for _, raw := range res.Rows {
		row := map[string]any{}
		for i, col := range res.Columns {
			if i < len(raw) {
				row[col] = raw[i]
			}
		}
		if gm > 0 && i64(row["expires_game_minute"]) < gm {
			continue // lapsed; the next accept or decline closes it
		}
		names, err := tradeNamesTx(conn, i64(row["from_user_id"]), i64(row["to_user_id"]))
		if err != nil {
			return nil, err
		}
		view := tradeOfferResult(catalog, row, names)
		if i64(row["from_user_id"]) == userID {
			made = append(made, view)
		} else {
			received = append(received, view)
		}
	}
	out["offers_made"] = made
	out["offers_received"] = received
	return out, nil
}
