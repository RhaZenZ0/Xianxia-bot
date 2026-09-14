package game

// v0.23.0 regression tests for sect.discover. The character.set_gender tests
// went with the operation in v1.0.0-rc.15: sex is chosen at creation, where
// /begin requires it, so there is no second setter left to regress.

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

func setupIdentityDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE character_sect_discoveries(
	user_id INTEGER NOT NULL, sect_name TEXT NOT NULL, discovery_kind TEXT NOT NULL DEFAULT 'rumor',
	source_key TEXT NOT NULL DEFAULT '', discovered_game_minute INTEGER NOT NULL DEFAULT 0,
	created_at REAL NOT NULL, PRIMARY KEY(user_id,sect_name)
);
`); err != nil {
		t.Fatal(err)
	}
	batch4SetCanonicalGameMinute(t, path, 4000)
	return path
}

func identityScalar(t *testing.T, path, sql string, args ...any) int64 {
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

// ---------------------------------------------------------- sect.discover

func discoverApply(t *testing.T, path string, actor int64, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := Apply(path, ActionRequest{Operation: "sect.discover", ActorID: actor, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func discoveredNames(t *testing.T, result map[string]any, key string) []string {
	t.Helper()
	raw, ok := result[key].([]string)
	if !ok {
		t.Fatalf("%s is %T, want []string", key, result[key])
	}
	return raw
}

func TestDiscoveringSectsReportsWhichWereActuallyNew(t *testing.T) {
	// The whole reason for a batch call: the caller needs to announce only the
	// sects the player did not already know. Per-sect writes could not say.
	path := setupIdentityDB(t)
	result, err := discoverApply(t, path, 42, map[string]any{
		"sects":          []any{"Azure Cloud Sect", "Iron Peak Sect"},
		"discovery_kind": "exploration",
		"source_key":     "Greenriver Town",
		"game_minute":    4000,
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := discoveredNames(t, result, "discovered"); len(got) != 2 {
		t.Fatalf("discovered=%v, want both", got)
	}

	result, err = discoverApply(t, path, 42, map[string]any{
		"sects":       []any{"Azure Cloud Sect", "Jade Fern Sect"},
		"game_minute": 4100,
	})
	if err != nil {
		t.Fatal(err)
	}
	discovered := discoveredNames(t, result, "discovered")
	already := discoveredNames(t, result, "already_known")
	if len(discovered) != 1 || discovered[0] != "Jade Fern Sect" {
		t.Fatalf("discovered=%v, want only the new one", discovered)
	}
	if len(already) != 1 || already[0] != "Azure Cloud Sect" {
		t.Fatalf("already_known=%v", already)
	}
	// Re-learning must not rewrite when or how it was first learned.
	if got := identityScalar(t, path,
		`SELECT discovered_game_minute FROM character_sect_discoveries WHERE user_id=42 AND sect_name='Azure Cloud Sect'`); got != 4000 {
		t.Fatalf("discovered_game_minute=%d; the original discovery was overwritten", got)
	}
}

func TestDiscoveringTheSameSectTwiceInOneCallCountsOnce(t *testing.T) {
	path := setupIdentityDB(t)
	result, err := discoverApply(t, path, 42, map[string]any{
		"sects":       []any{"Azure Cloud Sect", "Azure Cloud Sect", "  ", "Azure Cloud Sect"},
		"game_minute": 4000,
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := discoveredNames(t, result, "discovered"); len(got) != 1 {
		t.Fatalf("discovered=%v, want one", got)
	}
	// And the repeats must not come back as "you already knew this": the
	// caller named it once as far as it is concerned, and a list padded with
	// its own duplicates is what a caller would print at the player.
	if got := discoveredNames(t, result, "already_known"); len(got) != 0 {
		t.Fatalf("already_known=%v; the duplicates were echoed back", got)
	}
	if got := identityScalar(t, path,
		`SELECT COUNT(*) FROM character_sect_discoveries WHERE user_id=42`); got != 1 {
		t.Fatalf("rows=%d, want 1", got)
	}
}

func TestDiscoveringNothingIsNotAnError(t *testing.T) {
	// The sect screen reconciles on every open and usually finds nothing new.
	path := setupIdentityDB(t)
	result, err := discoverApply(t, path, 42, map[string]any{"sects": []any{}, "game_minute": 4000})
	if err != nil {
		t.Fatalf("an empty reconcile failed: %v", err)
	}
	if got := discoveredNames(t, result, "discovered"); len(got) != 0 {
		t.Fatalf("discovered=%v, want none", got)
	}
}

func TestDiscoveringForAMissingCharacterIsRefused(t *testing.T) {
	path := setupIdentityDB(t)
	if _, err := discoverApply(t, path, 999, map[string]any{
		"sects": []any{"Azure Cloud Sect"}, "game_minute": 4000,
	}); err == nil {
		t.Fatal("a sect was discovered for a user with no character")
	}
	if got := identityScalar(t, path, `SELECT COUNT(*) FROM character_sect_discoveries`); got != 0 {
		t.Fatalf("rows=%d after a refusal", got)
	}
}

func TestEachSectRecordsTheLocationThatRevealedIt(t *testing.T) {
	// The sect screen reconciles several locations in one call. Before the
	// batch this was a write per sect, each carrying its own location; a batch
	// that flattened them to one source_key would credit every sect to
	// whichever location was passed at the top level.
	path := setupIdentityDB(t)
	if _, err := discoverApply(t, path, 42, map[string]any{
		"sects":          []any{"Azure Cloud Sect", "Iron Peak Sect"},
		"discovery_kind": "recruitment_route",
		"source_key":     "fallback",
		"source_keys": map[string]any{
			"Azure Cloud Sect": "Greenriver Town",
			"Iron Peak Sect":   "Blackstone Pass",
		},
		"game_minute": 4000,
	}); err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for sect, want := range map[string]string{
		"Azure Cloud Sect": "Greenriver Town",
		"Iron Peak Sect":   "Blackstone Pass",
	} {
		res, err := conn.Execute(
			`SELECT source_key FROM character_sect_discoveries WHERE user_id=42 AND sect_name=?`,
			[]any{sect})
		if err != nil {
			t.Fatal(err)
		}
		if got := fmt.Sprint(firstRowMap(res)["source_key"]); got != want {
			t.Fatalf("%s source_key=%q, want %q", sect, got, want)
		}
	}
}

func TestASectWithNoSpecificSourceFallsBackToTheSharedOne(t *testing.T) {
	path := setupIdentityDB(t)
	if _, err := discoverApply(t, path, 42, map[string]any{
		"sects":       []any{"Jade Fern Sect"},
		"source_key":  "Greenriver Town",
		"game_minute": 4000,
	}); err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(
		`SELECT source_key FROM character_sect_discoveries WHERE user_id=42`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := fmt.Sprint(firstRowMap(res)["source_key"]); got != "Greenriver Town" {
		t.Fatalf("source_key=%q", got)
	}
}
