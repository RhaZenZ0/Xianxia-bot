package simulation

import (
	"testing"

	"xianxia/core/internal/gamerng"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Clan diplomacy (v1.0.1).
//
// The fixture carries the constraints production carries, which is the whole
// reason this is a new file rather than a case bolted onto `bootstrap_test.go`:
// that one declares `martial_clan_relations` with no foreign keys at all and a
// `birth_families` missing every column diplomacy reads, so it could not fail
// the way production fails. Here both keys are real and `storage.Open` sets
// `foreign_keys=ON`, so a partner id pointing at nobody is refused by SQLite
// rather than stored.
//
// Nothing below asserts that a random thing happened. The one roll in the step
// is lent with `gamerng.UseRoller`, and everything else - what two houses sign,
// whether they may - contains no die at all.

const clanDiplomacySchema = `
CREATE TABLE birth_families(
	family_id INTEGER PRIMARY KEY AUTOINCREMENT,
	family_name TEXT NOT NULL, surname TEXT NOT NULL, archetype TEXT NOT NULL DEFAULT 'martial_household',
	tier INTEGER NOT NULL DEFAULT 1, wealth INTEGER NOT NULL DEFAULT 20,
	influence INTEGER NOT NULL DEFAULT 10, stability INTEGER NOT NULL DEFAULT 60,
	alignment_bias INTEGER NOT NULL DEFAULT 0, location TEXT NOT NULL,
	head_name TEXT NOT NULL DEFAULT 'Head', head_realm_index INTEGER NOT NULL DEFAULT 0,
	branch_count INTEGER NOT NULL DEFAULT 1, retainer_count INTEGER NOT NULL DEFAULT 0,
	line_status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE martial_clan_branches(branch_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER NOT NULL,branch_name TEXT,branch_type TEXT,leader_name TEXT,members_estimate INTEGER,martial_strength INTEGER,wealth_share INTEGER,loyalty INTEGER,status TEXT,updated_at REAL,
	FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE);
CREATE TABLE martial_clan_retainers(retainer_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER NOT NULL,group_name TEXT,leader_name TEXT,role TEXT,members INTEGER,realm_index INTEGER,loyalty INTEGER,upkeep INTEGER,status TEXT,updated_at REAL,
	FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE);
CREATE TABLE martial_clan_relations(
	relation_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, partner_family_id INTEGER,
	partner_name TEXT NOT NULL, relation_type TEXT NOT NULL, relation_score INTEGER NOT NULL DEFAULT 0,
	active INTEGER NOT NULL DEFAULT 1, started_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
	FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE,
	FOREIGN KEY(partner_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL
);
CREATE TABLE world_history_events(
	source_key TEXT PRIMARY KEY,event_type TEXT,title TEXT,summary TEXT,significance INTEGER,visibility TEXT,
	location TEXT,world_name TEXT,faction TEXT,actor_type TEXT,actor_key TEXT,actor_name TEXT,target_type TEXT,
	target_key TEXT,target_name TEXT,related_user_id INTEGER,related_npc_name TEXT,tags TEXT,game_minute INTEGER,
	metadata_json TEXT,created_at REAL,updated_at REAL
);
`

func clanRunner() *Runner {
	return &Runner{World: worlddata.Catalog{Locations: map[string]worlddata.LocationDefinition{
		"Riverguard City":       {World: "Mortal World", SettlementType: "city", Roads: []string{"Emberforge City"}},
		"Emberforge City":       {World: "Mortal World", SettlementType: "city", Roads: []string{"Riverguard City"}},
		"Frostwatch City":       {World: "Mortal World", SettlementType: "city"},
		"Skyroad Immortal City": {World: "Immortal World", SettlementType: "city"},
	}, NPCs: map[string]worlddata.NPCDefinition{}}}
}

func clanDB(t *testing.T) string {
	t.Helper()
	return setupSimulationDB(t, clanDiplomacySchema)
}

// addHouse seeds one household. Wealth, influence and alignment are what the
// type rule reads, so every test states all three rather than leaning on a
// default that could quietly decide the answer.
func addHouse(t *testing.T, path, name, location string, wealth, influence, alignment int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`INSERT INTO birth_families(family_name,surname,location,wealth,influence,alignment_bias)
VALUES(?,?,?,?,?,?)`, []any{name, name, location, wealth, influence, alignment})
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return res.LastInsertID
}

// alwaysSigns lends the dice so every eligible pair rolls under the chance.
func alwaysSigns(t *testing.T) {
	t.Helper()
	t.Cleanup(gamerng.UseRoller(func(int) int { return 0 }))
}

// neverSigns lends the dice so no pair ever does.
func neverSigns(t *testing.T) {
	t.Helper()
	t.Cleanup(gamerng.UseRoller(func(int) int { return 99 }))
}

func clanRun(t *testing.T, r *Runner, path string, gm int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	made, err := r.clanDiplomacy(conn, gm)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return made
}

