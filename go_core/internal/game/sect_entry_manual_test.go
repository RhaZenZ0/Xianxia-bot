package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// v0.21.3: one manual on joining a sect. The trial is the only public way
// into a sect, so the gift lives inside its transaction.

func setupSectTrialDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL);
CREATE TABLE character_sect_discoveries(user_id INTEGER NOT NULL,sect_name TEXT NOT NULL,discovery_kind TEXT NOT NULL DEFAULT 'rumor',source_key TEXT NOT NULL DEFAULT '',discovered_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,PRIMARY KEY(user_id,sect_name));
CREATE TABLE sect_recommendations(recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,npc_name TEXT NOT NULL,sect_name TEXT NOT NULL,bonus INTEGER NOT NULL DEFAULT 2,status TEXT NOT NULL DEFAULT 'active',issued_game_minute INTEGER NOT NULL DEFAULT 0,used_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE sect_recruitment_attempts(attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,sect_name TEXT NOT NULL,attempt_type TEXT NOT NULL,npc_name TEXT NOT NULL DEFAULT '',location TEXT NOT NULL DEFAULT '',result TEXT NOT NULL,score INTEGER NOT NULL DEFAULT 0,target INTEGER NOT NULL DEFAULT 0,recommendation_bonus INTEGER NOT NULL DEFAULT 0,details_json TEXT NOT NULL DEFAULT '{}',game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS character_manuals(user_id INTEGER NOT NULL,manual_id TEXT NOT NULL,mastery INTEGER NOT NULL DEFAULT 0,practice INTEGER NOT NULL DEFAULT 0,learned_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(user_id,manual_id));
CREATE TABLE item_provenance(provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,source_type TEXT NOT NULL DEFAULT 'unknown',source_key TEXT NOT NULL DEFAULT '',ownership_mark TEXT NOT NULL DEFAULT '',legal_status TEXT NOT NULL DEFAULT 'clean',authenticity INTEGER NOT NULL DEFAULT 100,tracking_strength INTEGER NOT NULL DEFAULT 0,acquired_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
`); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()
	// Character 42 has every attribute at 100, so both 2d10 rolls clear any
	// TN: the trial passes deterministically. Move them to the gate and let
	// them know the sect exists.
	batch4Exec(t, path, `UPDATE characters SET location='Azure Cloud Mountain Gate' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO character_sect_discoveries(user_id,sect_name,created_at) VALUES(42,'Azure Cloud Sect',0)`)
	batch4Exec(t, path, `INSERT INTO character_sect_discoveries(user_id,sect_name,created_at) VALUES(42,'Blood River Sect',0)`)
	return path
}

func sitTrial(t *testing.T, path, world, sect, location string, seq int) map[string]any {
	t.Helper()
	out := batch4Apply(t, path, world, "sect.recruitment.trial", seq, map[string]any{
		"sect_name": sect, "examiner": "Gate Elder Jian Mu", "location": location, "trial_name": "Entrance",
	})
	return batch4Result(t, out)
}

func TestSectTrialPassGrantsTheEntryManual(t *testing.T) {
	path := setupSectTrialDB(t)
	world := batch4WorldPath(t)
	result := sitTrial(t, path, world, "Azure Cloud Sect", "Azure Cloud Mountain Gate", 1)
	if got := fmt.Sprint(result["outcome"]); got != "pass" {
		t.Fatalf("outcome=%s (attributes at 100 must pass)", got)
	}
	granted, ok := result["granted_manual"].(map[string]any)
	if !ok {
		t.Fatalf("no granted_manual in result: %v", result)
	}
	manualID := fmt.Sprint(granted["manual_id"])
	itemID := fmt.Sprint(granted["item_id"])
	if !strings.EqualFold(fmt.Sprint(granted["alignment"]), "Orthodox") {
		t.Fatalf("an orthodox sect handed out %v", granted["alignment"])
	}
	// v0.21.4: the sect's own authored entry inheritance, tier 0, path-agnostic.
	if manualID != "azure_cloud_foundation_sword_canon" {
		t.Fatalf("expected the Azure Cloud Sect's own entry manual, got %s", manualID)
	}
	if tier := storage.ParseInt(granted["min_realm_index"]); tier != 0 {
		t.Fatalf("a genuine entry manual is tier 0, got %d", tier)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, itemID)); got != 1 {
		t.Fatalf("manual item not in inventory: %d", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT source_type FROM item_provenance WHERE user_id=42 AND item_id=?`, itemID)); got != "sect_entry" {
		t.Fatalf("provenance=%s", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT legal_status FROM item_provenance WHERE user_id=42 AND item_id=?`, itemID)); got != "clean" {
		t.Fatalf("an orthodox manual is clean, got %s", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT sect_name FROM sect_membership WHERE user_id=42`)); got != "Azure Cloud Sect" {
		t.Fatalf("membership=%s", got)
	}
	// And it is learnable at once: the same catalog the trial drew from is
	// the one manual.study reads, and a tier-0 manual needs no growing into.
	batch4SetCanonicalGameMinute(t, path, 10)
	study := batch4Apply(t, path, world, "manual.study", 2, map[string]any{"manual_id": manualID, "cooldown_seconds": 1})
	if got := fmt.Sprint(batch4Result(t, study)["first_study"]); got != "true" {
		t.Fatalf("study result=%v", batch4Result(t, study))
	}
}

