package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The birth family's send-off (v1.0.0-rc.15).
//
// Thirteen households with a hand-tuned Wealth from 26 to 82, and every one of
// them used to hand a new cultivator the same two spirit herbs and one spirit
// iron - so what a family was worth bought their child exactly nothing on the
// way out of the door. Each sends its own flying artifact now, no two the
// same, and the two wealthiest send one that is still carrying its rider at
// Core Formation.

// sendoffDB is the shop fixture with a birth_families table wide enough for
// the support action to read: batch4's is two columns, because no native test
// had gone through a household before this one.
func sendoffDB(t *testing.T) string {
	t.Helper()
	path := setupShopDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
DROP TABLE IF EXISTS birth_families;
CREATE TABLE birth_families(family_id INTEGER PRIMARY KEY AUTOINCREMENT,family_name TEXT NOT NULL DEFAULT '',surname TEXT NOT NULL DEFAULT '',archetype TEXT NOT NULL,tier INTEGER NOT NULL DEFAULT 1,wealth INTEGER NOT NULL DEFAULT 20,influence INTEGER NOT NULL DEFAULT 10,stability INTEGER NOT NULL DEFAULT 60,alignment_bias INTEGER NOT NULL DEFAULT 0,location TEXT NOT NULL DEFAULT '',bloodline_purity INTEGER NOT NULL DEFAULT 0,starter_key TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL DEFAULT 0);
DROP TABLE IF EXISTS character_birth_family;
CREATE TABLE character_birth_family(user_id INTEGER PRIMARY KEY,family_id INTEGER NOT NULL,birth_order INTEGER NOT NULL DEFAULT 1,generation INTEGER NOT NULL DEFAULT 1,last_support_game_minute INTEGER NOT NULL DEFAULT -999999999);
CREATE TABLE IF NOT EXISTS martial_clan_branches(family_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'active',loyalty INTEGER NOT NULL DEFAULT 50);
CREATE TABLE IF NOT EXISTS martial_clan_retainers(family_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'active',loyalty INTEGER NOT NULL DEFAULT 50,members INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS martial_clan_relations(family_id INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,relation_type TEXT NOT NULL DEFAULT 'alliance',relation_score INTEGER NOT NULL DEFAULT 0);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func sendoffFamily(t *testing.T, path, archetype, starter string, tier, wealth int64) int64 {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO birth_families(family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,bloodline_purity,starter_key,updated_at) VALUES(?,'Test',?,?,?,40,50,0,'Greenriver Town',0,?,0)`,
		"House "+archetype, archetype, tier, wealth, starter)
	return i64(actionScalar(t, path, `SELECT family_id FROM birth_families WHERE starter_key=?`, starter))
}

// sendOut is one household handing its heirloom over, committed.
func sendOut(t *testing.T, path string, userID, familyID int64, archetype string, gameMinute int64) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	got, err := grantBirthFamilySendoffTx(conn, districtCatalog(t), userID, familyID, archetype, gameMinute, 1)
	if err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	return got
}

func TestEveryHouseholdSendsItsChildOutWithSomethingOfItsOwn(t *testing.T) {
	catalog := districtCatalog(t)
	if len(catalog.BirthFamilySendoff) == 0 {
		t.Fatal("no household sends anything")
	}
	seen := map[string]string{}
	for _, arch := range birthFamilyArchetypes {
		sendoff, ok := catalog.BirthFamilySendoff[arch.ID]
		if !ok {
			t.Fatalf("%s sends its children out with nothing", arch.ID)
		}
		item, ok := catalog.Items[sendoff.Item]
		if !ok {
			t.Fatalf("%s sends out %q, which is not an item", arch.ID, sendoff.Item)
		}
		if item.Flight < travelFlightRealm {
			t.Fatalf("%s sends out %q, which does not fly (%d)", arch.ID, sendoff.Item, item.Flight)
		}
		if other, clash := seen[sendoff.Item]; clash {
			t.Fatalf("%s and %s hand out the same object (%s)", other, arch.ID, sendoff.Item)
		}
		seen[sendoff.Item] = arch.ID
		// The gift tracks the purse the household already had written on it.
		want := int64(3)
		if arch.Wealth >= 60 {
			want = 5
		}
		if item.Flight != want {
			t.Fatalf("%s (wealth %d) sends flight %d, want %d", arch.ID, arch.Wealth, item.Flight, want)
		}
		if strings.TrimSpace(sendoff.Line) == "" {
			t.Fatalf("%s hands it over without a word", arch.ID)
		}
	}
	if len(seen) != len(birthFamilyArchetypes) {
		t.Fatalf("%d households, %d distinct heirlooms", len(birthFamilyArchetypes), len(seen))
	}
}

// A cultivator leaves home carrying it, and it gets them off the road on their
// very first journey at realm 0 - which is the whole point of one.
func TestANewCultivatorLeavesHomeCarryingItAndFliesOnIt(t *testing.T) {
	path := sendoffDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	fid := sendoffFamily(t, path, "noble_martial_clan", "test:noble", 4, 82)

	got := sendOut(t, path, 42, fid, "noble_martial_clan", 100)
	if got == nil {
		t.Fatal("the household sent them out with nothing")
	}
	want := catalog.BirthFamilySendoff["noble_martial_clan"].Item
	if fmt.Sprint(got["item_id"]) != want {
		t.Fatalf("sent out with %v, want %s", got["item_id"], want)
	}
	if q := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, want)); q != 1 {
		t.Fatalf("not in the bag: %d", q)
	}

	batch4Exec(t, path, `UPDATE characters SET spirit_stones=500,vitality=100,realm_index=0 WHERE user_id=42`)
	quiet := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadEncounterIntn = quiet }()
	missed := roadSiteDiscoveryIntn
	roadSiteDiscoveryIntn = func(n int) (int, error) { return n - 1, nil }
	defer func() { roadSiteDiscoveryIntn = missed }()
	batch4SetCanonicalGameMinute(t, path, 3000)
	travel := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{"destination": "Riverguard City", "mode": "known"}))
	if fmt.Sprint(travel["travel_mode"]) != "flying" {
		t.Fatalf("a clan sword should get an heir off the road at realm 0: %v", travel["travel_mode"])
	}
	if fmt.Sprint(travel["travel_mount"]) != catalog.Items[want].FlightName {
		t.Fatalf("travel_mount=%v", travel["travel_mount"])
	}
}

// Once per household, and no more - guarded on item_provenance rather than a
// new column, and keyed on the family so a new life still gets its own.
func TestTheHouseholdSendsYouOutOnlyOnce(t *testing.T) {
	path := sendoffDB(t)
	catalog := districtCatalog(t)
	fid := sendoffFamily(t, path, "tomb_watch_clan", "test:tomb", 2, 31)

	for attempt, wantGift := range []bool{true, false} {
		got := sendOut(t, path, 42, fid, "tomb_watch_clan", 100)
		if (got != nil) != wantGift {
			t.Fatalf("attempt %d: gift=%v want=%v", attempt+1, got != nil, wantGift)
		}
	}
	item := catalog.BirthFamilySendoff["tomb_watch_clan"].Item
	if q := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, item)); q != 1 {
		t.Fatalf("asking twice got %d", q)
	}

	// A new life is a new household, and it sends its own child out: the
	// earlier incarnation's heirloom must not block it.
	reborn := sendoffFamily(t, path, "nether_market_house", "test:nether", 2, 55)
	got := sendOut(t, path, 42, reborn, "nether_market_house", 200)
	if got == nil {
		t.Fatal("a rebirth into a new household got nothing")
	}
	if fmt.Sprint(got["item_id"]) == item {
		t.Fatalf("the new household handed out the old one's heirloom: %v", got["item_id"])
	}
}

// Coming home and asking is the door for a character who predates the
// send-off, and it opens once.
func TestComingHomeAndAskingGetsTheHeirloomOnce(t *testing.T) {
	path := sendoffDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	fid := sendoffFamily(t, path, "sword_hall_family", "test:sword", 3, 90)
	batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute) VALUES(42,?,1,1,-999999999)`, fid)

	batch4SetCanonicalGameMinute(t, path, 5000)
	first := batch4Result(t, batch4Apply(t, path, world, "family.support", 1, map[string]any{"cooldown_game_minutes": 100}))
	gift, _ := first["family_sendoff"].(map[string]any)
	if gift == nil {
		t.Fatalf("coming home got no heirloom: %v", first)
	}
	want := catalog.BirthFamilySendoff["sword_hall_family"].Item
	if fmt.Sprint(gift["item_id"]) != want {
		t.Fatalf("the sword hall handed over %v", gift["item_id"])
	}

	batch4SetCanonicalGameMinute(t, path, 9000)
	second := batch4Result(t, batch4Apply(t, path, world, "family.support", 2, map[string]any{"cooldown_game_minutes": 100}))
	if _, again := second["family_sendoff"]; again {
		t.Fatal("the household handed out a second heirloom")
	}
	if q := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, want)); q != 1 {
		t.Fatalf("quantity after two visits=%d", q)
	}
}

