package game

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The Marrow-Tempering Pill (v1.8.0) drives the shipped catalogue: the pill's
// numbers are content, and a fixture that restated them could agree with
// itself and disagree with the file.
const marrowSchema = `
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, qi INTEGER, qi_max INTEGER, vitality INTEGER, vitality_max INTEGER,
	body_realm_index INTEGER NOT NULL DEFAULT 0, life_extension_years INTEGER NOT NULL DEFAULT 0, updated_at REAL);
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY, user_id INTEGER, player_hp INTEGER, player_hp_max INTEGER, status TEXT,
	version INTEGER DEFAULT 0, updated_at REAL);
CREATE TABLE inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id));
CREATE TABLE active_effects(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, effect_key TEXT, name TEXT,
	source_type TEXT, source_id TEXT, effect_json TEXT, stacks INTEGER, starts_game_minute INTEGER,
	ends_game_minute INTEGER, created_at REAL, UNIQUE(user_id,effect_key,source_type,source_id));
CREATE TABLE alchemy_state(user_id INTEGER PRIMARY KEY, pill_toxicity INTEGER NOT NULL DEFAULT 0,
	last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0, total_refinements INTEGER NOT NULL DEFAULT 0,
	successful_refinements INTEGER NOT NULL DEFAULT 0, flawless_refinements INTEGER NOT NULL DEFAULT 0,
	best_margin INTEGER NOT NULL DEFAULT -99, last_quality TEXT NOT NULL DEFAULT '', updated_at REAL);
CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT NOT NULL,
	payload_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE soul_legacy(user_id INTEGER PRIMARY KEY, incarnation_count INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL,
	FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
INSERT INTO characters(user_id,qi,qi_max,vitality,vitality_max,body_realm_index,updated_at) VALUES(7,10,20,5,40,0,0);
INSERT INTO alchemy_state(user_id,updated_at) VALUES(7,0);
`

func marrowWorld(t *testing.T) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(filepath.Join(t.TempDir(), "marrow.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if err := conn.ExecScript(marrowSchema); err != nil {
		t.Fatal(err)
	}
	return conn
}