func clanInt(t *testing.T, path, query string, params ...any) int64 {
	t.Helper()
	return storage.ParseInt(simScalar(t, path, query, params...))
}

// A treaty one of the two houses has never heard of is not a treaty. Every
// reader is `WHERE family_id=?`, so a single row would be invisible to the
// other side - `/family clan`, the Admin Console's clan card and the standing
// term in `family.support` all read it that way.
func TestATreatyIsWrittenFromBothSides(t *testing.T) {
	path := clanDB(t)
	r := clanRunner()
	alwaysSigns(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)
	b := addHouse(t, path, "Zhao", "Emberforge City", 20, 40, 10)

	if made := clanRun(t, r, path, 5000); made != 1 {
		t.Fatalf("relations signed=%d, want 1", made)
	}
	if got := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations WHERE active=1`); got != 2 {
		t.Fatalf("rows written=%d, want one per house", got)
	}
	for _, pair := range [][2]int64{{a, b}, {b, a}} {
		got := clanInt(t, path,
			`SELECT COUNT(*) FROM martial_clan_relations WHERE family_id=? AND partner_family_id=? AND active=1`,
			pair[0], pair[1])
		if got != 1 {
			t.Fatalf("house %d holds %d relations with %d, want 1", pair[0], got, pair[1])
		}
	}
	if got := clanInt(t, path, `SELECT COUNT(*) FROM world_history_events`); got != 1 {
		t.Fatalf("history rows=%d, want one treaty one row", got)
	}
}

// The fault this release exists for: `partner_family_id` is foreign-keyed to
// `birth_families` and every row ever written left it NULL, because the
// partner was a name off a list. Nothing invents a house any more.
func TestAPartnerIsARealHouse(t *testing.T) {
	path := clanDB(t)
	alwaysSigns(t)
	addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)
	addHouse(t, path, "Zhao", "Riverguard City", 20, 40, 0)
	clanRun(t, clanRunner(), path, 5000)

	if got := clanInt(t, path,
		`SELECT COUNT(*) FROM martial_clan_relations WHERE partner_family_id IS NULL`); got != 0 {
		t.Fatalf("%d relations point at nobody", got)
	}
	if got := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations mr
		JOIN birth_families f ON f.family_id=mr.partner_family_id`); got != 2 {
		t.Fatalf("%d relations resolve to a real household, want 2", got)
	}
}

// The rc.44 rule, one level up: a road that leaves a world is a thing a player
// tears open, not a thing a clan signs.
func TestAHouseNeverTreatsAcrossAWorldBoundary(t *testing.T) {
	path := clanDB(t)
	alwaysSigns(t)
	addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)
	addHouse(t, path, "Yun", "Skyroad Immortal City", 20, 40, 0)

	if made := clanRun(t, clanRunner(), path, 5000); made != 0 {
		t.Fatalf("signed %d relations across a world boundary", made)
	}
}

// A house the catalogue does not place has no world, and a house with no world
// deals with nobody - the same answer `WhereAnNPCCanWalk` gives.
func TestAHouseNowhereOnTheMapTreatsWithNobody(t *testing.T) {
	path := clanDB(t)
	alwaysSigns(t)
	addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)
	addHouse(t, path, "Gu", "A Town The Catalogue Forgot", 20, 40, 0)

	if made := clanRun(t, clanRunner(), path, 5000); made != 0 {
		t.Fatalf("signed %d relations with a house that is nowhere", made)
	}
}

// Diplomacy does not re-sign what two houses already hold, in either
// direction, and it does not count an invented bootstrap partner as one: a
// NULL partner is nobody, and standing one must not use up a house's
// willingness to deal with a real neighbour.
func TestAPairAlreadyRelatedIsLeftAlone(t *testing.T) {
	path := clanDB(t)
	alwaysSigns(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)
	b := addHouse(t, path, "Zhao", "Riverguard City", 20, 40, 0)

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	// One real relation b->a, and one invented partner on a, the way bootstrap
	// writes them.
	if _, err := conn.Execute(`INSERT INTO martial_clan_relations(
family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
VALUES(?,?,'Wei','trade_pact',25,1,0,0)`, []any{b, a}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO martial_clan_relations(
family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
VALUES(?,NULL,'Tang Martial Clan','alliance',45,1,0,0)`, []any{a}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()

	if made := clanRun(t, clanRunner(), path, 5000); made != 0 {
		t.Fatalf("signed %d relations with a house it already deals with", made)
	}
	if got := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations`); got != 2 {
		t.Fatalf("rows=%d, want the two that were already there", got)
	}
}

// What two houses sign is decided by what they are, with no dice in it at all.
func TestWhatTwoHousesSignIsWhatTheyAre(t *testing.T) {
	house := func(wealth, influence, alignment int64) clanHouse {
		return clanHouse{wealth: wealth, influence: influence, alignment: alignment}
	}
	for _, tc := range []struct {
		name string
		a, b clanHouse
		want string
	}{
		{"opposed enough to quarrel", house(50, 50, -40), house(50, 50, 40), "rivalry"},
		{"neither friends nor enemies", house(50, 50, 0), house(50, 50, 40), ""},
		{"two rich houses trade", house(60, 10, 0), house(50, 10, 10), "trade_pact"},
		{"two weighty houses ally", house(20, 40, 0), house(20, 50, 10), "alliance"},
		{"anybody else marries", house(20, 10, 0), house(20, 10, 5), "marriage_pact"},
		{"one rich house is not a trade", house(90, 10, 0), house(10, 10, 0), "marriage_pact"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := clanRelationBetween(tc.a, tc.b); got != tc.want {
				t.Fatalf("signed %q, want %q", got, tc.want)
			}
			// The rule is symmetric, because a treaty is.
			if got := clanRelationBetween(tc.b, tc.a); got != tc.want {
				t.Fatalf("reversed signed %q, want %q", got, tc.want)
			}
		})
	}
}

