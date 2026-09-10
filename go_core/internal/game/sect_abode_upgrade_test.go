package game

// The sect residence grows (v0.30.1): contribution points pay, rank caps the
// level, stage gates each step, and the homestead is earned part-way up the
// ladder.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupSectResidenceDB(t *testing.T) string {
	t.Helper()
	path := setupPropertyTypesDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE sect_abodes(
	user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL, name TEXT NOT NULL,
	location_key TEXT NOT NULL UNIQUE, base_location TEXT NOT NULL,
	thread_id INTEGER, thread_channel_id INTEGER, created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0,
	cultivation_level INTEGER NOT NULL DEFAULT 1, alchemy_level INTEGER NOT NULL DEFAULT 0, forge_level INTEGER NOT NULL DEFAULT 0,
	formation_level INTEGER NOT NULL DEFAULT 0, storage_level INTEGER NOT NULL DEFAULT 1, herb_garden_level INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE sect_membership ADD COLUMN contribution_points INTEGER NOT NULL DEFAULT 0;
INSERT INTO sect_abodes(user_id,sect_name,name,location_key,base_location) VALUES(42,'Azure Cloud Sect','Lin Test''s Disciple Courtyard','sect_abode:42','Cloudspine Foothills');
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func setMembership(t *testing.T, path string, rankName string, rankLevel, points int64) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,contribution_points) VALUES(42,'Azure Cloud Sect',?,?,?) ON CONFLICT(user_id) DO UPDATE SET rank_name=excluded.rank_name,rank_level=excluded.rank_level,contribution_points=excluded.contribution_points`, rankName, rankLevel, points)
}

func upgradeResidence(t *testing.T, path, world, facility string, seq int) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"facility": facility})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("residence-%s-%d", facility, seq), Operation: "sect.abode.upgrade", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func TestSectResidenceBuildCostsContributionPoints(t *testing.T) {
	path := setupSectResidenceDB(t)
	world := batch4WorldPath(t)
	setMembership(t, path, "Inner Disciple", 20, 200)
	result, err := upgradeResidence(t, path, world, "herb_garden", 1)
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(result["level"]) != 1 || result["built"] != true || storage.ParseInt(result["cost"]) != 40 || storage.ParseInt(result["remaining_points"]) != 160 {
		t.Fatalf("build=%#v", result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT herb_garden_level FROM sect_abodes WHERE user_id=42")); got != 1 {
		t.Fatalf("herb_garden_level=%d want 1", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT contribution_points FROM sect_membership WHERE user_id=42")); got != 160 {
		t.Fatalf("points=%d want 160", got)
	}
	// Level 1 -> 2 costs four times the base and this Inner Disciple, at
	// Body Tempering, lacks the stage for it; nothing is spent.
	_, err = upgradeResidence(t, path, world, "herb_garden", 2)
	if err == nil || !strings.Contains(err.Error(), "Qi Refining") {
		t.Fatalf("stage gate: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT contribution_points FROM sect_membership WHERE user_id=42")); got != 160 {
		t.Fatalf("a refused upgrade spent points: %d", got)
	}
}

func TestSectResidenceRankCapsTheLevel(t *testing.T) {
	path := setupSectResidenceDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=5 WHERE user_id=42`)
	setMembership(t, path, "Outer Disciple", 10, 10000)
	// An Outer Disciple's cap is 1: the chamber is already there.
	_, err := upgradeResidence(t, path, world, "cultivation", 1)
	if err == nil || !strings.Contains(err.Error(), "Inner Disciple") {
		t.Fatalf("outer disciple cap: %v", err)
	}
	setMembership(t, path, "Deacon", 40, 10000)
	for level := int64(2); level <= 4; level++ {
		result, err := upgradeResidence(t, path, world, "cultivation", int(level))
		if err != nil {
			t.Fatalf("level %d: %v", level, err)
		}
		if storage.ParseInt(result["level"]) != level || storage.ParseInt(result["cost"]) != 40*level*level {
			t.Fatalf("level %d result=%#v", level, result)
		}
	}
	_, err = upgradeResidence(t, path, world, "cultivation", 5)
	if err == nil || !strings.Contains(err.Error(), "Elder") {
		t.Fatalf("deacon cap: %v", err)
	}
}

