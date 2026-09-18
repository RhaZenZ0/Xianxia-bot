package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A house worth coming back to (v1.0.0-rc.32). These hold what the household
// asks for at home and refuses elsewhere, what the coffers do, that the
// teaching only ever rises, that errands come one at a time and once, what an
// errand brought home leaves behind, the door's town gate, and the two
// talismans that make the round trip.

// householdDB is the tutoring fixture with the household's tables in
// production's shape: the coffers and the chronicle live on birth_families,
// the quests carry seed_json and terms_json, and the scene remembers a mark.
func householdDB(t *testing.T) string {
	t.Helper()
	path := tutoringDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
DROP TABLE IF EXISTS birth_families;
CREATE TABLE birth_families(family_id INTEGER PRIMARY KEY AUTOINCREMENT, family_name TEXT NOT NULL, surname TEXT NOT NULL DEFAULT '', archetype TEXT NOT NULL, tier INTEGER NOT NULL DEFAULT 1, wealth INTEGER NOT NULL DEFAULT 20, influence INTEGER NOT NULL DEFAULT 10, stability INTEGER NOT NULL DEFAULT 60, alignment_bias INTEGER NOT NULL DEFAULT 0, location TEXT NOT NULL, head_name TEXT NOT NULL DEFAULT '', treasury_balance INTEGER NOT NULL DEFAULT 0, history_json TEXT NOT NULL DEFAULT '[]', bloodline_purity INTEGER NOT NULL DEFAULT 0, starter_key TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS quest_definitions(quest_key TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',source_type TEXT NOT NULL DEFAULT 'forge',source_key TEXT NOT NULL DEFAULT '',objectives_json TEXT NOT NULL DEFAULT '[]',rewards_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'draft',created_at REAL NOT NULL,updated_at REAL NOT NULL,giver_npc TEXT NOT NULL DEFAULT '',realm_band TEXT NOT NULL DEFAULT '',tier INTEGER NOT NULL DEFAULT 1,deadline_game_minutes INTEGER NOT NULL DEFAULT 0,variants_json TEXT NOT NULL DEFAULT '[]',seed_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS character_quests(user_id INTEGER NOT NULL,quest_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',progress_json TEXT NOT NULL DEFAULT '{}',accepted_game_minute INTEGER NOT NULL DEFAULT 0,completed_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL,commission INTEGER NOT NULL DEFAULT 0,deadline_game_minute INTEGER,variant_index INTEGER NOT NULL DEFAULT 0,resolved_game_minute INTEGER,terms_json TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,quest_key));
CREATE TABLE IF NOT EXISTS player_scene_state(user_id INTEGER PRIMARY KEY, physical_location TEXT NOT NULL DEFAULT '', scene_type TEXT NOT NULL DEFAULT 'world', scene_key TEXT NOT NULL DEFAULT '', scene_label TEXT NOT NULL DEFAULT '', channel_id INTEGER, metadata_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS battles(battle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active', player_hp INTEGER NOT NULL DEFAULT 1, player_hp_max INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

// householdFamily seeds a Forging house in Greenriver Town and makes
// character 42 its child, standing where the test says.
func householdFamily(t *testing.T, path string, wealth int64, where string) int64 {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO birth_families(family_name,archetype,tier,wealth,influence,stability,location,starter_key) VALUES('House Wen','martial_household',2,?,40,50,'Greenriver Town','test:wen')`, wealth)
	fid := i64(actionScalar(t, path, `SELECT family_id FROM birth_families WHERE starter_key='test:wen'`))
	batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute) VALUES(42,?,1,1,-999999999)`, fid)
	if where == "home" {
		where = birthFamilyHouseholdLocation(fid)
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, where)
	return fid
}

func householdApply(t *testing.T, path string, fn func(conn *storage.Conn) (authoritativeMutation, error)) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	mut, actionErr := fn(conn)
	if conn.InTransaction() {
		if actionErr != nil {
			_ = conn.Rollback()
		} else if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	if actionErr != nil {
		return nil, actionErr
	}
	out, _ := mut.Result.(map[string]any)
	return out, nil
}

func payload(v map[string]any) json.RawMessage {
	raw, _ := json.Marshal(v)
	return raw
}

func TestTheHouseholdGivesOnlyToSomebodyStandingInIt(t *testing.T) {
	path := householdDB(t)
	householdFamily(t, path, 42, "Greenriver Town")
	catalog := districtCatalog(t)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',100) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=100`)
	cases := map[string]func(conn *storage.Conn) (authoritativeMutation, error){
		"support": func(c *storage.Conn) (authoritativeMutation, error) {
			return familySupportActionGo(c, catalog, 42, payload(map[string]any{"cooldown_game_minutes": 100}))
		},
		"contribute": func(c *storage.Conn) (authoritativeMutation, error) {
			return familyContributeActionGo(c, worlddata.Catalog{}, 42, payload(map[string]any{"amount": 10}))
		},
		"tutor": func(c *storage.Conn) (authoritativeMutation, error) {
			return familyTutorActionGo(c, catalog, 42, payload(map[string]any{}))
		},
		"errand": func(c *storage.Conn) (authoritativeMutation, error) {
			return familyErrandActionGo(c, catalog, 42, payload(map[string]any{}))
		},
	}
	for name, fn := range cases {
		_, err := householdApply(t, path, fn)
		if err == nil || !strings.Contains(err.Error(), "asked for at home") {
			t.Errorf("%s away from home: err=%v, want the at-home refusal", name, err)
		}
	}
	if got := i64(actionScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)); got != 100 {
		t.Fatalf("a refused contribution still charged the wallet: %d", got)
	}
}

func TestAContributionFillsTheCoffersAndTheLedger(t *testing.T) {
	path := householdDB(t)
	fid := householdFamily(t, path, 42, "home")
	catalog := districtCatalog(t)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',300) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=300`)

	_, err := householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
		return familyContributeActionGo(c, worlddata.Catalog{}, 42, payload(map[string]any{"amount": 500}))
	})
	if err == nil || !strings.Contains(err.Error(), "not enough") {
		t.Fatalf("a contribution beyond the wallet: err=%v", err)
	}
	out, err := householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
		return familyContributeActionGo(c, worlddata.Catalog{}, 42, payload(map[string]any{"amount": 100}))
	})
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["balance"]) != 200 || i64(out["treasury_balance"]) != 100 || i64(out["wealth"]) != 62 || i64(out["influence"]) != 44 {
		t.Fatalf("contribution result: %v", out)
	}
	if i64(out["standing"]) != 10 || out["standing_band"] != "known" {
		t.Fatalf("standing after one contribution: %v %v", out["standing"], out["standing_band"])
	}
	var history []string
	_ = json.Unmarshal([]byte(fmt.Sprint(actionScalar(t, path, `SELECT history_json FROM birth_families WHERE family_id=?`, fid))), &history)
	if len(history) != 1 || !strings.Contains(history[0], "100 spirit stones") {
		t.Fatalf("the chronicle: %v", history)
	}
	// Wealth is capped at 100 however much comes in.
	for i := 0; i < 2; i++ {
		if _, err := householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
			return familyContributeActionGo(c, worlddata.Catalog{}, 42, payload(map[string]any{"amount": 100}))
		}); err != nil {
			t.Fatal(err)
		}
	}
	if got := i64(actionScalar(t, path, `SELECT wealth FROM birth_families WHERE family_id=?`, fid)); got != 100 {
		t.Fatalf("wealth after 300 stones: %d, want the cap of 100", got)
	}
	// And support reads the ledger: standing 30 is three stones more.
	support, err := householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
		return familySupportActionGo(c, catalog, 42, payload(map[string]any{"cooldown_game_minutes": 100}))
	})
	if err != nil {
		t.Fatal(err)
	}
	if i64(support["standing_bonus"]) != 3 || i64(support["standing"]) != 30 {
		t.Fatalf("support's standing term: %v", support)
	}
}

