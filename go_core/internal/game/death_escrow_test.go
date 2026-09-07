package game

// v0.23.1 regression tests for the cross-incarnation economy exploit and the
// seclusion that outlived its body (external review findings #1 and #4).
//
// The exploit in one line: list a rare item on a long auction, die, reincarnate
// - inventory and wallets are wiped as incarnation-scoped state - and then let
// the auction close, delivering the item or its proceeds into the new body.
// Bids work the same way in reverse, because a bid escrows currency and a
// refund pays whoever holds the id when it fires.

import (
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func setupEscrowDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
ALTER TABLE characters ADD COLUMN spirit_stones INTEGER NOT NULL DEFAULT 0;
CREATE TABLE auctions(
	auction_id INTEGER PRIMARY KEY AUTOINCREMENT, house_id TEXT NOT NULL DEFAULT 'h',
	seller_user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
	currency_id TEXT NOT NULL DEFAULT 'low_spirit_stone', starting_bid INTEGER NOT NULL DEFAULT 10,
	current_bid INTEGER NOT NULL DEFAULT 0, current_bidder_user_id INTEGER,
	anonymous INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
	created_at REAL NOT NULL DEFAULT 0, ends_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE caravans(
	caravan_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL DEFAULT 'npc',
	owner_key TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
	cargo_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'traveling',
	risk INTEGER NOT NULL DEFAULT 10, depart_game_minute INTEGER NOT NULL DEFAULT 0,
	arrive_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE seclusion_sessions(
	user_id INTEGER PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'qi',
	started_game_minute INTEGER NOT NULL DEFAULT 0, ends_game_minute INTEGER NOT NULL DEFAULT 0,
	last_settled_game_minute INTEGER NOT NULL DEFAULT 0, environment_mult REAL NOT NULL DEFAULT 1,
	accumulated_gain INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
	ended_reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0
);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func escrowScalar(t *testing.T, path, sql string, args ...any) int64 {
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

// resolveEscrow runs the resolution the way true death does, then commits so
// another connection can read the result.
func resolveEscrow(t *testing.T, path string, userID int64) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	out, err := resolveIncarnationEscrowTx(conn, userID, 9000, 1234.5)
	if err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return out
}

func TestDyingEndsALiveListingAndRefundsItsBidder(t *testing.T) {
	// 42 is selling; 43 holds the high bid with money already escrowed. When
	// 42 dies the sale cannot complete, so 43 gets their stones back and the
	// goods go to the corpse rather than waiting to be delivered to whoever
	// holds user 42 next.
	path := setupEscrowDB(t)
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,active,ends_at)
		VALUES(1,42,'nine_yang_fragment',1,'low_spirit_stone',10,400,43,1,99999999999)`)

	out := resolveEscrow(t, path, 42)

	if got := storage.ParseInt(out["auctions_cancelled"]); got != 1 {
		t.Fatalf("auctions_cancelled=%d", got)
	}
	if got := escrowScalar(t, path, `SELECT active FROM auctions WHERE auction_id=1`); got != 0 {
		t.Fatalf("the lot is still live after the seller died")
	}
	if got := escrowScalar(t, path,
		`SELECT balance FROM currency_wallets WHERE user_id=43 AND currency_id='low_spirit_stone'`); got != 400 {
		t.Fatalf("bidder refund=%d, want 400 - their escrow was kept", got)
	}
	// The item leaves escrow into the dead character, whose inventory the
	// reincarnation wipe then clears. What matters is that it is no longer
	// waiting in `auctions` for a settlement that pays a future body.
	if got := escrowScalar(t, path,
		`SELECT quantity FROM inventory WHERE user_id=42 AND item_id='nine_yang_fragment'`); got != 1 {
		t.Fatalf("item quantity=%d; the goods stayed in escrow", got)
	}
}

func TestDyingReleasesTheDeadBiddersEscrowAndTheLot(t *testing.T) {
	// 42 is the high bidder on someone else's lot. They cannot collect, so the
	// escrow returns and the lot reverts to no bid - leaving a dead
	// cultivator's offer standing would price every other bidder out.
	path := setupEscrowDB(t)
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,active,ends_at)
		VALUES(2,43,'spirit_iron',1,'low_spirit_stone',10,250,42,1,99999999999)`)

	out := resolveEscrow(t, path, 42)

	if got := storage.ParseInt(out["bids_refunded"]); got != 1 {
		t.Fatalf("bids_refunded=%d", got)
	}
	if got := escrowScalar(t, path,
		`SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`); got != 250 {
		t.Fatalf("refund=%d, want 250", got)
	}
	if got := escrowScalar(t, path, `SELECT current_bid FROM auctions WHERE auction_id=2`); got != 0 {
		t.Fatalf("current_bid=%d; a dead cultivator's bid still sets the floor", got)
	}
	if got := escrowScalar(t, path,
		`SELECT COUNT(*) FROM auctions WHERE auction_id=2 AND current_bidder_user_id IS NOT NULL`); got != 0 {
		t.Fatalf("the dead bidder is still recorded as winning")
	}
	if got := escrowScalar(t, path, `SELECT active FROM auctions WHERE auction_id=2`); got != 1 {
		t.Fatalf("someone else's lot was cancelled by an unrelated death")
	}
}

func TestDyingLosesACaravanInsteadOfPayingTheNextIncarnation(t *testing.T) {
	path := setupEscrowDB(t)
	batch4Exec(t, path, `INSERT INTO caravans(caravan_id,owner_type,owner_key,status,arrive_game_minute) VALUES(1,'player','42','traveling',99999)`)
	batch4Exec(t, path, `INSERT INTO caravans(caravan_id,owner_type,owner_key,status,arrive_game_minute) VALUES(2,'player','43','traveling',99999)`)
	batch4Exec(t, path, `INSERT INTO caravans(caravan_id,owner_type,owner_key,status,arrive_game_minute) VALUES(3,'npc','some_merchant','traveling',99999)`)

	out := resolveEscrow(t, path, 42)

	if got := storage.ParseInt(out["caravans_lost"]); got != 1 {
		t.Fatalf("caravans_lost=%d, want 1", got)
	}
	if got := escrowScalar(t, path, `SELECT COUNT(*) FROM caravans WHERE status='traveling'`); got != 2 {
		t.Fatalf("traveling caravans=%d; someone else's venture was caught up in this death", got)
	}
	if got := escrowScalar(t, path, `SELECT COUNT(*) FROM caravans WHERE caravan_id=1 AND status='lost'`); got != 1 {
		t.Fatalf("the dead owner's caravan is still on the road")
	}
}

func TestDyingEndsAnActiveSeclusion(t *testing.T) {
	// Finding #4: seclusion_sessions is keyed by user_id with no incarnation of
	// its own and is not in the reincarnation wipe, so an old body's retreat
	// could later be settled against a new body's realm and attributes.
	path := setupEscrowDB(t)
	batch4Exec(t, path, `INSERT INTO seclusion_sessions(user_id,mode,started_game_minute,ends_game_minute,last_settled_game_minute,status)
		VALUES(42,'qi',1000,100000,1000,'active')`)

	out := resolveEscrow(t, path, 42)

	if got := storage.ParseInt(out["seclusions_ended"]); got != 1 {
		t.Fatalf("seclusions_ended=%d", got)
	}
	if got := escrowScalar(t, path,
		`SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42 AND status='active'`); got != 0 {
		t.Fatalf("the retreat outlived the body")
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT ended_reason FROM seclusion_sessions WHERE user_id=42`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if reason := firstRowMap(res)["ended_reason"]; reason != "incarnation ended" {
		t.Fatalf("ended_reason=%v", reason)
	}
}

func TestResolvingEscrowForACharacterWithNoneIsHarmless(t *testing.T) {
	path := setupEscrowDB(t)
	out := resolveEscrow(t, path, 42)
	for _, key := range []string{"auctions_cancelled", "bids_refunded", "caravans_lost", "seclusions_ended"} {
		if got := storage.ParseInt(out[key]); got != 0 {
			t.Fatalf("%s=%d for a character with no escrow", key, got)
		}
	}
	if got := escrowScalar(t, path, `SELECT COUNT(*) FROM currency_wallets`); got != 0 {
		t.Fatalf("a wallet was conjured for a character with nothing in escrow")
	}
}

func TestEveryEscrowKindIsResolvedInOnePass(t *testing.T) {
	// The whole exploit, end to end: a character with something in every kind
	// of escrow dies once, and nothing is left for a settlement sweep to pay
	// into whatever body holds the id afterwards.
	path := setupEscrowDB(t)
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,seller_user_id,item_id,quantity,currency_id,current_bid,current_bidder_user_id,active,ends_at)
		VALUES(1,42,'nine_yang_fragment',1,'low_spirit_stone',400,43,1,99999999999)`)
	batch4Exec(t, path, `INSERT INTO auctions(auction_id,seller_user_id,item_id,quantity,currency_id,current_bid,current_bidder_user_id,active,ends_at)
		VALUES(2,43,'spirit_iron',1,'low_spirit_stone',250,42,1,99999999999)`)
	batch4Exec(t, path, `INSERT INTO caravans(caravan_id,owner_type,owner_key,status,arrive_game_minute) VALUES(1,'player','42','traveling',99999)`)
	batch4Exec(t, path, `INSERT INTO seclusion_sessions(user_id,ends_game_minute,status) VALUES(42,100000,'active')`)

	resolveEscrow(t, path, 42)

	if got := escrowScalar(t, path,
		`SELECT COUNT(*) FROM auctions WHERE active=1 AND (seller_user_id=42 OR current_bidder_user_id=42)`); got != 0 {
		t.Fatalf("%d auction rows still name the dead character", got)
	}
	if got := escrowScalar(t, path,
		`SELECT COUNT(*) FROM caravans WHERE owner_type='player' AND owner_key='42' AND status='traveling'`); got != 0 {
		t.Fatalf("%d caravans still travel for the dead character", got)
	}
	if got := escrowScalar(t, path,
		`SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42 AND status='active'`); got != 0 {
		t.Fatalf("the seclusion survived")
	}
}

// Death is not optional. A database mid-migration, or a fixture that never
// needed auctions, must still be able to kill a character - the first version
// of this fix broke every old-age death on a database with no `auctions`
// table, which the moderation suite caught.
func TestDyingWorksOnADatabaseWithNoEscrowTables(t *testing.T) {
	path := setupBatch4AuthorityDB(t) // no auctions, caravans or seclusion_sessions
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	out, err := resolveIncarnationEscrowTx(conn, 42, 9000, 1234.5)
	if err != nil {
		t.Fatalf("resolving escrow failed on a database without those tables: %v", err)
	}
	// And it still answers in full rather than leaving the caller to guess.
	for _, key := range []string{"auctions_cancelled", "bids_refunded", "caravans_lost", "seclusions_ended"} {
		if _, ok := out[key]; !ok {
			t.Fatalf("%s missing from the result: %v", key, out)
		}
	}
}

// Review finding #5: a seclusion whose duration is not a whole number of days
// could never finish.
//
//	duration    = 1500 minutes
//	minutes/day = 1440
//	settled     = start + 1440   (whole days only)
//	end         = start + 1500
//
// `completed` required `settled >= end` as well as the clock passing it, and
// `target` is capped at `end`, so the last 60 minutes never settled and the
// session stayed active for the rest of the character's life. The Discord UI
// only ever sends whole days, which is why nothing noticed; the operation
// accepts any positive duration.
func TestAPartDaySeclusionStillCompletes(t *testing.T) {
	path := setupEscrowDB(t)
	batch4Exec(t, path, `INSERT INTO seclusion_sessions(user_id,mode,started_game_minute,ends_game_minute,last_settled_game_minute,environment_mult,status)
		VALUES(42,'qi',0,1500,0,1,'active')`)
	batch4SetCanonicalGameMinute(t, path, 5000)

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw := payloadJSON(t, map[string]any{
		"game_minute": 5000, "minutes_per_day": 1440,
	})
	if _, err := seclusionSettleActionGo(conn, worlddata.Catalog{}, 42, raw); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}

	if got := escrowScalar(t, path,
		`SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42 AND status='completed'`); got != 1 {
		t.Fatal("a 1500-minute seclusion is still active long after it ended")
	}
	// The books are closed at the end so a later settle cannot re-count the
	// part-day that was never paid.
	if got := escrowScalar(t, path,
		`SELECT last_settled_game_minute FROM seclusion_sessions WHERE user_id=42`); got != 1500 {
		t.Fatalf("last_settled=%d, want the session's end at 1500", got)
	}
}

// A whole-day session must still behave exactly as it did.
func TestAWholeDaySeclusionStillCompletesOnTime(t *testing.T) {
	path := setupEscrowDB(t)
	batch4Exec(t, path, `INSERT INTO seclusion_sessions(user_id,mode,started_game_minute,ends_game_minute,last_settled_game_minute,environment_mult,status)
		VALUES(42,'qi',0,2880,0,1,'active')`)
	batch4SetCanonicalGameMinute(t, path, 2000)

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// Mid-session: one day banked, not finished.
	if _, err := seclusionSettleActionGo(conn, worlddata.Catalog{}, 42,
		payloadJSON(t, map[string]any{"game_minute": 2000, "minutes_per_day": 1440})); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	if got := escrowScalar(t, path,
		`SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42 AND status='active'`); got != 1 {
		t.Fatal("a seclusion completed before its end")
	}
}
