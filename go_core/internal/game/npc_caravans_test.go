package game

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The world's own caravans (v1.0.0-rc.18). `caravans.owner_type` defaulted to
// 'npc' and nothing ever wrote one, so the resolver's non-player branch was
// unreachable and the roads carried only what players put on them.

// setupEscrowDB already ships `caravans`; these are the two tables beside it
// that a dispatch also writes.
const npcCaravanTestTables = `
CREATE TABLE caravan_operations(
	caravan_id INTEGER PRIMARY KEY, escort_strength INTEGER NOT NULL DEFAULT 0, concealment INTEGER NOT NULL DEFAULT 0,
	smuggling INTEGER NOT NULL DEFAULT 0, tax_rate INTEGER NOT NULL DEFAULT 8, toll_paid INTEGER NOT NULL DEFAULT 0,
	intercepted INTEGER NOT NULL DEFAULT 0, seized INTEGER NOT NULL DEFAULT 0, payout_final INTEGER NOT NULL DEFAULT 0,
	losses_json TEXT NOT NULL DEFAULT '{}', outcome TEXT NOT NULL DEFAULT 'traveling', resolved_game_minute INTEGER,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE caravan_events(
	event_id INTEGER PRIMARY KEY AUTOINCREMENT, caravan_id INTEGER NOT NULL, event_type TEXT NOT NULL,
	detail_json TEXT NOT NULL DEFAULT '{}', game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0
);
`

// setupNPCCaravanDB is the merchant fixture with the caravan tables beside it,
// and every merchant already seeded at home - which is the state a merchant is
// actually in on any tick after the first.
func setupNPCCaravanDB(t *testing.T, catalog worlddata.Catalog, gm int64) string {
	t.Helper()
	path := setupMerchantDB(t)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if err := conn.ExecScript(npcCaravanTestTables); err != nil {
			t.Fatal(err)
		}
		if _, err := AdvanceMerchants(conn, catalog, gm); err != nil {
			t.Fatal(err)
		}
	})
	return path
}

func TestTheWorldSendsCaravansOfItsOwn(t *testing.T) {
	catalog := merchantCatalog(t)
	path := setupNPCCaravanDB(t, catalog, 1000)
	sent := int64(0)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		var err error
		sent, err = DispatchNPCCaravans(conn, catalog, 1000)
		if err != nil {
			t.Fatal(err)
		}
	})
	if sent == 0 {
		t.Fatal("the world sent no caravans; the roads are empty again")
	}
	if sent > npcCaravanCapPerTick {
		t.Fatalf("sent=%d, over the per-tick cap of %d", sent, npcCaravanCapPerTick)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM caravans WHERE owner_type='npc' AND status='traveling'")); got != sent {
		t.Fatalf("npc caravans on the road=%d, dispatched=%d", got, sent)
	}
	// Every caravan is three rows, the way a player's is, or the resolver and
	// the GM's caravan list see half of one.
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM caravan_operations")); got != sent {
		t.Fatalf("operations rows=%d, want %d", got, sent)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM caravan_events WHERE event_type='departed'")); got != sent {
		t.Fatalf("departure events=%d, want %d", got, sent)
	}

	row := caravanRow(t, path, "SELECT owner_key,origin,destination,cargo_json,risk,depart_game_minute,arrive_game_minute FROM caravans WHERE owner_type='npc' ORDER BY caravan_id LIMIT 1")
	key := row[0].(string)
	merchant, ok := catalog.Merchants[key]
	if !ok {
		t.Fatalf("owner_key %q is not a merchant in the catalogue", key)
	}
	origin, destination := row[1].(string), row[2].(string)
	if !merchantRouteHas(merchant, origin) || !merchantRouteHas(merchant, destination) {
		t.Fatalf("%s sent %s -> %s, which is not on its own loop %v", key, origin, destination, merchant.Route)
	}
	if origin == destination {
		t.Fatalf("%s sent a caravan to where it already is (%s)", key, origin)
	}
	if arrive, depart := storage.ParseInt(row[6]), storage.ParseInt(row[5]); arrive <= depart {
		t.Fatalf("depart=%d arrive=%d: a caravan must take time on the road", depart, arrive)
	}
	if risk := storage.ParseInt(row[4]); risk < 5 || risk > 85 {
		t.Fatalf("risk=%d, outside the 5..85 the road planner allows", risk)
	}

	cargo := map[string]any{}
	if err := json.Unmarshal([]byte(row[3].(string)), &cargo); err != nil {
		t.Fatal(err)
	}
	// The resolver reads `_payout` and `_currency` off the cargo; without
	// them an arrival pays nothing, which is the empty-room fault again.
	if storage.ParseInt(cargo["_payout"]) <= 0 {
		t.Fatalf("cargo carries no payout: %v", cargo)
	}
	if cargo["_currency"] != merchant.Currency {
		t.Fatalf("cargo currency=%v, merchant trades in %s", cargo["_currency"], merchant.Currency)
	}
	carried := false
	for k := range cargo {
		if k == "" || k[0] == '_' {
			continue
		}
		if _, isWare := merchantWare(merchant, k); !isWare {
			t.Fatalf("%s is carrying %s, which is not one of its wares", key, k)
		}
		carried = true
	}
	if !carried {
		t.Fatalf("cargo has no goods in it: %v", cargo)
	}

	// An all-zero operations row is what the resolver reads as a caravan from
	// before the system had one, and it prices the risk at nothing.
	if escort := storage.ParseInt(actionScalar(t, path, "SELECT escort_strength FROM caravan_operations ORDER BY caravan_id LIMIT 1")); escort <= 0 {
		t.Fatalf("escort_strength=%d: the operations row reads as legacy", escort)
	}
}