// Both ghost households had no case in the support switch at all and fell
// through to the plain default, so the two families that trade in funeral
// goods handed over one ordinary recovery pill.
func TestTheGhostHouseholdsSupportTheirOwn(t *testing.T) {
	path := sendoffDB(t)
	world := batch4WorldPath(t)
	for i, spec := range []struct{ archetype, starter, wants string }{
		{"nether_market_house", "test:ghost_nether", "talisman_paper"},
		{"tomb_watch_clan", "test:ghost_tomb", "talisman_paper"},
	} {
		user := int64(80 + i)
		batch4Exec(t, path, `INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,life_status,karma_score,qi,qi_max,vitality,vitality_max) VALUES(?,'Ghost Test','neutral','Rogue Cultivator','Water Root','Greenriver Town','{}',0,1,0,0,1,0,'alive',0,10,10,10,10)`, user)
		fid := sendoffFamily(t, path, spec.archetype, spec.starter, 3, 90)
		batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute) VALUES(?,?,1,1,-999999999)`, user, fid)

		batch4SetCanonicalGameMinute(t, path, int64(6000+i*1000))
		raw, _ := json.Marshal(map[string]any{"cooldown_game_minutes": 100})
		out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("ghost-support-%d", i), Operation: "family.support", ActorID: user, Payload: raw})
		if err != nil {
			t.Fatal(err)
		}
		items, _ := batch4Result(t, out)["items"].(map[string]int64)
		if _, ok := items[spec.wants]; !ok {
			t.Fatalf("%s support package is the plain default: %v", spec.archetype, items)
		}
	}
}
