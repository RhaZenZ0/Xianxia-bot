package simulation

// What the world's own people turn up, and where it goes.
//
// Forty-eight auction houses, every one with a steward standing in it, and the
// only way a lot ever appeared on any floor was a player walking in and listing
// one. A floor nobody played on was an empty room. Merchants were wired to the
// auctions in the buy direction only - they bid, they take unsold lots - so the
// world could consume treasure and never produce any.
//
// It produces now. A grave-robber, a beast hunter, a herb-gatherer or a
// prospector turns something up on their own time, and what happens next is
// decided by two things: whether the find is legal, and whether the finder can
// read it. Contraband goes to the night market. Something they understand goes
// under the hammer with a reserve. Something they *don't* goes under the hammer
// blind - the house grades it by eye and says no more than that - which is the
// bargain every treasure-hunting player in this genre is actually looking for.
//
// Nothing here needs an NPC inventory table, and that is deliberate: a find is
// resolved and consigned in one pass, so there is no half-owned item to
// reconcile if a tick is missed or replayed.

import (
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const (
	// How likely an NPC is to turn something up at all, by trade. A
	// grave-robber is looking; a gate guard is not.
	findChanceDigger   = 22
	findChanceHunter   = 14
	findChanceOrdinary = 3
	// No more than this many finds in one tick, for the same reason travel is
	// capped: a world where thirty treasures surface overnight is a fire sale,
	// not a living world.
	findCap = 6
	// A find is worth listing only if a floor has room for it.
	consignHours = 8
)

// npcFindChance is the trade, not the person. Matched the way every other
// profession rule in this package is matched, because `profession` is free
// text the content author wrote.
func npcFindChance(profession string) int64 {
	p := strings.ToLower(profession)
	switch {
	case strings.Contains(p, "grave"), strings.Contains(p, "tomb"),
		strings.Contains(p, "relic"), strings.Contains(p, "scaveng"),
		strings.Contains(p, "prospect"), strings.Contains(p, "digger"),
		strings.Contains(p, "miner"), strings.Contains(p, "salvage"):
		return findChanceDigger
	case strings.Contains(p, "hunter"), strings.Contains(p, "forager"),
		strings.Contains(p, "herbalist"), strings.Contains(p, "gatherer"),
		strings.Contains(p, "scout"), strings.Contains(p, "wanderer"):
		return findChanceHunter
	}
	return findChanceOrdinary
}

// findableItems is the pool a find is drawn from: the things a floor would
// hold an auction for. Sorted, because map iteration order must not decide
// what the world produces.
func findableItems(catalog worlddata.Catalog) []string {
	out := []string{}
	for id, item := range catalog.Items {
		interest := strings.ToLower(strings.TrimSpace(item.AuctionInterest))
		if interest == "special" || interest == "legendary" {
			out = append(out, id)
		}
	}
	sort.Strings(out)
	return out
}

// knowsWhatTheyFound decides whether the finder can read their own find.
//
// A legendary relic is beyond an ordinary scavenger; a merchant or broker
// prices things for a living and a high-realm cultivator has seen one before.
// Anyone who cannot read it consigns it blind, which is where the whole
// appraisal half of this comes from.
func knowsWhatTheyFound(profession string, realmIndex int64, interest string) bool {
	p := strings.ToLower(profession)
	expert := strings.Contains(p, "merchant") || strings.Contains(p, "broker") ||
		strings.Contains(p, "appraiser") || strings.Contains(p, "steward") ||
		strings.Contains(p, "alchemist") || strings.Contains(p, "smith")
	need := int64(4)
	if strings.ToLower(strings.TrimSpace(interest)) == "legendary" {
		need = 10
	}
	if expert {
		need -= 3
	}
	return realmIndex >= need
}

// gradeBandFor is all a house will say about a lot it cannot vouch for: the
// grade it carries by eye, and nothing about what it does.
func gradeBandFor(interest string) string {
	if strings.ToLower(strings.TrimSpace(interest)) == "legendary" {
		return "legendary or near it"
	}
	return "spiritual grade or better"
}

// npcConsignments is the tick: some of the world's people find something, and
// it reaches a floor or a night market the same day.
func (r *Runner) npcConsignments(conn *storage.Conn, steps, gm int64) (string, error) {
	if !simTableExists(conn, "auctions") || !simTableExists(conn, "npc_civilization_state") {
		return "no auction floor", nil
	}
	pool := findableItems(r.World)
	if len(pool) == 0 {
		return "nothing worth a hammer", nil
	}
	res, err := conn.Execute(`SELECT npc_name,current_location,world_name,profession,realm_index
        FROM npc_civilization_state WHERE status='alive' ORDER BY npc_name`, nil)
	if err != nil {
		return "", err
	}
	consigned, smuggled := int64(0), int64(0)
	for _, row := range res.Rows {
		if consigned+smuggled >= findCap {
			break
		}
		name := fmt.Sprint(row[0])
		where, world := fmt.Sprint(row[1]), fmt.Sprint(row[2])
		profession, realmIndex := fmt.Sprint(row[3]), i64(row[4])

		chance := min64(60, npcFindChance(profession)*max1(min64(3, steps)))
		roll, err := gamerng.Intn(100)
		if err != nil {
			return "", err
		}
		if int64(roll) >= chance {
			continue
		}
		pick, err := gamerng.Intn(len(pool))
		if err != nil {
			return "", err
		}
		itemID := pool[pick]
		item := r.World.Items[itemID]

		legal := strings.ToLower(strings.TrimSpace(item.LegalStatus))
		if legal == "forbidden" || legal == "contraband" || legal == "restricted" {
			ok, err := r.smuggleToNightMarket(conn, world, itemID, item, gm)
			if err != nil {
				return "", err
			}
			if ok {
				smuggled++
				r.recordFind(conn, name, where, world, itemID, item, "night market", gm)
			}
			continue
		}
		listed, err := r.consignToNearestHouse(conn, name, where, itemID, item, profession, realmIndex, gm)
		if err != nil {
			return "", err
		}
		if listed {
			consigned++
			r.recordFind(conn, name, where, world, itemID, item, "auction", gm)
		}
	}
	return fmt.Sprintf("%d consigned, %d smuggled", consigned, smuggled), nil
}

// consignToNearestHouse puts the find on the floor of the city the finder is
// standing in or beside, if that floor has room for it.
func (r *Runner) consignToNearestHouse(conn *storage.Conn, npcName, where, itemID string, item worlddata.Item, profession string, realmIndex, gm int64) (bool, error) {
	house, houseID, ok := r.houseNear(where)
	if !ok {
		return false, nil
	}
	if house.MaxActiveLots > 0 {
		countRes, err := conn.Execute(`SELECT COUNT(*) FROM auctions WHERE house_id=? AND active=1`, []any{houseID})
		if err != nil {
			return false, err
		}
		if len(countRes.Rows) > 0 && i64(countRes.Rows[0][0]) >= house.MaxActiveLots {
			return false, nil
		}
	}
	currency := strings.TrimSpace(house.DefaultCurrency)
	if currency == "" {
		currency = "low_spirit_stone"
	}
	appraised := knowsWhatTheyFound(profession, realmIndex, item.AuctionInterest)
	reserve := auctionReserve(item, appraised)
	band := ""
	if !appraised {
		band = gradeBandFor(item.AuctionInterest)
	}
	now := nowFloat()
	minutes := int64(consignHours * 60)
	if house.MaxLotMinutes > 0 && minutes > house.MaxLotMinutes {
		minutes = house.MaxLotMinutes
	}
	// NULL, not 0: `seller_user_id` is foreign-keyed to `characters`, so the
	// only value meaning "no character behind this lot" is the absent one.
	// This wrote 0 from rc.15 to rc.28 and was refused every time, which threw
	// the whole tick before `sect_politics` and everything after it could run
	// (schema 50).
	_, err := conn.Execute(`INSERT INTO auctions(house_id,seller_user_id,seller_npc_name,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,anonymous,active,appraised,grade_band,created_at,ends_at)
        VALUES(?,NULL,?,?,1,?,?,0,NULL,0,1,?,?,?,?)`,
		[]any{houseID, npcName, itemID, currency, reserve, boolInt(appraised), band, now, now + float64(minutes*60)})
	return err == nil, err
}

// auctionReserve is what the house asks to open the bidding. A lot nobody can
// vouch for opens far lower - that discount is the whole reason a player
// gambles on one.
func auctionReserve(item worlddata.Item, appraised bool) int64 {
	base := item.BasePrice
	if base <= 0 {
		base = max64(8, item.SectValue*8)
	}
	reserve := base / 4
	if !appraised {
		reserve = base / 16
	}
	return max64(1, reserve)
}

// smuggleToNightMarket puts a contraband find on the world's black market
// rather than a legal floor, where the same finder would be arrested for it.
func (r *Runner) smuggleToNightMarket(conn *storage.Conn, world, itemID string, item worlddata.Item, gm int64) (bool, error) {
	if !simTableExists(conn, "black_market_stock") {
		return false, nil
	}
	postRes, err := conn.Execute(`SELECT heat FROM black_market_posts WHERE world_name=? AND active=1`, []any{world})
	if err != nil || len(postRes.Rows) == 0 {
		return false, err
	}
	heat := i64(postRes.Rows[0][0])
	base := item.BasePrice
	if base <= 0 {
		base = max64(8, item.SectValue*8)
	}
	price := base + base*heat/100
	if price < 1 {
		price = 1
	}
	legal := strings.ToLower(strings.TrimSpace(item.LegalStatus))
	if legal == "" {
		legal = "restricted"
	}
	// The columns are the ones the table actually has. The first version of
	// this wrote `location` and `price`, which `black_market_stock` does not
	// carry, and omitted `currency_id`/`unit_price`, which are NOT NULL - so
	// every smuggled find raised a bare SQL error that aborted the whole
	// consignment tick, legal lots included. It passed because the test
	// fixture below had invented a matching-but-wrong schema of its own; the
	// fixture is now the production one.
	_, err = conn.Execute(`INSERT INTO black_market_stock(world_name,item_id,currency_id,unit_price,quantity,legal_status,updated_at)
        VALUES(?,?,?,?,1,?,?) ON CONFLICT(world_name,item_id) DO UPDATE SET quantity=black_market_stock.quantity+1,unit_price=excluded.unit_price,updated_at=excluded.updated_at`,
		[]any{world, itemID, blackMarketCurrency(world), price, legal, nowFloat()})
	return err == nil, err
}

// blackMarketCurrency is the world's own tier-1 coin, the same one the
// rotation stocks a post in (`worldCurrency`, bootstrap.go:91).
func blackMarketCurrency(world string) string {
	return worldCurrency(world)
}

// houseNear is the auction floor of the city the finder is in or standing
// outside. A finder inside the house itself counts too.
func (r *Runner) houseNear(where string) (worlddata.AuctionHouse, string, bool) {
	for id, house := range r.World.AuctionHouses {
		if house.Location == where || house.EntranceLocation == where {
			return house, id, true
		}
	}
	// Inside a district, gate or shop of the city the house fronts.
	if loc, ok := r.World.Locations[where]; ok && loc.OutsideLocation != "" {
		for id, house := range r.World.AuctionHouses {
			if house.EntranceLocation == loc.OutsideLocation {
				return house, id, true
			}
		}
	}
	return worlddata.AuctionHouse{}, "", false
}

// recordFind is the rumour. A treasure surfacing is exactly the kind of thing
// a town talks about, and `world_history_events` is what the narrator reads.
func (r *Runner) recordFind(conn *storage.Conn, npcName, where, world, itemID string, item worlddata.Item, destination string, gm int64) {
	if !simTableExists(conn, "world_history_events") {
		return
	}
	name := item.Name
	if name == "" {
		name = itemID
	}
	title := npcName + " turns up " + name
	summary := fmt.Sprintf("%s came by %s and put it to the %s at %s.", npcName, name, destination, where)
	source := fmt.Sprintf("npc_find:%s:%s:%d", npcName, itemID, gm)
	now := nowFloat()
	_, _ = conn.Execute(`INSERT INTO world_history_events(
        source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
        actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
        related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?, 'public', ?,?,'', 'npc',?,?, 'item',?,?, NULL,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO NOTHING`,
		[]any{source, "treasure_found", title, summary, 45, where, world,
			npcName, npcName, itemID, name, npcName, "treasure found " + destination, gm, "{}", now, now})
}

func boolInt(v bool) int64 {
	if v {
		return 1
	}
	return 0
}
