package game

import (
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const sectPoliticsDDL = `CREATE TABLE IF NOT EXISTS sect_politics_state(sect_name TEXT PRIMARY KEY, alignment TEXT NOT NULL DEFAULT 'Neutral', specialty TEXT NOT NULL DEFAULT '', influence INTEGER NOT NULL DEFAULT 50, cohesion INTEGER NOT NULL DEFAULT 50, resources INTEGER NOT NULL DEFAULT 50, recruitment_pressure INTEGER NOT NULL DEFAULT 50, doctrine_pressure INTEGER NOT NULL DEFAULT 50, leader_policy TEXT NOT NULL DEFAULT '', last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);`

func politics(t *testing.T, conn *storage.Conn, sect string) (int64, int64) {
	t.Helper()
	row := firstRowMap(mustExec(t, conn, `SELECT influence,resources FROM sect_politics_state WHERE sect_name='`+sect+`'`))
	return storage.ParseInt(row["influence"]), storage.ParseInt(row["resources"])
}

// v1.29.0: a war's end moved the walls, the relations and the fighters' pay,
// and nothing about either sect's standing in the world.
func TestAWarsEndMovesBothSects(t *testing.T) {
	path := warDB(t)
	conn := warConn(t, path)
	if err := conn.ExecScript(sectPoliticsDDL + `INSERT INTO sect_politics_state(sect_name) VALUES('Attacking Sect'),('Holding Sect');`); err != nil {
		t.Fatal(err)
	}
	if _, ended, err := ResolveWarTx(conn, worlddata.Catalog{}, 1, "Holding Sect", "defender_holds", 5000, 0); err != nil || !ended {
		t.Fatalf("ended=%v err=%v", ended, err)
	}
	if inf, res := politics(t, conn, "Holding Sect"); inf != 55 || res != 55 {
		t.Fatalf("the sect that held is at influence %d resources %d, want 55/55", inf, res)
	}
	if inf, res := politics(t, conn, "Attacking Sect"); inf != 45 || res != 45 {
		t.Fatalf("the sect thrown back is at influence %d resources %d, want 45/45", inf, res)
	}
}

func TestAPeaceMovesNeitherSect(t *testing.T) {
	path := warDB(t)
	conn := warConn(t, path)
	if err := conn.ExecScript(sectPoliticsDDL + `INSERT INTO sect_politics_state(sect_name) VALUES('Attacking Sect'),('Holding Sect');`); err != nil {
		t.Fatal(err)
	}
	if _, _, err := ResolveWarTx(conn, worlddata.Catalog{}, 1, "Holding Sect", "peace", 5000, 0); err != nil {
		t.Fatal(err)
	}
	for _, sect := range []string{"Attacking Sect", "Holding Sect"} {
		if inf, res := politics(t, conn, sect); inf != 50 || res != 50 {
			t.Fatalf("a peace moved %s to %d/%d", sect, inf, res)
		}
	}
}

// v1.29.0: a member's contribution strengthens the sect, a point of resources
// for every 25, at most three a credit.
func TestAMembersWorkStrengthensTheSect(t *testing.T) {
	path := warDB(t)
	conn := warConn(t, path)
	if err := conn.ExecScript(sectPoliticsDDL + `INSERT INTO sect_politics_state(sect_name) VALUES('Attacking Sect');`); err != nil {
		t.Fatal(err)
	}
	if _, err := creditSectContributionTx(conn, worlddata.Catalog{}, 42, 60, 0); err != nil {
		t.Fatal(err)
	}
	if _, res := politics(t, conn, "Attacking Sect"); res != 52 {
		t.Fatalf("60 contribution moved the sect's resources to %d, want 52", res)
	}
	if _, err := creditSectContributionTx(conn, worlddata.Catalog{}, 42, 1000, 0); err != nil {
		t.Fatal(err)
	}
	if _, res := politics(t, conn, "Attacking Sect"); res != 55 {
		t.Fatalf("one credit moved the sect's resources past the cap: %d", res)
	}
}

func TestARecruitingSectIsEasierToEnter(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/eager.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(sectPoliticsDDL + `INSERT INTO sect_politics_state(sect_name,recruitment_pressure) VALUES('Eager Sect',85),('Quiet Sect',40),('Desperate Sect',200);`); err != nil {
		t.Fatal(err)
	}
	for sect, want := range map[string]int64{"Eager Sect": 3, "Quiet Sect": 0, "Desperate Sect": 5, "Unknown Sect": 0} {
		if got := sectRecruitmentEagernessTx(conn, sect); got != want {
			t.Errorf("%s: %d off the trial, want %d", sect, got, want)
		}
	}
}

// v1.29.0: a caravan paid its owner and nothing else - its toll went to
// nobody and its goods to no market.
func TestACaravanArrivesSomewhere(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/caravan.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(sectPoliticsDDL + `
CREATE TABLE territory_state(territory_key TEXT PRIMARY KEY, controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '');
CREATE TABLE economy_markets(location TEXT NOT NULL, item_id TEXT NOT NULL, supply INTEGER NOT NULL DEFAULT 10, updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(location,item_id));
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY, prosperity INTEGER NOT NULL DEFAULT 50, security INTEGER NOT NULL DEFAULT 50);
INSERT INTO sect_politics_state(sect_name) VALUES('Toll Sect');
INSERT INTO territory_state VALUES('Greenriver Town','sect','Toll Sect');
INSERT INTO economy_markets(location,item_id,supply) VALUES('Greenriver Town','spirit_herb',10);
INSERT INTO civilization_regions(location,prosperity,security) VALUES('Greenriver Town',50,20);
`); err != nil {
		t.Fatal(err)
	}
	catalog := eventScopeCatalog(t)
	cargo := map[string]any{"spirit_herb": 6, "_payout": 900, "_currency": "low_spirit_stone"}
	if err := CaravanArrivedTx(conn, catalog, "Greenriver Town", cargo, 250, false, 1); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(firstRowMap(mustExec(t, conn, `SELECT supply FROM economy_markets WHERE item_id='spirit_herb'`))["supply"]); got != 16 {
		t.Fatalf("the market's supply is %d after six herbs arrived, want 16", got)
	}
	if got := storage.ParseInt(firstRowMap(mustExec(t, conn, `SELECT prosperity FROM civilization_regions`))["prosperity"]); got != 51 {
		t.Fatalf("the city's prosperity is %d after a caravan arrived, want 51", got)
	}
	if _, res := politics(t, conn, "Toll Sect"); res != 52 {
		t.Fatalf("a 250-stone toll moved the holding sect's resources to %d, want 52", res)
	}
	if got := CaravanSecurityRisk(conn, catalog, "Greenriver Town"); got != 6 {
		t.Fatalf("security 20 added %d to a caravan's risk, want 6", got)
	}
	if err := CaravanArrivedTx(conn, catalog, "Greenriver Town", cargo, 0, true, 1); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(firstRowMap(mustExec(t, conn, `SELECT supply FROM economy_markets WHERE item_id='spirit_herb'`))["supply"]); got != 16 {
		t.Fatalf("a seized caravan delivered goods: supply %d", got)
	}
}

// v1.29.0: a manor whose ground a rival holds lends its sect nothing.
func TestAManorUnderARivalsBannerLendsNothing(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/manor.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`CREATE TABLE territory_state(territory_key TEXT PRIMARY KEY, controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '');`); err != nil {
		t.Fatal(err)
	}
	catalog := eventScopeCatalog(t)
	seat := SectSeat(catalog, "Azure Cloud Sect")
	gate := SectGate(catalog, "Azure Cloud Sect")
	if seat == "" || gate == "" {
		t.Fatal("the Azure Cloud Sect keeps no seat in the shipped content")
	}
	if ManorGroundTakenTx(conn, catalog, "Azure Cloud Sect", gate) {
		t.Fatal("unclaimed ground was read as taken")
	}
	mustExec(t, conn, `INSERT INTO territory_state VALUES('`+seat+`','sect','Azure Cloud Sect')`)
	if ManorGroundTakenTx(conn, catalog, "Azure Cloud Sect", gate) {
		t.Fatal("a sect's own banner over its manor's city was read as a rival's")
	}
	mustExec(t, conn, `UPDATE territory_state SET controller_key='Black Serpent Clan'`)
	if !ManorGroundTakenTx(conn, catalog, "Azure Cloud Sect", gate) {
		t.Fatalf("a manor at %s, a part of %s which a rival holds, still lent its sect its bonuses", gate, seat)
	}
}