func TestSectResidenceRefusesTheUnaffiliatedAndTheUnopened(t *testing.T) {
	world := batch4WorldPath(t)
	path := setupSectResidenceDB(t)
	if _, err := upgradeResidence(t, path, world, "alchemy", 1); err == nil || !strings.Contains(err.Error(), "public sect member") {
		t.Fatalf("no membership: %v", err)
	}
	setMembership(t, path, "Core Disciple", 30, 500)
	batch4Exec(t, path, `DELETE FROM sect_abodes`)
	if _, err := upgradeResidence(t, path, world, "alchemy", 2); err == nil || !strings.Contains(err.Error(), "/sect abode") {
		t.Fatalf("no residence row: %v", err)
	}
	if _, err := upgradeResidence(t, path, world, "beast_pen", 3); err == nil || !strings.Contains(err.Error(), "develops one of") {
		t.Fatalf("a facility the residence does not have: %v", err)
	}
	setMembership(t, path, "Core Disciple", 30, 10)
	batch4Exec(t, path, `INSERT INTO sect_abodes(user_id,sect_name,name,location_key,base_location) VALUES(42,'Azure Cloud Sect','Courtyard','sect_abode:42','Cloudspine Foothills')`)
	if _, err := upgradeResidence(t, path, world, "alchemy", 4); err == nil || !strings.Contains(err.Error(), "40 needed, 10 held") {
		t.Fatalf("points: %v", err)
	}
}

func TestSeclusionInsideTheSectResidenceUsesItsChamber(t *testing.T) {
	path := setupSectResidenceDB(t)
	world := batch4WorldPath(t)
	setMembership(t, path, "Deacon", 40, 0)
	batch4Exec(t, path, `UPDATE sect_abodes SET cultivation_level=3 WHERE user_id=42`)
	batch4Exec(t, path, `UPDATE characters SET location='sect_abode:42' WHERE user_id=42`)
	// The manor stands at the residence's seat, so its array reaches inside.
	batch4Exec(t, path, `INSERT INTO sect_manors(sect_name,name,base_location,qi_array_level) VALUES('Azure Cloud Sect','Azure Hall','Cloudspine Foothills',1)`)
	result := batch4Result(t, batch4Apply(t, path, world, "seclusion.start", 1, map[string]any{"mode": "qi", "duration_game_minutes": 1440, "game_minute": 1000}))
	if got := parseFloat(result["environment_mult"]); !nearly(got, 1.20*1.08) {
		t.Fatalf("environment_mult=%v want %v", got, 1.20*1.08)
	}
	env := result["environment"].(map[string]any)
	if env["site"] != "sect_abode" || storage.ParseInt(env["abode_level"]) != 3 {
		t.Fatalf("environment=%#v", env)
	}
}

func TestFoundingAHomesteadIsEarnedPartWayUpTheLadder(t *testing.T) {
	world := batch4WorldPath(t)
	path := setupSectResidenceDB(t)
	if _, err := establishProperty(t, path, world, ""); err == nil || !strings.Contains(err.Error(), "Deacon (rank 40)") {
		t.Fatalf("unaffiliated: %v", err)
	}
	setMembership(t, path, "Core Disciple", 30, 0)
	if _, err := establishProperty(t, path, world, ""); err == nil || !strings.Contains(err.Error(), "Deacon (rank 40)") {
		t.Fatalf("core disciple: %v", err)
	}
	setMembership(t, path, "Deacon", 40, 0)
	if _, err := establishProperty(t, path, world, ""); err != nil {
		t.Fatalf("deacon: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM cave_abodes")); got != 1 {
		t.Fatalf("rows=%d want 1", got)
	}
}
