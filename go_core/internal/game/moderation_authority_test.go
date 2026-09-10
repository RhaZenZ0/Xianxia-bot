package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// setupModerationAuthorityDB builds on the shared batch5 authority fixture
// (real applyAuthoritative dispatch, not the narrow admin-only schema in
// admin_actions_test.go) and adds the tables recordTrueDeathAuthoritative
// needs, so this file can exercise the actual claim in moderation.go's
// doc comment: old-age death still fires for a frozen character.
func setupModerationAuthorityDB(t *testing.T) string {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	if err := conn.ExecScript(`
CREATE TABLE reincarnation_state(
    user_id INTEGER PRIMARY KEY, family_id INTEGER NOT NULL, death_game_minute INTEGER NOT NULL,
    ready_game_minute INTEGER NOT NULL, death_reason TEXT NOT NULL, previous_name TEXT NOT NULL,
    previous_generation INTEGER NOT NULL DEFAULT 1, karma_at_death INTEGER NOT NULL DEFAULT 0,
    family_target_minutes INTEGER NOT NULL DEFAULT 0, family_simulated_minutes INTEGER NOT NULL DEFAULT 0,
    afterlife_started_at REAL NOT NULL DEFAULT 0, reincarnation_ready_at REAL NOT NULL DEFAULT 0,
    rebirth_mode TEXT NOT NULL DEFAULT 'samsara', target_world TEXT NOT NULL DEFAULT 'Mortal World',
    samsara_lives_count INTEGER NOT NULL DEFAULT 0, samsara_history_json TEXT NOT NULL DEFAULT '[]',
    memory_retention INTEGER NOT NULL DEFAULT 0, talent_retention INTEGER NOT NULL DEFAULT 0,
    comprehension_retention INTEGER NOT NULL DEFAULT 0, insight_retention INTEGER NOT NULL DEFAULT 0,
    legacy_points INTEGER NOT NULL DEFAULT 0, special_trait TEXT NOT NULL DEFAULT '',
    karmic_fortune INTEGER NOT NULL DEFAULT 0, previous_realm_index INTEGER NOT NULL DEFAULT 0,
    previous_phase INTEGER NOT NULL DEFAULT 1, previous_body_realm_index INTEGER NOT NULL DEFAULT 0,
    previous_body_phase INTEGER NOT NULL DEFAULT 1, previous_spiritual_root TEXT, previous_path TEXT,
    previous_insight_xp INTEGER NOT NULL DEFAULT 0, law_snapshot_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
    partner_echo INTEGER NOT NULL DEFAULT 0, partner_name TEXT NOT NULL DEFAULT ''
);
ALTER TABLE character_birth_family ADD COLUMN generation INTEGER NOT NULL DEFAULT 1;
ALTER TABLE characters ADD COLUMN death_game_minute INTEGER;
ALTER TABLE characters ADD COLUMN reincarnation_ready_game_minute INTEGER;
ALTER TABLE characters ADD COLUMN true_death_count INTEGER NOT NULL DEFAULT 0;
`); err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, "INSERT INTO birth_families(archetype) VALUES('alchemy_family')")
	batch4Exec(t, path, "INSERT INTO character_birth_family(user_id,family_id,generation) VALUES(42,1,3)")
	return path
}

// setModerationFlags mutates the moderation columns setupModerationAuthorityDB's
// underlying characters row carries, exactly like admin.player.set_moderation
// would, but directly (these tests are about enforcement, not the admin op
// itself - that's covered separately in admin_actions_test.go).
func setModerationFlags(t *testing.T, path string, userID int64, muted, frozen bool, reason string) {
	t.Helper()
	batch4Exec(t, path, `UPDATE characters SET is_muted=?,is_frozen=?,moderation_reason=? WHERE user_id=?`,
		boolToInt(muted), boolToInt(frozen), reason, userID)
}

func boolToInt(v bool) int64 {
	if v {
		return 1
	}
	return 0
}

func validSceneActionPayload() map[string]any {
	// target "Self" short-circuits resolveSceneAction to an automatic success,
	// avoiding a dependency on canonicalAttribute/world catalog attribute math
	// that isn't the point of these tests.
	return map[string]any{"action_key": "observe", "target": "Self", "detail": "look around the room"}
}

func validCheckResolvePayload() map[string]any {
	return map[string]any{"attribute": "body", "tn": 10, "label": "moderation authority test"}
}

func applyModerationOp(t *testing.T, path, world, op string, payload map[string]any) (ActionResponse, error) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	return ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "moderation-authority-" + op,
		Operation:  op,
		ActorID:    42,
		Payload:    raw,
	})
}

