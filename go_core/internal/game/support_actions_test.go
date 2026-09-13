package game

// `support.vote_claim` is the one gameplay grant nobody can verify: a vote on a
// server listing happens on someone else's website, and this deployment
// publishes no inbound endpoint for the listing site to call back to. The
// engine's job is therefore the half it *can* own - the cadence, the size and
// the receipt - and these tests are what hold that half honest. A claim that
// could be repeated at will would be an economy exploit rather than a
// thank-you, so the cooldown is the property under test.

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupSupportVoteDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4SetCanonicalGameMinute(t, path, 5000)
	return path
}

func supportVoteClaim(t *testing.T, path, actionID string, site string) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"site": site})
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   actionID,
		Operation:  "support.vote_claim",
		ActorID:    42,
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

func supportVoteStatus(t *testing.T, path string) map[string]any {
	t.Helper()
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: authoritativeAPIVersion,
		Operation:  "support.vote_status",
		ActorID:    42,
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

func TestASupportVoteClaimPaysTheLocalCurrencyAndSetsTheTwelveHourWait(t *testing.T) {
	path := setupSupportVoteDB(t)

	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}

	// The fixture cultivator stands in Greenriver Town, which is the Mortal
	// World, so the gift arrives in that world's low-grade stone rather than
	// in a currency they cannot spend where they are.
	if got := result["currency"]; got != "low_spirit_stone" {
		t.Fatalf("currency = %v, want low_spirit_stone", got)
	}
	if got := i64(result["amount"]); got != supportVoteReward {
		t.Fatalf("amount = %d, want %d", got, supportVoteReward)
	}
	if got := result["site"]; got != "Top.gg" {
		t.Fatalf("site = %v, want Top.gg", got)
	}
	if got := i64(result["next_claim_seconds"]); got != supportVoteCooldownSeconds {
		t.Fatalf("next_claim_seconds = %d, want %d", got, supportVoteCooldownSeconds)
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != supportVoteReward {
		t.Fatalf("wallet balance = %d, want %d", balance, supportVoteReward)
	}
	if i64(result["balance"]) != balance {
		t.Fatalf("reported balance %d does not match the wallet %d", i64(result["balance"]), balance)
	}
	if rows := supportScalar(t, path, `SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='support_vote'`); rows != 1 {
		t.Fatalf("cooldown rows = %d, want 1", rows)
	}
}

func TestASecondSupportVoteClaimInsideTwelveHoursIsRefusedAndPaysNothing(t *testing.T) {
	path := setupSupportVoteDB(t)
	if _, err := supportVoteClaim(t, path, "vote-1", "Top.gg"); err != nil {
		t.Fatal(err)
	}

	_, err := supportVoteClaim(t, path, "vote-2", "Top.gg")
	if err == nil {
		t.Fatal("a second claim inside the window succeeded")
	}
	if !strings.Contains(err.Error(), "cooldown active") {
		t.Fatalf("error = %v, want a cooldown refusal", err)
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != supportVoteReward {
		t.Fatalf("wallet balance = %d after a refused claim, want %d", balance, supportVoteReward)
	}
}

func TestASupportVoteClaimIsPayableAgainOnceTheWaitHasPassed(t *testing.T) {
	path := setupSupportVoteDB(t)
	if _, err := supportVoteClaim(t, path, "vote-1", "Top.gg"); err != nil {
		t.Fatal(err)
	}
	// Twelve hours later, to the second: the row is the whole gate, so ageing
	// it is the same thing as waiting.
	batch4Exec(t, path, `UPDATE cooldowns SET available_at=0 WHERE user_id=42 AND action='support_vote'`)

	if _, err := supportVoteClaim(t, path, "vote-2", "Top.gg"); err != nil {
		t.Fatal(err)
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != 2*supportVoteReward {
		t.Fatalf("wallet balance = %d after two claims, want %d", balance, 2*supportVoteReward)
	}
}

func TestAReplayedSupportVoteActionIDPaysOnce(t *testing.T) {
	path := setupSupportVoteDB(t)
	if _, err := supportVoteClaim(t, path, "vote-1", "Top.gg"); err != nil {
		t.Fatal(err)
	}

	// The same action_id again is a retry, not a second claim: it must come
	// back as the stored receipt rather than as the cooldown refusal a fresh
	// claim would get, and it must not pay twice.
	result, err := supportVoteClaim(t, path, "vote-1", "Top.gg")
	if err != nil {
		t.Fatal(err)
	}
	if i64(result["amount"]) != supportVoteReward {
		t.Fatalf("replayed amount = %d, want %d", i64(result["amount"]), supportVoteReward)
	}
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != supportVoteReward {
		t.Fatalf("wallet balance = %d after a replay, want %d", balance, supportVoteReward)
	}
}

func TestSupportVoteStatusReportsTheWaitAndGrantsNothing(t *testing.T) {
	path := setupSupportVoteDB(t)

	before := supportVoteStatus(t, path)
	if claimable, _ := before["claimable"].(bool); !claimable {
		t.Fatal("a cultivator who has never claimed is not claimable")
	}
	if got := i64(before["remaining_seconds"]); got != 0 {
		t.Fatalf("remaining_seconds = %d before any claim, want 0", got)
	}

	if _, err := supportVoteClaim(t, path, "vote-1", "Top.gg"); err != nil {
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
	balance := supportScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if balance != supportVoteReward {
		t.Fatalf("wallet balance = %d after two status reads, want %d", balance, supportVoteReward)
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
