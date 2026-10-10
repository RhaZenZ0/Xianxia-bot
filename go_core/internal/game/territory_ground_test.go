package game

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A banner sits on a whole place. A sect holds a city, not one of its streets
// (`TerritoryIsWholePlace`, v1.12.0), and every reader of a banner - the sect a
// world event hits, the caravan toll, the city page - looks at the city's row;
// but only the world's own claim step ever obeyed the rule. `territory.claim`,
// both banner branches of `ResolveWarTx` and the war step's targets took any
// row, and migration 74 left an active war over each gate it neutralised.
//
// These drive the shipped catalogue and the war tables as production has them
// - `territory_wars` foreign-keyed to `territory_state` and every connection
// opened with `foreign_keys=ON` - because a fixture with no keys accepts the
// war over a row that was never there.

const (
	groundGate    = "Azure Cloud Mountain Gate"
	groundCity    = "Cloudblade City"
	groundEast    = "Cloudblade City East Gate"
	groundYards   = "Cloudblade Blade Yards"
	groundOther   = "Greenriver Town"
	groundAzure   = "Azure Cloud Sect"
	groundCrimson = "Crimson Furnace Sect"
	groundJade    = "Jade Meridian Sect"
)

func groundCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatalf("the content file is in the repository; the read is broken, not the tree: %v", err)
	}
	// The reader is asserted before it is trusted: a catalogue that did not
	// parse the places below would make every "is a part" answer false and
	// every assertion after it vacuous.
	for _, part := range []string{groundEast, groundYards, groundGate} {
		if whole, isPart := TerritoryGround(catalog, part); !isPart || whole != groundCity {
			t.Fatalf("%s should be a part of %s; the catalogue read is broken, not the tree (got %q, part=%v)", part, groundCity, whole, isPart)
		}
	}
	return catalog
}

// groundDB is the batch-4 cultivator (42) with the war tables as production has
// them, a territory row for every place the tests name - all neutral - and the
// two sects' standing and politics at values a war's end could move.
func groundDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(warFixtureSchema); err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`CREATE TABLE IF NOT EXISTS sect_politics_state(
        sect_name TEXT PRIMARY KEY, influence INTEGER NOT NULL DEFAULT 50, cohesion INTEGER NOT NULL DEFAULT 50,
        resources INTEGER NOT NULL DEFAULT 50, recruitment_pressure INTEGER NOT NULL DEFAULT 50,
        doctrine_pressure INTEGER NOT NULL DEFAULT 50, updated_at REAL NOT NULL DEFAULT 0);`); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{groundGate, groundCity, groundEast, groundYards, groundOther, "Cloudspine Foothills"} {
		if _, err := conn.Execute(`INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES(?,?,?,0)`, []any{key, key, key}); err != nil {
			t.Fatal(err)
		}
	}
	for _, sect := range []string{groundAzure, groundCrimson} {
		if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,influence,resources) VALUES(?,60,60)`, []any{sect}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := conn.Execute(`INSERT INTO sect_relations(sect_a,sect_b,relation_score,relation_type,updated_at) VALUES(?,?,-15,'neutral',0)`, []any{groundAzure, groundCrimson}); err != nil {
		t.Fatal(err)
	}
	// A Deacon of the Azure Cloud Sect: Core Disciple is the floor for a claim
	// or a blow, and a Deacon may also sue for peace.
	if _, err := conn.Execute(`INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,?,'Deacon',40,0)`, []any{groundAzure}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func groundHold(t *testing.T, path, key, sect string) {
	t.Helper()
	batch4Exec(t, path, `UPDATE territory_state SET controller_type='sect',controller_key=? WHERE territory_key=?`, sect, key)
}

// groundWar writes an active war the way a world that ran v1.12 to v1.18
// holds one - straight into the table, because the war door now refuses a part.
func groundWar(t *testing.T, path, attacker, defender, territory string) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_at,updated_at) VALUES(?,?,?,'active',0,0)`, attacker, defender, territory)
	batch4Exec(t, path, `INSERT INTO territory_war_operations(war_id,updated_at) SELECT MAX(war_id),0 FROM territory_wars`)
}

func groundBanner(t *testing.T, path, key string) string {
	t.Helper()
	return fmt.Sprint(actionScalar(t, path, `SELECT controller_type||':'||controller_key FROM territory_state WHERE territory_key=?`, key))
}

