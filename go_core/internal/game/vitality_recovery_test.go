package game

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A body mends on its own (v1.0.4).
//
// Before this nothing in the tree restored vitality with time - four pills and
// one technique were the whole of it - so a cultivator who lost a fight sat on
// the number the fight left them with until they bought their way off it.

const testRecoveryDay = int64(1440)

func vitalityRecoveryCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		VitalityRecovery: worlddata.VitalityRecovery{
			PercentPerGameDay: 25, MinutesPerGameDay: testRecoveryDay,
		},
	}
}

func setupVitalityRecoveryDB(t *testing.T, vitality, maxVitality int64, anchor any) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "vitality_recovery.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// Production's shape: the anchor is nullable, and `battles` exists because
	// the settle asks it whether this cultivator is mid-fight.
	if err := conn.ExecScript(`
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY, life_status TEXT DEFAULT 'alive',
	vitality INTEGER, vitality_max INTEGER, vitality_recovered_game_minute INTEGER
);
CREATE TABLE battles(
	battle_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES characters(user_id),
	status TEXT, player_hp INTEGER
);
`); err != nil {
		t.Fatal(err)
	}
	anchorSQL := "NULL"
	if anchor != nil {
		anchorSQL = fmt.Sprint(anchor)
	}
	if err := conn.ExecScript(fmt.Sprintf(
		`INSERT INTO characters(user_id,life_status,vitality,vitality_max,vitality_recovered_game_minute)
		 VALUES(42,'alive',%d,%d,%s);`, vitality, maxVitality, anchorSQL)); err != nil {
		t.Fatal(err)
	}
	return path
}

