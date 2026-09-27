package game

import (
	"strings"
	"testing"
)

// Underworld Contacts had one source, a trade at a black-market post, and a
// trade needed the very trust it was meant to earn - so the door by reputation
// could never open (v1.11.1). A broker buys from a stranger now, and five
// underworld households send their children out already known.

func underworldPostDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS black_market_posts(world_name TEXT PRIMARY KEY,location TEXT NOT NULL,heat INTEGER NOT NULL DEFAULT 0,opens_game_minute INTEGER NOT NULL,closes_game_minute INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS black_market_stock(world_name TEXT NOT NULL,item_id TEXT NOT NULL,currency_id TEXT NOT NULL,unit_price INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,legal_status TEXT NOT NULL DEFAULT 'forbidden',updated_at REAL NOT NULL,PRIMARY KEY(world_name,item_id),FOREIGN KEY(world_name) REFERENCES black_market_posts(world_name) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS item_provenance(provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,source_type TEXT NOT NULL,source_key TEXT NOT NULL DEFAULT '',ownership_mark TEXT NOT NULL DEFAULT '',legal_status TEXT NOT NULL DEFAULT 'clean',authenticity INTEGER NOT NULL DEFAULT 100,tracking_strength INTEGER NOT NULL DEFAULT 0,acquired_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL DEFAULT 0)`)
	// Heat 20 keeps a buy below the watch's notice (40), so no roll decides it.
	batch4Exec(t, path, `INSERT INTO black_market_posts(world_name,location,heat,opens_game_minute,closes_game_minute,active,created_at,updated_at) VALUES('Mortal World','Greenriver Town',20,0,999999999,1,0,0)`)
	batch4Exec(t, path, `INSERT INTO black_market_stock(world_name,item_id,currency_id,unit_price,quantity,legal_status,updated_at) VALUES('Mortal World','hundred_year_peach','low_spirit_stone',100,5,'restricted',0)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'hundred_year_peach',3) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=3`)
	batch4Exec(t, path, `UPDATE characters SET karma_score=50 WHERE user_id=42`)
	return path
}

func underworldStanding(t *testing.T, path string) int64 {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT COALESCE((SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key='Underworld Contacts'),0)`))
}

func TestABrokerBuysFromAStrangerAndThatBuildsTrust(t *testing.T) {
	path := underworldPostDB(t)
	world := batch4WorldPath(t)
	if _, err := batch4ApplyErr(path, world, "black_market.trade", 42, 1, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": true}); err == nil || !strings.Contains(err.Error(), "will buy from a stranger") {
		t.Fatalf("a stranger must be refused a purchase, and told the way in: %v", err)
	}
	sold := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 2, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": false}))
	if sold["access"] != "fencing as a stranger" || i64(sold["underworld_reputation"]) != 1 || i64(sold["trust_reputation"]) != blackMarketTrustReputation {
		t.Fatalf("a stranger fences and the fence is the first point of trust: %v", sold)
	}
	if got := underworldStanding(t, path); got != 1 {
		t.Fatalf("the fence did not raise Underworld Contacts: %d", got)
	}
	// One fence short of trust, then the fence that earns it.
	batch4Exec(t, path, `UPDATE faction_reputation SET score=? WHERE user_id=42 AND faction_key='Underworld Contacts'`, blackMarketTrustReputation-1)
	batch4Apply(t, path, world, "black_market.trade", 3, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": false})
	bought := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 4, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": true}))
	if bought["access"] != "underworld contacts" {
		t.Fatalf("fifteen fences are the trust to buy: %v", bought["access"])
	}
}

func TestAnUnderworldHouseholdSendsItsChildOutKnown(t *testing.T) {
	catalog := districtCatalog(t)
	houses := 0
	for archetype, sendoff := range catalog.BirthFamilySendoff {
		if n := sendoff.Reputation["Underworld Contacts"]; n > 0 {
			houses++
			if n < blackMarketTrustReputation {
				t.Fatalf("%s grants Underworld Contacts %d, short of the %d a broker sells at", archetype, n, blackMarketTrustReputation)
			}
		}
	}
	if houses < 1 {
		t.Fatal("no household grants Underworld Contacts; the content read is broken, not the tree")
	}
	if catalog.BirthFamilySendoff["hidden_weapon_family"].Reputation["Underworld Contacts"] <= 0 {
		t.Fatal("the Hidden-Weapon family's discreet underworld contacts are prose again")
	}

	path := sendoffDB(t)
	first := sendoffFamily(t, path, "hidden_weapon_family", "uw-1", 3, 50)
	out := sendOut(t, path, 42, first, "hidden_weapon_family", 0)
	if got := underworldStanding(t, path); got != 15 {
		t.Fatalf("the household's contacts did not come with the child: %d (%v)", got, out["reputation"])
	}
	// A floor, not a grant: a second household of the same kind adds nothing
	// to standing already earned past it.
	batch4Exec(t, path, `UPDATE faction_reputation SET score=20 WHERE user_id=42 AND faction_key='Underworld Contacts'`)
	second := sendoffFamily(t, path, "hidden_weapon_family", "uw-2", 3, 50)
	again := sendOut(t, path, 42, second, "hidden_weapon_family", 0)
	if got := underworldStanding(t, path); got != 20 {
		t.Fatalf("a second household stacked its contacts: %d", got)
	}
	if _, raised := again["reputation"]; raised {
		t.Fatalf("a floor already met must not be reported as raised: %v", again["reputation"])
	}
}
