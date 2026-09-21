package game

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// "Violence cannot mechanically begin here" was a promise only the bot kept
// (v1.0.6).
//
// `app/ai/narrator_context.py` tells the model that sentence, in those words,
// in two places. `pvp_invariants.go` held it for a duel. `battle.py` held it
// for `/battle challenge` - in the bot, and `combat_actions.go` named
// `SafeZone` zero times, so the engine would have started that fight for any
// caller that asked. And `advanceHunters` captured a fugitive with no
// reference to `characters.location` at all, inside a hall whose own
// description forbids violence on its floor.
//
// The two halves of the rule are drilled apart on purpose: a safe zone stops
// what a player starts, and a sanctuary also stops what the world does to
// them. See `violence_suppression.go` for why they are not one predicate.

// violenceCatalog is three places and one house: an ordinary rough location, an
// ordinary settled one, and the protected floor of an auction house reached
// from the settled one. It mirrors the shipped shape - the interior carries
// both `safe_zone` and an `auction_house` pointer, exactly as all 48 do - so
// the fixture cannot pass on a combination production does not have.
func violenceCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		Locations: map[string]worlddata.LocationDefinition{
			"Hunting Ground":   {World: "Mortal World"},
			"Greenriver Town":  {World: "Mortal World", SafeZone: true},
			"Golden Pavilion":  {World: "Mortal World", SafeZone: true, AuctionHouse: "golden_pavilion", OutsideLocation: "Greenriver Town"},
			"Unguarded Stalls": {World: "Mortal World", SafeZone: true, AuctionHouse: "open_yard", OutsideLocation: "Greenriver Town"},
		},
		AuctionHouses: map[string]worlddata.AuctionHouse{
			"golden_pavilion": {
				Name: "Golden Pavilion Auction House", Location: "Golden Pavilion",
				EntranceLocation: "Greenriver Town", ProtectedInterior: true,
			},
			// The counterfactual the shipped content does not have: a floor
			// that claims no protection. Without it, every assertion below
			// would also pass for a rule that answered "sanctuary" to any
			// auction interior at all, and the field would be decoration.
			"open_yard": {
				Name: "Open Yard", Location: "Unguarded Stalls",
				EntranceLocation: "Greenriver Town", ProtectedInterior: false,
			},
		},
	}
}

func setupViolenceDB(t *testing.T, location string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "violence.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY, name TEXT, gender TEXT, path TEXT, spiritual_root TEXT,
	location TEXT, attributes_json TEXT, realm_index INTEGER, phase INTEGER,
	body_realm_index INTEGER, body_phase INTEGER, cultivation INTEGER, body_cultivation INTEGER,
	life_status TEXT, vitality INTEGER, vitality_max INTEGER, updated_at REAL
);
CREATE TABLE battles(
	battle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, npc_name TEXT,
	npc_realm_index INTEGER, npc_stage INTEGER, player_hp INTEGER, player_hp_max INTEGER,
	npc_hp INTEGER, npc_hp_max INTEGER, status TEXT, location TEXT, source TEXT,
	target_key TEXT, npc_suppressed_turns INTEGER DEFAULT 0, version INTEGER DEFAULT 0,
	created_at REAL, updated_at REAL
);
`); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(
		`INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,life_status,vitality,vitality_max,updated_at)
		 VALUES(7,'Tester','','Sword Cultivator','Fire',?,'{"body":5,"agility":5,"spirit":5}',3,4,0,1,0,0,'alive',20,20,0)`,
		[]any{location},
	); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return path
}

func startCombatAt(t *testing.T, location, kind string) error {
	t.Helper()
	conn, err := storage.Open(setupViolenceDB(t, location))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(map[string]any{
		"kind": kind, "npc_name": "Wandering Swordsman", "source": "test:" + kind,
		"npc_realm_index": 2, "npc_stage": 3, "severity": 5,
	})
	if err != nil {
		t.Fatal(err)
	}
	_, mutErr := combatStartAction(conn, violenceCatalog(), 7, raw)
	if conn.InTransaction() {
		_ = conn.Rollback()
	}
	return mutErr
}

func TestASafeZoneRefusesAFightSomebodyChoseToStart(t *testing.T) {
	if err := startCombatAt(t, "Greenriver Town", "challenge"); err == nil {
		t.Fatal("a challenge in a safe zone was allowed; the engine had no such rule before v1.0.6 " +
			"and app/bot/commands/battle.py was the only thing refusing it")
	} else if !strings.Contains(err.Error(), "suppress") {
		t.Fatalf("the refusal does not name the suppression: %v", err)
	}
	if err := startCombatAt(t, "Hunting Ground", "challenge"); err != nil {
		t.Fatalf("a challenge in the wilds must still be allowed: %v", err)
	}
}

func TestBeingCaughtInSomethingIsNotStartingIt(t *testing.T) {
	// rc.49's asymmetry, one system over: the player-triggered draw is banded
	// and the autonomous one is not, because walking into a thing is not the
	// same as being handed it. A world event that landed in a town must still
	// be fightable, or the safe-zone rule would silently delete the event
	// battle in 446 of 477 places.
	if err := startCombatAt(t, "Greenriver Town", "event"); err != nil {
		t.Fatalf("an event battle in a safe zone must still be allowed: %v", err)
	}
	if err := startCombatAt(t, "Golden Pavilion", "event"); err != nil {
		t.Fatalf("an event battle on a protected floor must still be allowed: %v", err)
	}
}

func TestOnlyAProtectedInteriorIsASanctuary(t *testing.T) {
	catalog := violenceCatalog()
	for _, tc := range []struct {
		location string
		want     string
	}{
		{"Golden Pavilion", "Golden Pavilion Auction House"},
		{"Unguarded Stalls", ""},
		{"Greenriver Town", ""},
		{"Hunting Ground", ""},
		{"Nowhere At All", ""},
		{"", ""},
	} {
		name, ok := LocationIsSanctuary(catalog, tc.location)
		if tc.want == "" {
			if ok {
				t.Fatalf("%q is not a protected interior but answered sanctuary %q", tc.location, name)
			}
			continue
		}
		if !ok || name != tc.want {
			t.Fatalf("%q: got (%q,%v), want (%q,true) - protected_interior is read by nothing again",
				tc.location, name, ok, tc.want)
		}
	}
}

func TestTheRefusalNamesTheHouseOnItsOwnFloor(t *testing.T) {
	// A player refused in the street and a player refused on an auction floor
	// have been stopped by different things, and the sentence should say so -
	// the floor is the one they can do something about, by leaving.
	suppressed, why := violenceSuppressed(violenceCatalog(), "Golden Pavilion")
	if !suppressed || !strings.Contains(why, "Golden Pavilion Auction House") {
		t.Fatalf("the floor's refusal does not name the house: %v %q", suppressed, why)
	}
	suppressed, why = violenceSuppressed(violenceCatalog(), "Greenriver Town")
	if !suppressed || strings.Contains(why, "Auction") {
		t.Fatalf("the street's refusal should not name a house: %v %q", suppressed, why)
	}
	if suppressed, _ := violenceSuppressed(violenceCatalog(), "Hunting Ground"); suppressed {
		t.Fatal("the wilds suppress nothing")
	}
}
