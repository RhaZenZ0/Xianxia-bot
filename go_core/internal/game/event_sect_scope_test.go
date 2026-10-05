package game

import (
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// v1.27.0: a world event's `sect` effect ran `UPDATE sect_politics_state` with
// no WHERE, so an invasion in a Mortal village drained every sect in all four
// worlds. These drive the one helper both writers now share, on the shipped
// catalogue, because which sect a place belongs to is the content's to say.

func eventScopeCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load("../../../content/world.json")
	if err != nil {
		t.Fatalf("the content file is in the repository and must load: %v", err)
	}
	return catalog
}

func eventScopeDB(t *testing.T, catalog worlddata.Catalog) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(filepath.Join(t.TempDir(), "scope.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	if err = conn.ExecScript(`
CREATE TABLE sect_politics_state(sect_name TEXT PRIMARY KEY, influence INTEGER NOT NULL DEFAULT 50, cohesion INTEGER NOT NULL DEFAULT 50, resources INTEGER NOT NULL DEFAULT 50, recruitment_pressure INTEGER NOT NULL DEFAULT 50, doctrine_pressure INTEGER NOT NULL DEFAULT 50, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE sect_politics_events(id INTEGER PRIMARY KEY AUTOINCREMENT, sect_name TEXT, event_text TEXT, severity INTEGER, game_minute INTEGER, created_at REAL);
CREATE TABLE territory_state(territory_key TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '', controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50, defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
`); err != nil {
		t.Fatal(err)
	}
	for name := range catalog.Sects {
		if _, err = conn.Execute(`INSERT INTO sect_politics_state(sect_name) VALUES(?)`, []any{name}); err != nil {
			t.Fatal(err)
		}
	}
	return conn
}

func sectResources(t *testing.T, conn *storage.Conn) map[string]int64 {
	t.Helper()
	r, err := conn.Execute(`SELECT sect_name,resources FROM sect_politics_state`, nil)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]int64{}
	for _, row := range r.Rows {
		out[row[0].(string)] = storage.ParseInt(row[1])
	}
	return out
}

func sectWorld(catalog worlddata.Catalog, sect string) string {
	return catalog.Locations[sectGate(catalog, sect)].World
}

func TestAnEventReachesOnlyTheSectsOfItsWorld(t *testing.T) {
	catalog := eventScopeCatalog(t)
	conn := eventScopeDB(t, catalog)
	place := "Greenriver Town"
	if catalog.Locations[place].World != "Mortal World" {
		t.Fatalf("%s is expected in the Mortal World", place)
	}
	for name := range catalog.Sects {
		if SectHome(catalog, name) == place {
			t.Fatalf("%s is the home of %s; the test wants a place no sect keeps", place, name)
		}
	}
	touched, err := ApplyEventSectEffectTx(conn, catalog, place, map[string]any{"resources": -8}, "Demons crossed the river.", 7, 100, 1)
	if err != nil || !touched {
		t.Fatalf("touched=%v err=%v", touched, err)
	}
	moved, kept := 0, 0
	for sect, res := range sectResources(t, conn) {
		def := catalog.Sects[sect]
		inWorld := !def.Hidden && sectWorld(catalog, sect) == "Mortal World"
		switch {
		case inWorld && res != 42:
			t.Errorf("%s is a Mortal sect and kept %d resources, want 42", sect, res)
		case !inWorld && res != 50:
			t.Errorf("an event in %s moved %s (%s) to %d; it is not that world's sect", place, sect, sectWorld(catalog, sect), res)
		case inWorld:
			moved++
		default:
			kept++
		}
	}
	if moved == 0 || kept == 0 {
		t.Fatalf("moved=%d kept=%d - the shipped sects should fall on both sides", moved, kept)
	}
}

func TestAnEventInASeatCityIsThatSectsAlone(t *testing.T) {
	catalog := eventScopeCatalog(t)
	conn := eventScopeDB(t, catalog)
	seat := SectSeat(catalog, "Azure Cloud Sect")
	if seat == "" {
		t.Fatal("the Azure Cloud Sect keeps no seat in the shipped content")
	}
	if got := EventSectTargetsTx(conn, catalog, seat); len(got) != 1 || got[0] != "Azure Cloud Sect" {
		t.Fatalf("an event in %s reached %v, want only the Azure Cloud Sect", seat, got)
	}
}

func TestAnEventOnHeldGroundIsTheHoldersAlone(t *testing.T) {
	catalog := eventScopeCatalog(t)
	conn := eventScopeDB(t, catalog)
	if _, err := conn.Execute(`INSERT INTO territory_state(territory_key,controller_type,controller_key) VALUES('Greenriver Town','sect','Black Serpent Clan')`, nil); err != nil {
		t.Fatal(err)
	}
	if got := EventSectTargetsTx(conn, catalog, "Greenriver Town"); len(got) != 1 || got[0] != "Black Serpent Clan" {
		t.Fatalf("an event on ground the Black Serpent Clan holds reached %v", got)
	}
}

func TestAnEventSomewhereNoWorldCarriesReachesNoSect(t *testing.T) {
	catalog := eventScopeCatalog(t)
	conn := eventScopeDB(t, catalog)
	if got := EventSectTargetsTx(conn, catalog, "birth_family:3"); len(got) != 0 {
		t.Fatalf("an event in a household reached %v", got)
	}
}
