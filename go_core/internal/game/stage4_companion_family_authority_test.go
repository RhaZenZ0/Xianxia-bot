package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func setupStage4CompanionTables(t *testing.T, path string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	if err := conn.ExecScript(`
ALTER TABLE cave_abodes ADD COLUMN beast_pen_level INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spirit_beasts(
	beast_id INTEGER PRIMARY KEY AUTOINCREMENT,
	user_id INTEGER NOT NULL,
	name TEXT NOT NULL,
	species TEXT NOT NULL,
	rank INTEGER NOT NULL DEFAULT 0,
	element TEXT NOT NULL DEFAULT 'None',
	intelligence INTEGER NOT NULL DEFAULT 10,
	temperament TEXT NOT NULL DEFAULT 'wary',
	bloodline TEXT NOT NULL DEFAULT 'Common',
	evolution_stage INTEGER NOT NULL DEFAULT 0,
	loyalty INTEGER NOT NULL DEFAULT 25,
	contract_type TEXT NOT NULL DEFAULT 'temporary',
	active INTEGER NOT NULL DEFAULT 0,
	techniques_json TEXT NOT NULL DEFAULT '[]',
	created_at REAL NOT NULL,
	updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wild_beast_encounters(
	encounter_id INTEGER PRIMARY KEY AUTOINCREMENT,
	user_id INTEGER NOT NULL,
	species TEXT NOT NULL,
	rank INTEGER NOT NULL DEFAULT 0,
	element TEXT NOT NULL DEFAULT 'Wild',
	intelligence INTEGER NOT NULL DEFAULT 10,
	temperament TEXT NOT NULL DEFAULT 'wary',
	bloodline TEXT NOT NULL DEFAULT 'Wild Spirit',
	taming_tn INTEGER NOT NULL DEFAULT 14,
	location TEXT NOT NULL,
	expires_game_minute INTEGER NOT NULL,
	status TEXT NOT NULL DEFAULT 'available',
	created_game_minute INTEGER NOT NULL DEFAULT 0,
	created_at REAL NOT NULL,
	updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_bonds(
	user_id INTEGER NOT NULL,
	item_id TEXT NOT NULL,
	bond_level INTEGER NOT NULL DEFAULT 0,
	resonance INTEGER NOT NULL DEFAULT 0,
	awakened INTEGER NOT NULL DEFAULT 0,
	spirit_name TEXT NOT NULL DEFAULT '',
	temperament TEXT NOT NULL DEFAULT 'dormant',
	created_at REAL NOT NULL,
	updated_at REAL NOT NULL,
	PRIMARY KEY(user_id,item_id)
);
`); err != nil {
		t.Fatal(err)
	}
}

func stage4ApplyError(t *testing.T, path, world, operation string, seq int, payload map[string]any) error {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	_, err = ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   fmt.Sprintf("stage4-error-%s-%d", strings.ReplaceAll(operation, ".", "-"), seq),
		Operation:  operation,
		ActorID:    42,
		Payload:    raw,
	})
	return err
}

func TestStage4CompanionActionsRejectCallerMechanicalInputs(t *testing.T) {
	tests := []struct {
		operation string
		base      map[string]any
		fields    []string
	}{
		{"beast.tame", map[string]any{"encounter_id": 1}, []string{"game_minute", "cooldown_seconds", "context_bonus", "location", "taming_tn", "modifier", "unexpected"}},
		{"beast.feed", map[string]any{"beast_id": 1, "food": "spirit_herb"}, []string{"game_minute", "location", "cooldown_seconds", "context_bonus", "gain", "loyalty_gain", "unexpected"}},
		{"beast.train", map[string]any{"beast_id": 1}, []string{"game_minute", "context_bonus", "cooldown_seconds", "location", "beast_pen_level", "gain", "unexpected"}},
		{"beast.evolve", map[string]any{"beast_id": 1}, []string{"game_minute", "location", "cooldown_seconds", "loyalty", "evolution_stage", "rank", "unexpected"}},
		{"beast.active", map[string]any{"beast_id": 1}, []string{"game_minute", "location", "active", "cooldown_seconds", "unexpected"}},
		{"artifact.bond", map[string]any{"item_id": "spirit_iron"}, []string{"game_minute", "location", "cooldown_seconds", "context_bonus", "bond_level", "resonance", "unexpected"}},
		{"artifact.awaken", map[string]any{"item_id": "spirit_iron", "spirit_name": "Ash"}, []string{"game_minute", "location", "bond_level", "resonance", "awakened", "unexpected"}},
	}

	for _, tc := range tests {
		t.Run(tc.operation, func(t *testing.T) {
			path := setupBatch4AuthorityDB(t)
			setupStage4CompanionTables(t, path)
			world := batch4WorldPath(t)
			for index, field := range tc.fields {
				payload := make(map[string]any, len(tc.base)+1)
				for key, value := range tc.base {
					payload[key] = value
				}
				payload[field] = 1
				err := stage4ApplyError(t, path, world, tc.operation, index+1, payload)
				if err == nil || !strings.Contains(err.Error(), "client-supplied "+field+" is forbidden") {
					t.Fatalf("%s err=%v", field, err)
				}
			}
		})
	}
}

