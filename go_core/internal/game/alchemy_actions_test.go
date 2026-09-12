package game

// v0.23.0 regression tests for `alchemy.purge`.
//
// The property under test is the one the Python command could not hold: a
// purge is one transaction. Before this, `/alchemy purge` spent Qi, reduced
// toxicity, rewrote the shared effect row and set the cooldown as four
// separate round trips, so an interruption between any two of them left the
// player having paid for something they did not get, or having got something
// they did not pay for. The tests assert the whole cycle lands together or not
// at all, and that a retried action_id does not purge twice.

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func setupAlchemyPurgeDB(t *testing.T, toxicity int64) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4SetCanonicalGameMinute(t, path, 5000)
	if toxicity >= 0 {
		batch4Exec(t, path,
			`INSERT INTO alchemy_state(user_id,pill_toxicity,last_toxicity_game_minute,updated_at) VALUES(42,?,5000,0)`,
			toxicity)
	}
	return path
}

func alchemyPurgeApply(t *testing.T, path string, actor int64, actionID string) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	// The purge reads the catalogue since v1.0.0-rc.7: its qi cost is scaled
	// into the cultivator's own pool.
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   actionID,
		Operation:  "alchemy.purge",
		ActorID:    actor,
		Payload:    raw,
	})
	if err != nil {
		return nil, err
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("alchemy.purge result type %T", out.Result)
	}
	return result, nil
}

func alchemyScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}

// The fixture character has will=100 and spirit=100, so the purge amount is
// 8 + 50 + 33 = 91 - more than any toxicity a test sets, meaning `purged`
// is normally the whole load. Tests that want a partial purge say so.
func TestAPurgeSpendsQiReducesToxicityAndSetsTheCooldownTogether(t *testing.T) {
	path := setupAlchemyPurgeDB(t, 64)
	qiBefore := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`)

	result, err := alchemyPurgeApply(t, path, 42, "purge-1")
	if err != nil {
		t.Fatal(err)
	}

	// min(12, max(4, 64/8)) = 8 of the old fourteen-point pool, scaled into
	// the cultivator's own dantian and priced by their purity (v1.0.0-rc.7).
	capacity := storage.ParseInt(result["qi_max"])
	wantCost := scaledQiCost(8, capacity, referenceQiPool(100), qiBody{Purity: purityStart, MeridiansOpen: meridianStartOpen, DantianState: "intact"})
	if got := storage.ParseInt(result["qi_cost"]); got != wantCost {
		t.Fatalf("qi_cost=%d, want %d (8 base against a pool of %d)", got, wantCost, capacity)
	}
	_ = qiBefore
	if got := storage.ParseInt(result["purged"]); got != 64 {
		t.Fatalf("purged=%d, want the whole 64", got)
	}
	// The fixture starts with a full pool, so what is left is the settled
	// capacity less the charge, and the row agrees with the receipt.
	if got := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`); got != storage.ParseInt(result["qi"]) {
		t.Fatalf("qi=%d, want the %v the receipt reports", got, result["qi"])
	}
	if got, want := storage.ParseInt(result["qi"]), capacity-wantCost; got != want {
		t.Fatalf("qi=%d, want %d", got, want)
	}
	if got := alchemyScalar(t, path, `SELECT pill_toxicity FROM alchemy_state WHERE user_id=42`); got != 0 {
		t.Fatalf("pill_toxicity=%d, want 0", got)
	}
	if got := alchemyScalar(t, path, `SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='alchemy_purge'`); got != 1 {
		t.Fatalf("cooldown rows=%d, want 1", got)
	}
	// Dropping under 40 must take the shared effect row with it.
	if got := alchemyScalar(t, path,
		`SELECT COUNT(*) FROM active_effects WHERE user_id=42 AND effect_key='pill_toxicity'`); got != 0 {
		t.Fatalf("effect rows=%d, want the effect gone", got)
	}
}

