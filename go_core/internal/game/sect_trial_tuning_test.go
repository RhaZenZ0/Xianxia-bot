package game

// A sect's trial reads what the sect authored (v1.2.4): `base_tn`,
// `path_bonuses`, `root_affinities`, `family_archetype_bonus` and
// `karma_preference` were in the content and in the notes the bot printed,
// and the engine read none of them.

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

func forcedAzureTrial(t *testing.T, path string, seq int) map[string]any {
	t.Helper()
	restore := gamerng.UseRoller(func(int) int { return 4 })
	defer restore()
	return sitTrial(t, path, batch4WorldPath(t), "Azure Cloud Sect", "Azure Cloud Mountain Gate", seq)
}

func TestTheTrialReadsTheSectsOwnTuning(t *testing.T) {
	path := setupSectTrialDB(t)
	// A Sword Cultivator with a Metal root and a good name, at the Azure Cloud
	// gate: +2 for the path, +1 for the root, +1 for the karma, on the sect's
	// own base TN of 14.
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=0,path='Sword Cultivator',spiritual_root='Metal Root',karma_score=60,attributes_json='{"body":2,"agility":2,"spirit":2,"insight":2,"will":2,"presence":2}' WHERE user_id=42`)
	result := forcedAzureTrial(t, path, 1)
	tuning, _ := result["tuning"].(map[string]any)
	if tuning == nil {
		t.Fatalf("the trial reports no tuning: %v", result)
	}
	if got := storage.ParseInt(tuning["base_tn"]); got != 14 {
		t.Fatalf("base_tn=%d, want the Azure Cloud Sect's authored 14", got)
	}
	for key, want := range map[string]int64{"path_bonus": 2, "root_affinity": 1, "karma_adjustment": 1, "bonus": 4} {
		if got := storage.ParseInt(tuning[key]); got != want {
			t.Fatalf("%s=%d, want %d (%v)", key, got, want, tuning)
		}
	}
	primary, _ := result["primary"].(map[string]any)
	if got := storage.ParseInt(primary["tn"]); got != 14 {
		t.Fatalf("primary tn=%d, want 14: the trial is still rolling against the literal 15", got)
	}
	// 5+5 + body 2 + the sect's +4 = 16 against 14.
	if got := storage.ParseInt(primary["total"]); got != 16 {
		t.Fatalf("primary total=%d, want 16: the authored bonus is not on the roll", got)
	}
}

func TestAHouseholdTraditionCountsAtTheGate(t *testing.T) {
	path := setupSectTrialDB(t)
	batch4Exec(t, path, `INSERT INTO birth_families(family_id,archetype) VALUES(7,'alchemy_family')`)
	batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id) VALUES(42,7)`)
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=0,path='Beast Binder',spiritual_root='Water Root',karma_score=0,location='Crimson Furnace Valley' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO character_sect_discoveries(user_id,sect_name,created_at) VALUES(42,'Crimson Furnace Sect',0)`)
	restore := gamerng.UseRoller(func(int) int { return 4 })
	defer restore()
	result := sitTrial(t, path, batch4WorldPath(t), "Crimson Furnace Sect", "Crimson Furnace Valley", 1)
	tuning, _ := result["tuning"].(map[string]any)
	if got := storage.ParseInt(tuning["family_bonus"]); got != 2 {
		t.Fatalf("family_bonus=%d, want the Crimson Furnace Sect's +2 for an alchemy family (%v)", got, tuning)
	}
}

func TestAnOrthodoxSectRefusesANotoriousApplicantWithoutASponsor(t *testing.T) {
	path := setupSectTrialDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET karma_score=-300 WHERE user_id=42`)
	_, err := batch4ApplyErr(path, world, "sect.recruitment.trial", 42, 1, map[string]any{"sect_name": "Azure Cloud Sect", "trial_name": "Entrance"})
	if err == nil || !strings.Contains(err.Error(), "notorious") {
		t.Fatalf("a karma -300 applicant sat the Azure Cloud trial: %v", err)
	}
	// A sponsor vouches, and the ledger still costs two on the roll.
	batch4Exec(t, path, `INSERT INTO sect_recommendations(user_id,npc_name,sect_name,bonus,status,created_at,updated_at) VALUES(42,'Inquisitor Shen Rui','Azure Cloud Sect',1,'active',0,0)`)
	result := forcedAzureTrial(t, path, 2)
	tuning, _ := result["tuning"].(map[string]any)
	if got := storage.ParseInt(tuning["karma_adjustment"]); got != -2 {
		t.Fatalf("karma_adjustment=%d, want -2 (%v)", got, fmt.Sprint(tuning))
	}
}