func TestStage4BeastTameUsesCanonicalClockAndLocation(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)

	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at)
		VALUES('world_clock','{"anchor_game_minute":1000,"anchor_real_ts":1,"scale":0}',0)`)
	batch4Exec(t, path, `INSERT INTO wild_beast_encounters(
		user_id,species,rank,element,intelligence,temperament,bloodline,taming_tn,
		location,expires_game_minute,status,created_game_minute,created_at,updated_at
	) VALUES(42,'Sunmane Lynx',1,'Fire',12,'wary','Solar Lynx',8,'Cloudspine Foothills',1100,'available',900,0,0)`)

	err := stage4ApplyError(t, path, world, "beast.tame", 1, map[string]any{"encounter_id": 1})
	if err == nil || !strings.Contains(err.Error(), "current location") {
		t.Fatalf("cross-location tame err=%v", err)
	}

	batch4Exec(t, path, `UPDATE wild_beast_encounters SET location='Greenriver Town' WHERE encounter_id=1`)
	result := batch4Result(t, batch4Apply(t, path, world, "beast.tame", 2, map[string]any{"encounter_id": 1}))
	if got := storage.ParseInt(result["game_minute"]); got != 1000 {
		t.Fatalf("game_minute=%d want 1000", got)
	}
	if got := fmt.Sprint(result["location"]); got != "Greenriver Town" {
		t.Fatalf("location=%q want Greenriver Town", got)
	}
	if got := storage.ParseInt(result["cooldown_seconds"]); got != beastTameCooldownSeconds {
		t.Fatalf("cooldown_seconds=%d", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, "SELECT status FROM wild_beast_encounters WHERE encounter_id=1")); got != "tamed" {
		t.Fatalf("encounter status=%q want tamed", got)
	}

	remaining := stage4CooldownRemaining(t, path, "beast_tame")
	if remaining < beastTameCooldownSeconds-5 || remaining > beastTameCooldownSeconds {
		t.Fatalf("remaining=%d want near %d", remaining, beastTameCooldownSeconds)
	}
}

func TestStage4BeastTrainDerivesAccessiblePenContextAndFixedCooldown(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)

	batch4Exec(t, path, `UPDATE characters
		SET location='guest_beast_abode',
		    attributes_json='{"body":10,"agility":10,"spirit":6,"insight":10,"will":10,"presence":10}'
		WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,herb_garden_level,beast_pen_level)
		VALUES(43,'guest_beast_abode','Greenriver Town',0,3)`)
	batch4Exec(t, path, `INSERT INTO cave_abode_access(owner_user_id,guest_user_id) VALUES(43,42)`)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',1,'Wind',10,'bonded','Common',0,30,'equality',1,'[]',0,0)`)

	result := batch4Result(t, batch4Apply(t, path, world, "beast.train", 1, map[string]any{"beast_id": 1}))
	if got := storage.ParseInt(result["beast_pen_level"]); got != 3 {
		t.Fatalf("beast_pen_level=%d want 3", got)
	}
	if got := storage.ParseInt(result["context_bonus"]); got != 6 {
		t.Fatalf("context_bonus=%d want 6", got)
	}
	if got := storage.ParseInt(result["gain"]); got != 14 {
		t.Fatalf("gain=%d want 14", got)
	}
	if got := fmt.Sprint(result["location"]); got != "guest_beast_abode" {
		t.Fatalf("location=%q", got)
	}
	if got := storage.ParseInt(result["cooldown_seconds"]); got != beastTrainCooldownSeconds {
		t.Fatalf("cooldown_seconds=%d", got)
	}
	if err := stage4ApplyError(t, path, world, "beast.train", 2, map[string]any{"beast_id": 1}); err == nil || !strings.Contains(err.Error(), "cooldown") {
		t.Fatalf("second train err=%v", err)
	}
}