func TestAPurgeThatOnlyPartlyClearsLeavesTheEffectRewritten(t *testing.T) {
	// will=10, spirit=10 -> 8 + 5 + 3 = 16 purged from 90, leaving 74: still
	// over the 40 threshold, so the effect row must survive and be rewritten
	// to the milder band rather than left describing the old load.
	path := setupAlchemyPurgeDB(t, 90)
	batch4Exec(t, path,
		`UPDATE characters SET attributes_json='{"body":10,"agility":10,"spirit":10,"insight":10,"will":10,"presence":10}' WHERE user_id=42`)

	result, err := alchemyPurgeApply(t, path, 42, "purge-partial")
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["purged"]); got != 16 {
		t.Fatalf("purged=%d, want 16", got)
	}
	if got := storage.ParseInt(result["pill_toxicity"]); got != 74 {
		t.Fatalf("pill_toxicity=%d, want 74", got)
	}
	if got := alchemyScalar(t, path,
		`SELECT COUNT(*) FROM active_effects WHERE user_id=42 AND effect_key='pill_toxicity'`); got != 1 {
		t.Fatalf("effect rows=%d, want the effect still applied", got)
	}
	// 74 is the 60-79 band: cultivation x0.85, will -1. The row has to say so
	// rather than still carrying the 80+ penalties it had at 90.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(
		`SELECT effect_json FROM active_effects WHERE user_id=42 AND effect_key='pill_toxicity'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	effect := fmt.Sprint(firstRowMap(res)["effect_json"])
	expected, err := medicineToxicityEffectJSON(74)
	if err != nil {
		t.Fatal(err)
	}
	if effect != expected {
		t.Fatalf("effect row was not rewritten for the new toxicity:\n got %s\nwant %s", effect, expected)
	}
}

func TestAPurgeWithoutEnoughQiChangesNothingAtAll(t *testing.T) {
	// This is the atomicity property in its plainest form. The old sequence
	// could spend nothing, reduce nothing, and still set an hour's cooldown -
	// or the reverse - because each write stood alone.
	path := setupAlchemyPurgeDB(t, 64)
	batch4Exec(t, path, `UPDATE characters SET qi=3 WHERE user_id=42`)

	if _, err := alchemyPurgeApply(t, path, 42, "purge-poor"); err == nil {
		t.Fatal("purge succeeded on 3 Qi against a cost of 8")
	}
	if got := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`); got != 3 {
		t.Fatalf("qi=%d, want the 3 they started with", got)
	}
	if got := alchemyScalar(t, path, `SELECT pill_toxicity FROM alchemy_state WHERE user_id=42`); got != 64 {
		t.Fatalf("pill_toxicity=%d, want it untouched at 64", got)
	}
	if got := alchemyScalar(t, path, `SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='alchemy_purge'`); got != 0 {
		t.Fatalf("cooldown rows=%d; a failed purge must not start the timer", got)
	}
	if got := alchemyScalar(t, path, `SELECT COUNT(*) FROM domain_events WHERE actor_id=42`); got != 0 {
		t.Fatalf("domain_events=%d; a failed purge is not an event", got)
	}
}

func TestTheCooldownBlocksASecondPurge(t *testing.T) {
	path := setupAlchemyPurgeDB(t, 64)
	if _, err := alchemyPurgeApply(t, path, 42, "purge-a"); err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, `UPDATE alchemy_state SET pill_toxicity=50 WHERE user_id=42`)
	qiAfterFirst := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`)

	if _, err := alchemyPurgeApply(t, path, 42, "purge-b"); err == nil {
		t.Fatal("a second purge ran inside the cooldown")
	}
	if got := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`); got != qiAfterFirst {
		t.Fatalf("qi=%d, want %d - the blocked purge still charged", got, qiAfterFirst)
	}
	if got := alchemyScalar(t, path, `SELECT pill_toxicity FROM alchemy_state WHERE user_id=42`); got != 50 {
		t.Fatalf("pill_toxicity=%d, want 50", got)
	}
}

func TestPurgingNothingIsRefusedRatherThanCharged(t *testing.T) {
	path := setupAlchemyPurgeDB(t, 0)
	if _, err := alchemyPurgeApply(t, path, 42, "purge-clean"); err == nil {
		t.Fatal("purged a character with no toxicity")
	}
	if got := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`); got != 100 {
		t.Fatalf("qi=%d; a refused purge charged the player", got)
	}
	if got := alchemyScalar(t, path, `SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='alchemy_purge'`); got != 0 {
		t.Fatalf("cooldown rows=%d after a refused purge", got)
	}
}

func TestARetriedPurgeReplaysInsteadOfPurgingTwice(t *testing.T) {
	// The receipt is what the four-write Python sequence could never offer: a
	// player whose client retries gets the original answer, not a second
	// charge. Without it the retry would be refused by the cooldown anyway -
	// which is a different, worse answer than the one they already earned.
	path := setupAlchemyPurgeDB(t, 64)
	first, err := alchemyPurgeApply(t, path, 42, "purge-retry")
	if err != nil {
		t.Fatal(err)
	}
	qiAfterFirst := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`)

	second, err := alchemyPurgeApply(t, path, 42, "purge-retry")
	if err != nil {
		t.Fatalf("the retry failed instead of replaying: %v", err)
	}
	if fmt.Sprint(first) != fmt.Sprint(second) {
		t.Fatalf("replay differs:\n %v\nvs\n %v", second, first)
	}
	if got := alchemyScalar(t, path, `SELECT qi FROM characters WHERE user_id=42`); got != qiAfterFirst {
		t.Fatalf("qi=%d, want %d - the retry charged again", got, qiAfterFirst)
	}
	if got := alchemyScalar(t, path, `SELECT COUNT(*) FROM domain_events WHERE actor_id=42 AND event_type='pill_toxicity_purged'`); got != 1 {
		t.Fatalf("domain_events=%d, want exactly 1", got)
	}
}

