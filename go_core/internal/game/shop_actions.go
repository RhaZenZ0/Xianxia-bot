package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// City shops (v0.35.0). Every city has a few fixed shops - a smithy, an
// apothecary, a talisman hall - that differ by city in kind, goods and tier.
// A shop is an interior location: exploring the city finds it, travelling to
// it enters it, and inside it a player buys from the shelf and sells what the
// keeper wants. Stock is content, refilled on the shop's own clock; nothing a
// shop sells is conjured by narration.

var shopDiscoveryIntn = gamerng.Intn

// shopDiscoveryChance is the chance, per explore in a city, of turning up
// one of its shops the player has not yet found.
const shopDiscoveryChance = 60

func shopTablesExist(conn *storage.Conn) (bool, error) {
	res, err := conn.Execute(`SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name IN ('shop_state','shop_stock')`, nil)
	if err != nil {
		return false, err
	}
	row := firstRowMap(res)
	return row != nil && i64(row["n"]) == 2, nil
}

// shopAt is the shop whose inside is this location.
func shopAt(catalog worlddata.Catalog, location string) (string, worlddata.Shop, bool) {
	loc, ok := catalog.Locations[location]
	if !ok || loc.Shop == "" {
		return "", worlddata.Shop{}, false
	}
	shop, ok := catalog.Shops[loc.Shop]
	if !ok {
		return "", worlddata.Shop{}, false
	}
	return loc.Shop, shop, true
}

// cityShopKeys lists the shops of a city, by key, in a fixed order.
func cityShopKeys(catalog worlddata.Catalog, city string) []string {
	keys := []string{}
	for key, shop := range catalog.Shops {
		if shop.City == city {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	return keys
}

// ensureShopStockTx seeds a shop's shelf the first time anyone looks at it
// and refills it to the content quantities once RestockMinutes have passed
// since the last refill. A line with more than content (a player sold some
// back) keeps what it has.
func ensureShopStockTx(conn *storage.Conn, catalog worlddata.Catalog, key string, gm int64, now float64) error {
	shop := catalog.Shops[key]
	res, err := conn.Execute(`SELECT last_restock_game_minute FROM shop_state WHERE shop=?`, []any{key})
	if err != nil {
		return err
	}
	row := firstRowMap(res)
	if row != nil {
		every := shop.RestockMinutes
		if every <= 0 {
			every = 720
		}
		if gm-i64(row["last_restock_game_minute"]) < every {
			return nil
		}
	}
	for _, line := range shop.Sells {
		if strings.TrimSpace(line.ItemID) == "" || line.Quantity <= 0 {
			continue
		}
		made := int64(0)
		if line.MadeHere {
			made = 1
		}
		if _, err := conn.Execute(`INSERT INTO shop_stock(shop,item_id,quantity,price,made_here,updated_at) VALUES(?,?,?,?,?,?)
			ON CONFLICT(shop,item_id) DO UPDATE SET quantity=MAX(shop_stock.quantity,excluded.quantity),price=excluded.price,made_here=excluded.made_here,updated_at=excluded.updated_at`,
			[]any{key, line.ItemID, line.Quantity, max64(1, line.Price), made, now}); err != nil {
			return err
		}
	}
	_, err = conn.Execute(`INSERT INTO shop_state(shop,last_restock_game_minute,updated_at) VALUES(?,?,?) ON CONFLICT(shop) DO UPDATE SET last_restock_game_minute=excluded.last_restock_game_minute,updated_at=excluded.updated_at`, []any{key, gm, now})
	return err
}

func shopStockRows(conn *storage.Conn, catalog worlddata.Catalog, key string) ([]map[string]any, error) {
	res, err := conn.Execute(`SELECT item_id,quantity,price,made_here FROM shop_stock WHERE shop=? AND quantity>0 ORDER BY made_here DESC,item_id`, []any{key})
	if err != nil {
		return nil, err
	}
	rows := []map[string]any{}
	for _, row := range rowsToMaps(res) {
		itemID := fmt.Sprint(row["item_id"])
		name := catalog.Items[itemID].Name
		if name == "" {
			name = itemID
		}
		rows = append(rows, map[string]any{"item_id": itemID, "name": name, "quantity": i64(row["quantity"]), "price": i64(row["price"]), "made_here": i64(row["made_here"]) == 1})
	}
	return rows, nil
}

func shopBuysRows(catalog worlddata.Catalog, shop worlddata.Shop) []map[string]any {
	ids := make([]string, 0, len(shop.Buys))
	for id := range shop.Buys {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	rows := []map[string]any{}
	for _, id := range ids {
		name := catalog.Items[id].Name
		if name == "" {
			name = id
		}
		rows = append(rows, map[string]any{"item_id": id, "name": name, "price": max64(1, shop.Buys[id])})
	}
	return rows
}

// shopHereQuery lists the shops of the city the player stands in (or whose
// shop they are inside), and which of them they have found.
func shopHereQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	city := c.Location
	inside := ""
	if key, _, ok := shopAt(catalog, c.Location); ok {
		city = catalog.Locations[c.Location].OutsideLocation
		inside = key
	}
	keys := cityShopKeys(catalog, city)
	known, err := knownLocationsTx(conn, catalog, userID, c)
	if err != nil {
		return nil, err
	}
	shops := []map[string]any{}
	found := 0
	for _, key := range keys {
		shop := catalog.Shops[key]
		discovered := known[shop.Location]
		if discovered {
			found++
		}
		row := map[string]any{"shop": key, "kind": shop.Kind, "tier": shop.Tier, "discovered": discovered}
		if discovered {
			row["name"] = shop.Name
			row["location"] = shop.Location
			row["keeper"] = shop.Keeper
		}
		shops = append(shops, row)
	}
	return map[string]any{"city": city, "inside": inside, "shops": shops, "found": found, "total": len(keys), "is_city": len(keys) > 0}, nil
}

// shopBrowseQuery is the shelf and the buying board of the shop the player
// is inside. A query's connection is not committed, so the refill it runs
// through ensureShopStockTx is seen in this view and written for good by
// the next buy or sell - which runs the same refill first, so the shelf a
// player was shown is the shelf they buy from.
func shopBrowseQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	key, shop, ok := shopAt(catalog, c.Location)
	if !ok {
		return nil, errors.New("not inside a shop; find one by exploring a city and travel to it")
	}
	tables, err := shopTablesExist(conn)
	if err != nil {
		return nil, err
	}
	if !tables {
		return nil, errors.New("shops are not open in this world yet")
	}
	gm, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	if err := ensureShopStockTx(conn, catalog, key, gm, nowSeconds()); err != nil {
		return nil, err
	}
	stock, err := shopStockRows(conn, catalog, key)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"shop":        key,
		"name":        shop.Name,
		"kind":        shop.Kind,
		"tier":        shop.Tier,
		"city":        shop.City,
		"keeper":      shop.Keeper,
		"currency_id": shop.Currency,
		"description": shop.Description,
		"stock":       stock,
		"buys":        shopBuysRows(catalog, shop),
	}, nil
}

