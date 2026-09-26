package game

import (
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

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
	// Rank 16: six levels climbed from 10 at 8 cores each = 48.
	if rank, mark, left := grandfatherBeast(t, 50, 16); rank != 16 || mark != 1 || left != 2 {
		t.Fatalf("rank=%d mark=%d cores left=%d, want 16/1/2", rank, mark, left)
	}
	// 20 cores pays two levels (10->11, 11->12) and stops at 12.
	if rank, mark, left := grandfatherBeast(t, 20, 16); rank != 12 || mark != 1 || left != 4 {
		t.Fatalf("rank=%d mark=%d left=%d, want 12/1/4", rank, mark, left)
	}
	// No cores: kept at 10, which cost nothing to reach.
	if rank, _, _ := grandfatherBeast(t, 0, 16); rank != 10 {
		t.Fatalf("rank=%d, want 10", rank)
	}
}

func TestAnOldBeastIsLoweredToItsWorldsLimit(t *testing.T) {
	// Rank 35 in the Mortal World: lowered to 20, then 10 levels x 8 = 80.
	if rank, _, left := grandfatherBeast(t, 80, 35); rank != 20 || left != 0 {
		t.Fatalf("rank=%d cores left=%d, want 20/0", rank, left)
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
	// Standing in the Mortal World at a Spiritual World realm: 35 is under 40,
	// so it keeps its rank and pays for 10..34 (10x8 + 10x16 + 5x24 = 360).
	if rank, _, left := grandfatherBeastAt(t, 360, 35, spiritual); rank != 35 || left != 0 {
		t.Fatalf("rank=%d cores left=%d, want 35/0 - a Spiritual cultivator at home was held to the Mortal limit", rank, left)
	}
}