func TestStage4FeedAndArtifactBondUseFixedServerCooldowns(t *testing.T) {
	t.Run("feed", func(t *testing.T) {
		path := setupBatch4AuthorityDB(t)
		setupStage4CompanionTables(t, path)
		world := batch4WorldPath(t)
		batch4Exec(t, path, `INSERT INTO spirit_beasts(
			user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
			loyalty,contract_type,active,techniques_json,created_at,updated_at
		) VALUES(42,'Cloudpaw','Wind Lynx',1,'Wind',10,'bonded','Common',0,30,'equality',1,'[]',0,0)`)
		batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',2)`)

		result := batch4Result(t, batch4Apply(t, path, world, "beast.feed", 1, map[string]any{"beast_id": 1, "food": "spirit_herb"}))
		if got := storage.ParseInt(result["cooldown_seconds"]); got != beastFeedCooldownSeconds {
			t.Fatalf("cooldown_seconds=%d", got)
		}
		if got := storage.ParseInt(actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")); got != 1 {
			t.Fatalf("food quantity=%d want 1", got)
		}
		if err := stage4ApplyError(t, path, world, "beast.feed", 2, map[string]any{"beast_id": 1, "food": "spirit_herb"}); err == nil || !strings.Contains(err.Error(), "cooldown") {
			t.Fatalf("second feed err=%v", err)
		}
		if got := storage.ParseInt(actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")); got != 1 {
			t.Fatalf("second feed consumed inventory; quantity=%d", got)
		}
	})

	t.Run("artifact", func(t *testing.T) {
		path := setupBatch4AuthorityDB(t)
		setupStage4CompanionTables(t, path)
		world := batch4WorldPath(t)
		batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron',1)`)

		result := batch4Result(t, batch4Apply(t, path, world, "artifact.bond", 1, map[string]any{"item_id": "spirit_iron"}))
		if got := storage.ParseInt(result["cooldown_seconds"]); got != artifactBondCooldownSeconds {
			t.Fatalf("cooldown_seconds=%d", got)
		}
		if err := stage4ApplyError(t, path, world, "artifact.bond", 2, map[string]any{"item_id": "spirit_iron"}); err == nil || !strings.Contains(err.Error(), "cooldown") {
			t.Fatalf("second bond err=%v", err)
		}
	})
}

func stage4CooldownRemaining(t *testing.T, path, action string) int64 {
	t.Helper()
	available := actionScalar(t, path, "SELECT available_at FROM cooldowns WHERE user_id=42 AND action=?", action)
	availableAt, ok := available.(float64)
	if !ok {
		t.Fatalf("available_at type=%T value=%v", available, available)
	}
	remaining := int64(availableAt - float64(time.Now().UnixNano())/1e9)
	if remaining < 0 {
		return 0
	}
	return remaining
}

