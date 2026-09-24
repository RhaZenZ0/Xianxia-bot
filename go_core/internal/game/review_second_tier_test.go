package game

// The v1.2.1 review's second tier of claims, each read and held (v1.2.3).

import (
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A commission is completed by doing it. The action used to pay whatever
// outcome the payload named.
func TestAPlayerCannotCompleteACommissionByAsking(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "variant_index": 1, "game_minute": 1000})
	before, _ := characterPurse(t, path, 42)
	_, err := commissionApply(t, path, "commission.resolve", 42, 2,
		map[string]any{"quest_key": "commission_crate", "outcome": "completed", "game_minute": 1200})
	if err == nil || !strings.Contains(err.Error(), "completed by doing it") {
		t.Fatalf("a client naming \"completed\" was paid: err=%v", err)
	}
	if after, _ := characterPurse(t, path, 42); after != before {
		t.Fatalf("the purse moved from %d to %d on a refused completion", before, after)
	}
	if got := questRow(t, path, 42, "commission_crate")["status"]; got != "active" {
		t.Fatalf("status=%v, want still active", got)
	}
}

// A market counter never undercuts what a keeper pays and never pays what a
// shelf asks, in either direction and at any index.
func TestAMarketCounterStaysInsideTheShopsBand(t *testing.T) {
	catalog := shopCatalog(t)
	checked := 0
	for itemID, item := range catalog.Items {
		if !worlddata.MarketTradeable(item.MarketExcluded, item.SpatialKey != nil, item.AuctionInterest) {
			continue
		}
		base := item.SectValue
		if base < 1 {
			base = 5
		}
		for _, currency := range []string{"low_spirit_stone", "low_spirit_crystal", "low_spirit_jade", "low_spirit_star"} {
			for _, index := range []float64{0.5, 1.0, 3.0} {
				buy := marketUnitPrice(catalog, itemID, currency, base, index, true)
				if floor, ok := highestKeeperBuy(catalog, itemID, currency); ok && buy < floor {
					t.Fatalf("a market sells %s for %d and a keeper pays %d: buy at the market, sell to the counter", itemID, buy, floor)
				}
				sell := marketUnitPrice(catalog, itemID, currency, base, index, false)
				if shelf, ok := cheapestShelfPrice(catalog, itemID, currency); ok && sell >= shelf {
					t.Fatalf("a market pays %d for %s and a shelf sells it for %d: buy off the shelf, sell to the market", sell, itemID, shelf)
				}
				if sell < 1 || buy < 1 {
					t.Fatalf("%s priced at buy=%d sell=%d", itemID, buy, sell)
				}
				checked++
			}
		}
	}
	if checked < 100 {
		t.Fatalf("only %d prices were checked; the walk has stopped seeing the catalogue", checked)
	}
}

// A lot is read on the floor it stands on.
func TestALotIsReadOnItsOwnFloor(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := shopCatalog(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	houseID, house := "", ""
	for key, h := range catalog.AuctionHouses {
		if h.Location != "" {
			houseID, house = key, h.Location
			break
		}
	}
	if houseID == "" {
		t.Fatal("the catalogue carries no auction house; the reader is broken, not the tree")
	}
	batch4Exec(t, path, `INSERT INTO auctions(house_id,item_id,quantity,currency_id,starting_bid,active,ends_at) VALUES(?,'nine_echo_sword_tablet',1,'low_spirit_stone',10,1,9999999999)`, houseID)
	_, err := batch4ApplyErr(path, world, "appraisal.read", 42, 1, map[string]any{"auction_id": 1})
	if err == nil || !strings.Contains(err.Error(), "not on this floor") {
		t.Fatalf("a lot was read from a field, got %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, house)
	if _, err := batch4ApplyErr(path, world, "appraisal.read", 42, 2, map[string]any{"auction_id": 1}); err != nil {
		t.Fatalf("standing on the floor, the lot refused: %v", err)
	}
}

// What time a sect was discovered is the engine's, not the caller's.
func TestASectDiscoveryIsStampedWithTheCanonicalMinute(t *testing.T) {
	path := setupIdentityDB(t)
	batch4SetCanonicalGameMinute(t, path, 4000)
	if _, err := discoverApply(t, path, 42, map[string]any{
		"sects": []any{"Azure Cloud Sect"}, "discovery_kind": "exploration", "game_minute": 9999999,
	}); err != nil {
		t.Fatal(err)
	}
	got := identityScalar(t, path, `SELECT discovered_game_minute FROM character_sect_discoveries WHERE user_id=42 AND sect_name='Azure Cloud Sect'`)
	if got != 4000 {
		t.Fatalf("discovered_game_minute=%d; the caller's 9999999 was believed", got)
	}
}

// Every upper-world house sends its child off with a tradition the content
// file authors.
func TestEveryUpperWorldHouseHasASendoff(t *testing.T) {
	catalog := shopCatalog(t)
	if len(catalog.BirthFamilySendoff) == 0 {
		t.Fatal("no send-offs parsed; the reader is broken, not the tree")
	}
	checked := 0
	for world, templates := range upperSamsaraFamilies {
		for _, template := range templates {
			family := BirthFamily{ID: template.ID, Archetype: template.Archetype}
			key := sendoffArchetypeFor(catalog, family)
			if _, ok := catalog.BirthFamilySendoff[key]; !ok {
				t.Fatalf("%s / %s (%s) is sent off as %q, which no birth_family_sendoff entry carries: a rebirth there gets no heirloom, trade or tutoring", world, template.ID, template.Archetype, key)
			}
			// v1.3.0: the entry is the house's own, not a Mortal counterpart
			// read off the id.
			if key != template.Archetype {
				t.Fatalf("%s / %s is sent off as %q rather than as itself; the upper-world send-offs are authored now", world, template.ID, key)
			}
			checked++
		}
	}
	if checked < 20 {
		t.Fatalf("only %d templates checked", checked)
	}
	// A Mortal template is its own archetype and is left alone.
	if got := sendoffArchetypeFor(catalog, BirthFamily{ID: "alchemy_family", Archetype: "alchemy_family"}); got != "alchemy_family" {
		t.Fatalf("a Mortal house was re-pointed to %q", got)
	}
}

var _ = storage.ParseInt
