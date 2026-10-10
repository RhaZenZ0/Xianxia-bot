package game

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// The Player Editor's three progress levers (v1.23.0). Each is driven through
// the production dispatch with the real content file behind it (v1.0.11), and
// each undo is driven too, because an audit row is only a record of a change
// a GM can take back if the reversal really restores the row.

func setupProgressDB(t *testing.T) string {
	t.Helper()
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE profession_progress(user_id INTEGER NOT NULL,profession TEXT NOT NULL,level INTEGER NOT NULL DEFAULT 0,xp INTEGER NOT NULL DEFAULT 0,successes INTEGER NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,quality_points INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(user_id,profession),FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE law_progress(user_id INTEGER NOT NULL,law_id TEXT NOT NULL,comprehension INTEGER NOT NULL DEFAULT 0,insights INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(user_id,law_id),FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at,contribution_points,contribution_earned) VALUES(42,'Azure Cloud Sect','Outer Disciple',10,0,30,120);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestTheTradeRankLeverSetsAndUndoes(t *testing.T) {
	path := setupProgressDB(t)
	applyAdmin(t, path, "admin.player.set_profession", map[string]any{"user_id": 42, "profession": "Beast Taming", "level": 4, "xp": 7, "reason": "fault"})
	if got := storage.ParseInt(scalar(t, path, "SELECT level FROM profession_progress WHERE user_id=42 AND profession='Beast Taming'")); got != 4 {
		t.Fatalf("level=%d, want 4", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM profession_progress WHERE user_id=42")); got != 0 {
		t.Fatalf("an undo of a trade the character never had left %d row(s); it must delete the row it made", got)
	}
}

func TestTheTradeRankLeverIsHeldToTheRuleThatWritesIt(t *testing.T) {
	path := setupProgressDB(t)
	cases := []struct {
		payload map[string]any
		want    string
	}{
		{map[string]any{"user_id": 42, "profession": "Basket Weaving", "level": 1, "xp": 0}, "profession must be one of"},
		{map[string]any{"user_id": 42, "profession": "Forging", "level": tradeTopRank + 1, "xp": 0}, "level must be 0 to"},
		// A full bar below the top is a rank-up the next craft would take.
		{map[string]any{"user_id": 42, "profession": "Forging", "level": 0, "xp": professionXPNeeded(0)}, "xp must be 0 to"},
	}
	for _, c := range cases {
		if _, err := applyAdminRaw(t, path, "admin.player.set_profession", 0, c.payload); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%v: want a refusal naming %q, got %v", c.payload, c.want, err)
		}
	}
	// Every trade the engine advances is one the lever accepts.
	for _, name := range []string{miningProfession, beastTamingProfession, artifactRefiningProfession, appraisalProfession, "Foraging", "Alchemy"} {
		if _, err := applyAdminRaw(t, path, "admin.player.set_profession", 0, map[string]any{"user_id": 42, "profession": name, "level": 0, "xp": 0}); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestTheLawLeverSetsComprehensionAndKeepsTheSittings(t *testing.T) {
	path := setupProgressDB(t)
	if _, err := storageExec(path, `INSERT INTO law_progress VALUES(42,'fire',20,6,0)`); err != nil {
		t.Fatal(err)
	}
	applyAdmin(t, path, "admin.player.set_law", map[string]any{"user_id": 42, "law_id": "fire", "comprehension": 55, "reason": "fault"})
	if got := storage.ParseInt(scalar(t, path, "SELECT comprehension FROM law_progress WHERE user_id=42 AND law_id='fire'")); got != 55 {
		t.Fatalf("comprehension=%d, want 55", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT insights FROM law_progress WHERE user_id=42 AND law_id='fire'")); got != 6 {
		t.Fatalf("insights=%d, want the 6 already there: a GM setting the number did not ask to move the count", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT comprehension FROM law_progress WHERE user_id=42 AND law_id='fire'")); got != 20 {
		t.Fatalf("comprehension=%d after undo, want 20", got)
	}
	if _, err := applyAdminRaw(t, path, "admin.player.set_law", 0, map[string]any{"user_id": 42, "law_id": "fire", "comprehension": 101}); err == nil {
		t.Fatal("comprehension 101 was accepted; law.comprehend clamps every gain to 100")
	}
	if _, err := applyAdminRaw(t, path, "admin.player.set_law", 0, map[string]any{"user_id": 42, "law_id": "not_a_law", "comprehension": 1}); err == nil || !strings.Contains(err.Error(), "law_id must be one of") {
		t.Fatalf("a Law the catalogue does not carry: %v", err)
	}
}

func TestTheContributionLeverSetsTheBalanceAndPromotesNobody(t *testing.T) {
	path := setupProgressDB(t)
	applyAdmin(t, path, "admin.player.set_sect_contribution", map[string]any{"user_id": 42, "contribution_points": 500, "contribution_earned": 9000, "reason": "fault"})
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_points FROM sect_membership WHERE user_id=42")); got != 500 {
		t.Fatalf("contribution_points=%d, want 500", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=42")); got != 9000 {
		t.Fatalf("contribution_earned=%d, want 9000", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT rank_level FROM sect_membership WHERE user_id=42")); got != 10 {
		t.Fatalf("rank_level=%d: the lever promoted somebody, and the rank is the sect card's to set", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_points FROM sect_membership WHERE user_id=42")); got != 30 {
		t.Fatalf("contribution_points=%d after undo, want 30", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=42")); got != 120 {
		t.Fatalf("contribution_earned=%d after undo, want 120", got)
	}
	// Leaving the count blank leaves it alone.
	applyAdmin(t, path, "admin.player.set_sect_contribution", map[string]any{"user_id": 42, "contribution_points": 5, "reason": "fault"})
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=42")); got != 120 {
		t.Fatalf("contribution_earned=%d, want 120 untouched", got)
	}
	if _, err := storageExec(path, `DELETE FROM sect_membership WHERE user_id=42`); err != nil {
		t.Fatal(err)
	}
	if _, err := applyAdminRaw(t, path, "admin.player.set_sect_contribution", 0, map[string]any{"user_id": 42, "contribution_points": 5}); err == nil || !strings.Contains(err.Error(), "not in a recorded sect") {
		t.Fatalf("a player in no sect: %v", err)
	}
}

func storageExec(path, sql string) (any, error) {
	conn, err := storage.Open(path)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	return nil, conn.ExecScript(sql)
}

// The root lever's mutation is read by its catalogue id, so a typed name -
// the card's own placeholder used to suggest one - reached no rule and was
// stored anyway (v1.23.0). What is already stored stays saveable.
func TestTheRootLeverTakesAMutationTheCatalogueCarries(t *testing.T) {
	path := setupAdminDB(t)
	if _, err := applyAdminRaw(t, path, "admin.player.set_spiritual_root", 0, map[string]any{"user_id": 42, "grade": "Heaven", "purity": 80, "mutation": "Phoenix Blood"}); err == nil || !strings.Contains(err.Error(), "mutation must be empty or one of") {
		t.Fatalf("a mutation the catalogue does not carry: %v", err)
	}
	applyAdmin(t, path, "admin.player.set_spiritual_root", map[string]any{"user_id": 42, "grade": "Heaven", "purity": 80, "mutation": "heavenly_flame", "reason": "story"})
	if _, err := storageExec(path, `UPDATE character_spiritual_roots SET mutation='Old Freeform' WHERE user_id=42`); err != nil {
		t.Fatal(err)
	}
	if _, err := applyAdminRaw(t, path, "admin.player.set_spiritual_root", 0, map[string]any{"user_id": 42, "grade": "Heaven", "purity": 60, "mutation": "Old Freeform"}); err != nil {
		t.Fatalf("saving the card unchanged was refused over a value already stored: %v", err)
	}
}

// --- what an undo takes back (admin_undo_rows.go) ---
//
// Each of the three levers wrote a pair of numbers on a row play was already
// writing to, and their undo restored the whole snapshot - the column the lever
// left alone included. Every test lets real play happen between the lever and
// the undo, because an undo driven straight after its lever cannot tell the two
// apart.

// A trade the character never had, set by the lever and crafted on: the undo
// takes the rank back and leaves the row, because the successes an examination
// reads are the craft's.
func TestAnUndoKeepsWhatACraftEarnedOnTheLeversRow(t *testing.T) {
	// The craft is a roll; the dice are lent so the successes the undo must
	// leave alone are there to be left.
	defer gamerng.UseRoller(highDice)()
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	batch4Exec(t, path, perfectionAuditDDL)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "DELETE FROM profession_progress WHERE user_id=42")
	applyAdmin(t, path, "admin.player.set_profession", map[string]any{"user_id": 42, "profession": "Alchemy", "level": 1, "xp": 0, "reason": "fault"})
	batch4TeachRecipe(t, path, 42, "Recovery Pill")
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',4),(42,'beast_core',2)`)
	if _, err := craftBatchApply(t, path, world, "undo-craft", map[string]any{"recipe": "Recovery Pill", "quantity": 2}); err != nil {
		t.Fatal(err)
	}
	if a := lastAuditAction(t, path); a != "admin.player.set_profession" {
		t.Fatalf("the last audit row is %q; a craft must not write one", a)
	}
	crafted := storage.ParseInt(actionScalar(t, path, "SELECT successes FROM profession_progress WHERE user_id=42 AND profession='Alchemy'"))
	if crafted < 1 {
		t.Fatalf("successes=%d after two crafts by a giant; the fixture no longer lets a craft land", crafted)
	}
	undoLast(t, path)
	row := actionScalar(t, path, "SELECT level||':'||xp||':'||successes FROM profession_progress WHERE user_id=42 AND profession='Alchemy'")
	if row == nil {
		t.Fatalf("the undo deleted the row the player crafted on: the %d success(es) an examination reads went with it", crafted)
	}
	if want := fmt.Sprintf("0:0:%d", crafted); row != want {
		t.Fatalf("row=%v after undo, want %s: the rank the lever set goes back and the successes stay", row, want)
	}
	// A redo is the forward upsert, and lands on the row that is there.
	undoLast(t, path)
	if got := storage.ParseInt(actionScalar(t, path, "SELECT level FROM profession_progress WHERE user_id=42 AND profession='Alchemy'")); got != 1 {
		t.Fatalf("level=%d after the redo, want the 1 the lever set", got)
	}
}

// An undo is an UPDATE: it does not bring back a trade a samsara has since wiped.
func TestAnUndoOfATradeDoesNotBringBackARowAResetRemoved(t *testing.T) {
	path := setupProgressDB(t)
	if _, err := storageExec(path, `INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,'Forging',2,10,0)`); err != nil {
		t.Fatal(err)
	}
	applyAdmin(t, path, "admin.player.set_profession", map[string]any{"user_id": 42, "profession": "Forging", "level": 5, "xp": 3, "reason": "fault"})
	if _, err := storageExec(path, `DELETE FROM profession_progress WHERE user_id=42`); err != nil {
		t.Fatal(err)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM profession_progress WHERE user_id=42")); got != 0 {
		t.Fatalf("%d row(s) after the undo: it brought back a trade the life had wiped", got)
	}
}

func TestAnUndoOfALawLeavesTheSittingsTheGMLeftAlone(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Exec(t, path, "UPDATE characters SET realm_index=8,phase=3 WHERE user_id=42")
	batch4Exec(t, path, "INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(42,'fire',20,6,0)")
	sitting := func(law string, seq int) {
		t.Helper()
		batch4Exec(t, path, "DELETE FROM cooldowns WHERE user_id=42")
		batch4Apply(t, path, world, "law.comprehend", seq, map[string]any{"law": law, "game_minute": 200})
	}
	read := func(law string) string {
		return fmt.Sprint(actionScalar(t, path, "SELECT comprehension||':'||insights FROM law_progress WHERE user_id=42 AND law_id='"+law+"'"))
	}

	// The count of sittings was never the lever's: a GM who gave only the
	// comprehension left it, and a sitting taken since stays taken.
	applyAdmin(t, path, "admin.player.set_law", map[string]any{"user_id": 42, "law_id": "fire", "comprehension": 55, "reason": "fault"})
	sitting("fire", 1)
	undoLast(t, path)
	if got := read("fire"); !strings.HasSuffix(got, ":7") || !strings.HasPrefix(got, "20:") {
		t.Fatalf("fire is %s after the undo, want comprehension 20 and the 7 sittings play made (the undo erased the sitting)", got)
	}
	redo := undoLast(t, path)
	if got := read("fire"); !strings.HasPrefix(got, "55:") || !strings.HasSuffix(got, ":7") {
		t.Fatalf("fire is %s after the redo (%v), want comprehension 55 and the same 7 sittings", got, redo)
	}

	// A Law the character never sat with: the undo takes the comprehension and
	// keeps the row a sitting has since been logged on.
	applyAdmin(t, path, "admin.player.set_law", map[string]any{"user_id": 42, "law_id": "water", "comprehension": 30, "reason": "fault"})
	sitting("water", 2)
	undoLast(t, path)
	if got := read("water"); got != "0:1" {
		t.Fatalf("water is %s after the undo, want 0:1 - the row the player sat on is kept with its one sitting", got)
	}

	// Untouched by play, the row the lever made goes with its undo.
	applyAdmin(t, path, "admin.player.set_law", map[string]any{"user_id": 42, "law_id": "sword", "comprehension": 30, "insights": 4, "reason": "fault"})
	undoLast(t, path)
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM law_progress WHERE user_id=42 AND law_id='sword'")); got != 0 {
		t.Fatalf("%d row(s) for a Law nobody sat with after its lever was undone", got)
	}
}

// The balance is the lever's; the lifetime count promotion reads is play's.
func TestAnUndoOfAContributionLeavesTheCountTheGMLeftAlone(t *testing.T) {
	// The donation's points are sect-value arithmetic, and the dice are lent
	// anyway so nothing on the sect path can answer zero by chance.
	defer gamerng.UseRoller(highDice)()
	path := filepath.Join(t.TempDir(), "contribution.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(sectExchangeSchema + perfectionAuditDDL); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_sect_contribution", map[string]any{"user_id": 1, "contribution_points": 5, "reason": "fault"})
	// A real donation, through the production door into creditSectContributionTx.
	play, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := addInventoryTx(play, 1, map[string]int64{"qi_pill": 1}); err != nil {
		play.Close()
		t.Fatal(err)
	}
	out, err := sectOp(t, play, "sect.contribute", 1, map[string]any{"item_id": "qi_pill", "quantity": 1})
	if err != nil {
		play.Close()
		t.Fatal(err)
	}
	if err := play.Commit(); err != nil {
		play.Close()
		t.Fatal(err)
	}
	play.Close()
	earned := i64(out["points"])
	if earned < 1 {
		t.Fatalf("a donation earned %d points; the fixture no longer pays one", earned)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=1")); got != earned {
		t.Fatalf("contribution_earned=%d after the donation, want %d", got, earned)
	}

	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_points FROM sect_membership WHERE user_id=1")); got != 0 {
		t.Fatalf("contribution_points=%d after undo, want the 0 the lever replaced", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=1")); got != earned {
		t.Fatalf("contribution_earned=%d after undo, want the %d play earned: the lever left the count alone and the undo erased the points", got, earned)
	}
}

// The lever on a count the GM did set: both sides differ, so the undo restores it.
func TestAnUndoOfAContributionRestoresACountTheGMSet(t *testing.T) {
	path := setupProgressDB(t)
	applyAdmin(t, path, "admin.player.set_sect_contribution", map[string]any{"user_id": 42, "contribution_points": 500, "contribution_earned": 9000, "reason": "fault"})
	if _, err := storageExec(path, `UPDATE sect_membership SET contribution_points=contribution_points+10,contribution_earned=contribution_earned+10 WHERE user_id=42`); err != nil {
		t.Fatal(err)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=42")); got != 120 {
		t.Fatalf("contribution_earned=%d after undo, want the 120 the lever replaced", got)
	}
	redo := undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=42")); got != 9000 {
		t.Fatalf("contribution_earned=%d after redo (%v), want 9000", got, redo)
	}
}