type shopTradePayload struct {
	ItemID     string `json:"item_id"`
	Quantity   int64  `json:"quantity"`
	GameMinute int64  `json:"game_minute"`
}

func shopTradeSetup(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (shopTradePayload, string, worlddata.Shop, error) {
	var p shopTradePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return p, "", worlddata.Shop{}, err
	}
	p.ItemID = strings.TrimSpace(p.ItemID)
	if p.ItemID == "" {
		return p, "", worlddata.Shop{}, errors.New("item_id is required")
	}
	if p.Quantity <= 0 {
		p.Quantity = 1
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return p, "", worlddata.Shop{}, err
	}
	key, shop, ok := shopAt(catalog, c.Location)
	if !ok {
		return p, "", worlddata.Shop{}, errors.New("not inside a shop; find one by exploring a city and travel to it")
	}
	tables, err := shopTablesExist(conn)
	if err != nil {
		return p, "", worlddata.Shop{}, err
	}
	if !tables {
		return p, "", worlddata.Shop{}, errors.New("shops are not open in this world yet")
	}
	if err := ensureShopStockTx(conn, catalog, key, p.GameMinute, nowSeconds()); err != nil {
		return p, "", worlddata.Shop{}, err
	}
	return p, key, shop, nil
}

func itemDisplayName(catalog worlddata.Catalog, itemID string) string {
	if name := catalog.Items[itemID].Name; name != "" {
		return name
	}
	return itemID
}