func TestSectEntryManualSelectionRules(t *testing.T) {
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	sword := mechanicsCharacter{Path: "Sword Cultivator", RealmIndex: 3}

	// Every public sect's first gift is its own authored tier-0 entry manual,
	// whatever the character's path.
	for sect, def := range catalog.Sects {
		if def.Hidden {
			continue
		}
		for _, who := range []mechanicsCharacter{sword, {Path: "Beast Binder", RealmIndex: 0}} {
			id := sectEntryManual(catalog, sect, who, map[string]bool{})
			m := catalog.TechniqueSystem.Manuals[id]
			if m.Sect != sect || m.MinRealmIndex != 0 {
				t.Fatalf("%s gave %q (sect=%q tier=%d) to a %s", sect, id, m.Sect, m.MinRealmIndex, who.Path)
			}
			if !strings.EqualFold(m.Alignment, def.Alignment) {
				t.Fatalf("%s (%s) entry manual is %s", sect, def.Alignment, m.Alignment)
			}
		}
	}

	// With the sect's own manual already owned, the general rule applies.
	entryAzure := sectEntryManual(catalog, "Azure Cloud Sect", sword, map[string]bool{})
	ownedEntry := map[string]bool{entryAzure: true}
	orthodox := sectEntryManual(catalog, "Azure Cloud Sect", sword, ownedEntry)
	if orthodox == "" || orthodox == entryAzure || !strings.EqualFold(catalog.TechniqueSystem.Manuals[orthodox].Alignment, "Orthodox") {
		t.Fatalf("orthodox sect, entry owned: %q", orthodox)
	}
	if catalog.TechniqueSystem.Manuals[orthodox].Path != "Sword Cultivator" {
		t.Fatalf("path match first: %q", orthodox)
	}

	entryBlood := sectEntryManual(catalog, "Blood River Sect", sword, map[string]bool{})
	demonic := sectEntryManual(catalog, "Blood River Sect", sword, map[string]bool{entryBlood: true})
	if demonic == "" || !strings.EqualFold(catalog.TechniqueSystem.Manuals[demonic].Alignment, "Demonic") {
		t.Fatalf("demonic sect: %q", demonic)
	}

	entrySerpent := sectEntryManual(catalog, "Black Serpent Clan", sword, map[string]bool{})
	neutral := sectEntryManual(catalog, "Black Serpent Clan", sword, map[string]bool{entrySerpent: true})
	if neutral == "" || strings.EqualFold(catalog.TechniqueSystem.Manuals[neutral].Alignment, "Demonic") {
		t.Fatalf("a neutral sect never hands out a forbidden art: %q", neutral)
	}

	// Already owned: the next one, never a duplicate.
	owned := map[string]bool{entryAzure: true, orthodox: true}
	second := sectEntryManual(catalog, "Azure Cloud Sect", sword, owned)
	if second == "" || second == orthodox || second == entryAzure {
		t.Fatalf("duplicate or nothing: %q", second)
	}

	// Tier (general rule, entry owned): within reach beats out of reach; out
	// of reach picks the lowest.
	high := mechanicsCharacter{Path: "Sword Cultivator", RealmIndex: 9}
	reachable := sectEntryManual(catalog, "Azure Cloud Sect", high, ownedEntry)
	if m := catalog.TechniqueSystem.Manuals[reachable]; m.MinRealmIndex > 9 {
		t.Fatalf("realm 9 got a tier-%d manual with lower ones available", m.MinRealmIndex)
	}
	if m := catalog.TechniqueSystem.Manuals[reachable]; m.MinRealmIndex != 2 {
		t.Fatalf("the lowest reachable tier is the entry manual, got %d", m.MinRealmIndex)
	}
	mortal := mechanicsCharacter{Path: "Sword Cultivator", RealmIndex: 0}
	entry := sectEntryManual(catalog, "Azure Cloud Sect", mortal, ownedEntry)
	if entry != reachable {
		t.Fatalf("a fresh Mortal gets the same entry manual to grow into: %q vs %q", entry, reachable)
	}

	// A path the catalog has nothing for still gets a manual of the right alignment.
	odd := mechanicsCharacter{Path: "Rogue Cultivator", RealmIndex: 2}
	fallback := sectEntryManual(catalog, "Azure Cloud Sect", odd, ownedEntry)
	if fallback == "" || !strings.EqualFold(catalog.TechniqueSystem.Manuals[fallback].Alignment, "Orthodox") {
		t.Fatalf("fallback: %q", fallback)
	}

	// Deterministic: the same inputs pick the same manual every time.
	for i := 0; i < 20; i++ {
		if again := sectEntryManual(catalog, "Azure Cloud Sect", sword, ownedEntry); again != orthodox {
			t.Fatalf("selection depends on map order: %q vs %q", again, orthodox)
		}
	}
}