func TestAMerchantWithALoadOnTheRoadDoesNotSendAnother(t *testing.T) {
	catalog := merchantCatalog(t)
	path := setupNPCCaravanDB(t, catalog, 1000)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := DispatchNPCCaravans(conn, catalog, 1000); err != nil {
			t.Fatal(err)
		}
	})
	sentFirst := caravanSenders(t, path)
	if len(sentFirst) == 0 {
		t.Fatal("nobody sent anything on the first tick")
	}
	// The next tick may well send more caravans - the per-tick cap leaves
	// merchants waiting their turn - but never a second one from a merchant
	// whose load is still on the road.
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := DispatchNPCCaravans(conn, catalog, 1001); err != nil {
			t.Fatal(err)
		}
	})
	for key, n := range caravanSenders(t, path) {
		if sentFirst[key] > 0 && n != sentFirst[key] {
			t.Fatalf("%s sent %d caravans, having had %d already on the road", key, n, sentFirst[key])
		}
	}
	// And not even once they have all arrived, until the interval is up:
	// otherwise a fast-forwarded world stacks a year of trade into a minute.
	before := caravanSenders(t, path)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := conn.Execute(`UPDATE caravans SET status='arrived'`, nil); err != nil {
			t.Fatal(err)
		}
		if _, err := DispatchNPCCaravans(conn, catalog, 1000+npcCaravanIntervalMinutes/2); err != nil {
			t.Fatal(err)
		}
	})
	for key, n := range caravanSenders(t, path) {
		if before[key] > 0 && n != before[key] {
			t.Fatalf("%s sent again inside the dispatch interval", key)
		}
	}
	withMerchantConn(t, path, func(conn *storage.Conn) {
		again, err := DispatchNPCCaravans(conn, catalog, 1000+npcCaravanIntervalMinutes+1)
		if err != nil {
			t.Fatal(err)
		}
		if again == 0 {
			t.Fatal("the interval passed and nobody sent anything")
		}
	})
}

// caravanSenders is how many loads each merchant has sent so far.
func caravanSenders(t *testing.T, path string) map[string]int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT owner_key,COUNT(*) FROM caravans WHERE owner_type='npc' GROUP BY owner_key`, nil)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]int64{}
	for _, row := range res.Rows {
		out[fmt.Sprint(row[0])] = storage.ParseInt(row[1])
	}
	return out
}

func TestNPCCaravansAreNotVisibleAsAnyPlayersOwn(t *testing.T) {
	catalog := merchantCatalog(t)
	path := setupNPCCaravanDB(t, catalog, 1000)
	withMerchantConn(t, path, func(conn *storage.Conn) {
		if _, err := DispatchNPCCaravans(conn, catalog, 1000); err != nil {
			t.Fatal(err)
		}
	})
	// Every player-facing read of this table filters on owner_type='player',
	// and an owner_key that parsed as a user id would be the one way a
	// merchant's load could land in somebody's /caravans.
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM caravans WHERE owner_type='player'")); got != 0 {
		t.Fatalf("%d npc caravans are labelled as a player's", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM caravans WHERE owner_key GLOB '[0-9]*'")); got != 0 {
		t.Fatalf("%d npc caravans carry a numeric owner_key, which reads as a user id", got)
	}
}

func TestNPCCaravanSenderNameIsThePersonNotTheKey(t *testing.T) {
	catalog := merchantCatalog(t)
	if got := NPCCaravanSenderName(catalog, "old_hu_the_peddler"); got != catalog.Merchants["old_hu_the_peddler"].Name {
		t.Fatalf("sender name=%q, want %q", got, catalog.Merchants["old_hu_the_peddler"].Name)
	}
	// An owner_key from some other kind of sender is passed through rather
	// than resolved to nothing, so a caller always has something to print.
	if got := NPCCaravanSenderName(catalog, "nobody_at_all"); got != "nobody_at_all" {
		t.Fatalf("unknown sender=%q, want it passed through", got)
	}
}

func caravanRow(t *testing.T, path, sql string) []any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		t.Fatalf("no rows: %s", sql)
	}
	return res.Rows[0]
}
