package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type storageMovePayload struct {
	ItemID     string `json:"item_id"`
	Quantity   int64  `json:"quantity"`
	GameMinute int64  `json:"game_minute"`
}
type storageUpgradePayload struct {
	ItemID     string `json:"item_id"`
	GameMinute int64  `json:"game_minute"`
}
type abodeEstablishPayload struct {
	Name         string `json:"name"`
	PropertyType string `json:"property_type"`
	GameMinute   int64  `json:"game_minute"`
}
type abodeGuestPayload struct {
	GuestUserID int64 `json:"guest_user_id"`
	GameMinute  int64 `json:"game_minute"`
}
type abodeFacilityPayload struct {
	Facility   string `json:"facility"`
	GameMinute int64  `json:"game_minute"`
}
type locationActionPayload struct {
	OwnerUserID int64 `json:"owner_user_id"`
	GameMinute  int64 `json:"game_minute"`
}
type teleportPayload struct {
	ArrayID    string `json:"array_id"`
	GameMinute int64  `json:"game_minute"`
}
type deployArrayPayload struct {
	ItemID     string `json:"item_id"`
	GameMinute int64  `json:"game_minute"`
}
type spatialKeyPayload struct {
	ItemID     string `json:"item_id"`
	GameMinute int64  `json:"game_minute"`
}
type personalWorldCreatePayload struct {
	Name       string `json:"name"`
	GameMinute int64  `json:"game_minute"`
}
type personalWorldRulePayload struct {
	Rule       string `json:"rule"`
	Definition string `json:"definition"`
	GameMinute int64  `json:"game_minute"`
}