// v1.29.0: a killing's mark on its region and sect is one statement, and the
// severity it is given is the whole difference between a player's kill and
// the world's own.
func TestAKillingMarksItsRegionAndSect(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/mark.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(sectPoliticsDDL + `
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY, world_name TEXT NOT NULL DEFAULT 'Mortal World', population INTEGER NOT NULL DEFAULT 0, prosperity INTEGER NOT NULL DEFAULT 50, security INTEGER NOT NULL DEFAULT 50, spirit_resources INTEGER NOT NULL DEFAULT 50, food_supply INTEGER NOT NULL DEFAULT 50, migration_pressure INTEGER NOT NULL DEFAULT 0, unrest INTEGER NOT NULL DEFAULT 0, last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
INSERT INTO civilization_regions(location) VALUES('Greenriver Town');
INSERT INTO sect_politics_state(sect_name) VALUES('Azure Cloud Sect');`); err != nil {
		t.Fatal(err)
	}
	impacts, err := MarkKillingTx(conn, "Greenriver Town", "Old Wen", "Herbalist", "Azure Cloud Sect", 1, "Old Wen was killed.", 10, 0)
	if err != nil {
		t.Fatal(err)
	}
	row := firstRowMap(mustExec(t, conn, `SELECT security,unrest,prosperity FROM civilization_regions`))
	if storage.ParseInt(row["security"]) != 48 || storage.ParseInt(row["unrest"]) != 3 || storage.ParseInt(row["prosperity"]) != 49 {
		t.Fatalf("a severity-1 killing left the region at %v, want security 48, unrest 3, prosperity 49", row)
	}
	if inf, res := politics(t, conn, "Azure Cloud Sect"); inf != 48 || res != 49 {
		t.Fatalf("the dead's sect is at influence %d resources %d, want 48/49", inf, res)
	}
	if len(impacts) != 2 {
		t.Fatalf("impacts %v, want the region's and the sect's", impacts)
	}
}