// groundSweep runs the repair on its own connection and commits it.
func groundSweep(t *testing.T, path string, cat worlddata.Catalog) int64 {
	t.Helper()
	conn := warConn(t, path)
	changed, err := SetAsidePartialHoldingsTx(conn, cat, 100, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return changed
}

// The state migration 74 leaves behind: Azure Cloud's claim moved onto its
// seat, the gate neutral - and the war that was on over the gate still on.
// The tick would fight it, and an attacker's win would plant a rival inside the
// seat city.
func TestAWarOverAGateIsSetAside(t *testing.T) {
	cat := groundCatalog(t)
	path := groundDB(t)
	groundHold(t, path, groundCity, groundAzure)
	groundWar(t, path, groundCrimson, groundAzure, groundGate)
	if got := groundBanner(t, path, groundGate); got != "neutral:" {
		t.Fatalf("the fixture is not the state migration 74 leaves: the gate reads %q", got)
	}
	if changed := groundSweep(t, path, cat); changed != 1 {
		t.Fatalf("the repair changed %d thing(s); want the one war", changed)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT w.status||'|'||o.resolution||'|'||o.winner_key FROM territory_wars w JOIN territory_war_operations o USING(war_id)`)); got != "resolved|set_aside|" {
		t.Fatalf("the war over the gate reads %q after the repair: the tick would fight it, and an attacker's win would plant a rival inside the seat city", got)
	}
	if got := groundBanner(t, path, groundGate); got != "neutral:" {
		t.Fatalf("the gate reads %q: a war set aside moves no banner", got)
	}
	if got := groundBanner(t, path, groundCity); got != "sect:"+groundAzure {
		t.Fatalf("the seat reads %q: the repair took the city off its holder", got)
	}
	conn := warConn(t, path)
	if until := WarTruceUntilTx(conn, cat, groundCrimson, groundGate, 200); until != 0 {
		t.Fatalf("a war set aside bound the attacker with a truce until %d", until)
	}
	// Nobody was thrown back and nobody won, so the standing the declaration
	// cost stands and nothing warms or cools it again, and no sect is stronger.
	if got := storage.ParseInt(actionScalar(t, path, `SELECT relation_score FROM sect_relations`)); got != -15 {
		t.Fatalf("a war set aside moved the two sects' standing to %d", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT GROUP_CONCAT(influence||'/'||resources) FROM sect_politics_state`)); got != "60/60,60/60" {
		t.Fatalf("a war set aside moved the sects' politics to %s", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT significance FROM world_history_events WHERE event_type='territory_war_resolved'`)); got != 50 {
		t.Fatalf("the world heard the ending at significance %d; it is below the Quest Forge's floor, because nobody won anything", got)
	}
	// It is no truce on the seat either: the attacker may still move on the city.
	if _, err := DeclareWarTx(conn, cat, groundCrimson, groundAzure, groundCity, 200, 2); err != nil {
		t.Fatalf("the sect that was set aside cannot move on the seat itself: %v", err)
	}
	if again := groundSweep(t, path, cat); again != 0 {
		t.Fatalf("a second pass changed %d thing(s); the repair is not idempotent", again)
	}
}

// A player's /territory claim from a gate or a street held a row of its own;
// the repair folds it into the city when nobody contests, and never picks a
// side when somebody does.
func TestAStreetIsFoldedIntoItsCity(t *testing.T) {
	cat := groundCatalog(t)
	t.Run("a sole holder is given the city and its streets are released", func(t *testing.T) {
		path := groundDB(t)
		groundHold(t, path, groundEast, groundJade)
		groundHold(t, path, groundYards, groundJade)
		groundSweep(t, path, cat)
		if got := groundBanner(t, path, groundCity); got != "sect:"+groundJade {
			t.Fatalf("a sect that held streets of the city and nothing else beside it left the city at %q", got)
		}
		for _, part := range []string{groundEast, groundYards} {
			if got := groundBanner(t, path, part); got != "neutral:" {
				t.Fatalf("%s reads %q after the repair; a street is released once the city carries the banner", part, got)
			}
		}
		if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='territory_claimed' AND location=?`, groundCity)); n != 1 {
			t.Fatalf("the world heard of the city's claim %d time(s); want once", n)
		}
	})
	t.Run("two holders: nobody is given the city, and both streets are released", func(t *testing.T) {
		path := groundDB(t)
		groundHold(t, path, groundEast, groundJade)
		groundHold(t, path, groundYards, groundCrimson)
		groundSweep(t, path, cat)
		if got := groundBanner(t, path, groundCity); got != "neutral:" {
			t.Fatalf("two sects held streets of one city and the repair gave it to %q", got)
		}
		for _, part := range []string{groundEast, groundYards} {
			if got := groundBanner(t, path, part); got != "neutral:" {
				t.Fatalf("%s reads %q after the repair", part, got)
			}
		}
	})
	t.Run("another sect holds the city: the street is released and the city is not touched", func(t *testing.T) {
		path := groundDB(t)
		groundHold(t, path, groundCity, groundAzure)
		groundHold(t, path, groundEast, groundJade)
		groundSweep(t, path, cat)
		if got := groundBanner(t, path, groundCity); got != "sect:"+groundAzure {
			t.Fatalf("a street's holder took the city off %q: it reads %q", groundAzure, got)
		}
		if got := groundBanner(t, path, groundEast); got != "neutral:" {
			t.Fatalf("the street reads %q", got)
		}
	})
	t.Run("a city at war is not folded into", func(t *testing.T) {
		path := groundDB(t)
		groundHold(t, path, groundEast, groundJade)
		groundWar(t, path, groundCrimson, groundAzure, groundCity)
		groundSweep(t, path, cat)
		if got := groundBanner(t, path, groundCity); got != "neutral:" {
			t.Fatalf("the repair handed a city under an active war to %q", got)
		}
		if got := groundBanner(t, path, groundEast); got != "neutral:" {
			t.Fatalf("the street reads %q", got)
		}
	})
}

