package game

import (
	"encoding/json"
	"strings"
	"testing"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Reported from play (v1.2.1): "it says loyalty should be 110, but I can't go
// over 100". The requirement climbed ten a stage past the cap feed and train
// clamp loyalty at, so stage five asked for a number no action could produce.
func evolveBeast(t *testing.T, path, world string, seq int) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"beast_id": 1})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "evolve-cap-" + string(rune('a'+seq)), Operation: "beast.evolve", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	return out.Result.(map[string]any), nil
}

func TestABeastAtTheLoyaltyCapCanAlwaysEvolve(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',6,'Wind',10,'bonded','Common',5,100,'equality',1,'[]',0,0)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'beast_core',1)`)
	got, err := evolveBeast(t, path, world, 0)
	if err != nil {
		t.Fatalf("a beast at the loyalty cap was refused: %v", err)
	}
	beast, _ := got["beast"].(map[string]any)
	if storage.ParseInt(beast["evolution_stage"]) != 6 {
		t.Fatalf("evolution_stage=%v want 6", beast["evolution_stage"])
	}
	// v1.7.7: an evolution spends the bond, so the next one is a long climb.
	if got := storage.ParseInt(beast["loyalty"]); got != beastEvolvedLoyalty {
		t.Fatalf("loyalty after evolving is %d, want %d - one Train and a core must not buy the next stage back", got, beastEvolvedLoyalty)
	}
}

func TestTheEvolutionRequirementNeverNamesANumberAboveTheCap(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',6,'Wind',10,'bonded','Common',5,99,'equality',1,'[]',0,0)`)
	_, err := evolveBeast(t, path, world, 1)
	if err == nil {
		t.Fatal("loyalty 99 evolved a stage-5 beast")
	}
	if !strings.Contains(err.Error(), "requirement 100") || strings.Contains(err.Error(), "110") {
		t.Fatalf("the refusal must ask for the cap and nothing above it: %v", err)
	}
}

func TestABeastCannotOutgrowTheWorldItStandsIn(t *testing.T) {
	for world, limit := range beastRankLimits {
		if limit != map[string]int64{"Mortal World": 25, "Spiritual World": 50, "Immortal World": 75, "Celestial World": 100}[world] {
			t.Fatalf("%s limit is %d", world, limit)
		}
	}
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',25,'Wind',10,'bonded','Common',5,100,'equality',1,'[]',0,0)`)
	_, err := evolveBeast(t, path, world, 0)
	if err == nil || !strings.Contains(err.Error(), "rank 25") {
		t.Fatalf("a rank-25 beast in the Mortal World evolved: %v", err)
	}
	if beastRankLimit("Spiritual World") != 50 || beastRankLimit("nowhere") != 25 {
		t.Fatal("the limit is not read per world")
	}
}

func TestFromRankTenEveryLevelCostsCores(t *testing.T) {
	if beastLevelCores(9) != 1 || beastLevelCores(0) != 1 || beastLevelCores(10) != 8 || beastLevelCores(15) != 8 || beastLevelCores(20) != 16 {
		t.Fatal("cores are 1 a level below 10, 8 from rank 10, 16 from 20")
	}
	if beastMilestoneBonus(9) != 0 || beastMilestoneBonus(10) != 2 || beastMilestoneBonus(25) != 4 {
		t.Fatal("milestone bonus is +2 per tenth rank")
	}
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',10,'Wind',10,'bonded','Common',5,100,'equality',1,'[]',0,0)`)
	if _, err := evolveBeast(t, path, world, 0); err == nil || !strings.Contains(err.Error(), "8 beast cores") {
		t.Fatalf("10 -> 11 went without cores: %v", err)
	}
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'beast_core',8)`)
	got, err := evolveBeast(t, path, world, 1)
	if err != nil {
		t.Fatalf("10 -> 11 with eight cores was refused: %v", err)
	}
	if beast, _ := got["beast"].(map[string]any); storage.ParseInt(beast["rank"]) != 11 {
		t.Fatalf("rank is %v, want 11", beast["rank"])
	}
}

// --- from beast_grandfather_test.go ---

func grandfatherBeast(t *testing.T, cores int64, rank int64) (int64, int64, int64) {
	return grandfatherBeastAt(t, cores, rank, -1)
}

func grandfatherBeastAt(t *testing.T, cores int64, rank int64, realm int64) (int64, int64, int64) {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `ALTER TABLE spirit_beasts ADD COLUMN grandfathered INTEGER NOT NULL DEFAULT 1`)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at,grandfathered
	) VALUES(42,'Mistclaw','Mistclaw Wolf',?,'Wind',10,'bonded','Common',16,80,'equality',1,'[]',0,0,0)`, rank)
	if realm >= 0 {
		batch4Exec(t, path, `UPDATE characters SET realm_index=? WHERE user_id=42`, realm)
	}
	if cores > 0 {
		batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'beast_core',?)`, cores)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	if err := settleGrandfatheredBeastsTx(conn, catalog, 42); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	r, _ := conn.Execute(`SELECT rank,grandfathered,(SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='beast_core') FROM spirit_beasts WHERE user_id=42`, nil)
	return i64(r.Rows[0][0]), i64(r.Rows[0][1]), i64(r.Rows[0][2])
}

func TestAnOldBeastPaysForItsLevels(t *testing.T) {
	// Rank 16, stage 16: 0..9 at 1 each, then 10..15 at 8 each = 10 + 48.
	if rank, mark, left := grandfatherBeast(t, 60, 16); rank != 16 || mark != 1 || left != 2 {
		t.Fatalf("rank=%d mark=%d cores left=%d, want 16/1/2", rank, mark, left)
	}
	// 30 cores pays 0..9 and two levels past 10, and stops at 12.
	if rank, mark, left := grandfatherBeast(t, 30, 16); rank != 12 || mark != 1 || left != 4 {
		t.Fatalf("rank=%d mark=%d left=%d, want 12/1/4", rank, mark, left)
	}
	// No cores: back to where it was tamed.
	if rank, _, _ := grandfatherBeast(t, 0, 16); rank != 0 {
		t.Fatalf("rank=%d, want 0", rank)
	}
}

func TestAnOldBeastIsLoweredToItsWorldsLimit(t *testing.T) {
	// Rank 35, stage 16 (tamed at 19) in the Mortal World: lowered to 25,
	// then 19 -> 25 is owed: 8 + 5x16 = 88.
	if rank, _, left := grandfatherBeast(t, 88, 35); rank != 25 || left != 0 {
		t.Fatalf("rank=%d cores left=%d, want 25/0", rank, left)
	}
}

func TestTheOwnersRealmDecidesTheLimitBeforeWhereTheyStand(t *testing.T) {
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	spiritual := int64(-1)
	for i, r := range catalog.Realms {
		if r.World == "Spiritual World" {
			spiritual = int64(i)
			break
		}
	}
	if spiritual < 0 {
		t.Fatal("the catalogue carries no Spiritual World realm; the test is broken, not the tree")
	}
	// Standing in the Mortal World at a Spiritual World realm: 35 is under 50,
	// so it keeps its rank and pays for 19..34, the sixteen levels it evolved
	// (1x8 + 10x16 + 5x24 = 288).
	if rank, _, left := grandfatherBeastAt(t, 288, 35, spiritual); rank != 35 || left != 0 {
		t.Fatalf("rank=%d cores left=%d, want 35/0 - a Spiritual cultivator at home was held to the Mortal limit", rank, left)
	}
}
