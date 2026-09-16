package game

import (
	"testing"

	"xianxia/core/internal/storage"
)

// Every household teaches its trade at level 0 already; what varies is how
// well. These hold the two things that vary - the tradition bonus, keyed on
// the trade rather than on one archetype's name, and the head start the
// household's Wealth buys - and that the head start is handed over once.

func TestTheTutoringBandsReadOffTheShippedSpread(t *testing.T) {
	cases := []struct {
		wealth    int64
		level, xp int64
		tutor     string
		who       string
	}{
		{26, 0, 0, "shown the basics", "the fallen martial clan"},
		{39, 0, 0, "shown the basics", "the top of the lowest band"},
		{40, 0, 30, "a journeyman in the family", "the border garrison, at the edge"},
		{42, 0, 30, "a journeyman in the family", "the martial household"},
		{59, 0, 30, "a journeyman in the family", "the top of the middle band"},
		{60, 0, 55, "a hired tutor", "the edge of the tutor band"},
		{61, 0, 55, "a hired tutor", "the alchemy family"},
		{79, 0, 55, "a hired tutor", "the top of the tutor band"},
		{80, 1, 0, "a master retained", "the edge of the master band"},
		{82, 1, 0, "a master retained", "the noble martial clan"},
	}
	for _, tc := range cases {
		level, xp, tutor := householdTutoring(tc.wealth)
		if level != tc.level || xp != tc.xp || tutor != tc.tutor {
			t.Errorf("wealth %d (%s): got level %d xp %d %q, want level %d xp %d %q", tc.wealth, tc.who, level, xp, tutor, tc.level, tc.xp, tc.tutor)
		}
		if level > 1 {
			t.Errorf("wealth %d: a head start is capped at Apprentice, got level %d", tc.wealth, level)
		}
	}
}

func tutoringDB(t *testing.T) string {
	t.Helper()
	path := sendoffDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// Production's shape (app/database/core.py), so the ON CONFLICT clause
	// the grant relies on has the primary key it names.
	if err := conn.ExecScript(`CREATE TABLE IF NOT EXISTS profession_progress(user_id INTEGER NOT NULL, profession TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 0, xp INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, quality_points INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, PRIMARY KEY(user_id, profession));`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestTheHouseholdTutorsOnceAndNeverResetsProgress(t *testing.T) {
	path := tutoringDB(t)
	familyID := sendoffFamily(t, path, "noble_martial_clan", "mortal:noble", 4, 82)
	batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id) VALUES(42,?)`, familyID)

	got := sendOut(t, path, 42, familyID, "noble_martial_clan", 100)
	tutoring, _ := got["tutoring"].(map[string]any)
	if tutoring == nil {
		t.Fatalf("the send-off must say who taught you: %+v", got)
	}
	if tutoring["profession"] != "Formation" || tutoring["tutor"] != "a master retained" || tutoring["granted"] != true {
		t.Fatalf("tutoring=%+v", tutoring)
	}
	if got["trade"] != "Formation" {
		t.Fatalf("the send-off names the trade: %+v", got)
	}
	if level := i64(actionScalar(t, path, `SELECT level FROM profession_progress WHERE user_id=42 AND profession='Formation'`)); level != 1 {
		t.Fatalf("an imperial clan's child leaves as an Apprentice, got level %d", level)
	}

	// Progress earned since is the row's, not the household's to reset. The
	// second send-off is the "asking the same household twice gets nothing"
	// path - the heirloom guard returns before anything is reported - and
	// the tutoring, which runs ahead of that guard, must find the row held
	// and leave it exactly as it is.
	batch4Exec(t, path, `UPDATE profession_progress SET level=1, xp=47 WHERE user_id=42 AND profession='Formation'`)
	again := sendOut(t, path, 42, familyID, "noble_martial_clan", 200)
	if len(again) != 0 {
		t.Fatalf("a second send-off from the same household hands over nothing: %+v", again)
	}
	if xp := i64(actionScalar(t, path, `SELECT xp FROM profession_progress WHERE user_id=42 AND profession='Formation'`)); xp != 47 {
		t.Fatalf("the household reset earned progress: xp=%d", xp)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM profession_progress WHERE user_id=42`)); n != 1 {
		t.Fatalf("profession rows=%d want 1", n)
	}
}