func settleRecovery(t *testing.T, path string, gameMinute int64) (int64, int64, any) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	gain, err := settleVitalityRecoveryTx(conn, vitalityRecoveryCatalog(), 42, gameMinute)
	if err != nil {
		t.Fatalf("the settle must never fail an action: %v", err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	r, err := conn.Execute(`SELECT vitality,vitality_recovered_game_minute FROM characters WHERE user_id=42`, nil)
	if err != nil {
		t.Fatal(err)
	}
	row := firstRowMap(r)
	return gain, i64(row["vitality"]), row["vitality_recovered_game_minute"]
}

// TestABodyMendsOnItsOwn. Drill: delete the UPDATE and this prints 0.
func TestABodyMendsOnItsOwn(t *testing.T) {
	path := setupVitalityRecoveryDB(t, 0, 12, 1000)
	// One world day at 25% of a maximum of 12 is three points.
	gain, vitality, _ := settleRecovery(t, path, 1000+testRecoveryDay)
	if gain != 3 || vitality != 3 {
		t.Fatalf("a world day of rest gained %d to vitality %d; 25%% of 12 is 3", gain, vitality)
	}
}

// TestTheRemainderIsCarried: resting in pieces must be worth exactly what
// resting in one span is. The anchor moves only by the minutes that bought a
// whole point, which is rc.56's `seclusionGainForSpan` rule one system over.
//
// The cadence matters and the drill is what said so. A first version settled on
// multiples of 120, and at 25% of 12 a point costs exactly 480 minutes - so
// every settle landed on a point boundary, `anchor + consumed` and `gameMinute`
// coincided, and discarding the remainder changed nothing. The settles here
// land *between* boundaries, which is the only place the two differ.
func TestTheRemainderIsCarried(t *testing.T) {
	// A point costs 480 minutes here (25% of 12 a day). 700 buys one with 220
	// left over; 1440 is three points exactly.
	pieces := setupVitalityRecoveryDB(t, 0, 12, 0)
	settleRecovery(t, pieces, 700)
	_, piecesVitality, _ := settleRecovery(t, pieces, testRecoveryDay)

	whole := setupVitalityRecoveryDB(t, 0, 12, 0)
	_, wholeVitality, _ := settleRecovery(t, whole, testRecoveryDay)

	if wholeVitality != 3 {
		t.Fatalf("one world day gave %d, not 3; the test proves nothing", wholeVitality)
	}
	if piecesVitality != wholeVitality {
		t.Fatalf("resting in two pieces gave %d and one span gave %d; the leftover minutes are being discarded",
			piecesVitality, wholeVitality)
	}
}

// TestTimeAtFullDoesNotBank: a cultivator who has been whole for a world week
// must not carry that week into the next wound.
func TestTimeAtFullDoesNotBank(t *testing.T) {
	path := setupVitalityRecoveryDB(t, 12, 12, 0)
	settleRecovery(t, path, 7*testRecoveryDay) // whole the entire time
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE characters SET vitality=1 WHERE user_id=42`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	conn.Close()

	gain, vitality, _ := settleRecovery(t, path, 7*testRecoveryDay+1)
	if gain != 0 || vitality != 1 {
		t.Fatalf("a week spent whole banked %d points into the next wound (vitality %d)", gain, vitality)
	}
}

// TestAFightIsNotRest. `combatTurnAction` keeps `battles.player_hp` and
// `characters.vitality` in lockstep, so mending behind an active battle's back
// would desync them and the next turn would write the stale number back.
func TestAFightIsNotRest(t *testing.T) {
	path := setupVitalityRecoveryDB(t, 2, 12, 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`INSERT INTO battles(battle_id,user_id,status,player_hp) VALUES(1,42,'active',2);`); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	gain, vitality, _ := settleRecovery(t, path, 5*testRecoveryDay)
	if gain != 0 || vitality != 2 {
		t.Fatalf("a cultivator mid-fight healed %d to %d; the battle row and the sheet would then disagree", gain, vitality)
	}
}

// TestAnUnauthoredRateHealsNobody: the fallback is no recovery, never a rate of
// the engine's own invention. A fallback that looks like a value is not a
// sentinel, and a healing rate nobody authored is exactly the kind of number
// that would then be tuned by editing Go.
func TestAnUnauthoredRateHealsNobody(t *testing.T) {
	path := setupVitalityRecoveryDB(t, 0, 12, 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	gain, err := settleVitalityRecoveryTx(conn, worlddata.Catalog{}, 42, 9*testRecoveryDay)
	if err != nil {
		t.Fatalf("an unauthored rate must not fail an action: %v", err)
	}
	if gain != 0 {
		t.Fatalf("an empty catalogue healed %d; the rate is content, not a Go default", gain)
	}
}

// TestAnAnchorlessCharacterStartsTheClock: NULL is a character from before
// schema 59, and there is no honest way to say how long they have been hurt.
func TestAnAnchorlessCharacterStartsTheClock(t *testing.T) {
	path := setupVitalityRecoveryDB(t, 1, 12, nil)
	gain, vitality, anchor := settleRecovery(t, path, 5000)
	if gain != 0 || vitality != 1 {
		t.Fatalf("a NULL anchor granted %d points for a span nothing recorded", gain)
	}
	if i64(anchor) != 5000 {
		t.Fatalf("the clock was not started: anchor is %v", anchor)
	}
	// And from there it mends normally.
	if g, v, _ := settleRecovery(t, path, 5000+testRecoveryDay); g != 3 || v != 4 {
		t.Fatalf("after the anchor was set a world day gained %d to %d, want 3 to 4", g, v)
	}
}

// TestEveryActionSettlesTheBody is the wire, which the behavioural tests above
// cannot see: they call `settleVitalityRecoveryTx` directly, so deleting the
// call site would leave every one of them green. That is v1.0.3's own lesson
// about `TestSurvivingADefeatLeavesAHeartbeat`, applied before the fact.
func TestEveryActionSettlesTheBody(t *testing.T) {
	src, err := os.ReadFile("authoritative.go")
	if err != nil {
		t.Fatalf("the sweep cannot read authoritative.go, so it proves nothing: %v", err)
	}
	file, err := parser.ParseFile(token.NewFileSet(), "authoritative.go", src, 0)
	if err != nil {
		t.Fatalf("the sweep cannot parse authoritative.go, so it proves nothing: %v", err)
	}
	found, sawApply := false, false
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "applyAuthoritative" || fn.Body == nil {
			continue
		}
		sawApply = true
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			if ident, ok := call.Fun.(*ast.Ident); ok && ident.Name == "settleVitalityRecoveryTx" {
				found = true
			}
			return true
		})
	}
	if !sawApply {
		t.Fatal("the sweep did not find applyAuthoritative at all; it is broken, not the tree")
	}
	if !found {
		t.Fatal("applyAuthoritative does not settle vitality recovery, so a body only mends where " +
			"some other caller remembers to ask - and the lazy settle is the whole mechanism")
	}
}