func TestFamilyHomelandsUseDistinctPublicStandardCities(t *testing.T) {
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	worlds := []string{"Mortal World", "Spiritual World", "Immortal World", "Celestial World"}

	if len(birthFamilyHomelands) != len(birthFamilyArchetypes) {
		t.Fatalf("homeland profiles=%d archetypes=%d", len(birthFamilyHomelands), len(birthFamilyArchetypes))
	}
	for _, worldName := range worlds {
		options, err := generateBirthFamilyOptions(worldName)
		if err != nil {
			t.Fatalf("%s: %v", worldName, err)
		}
		if len(options) != len(birthFamilyArchetypes) {
			t.Fatalf("%s options=%d", worldName, len(options))
		}
		seen := map[string]string{}
		for _, family := range options {
			if family.NearbyCity != family.Location {
				t.Fatalf("%s %s nearby_city=%q location=%q", worldName, family.Archetype, family.NearbyCity, family.Location)
			}
			if family.HomelandTheme == "" || family.Climate == "" {
				t.Fatalf("%s %s missing environment metadata", worldName, family.Archetype)
			}
			location, ok := catalog.Locations[family.Location]
			if !ok {
				t.Fatalf("%s %s uses non-standard location %q", worldName, family.Archetype, family.Location)
			}
			if location.World != worldName {
				t.Fatalf("%s %s location world=%q", worldName, family.Archetype, location.World)
			}
			if location.Private {
				t.Fatalf("%s %s location %q is private and cannot be normally discovered", worldName, family.Archetype, family.Location)
			}
			if location.MinRealmIndex != 0 {
				t.Fatalf("%s %s location %q min realm=%d; newborns/other players may be excluded", worldName, family.Archetype, family.Location, location.MinRealmIndex)
			}
			if location.SettlementType != "city" {
				t.Fatalf("%s %s location %q settlement_type=%q", worldName, family.Archetype, family.Location, location.SettlementType)
			}
			if previous, exists := seen[family.Location]; exists {
				t.Fatalf("%s families %s and %s share %q; every current family must have its own city", worldName, previous, family.Archetype, family.Location)
			}
			seen[family.Location] = family.Archetype
		}
	}

	for _, worldName := range worlds {
		_, hot, _ := birthFamilyHomeland(worldName, "weaponsmith_martial_family")
		_, cold, _ := birthFamilyHomeland(worldName, "border_garrison_family")
		_, yin, _ := birthFamilyHomeland(worldName, "hidden_weapon_family")
		if hot == cold || hot == yin || cold == yin {
			t.Fatalf("%s climate-aligned family cities collapsed together: hot=%q cold=%q yin=%q", worldName, hot, cold, yin)
		}
	}
}

func TestStage45AllCompanionMutationsRejectDeadActors(t *testing.T) {
	tests := []struct {
		operation string
		payload   map[string]any
	}{
		{"beast.tame", map[string]any{"encounter_id": 1}},
		{"beast.feed", map[string]any{"beast_id": 1, "food": "spirit_herb"}},
		{"beast.train", map[string]any{"beast_id": 1}},
		{"beast.evolve", map[string]any{"beast_id": 1}},
		{"beast.active", map[string]any{"beast_id": 1}},
		{"artifact.bond", map[string]any{"item_id": "spirit_iron"}},
		{"artifact.awaken", map[string]any{"item_id": "spirit_iron", "spirit_name": "Ash"}},
	}

	for index, tc := range tests {
		t.Run(tc.operation, func(t *testing.T) {
			path := setupBatch4AuthorityDB(t)
			setupStage4CompanionTables(t, path)
			world := batch4WorldPath(t)
			batch4Exec(t, path, `UPDATE characters SET life_status='dead' WHERE user_id=42`)
			err := stage4ApplyError(t, path, world, tc.operation, index+100, tc.payload)
			if err == nil || !strings.Contains(err.Error(), "only a living character") {
				t.Fatalf("dead actor %s err=%v", tc.operation, err)
			}
		})
	}
}

func TestStage45FeedReturnsCanonicalContextAndDoesNotAcceptCallerTime(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at)
		VALUES('world_clock','{"anchor_game_minute":2222,"anchor_real_ts":1,"scale":0}',0)`)
	batch4Exec(t, path, `UPDATE characters SET location='Cloudspine Foothills' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',1,'Wind',10,'bonded','Common',0,30,'equality',1,'[]',0,0)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',1)`)

	result := batch4Result(t, batch4Apply(t, path, world, "beast.feed", 90, map[string]any{
		"beast_id": 1,
		"food":     "spirit_herb",
	}))
	if got := storage.ParseInt(result["game_minute"]); got != 2222 {
		t.Fatalf("game_minute=%d want 2222", got)
	}
	if got := fmt.Sprint(result["location"]); got != "Cloudspine Foothills" {
		t.Fatalf("location=%q want Cloudspine Foothills", got)
	}
}

func TestStage45ArtifactAwakenIsOneWay(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO artifact_bonds(
		user_id,item_id,bond_level,resonance,awakened,spirit_name,temperament,created_at,updated_at
	) VALUES(42,'spirit_iron',3,25,0,'','dormant',0,0)`)

	result := batch4Result(t, batch4Apply(t, path, world, "artifact.awaken", 95, map[string]any{
		"item_id": "spirit_iron", "spirit_name": "Ash",
	}))
	artifact := result["artifact"].(map[string]any)
	if got := storage.ParseInt(artifact["awakened"]); got != 1 {
		t.Fatalf("awakened=%d want 1", got)
	}
	beforeXP := storage.ParseInt(actionScalar(t, path, `SELECT xp FROM profession_progress WHERE user_id=42 AND profession='Artifact Refining'`))

	err := stage4ApplyError(t, path, world, "artifact.awaken", 96, map[string]any{
		"item_id": "spirit_iron", "spirit_name": "Ash Again",
	})
	if err == nil || !strings.Contains(err.Error(), "already awakened") {
		t.Fatalf("repeat awaken err=%v", err)
	}
	afterXP := storage.ParseInt(actionScalar(t, path, `SELECT xp FROM profession_progress WHERE user_id=42 AND profession='Artifact Refining'`))
	if afterXP != beforeXP {
		t.Fatalf("repeat awaken changed profession xp: before=%d after=%d", beforeXP, afterXP)
	}
}

