package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// The ten decisions the owner took in one message (v1.3.0), the engine's
// half. Each test is the rule, drilled against the tree that did not have it.

// A failed craft returns half of each input, rounded down.
func TestAFailedCraftReturnsHalfOfEachInput(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4TeachRecipe(t, path, 42, "Spirit-Iron Sword")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron',3),(42,'beast_core',1)`)
	// The fixture's sheet is a giant's (body 200); a fresh crafter's is 2, so
	// two ones against TN 14 is a miss.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":2,"agility":2,"spirit":2,"insight":2,"will":2,"presence":2}' WHERE user_id=42`)
	defer gamerng.UseRoller(func(int) int { return 0 })() // every die its lowest face: a miss

	raw, _ := json.Marshal(map[string]any{"recipe": "Spirit-Iron Sword"})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "v130-craft-miss", Operation: "craft.resolve", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	result := out.Result.(map[string]any)
	if result["success"] != false {
		t.Fatalf("two ones against TN 14 landed: %+v", result)
	}
	returned, _ := result["returned"].(map[string]int64)
	if returned["spirit_iron"] != 1 || returned["beast_core"] != 0 {
		t.Fatalf("a miss on 3 iron and 1 core returned %v; half of each, rounded down, is 1 iron and no core", returned)
	}
	if iron := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_iron'`)); iron != 1 {
		t.Fatalf("the bag holds %d spirit iron after the miss; the refund was reported and not paid", iron)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='spirit_iron_sword'`)); got != 0 {
		t.Fatalf("a miss made %d swords", got)
	}
}

func TestTheRefundIsHalfRoundedDown(t *testing.T) {
	got := craftFailureRefund(map[string]int64{"a": 1, "b": 2, "c": 3, "d": 5})
	want := map[string]int64{"b": 1, "c": 1, "d": 2}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("refund %v, want %v: one unit of anything is the stake", got, want)
	}
}

// A property cannot be founded inside the household you were born into.
func TestAPropertyIsNotFoundedInsideABirthHousehold(t *testing.T) {
	path := setupPropertyTypesDB(t)
	world := batch4WorldPath(t)
	deaconMembership(t, path)
	batch4Exec(t, path, `UPDATE characters SET location='birth_family:7' WHERE user_id=42`)
	_, err := establishProperty(t, path, world, "homestead")
	if err == nil || !strings.Contains(err.Error(), "household you were born into") {
		t.Fatalf("a homestead founded inside a starter household, which other players share: err=%v", err)
	}
}

// A hall teaches only what its own world can make; the rest is named.
func TestAHallTeachesOnlyWhatItsWorldCanMake(t *testing.T) {
	catalog := crossingCatalog(t)
	if len(worldOffers(catalog, "Mortal World")) < 20 {
		t.Fatal("the Mortal World offers almost nothing; the reader is broken, not the tree")
	}
	// The report's own case: the Dawn Lotus Vitality Pill wants a herb
	// shelved from the Spiritual World up.
	taught, withheld := rankRecipesWhereTheyCanBeMade(catalog, "Alchemy", 2, "Mortal World")
	if !contains(withheld, "Dawn Lotus Vitality Pill") || contains(taught, "Dawn Lotus Vitality Pill") {
		t.Fatalf("a Mortal hall taught the Dawn Lotus Vitality Pill: taught=%v withheld=%v", taught, withheld)
	}
	taught, withheld = rankRecipesWhereTheyCanBeMade(catalog, "Alchemy", 2, "Spiritual World")
	if !contains(taught, "Dawn Lotus Vitality Pill") {
		t.Fatalf("a Spiritual hall withheld what its world can make: taught=%v withheld=%v", taught, withheld)
	}
	// Every recipe of the rank is on one side or the other, never both.
	for _, name := range taught {
		if contains(withheld, name) {
			t.Fatalf("%s is both taught and withheld", name)
		}
	}
}

func TestAPassedExaminationWithholdsWhatTheWorldCannotMake(t *testing.T) {
	path := examDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
		VALUES(42,'Alchemy',2,0,0,0,0,0) ON CONFLICT(user_id,profession) DO UPDATE SET level=2`)
	apothecary := oneHallOf(t, catalog, "apothecary")
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, apothecary.Location)
	defer gamerng.UseRoller(func(int) int { return 9 })()
	out, err := sitExam(t, path, catalog, "Alchemy", 100)
	if err != nil {
		t.Fatal(err)
	}
	if out["passed"] != true {
		t.Fatalf("not passed: %+v", out)
	}
	withheld, _ := out["recipes_withheld"].([]string)
	if !contains(withheld, "Dawn Lotus Vitality Pill") {
		t.Fatalf("the Mortal hall's reply does not name the method it withheld: %+v", out)
	}
	if out["hall_world"] != "Mortal World" {
		t.Fatalf("hall_world=%v", out["hall_world"])
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_recipes WHERE user_id=42 AND recipe='Dawn Lotus Vitality Pill'`)); n != 0 {
		t.Fatal("the withheld method was written into character_recipes anyway")
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_recipes WHERE user_id=42 AND source='exam'`)); n == 0 {
		t.Fatal("the hall taught nothing at all")
	}
}

