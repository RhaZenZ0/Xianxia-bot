package game

// v0.23.0 regression tests for `sect.shadow` - the last four rows of the
// v0.21 Authority I backlog.
//
// Two properties. The initiation is one transaction: membership, manual and
// provenance land together or not at all, where the command wrote them as
// three separate calls and a failure in the middle left a Shadow Initiate
// holding a forbidden manual with no record of where it came from - or a
// manual with no membership. And the karma gates are engine-side: a player is
// initiated because the engine reads their karma, not because the Discord
// command read it.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func shadowCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		Realms: []worlddata.Realm{
			{Name: "Qi Refining", World: "Mortal World"},
			{Name: "Foundation", World: "Mortal World"},
		},
		Sects: map[string]worlddata.SectDefinition{
			hiddenSectName: {
				Alignment: "Demonic",
				Hidden:    true,
				Branches: map[string]string{
					"Mortal World":    "Ashen Veil Cell",
					"Spiritual World": "Blood Moon Hall",
				},
				KarmaObservation: -50,
				KarmaInitiation:  -200,
				RighteousEnemy:   50,
			},
		},
		TechniqueSystem: worlddata.TechniqueSystemDefinition{
			Manuals: map[string]worlddata.ManualDefinition{
				"blood_river_scripture": {
					Name: "Blood River Scripture", ItemID: "manual_blood_river",
					Alignment: "Demonic", Path: "Sword Cultivator", MinRealmIndex: 0,
				},
				"corpse_lotus_canon": {
					Name: "Corpse Lotus Canon", ItemID: "manual_corpse_lotus",
					Alignment: "Demonic", Path: "Sword Cultivator", MinRealmIndex: 1,
				},
				"heaven_swallowing_rite": {
					Name: "Heaven-Swallowing Rite", ItemID: "manual_heaven_swallow",
					Alignment: "Demonic", Path: "Sword Cultivator", MinRealmIndex: 6,
				},
				"orthodox_sword_canon": {
					Name: "Orthodox Sword Canon", ItemID: "manual_orthodox",
					Alignment: "Orthodox", Path: "Sword Cultivator", MinRealmIndex: 0,
				},
				"demonic_body_sutra": {
					Name: "Demonic Body Sutra", ItemID: "manual_demon_body",
					Alignment: "Demonic", Path: "Body Cultivator", MinRealmIndex: 0,
				},
			},
		},
	}
}