func TestSectTrialFailGrantsNothing(t *testing.T) {
	path := setupSectTrialDB(t)
	world := batch4WorldPath(t)
	// Attributes at 1: 2d10+1+0 against TN 15 fails both rolls (max 21 - but
	// margins combined must reach +2 for a pass; force it with a hopeless mod).
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":-40,"agility":-40,"spirit":-40,"insight":-40,"will":-40,"presence":-40}' WHERE user_id=42`)
	result := sitTrial(t, path, world, "Azure Cloud Sect", "Azure Cloud Mountain Gate", 1)
	if got := fmt.Sprint(result["outcome"]); got != "fail" {
		t.Fatalf("outcome=%s", got)
	}
	if _, ok := result["granted_manual"]; ok {
		t.Fatal("a failed trial granted a manual")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42 AND item_id LIKE '%_manual'`)); got != 0 {
		t.Fatalf("inventory has %d manuals after a failed trial", got)
	}
}

// v0.34.0 playtest finding: the trial's retry wait is read off the last
// failed attempt, not the cooldowns table, so the GM's Reset Cooldowns left
// a failed disciple waiting a day regardless. A full reset ages it out.
func TestResetCooldownsClearsTheTrialRetryWait(t *testing.T) {
	path := setupSectTrialDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 5000)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_user_id INTEGER NOT NULL,action TEXT,target TEXT,before_json TEXT,after_json TEXT,reason TEXT,created_at REAL)`)
	batch4Exec(t, path, `INSERT INTO sect_recruitment_attempts(user_id,sect_name,attempt_type,result,game_minute,created_at) VALUES(42,'Azure Cloud Sect','trial','fail',4900,0)`)

	raw, _ := json.Marshal(map[string]any{"sect_name": "Azure Cloud Sect", "examiner": "Gate Elder Jian Mu", "location": "Azure Cloud Mountain Gate", "trial_name": "Entrance"})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "trial-retry-1", Operation: "sect.recruitment.trial", ActorID: 42, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "retry cooldown") {
		t.Fatalf("a trial an hour after a failure should wait, got %v", err)
	}

	reset, _ := json.Marshal(map[string]any{"user_id": 42, "reason": "playtest"})
	out, err := Apply(path, ActionRequest{Operation: "admin.player.reset_cooldowns", ActorID: 1, Payload: reset})
	if err != nil {
		t.Fatalf("reset: %v", err)
	}
	if result, _ := out.Result.(map[string]any); i64(result["trial_retries_cleared"]) != 1 {
		t.Fatalf("reset should report the aged retry: %v", out.Result)
	}
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "trial-retry-2", Operation: "sect.recruitment.trial", ActorID: 42, Payload: raw}); err != nil {
		t.Fatalf("after a reset the trial should be sat again: %v", err)
	}
}
