package game

import (
	"encoding/json"
	"fmt"
	"strings"
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
	var last *OpenedSecretRealm
	open := func(gm int64) int64 {
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		opened, err := RotateSecretRealms(conn, catalog, gm)
		if err != nil {
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		last = opened
		if opened == nil {
			return 0
		}
		return 1
	}
	if got := open(1000); got != 1 {
		t.Fatalf("first tick should open a realm, opened %d", got)
	}
	// What it opened has to reach the caller, not just SQLite: the bot spawns
	// the scene thread from these fields, and a rotation that answered with a
	// count opened an entrance nobody could be told about.
	if last == nil || last.RealmID != ids[0] {
		t.Fatalf("the tick should hand back the realm it opened, got %+v", last)
	}
	if last.EventKey == "" || last.Name == "" || last.Location == "" || last.EndsAt <= 0 || last.OpenHours <= 0 {
		t.Fatalf("an opened realm must carry everything a scene thread needs: %+v", last)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT event_key FROM world_events WHERE dedupe_key=? AND active=1`, "secret_realm:"+ids[0])); got != last.EventKey {
		t.Fatalf("event key %q does not name the row it opened (%q)", last.EventKey, got)
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

// --- from rc2_views_test.go ---

// v1.0.0-rc.2: the realm rotation rides on secret_realm.status, and the
// dashboard can void an open trade offer with an audit row.

func TestSecretRealmStatusCarriesTheRotation(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
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
	batch4SetCanonicalGameMinute(t, path, 5100)
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "rc2-status", Operation: "secret_realm.status", ActorID: 42, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	result := batch4Result(t, out)
	rotation, _ := result["rotation"].(map[string]any)
	ids := secretRealmIDs(catalog)
	if rotation == nil || fmt.Sprint(rotation["last_realm_id"]) != ids[0] || fmt.Sprint(rotation["next_realm_id"]) != ids[1%len(ids)] {
		t.Fatalf("rotation on status: %v", result["rotation"])
	}
	if storage.ParseInt(rotation["next_game_minute"]) != 5000+secretRealmRotationMinutes {
		t.Fatalf("next_game_minute=%v", rotation["next_game_minute"])
	}
}

func TestTheDashboardVoidsAnOpenTradeOfferWithAnAuditRow(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE IF NOT EXISTS trade_offers(offer_id INTEGER PRIMARY KEY AUTOINCREMENT, from_user_id INTEGER NOT NULL, to_user_id INTEGER NOT NULL, location TEXT NOT NULL, give_json TEXT NOT NULL DEFAULT '{}', give_stones INTEGER NOT NULL DEFAULT 0, want_json TEXT NOT NULL DEFAULT '{}', want_stones INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', created_game_minute INTEGER NOT NULL DEFAULT 0, expires_game_minute INTEGER NOT NULL DEFAULT 0, resolved_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
INSERT INTO trade_offers(from_user_id,to_user_id,location,give_json,status,created_at,updated_at) VALUES(42,43,'Greenriver Inn','{"spirit_herb":1}','open',0,0);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	result, _ := applyAdmin(t, path, "admin.trade.void", map[string]any{"offer_id": 1, "reason": "test"}).(map[string]any)
	if result == nil || fmt.Sprint(result["status"]) != "voided" || storage.ParseInt(result["from_user_id"]) != 42 {
		t.Fatalf("void: %v", result)
	}
	if got := fmt.Sprint(scalar(t, path, `SELECT status FROM trade_offers WHERE offer_id=1`)); got != "voided" {
		t.Fatalf("status=%q", got)
	}
	if got := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.trade.void' AND target='1'`)); got != 1 {
		t.Fatalf("audit rows=%d", got)
	}
	// Voiding twice is refused: the offer is no longer open.
	raw, _ := json.Marshal(map[string]any{"offer_id": 1, "reason": "again"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.trade.void", ActorID: 0, Payload: raw}); err == nil || !strings.Contains(err.Error(), "already voided") {
		t.Fatalf("a second void should be refused, got %v", err)
	}
}

// --- from spatial_key_test.go ---

// The spatial keys (v1.0.0-rc.15). Three items carried `spatial_key` blocks
// that `spatial_key.use` reads, and every one of them named a secret realm
// that does not exist - `sword_grave` against a catalogue holding
// `sword_grave_nine_echoes` - so the action could only ever answer "the
// token's coordinates no longer correspond to a known realm". Nothing sold
// them either, so nobody ever found out.

func TestEverySpatialKeyOpensARealmThatExists(t *testing.T) {
	catalog := districtCatalog(t)
	keys := 0
	for id, item := range catalog.Items {
		if len(item.SpatialKey) == 0 {
			continue
		}
		keys++
		rid, _ := item.SpatialKey["secret_realm_id"].(string)
		if _, ok := catalog.SecretRealms[rid]; !ok {
			t.Fatalf("%s opens %q, which is not a secret realm", id, rid)
		}
	}
	if keys < 3 {
		t.Fatalf("the world ships %d spatial keys", keys)
	}
}

func TestAKeyIsRefusedAwayFromItsEntrance(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	realm := catalog.SecretRealms[catalog.Items["verdant_grotto_key"].SpatialKey["secret_realm_id"].(string)]
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'verdant_grotto_key',1)`)

	// Standing in Greenriver Town, not at the grotto's mouth.
	_, err := batch4ApplyErr(path, world, "spatial_key.use", 42, 1, map[string]any{"item_id": "verdant_grotto_key"})
	if err == nil || !strings.Contains(err.Error(), realm.Location) {
		t.Fatalf("a key spent away from its entrance opens nothing and must say so, got %v", err)
	}
	if got := i64(actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='verdant_grotto_key'`)); got != 1 {
		t.Fatalf("the refused key was consumed anyway: %d", got)
	}

	// At the entrance it opens.
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, realm.Location)
	out := batch4Result(t, batch4Apply(t, path, world, "spatial_key.use", 2, map[string]any{"item_id": "verdant_grotto_key"}))
	if out["name"] != realm.Name || out["consumed"] != true {
		t.Fatalf("the key did not open its realm: %v", out)
	}
	if got := i64(actionScalar(t, path, `SELECT COUNT(*) FROM inventory WHERE user_id=42 AND item_id='verdant_grotto_key' AND quantity>0`)); got != 0 {
		t.Fatalf("a spent key is still in the bag: %d", got)
	}
}
