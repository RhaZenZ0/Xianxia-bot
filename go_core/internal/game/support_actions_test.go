package game

// `support.vote_claim` is the one gameplay grant nobody can verify: a vote on a
// server listing happens on someone else's website, and this deployment
// publishes no inbound endpoint for the listing site to call back to. The
// engine's job is therefore the half it *can* own - the cadence, the size and
// the receipt - and these tests are what hold that half honest.
//
// Two properties carry most of the weight. A claim that could be repeated at
// will would be an economy exploit rather than a thank-you, so the cooldown is
// pinned. And the command prints what the gift will be *before* the player
// presses, so the status read and the claim must agree field for field - a
// promise and a different payment is the one lie this pair can tell.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

func setupSupportVoteDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	// The base fixture predates the money column the sheet reads; every test
	// that pays a cultivator adds it the same way (batch5_authority_test.go:22).
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`ALTER TABLE characters ADD COLUMN spirit_stones INTEGER NOT NULL DEFAULT 0;`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	batch4SetCanonicalGameMinute(t, path, 5000)
	return path
}

func supportVoteClaimAs(t *testing.T, path string, actor int64, actionID string, site string) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"site": site})
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   actionID,
		Operation:  "support.vote_claim",
		ActorID:    actor,
		Payload:    raw,
	})
	if err != nil {
		return nil, err
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("support.vote_claim result type %T", out.Result)
	}
	return result, nil
}

func supportVoteClaim(t *testing.T, path, actionID string, site string) (map[string]any, error) {
	t.Helper()
	return supportVoteClaimAs(t, path, 42, actionID, site)
}

func supportVoteStatusAs(t *testing.T, path string, actor int64) map[string]any {
	t.Helper()
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: authoritativeAPIVersion,
		Operation:  "support.vote_status",
		ActorID:    actor,
		Payload:    json.RawMessage(`{}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("support.vote_status result type %T", out.Result)
	}
	return result
}

func supportVoteStatus(t *testing.T, path string) map[string]any {
	t.Helper()
	return supportVoteStatusAs(t, path, 42)
}

func supportScalar(t *testing.T, path, sql string, args ...any) int64 {
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

// expectedGift is the ladder as the design states it, recomputed here rather
// than read off the constants, so a change to the formula has to be made
// twice - once in the engine and once as a deliberate edit here.
func expectedGift(depth, multiplier int64) int64 { return (10 + depth*5) * multiplier }

func TestASupportVoteClaimPaysTheLocalCurrencyAndSetsTheTwelveHourWait(t *testing.T) {
	path := setupSupportVoteDB(t)

	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}

	// The fixture cultivator stands in Greenriver Town, which is the Mortal
	// World, so the gift arrives in that world's low-grade stone rather than
	// in a currency they cannot spend where they are. Realm 0 is the floor of
	// that world, so the depth term is zero.
	if got := result["currency"]; got != "low_spirit_stone" {
		t.Fatalf("currency = %v, want low_spirit_stone", got)
	}
	if got := i64(result["depth"]); got != 0 {
		t.Fatalf("depth = %d at the first realm of the Mortal World, want 0", got)
	}
	multiplier := i64(result["multiplier"])
	if multiplier != 1 && multiplier != 2 {
		t.Fatalf("multiplier = %d, want 1 on a weekday or 2 on a bonus weekend", multiplier)
	}
	if got, want := i64(result["amount"]), expectedGift(0, multiplier); got != want {
		t.Fatalf("amount = %d, want %d", got, want)
	}
	if got := result["site"]; got != "Top.gg" {
		t.Fatalf("site = %v, want Top.gg", got)
	}
	if got := i64(result["next_claim_seconds"]); got != supportVoteCooldownSeconds {
		t.Fatalf("next_claim_seconds = %d, want %d", got, supportVoteCooldownSeconds)
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != i64(result["amount"]) {
		t.Fatalf("wallet balance = %d, want the amount paid %d", balance, i64(result["amount"]))
	}
	if i64(result["balance"]) != balance {
		t.Fatalf("reported balance %d does not match the wallet %d", i64(result["balance"]), balance)
	}
	if rows := supportScalar(t, path, `SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='support_vote'`); rows != 1 {
		t.Fatalf("cooldown rows = %d, want 1", rows)
	}
}

// The bug this pins is not the scaling: walletDeltaTx mirrors low_spirit_stone
// into characters.spirit_stones, and the raw upsert this replaced did not, so
// every Mortal-World claim left the character sheet stale against the wallet.
func TestASupportVoteClaimMirrorsLowSpiritStonesOntoTheCharacterSheet(t *testing.T) {
	path := setupSupportVoteDB(t)
	before := supportScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`)

	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}

	wallet := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	sheet := supportScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`)
	if sheet != wallet {
		t.Fatalf("character sheet %d and wallet %d disagree after a claim", sheet, wallet)
	}
	if sheet == before {
		t.Fatalf("the sheet did not move: %d before, %d after a gift of %d", before, sheet, i64(result["amount"]))
	}
}

func TestTheGiftGrowsWithTheRealmsClimbedInsideThisWorld(t *testing.T) {
	path := setupSupportVoteDB(t)

	// Actor 43 is one realm further up the same world than actor 42.
	shallow, err := supportVoteClaim(t, path, "vote-shallow", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	deeper, err := supportVoteClaimAs(t, path, 43, "vote-deeper", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}

	if i64(deeper["depth"]) != 1 {
		t.Fatalf("depth = %d at realm 1 of the Mortal World, want 1", i64(deeper["depth"]))
	}
	if i64(deeper["amount"]) <= i64(shallow["amount"]) {
		t.Fatalf("a deeper cultivator was given %d, no more than the shallower one's %d",
			i64(deeper["amount"]), i64(shallow["amount"]))
	}
	if got, want := i64(deeper["amount"]), expectedGift(1, i64(deeper["multiplier"])); got != want {
		t.Fatalf("amount at depth 1 = %d, want %d", got, want)
	}
}

func TestTheGiftArrivesAsTheMaterialTheCultivatorsPathUses(t *testing.T) {
	path := setupSupportVoteDB(t)

	// Actor 42 walks the Sword Cultivator's road, which content maps to "@ore";
	// in the Mortal World that resolves to spirit iron.
	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	if got := result["item_id"]; got != "spirit_iron" {
		t.Fatalf("item_id = %v for a Sword Cultivator in the Mortal World, want spirit_iron", got)
	}
	quantity := supportScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_iron'`)
	if quantity != i64(result["item_quantity"]) || quantity < 1 {
		t.Fatalf("inventory holds %d spirit iron, the receipt says %d", quantity, i64(result["item_quantity"]))
	}
}

