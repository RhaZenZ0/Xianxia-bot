package game

import (
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A sect keeps its seat in a city (v1.19.0). On the owner's call (Option A
// of the rival-capitals question) each public sect's gate is a district of
// one of its world's great cities, and the claims, wars and relations the
// sects already have are what make two cities rivals. These drive the shipped
// catalogue: the seat is read off the map, never a field of the sect.

func seatCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(filepath.Join("..", "..", "..", "content", "world.json"))
	if err != nil {
		t.Fatalf("the content file is in the repository; the read is broken, not the tree: %v", err)
	}
	return catalog
}

func TestEveryPublicSectKeepsASeatInACityOfItsWorld(t *testing.T) {
	catalog := seatCatalog(t)
	seats := map[string]string{}
	for name, def := range catalog.Sects {
		gate := sectGate(catalog, name)
		if def.Hidden || gate == "" {
			if seat := sectSeat(catalog, name); seat != "" {
				t.Errorf("%s keeps no public gate and still has a seat %q", name, seat)
			}
			continue
		}
		seat := sectSeat(catalog, name)
		if seat == "" {
			t.Errorf("%s's gate %q stands in no city: a sect with no seat has no ground to grow from", name, gate)
			continue
		}
		city := catalog.Locations[seat]
		if city.RealmHub {
			t.Errorf("%s sits in the capital %q; the capital is nobody's seat", name, seat)
		}
		if city.World != catalog.Locations[gate].World {
			t.Errorf("%s's gate is in %s and its seat %q in %s", name, catalog.Locations[gate].World, seat, city.World)
		}
		if catalog.Locations[gate].District != "sect_gate" {
			t.Errorf("%s's gate %q is a %q district, want sect_gate", name, gate, catalog.Locations[gate].District)
		}
		if other, taken := seats[seat]; taken {
			t.Errorf("%s and %s both sit in %q", name, other, seat)
		}
		seats[seat] = name
		if TerritoryIsWholePlace(catalog, gate) {
			t.Errorf("%s's gate %q is a whole place; a seated gate is a street of its city", name, gate)
		}
		if SectHome(catalog, name) != seat {
			t.Errorf("%s's home is %q, want its seat %q", name, SectHome(catalog, name), seat)
		}
	}
	if len(seats) < 12 {
		t.Fatalf("only %d sects are seated; the content read is broken, not the tree", len(seats))
	}
}

// A public delegation never speaks for a private-route sect (v1.1.0: a
// sponsor is that sect's one door), so those are left out here on purpose.
func TestADelegationInTheSeatCitySpeaksForItsSect(t *testing.T) {
	catalog := seatCatalog(t)
	checked := 0
	for name, def := range catalog.Sects {
		seat := sectSeat(catalog, name)
		if seat == "" || !def.Recruitment.Public() {
			continue
		}
		checked++
		if got := recruitingSectFor(catalog, "event-1", seat); got != name {
			t.Errorf("an event in %s's seat %q recruits for %q", name, seat, got)
		}
		for _, part := range cityPartsOf(catalog, seat) {
			if got := recruitingSectFor(catalog, "event-1", part); got != name {
				t.Errorf("an event in %q, a part of %s's seat, recruits for %q", part, name, got)
			}
		}
	}
	if checked < 10 {
		t.Fatalf("only %d public seated sects; the content read is broken, not the tree", checked)
	}
}

func TestASeatlessSectsHomeIsItsGate(t *testing.T) {
	catalog := crossingCatalog(t)
	catalog.Sects["Hermit Sect"] = worlddata.SectDefinition{Recruitment: worlddata.SectRecruitment{Location: "Lonely Crag"}}
	catalog.Locations["Lonely Crag"] = worlddata.LocationDefinition{World: "Mortal World"}
	if seat := sectSeat(catalog, "Hermit Sect"); seat != "" {
		t.Fatalf("a wilderness gate has a seat %q", seat)
	}
	if home := SectHome(catalog, "Hermit Sect"); home != "Lonely Crag" {
		t.Fatalf("a seatless sect's home is its gate, got %q", home)
	}
}