func TestModerationFrozenPlayerBlockedFromAnyAuthoritativeAction(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	setModerationFlags(t, path, 42, false, true, "cooling off")

	if _, err := applyModerationOp(t, path, world, "check.resolve", validCheckResolvePayload()); err == nil {
		t.Fatal("expected frozen character to be blocked from check.resolve")
	} else if !strings.Contains(err.Error(), "frozen") || !strings.Contains(err.Error(), "cooling off") {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, err := applyModerationOp(t, path, world, "scene.action", validSceneActionPayload()); err == nil {
		t.Fatal("expected frozen character to be blocked from scene.action")
	} else if !strings.Contains(err.Error(), "frozen") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestModerationMutedPlayerBlockedFromSceneActionOnlyNotCheckResolve(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	setModerationFlags(t, path, 42, true, false, "spamming OOC")

	if _, err := applyModerationOp(t, path, world, "scene.action", validSceneActionPayload()); err == nil {
		t.Fatal("expected muted character to be blocked from scene.action")
	} else if !strings.Contains(err.Error(), "muted") || !strings.Contains(err.Error(), "spamming OOC") {
		t.Fatalf("unexpected error: %v", err)
	}

	// check.resolve must NOT be blocked by mute - mute is scoped to
	// scene.action only, per moderation.go's design.
	if _, err := applyModerationOp(t, path, world, "check.resolve", validCheckResolvePayload()); err != nil {
		t.Fatalf("check.resolve should not be blocked while only muted: %v", err)
	}
}

func TestModerationUnflaggedPlayerUnaffected(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	// Regression guard: both flags at their default 0 must not block anything.

	if _, err := applyModerationOp(t, path, world, "check.resolve", validCheckResolvePayload()); err != nil {
		t.Fatalf("check.resolve should succeed for an unmoderated character: %v", err)
	}
	if _, err := applyModerationOp(t, path, world, "scene.action", validSceneActionPayload()); err != nil {
		t.Fatalf("scene.action should succeed for an unmoderated character: %v", err)
	}
}

func TestModerationOldAgeDeathStillFiresForFrozenCharacter(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	setModerationFlags(t, path, 42, true, true, "under investigation")
	// Natural lifespan of 1 year, created at game_minute 0, evaluated far past
	// that threshold (minutesPerYear=518400) - old enough to have died of old
	// age many times over, regardless of the moderation flags.
	batch4Exec(t, path, `UPDATE characters SET natural_lifespan_years=1,life_extension_years=0,age_at_creation_years=18,created_game_minute=0,realm_index=0,phase=1,body_realm_index=0,body_phase=1 WHERE user_id=42`)
	batch4SetCanonicalGameMinute(t, path, 20000000)

	// The moderation flags must not prevent the engine's own preemptive old-age
	// death from firing - checkPlayerModerationTx only runs when
	// checkPlayerOldAgeDeathTx did NOT already preempt the call.
	if _, err := applyModerationOp(t, path, world, "check.resolve", validCheckResolvePayload()); err != nil {
		t.Fatalf("old-age death mutation should have succeeded despite frozen/muted flags: %v", err)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=42 AND life_status='deceased'")); got != 1 {
		t.Fatalf("character was not recorded as deceased from old age, life_status count=%d", got)
	}
}

// A lapsed mute is over the moment the clock passes it, whether or not the
// simulation tick has cleared the row yet (v0.32.0).
func TestModerationLapsedMuteNoLongerBlocks(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	batch4Exec(t, path, `UPDATE characters SET is_muted=1,muted_until=?,moderation_reason='lapsed' WHERE user_id=42`, nowSeconds()-30)

	if _, err := applyModerationOp(t, path, world, "scene.action", validSceneActionPayload()); err != nil {
		t.Fatalf("a lapsed mute must not block scene.action: %v", err)
	}
}

func TestModerationRunningFreezeStillBlocks(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	batch4Exec(t, path, `UPDATE characters SET is_frozen=1,frozen_until=?,moderation_reason='an hour' WHERE user_id=42`, nowSeconds()+3600)

	if _, err := applyModerationOp(t, path, world, "check.resolve", validCheckResolvePayload()); err == nil {
		t.Fatal("a freeze with time left must still block")
	} else if !strings.Contains(err.Error(), "frozen") {
		t.Fatalf("unexpected error: %v", err)
	}
}

// A ban blocks everything, mute-scoped ops included, and names itself.
func TestModerationBannedPlayerBlockedFromEverything(t *testing.T) {
	path := setupModerationAuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	batch4Exec(t, path, `UPDATE characters SET is_banned=1,moderation_reason='gone for good' WHERE user_id=42`)

	for _, op := range []string{"check.resolve", "scene.action"} {
		payload := validCheckResolvePayload()
		if op == "scene.action" {
			payload = validSceneActionPayload()
		}
		if _, err := applyModerationOp(t, path, world, op, payload); err == nil {
			t.Fatalf("expected banned character to be blocked from %s", op)
		} else if !strings.Contains(err.Error(), "banned") || !strings.Contains(err.Error(), "gone for good") {
			t.Fatalf("%s: unexpected error: %v", op, err)
		}
	}
}
