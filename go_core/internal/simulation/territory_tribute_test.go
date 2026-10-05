package simulation

import (
	"testing"

	"xianxia/core/internal/worlddata"
)

// v1.29.0: holding ground paid nothing. A territory's resource_type was worked
// out at seeding and read by no rule; held places send their sect what they
// yield now.
func TestHeldGroundStocksItsSectsTreasury(t *testing.T) {
	path := setupSimulationDB(t, sectTributeSchema+`
CREATE TABLE territory_state(territory_key TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '', controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50, defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY, prosperity INTEGER NOT NULL DEFAULT 50);
INSERT INTO territory_state(territory_key,controller_type,controller_key,resource_type) VALUES('Iron Hills','sect','Azure Cloud Sect','ore');
INSERT INTO territory_state(territory_key,controller_type,controller_key,resource_type,unrest) VALUES('Restless Fen','sect','Azure Cloud Sect','spirit_herbs',60);
INSERT INTO territory_state(territory_key,controller_type,controller_key,resource_type) VALUES('Neutral Glade','neutral','','spirit_herbs');
INSERT INTO civilization_regions(location,prosperity) VALUES('Iron Hills',80);
`)
	r := tributeRunner()
	for _, place := range []string{"Iron Hills", "Restless Fen", "Neutral Glade"} {
		r.World.Locations[place] = worlddata.LocationDefinition{World: "Mortal World"}
	}
	runTribute(t, path, r, 1)
	if got := treasury(t, path, "Azure Cloud Sect", "spirit_iron"); got != 2 {
		t.Fatalf("a prospering ore territory sent %d spirit iron, want 2 (one, and one for prosperity)", got)
	}
	if got := treasury(t, path, "Azure Cloud Sect", "spirit_herb"); got != 0 {
		t.Fatalf("a restless herb territory sent %d herbs, want none", got)
	}
}

func TestATerritorysLotsFollowItsFortunes(t *testing.T) {
	for _, tc := range []struct{ prosperity, unrest, want int64 }{
		{50, 0, 1}, {80, 0, 2}, {50, 60, 0}, {90, 70, 1},
	} {
		if got := territoryLots(tc.prosperity, tc.unrest); got != tc.want {
			t.Errorf("prosperity %d unrest %d: %d lots, want %d", tc.prosperity, tc.unrest, got, tc.want)
		}
	}
}