func TestTheHouseholdTeachesAgainOnlyWhenItCanAffordTo(t *testing.T) {
	path := householdDB(t)
	fid := householdFamily(t, path, 42, "home")
	catalog := districtCatalog(t)
	// The send-off's head start at wealth 42: 30 XP toward Apprentice Forging.
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,'Forging',0,30,0)`)
	tutor := func() (map[string]any, error) {
		return householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
			return familyTutorActionGo(c, catalog, 42, payload(map[string]any{}))
		})
	}
	if _, err := tutor(); err == nil || !strings.Contains(err.Error(), "wealth 60") {
		t.Fatalf("same band, nothing more to teach: err=%v", err)
	}
	batch4Exec(t, path, `UPDATE birth_families SET wealth=65 WHERE family_id=?`, fid)
	out, err := tutor()
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["level"]) != 0 || i64(out["xp"]) != 55 || out["tutor"] != "a hired tutor" {
		t.Fatalf("a richer house teaches better: %v", out)
	}
	if _, err := tutor(); err == nil || !strings.Contains(err.Error(), "wealth 80") {
		t.Fatalf("the same band twice: err=%v", err)
	}
	// Practice already past the band is never lowered.
	batch4Exec(t, path, `UPDATE profession_progress SET xp=70 WHERE user_id=42 AND profession='Forging'`)
	if _, err := tutor(); err == nil {
		t.Fatal("the household re-taught what the player already knew")
	}
	if got := i64(actionScalar(t, path, `SELECT xp FROM profession_progress WHERE user_id=42 AND profession='Forging'`)); got != 70 {
		t.Fatalf("xp lowered to %d", got)
	}
	// A master retained raises the level, and a level is never lowered either.
	batch4Exec(t, path, `UPDATE birth_families SET wealth=90 WHERE family_id=?`, fid)
	if out, err = tutor(); err != nil || i64(out["level"]) != 1 {
		t.Fatalf("a master retained: %v err=%v", out, err)
	}
	batch4Exec(t, path, `UPDATE birth_families SET wealth=10 WHERE family_id=?`, fid)
	_, _ = tutor()
	if got := i64(actionScalar(t, path, `SELECT level FROM profession_progress WHERE user_id=42 AND profession='Forging'`)); got != 1 {
		t.Fatalf("a poorer house took the level back: %d", got)
	}
}

// seedErrands puts the content's errands for one trade into quest_definitions
// the way the bot's seeder does.
func seedErrands(t *testing.T, path string, catalog worlddata.Catalog, trade string) []string {
	t.Helper()
	keys := []string{}
	for _, errand := range catalog.HouseholdErrands[trade] {
		batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,rewards_json,status,created_at,updated_at,giver_npc) VALUES(?,?,'',"system","household_errand",'[{"id":"home","type":"return_home","count":1}]','{"spirit_stones":5,"household_standing":8}','approved',0,0,'')`, errand.QuestKey, errand.Title)
		keys = append(keys, errand.QuestKey)
	}
	return keys
}