// Whatever a resolver is told, a part of a city is never handed to a sect: an
// occupation, a cession, a peace and a hold all end with no victor and move
// nothing. The war door refuses to open one in the first place.
func TestNoResolverHandsOverAPart(t *testing.T) {
	cat := groundCatalog(t)
	for _, c := range []struct{ winner, resolution string }{
		{groundCrimson, "attacker_occupation"},
		{groundCrimson, "ceded"},
		{groundAzure, "peace"},
		{groundAzure, "defender_holds"},
	} {
		t.Run(c.resolution, func(t *testing.T) {
			path := groundDB(t)
			groundHold(t, path, groundEast, groundAzure)
			groundWar(t, path, groundCrimson, groundAzure, groundEast)
			batch4Exec(t, path, `INSERT INTO territory_war_actions(war_id,user_id,side,tactic,created_at) VALUES(1,42,'attacker','assault',0)`)
			conn := warConn(t, path)
			spoils, ended, err := ResolveWarTx(conn, cat, 1, c.winner, c.resolution, 5000, 1)
			if err != nil || !ended {
				t.Fatalf("ended=%v err=%v", ended, err)
			}
			if len(spoils) != 0 {
				t.Fatalf("a war over a street paid %d victor(s)", len(spoils))
			}
			if got := fmt.Sprint(scalarOn(t, conn, fmt.Sprintf(`SELECT controller_type||':'||controller_key||'|'||defense||'|'||unrest FROM territory_state WHERE territory_key='%s'`, groundEast))); got != "sect:"+groundAzure+"|50|0" {
				t.Fatalf("%s on a street left it reading %q; no banner, wall or unrest moves", c.resolution, got)
			}
			if got := fmt.Sprint(scalarOn(t, conn, `SELECT resolution||'|'||winner_key||'|'||occupation_until_game_minute FROM territory_war_operations`)); got != "set_aside||0" {
				t.Fatalf("%s on a street was recorded as %q; a set-aside war has no winner and no occupation", c.resolution, got)
			}
			if from := WarOccupiedFromTx(conn, groundEast, 5010); from != "" {
				t.Fatalf("the street is occupied from %q", from)
			}
			if until := WarTruceUntilTx(conn, cat, groundCrimson, groundEast, 5010); until != 0 {
				t.Fatalf("a war set aside bound a truce until %d", until)
			}
			if got := fmt.Sprint(scalarOn(t, conn, `SELECT GROUP_CONCAT(influence||'/'||resources) FROM sect_politics_state`)); got != "60/60,60/60" {
				t.Fatalf("a war set aside moved the sects' politics to %s", got)
			}
			if got := storage.ParseInt(scalarOn(t, conn, `SELECT relation_score FROM sect_relations`)); got != -15 {
				t.Fatalf("a war set aside moved the sects' standing to %d", got)
			}
			if got := storage.ParseInt(scalarOn(t, conn, `SELECT contribution_earned FROM sect_membership WHERE user_id=42`)); got != 0 {
				t.Fatalf("a fighter in a war set aside was paid %d", got)
			}
		})
	}
	t.Run("a declaration over a part is refused", func(t *testing.T) {
		path := groundDB(t)
		conn := warConn(t, path)
		for _, part := range []string{groundEast, groundYards, groundGate} {
			if _, err := DeclareWarTx(conn, cat, groundCrimson, groundAzure, part, 100, 1); err == nil || !strings.Contains(err.Error(), "part of "+groundCity) {
				t.Fatalf("a declaration over %s: %v; want a refusal naming %s", part, err, groundCity)
			}
		}
		if n := storage.ParseInt(scalarOn(t, conn, `SELECT COUNT(*) FROM territory_wars`)); n != 0 {
			t.Fatalf("%d war(s) were opened over a part", n)
		}
		// A whole place is still a war, and so is ground the catalogue does not
		// carry: the rule asks the catalogue, it does not guess.
		if _, err := DeclareWarTx(conn, cat, groundCrimson, groundAzure, groundCity, 100, 1); err != nil {
			t.Fatalf("a declaration over the city itself was refused: %v", err)
		}
		if _, err := conn.Execute(`INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES('the_ford','The Ford','Greenriver Town',0)`, nil); err != nil {
			t.Fatal(err)
		}
		if _, err := DeclareWarTx(conn, cat, groundJade, groundAzure, "the_ford", 100, 1); err != nil {
			t.Fatalf("a declaration over ground the catalogue does not carry was refused: %v", err)
		}
	})
}