// The Nine-Echo Sword Wraith's floor opens beneath the realm once walked.
func setupNineEchoDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS parties(party_id INTEGER PRIMARY KEY AUTOINCREMENT, leader_user_id INTEGER NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS party_members(party_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'member', joined_at REAL NOT NULL, PRIMARY KEY(party_id,user_id))`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS boss_encounters(encounter_id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, template_key TEXT NOT NULL, location TEXT NOT NULL, boss_name TEXT NOT NULL, boss_hp INTEGER NOT NULL, boss_hp_max INTEGER NOT NULL, phase_index INTEGER NOT NULL DEFAULT 0, round_index INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active', winner_party_id INTEGER, version INTEGER NOT NULL DEFAULT 0, started_game_minute INTEGER NOT NULL DEFAULT 0, finished_game_minute INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS boss_participants(encounter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, vitality INTEGER NOT NULL, vitality_max INTEGER NOT NULL, acted_round INTEGER NOT NULL DEFAULT 0, total_damage INTEGER NOT NULL DEFAULT 0, guard INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL, PRIMARY KEY(encounter_id,user_id))`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS inheritances(user_id INTEGER NOT NULL, inheritance_id TEXT NOT NULL, source_realm_id TEXT NOT NULL, acquired_at REAL NOT NULL, PRIMARY KEY(user_id, inheritance_id))`)
	batch4Exec(t, path, `INSERT INTO parties(party_id,leader_user_id,name,status,created_at,updated_at) VALUES(1,42,'Echoes',
		'active',0,0)`)
	batch4Exec(t, path, `INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(1,42,'leader',0)`)
	return path
}

func startWraith(t *testing.T, path string) (map[string]any, error) {
	t.Helper()
	catalog := crossingCatalog(t)
	raw, _ := json.Marshal(map[string]any{"template_key": "nine_echo_sword_wraith"})
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		m, err := bossStartActionGo(conn, catalog, 42, raw)
		out, _ = m.Result.(map[string]any)
		return err
	})
	return out, err
}

func TestTheNineEchoFloorOpensBeneathTheRealmOnceWalked(t *testing.T) {
	catalog := crossingCatalog(t)
	lair, realmID := bossLair(catalog, bossTemplatesGo["nine_echo_sword_wraith"])
	realm, ok := catalog.SecretRealms[realmID]
	if !ok || lair != realm.Location || lair == "" {
		t.Fatalf("the wraith's lair resolved to %q / realm %q; it is a secret floor of the Sword Grave", lair, realmID)
	}
	if _, isPlace := catalog.Locations[lair]; !isPlace {
		t.Fatalf("%q is not a catalogue place", lair)
	}

	path := setupNineEchoDB(t)
	// Standing at the realm's name, which nobody can: still refused, and the
	// refusal names the place a party can actually stand.
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, "Sword Grave of Nine Echoes")
	if _, err := startWraith(t, path); err == nil || !strings.Contains(err.Error(), lair) {
		t.Fatalf("err=%v; the refusal must name %q", err, lair)
	}
	// At the entrance without having walked the realm: the floor is shut.
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, lair)
	if _, err := startWraith(t, path); err == nil || !strings.Contains(err.Error(), "walked the realm to its end") {
		t.Fatalf("a party that never cleared the Sword Grave started the raid: err=%v", err)
	}
	// Having walked it - the inheritance is the record of the last room.
	batch4Exec(t, path, `INSERT INTO inheritances(user_id,inheritance_id,source_realm_id,acquired_at) VALUES(42,?,?,0)`, realm.InheritanceID, realmID)
	out, err := startWraith(t, path)
	if err != nil {
		t.Fatalf("the floor did not open to somebody holding the realm's inheritance: %v", err)
	}
	if out == nil {
		t.Fatal("no result")
	}
	if where := actionScalar(t, path, `SELECT location FROM boss_encounters WHERE party_id=1`); fmt.Sprint(where) != lair {
		t.Fatalf("the encounter stands at %v, not at the entrance %q", where, lair)
	}
}

func TestAnOrdinaryLairIsItself(t *testing.T) {
	catalog := crossingCatalog(t)
	for key, template := range bossTemplatesGo {
		if key == "nine_echo_sword_wraith" {
			continue
		}
		lair, realmID := bossLair(catalog, template)
		if realmID != "" || lair != template.Location {
			t.Fatalf("%s: %q resolved to %q / %q", key, template.Location, lair, realmID)
		}
	}
}

// No event node or event action rolls an attribute nobody has.
func TestNoEventRollsAnAttributeNobodyHas(t *testing.T) {
	catalog := crossingCatalog(t)
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	seen := 0
	check := func(where, attr string) {
		t.Helper()
		seen++
		if _, err := canonicalAttribute(conn, catalog, 42, 0, attr); err != nil && strings.Contains(err.Error(), "unknown attribute") {
			t.Fatalf("%s rolls %q, which no character has", where, attr)
		}
	}
	for cat, site := range catalog.EventSites.Categories {
		for _, node := range site.Nodes {
			check(cat+"/"+node.Key, node.Attribute)
		}
	}
	for _, node := range catalog.EventSites.Default.Nodes {
		check("default/"+node.Key, node.Attribute)
	}
	for key, rule := range worldEventActionRules {
		check("action "+key, rule.Attribute)
	}
	if seen < 30 {
		t.Fatalf("only %d rolls checked; the reader is broken, not the tree", seen)
	}
	if _, err := canonicalAttribute(conn, catalog, 42, 0, "heart"); err == nil || !strings.Contains(err.Error(), "unknown attribute") {
		t.Fatalf("heart is still an attribute the engine accepts: err=%v", err)
	}
}