func TestAPractisedCraftChoosesTheGiftOverThePath(t *testing.T) {
	path := setupSupportVoteDB(t)
	// A sword cultivator who has actually worked a cauldron is given medicine,
	// not ore: the craft a player practises says more about what they need
	// than the road they walk.
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,'Alchemy',3,40,0)`)

	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	if got := result["item_id"]; got != "spirit_herb" {
		t.Fatalf("item_id = %v for a practising alchemist, want spirit_herb", got)
	}
}

// A row exists from the first *attempt* at a craft, so "has a row" is not
// "practises a craft" - an untouched row must not redirect the gift.
func TestAnUntouchedProfessionRowDoesNotChooseTheGift(t *testing.T) {
	path := setupSupportVoteDB(t)
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,'Alchemy',0,0,0)`)

	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	if got := result["item_id"]; got != "spirit_iron" {
		t.Fatalf("item_id = %v with an untouched Alchemy row, want the path's spirit_iron", got)
	}
}

func TestASecondSupportVoteClaimInsideTwelveHoursIsRefusedAndPaysNothing(t *testing.T) {
	path := setupSupportVoteDB(t)
	first, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}

	_, err = supportVoteClaim(t, path, "vote-2", "Top.gg")
	if err == nil {
		t.Fatal("a second claim inside the window succeeded")
	}
	if !strings.Contains(err.Error(), "cooldown active") {
		t.Fatalf("error = %v, want a cooldown refusal", err)
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != i64(first["amount"]) {
		t.Fatalf("wallet balance = %d after a refused claim, want the one gift %d", balance, i64(first["amount"]))
	}
}