func storageMoveActionGo(conn *storage.Conn, userID int64, raw json.RawMessage, withdraw bool) (authoritativeMutation, error) {
	var p storageMovePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.ItemID = strings.TrimSpace(p.ItemID)
	if p.ItemID == "" {
		return authoritativeMutation{}, errors.New("item_id is required")
	}
	if p.Quantity <= 0 {
		p.Quantity = 1
	}
	if withdraw {
		r, e := conn.Execute(`SELECT quantity FROM storage_inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row := firstRowMap(r)
		if row == nil || i64(row["quantity"]) < p.Quantity {
			return authoritativeMutation{}, errors.New("not enough of that item in spatial storage")
		}
		if _, e = conn.Execute(`UPDATE storage_inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID}); e != nil {
			return authoritativeMutation{}, e
		}
		if _, e = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`, []any{userID, p.ItemID, p.Quantity}); e != nil {
			return authoritativeMutation{}, e
		}
	} else {
		r, e := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row := firstRowMap(r)
		if row == nil || i64(row["quantity"]) < p.Quantity {
			return authoritativeMutation{}, errors.New("not enough of that item in carried inventory")
		}
		r, e = conn.Execute(`SELECT slot_capacity FROM storage_containers WHERE user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		container := firstRowMap(r)
		if container == nil {
			return authoritativeMutation{}, errors.New("no spatial storage container")
		}
		r, e = conn.Execute(`SELECT quantity FROM storage_inventory WHERE user_id=? AND item_id=? AND quantity>0`, []any{userID, p.ItemID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if firstRowMap(r) == nil {
			r, e = conn.Execute(`SELECT COUNT(*) AS n FROM storage_inventory WHERE user_id=? AND quantity>0`, []any{userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			if i64(firstRowMap(r)["n"]) >= i64(container["slot_capacity"]) {
				return authoritativeMutation{}, errors.New("spatial storage has no free item slots")
			}
		}
		if _, e = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID}); e != nil {
			return authoritativeMutation{}, e
		}
		if _, e = conn.Execute(`INSERT INTO storage_inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`, []any{userID, p.ItemID, p.Quantity}); e != nil {
			return authoritativeMutation{}, e
		}
	}
	direction := "deposit"
	if withdraw {
		direction = "withdraw"
	}
	out := map[string]any{"item_id": p.ItemID, "quantity": p.Quantity, "direction": direction}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "storage", EventType: "storage." + direction, EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func storageUpgradeActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p storageUpgradePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	item, ok := catalog.Items[p.ItemID]
	if !ok || len(item.StorageUpgrade) == 0 {
		return authoritativeMutation{}, errors.New("item is not a spatial-storage treasure")
	}
	r, e := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	row := firstRowMap(r)
	if row == nil || i64(row["quantity"]) <= 0 {
		return authoritativeMutation{}, errors.New("you do not carry that item")
	}
	if _, e = conn.Execute(`UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID}); e != nil {
		return authoritativeMutation{}, e
	}
	r, e = conn.Execute(`SELECT COUNT(*) AS n FROM storage_inventory WHERE user_id=? AND quantity>0`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	cap := max64(1, i64(item.StorageUpgrade["slot_capacity"]))
	if used := i64(firstRowMap(r)["n"]); cap < used {
		cap = used
	}
	living := int64(0)
	if b, ok := item.StorageUpgrade["living_space"].(bool); ok && b {
		living = 1
	}
	// An upgrade may not be a downgrade (v0.23.1). The command calls this
	// operation an upgrade and the catalog runs from a 24-slot pouch to a
	// 500-slot ring with living space, so using the wrong item after owning
	// the better one silently traded 500 slots and a living world for 24 and
	// nothing - consuming the pouch on the way. The occupied-slot floor above
	// stopped items being *stranded*, which is not the same as stopping the
	// container being made worse.
	//
	// The check happens after the item is consumed only in source order; the
	// whole action is one transaction, so a refusal here returns the item too.
	existing, e := conn.Execute(
		`SELECT slot_capacity,living_space,name FROM storage_containers WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if held := firstRowMap(existing); held != nil {
		heldCap, heldLiving := i64(held["slot_capacity"]), i64(held["living_space"])
		if cap < heldCap {
			return authoritativeMutation{}, fmt.Errorf(
				"%s holds %d stacks; %s would hold only %d",
				held["name"], heldCap, item.Name, cap)
		}
		if heldLiving != 0 && living == 0 {
			return authoritativeMutation{}, fmt.Errorf(
				"%s has a living space and %s does not", held["name"], item.Name)
		}
	}
	cid := strings.TrimSpace(fmt.Sprint(item.StorageUpgrade["container_id"]))
	if cid == "" {
		cid = p.ItemID
	}
	grade := strings.TrimSpace(fmt.Sprint(item.StorageUpgrade["grade"]))
	if grade == "" {
		grade = "Mortal"
	}
	if _, e = conn.Execute(`INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET container_id=excluded.container_id,name=excluded.name,grade=excluded.grade,slot_capacity=excluded.slot_capacity,living_space=excluded.living_space,updated_at=excluded.updated_at`, []any{userID, cid, item.Name, grade, cap, living, nowSeconds()}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"item_id": p.ItemID, "container_id": cid, "name": item.Name, "grade": grade, "slot_capacity": cap, "living_space": living != 0}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "storage", EventType: "storage.upgrade", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func auctionHouseExistsAt(c worlddata.Catalog, loc string) bool {
	_, _, ok := catalogHouseAt(c, loc)
	return ok
}

func abodeByOwnerGo(conn *storage.Conn, id int64) (map[string]any, error) {
	r, e := conn.Execute(`SELECT * FROM cave_abodes WHERE user_id=?`, []any{id})
	return firstRowMap(r), e
}
func abodeByLocationGo(conn *storage.Conn, loc string) (map[string]any, error) {
	r, e := conn.Execute(`SELECT * FROM cave_abodes WHERE location_key=?`, []any{loc})
	return firstRowMap(r), e
}
func canAccessAbodeGo(conn *storage.Conn, owner, guest int64) (bool, error) {
	if owner == guest {
		return true, nil
	}
	r, e := conn.Execute(`SELECT 1 AS ok FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?`, []any{owner, guest})
	return firstRowMap(r) != nil, e
}
func propertyDefaults(c worlddata.Catalog, pt string) map[string]int64 {
	out := map[string]int64{"cultivation": 1, "alchemy": 0, "forge": 0, "formation": 0, "defense": 0, "storage": 1, "herb_garden": 0, "beast_pen": 0, "merchant": 0}
	pts, _ := c.AbodeSystem["property_types"].(map[string]any)
	d, _ := pts[pt].(map[string]any)
	defs, _ := d["defaults"].(map[string]any)
	for k, v := range defs {
		if _, ok := out[k]; ok {
			out[k] = clamp(i64(v), 0, 9)
		}
	}
	return out
}

func abodeEstablishActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p abodeEstablishPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.Name = strings.TrimSpace(p.Name)
	if p.Name == "" {
		return authoritativeMutation{}, errors.New("property name is required")
	}
	rr := []rune(p.Name)
	if len(rr) > 60 {
		p.Name = string(rr[:60])
	}
	p.PropertyType = strings.TrimSpace(p.PropertyType)
	buildable := buildablePropertyTypes(catalog)
	if p.PropertyType == "" && len(buildable) == 1 {
		p.PropertyType = buildable[0]
	}
	if !propertyTypeBuildable(catalog, p.PropertyType) {
		return authoritativeMutation{}, fmt.Errorf("a home is founded as one of: %s; its facilities are built with abode.upgrade", strings.Join(buildable, ", "))
	}
	r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	loc := fmt.Sprint(ch["location"])
	if strings.HasPrefix(loc, "abode:") || strings.HasPrefix(loc, "sect_abode:") || strings.HasPrefix(loc, "personal_world:") || auctionHouseExistsAt(catalog, loc) {
		return authoritativeMutation{}, errors.New("choose a normal outdoor/city location as the foundation of your property")
	}
	if a, _ := abodeByOwnerGo(conn, userID); a != nil {
		return authoritativeMutation{}, errors.New("you already own a player property")
	}
	if floor := homesteadFoundingRankGo(catalog); floor > 0 {
		// v0.30.1: the sect residence is the starter home; land of one's own
		// comes with standing. The content names the rank.
		mr, e := conn.Execute(`SELECT rank_name,rank_level FROM sect_membership WHERE user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		membership := firstRowMap(mr)
		if membership == nil || i64(membership["rank_level"]) < floor {
			return authoritativeMutation{}, fmt.Errorf("founding a homestead asks standing in a public sect: %s (rank %d) or higher", sectRankName(catalog, floor), floor)
		}
	}
	lv := propertyDefaults(catalog, p.PropertyType)
	now := nowSeconds()
	key := fmt.Sprintf("abode:%d", userID)
	_, e = conn.Execute(`INSERT INTO cave_abodes(user_id,location_key,name,base_location,property_type,cultivation_level,alchemy_level,forge_level,formation_level,defense_level,storage_level,herb_garden_level,beast_pen_level,merchant_level,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{userID, key, p.Name, loc, p.PropertyType, lv["cultivation"], lv["alchemy"], lv["forge"], lv["formation"], lv["defense"], lv["storage"], lv["herb_garden"], lv["beast_pen"], lv["merchant"], now, now})
	if e != nil {
		return authoritativeMutation{}, e
	}
	a, _ := abodeByOwnerGo(conn, userID)
	return authoritativeMutation{Result: a, Event: eventledger.Event{Domain: "property", EventType: "abode.establish", EntityType: "abode", EntityID: key, GameMinute: p.GameMinute, Payload: a}}, nil
}

func abodeMoveActionGo(conn *storage.Conn, userID int64, raw json.RawMessage, mode string) (authoritativeMutation, error) {
	var p locationActionPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	current := fmt.Sprint(ch["location"])
	var a map[string]any
	switch mode {
	case "enter":
		a, e = abodeByOwnerGo(conn, userID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if a == nil {
			return authoritativeMutation{}, errors.New("you do not own a player property")
		}
		if current != fmt.Sprint(a["base_location"]) {
			return authoritativeMutation{}, fmt.Errorf("travel to %s first", a["base_location"])
		}
	case "visit":
		if p.OwnerUserID <= 0 {
			return authoritativeMutation{}, errors.New("owner_user_id is required")
		}
		a, e = abodeByOwnerGo(conn, p.OwnerUserID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if a == nil {
			return authoritativeMutation{}, errors.New("that cultivator has no player-owned property")
		}
		if current != fmt.Sprint(a["base_location"]) {
			return authoritativeMutation{}, fmt.Errorf("travel to %s first", a["base_location"])
		}
		ok, e := canAccessAbodeGo(conn, p.OwnerUserID, userID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if !ok {
			return authoritativeMutation{}, errors.New("you have not been invited to that property")
		}
	case "leave":
		a, e = abodeByLocationGo(conn, current)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if a == nil {
			return authoritativeMutation{}, errors.New("you are not inside a player-owned property")
		}
	default:
		return authoritativeMutation{}, errors.New("unknown abode movement")
	}
	dest := fmt.Sprint(a["location_key"])
	if mode == "leave" {
		dest = fmt.Sprint(a["base_location"])
	}
	if _, e = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{dest, nowSeconds(), userID}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"abode": a, "location": dest, "mode": mode}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "property", EventType: "abode." + mode, EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func abodeGuestActionGo(conn *storage.Conn, userID int64, raw json.RawMessage, revoke bool) (authoritativeMutation, error) {
	var p abodeGuestPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.GuestUserID <= 0 || p.GuestUserID == userID {
		return authoritativeMutation{}, errors.New("valid guest_user_id is required")
	}
	a, e := abodeByOwnerGo(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if a == nil {
		return authoritativeMutation{}, errors.New("you do not own a player property")
	}
	r, e := conn.Execute(`SELECT 1 AS ok FROM characters WHERE user_id=?`, []any{p.GuestUserID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if firstRowMap(r) == nil {
		return authoritativeMutation{}, errors.New("that member has no cultivation character")
	}
	evicted := false
	if revoke {
		if _, e = conn.Execute(`DELETE FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?`, []any{userID, p.GuestUserID}); e != nil {
			return authoritativeMutation{}, e
		}
		r, e = conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{p.GuestUserID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		g := firstRowMap(r)
		if g != nil && fmt.Sprint(g["location"]) == fmt.Sprint(a["location_key"]) {
			if _, e = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{a["base_location"], nowSeconds(), p.GuestUserID}); e != nil {
				return authoritativeMutation{}, e
			}
			evicted = true
		}
	} else {
		if _, e = conn.Execute(`INSERT INTO cave_abode_access(owner_user_id,guest_user_id,access_role,created_at) VALUES(?,?,'guest',?) ON CONFLICT(owner_user_id,guest_user_id) DO NOTHING`, []any{userID, p.GuestUserID, nowSeconds()}); e != nil {
			return authoritativeMutation{}, e
		}
	}
	et := "abode.invite"
	if revoke {
		et = "abode.revoke"
	}
	out := map[string]any{"guest_user_id": p.GuestUserID, "revoked": revoke, "evicted": evicted, "abode": a}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "property", EventType: et, EntityType: "abode", EntityID: fmt.Sprint(a["location_key"]), GameMinute: p.GameMinute, Payload: out}}, nil
}

var abodeFacilityCols = map[string]string{"cultivation": "cultivation_level", "alchemy": "alchemy_level", "forge": "forge_level", "formation": "formation_level", "defense": "defense_level", "storage": "storage_level", "herb_garden": "herb_garden_level", "beast_pen": "beast_pen_level", "merchant": "merchant_level"}

func abodeUpgradeActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p abodeFacilityPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	col := abodeFacilityCols[p.Facility]
	if col == "" {
		return authoritativeMutation{}, errors.New("unknown player-property facility")
	}
	a, e := abodeByOwnerGo(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if a == nil {
		return authoritativeMutation{}, errors.New("you do not own a player property")
	}
	current := i64(a[col])
	maxlvl := i64(catalog.AbodeSystem["max_level"])
	if maxlvl <= 0 {
		maxlvl = 9
	}
	if current >= maxlvl {
		return authoritativeMutation{}, errors.New("that facility is already at maximum level")
	}
	base := i64(catalog.AbodeSystem["upgrade_base_cost"])
	if base <= 0 {
		base = 100
	}
	cost := base * (current + 1) * (current + 1)
	currency := fmt.Sprint(catalog.AbodeSystem["currency"])
	if currency == "" {
		currency = "low_spirit_stone"
	}
	balance, e := walletDeltaTx(conn, userID, currency, -cost, nowSeconds())
	if e != nil {
		return authoritativeMutation{}, errors.New("insufficient currency")
	}
	q := fmt.Sprintf(`UPDATE cave_abodes SET %s=MIN(?,%s+1),updated_at=? WHERE user_id=?`, col, col)
	if _, e = conn.Execute(q, []any{maxlvl, nowSeconds(), userID}); e != nil {
		return authoritativeMutation{}, e
	}
	a, _ = abodeByOwnerGo(conn, userID)
	out := map[string]any{"facility": p.Facility, "level": i64(a[col]), "cost": cost, "currency": currency, "balance": balance, "abode": a}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "property", EventType: "abode.upgrade", EntityType: "abode", EntityID: fmt.Sprint(a["location_key"]), GameMinute: p.GameMinute, Payload: out}}, nil
}

func abodeFocusActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p abodeFacilityPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	col := abodeFacilityCols[p.Facility]
	if col == "" {
		return authoritativeMutation{}, errors.New("unknown player-property facility")
	}
	r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	a, e := abodeByLocationGo(conn, fmt.Sprint(ch["location"]))
	if e != nil {
		return authoritativeMutation{}, e
	}
	if a == nil {
		return authoritativeMutation{}, errors.New("you must be inside a player-owned property you are allowed to use")
	}
	ok, e := canAccessAbodeGo(conn, i64(a["user_id"]), userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if !ok {
		return authoritativeMutation{}, errors.New("you must be inside a player-owned property you are allowed to use")
	}
	lvl := i64(a[col])
	if lvl <= 0 {
		return authoritativeMutation{}, errors.New("that facility has not been built yet")
	}
	effectID := map[string]string{"cultivation": "abode_cultivation_focus", "alchemy": "alchemy_inspiration", "forge": "forge_inspiration"}[p.Facility]
	out := map[string]any{"facility": p.Facility, "level": lvl, "effect_id": effectID}
	if effectID != "" {
		effect := catalog.SpecialEffects[effectID]
		enc, _ := json.Marshal(effect)
		name := fmt.Sprint(effect["name"])
		if name == "" {
			name = p.Facility
		}
		if _, e = conn.Execute(`INSERT INTO active_effects(user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at) VALUES(?,?,?,?,?,?,1,?,?,?) ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET name=excluded.name,effect_json=excluded.effect_json,stacks=1,starts_game_minute=excluded.starts_game_minute,ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at`, []any{userID, effectID, name, "abode", fmt.Sprint(a["user_id"]), string(enc), p.GameMinute, p.GameMinute + 240, nowSeconds()}); e != nil {
			return authoritativeMutation{}, e
		}
		out["effect_name"] = name
		out["duration_game_minutes"] = int64(240)
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "property", EventType: "abode.focus", EntityType: "abode", EntityID: fmt.Sprint(a["location_key"]), GameMinute: p.GameMinute, Payload: out}}, nil
}

func teleportArrayActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p teleportPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	d, ok := catalog.TeleportArrays[p.ArrayID]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown teleportation array")
	}
	r, e := conn.Execute(`SELECT location,realm_index FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	if fmt.Sprint(ch["location"]) != d.From {
		return authoritativeMutation{}, errors.New("that array is not available here")
	}
	if i64(ch["realm_index"]) < d.MinRealmIndex {
		return authoritativeMutation{}, errors.New("your realm cannot withstand this transit")
	}
	balance, e := walletDeltaTx(conn, userID, d.Currency, -d.Cost, nowSeconds())
	if e != nil {
		return authoritativeMutation{}, errors.New("you cannot pay the array activation cost")
	}
	if _, e = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{d.To, nowSeconds(), userID}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"array_id": p.ArrayID, "name": d.Name, "from": d.From, "to": d.To, "currency": d.Currency, "cost": d.Cost, "balance": balance}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "travel", EventType: "array.use", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

type deployedArrayDef struct {
	Name     string
	Duration int64
	Effect   map[string]any
}

var deployedArrayDefs = map[string]deployedArrayDef{
	"minor_qi_gathering_array_disk": {"Minor Qi Gathering Array", 360, map[string]any{"description": "Formation flags draw ambient qi toward everyone cultivating at this location.", "modifiers": []any{map[string]any{"stat": "cultivation_gain", "operation": "mul", "value": 1.10}, map[string]any{"stat": "spirit", "operation": "add", "value": 1}}, "tags": []any{"formation", "location", "qi"}}},
	"minor_warding_array_disk":      {"Minor Warding Array", 360, map[string]any{"description": "A compact defensive formation steadies cultivators and reinforces combat exchanges in this location.", "modifiers": []any{map[string]any{"stat": "will", "operation": "add", "value": 1}, map[string]any{"stat": "combat_bonus", "operation": "add", "value": 1}}, "tags": []any{"formation", "location", "defense"}}},
}

func deployArrayActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p deployArrayPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	item, ok := catalog.Items[p.ItemID]
	if !ok || item.ArrayDeploy == "" {
		return authoritativeMutation{}, errors.New("item is not a deployable formation disk")
	}
	def, ok := deployedArrayDefs[item.ArrayDeploy]
	if !ok {
		return authoritativeMutation{}, errors.New("formation disk has no valid deployment definition")
	}
	r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	loc := fmt.Sprint(ch["location"])
	r, e = conn.Execute(`SELECT name,ends_game_minute FROM deployed_location_arrays WHERE location=? AND ends_game_minute>?`, []any{loc, p.GameMinute})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if a := firstRowMap(r); a != nil {
		return authoritativeMutation{}, fmt.Errorf("%s is already active at this location until game-minute %d", a["name"], i64(a["ends_game_minute"]))
	}
	r, e = conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	row := firstRowMap(r)
	if row == nil || i64(row["quantity"]) <= 0 {
		return authoritativeMutation{}, errors.New("you no longer carry that formation disk")
	}
	r, e = conn.Execute(`SELECT sect_name FROM sect_membership WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	sect := ""
	if m := firstRowMap(r); m != nil {
		sect = fmt.Sprint(m["sect_name"])
	}
	enc, _ := json.Marshal(def.Effect)
	now := nowSeconds()
	ends := p.GameMinute + def.Duration
	if _, e = conn.Execute(`UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID}); e != nil {
		return authoritativeMutation{}, e
	}
	if _, e = conn.Execute(`INSERT INTO deployed_location_arrays(location,item_id,name,owner_user_id,sect_name,effect_json,starts_game_minute,ends_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(location) DO UPDATE SET item_id=excluded.item_id,name=excluded.name,owner_user_id=excluded.owner_user_id,sect_name=excluded.sect_name,effect_json=excluded.effect_json,starts_game_minute=excluded.starts_game_minute,ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at,updated_at=excluded.updated_at`, []any{loc, p.ItemID, def.Name, userID, sect, string(enc), p.GameMinute, ends, now, now}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"location": loc, "item_id": p.ItemID, "name": def.Name, "owner_user_id": userID, "sect_name": sect, "effect": def.Effect, "starts_game_minute": p.GameMinute, "ends_game_minute": ends}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "formation", EventType: "array.deploy", EntityType: "location", EntityID: loc, GameMinute: p.GameMinute, Payload: out}}, nil
}

func spatialKeyActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p spatialKeyPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	item, ok := catalog.Items[p.ItemID]
	if !ok || len(item.SpatialKey) == 0 {
		return authoritativeMutation{}, errors.New("item is not a spatial key/token")
	}
	rid := fmt.Sprint(item.SpatialKey["secret_realm_id"])
	realm, ok := catalog.SecretRealms[rid]
	if !ok {
		return authoritativeMutation{}, errors.New("the token's coordinates no longer correspond to a known realm")
	}
	r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	r, e = conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	row := firstRowMap(r)
	if row == nil || i64(row["quantity"]) <= 0 {
		return authoritativeMutation{}, errors.New("you do not carry that spatial key")
	}
	consumed := true
	if b, ok := item.SpatialKey["consumed"].(bool); ok {
		consumed = b
	}
	if consumed {
		if _, e = conn.Execute(`UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID}); e != nil {
			return authoritativeMutation{}, e
		}
	}
	hours := i64(item.SpatialKey["open_hours"])
	if hours <= 0 {
		hours = 2
	}
	now := nowSeconds()
	key := fmt.Sprintf("spatial_key:%s:%d", rid, timeNowUnixNano())
	payload := map[string]any{"definition_id": "spatial_key", "realm_id": rid, "opened_by": userID}
	enc, _ := json.Marshal(payload)
	loc := fmt.Sprint(ch["location"])
	ends := now + float64(hours*3600)
	if _, e = conn.Execute(`INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES(?,'','secret_realm',?,?,?,1,?,?)`, []any{key, realm.Name, loc, string(enc), now, ends}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"event_key": key, "realm_id": rid, "name": realm.Name, "description": realm.Description, "location": loc, "ends_at": ends, "consumed": consumed}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "secret_realm", EventType: "spatial_key.use", EntityType: "world_event", EntityID: key, GameMinute: p.GameMinute, Payload: out}}, nil
}

func personalWorldCreateActionGo(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p personalWorldCreatePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.Name = strings.TrimSpace(p.Name)
	if p.Name == "" {
		return authoritativeMutation{}, errors.New("world name is required")
	}
	rr := []rune(p.Name)
	if len(rr) > 60 {
		p.Name = string(rr[:60])
	}
	r, e := conn.Execute(`SELECT realm_index FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	r, e = conn.Execute(`SELECT comprehension FROM law_progress WHERE user_id=? AND law_id='space'`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	comp := int64(0)
	if lp := firstRowMap(r); lp != nil {
		comp = i64(lp["comprehension"])
	}
	if comp < 100 || i64(ch["realm_index"]) < 30 {
		return authoritativeMutation{}, errors.New("world creation requires Space Law — Essence/Origin (100%) and at least Dao Saint realm")
	}
	r, e = conn.Execute(`SELECT 1 AS ok FROM personal_worlds WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if firstRowMap(r) != nil {
		return authoritativeMutation{}, errors.New("you have already stabilized a personal world")
	}
	key := fmt.Sprintf("personal_world:%d", userID)
	now := nowSeconds()
	if _, e = conn.Execute(`INSERT INTO personal_worlds(user_id,location_key,name,created_at,updated_at) VALUES(?,?,?,?,?)`, []any{userID, key, p.Name, now, now}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"user_id": userID, "location_key": key, "name": p.Name, "stability": int64(1), "laws": map[string]any{}, "access_mode": "private"}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "personal_world", EventType: "personal_world.create", EntityType: "personal_world", EntityID: key, GameMinute: p.GameMinute, Payload: out}}, nil
}
func personalWorldRuleActionGo(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p personalWorldRulePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.Rule = strings.TrimSpace(p.Rule)
	p.Definition = strings.TrimSpace(p.Definition)
	if p.Rule == "" {
		return authoritativeMutation{}, errors.New("rule is required")
	}
	rr := []rune(p.Rule)
	if len(rr) > 40 {
		p.Rule = string(rr[:40])
	}
	dd := []rune(p.Definition)
	if len(dd) > 300 {
		p.Definition = string(dd[:300])
	}
	r, e := conn.Execute(`SELECT location_key,name,laws_json,stability FROM personal_worlds WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	w := firstRowMap(r)
	if w == nil {
		return authoritativeMutation{}, errors.New("you have not created a personal world")
	}
	rules := map[string]any{}
	_ = json.Unmarshal([]byte(fmt.Sprint(w["laws_json"])), &rules)
	rules[p.Rule] = p.Definition
	enc, _ := json.Marshal(rules)
	st := min64(100, i64(w["stability"])+1)
	if _, e = conn.Execute(`UPDATE personal_worlds SET laws_json=?,stability=?,updated_at=? WHERE user_id=?`, []any{string(enc), st, nowSeconds(), userID}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"location_key": w["location_key"], "name": w["name"], "stability": st, "laws": rules, "rule": p.Rule, "definition": p.Definition}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "personal_world", EventType: "personal_world.set_rule", EntityType: "personal_world", EntityID: fmt.Sprint(w["location_key"]), GameMinute: p.GameMinute, Payload: out}}, nil
}
func personalWorldMoveActionGo(conn *storage.Conn, userID int64, raw json.RawMessage, leave bool) (authoritativeMutation, error) {
	var p struct {
		GameMinute int64 `json:"game_minute"`
	}
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	r, e = conn.Execute(`SELECT location_key,name FROM personal_worlds WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	w := firstRowMap(r)
	if w == nil {
		return authoritativeMutation{}, errors.New("you have no personal world")
	}
	dest := fmt.Sprint(w["location_key"])
	et := "personal_world.enter"
	if leave {
		if fmt.Sprint(ch["location"]) != dest {
			return authoritativeMutation{}, errors.New("you are not inside your personal world")
		}
		dest = "Greenriver Town"
		et = "personal_world.leave"
	}
	if _, e = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{dest, nowSeconds(), userID}); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"location": dest, "name": w["name"]}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "personal_world", EventType: et, EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