// A sponsor reveals a private gate, as a sponsor always has (v1.1.0): making
// the gate a district of a city must not put it in plain sight from the
// street, while a public sect's gate is seen from its city like any district.
func TestAPrivateGateIsNotInPlainSightFromItsSeat(t *testing.T) {
	catalog := seatCatalog(t)
	private, public := "", ""
	for name, def := range catalog.Sects {
		gate := sectGate(catalog, name)
		if gate == "" {
			continue
		}
		if catalog.Locations[gate].Private && private == "" {
			private = name
		}
		if !catalog.Locations[gate].Private && def.Recruitment.Public() && public == "" {
			public = name
		}
	}
	if private == "" || public == "" {
		t.Fatalf("the content carries no private gate or no public one: %q %q", private, public)
	}
	for _, tc := range []struct {
		sect string
		seen bool
	}{{public, true}, {private, false}} {
		path := setupSectTrialDB(t)
		batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS character_location_discoveries(user_id INTEGER NOT NULL, location TEXT NOT NULL, discovery_kind TEXT NOT NULL DEFAULT '', discovered_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id,location))`)
		seat := sectSeat(catalog, tc.sect)
		gate := sectGate(catalog, tc.sect)
		batch4Exec(t, path, `UPDATE characters SET location='`+seat+`' WHERE user_id=42`)
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		c, err := loadMechanicsCharacter(conn, catalog, 42)
		if err != nil {
			t.Fatal(err)
		}
		known, err := knownLocationsTx(conn, catalog, 42, c)
		conn.Close()
		if err != nil {
			t.Fatal(err)
		}
		if known[gate] != tc.seen {
			t.Errorf("standing in %q, %s's gate %q known=%v, want %v", seat, tc.sect, gate, known[gate], tc.seen)
		}
	}
}

// A gate known before v1.19.0 seated it was a road-less place a cultivator
// could jump to; seated, it is a district, entered only from inside its city.
// A world upgrading carries `character_location_discoveries` rows naming the
// gate and not the seat, so the rule that grandfathers them is that a known
// part of a city is a known city: the seat is on the travel list because the
// gate is. Its drill removes the expansion from knownLocationsTx and prints
// the seat as unknown.
func TestAGateKnownBeforeTheSeatsPutsTheSeatOnTheMap(t *testing.T) {
	catalog := seatCatalog(t)
	sect := ""
	for name, def := range catalog.Sects {
		if gate := sectGate(catalog, name); gate != "" && !catalog.Locations[gate].Private && def.Recruitment.Public() {
			if sect == "" || name < sect {
				sect = name
			}
		}
	}
	if sect == "" {
		t.Fatal("the content carries no public seated sect; the fixture is broken, not the rule")
	}
	seat, gate := sectSeat(catalog, sect), sectGate(catalog, sect)
	path := setupSectTrialDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS character_location_discoveries(user_id INTEGER NOT NULL, location TEXT NOT NULL, discovery_kind TEXT NOT NULL DEFAULT '', discovered_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id,location))`)
	// Standing somewhere with no roads to the seat, holding the pre-seat row.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,'`+gate+`','recruitment_route',0,0)`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	c, err := loadMechanicsCharacter(conn, catalog, 42)
	if err != nil {
		t.Fatal(err)
	}
	known, err := knownLocationsTx(conn, catalog, 42, c)
	if err != nil {
		t.Fatal(err)
	}
	if !known[gate] {
		t.Fatalf("the discovery row for %q was not read; the fixture is broken, not the rule", gate)
	}
	if !known[seat] {
		t.Fatalf("%s is known from before the seats and %s, the city it is a district of, is not: the gate is on the map and cannot be walked to", gate, seat)
	}
}