func temper(t *testing.T, conn *storage.Conn, itemID string) (map[string]any, error) {
	t.Helper()
	if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(7,?,1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+1`, []any{itemID}); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"item_id": itemID, "game_minute": 100})
	mut, err := itemUseActionGo(conn, shippedCatalog(t), 7, raw)
	res, _ := mut.Result.(map[string]any)
	return res, err
}

func carried(t *testing.T, conn *storage.Conn, itemID string) int64 {
	t.Helper()
	r, err := conn.Execute(`SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=7 AND item_id=?`, []any{itemID})
	if err != nil {
		t.Fatal(err)
	}
	return i64(r.Rows[0][0])
}

func TestThePillRaisesTheMaximumAndTheCurrentValueTogether(t *testing.T) {
	conn := marrowWorld(t)
	out, err := temper(t, conn, "marrow_tempering_pill")
	if err != nil {
		t.Fatal(err)
	}
	// 10% of 40 is 4, above the minimum of 2.
	if i64(out["vitality_max_gain"]) != 4 || i64(out["vitality_max"]) != 44 || i64(out["vitality"]) != 9 {
		t.Fatalf("a tempering at 5/40 answered %v, want +4 to 9/44", out)
	}
	if i64(out["tempering_used"]) != 1 || i64(out["tempering_allowance"]) != marrowTemperingPerBodyRealm {
		t.Fatalf("the reply counts %v of %v, want 1 of %d", out["tempering_used"], out["tempering_allowance"], marrowTemperingPerBodyRealm)
	}
}

func TestASmallBodyIsTemperedByTheMinimum(t *testing.T) {
	conn := marrowWorld(t)
	if _, err := conn.Execute(`UPDATE characters SET vitality_max=12 WHERE user_id=7`, nil); err != nil {
		t.Fatal(err)
	}
	out, err := temper(t, conn, "marrow_tempering_pill")
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["vitality_max_gain"]) != 2 {
		t.Fatalf("10%% of 12 is 1 and the minimum is 2; the pill added %v", out["vitality_max_gain"])
	}
}

func TestTheMarrowTakesOnlySoManyAtOneBodyRealm(t *testing.T) {
	conn := marrowWorld(t)
	for i := 0; i < marrowTemperingPerBodyRealm; i++ {
		if _, err := temper(t, conn, "marrow_tempering_pill"); err != nil {
			t.Fatalf("pill %d was refused: %v", i+1, err)
		}
	}
	_, err := temper(t, conn, "marrow_tempering_pill")
	if err == nil || !strings.Contains(err.Error(), "taken all it can") {
		t.Fatalf("a pill past the allowance was not refused: %v", err)
	}
	if got := carried(t, conn, "marrow_tempering_pill"); got != 1 {
		t.Fatalf("the refused pill left %d in the bag, want 1 - it was spent for nothing", got)
	}
}

func TestTheNextBodyRealmOpensTheAllowanceAgain(t *testing.T) {
	conn := marrowWorld(t)
	for i := 0; i < marrowTemperingPerBodyRealm; i++ {
		if _, err := temper(t, conn, "marrow_tempering_pill"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := conn.Execute(`UPDATE characters SET body_realm_index=1 WHERE user_id=7`, nil); err != nil {
		t.Fatal(err)
	}
	out, err := temper(t, conn, "marrow_tempering_pill")
	if err != nil {
		t.Fatalf("a new body realm did not open the allowance: %v", err)
	}
	if i64(out["tempering_used"]) != 1 {
		t.Fatalf("the new realm counts %v, want 1", out["tempering_used"])
	}
	// And going back down does not refill the realm already spent.
	if _, err := conn.Execute(`UPDATE characters SET body_realm_index=0 WHERE user_id=7`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := temper(t, conn, "marrow_tempering_pill"); err == nil {
		t.Fatal("lowering the body realm and raising it again refilled an allowance already spent")
	}
}

func TestANewLifeIsTemperedAfresh(t *testing.T) {
	conn := marrowWorld(t)
	if _, err := conn.Execute(`INSERT INTO soul_legacy(user_id,incarnation_count,updated_at) VALUES(7,1,0)`, nil); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < marrowTemperingPerBodyRealm; i++ {
		if _, err := temper(t, conn, "marrow_tempering_pill"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := conn.Execute(`UPDATE soul_legacy SET incarnation_count=2 WHERE user_id=7`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := temper(t, conn, "marrow_tempering_pill"); err != nil {
		t.Fatalf("a new life inherited the last life's spent allowance: %v", err)
	}
}

func TestAFinerPillTempersFurther(t *testing.T) {
	conn := marrowWorld(t)
	out, err := temper(t, conn, "marrow_tempering_pill@high")
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["vitality_max_gain"]) <= 4 {
		t.Fatalf("a High pill added %v, no more than a Low one's 4", out["vitality_max_gain"])
	}
}

func TestTheMarrowIsNotTemperedMidBattle(t *testing.T) {
	conn := marrowWorld(t)
	if _, err := conn.Execute(`INSERT INTO battles(battle_id,user_id,player_hp,player_hp_max,status) VALUES(1,7,5,40,'active')`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := temper(t, conn, "marrow_tempering_pill"); err == nil {
		t.Fatal("a pill was tempered in the middle of a battle")
	}
	if got := carried(t, conn, "marrow_tempering_pill"); got != 1 {
		t.Fatalf("the refused pill left %d in the bag, want 1", got)
	}
}

func TestTheBattlePanelNeverOffersThePill(t *testing.T) {
	item, _, ok := itemDef(shippedCatalog(t), "marrow_tempering_pill")
	if !ok {
		t.Fatal("the shipped catalogue carries no marrow_tempering_pill")
	}
	if item.Use.Instant.QiRestore > 0 || item.Use.Instant.VitalityRestore > 0 {
		t.Fatal("the pill carries an instant restore, so combat.recovery_item would spend it without tempering anything")
	}
	if pillToxicityValue("marrow_tempering_pill", item) != 24 {
		t.Fatalf("a permanent gain carries %d residue, want 24 - the same as a lifespan gain", pillToxicityValue("marrow_tempering_pill", item))
	}
}