// A decisive blow in a war over a street, through the production dispatch,
// reports the verdict the war door writes - not the occupation it would have
// won - and a peace over one costs nothing, because there are no terms to make.
func TestAWarActAndAPeaceOverAPartReportTheVerdict(t *testing.T) {
	cat := groundCatalog(t)
	setup := func(t *testing.T) string {
		path := groundDB(t)
		groundHold(t, path, groundEast, groundAzure)
		groundWar(t, path, groundCrimson, groundAzure, groundEast)
		batch4Exec(t, path, `UPDATE sect_membership SET sect_name=?,rank_level=40 WHERE user_id=42`, groundCrimson)
		return path
	}
	t.Run("war.act", func(t *testing.T) {
		path := setup(t)
		world := batch4WorldPath(t)
		batch4Exec(t, path, `UPDATE territory_war_operations SET siege_progress=100 WHERE war_id=1`)
		out, err := warAct(t, path, world, 42, 1, "assault")
		if err != nil {
			t.Fatal(err)
		}
		ops, _ := out["operations"].(map[string]any)
		if out["status"] != "resolved" || ops["resolution"] != WarSetAside || ops["winner_key"] != "" {
			t.Fatalf("a breach of a street's walls reads status=%v resolution=%v winner=%v; want resolved, %s, nobody", out["status"], ops["resolution"], ops["winner_key"], WarSetAside)
		}
		if storage.ParseInt(out["victory_points"]) != 0 || storage.ParseInt(ops["occupation_until_game_minute"]) != 0 {
			t.Fatalf("a breach of a street paid victory %v and occupied until %v", out["victory_points"], ops["occupation_until_game_minute"])
		}
		if got := groundBanner(t, path, groundEast); got != "sect:"+groundAzure {
			t.Fatalf("a breach of a street's walls left it reading %q", got)
		}
	})
	t.Run("war.peace", func(t *testing.T) {
		path := setup(t)
		ready := WarRules(cat).PeaceMinDays * warMinutesPerDay
		conn := warConn(t, path)
		out, err := warPeaceActionGo(conn, cat, 42, 1, ready)
		if err != nil {
			t.Fatal(err)
		}
		if out["resolution"] != WarSetAside || out["winner_key"] != "" || storage.ParseInt(out["cost"]) != 0 {
			t.Fatalf("a peace over a street reads resolution=%v winner=%v cost=%v; want %s, nobody, free", out["resolution"], out["winner_key"], out["cost"], WarSetAside)
		}
		if got := storage.ParseInt(scalarOn(t, conn, `SELECT contribution_points FROM sect_membership WHERE user_id=42`)); got != 0 {
			t.Fatalf("the member's balance is %d after a free peace", got)
		}
		if got := fmt.Sprint(scalarOn(t, conn, fmt.Sprintf(`SELECT controller_type||':'||controller_key FROM territory_state WHERE territory_key='%s'`, groundEast))); got != "sect:"+groundAzure {
			t.Fatalf("a peace over a street left it reading %q", got)
		}
	})
}