// v1.29.0: the dead's own people remember who killed them, read before the
// widowing takes the spouse's name off their row.
func TestTheDeadsKinRememberTheKiller(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/kin.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(user_id INTEGER PRIMARY KEY);
INSERT INTO characters VALUES(42);
CREATE TABLE npc_relationships(user_id INTEGER NOT NULL, npc_name TEXT NOT NULL, trust INTEGER NOT NULL DEFAULT 0, respect INTEGER NOT NULL DEFAULT 0, fear INTEGER NOT NULL DEFAULT 0, affection INTEGER NOT NULL DEFAULT 0, debt INTEGER NOT NULL DEFAULT 0, grudge INTEGER NOT NULL DEFAULT 0, encounter_count INTEGER NOT NULL DEFAULT 0, last_summary TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL, PRIMARY KEY(user_id,npc_name), FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE npc_life_state(npc_name TEXT PRIMARY KEY, health INTEGER NOT NULL DEFAULT 100, relationship_status TEXT NOT NULL DEFAULT 'single', spouse_name TEXT NOT NULL DEFAULT '', last_social_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE npc_descendants(descendant_id INTEGER PRIMARY KEY AUTOINCREMENT, child_name TEXT NOT NULL UNIQUE, parent_a TEXT NOT NULL, parent_b TEXT NOT NULL, birth_game_minute INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'alive', created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE npc_disciple_bonds(master_name TEXT NOT NULL, disciple_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', started_game_minute INTEGER NOT NULL DEFAULT 0, ended_game_minute INTEGER, reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL, PRIMARY KEY(master_name,disciple_name));
INSERT INTO npc_life_state(npc_name,relationship_status,spouse_name) VALUES('Old Wen','married','Widow Lan'),('Widow Lan','married','Old Wen'),('Stranger','single','');
INSERT INTO npc_descendants(child_name,parent_a,parent_b,birth_game_minute) VALUES('Little Wen','Old Wen','Widow Lan',0),('Dead Child','Old Wen','Widow Lan',0);
UPDATE npc_descendants SET status='dead' WHERE child_name='Dead Child';
INSERT INTO npc_disciple_bonds(master_name,disciple_name,updated_at) VALUES('Old Wen','Apprentice Mo',0);
INSERT INTO npc_relationships(user_id,npc_name,grudge,updated_at) VALUES(42,'Little Wen',70,0);`); err != nil {
		t.Fatal(err)
	}
	kin := kinOfTx(conn, "Old Wen")
	if err := ReleaseNPCBondsTx(conn, "Old Wen", 10, 0); err != nil {
		t.Fatal(err)
	}
	got, err := RememberTheKillerTx(conn, 42, kin, "Old Wen", 0)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"Apprentice Mo", "Little Wen", "Widow Lan"}; len(got) != len(want) || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Fatalf("the kin who remember are %v, want %v", got, want)
	}
	grudge := func(name string) int64 {
		row := firstRowMap(mustExec(t, conn, `SELECT grudge FROM npc_relationships WHERE user_id=42 AND npc_name='`+name+`'`))
		if row == nil {
			return 0
		}
		return storage.ParseInt(row["grudge"])
	}
	if grudge("Widow Lan") != kinGrudge || grudge("Apprentice Mo") != kinGrudge {
		t.Fatalf("the widow holds %d and the disciple %d, want %d each", grudge("Widow Lan"), grudge("Apprentice Mo"), kinGrudge)
	}
	if grudge("Little Wen") != 100 {
		t.Fatalf("a grudge went past the clamp: %d", grudge("Little Wen"))
	}
	if grudge("Stranger") != 0 || grudge("Dead Child") != 0 {
		t.Fatal("somebody who was not the dead's own was given a grudge")
	}
}

// v1.29.0: the household's year reads its real treaties, feuds and branches.
func TestTheHouseholdsYearReadsItsClanAndBranches(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/house.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE martial_clan_relations(relation_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, partner_family_id INTEGER, partner_name TEXT NOT NULL, relation_type TEXT NOT NULL, relation_score INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, started_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE martial_clan_branches(branch_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, branch_name TEXT NOT NULL, branch_type TEXT NOT NULL DEFAULT 'cadet', leader_name TEXT NOT NULL, members_estimate INTEGER NOT NULL DEFAULT 10, martial_strength INTEGER NOT NULL DEFAULT 20, wealth_share INTEGER NOT NULL DEFAULT 10, loyalty INTEGER NOT NULL DEFAULT 60, status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL DEFAULT 0);
INSERT INTO martial_clan_relations(family_id,partner_name,relation_type) VALUES(1,'A','alliance'),(1,'B','trade_pact'),(1,'C','blood_feud'),(2,'D','rivalry');
INSERT INTO martial_clan_relations(family_id,partner_name,relation_type,active) VALUES(1,'E','alliance',0);
INSERT INTO martial_clan_branches(family_id,branch_name,leader_name,wealth_share) VALUES(1,'East','X',60),(1,'West','Y',50),(1,'Lost','Z',500);
UPDATE martial_clan_branches SET status='broken' WHERE branch_name='Lost';`); err != nil {
		t.Fatal(err)
	}
	terms := householdWorldTermsTx(conn, 1)
	if terms.Influence != 2 || terms.Stability != -2 || terms.Wealth != 2 {
		t.Fatalf("terms %+v, want influence +2 (two treaties in force), stability -2 (one blood feud), wealth +2 (110 share)", terms)
	}
	if none := householdWorldTermsTx(conn, 3); none.Influence != 0 || none.Stability != 0 || none.Wealth != 0 || len(none.Lines) != 0 {
		t.Fatalf("a household with no clan and no branches was given %+v", none)
	}
}