func TestFamilyCityRoadGraphIsBidirectionalConnectedAndWorldLocal(t *testing.T) {
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	worlds := []string{"Mortal World", "Spiritual World", "Immortal World", "Celestial World"}

	for _, worldName := range worlds {
		options, err := generateBirthFamilyOptions(worldName)
		if err != nil {
			t.Fatal(err)
		}
		familyCities := map[string]bool{}
		for _, family := range options {
			familyCities[family.Location] = true
		}
		if len(familyCities) != len(birthFamilyArchetypes) {
			t.Fatalf("%s family city count=%d", worldName, len(familyCities))
		}

		for city := range familyCities {
			location, ok := catalog.Locations[city]
			if !ok {
				t.Fatalf("%s missing city %q", worldName, city)
			}
			if len(location.Roads) == 0 {
				t.Fatalf("%s city %q has no roads", worldName, city)
			}
			for _, neighbor := range location.Roads {
				next, ok := catalog.Locations[neighbor]
				if !ok {
					t.Fatalf("%s road %q -> missing %q", worldName, city, neighbor)
				}
				if next.World != worldName {
					t.Fatalf("%s cross-world road %q -> %q (%s)", worldName, city, neighbor, next.World)
				}
				if !stringInList(next.Roads, city) {
					t.Fatalf("%s road %q -> %q is not bidirectional", worldName, city, neighbor)
				}
			}
		}

		start := options[0].Location
		seen := map[string]bool{start: true}
		queue := []string{start}
		for len(queue) > 0 {
			current := queue[0]
			queue = queue[1:]
			for _, neighbor := range catalog.Locations[current].Roads {
				if !familyCities[neighbor] || seen[neighbor] {
					continue
				}
				seen[neighbor] = true
				queue = append(queue, neighbor)
			}
		}
		if len(seen) != len(familyCities) {
			t.Fatalf("%s family-road graph reaches %d/%d cities", worldName, len(seen), len(familyCities))
		}
	}
}

func TestFamilyRoadNeighborCanBeTravelledAndIsPersistentlyDiscovered(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	current := "Riverguard City"
	roads := catalog.Locations[current].Roads
	if len(roads) == 0 {
		t.Fatal("Riverguard City has no roads")
	}
	destination := roads[0]

	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=100 WHERE user_id=42`, current)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 301, map[string]any{
		"destination": destination,
		"mode":        "known",
		"game_minute": 3000,
	}))
	if road, _ := result["road_connection"].(bool); !road {
		t.Fatalf("travel did not report road connection: %v", result)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT discovery_kind FROM character_location_discoveries WHERE user_id=42 AND location=?`, destination)); got != "road_travel" {
		t.Fatalf("discovery_kind=%q want road_travel", got)
	}
}

func TestRealmHubUnlockUsesHubThresholdNotNewbornFamilyCityFloor(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3001)
	err := stage4ApplyError(t, path, world, "exploration.travel", 401, map[string]any{
		"destination": "Spirit Jade Capital",
		"mode":        "hub",
	})
	if err == nil {
		t.Fatal("realm-0 actor crossed to Spiritual hub")
	}
	if !strings.Contains(err.Error(), "not yet unlocked") && !strings.Contains(err.Error(), "beyond the character's current cultivation") {
		t.Fatalf("unexpected hub rejection: %v", err)
	}
}