// A claim made from any part of a city is a claim on the city: it takes the
// city's row, pays once, and cannot be made again from the next street. An
// older bot that still names the part keeps working.
func TestAClaimFromAnyPartClaimsItsCity(t *testing.T) {
	cat := groundCatalog(t)
	world := batch4WorldPath(t)
	path := groundDB(t)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, groundEast)
	out, err := batch4ApplyErr(path, world, "territory.claim", 42, 1, map[string]any{"territory_key": groundEast})
	if err != nil {
		t.Fatalf("a claim from the East Gate was refused: %v", err)
	}
	r := batch4Result(t, out)
	if r["claimed"] != true || r["territory_key"] != groundCity {
		t.Fatalf("a claim from %s answered claimed=%v for %v; want the city", groundEast, r["claimed"], r["territory_key"])
	}
	if got := groundBanner(t, path, groundCity); got != "sect:"+groundAzure {
		t.Fatalf("the city reads %q after a claim from its gate", got)
	}
	if got := groundBanner(t, path, groundEast); got != "neutral:" {
		t.Fatalf("the street reads %q: a claim from a gate left the banner on the gate", got)
	}
	paid := storage.ParseInt(actionScalar(t, path, `SELECT contribution_earned FROM sect_membership WHERE user_id=42`))
	if paid != WarRules(cat).ActPoints {
		t.Fatalf("the claim paid %d contribution; want one act's worth", paid)
	}
	// The farm: a city has up to eight claimable parts and every claim paid,
	// so the next street's claim is the same city, held already.
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, groundYards)
	if _, err := batch4ApplyErr(path, world, "territory.claim", 42, 2, map[string]any{"territory_key": groundYards}); err == nil || !strings.Contains(err.Error(), "already controls") {
		t.Fatalf("a second claim from another street of the same city: %v; want 'already controls'", err)
	}
	if again := storage.ParseInt(actionScalar(t, path, `SELECT contribution_earned FROM sect_membership WHERE user_id=42`)); again != paid {
		t.Fatalf("claiming from another street paid %d more contribution", again-paid)
	}
	// And a rival who claims from a street declares on the city, not the street.
	batch4Exec(t, path, `UPDATE sect_membership SET sect_name=? WHERE user_id=42`, groundCrimson)
	out, err = batch4ApplyErr(path, world, "territory.claim", 42, 3, map[string]any{"territory_key": groundYards})
	if err != nil {
		t.Fatalf("a rival's claim from a street was refused: %v", err)
	}
	if got := fmt.Sprint(actionScalar(t, path, `SELECT territory_key FROM territory_wars WHERE status='active'`)); got != groundCity || batch4Result(t, out)["war_id"] == nil {
		t.Fatalf("a rival's claim from a street opened a war over %q; want %s", got, groundCity)
	}
}

// Standing in the city is the condition, not standing on the named row: a
// claim naming a street of another city is still refused from here.
func TestAClaimFromAnotherCityIsStillRefused(t *testing.T) {
	groundCatalog(t)
	world := batch4WorldPath(t)
	path := groundDB(t)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, groundOther)
	for _, key := range []string{groundEast, groundCity} {
		if _, err := batch4ApplyErr(path, world, "territory.claim", 42, 1, map[string]any{"territory_key": key}); err == nil || !strings.Contains(err.Error(), "is claimed from") {
			t.Fatalf("a claim on %s from %s: %v; want it refused as claimed from elsewhere", key, groundOther, err)
		}
	}
	if got := groundBanner(t, path, groundCity); got != "neutral:" {
		t.Fatalf("a refused claim left the city reading %q", got)
	}
}