func TestErrandsAreHandedOverOneAtATimeAndOnlyOnce(t *testing.T) {
	path := householdDB(t)
	householdFamily(t, path, 42, "home")
	catalog := districtCatalog(t)
	keys := seedErrands(t, path, catalog, "Forging")
	if len(keys) != 3 {
		t.Fatalf("the content has %d Forging errands, want 3", len(keys))
	}
	ask := func() (map[string]any, error) {
		return householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
			return familyErrandActionGo(c, catalog, 42, payload(map[string]any{}))
		})
	}
	for i, key := range keys {
		out, err := ask()
		if err != nil || out["quest_key"] != key {
			t.Fatalf("errand %d: %v err=%v, want %s", i+1, out, err, key)
		}
		if _, err := ask(); err == nil || !strings.Contains(err.Error(), "finish the errand") {
			t.Fatalf("a second errand while one is carried: err=%v", err)
		}
		batch4Exec(t, path, `UPDATE character_quests SET status='completed' WHERE user_id=42 AND quest_key=?`, key)
	}
	if _, err := ask(); err == nil || !strings.Contains(err.Error(), "nothing more to ask") {
		t.Fatalf("after the pool: err=%v", err)
	}
}

func TestAnErrandBroughtHomePaysStandingAndWritesTheChronicle(t *testing.T) {
	path := householdDB(t)
	fid := householdFamily(t, path, 42, "home")
	catalog := districtCatalog(t)
	keys := seedErrands(t, path, catalog, "Forging")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	standing, err := householdErrandCompletedTx(conn, 42, keys[0], map[string]any{"household_standing": 8})
	if err != nil {
		t.Fatal(err)
	}
	// A quest that is not an errand pays no standing whatever it claims.
	forged, err := householdErrandCompletedTx(conn, 42, "forge_a_debt_repaid", map[string]any{"household_standing": 25})
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	if standing != 8 || forged != 0 {
		t.Fatalf("standing paid: errand %d, forged %d", standing, forged)
	}
	if got := i64(actionScalar(t, path, `SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key=?`, householdStandingKey(fid))); got != 8 {
		t.Fatalf("the ledger: %d", got)
	}
	var history []string
	_ = json.Unmarshal([]byte(fmt.Sprint(actionScalar(t, path, `SELECT history_json FROM birth_families WHERE family_id=?`, fid))), &history)
	if len(history) != 1 || !strings.Contains(history[0], "brought home") {
		t.Fatalf("the chronicle: %v", history)
	}
}

func TestTheHearthIsAGoodPlaceToSitAndNeverTheBest(t *testing.T) {
	path := householdDB(t)
	fid := householdFamily(t, path, 42, "home")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for tier, want := range map[int64]float64{1: 1.06, 2: 1.08, 5: 1.14, 9: 1.14} {
		if _, err := conn.Execute(`UPDATE birth_families SET tier=? WHERE family_id=?`, []any{tier, fid}); err != nil {
			t.Fatal(err)
		}
		name, mult, err := birthFamilyCultivationMultiplier(conn, birthFamilyHouseholdLocation(fid))
		if err != nil || name != "House Wen Household" || mult != want {
			t.Errorf("tier %d: %q %v err=%v, want %v", tier, name, mult, err, want)
		}
		if mult >= placeShrineMult {
			t.Errorf("tier %d: the hearth (%v) outdoes the shrine (%v)", tier, mult, placeShrineMult)
		}
	}
	if name, mult, _ := birthFamilyCultivationMultiplier(conn, "Greenriver Town"); name != "" || mult != 1 {
		t.Fatalf("the street is nothing in particular: %q %v", name, mult)
	}
}

