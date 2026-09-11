package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// Secret realms on rotation (v0.39.0): the tick opens the next realm in
// turn, one every three game days, at its own entrance.

func TestTheRotationOpensTheRealmsInTurnOneEveryThreeDays(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	catalog := districtCatalog(t)
	ids := secretRealmIDs(catalog)
	if len(ids) < 2 {
		t.Fatalf("need at least two realms, have %v", ids)
	}
	open := func(gm int64) int64 {
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		n, err := RotateSecretRealms(conn, catalog, gm)
		if err != nil {
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		return n
	}
	if got := open(1000); got != 1 {
		t.Fatalf("first tick should open a realm, opened %d", got)
	}
	first := catalog.SecretRealms[ids[0]]
	if got := fmt.Sprint(actionScalar(t, path, `SELECT location FROM world_events WHERE dedupe_key=? AND active=1`, "secret_realm:"+ids[0])); got != first.Location {
		t.Fatalf("%s should be open at %s, got %q", ids[0], first.Location, got)
	}
	if got := open(1000 + secretRealmRotationMinutes - 1); got != 0 {
		t.Fatalf("before the interval nothing opens, opened %d", got)
	}
	if got := open(1000 + secretRealmRotationMinutes); got != 1 {
		t.Fatalf("at the interval the next opens, opened %d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_events WHERE dedupe_key=? AND active=1`, "secret_realm:"+ids[1])); got != 1 {
		t.Fatalf("%s should be open now", ids[1])
	}
	view, err := func() (map[string]any, error) {
		conn, err := storage.Open(path)
		if err != nil {
			return nil, err
		}
		defer conn.Close()
		return SecretRealmRotationView(conn, catalog, 5000)
	}()
	if err != nil || fmt.Sprint(view["last_realm_id"]) != ids[1] || fmt.Sprint(view["next_realm_id"]) != ids[2%len(ids)] {
		t.Fatalf("rotation view: %v %v", view, err)
	}
	// Every realm opens in turn; when the turn comes round to one that is
	// still open it is skipped, not extended, and the rotation moves on.
	for n := 2; n < len(ids); n++ {
		if got := open(1000 + int64(n)*secretRealmRotationMinutes); got != 1 {
			t.Fatalf("turn %d should open %s, opened %d", n, ids[n], got)
		}
	}
	if got := open(1000 + int64(len(ids))*secretRealmRotationMinutes); got != 0 {
		t.Fatalf("an open realm skips its turn, opened %d", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT value_json FROM world_state WHERE key=?`, secretRealmRotationKey)); got == "" {
		t.Fatal("the rotation state should be stored")
	}
}
