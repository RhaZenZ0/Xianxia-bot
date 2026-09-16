package game

import (
	"testing"

	"xianxia/core/internal/storage"
)

// A past life's hands leave an echo, and only as far as the soul's memory has
// woken. These hold the shape of that echo - the cap, the memory gate, the
// zero cases, and that the most recent life with the best hands is the one
// named - and that the record samsara writes carries the trades to read.

func craftEchoDB(t *testing.T) string {
	t.Helper()
	path := tutoringDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// Production's shape (app/database/core.py).
	if err := conn.ExecScript(`CREATE TABLE IF NOT EXISTS soul_legacy(user_id INTEGER PRIMARY KEY,incarnation_count INTEGER NOT NULL DEFAULT 1,legacy_points INTEGER NOT NULL DEFAULT 0,memory_seed INTEGER NOT NULL DEFAULT 0,talent_echo INTEGER NOT NULL DEFAULT 0,law_echo INTEGER NOT NULL DEFAULT 0,insight_echo INTEGER NOT NULL DEFAULT 0,karmic_fortune INTEGER NOT NULL DEFAULT 0,special_trait TEXT NOT NULL DEFAULT '',awakened_memory INTEGER NOT NULL DEFAULT 0,past_lives_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL DEFAULT 0);`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func seedSoul(t *testing.T, path string, userID, seed, awakened int64, pastLives string) {
	t.Helper()
	batch4Exec(t, path, `INSERT INTO soul_legacy(user_id,memory_seed,awakened_memory,past_lives_json) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET memory_seed=excluded.memory_seed,awakened_memory=excluded.awakened_memory,past_lives_json=excluded.past_lives_json`, userID, seed, awakened, pastLives)
}

func TestTheCraftEchoIsTheBestPastLevelAsFarAsMemoryHasWoken(t *testing.T) {
	path := craftEchoDB(t)
	smith := `[{"name":"Old Wen","professions":{"Forging":4,"Alchemy":1}}]`
	cases := []struct {
		seed, awakened int64
		want           int64
		why            string
	}{
		{40, 0, 0, "a fresh rebirth remembers nothing"},
		{40, 10, 1, "a quarter awake, a quarter of level 4"},
		{40, 20, 2, "half awake, half of level 4"},
		{40, 40, 3, "fully awake, capped at +3 not +4"},
		{40, 60, 3, "awakened past the seed is still the seed"},
		{0, 10, 0, "no seed means no memory to wake"},
	}
	for _, tc := range cases {
		seedSoul(t, path, 42, tc.seed, tc.awakened, smith)
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		bonus, life, level, err := craftEchoTx(conn, 42, "Forging")
		conn.Close()
		if err != nil {
			t.Fatal(err)
		}
		if bonus != tc.want {
			t.Errorf("seed %d awakened %d: got +%d, want +%d (%s)", tc.seed, tc.awakened, bonus, tc.want, tc.why)
		}
		if bonus > 0 && (life != "Old Wen" || level != 4) {
			t.Errorf("seed %d awakened %d: echo names %q level %d, want Old Wen level 4", tc.seed, tc.awakened, life, level)
		}
	}
}

func TestACraftNobodyPractisedLeavesNoEcho(t *testing.T) {
	path := craftEchoDB(t)
	seedSoul(t, path, 42, 40, 40, `[{"name":"Old Wen","professions":{"Forging":4}}]`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for _, profession := range []string{"Alchemy", "Foraging", ""} {
		bonus, life, level, err := craftEchoTx(conn, 42, profession)
		if err != nil || bonus != 0 || life != "" || level != 0 {
			t.Errorf("%q: got +%d %q level %d err=%v, want nothing", profession, bonus, life, level, err)
		}
	}
	// And a soul with no record at all.
	bonus, _, _, err := craftEchoTx(conn, 7, "Forging")
	if err != nil || bonus != 0 {
		t.Fatalf("no soul_legacy row: got +%d err=%v", bonus, err)
	}
}

func TestTheMostRecentLifeWithTheBestHandsIsNamed(t *testing.T) {
	path := craftEchoDB(t)
	// Two smiths at level 3, then a farmer: the later smith is the echo.
	seedSoul(t, path, 42, 50, 50, `[{"name":"First","professions":{"Forging":3}},{"name":"Second","professions":{"Forging":3}},{"name":"Third","professions":{"Alchemy":2}}]`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	bonus, life, level, err := craftEchoTx(conn, 42, "Forging")
	if err != nil || bonus != 3 || life != "Second" || level != 3 {
		t.Fatalf("got +%d %q level %d err=%v, want +3 Second level 3", bonus, life, level, err)
	}
	// A better, older life still wins on level.
	seedSoul(t, path, 42, 50, 50, `[{"name":"Master","professions":{"Forging":5}},{"name":"Later","professions":{"Forging":2}}]`)
	conn2, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn2.Close()
	bonus, life, level, err = craftEchoTx(conn2, 42, "Forging")
	if err != nil || bonus != 3 || life != "Master" || level != 5 {
		t.Fatalf("got +%d %q level %d err=%v, want +3 Master level 5", bonus, life, level, err)
	}
}

func TestTheRecordCarriesOnlyTradesPractisedPastNovice(t *testing.T) {
	path := craftEchoDB(t)
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,'Forging',3,10,0),(42,'Alchemy',0,45,0),(42,'Foraging',1,0,0),(9,'Forging',6,0,0)`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	got, err := pastLifeProfessionsTx(conn, 42)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got["Forging"] != 3 || got["Foraging"] != 1 {
		t.Fatalf("got %v, want Forging 3 and Foraging 1 only", got)
	}
}