// A blood feud comes from a body. `combat_aftermath.go` writes one when a
// player kills a family head, and that is the only thing in the game that
// should be able to make two houses enemies over one.
func TestABloodFeudIsNotSignedHere(t *testing.T) {
	for wealth := int64(0); wealth <= 100; wealth += 10 {
		for influence := int64(0); influence <= 100; influence += 10 {
			for gap := int64(-100); gap <= 100; gap += 10 {
				a := clanHouse{wealth: wealth, influence: influence, alignment: 0}
				b := clanHouse{wealth: wealth, influence: influence, alignment: gap}
				if got := clanRelationBetween(a, b); got == "blood_feud" {
					t.Fatalf("diplomacy signed a blood feud at wealth=%d influence=%d gap=%d", wealth, influence, gap)
				}
			}
		}
	}
}

// Every type diplomacy can reach has an opening score, and a type that has
// none is refused rather than opened at zero - which `family.support` reads as
// worthless and nothing can ever lift, because no rule re-types a row.
func TestARelationWithNoOpeningScoreIsRefused(t *testing.T) {
	for _, relation := range []string{"alliance", "marriage_pact", "trade_pact", "rivalry", "blood_feud"} {
		if score, ok := clanRelationOpeningScore[relation]; !ok || score == 0 {
			t.Fatalf("%s opens at %d (present=%v)", relation, score, ok)
		}
	}
	path := clanDB(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)
	b := addHouse(t, path, "Zhao", "Riverguard City", 20, 40, 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	err = clanRunner().signClanRelation(conn,
		clanHouse{id: a, name: "Wei"}, clanHouse{id: b, name: "Zhao"}, "concordat", 0, 0)
	if err == nil {
		t.Fatal("a relation the opening-score map does not carry was written anyway")
	}
}

// Nothing is signed while the dice say no, which is what makes every "want 1"
// above a statement about the rule rather than about the roll.
func TestNothingIsSignedWhenTheRollFails(t *testing.T) {
	path := clanDB(t)
	neverSigns(t)
	addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)
	addHouse(t, path, "Zhao", "Riverguard City", 20, 40, 0)

	if made := clanRun(t, clanRunner(), path, 5000); made != 0 {
		t.Fatalf("signed %d relations on a failed roll", made)
	}
}

// `trade_pact` was the one seeded type the drift CASE did not name, so it sat
// at exactly its opening 25 from the day the world started.
func TestATradePactWarmsLikeEverythingElseAHouseSigns(t *testing.T) {
	path := clanDB(t)
	neverSigns(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 60, 10, 0)
	b := addHouse(t, path, "Zhao", "Riverguard City", 60, 10, 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, pair := range [][2]int64{{a, b}, {b, a}} {
		if _, err := conn.Execute(`INSERT INTO martial_clan_relations(
family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
VALUES(?,?,'x','trade_pact',25,1,0,0)`, []any{pair[0], pair[1]}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()

	r := clanRunner()
	conn2, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.clans(conn2, 1, 5000); err != nil {
		t.Fatal(err)
	}
	if err := conn2.Commit(); err != nil {
		t.Fatal(err)
	}
	conn2.Close()

	if got := clanInt(t, path,
		`SELECT MIN(relation_score) FROM martial_clan_relations WHERE relation_type='trade_pact'`); got != 26 {
		t.Fatalf("a trade pact is worth %d after a tick, want 26 - it is in the ELSE again", got)
	}
}

// A one-household world signs nothing and does not fall over, which is why the
// invented bootstrap partners are left where they are: on such a world they
// are the only relation there can be.
func TestAWorldWithOneHouseholdSignsNothing(t *testing.T) {
	path := clanDB(t)
	alwaysSigns(t)
	addHouse(t, path, "Wei", "Riverguard City", 20, 40, 0)

	if made := clanRun(t, clanRunner(), path, 5000); made != 0 {
		t.Fatalf("a lone house signed %d relations", made)
	}
}