func TestASupportVoteClaimIsPayableAgainOnceTheWaitHasPassed(t *testing.T) {
	path := setupSupportVoteDB(t)
	first, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	// Twelve hours later, to the second: the row is the whole gate, so ageing
	// it is the same thing as waiting.
	batch4Exec(t, path, `UPDATE cooldowns SET available_at=0 WHERE user_id=42 AND action='support_vote'`)

	second, err := supportVoteClaim(t, path, "vote-2", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if want := i64(first["amount"]) + i64(second["amount"]); balance != want {
		t.Fatalf("wallet balance = %d after two claims, want %d", balance, want)
	}
}

func TestAReplayedSupportVoteActionIDPaysOnce(t *testing.T) {
	path := setupSupportVoteDB(t)
	first, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}

	// The same action_id again is a retry, not a second claim: it must come
	// back as the stored receipt rather than as the cooldown refusal a fresh
	// claim would get, and it must not pay twice.
	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	if i64(result["amount"]) != i64(first["amount"]) {
		t.Fatalf("replayed amount = %d, want the original %d", i64(result["amount"]), i64(first["amount"]))
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != i64(first["amount"]) {
		t.Fatalf("wallet balance = %d after a replay, want %d", balance, i64(first["amount"]))
	}
}

// The command prints the gift before the player presses the button. If the
// status read and the claim can differ, that message is a lie - so they are
// computed by one function and this is the test that says so.
func TestTheVoteStatusPromisesExactlyWhatTheClaimPays(t *testing.T) {
	path := setupSupportVoteDB(t)
	batch4Exec(t, path, `INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(42,'Forging',5,90,0)`)

	promised := supportVoteStatus(t, path)
	paid, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}

	for _, field := range []string{"amount", "currency", "world", "depth", "item_id", "item_quantity", "multiplier"} {
		if fmtAny(promised[field]) != fmtAny(paid[field]) {
			t.Fatalf("%s: promised %v, paid %v", field, promised[field], paid[field])
		}
	}
}

// fmtAny compares two result fields without caring whether the value came
// back as a string, a bool or a number.
func fmtAny(v any) string { return fmt.Sprint(v) }

func TestSupportVoteStatusReportsTheWaitAndGrantsNothing(t *testing.T) {
	path := setupSupportVoteDB(t)

	before := supportVoteStatus(t, path)
	if claimable, _ := before["claimable"].(bool); !claimable {
		t.Fatal("a cultivator who has never claimed is not claimable")
	}
	if got := i64(before["remaining_seconds"]); got != 0 {
		t.Fatalf("remaining_seconds = %d before any claim, want 0", got)
	}

	paid, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	after := supportVoteStatus(t, path)
	if claimable, _ := after["claimable"].(bool); claimable {
		t.Fatal("still claimable immediately after a claim")
	}
	remaining := i64(after["remaining_seconds"])
	if remaining <= 0 || remaining > supportVoteCooldownSeconds {
		t.Fatalf("remaining_seconds = %d, want 0 < n <= %d", remaining, supportVoteCooldownSeconds)
	}
	// A query pays nothing, however often it is asked.
	supportVoteStatus(t, path)
	supportVoteStatus(t, path)
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != i64(paid["amount"]) {
		t.Fatalf("wallet balance = %d after three status reads, want the one gift %d", balance, i64(paid["amount"]))
	}
}