// The tutoring itself, asked directly, says "held" rather than writing when
// the row is already there - which is what the doors that reach a household
// with progress in hand (a dao-family rebirth, a samsara return) rely on.
func TestTutoringReportsAHeldRowWithoutTouchingIt(t *testing.T) {
	path := tutoringDB(t)
	familyID := sendoffFamily(t, path, "alchemy_family", "mortal:alchemy", 3, 61)
	batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id) VALUES(42,?)`, familyID)
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at) VALUES(42,'Alchemy',2,10,9,1,3,0)`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	got, err := tutorHouseholdTradeTx(conn, districtCatalog(t), 42, familyID, "alchemy_family", 5)
	if err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	if got["granted"] != false || got["tutor"] != "a hired tutor" || got["profession"] != "Alchemy" {
		t.Fatalf("tutoring=%+v", got)
	}
	if level := i64(actionScalar(t, path, `SELECT level FROM profession_progress WHERE user_id=42 AND profession='Alchemy'`)); level != 2 {
		t.Fatalf("a held row was rewritten: level=%d", level)
	}
}

func TestAPoorHouseholdShowsTheBasicsAndStartsNothing(t *testing.T) {
	path := tutoringDB(t)
	familyID := sendoffFamily(t, path, "fallen_martial_clan", "mortal:fallen", 2, 26)
	batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id) VALUES(42,?)`, familyID)
	got := sendOut(t, path, 42, familyID, "fallen_martial_clan", 100)
	tutoring, _ := got["tutoring"].(map[string]any)
	if tutoring == nil || tutoring["tutor"] != "shown the basics" || i64(tutoring["xp"]) != 0 || i64(tutoring["level"]) != 0 {
		t.Fatalf("tutoring=%+v", tutoring)
	}
	// Level 0 with 0 XP is still a row: it is the memory that the household
	// already taught, and it is what the picker of professions lists.
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM profession_progress WHERE user_id=42 AND profession='Forging'`)); n != 1 {
		t.Fatalf("profession row count=%d", n)
	}
}

func TestEveryHouseholdTradeEarnsTheTraditionBonus(t *testing.T) {
	path := tutoringDB(t)
	catalog := districtCatalog(t)
	cases := []struct {
		archetype, profession string
		want                  int64
		trade                 string
	}{
		{"body_tempering_family", "Alchemy", householdTradeBonus, "Alchemy"}, // taught Alchemy, got nothing until now
		{"alchemy_family", "Alchemy", householdTradeBonus, "Alchemy"},
		{"alchemy_family", "Forging", 0, "Alchemy"},
		{"martial_household", "Forging", householdTradeBonus, "Forging"},
		{"martial_household", "Alchemy", 0, "Forging"},
		{"tomb_watch_clan", "Inscription", householdTradeBonus, "Inscription"},
		{"noble_martial_clan", "Formation", householdTradeBonus, "Formation"},
	}
	for i, tc := range cases {
		userID := int64(100 + i)
		familyID := sendoffFamily(t, path, tc.archetype, "mortal:"+tc.archetype+"/"+tc.profession, 2, 50)
		batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id) VALUES(?,?)`, userID, familyID)
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		bonus, trade, err := householdTradeBonusTx(conn, catalog, userID, tc.profession)
		conn.Close()
		if err != nil {
			t.Fatal(err)
		}
		if bonus != tc.want || trade != tc.trade {
			t.Errorf("%s crafting %s: bonus %d trade %q, want %d %q", tc.archetype, tc.profession, bonus, trade, tc.want, tc.trade)
		}
	}
	// And the hills: Alchemy's gathering half, so the Alchemy houses' bonus.
	for _, tc := range []struct {
		archetype string
		want      int64
	}{{"body_tempering_family", householdTradeBonus}, {"alchemy_family", householdTradeBonus}, {"martial_household", 0}} {
		userID := int64(300 + len(tc.archetype))
		familyID := sendoffFamily(t, path, tc.archetype, "mortal:forage/"+tc.archetype, 2, 50)
		batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id) VALUES(?,?)`, userID, familyID)
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		bonus, _, err := householdForageBonusTx(conn, catalog, userID)
		conn.Close()
		if err != nil {
			t.Fatal(err)
		}
		if bonus != tc.want {
			t.Errorf("%s foraging: bonus %d want %d", tc.archetype, bonus, tc.want)
		}
	}
}

func TestSomebodyWithNoHouseholdCarriesNoTradition(t *testing.T) {
	path := tutoringDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	bonus, trade, err := householdTradeBonusTx(conn, districtCatalog(t), 999, "Forging")
	if err != nil || bonus != 0 || trade != "" {
		t.Fatalf("bonus=%d trade=%q err=%v", bonus, trade, err)
	}
}