func TestTheDoorOpensOnlyFromTheFamilysTown(t *testing.T) {
	path := householdDB(t)
	fid := householdFamily(t, path, 42, "Far Ridge")
	enter := func() (map[string]any, error) {
		return householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
			return familyHouseholdEnterAction(c, worlddata.Catalog{}, 42, payload(map[string]any{}))
		})
	}
	if _, err := enter(); err == nil || !strings.Contains(err.Error(), "travel there first") {
		t.Fatalf("entering from Far Ridge: err=%v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	out, err := enter()
	if err != nil || out["location"] != birthFamilyHouseholdLocation(fid) || out["return_location"] != nil {
		t.Fatalf("entering from the town: %v err=%v", out, err)
	}
	left, err := householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
		return familyHouseholdLeaveAction(c, worlddata.Catalog{}, 42, payload(map[string]any{}))
	})
	if err != nil || left["location"] != "Greenriver Town" {
		t.Fatalf("leaving on foot: %v err=%v", left, err)
	}
}

func talismanCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog := districtCatalog(t)
	if !catalog.Items["hearth_return_talisman"].Use.Homeward || !catalog.Items["waymark_talisman"].Use.Waymark {
		t.Fatal("the content's talismans do not carry their flags")
	}
	return catalog
}

func TestTheTwoTalismansMakeTheRoundTrip(t *testing.T) {
	path := householdDB(t)
	fid := householdFamily(t, path, 42, "Far Ridge")
	catalog := talismanCatalog(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'hearth_return_talisman',1),(42,'waymark_talisman',2)`)
	use := func(item string) (map[string]any, error) {
		return householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
			return itemUseActionGo(c, catalog, 42, payload(map[string]any{"item_id": item, "game_minute": 100}))
		})
	}
	// The waymark first: nothing has marked anywhere, and it is read at home.
	if _, err := use("waymark_talisman"); err == nil || !strings.Contains(err.Error(), "inside your birth household") {
		t.Fatalf("a waymark read on the road: err=%v", err)
	}
	// Mid-battle the paper stays in the pouch.
	batch4Exec(t, path, `INSERT INTO battles(user_id,status) VALUES(42,'active')`)
	if _, err := use("hearth_return_talisman"); err == nil || !strings.Contains(err.Error(), "battle") {
		t.Fatalf("burning it mid-battle: err=%v", err)
	}
	batch4Exec(t, path, `UPDATE battles SET status='finished' WHERE user_id=42`)
	if q := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='hearth_return_talisman'`)); q != 1 {
		t.Fatalf("a refused talisman was spent: %d left", q)
	}
	out, err := use("hearth_return_talisman")
	if err != nil {
		t.Fatal(err)
	}
	home, _ := out["homeward"].(map[string]any)
	if home == nil || home["location"] != birthFamilyHouseholdLocation(fid) || home["return_location"] != "Far Ridge" {
		t.Fatalf("homeward: %v", out)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != birthFamilyHouseholdLocation(fid) {
		t.Fatalf("standing at %q after the talisman", got)
	}
	if q := i64(actionScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42 AND item_id='hearth_return_talisman'`)); q != 0 {
		t.Fatalf("the talisman was not consumed")
	}
	// The waymark takes the mark.
	out, err = use("waymark_talisman")
	if err != nil {
		t.Fatal(err)
	}
	back, _ := out["waymark"].(map[string]any)
	if back == nil || back["location"] != "Far Ridge" {
		t.Fatalf("waymark: %v", out)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`)); got != "Far Ridge" {
		t.Fatalf("standing at %q after the waymark", got)
	}
	// Walking in leaves no mark, so the second waymark is refused unspent.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	if _, err := householdApply(t, path, func(c *storage.Conn) (authoritativeMutation, error) {
		return familyHouseholdEnterAction(c, worlddata.Catalog{}, 42, payload(map[string]any{}))
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := use("waymark_talisman"); err == nil || !strings.Contains(err.Error(), "no mark") {
		t.Fatalf("a waymark after walking in: err=%v", err)
	}
	if q := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='waymark_talisman'`)); q != 1 {
		t.Fatalf("the refused waymark was spent: %d left", q)
	}
}
