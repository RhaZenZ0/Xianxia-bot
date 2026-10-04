package game

import (
	"strings"
	"testing"

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