func TestThePurgeFormulasMatchTheCommandTheyReplaced(t *testing.T) {
	// min(12, max(4, toxicity//8)) and min(toxicity, 8 + will//2 + spirit//3),
	// transcribed from app/bot/commands/exploration.py:alchemy_purge as it
	// stood at v0.22.5. A migration that quietly rebalances is a bug report
	// from a player, not a refactor.
	for _, tc := range []struct{ toxicity, want int64 }{
		{1, 4}, {31, 4}, {32, 4}, {40, 5}, {64, 8}, {96, 12}, {100, 12},
	} {
		if got := alchemyPurgeQiCost(tc.toxicity); got != tc.want {
			t.Fatalf("qi cost at toxicity %d = %d, want %d", tc.toxicity, got, tc.want)
		}
	}
	for _, tc := range []struct{ toxicity, will, spirit, want int64 }{
		{100, 0, 0, 8},
		{100, 10, 10, 16},
		{100, 100, 100, 91},
		{5, 100, 100, 5}, // never more than what is there
	} {
		if got := alchemyPurgeAmount(tc.toxicity, tc.will, tc.spirit); got != tc.want {
			t.Fatalf("purge(%d,%d,%d)=%d, want %d", tc.toxicity, tc.will, tc.spirit, got, tc.want)
		}
	}
}

// The other half of "the engine owns the toxicity curve": the penalty has to
// stop applying when the clock has decayed it, whether or not the player did
// anything. Until v0.23.0 the row that carries the penalty was only rewritten
// by a Python sync that ran from `/alchemy status`, so waiting out a heavy
// dose did nothing until the player thought to open a screen.
func TestADecayedToxicityStopsPenalisingWithoutThePlayerActing(t *testing.T) {
	path := setupAlchemyPurgeDB(t, 90)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	// The dose lands at minute 5000 and writes its effect row: 80+ is the
	// worst band, will -2.
	if _, err := settlePillToxicityEffectTx(conn, 42, 5000); err != nil {
		t.Fatal(err)
	}
	catalog := worlddata.Catalog{}
	before, err := canonicalAdditiveEffectBonus(conn, catalog, 42, "", 5000, "will")
	if err != nil {
		t.Fatal(err)
	}
	if before != -2 {
		t.Fatalf("will bonus at toxicity 90 = %d, want -2", before)
	}

	// 51 decay steps of 12 game-hours takes 90 down to 39 - under the
	// threshold, so the effect must be gone the moment anything reads it.
	later := int64(5000 + 51*pillToxicityDecayMinutes)
	after, err := canonicalAdditiveEffectBonus(conn, catalog, 42, "", later, "will")
	if err != nil {
		t.Fatal(err)
	}
	if after != 0 {
		t.Fatalf("will bonus after decay = %d, want 0 - the stale penalty is still applied", after)
	}
	res, err := conn.Execute(
		`SELECT COUNT(*) FROM active_effects WHERE user_id=42 AND effect_key='pill_toxicity'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(res.Rows[0][0]); got != 0 {
		t.Fatalf("effect rows=%d, want the decayed effect cleared", got)
	}
}

// settleDueToxicityTx must stay silent for anyone who has never touched
// alchemy: it runs ahead of every effect read, so conjuring a row there would
// give every character in the game an alchemy_state they never earned.
func TestSettlingToxicityDoesNothingForACharacterWithNoAlchemyState(t *testing.T) {
	path := setupAlchemyPurgeDB(t, -1) // no alchemy_state row at all
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := settleDueToxicityTx(conn, 42, 9000); err != nil {
		t.Fatal(err)
	}
	res, err := conn.Execute(`SELECT COUNT(*) FROM alchemy_state`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(res.Rows[0][0]); got != 0 {
		t.Fatalf("alchemy_state rows=%d, want none created", got)
	}
}

// And it must tolerate a database that has no alchemy tables yet rather than
// failing every action on it.
func TestSettlingToxicityToleratesAMissingAlchemyTable(t *testing.T) {
	path := setupBatch4AuthorityDB(t) // no alchemy_state table
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := settleDueToxicityTx(conn, 42, 9000); err != nil {
		t.Fatalf("settling failed on a database without alchemy tables: %v", err)
	}
}
