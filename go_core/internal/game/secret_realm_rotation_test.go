package game

import (
	"encoding/json"
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

// v1.0.0: the rotation is a query of its own, so the GM dashboard can ask the
// engine what it already knows instead of keeping a second copy of the rule.
// The dashboard used to read content/world.json on every request and work out
// the interval, the catalogue order and "which is next" in Python - which had
// already drifted: on a world that has never rotated, this answers "the next
// tick" and that copy answered "the first interval after minute zero".
func TestTheRotationIsAQueryTheDashboardCanAsk(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	ids := secretRealmIDs(catalog)

	// Round-tripped through JSON, because that is what the dashboard receives:
	// an in-process assertion would pass on Go types the HTTP boundary never
	// delivers, and the shape of `order` is exactly what this is pinning.
	ask := func() map[string]any {
		out, err := ApplyWithWorld(path, world, ActionRequest{
			APIVersion: authoritativeAPIVersion, ActionID: "rotation-query",
			Operation: "secret_realm.rotation", ActorID: 0, Payload: json.RawMessage(`{}`),
		})
		if err != nil {
			t.Fatal(err)
		}
		encoded, err := json.Marshal(batch4Result(t, out))
		if err != nil {
			t.Fatal(err)
		}
		var overTheWire map[string]any
		if err := json.Unmarshal(encoded, &overTheWire); err != nil {
			t.Fatal(err)
		}
		return overTheWire
	}

	// A world that has never rotated: the next opening is the next tick, not
	// an interval counted from minute zero. This is the divergence that made
	// the Python copy worth deleting rather than keeping in step.
	batch4SetCanonicalGameMinute(t, path, 7000)
	fresh := ask()
	if storage.ParseInt(fresh["last_game_minute"]) != 0 {
		t.Fatalf("nothing has rotated yet: %v", fresh["last_game_minute"])
	}
	if got := storage.ParseInt(fresh["next_game_minute"]); got != 7000 {
		t.Fatalf("a world that has never rotated opens on the next tick, not at %d", got)
	}

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := RotateSecretRealms(conn, catalog, 5000); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()

	view := ask()
	if storage.ParseInt(view["realms"]) != int64(len(ids)) {
		t.Fatalf("realms=%v, catalogue has %d", view["realms"], len(ids))
	}
	if storage.ParseInt(view["interval_minutes"]) != secretRealmRotationMinutes {
		t.Fatalf("interval_minutes=%v", view["interval_minutes"])
	}
	if fmt.Sprint(view["last_realm_id"]) != ids[0] || fmt.Sprint(view["next_realm_id"]) != ids[1%len(ids)] {
		t.Fatalf("last/next: %v / %v", view["last_realm_id"], view["next_realm_id"])
	}
	if storage.ParseInt(view["next_game_minute"]) != 5000+secretRealmRotationMinutes {
		t.Fatalf("next_game_minute=%v", view["next_game_minute"])
	}
	// The display fields the dashboard renders: it must not have to open the
	// content pack to turn an id into a name and a place.
	last, next := catalog.SecretRealms[ids[0]], catalog.SecretRealms[ids[1%len(ids)]]
	if fmt.Sprint(view["last_realm_name"]) != last.Name || fmt.Sprint(view["last_location"]) != last.Location {
		t.Fatalf("last realm name/location: %v / %v", view["last_realm_name"], view["last_location"])
	}
	if fmt.Sprint(view["next_realm_name"]) != next.Name || fmt.Sprint(view["next_location"]) != next.Location {
		t.Fatalf("next realm name/location: %v / %v", view["next_realm_name"], view["next_location"])
	}
	order, _ := view["order"].([]any)
	if len(order) != len(ids) {
		t.Fatalf("order carries %d of %d realms", len(order), len(ids))
	}
	for i, row := range order {
		entry, _ := row.(map[string]any)
		if entry == nil || fmt.Sprint(entry["realm_id"]) != ids[i] {
			t.Fatalf("order[%d]=%v, want %s", i, row, ids[i])
		}
		if fmt.Sprint(entry["name"]) != catalog.SecretRealms[ids[i]].Name {
			t.Fatalf("order[%d] name=%v", i, entry["name"])
		}
	}
}