func TestTheSiteLabelIsBoundedBecauseItComesFromTheEnvironment(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"Top.gg", "Top.gg"},
		{"  DISBOARD  ", "DISBOARD"},
		{"", "the server listing"},
		{"   ", "the server listing"},
		{"Top\n.gg\r", "Top.gg"},
		{strings.Repeat("a", 80), strings.Repeat("a", 40)},
	} {
		if got := supportSiteLabel(tc.in); got != tc.want {
			t.Fatalf("supportSiteLabel(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// ---------------------------------------------------------------------------
// The weekend
// ---------------------------------------------------------------------------

// The window is the operator's local Friday through Sunday, which is a
// different instant in June than in January. A fixed +02:00 offset would open
// the bonus an hour late for half the year, so the zone follows DST and these
// are the instants that prove it - one in CET, one in CEST, both asserted in
// UTC so the test states the real moment rather than restating the rule.
func TestTheWeekendFollowsTheOperatorsClockAcrossTheDaylightChange(t *testing.T) {
	if got := supportWeekendLocation().String(); got != supportWeekendZoneName {
		t.Fatalf("weekend zone = %q, want %q - the embedded timezone database is not being read", got, supportWeekendZoneName)
	}
	for _, tc := range []struct {
		name    string
		at      time.Time
		weekend bool
	}{
		// Winter, UTC+1: the window opens at 23:00 UTC on Thursday.
		{"Thursday 22:59 UTC in January is still the week", time.Date(2026, 1, 15, 22, 59, 0, 0, time.UTC), false},
		{"Thursday 23:00 UTC in January is local Friday", time.Date(2026, 1, 15, 23, 0, 0, 0, time.UTC), true},
		{"Sunday 22:59 UTC in January is local Sunday", time.Date(2026, 1, 18, 22, 59, 0, 0, time.UTC), true},
		{"Sunday 23:00 UTC in January is local Monday", time.Date(2026, 1, 18, 23, 0, 0, 0, time.UTC), false},
		// Summer, UTC+2: the same boundaries move an hour earlier in UTC.
		{"Thursday 21:59 UTC in June is still the week", time.Date(2026, 6, 11, 21, 59, 0, 0, time.UTC), false},
		{"Thursday 22:00 UTC in June is local Friday", time.Date(2026, 6, 11, 22, 0, 0, 0, time.UTC), true},
		{"Sunday 21:59 UTC in June is local Sunday", time.Date(2026, 6, 14, 21, 59, 0, 0, time.UTC), true},
		{"Sunday 22:00 UTC in June is local Monday", time.Date(2026, 6, 14, 22, 0, 0, 0, time.UTC), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			weekend, _, _ := supportWeekendWindow(tc.at)
			if weekend != tc.weekend {
				t.Fatalf("weekend = %v, want %v", weekend, tc.weekend)
			}
			multiplier := supportVoteWeekendMultiplier(tc.at)
			want := int64(1)
			if tc.weekend {
				want = supportVoteWeekendMult
			}
			if multiplier != want {
				t.Fatalf("multiplier = %d, want %d", multiplier, want)
			}
		})
	}
}

// The announcement worker posts once per window and must not repeat after a
// restart, so the key has to be the same for every instant inside one weekend
// and different for the next.
func TestTheWeekendKeyIsStableInsideAWindowAndNewForTheNextOne(t *testing.T) {
	friday := time.Date(2026, 6, 12, 6, 0, 0, 0, time.UTC)
	sunday := time.Date(2026, 6, 14, 20, 0, 0, 0, time.UTC)
	nextFriday := friday.AddDate(0, 0, 7)

	_, opensFriday, closesFriday := supportWeekendWindow(friday)
	_, opensSunday, closesSunday := supportWeekendWindow(sunday)
	_, opensNext, _ := supportWeekendWindow(nextFriday)

	if !opensFriday.Equal(opensSunday) {
		t.Fatalf("Friday and Sunday of one weekend opened different windows: %s vs %s", opensFriday, opensSunday)
	}
	if !closesFriday.Equal(closesSunday) {
		t.Fatalf("Friday and Sunday of one weekend close differently: %s vs %s", closesFriday, closesSunday)
	}
	if opensNext.Equal(opensFriday) {
		t.Fatalf("the following weekend reused the window that opened %s", opensFriday)
	}
	if got := closesFriday.Sub(opensFriday); got != 72*time.Hour {
		t.Fatalf("the window lasted %s, want 72h (Friday 00:00 to Monday 00:00)", got)
	}
	if opensFriday.Weekday() != time.Friday {
		t.Fatalf("the window opens on a %s, want Friday", opensFriday.Weekday())
	}
}

// Monday to Thursday the query still has to say when the bonus is coming, or
// the announcement worker has nothing to schedule against.
func TestAWeekdayNamesTheWeekendThatIsComing(t *testing.T) {
	monday := time.Date(2026, 6, 8, 12, 0, 0, 0, time.UTC)
	weekend, opens, closes := supportWeekendWindow(monday)
	if weekend {
		t.Fatal("a Monday was reported as a bonus weekend")
	}
	if opens.Weekday() != time.Friday || !opens.After(monday) {
		t.Fatalf("the next window opens %s (%s), want the Friday after %s", opens, opens.Weekday(), monday)
	}
	if closes.Sub(opens) != 72*time.Hour {
		t.Fatalf("the coming window lasts %s, want 72h", closes.Sub(opens))
	}
}

func TestTheWeekendQueryAnswersWithoutAnActorOrADatabase(t *testing.T) {
	path := setupSupportVoteDB(t)
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: authoritativeAPIVersion,
		Operation:  "support.weekend",
		ActorID:    0,
		Payload:    json.RawMessage(`{}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("support.weekend result type %T", out.Result)
	}
	weekend, _ := result["weekend"].(bool)
	multiplier := i64(result["multiplier"])
	if weekend && multiplier != supportVoteWeekendMult {
		t.Fatalf("multiplier = %d during a weekend, want %d", multiplier, supportVoteWeekendMult)
	}
	if !weekend && multiplier != 1 {
		t.Fatalf("multiplier = %d outside a weekend, want 1", multiplier)
	}
	if key, _ := result["window_key"].(string); len(key) != len("2006-01-02") {
		t.Fatalf("window_key = %q, want a date the announcement can compare", result["window_key"])
	}
	if i64(result["closes_unix"]) <= i64(result["opens_unix"]) {
		t.Fatalf("window closes %d before it opens %d", i64(result["closes_unix"]), i64(result["opens_unix"]))
	}
	if got := result["zone"]; got != supportWeekendZoneName {
		t.Fatalf("zone = %v, want %s", got, supportWeekendZoneName)
	}
}
