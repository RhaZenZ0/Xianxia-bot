package simulation

import (
	"testing"

	"xianxia/core/internal/storage"
)

// A clan relation ends, and a broken treaty leaves a rivalry (v1.3.3).
//
// `active` was written 1 by every INSERT and 0 by nothing, and no rule re-typed
// a row: an alliance a killing had taken to zero sat there, read by
// `family.support` as worthless, for ever. On the owner's call a treaty at or
// below zero ends and a rivalry opens from both sides; a rivalry at or above
// zero ends and nothing follows; a blood feud never ends by drift.

func seedClanRelation(t *testing.T, path string, family, partner int64, name, relation string, score int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	var partnerArg any = partner
	if partner == 0 {
		partnerArg = nil
	}
	if _, err := conn.Execute(`INSERT INTO martial_clan_relations(
family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
VALUES(?,?,?,?,?,1,0,0)`, []any{family, partnerArg, name, relation, score}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func tickClans(t *testing.T, path string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := clanRunner().clans(conn, 1, 5000); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestATreatyAtZeroEndsAndARivalryOpensFromBothSides(t *testing.T) {
	path := clanDB(t)
	neverSigns(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 60, 10, 0)
	b := addHouse(t, path, "Zhao", "Riverguard City", 60, 10, 0)
	// A killing took the alliance down to -1 (the drift will lift it to 0).
	seedClanRelation(t, path, a, b, "Zhao", "alliance", -1)
	seedClanRelation(t, path, b, a, "Wei", "alliance", -1)
	tickClans(t, path)
	if n := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations WHERE relation_type='alliance' AND active=1`); n != 0 {
		t.Fatalf("%d alliance row(s) still active at 0; nothing ended the relation", n)
	}
	for _, side := range [][2]int64{{a, b}, {b, a}} {
		score := clanInt(t, path, `SELECT COALESCE(MAX(relation_score),999) FROM martial_clan_relations WHERE family_id=? AND partner_family_id=? AND relation_type='rivalry' AND active=1`, side[0], side[1])
		if score != clanRelationOpeningScore["rivalry"] {
			t.Fatalf("house %d holds no rivalry with %d at the opening score (got %d)", side[0], side[1], score)
		}
	}
	if n := clanInt(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='clan_relation_ended'`); n != 2 {
		t.Fatalf("the world remembers %d ending(s), want one per side", n)
	}
}

func TestARivalryThatCoolsToNothingEndsAndNothingFollows(t *testing.T) {
	path := clanDB(t)
	neverSigns(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 60, 10, 0)
	b := addHouse(t, path, "Zhao", "Riverguard City", 60, 10, 0)
	seedClanRelation(t, path, a, b, "Zhao", "rivalry", 1)
	tickClans(t, path)
	if n := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations WHERE active=1`); n != 0 {
		t.Fatalf("%d relation(s) active after a rivalry cooled to nothing; a rivalry at 0 must end and leave nothing", n)
	}
}

func TestABloodFeudNeverEndsByDrift(t *testing.T) {
	path := clanDB(t)
	neverSigns(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 60, 10, 0)
	b := addHouse(t, path, "Zhao", "Riverguard City", 60, 10, 0)
	seedClanRelation(t, path, a, b, "Zhao", "blood_feud", 5)
	tickClans(t, path)
	if n := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations WHERE relation_type='blood_feud' AND active=1`); n != 1 {
		t.Fatalf("a blood feud ended on the tick; it came from a body and only combat_aftermath owns it")
	}
}

func TestATreatyStillWarmIsLeftAlone(t *testing.T) {
	path := clanDB(t)
	neverSigns(t)
	a := addHouse(t, path, "Wei", "Riverguard City", 60, 10, 0)
	b := addHouse(t, path, "Zhao", "Riverguard City", 60, 10, 0)
	seedClanRelation(t, path, a, b, "Zhao", "trade_pact", 1)
	tickClans(t, path)
	if n := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations WHERE relation_type='trade_pact' AND active=1`); n != 1 {
		t.Fatalf("a trade pact above zero was ended")
	}
	if n := clanInt(t, path, `SELECT COUNT(*) FROM martial_clan_relations WHERE relation_type='rivalry'`); n != 0 {
		t.Fatalf("a rivalry was opened against a treaty that had not broken")
	}
}