// TerritoryGround is the one statement of the rule: across the whole shipped
// catalogue a key answers a whole place or nothing, and the answer is the
// city's own rule.
func TestTerritoryGroundIsAWholePlaceOrNothing(t *testing.T) {
	cat := groundCatalog(t)
	parts, wholes := 0, 0
	for name := range cat.Locations {
		whole, part := TerritoryGround(cat, name)
		if part != !TerritoryIsWholePlace(cat, name) {
			t.Fatalf("%s: part=%v disagrees with TerritoryIsWholePlace", name, part)
		}
		if !part {
			wholes++
			if whole != name {
				t.Fatalf("%s is a whole place and answered %q", name, whole)
			}
			continue
		}
		parts++
		if whole != "" && (!TerritoryIsWholePlace(cat, whole) || whole != cityOf(cat, name)) {
			t.Fatalf("%s answered %q, which is not its city %q or not a whole place", name, whole, cityOf(cat, name))
		}
	}
	if parts < 100 || wholes < 20 {
		t.Fatalf("the walk found %d parts and %d whole places; the catalogue read is broken, not the tree", parts, wholes)
	}
	// What the catalogue does not carry is left alone, and a private place that
	// is nobody's city is ground no sect can hold.
	if whole, part := TerritoryGround(cat, "the_ford"); part || whole != "the_ford" {
		t.Fatalf("a key the catalogue does not carry answered (%q, %v)", whole, part)
	}
	odd := worlddata.Catalog{Locations: map[string]worlddata.LocationDefinition{"Hidden Hollow": {Private: true}}}
	if whole, part := TerritoryGround(odd, "Hidden Hollow"); !part || whole != "" {
		t.Fatalf("a private place that is no part of a city answered (%q, %v); want (\"\", true)", whole, part)
	}
}

// Every production function that writes a sect's banner onto a territory row
// asks the rule, or is named here with the function it delegates to. The
// writers a rule is stated at are the ones it covers: this one was written
// for the world's own claim step and found three more.
func TestEveryBannerWriterAsksTheGround(t *testing.T) {
	asks := map[string]bool{"TerritoryGround": true, "TerritoryIsWholePlace": true, "WarVerdict": true}
	// A writer that does not ask but calls the function that does.
	delegates := map[string]string{"npcSectClaims": "claimTarget"}
	// What a statement sets, not what it selects by: advanceOccupations reads
	// controller_type='sect' in its WHERE and writes only unrest.
	banner := regexp.MustCompile(`(?is)update\s+territory_state\s+set\s.*controller_type\s*=\s*'sect'`)
	type fn struct {
		writes bool
		calls  map[string]bool
	}
	funcs := map[string]fn{}
	for _, dir := range []string{".", filepath.Join("..", "simulation")} {
		entries, err := os.ReadDir(dir)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			name := entry.Name()
			if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
				continue
			}
			file, err := parser.ParseFile(token.NewFileSet(), filepath.Join(dir, name), nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			for _, decl := range file.Decls {
				d, ok := decl.(*ast.FuncDecl)
				if !ok || d.Body == nil {
					continue
				}
				f := fn{calls: map[string]bool{}}
				ast.Inspect(d, func(n ast.Node) bool {
					switch x := n.(type) {
					case *ast.BasicLit:
						if x.Kind == token.STRING {
							if sql, err := strconv.Unquote(x.Value); err == nil {
								if i := strings.Index(strings.ToLower(sql), "where"); i >= 0 {
									sql = sql[:i]
								}
								f.writes = f.writes || banner.MatchString(sql)
							}
						}
					case *ast.CallExpr:
						switch callee := x.Fun.(type) {
						case *ast.Ident:
							f.calls[callee.Name] = true
						case *ast.SelectorExpr:
							f.calls[callee.Sel.Name] = true
						}
					}
					return true
				})
				funcs[d.Name.Name] = f
			}
		}
	}
	asksTheRule := func(f fn) bool {
		for name := range f.calls {
			if asks[name] {
				return true
			}
		}
		return false
	}
	writers := []string{}
	for name, f := range funcs {
		if f.writes {
			writers = append(writers, name)
		}
	}
	sort.Strings(writers)
	for _, want := range []string{"territoryClaimActionGo", "ResolveWarTx", "SetAsidePartialHoldingsTx", "npcSectClaims"} {
		found := false
		for _, w := range writers {
			found = found || w == want
		}
		if !found {
			t.Fatalf("the walk did not find %s writing a sect banner (found %v); the gate is broken, not the tree", want, writers)
		}
	}
	for _, name := range writers {
		f := funcs[name]
		if asksTheRule(f) {
			continue
		}
		if via, ok := delegates[name]; ok && f.calls[via] && asksTheRule(funcs[via]) {
			continue
		}
		t.Errorf("%s writes a sect's banner onto a territory row and never asks TerritoryGround, TerritoryIsWholePlace or WarVerdict: a sect holds a city, not one of its streets", name)
	}
}
