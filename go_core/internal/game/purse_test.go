package game

import (
	"testing"

	"xianxia/core/internal/storage"
)

// One door for money (v1.0.0-rc.43).
//
// A player's stones live in two places: `currency_wallets`, the purse, and
// `characters.spirit_stones`, which is a *mirror* of it that the sheet and a
// dozen readers use. `walletDeltaTx` is the one function that keeps them in
// step - it writes the purse and then sets the mirror from the new balance.
//
// Eleven other places wrote one of the two directly. `trade.accept` was the
// worst: it moved stones between two players with two bare
// `UPDATE characters SET spirit_stones=spirit_stones-?+?` statements and named
// `currency_wallets` nowhere in the file, so every trade left the two
// disagreeing - and because the mirror is written from the *purse*, the next
// shop purchase silently overwrote the traded stones out of existence. Five
// spends and a reward moved the sheet without the purse; two spends moved the
// purse without the sheet.
//
// Nothing caught it because every fixture seeded the column and most never made
// a `currency_wallets` row at all, so the tests modelled a state production
// cannot reach. That is the fixture rule in CLAUDE.md, and this is the gate for
// the other half: a write outside the one door has to be named here with the
// reason it is allowed to be.
//
// The gate itself, TestThePurseHasOneDoor, is in one_door_walks_test.go since
// v1.41: it walks every function by AST and allowlists per function
// (purseWritersAllowed there), where the text sweep that stood here
// allowlisted per file and could not see a DELETE or an INSERT INTO
// characters.

// The bug itself. With the old two bare UPDATEs restored this fails on the
// purse, which never moved at all.
func TestATradeMovesThePurseAndNotOnlyTheSheet(t *testing.T) {
	path, inn := setupTradeDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location=?,spirit_stones=100 WHERE user_id IN (42,43)`, inn)
	syncPurse(t, path)

	offer, err := tradeApply(t, path, world, "trade.offer", 42, 1, map[string]any{
		"to_user_id": 43, "give_stones": 30, "want_stones": 0, "game_minute": 1000,
	})
	if err != nil {
		t.Fatalf("offer: %v", err)
	}
	offerID := storage.ParseInt(offer["offer_id"])
	if offerID <= 0 {
		t.Fatalf("no offer was struck: %v", offer)
	}
	if _, err := tradeApply(t, path, world, "trade.accept", 43, 2, map[string]any{
		"offer_id": offerID, "game_minute": 1100,
	}); err != nil {
		t.Fatalf("accept: %v", err)
	}

	for _, want := range []struct {
		user    int64
		balance int64
	}{{42, 70}, {43, 130}} {
		purse := storage.ParseInt(actionScalar(t, path,
			`SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id='low_spirit_stone'`, want.user))
		sheet := storage.ParseInt(actionScalar(t, path,
			`SELECT spirit_stones FROM characters WHERE user_id=?`, want.user))
		if purse != want.balance {
			t.Errorf("user %d: the purse holds %d, want %d", want.user, purse, want.balance)
		}
		if sheet != purse {
			t.Errorf("user %d: the sheet says %d and the purse says %d", want.user, sheet, purse)
		}
	}
}
