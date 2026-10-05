package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// v1.27.0: a player's claim on neutral ground wrote no history, while the
// world's own sects wrote `territory_claimed` for theirs. One statement now.
func TestAPlayersClaimIsHeardLikeASects(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS territory_state(territory_key TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50, defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS world_history_events(history_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT UNIQUE, event_type TEXT, title TEXT, summary TEXT, significance INTEGER, visibility TEXT, location TEXT, world_name TEXT, faction TEXT, actor_type TEXT, actor_key TEXT, actor_name TEXT, target_type TEXT, target_key TEXT, target_name TEXT, related_user_id INTEGER, related_npc_name TEXT, tags TEXT, game_minute INTEGER, metadata_json TEXT, created_at REAL, updated_at REAL)`)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,'Azure Cloud Sect','Deacon',40,0)`)
	batch4Exec(t, path, `INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES('Riverguard City','Riverguard City','Riverguard City',0)`)
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "territory.claim", 42, 1, map[string]any{"territory_key": "Riverguard City"}); err != nil {
		t.Fatalf("the claim refused: %v", err)
	}
	row := fmt.Sprint(actionScalar(t, path, `SELECT event_type||'|'||significance||'|'||actor_key FROM world_history_events WHERE location='Riverguard City'`))
	if row != "territory_claimed|60|Azure Cloud Sect" {
		t.Fatalf("a player's claim left history %q, want the world's own territory_claimed row", row)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_history_events`)); n != 1 {
		t.Fatalf("%d history rows, want 1", n)
	}
}

// v1.27.0: an awakened artifact bond counted in every fight whether or not
// the cultivator still had the artifact.
func TestABondCountsOnlyWhileTheArtifactIsHeld(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/bond.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE spirit_beasts(beast_id INTEGER PRIMARY KEY, user_id INTEGER, rank INTEGER, evolution_stage INTEGER, loyalty INTEGER, active INTEGER);
CREATE TABLE artifact_bonds(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, bond_level INTEGER NOT NULL DEFAULT 0, resonance INTEGER NOT NULL DEFAULT 0, awakened INTEGER NOT NULL DEFAULT 0, spirit_name TEXT NOT NULL DEFAULT '', temperament TEXT NOT NULL DEFAULT 'dormant', created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id,item_id));
CREATE TABLE inventory(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,item_id));
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL, slot TEXT NOT NULL DEFAULT 'weapon', durability INTEGER NOT NULL DEFAULT 10, max_durability INTEGER NOT NULL DEFAULT 10, quality INTEGER NOT NULL DEFAULT 100, equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
INSERT INTO artifact_bonds(user_id,item_id,bond_level,awakened) VALUES(42,'spirit_iron_sword',8,1);
`); err != nil {
		t.Fatal(err)
	}
	bonus := func() int64 {
		t.Helper()
		b, err := combatCompanionBonus(conn, 42)
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	if b := bonus(); b != 0 {
		t.Fatalf("a bond with a sword the cultivator no longer has is worth %d in a fight, want 0", b)
	}
	if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron_sword@high',1)`, nil); err != nil {
		t.Fatal(err)
	}
	if b := bonus(); b != 3 {
		t.Fatalf("carrying the sword at a grade, the bond is worth %d, want 3", b)
	}
	if err := conn.ExecScript(`DELETE FROM inventory; INSERT INTO equipment_instances(user_id,item_id,equipped) VALUES(42,'spirit_iron_sword',1);`); err != nil {
		t.Fatal(err)
	}
	if b := bonus(); b != 3 {
		t.Fatalf("bound as equipment, the bond is worth %d, want 3", b)
	}
}