// shopBuyAction takes goods off the shelf for the shop's price.
func shopBuyAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	p, key, shop, err := shopTradeSetup(conn, catalog, userID, raw)
	if err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(`SELECT quantity,price,made_here FROM shop_stock WHERE shop=? AND item_id=?`, []any{key, p.ItemID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	line := firstRowMap(res)
	if line == nil || i64(line["quantity"]) <= 0 {
		return authoritativeMutation{}, fmt.Errorf("%s does not have that on the shelf", shop.Name)
	}
	if have := i64(line["quantity"]); have < p.Quantity {
		return authoritativeMutation{}, fmt.Errorf("%s has only %d of those", shop.Name, have)
	}
	now := nowSeconds()
	unit := max64(1, i64(line["price"]))
	total := unit * p.Quantity
	balance, err := walletDeltaTx(conn, userID, shop.Currency, -total, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err := addInventoryTx(conn, userID, map[string]int64{p.ItemID: p.Quantity}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE shop_stock SET quantity=quantity-?,updated_at=? WHERE shop=? AND item_id=?`, []any{p.Quantity, now, key, p.ItemID}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"shop":        key,
		"shop_name":   shop.Name,
		"item_id":     p.ItemID,
		"item_name":   itemDisplayName(catalog, p.ItemID),
		"quantity":    p.Quantity,
		"unit_price":  unit,
		"total":       total,
		"currency_id": shop.Currency,
		"balance":     balance,
		"made_here":   i64(line["made_here"]) == 1,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "shop.buy", EntityType: "shop", EntityID: key, GameMinute: p.GameMinute, Payload: out}}, nil
}

// shopSellAction hands carried goods over for what the keeper pays. What the
// shop also sells goes back on its shelf at the shelf price.
func shopSellAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	p, key, shop, err := shopTradeSetup(conn, catalog, userID, raw)
	if err != nil {
		return authoritativeMutation{}, err
	}
	price, wanted := shop.Buys[p.ItemID]
	if !wanted {
		return authoritativeMutation{}, fmt.Errorf("%s does not buy %s", shop.Name, itemDisplayName(catalog, p.ItemID))
	}
	unit := max64(1, price)
	missing, err := consumeInventoryTx(conn, userID, map[string]int64{p.ItemID: p.Quantity})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(missing) > 0 {
		return authoritativeMutation{}, fmt.Errorf("you do not carry %d %s", p.Quantity, itemDisplayName(catalog, p.ItemID))
	}
	now := nowSeconds()
	total := unit * p.Quantity
	balance, err := walletDeltaTx(conn, userID, shop.Currency, total, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	restocked := false
	for _, line := range shop.Sells {
		if line.ItemID == p.ItemID {
			made := int64(0)
			if line.MadeHere {
				made = 1
			}
			if _, err := conn.Execute(`INSERT INTO shop_stock(shop,item_id,quantity,price,made_here,updated_at) VALUES(?,?,?,?,?,?)
				ON CONFLICT(shop,item_id) DO UPDATE SET quantity=shop_stock.quantity+excluded.quantity,updated_at=excluded.updated_at`,
				[]any{key, p.ItemID, p.Quantity, max64(1, line.Price), made, now}); err != nil {
				return authoritativeMutation{}, err
			}
			restocked = true
			break
		}
	}
	out := map[string]any{
		"shop":         key,
		"shop_name":    shop.Name,
		"item_id":      p.ItemID,
		"item_name":    itemDisplayName(catalog, p.ItemID),
		"quantity":     p.Quantity,
		"unit_price":   unit,
		"total":        total,
		"currency_id":  shop.Currency,
		"balance":      balance,
		"on_the_shelf": restocked,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "shop.sell", EntityType: "shop", EntityID: key, GameMinute: p.GameMinute, Payload: out}}, nil
}

// discoverCityShopTx is the walk through the city: exploring a city with
// shops the player has not found has a fair chance of turning one up, which
// is recorded as a location discovery so /travel can enter it. It returns
// the shop key found, or "".
func discoverCityShopTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, gameMinute int64, now float64) (string, error) {
	keys := cityShopKeys(catalog, c.Location)
	if len(keys) == 0 {
		return "", nil
	}
	known, err := knownLocationsTx(conn, catalog, userID, c)
	if err != nil {
		return "", err
	}
	candidates := []string{}
	for _, key := range keys {
		if !known[catalog.Shops[key].Location] {
			candidates = append(candidates, key)
		}
	}
	if len(candidates) == 0 {
		return "", nil
	}
	roll, err := shopDiscoveryIntn(100)
	if err != nil {
		return "", err
	}
	if roll >= shopDiscoveryChance {
		return "", nil
	}
	idx, err := shopDiscoveryIntn(len(candidates))
	if err != nil {
		return "", err
	}
	key := candidates[idx]
	r, err := conn.Execute(`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,?,?,?)`, []any{userID, catalog.Shops[key].Location, "shop", gameMinute, now})
	if err != nil {
		return "", err
	}
	if r.RowsAffected == 0 {
		return "", nil
	}
	return key, nil
}

// shopDiscoveryView is what the explore reply says about a shop just found.
func shopDiscoveryView(catalog worlddata.Catalog, key string) map[string]any {
	shop := catalog.Shops[key]
	return map[string]any{"shop": key, "name": shop.Name, "kind": shop.Kind, "tier": shop.Tier, "location": shop.Location, "keeper": shop.Keeper, "description": shop.Description}
}
