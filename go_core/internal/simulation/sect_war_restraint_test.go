package simulation

import (
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// v1.27.0: the war step picked any rival-held ground. Two sects bound by
// marriage could besiege each other, and a Mortal sect could declare on
// ground in another world - which claims have never been allowed to reach.

func restraintDB(t *testing.T, extra string) string {
	t.Helper()
	path := sectWarDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`CREATE TABLE sect_relations(sect_a TEXT NOT NULL, sect_b TEXT NOT NULL, relation_score INTEGER NOT NULL DEFAULT 0, relation_type TEXT NOT NULL DEFAULT 'neutral', treaty_status TEXT NOT NULL DEFAULT 'none', updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(sect_a,sect_b));` + extra); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestASectDoesNotBesiegeItsAlly(t *testing.T) {
	path := restraintDB(t, `INSERT INTO sect_relations(sect_a,sect_b,relation_type,relation_score) VALUES('Azure Reed Sect','Quiet Fen Hall','marriage_pact',20);`)
	defer gamerng.UseRoller(func(int) int { return 0 })()
	if declared := runWars(t, path, &Runner{}, 1, 10000); declared != 0 {
		t.Fatalf("a sect declared %d war(s) on the sect it is married to", declared)
	}
}

func TestASectDoesNotMarchIntoAnotherWorld(t *testing.T) {
	path := restraintDB(t, "")
	r := &Runner{World: worlddata.Catalog{
		Sects: map[string]worlddata.SectDefinition{
			"Azure Reed Sect": {Recruitment: worlddata.SectRecruitment{Location: "Reed Gate"}},
			"Quiet Fen Hall":  {Recruitment: worlddata.SectRecruitment{Location: "Fen Gate"}},
		},
		Locations: map[string]worlddata.LocationDefinition{
			"Reed Gate":    {World: "Mortal World"},
			"Fen Gate":     {World: "Spiritual World"},
			"reed_marches": {World: "Spiritual World"},
		},
	}}
	defer gamerng.UseRoller(func(int) int { return 0 })()
	if declared := runWars(t, path, r, 1, 10000); declared != 0 {
		t.Fatalf("a Mortal sect declared %d war(s) on Spiritual ground", declared)
	}
	// Same world, and it moves: the rule is the world, not a quiet step.
	r.World.Locations["reed_marches"] = worlddata.LocationDefinition{World: "Mortal World"}
	if declared := runWars(t, path, r, 1, 10000); declared != 1 {
		t.Fatalf("on its own world's ground the sect declared %d war(s), want 1", declared)
	}
}