func setupShadowDB(t *testing.T, karma int64) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE hidden_sect_membership(
	user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL, rank_name TEXT NOT NULL DEFAULT 'Shadow Initiate',
	branch_name TEXT NOT NULL DEFAULT '', standing INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
	joined_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
);
CREATE TABLE item_provenance(
	provenance_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL,
	quantity INTEGER NOT NULL DEFAULT 1, source_type TEXT NOT NULL DEFAULT 'unknown',
	source_key TEXT NOT NULL DEFAULT '', ownership_mark TEXT NOT NULL DEFAULT '',
	legal_status TEXT NOT NULL DEFAULT 'clean', authenticity INTEGER NOT NULL DEFAULT 100,
	tracking_strength INTEGER NOT NULL DEFAULT 0, acquired_game_minute INTEGER NOT NULL DEFAULT 0,
	created_at REAL NOT NULL, updated_at REAL NOT NULL
);
`); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`UPDATE characters SET karma_score=?,realm_index=1,path='Sword Cultivator' WHERE user_id=42`,
		[]any{karma}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func shadowApply(t *testing.T, path, mode string) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(map[string]any{"mode": mode, "game_minute": 4000})
	if err != nil {
		t.Fatal(err)
	}
	mut, actionErr := shadowAction(conn, shadowCatalog(), 42, raw)
	if conn.InTransaction() {
		if actionErr != nil {
			if err := conn.Rollback(); err != nil {
				t.Fatal(err)
			}
		} else if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	if actionErr != nil {
		return nil, actionErr
	}
	result, _ := mut.Result.(map[string]any)
	return result, nil
}

func shadowScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}

func TestInitiationWritesMembershipManualAndProvenanceTogether(t *testing.T) {
	path := setupShadowDB(t, -250)
	result, err := shadowApply(t, path, "initiate")
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(result["branch"]) != "Ashen Veil Cell" {
		t.Fatalf("branch=%v, want the Mortal World cell", result["branch"])
	}
	// realm_index 1, Sword Cultivator: the Corpse Lotus Canon is the highest
	// demonic manual of that path within reach.
	if fmt.Sprint(result["manual_id"]) != "corpse_lotus_canon" {
		t.Fatalf("manual_id=%v", result["manual_id"])
	}
	if got := shadowScalar(t, path,
		`SELECT COUNT(*) FROM hidden_sect_membership WHERE user_id=42 AND status='active'`); got != 1 {
		t.Fatalf("membership rows=%d", got)
	}
	if got := shadowScalar(t, path,
		`SELECT quantity FROM inventory WHERE user_id=42 AND item_id='manual_corpse_lotus'`); got != 1 {
		t.Fatalf("manual quantity=%d", got)
	}
	if got := shadowScalar(t, path,
		`SELECT COUNT(*) FROM item_provenance WHERE user_id=42 AND item_id='manual_corpse_lotus'
		 AND source_type='hidden_sect_initiation' AND legal_status='forbidden' AND tracking_strength=70`); got != 1 {
		t.Fatalf("provenance rows=%d; the forbidden manual must be traceable", got)
	}
}

func TestARefusedInitiationWritesNothing(t *testing.T) {
	for name, karma := range map[string]int64{
		"karma too light": -100, // above the -200 gate
		"righteous":       80,   // at or above the enemy threshold
	} {
		t.Run(name, func(t *testing.T) {
			path := setupShadowDB(t, karma)
			if _, err := shadowApply(t, path, "initiate"); err == nil {
				t.Fatal("the initiation was accepted")
			}
			for table, sql := range map[string]string{
				"membership": `SELECT COUNT(*) FROM hidden_sect_membership`,
				"inventory":  `SELECT COUNT(*) FROM inventory WHERE user_id=42`,
				"provenance": `SELECT COUNT(*) FROM item_provenance`,
			} {
				if got := shadowScalar(t, path, sql); got != 0 {
					t.Fatalf("%s rows=%d after a refused initiation", table, got)
				}
			}
		})
	}
}

func TestCrossingIntoRighteousKarmaTurnsAnExistingMembershipHostile(t *testing.T) {
	// This used to happen only when the player opened the shadow screen, so a
	// cultivator who reformed and never looked stayed an active initiate.
	path := setupShadowDB(t, -250)
	if _, err := shadowApply(t, path, "initiate"); err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, `UPDATE characters SET karma_score=90 WHERE user_id=42`)

	result, err := shadowApply(t, path, "status")
	if err != nil {
		t.Fatal(err)
	}
	membership, _ := result["membership"].(map[string]any)
	if membership == nil || fmt.Sprint(membership["status"]) != "enemy" {
		t.Fatalf("membership=%v, want status enemy", membership)
	}
	if got := shadowScalar(t, path, `SELECT standing FROM hidden_sect_membership WHERE user_id=42`); got != -100 {
		t.Fatalf("standing=%d, want -100", got)
	}
}

func TestTheHostileTransitionHappensOnlyOnce(t *testing.T) {
	// standing takes -100 when the sect turns on you, not -100 per screen open.
	path := setupShadowDB(t, -250)
	if _, err := shadowApply(t, path, "initiate"); err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, `UPDATE characters SET karma_score=90 WHERE user_id=42`)
	for i := 0; i < 3; i++ {
		if _, err := shadowApply(t, path, "status"); err != nil {
			t.Fatal(err)
		}
	}
	if got := shadowScalar(t, path, `SELECT standing FROM hidden_sect_membership WHERE user_id=42`); got != -100 {
		t.Fatalf("standing=%d after three reads, want -100", got)
	}
}

func TestAlreadyActiveInitiatesAreNotReinitiated(t *testing.T) {
	path := setupShadowDB(t, -250)
	if _, err := shadowApply(t, path, "initiate"); err != nil {
		t.Fatal(err)
	}
	if _, err := shadowApply(t, path, "initiate"); err == nil {
		t.Fatal("an active initiate was initiated a second time")
	}
	if got := shadowScalar(t, path,
		`SELECT quantity FROM inventory WHERE user_id=42 AND item_id='manual_corpse_lotus'`); got != 1 {
		t.Fatalf("manual quantity=%d; a second initiation handed out another copy", got)
	}
}

func TestStatusReportsTheKarmaGatesWithoutInitiating(t *testing.T) {
	path := setupShadowDB(t, -60)
	result, err := shadowApply(t, path, "status")
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["karma_observation"]); got != -50 {
		t.Fatalf("karma_observation=%d", got)
	}
	if got := storage.ParseInt(result["karma_initiation"]); got != -200 {
		t.Fatalf("karma_initiation=%d", got)
	}
	if result["membership"] != nil {
		t.Fatalf("membership=%v; a status read must not create one", result["membership"])
	}
	if got := shadowScalar(t, path, `SELECT COUNT(*) FROM hidden_sect_membership`); got != 0 {
		t.Fatalf("membership rows=%d after a status read", got)
	}
}

func TestTheManualIsChosenByAlignmentPathAndReach(t *testing.T) {
	catalog := shadowCatalog()
	// Sword Cultivator at realm 1: the demonic manuals of that path within
	// reach are Blood River (0) and Corpse Lotus (1); the highest wins.
	if id, _, ok := shadowInitiationManual(catalog, "Sword Cultivator", 1); !ok || id != "corpse_lotus_canon" {
		t.Fatalf("realm 1 chose %q (ok=%v)", id, ok)
	}
	// At realm 0 only Blood River is within reach.
	if id, _, ok := shadowInitiationManual(catalog, "Sword Cultivator", 0); !ok || id != "blood_river_scripture" {
		t.Fatalf("realm 0 chose %q (ok=%v)", id, ok)
	}
	// A path with no demonic manual gets nothing rather than someone else's.
	if id, _, ok := shadowInitiationManual(catalog, "Formation Master", 9); ok {
		t.Fatalf("a pathless match returned %q", id)
	}
	// And an orthodox manual is never handed out by a demonic cell, even when
	// it is the only one of the right path within reach.
	if _, manual, ok := shadowInitiationManual(catalog, "Body Cultivator", 0); !ok || manual.ItemID != "manual_demon_body" {
		t.Fatalf("body cultivator got %v (ok=%v)", manual.ItemID, ok)
	}
}

func TestAnInitiateWhoseCellHasNoManualStillJoins(t *testing.T) {
	// The membership is the point; the manual is a bonus that may not exist
	// for this path. Python treated it that way and so must this.
	path := setupShadowDB(t, -250)
	batch4Exec(t, path, `UPDATE characters SET path='Formation Master' WHERE user_id=42`)
	result, err := shadowApply(t, path, "initiate")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := result["manual_id"]; ok {
		t.Fatalf("a manual was granted for a path with none: %v", result["manual_id"])
	}
	if got := shadowScalar(t, path,
		`SELECT COUNT(*) FROM hidden_sect_membership WHERE user_id=42 AND status='active'`); got != 1 {
		t.Fatalf("membership rows=%d", got)
	}
}

func TestAnUnknownShadowModeIsRefused(t *testing.T) {
	path := setupShadowDB(t, -250)
	_, err := shadowApply(t, path, "enrol_everyone")
	if err == nil {
		t.Fatal("an unknown mode was accepted")
	}
	if !strings.Contains(err.Error(), "unknown shadow mode") {
		t.Fatalf("refusal was %q", err)
	}
}
