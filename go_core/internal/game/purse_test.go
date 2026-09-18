package game

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
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
var purseWritersAllowed = map[string]string{
	"migration_helpers.go": "walletDeltaTx itself - the one door, which writes the purse and sets the mirror from the new balance",
	"lifecycle_actions.go": "character creation and samsara write the whole row from scratch, purse and mirror together, before any door exists to go through",
	"authoritative.go":     "the twenty-five stones a new character starts with, inserted beside the creation row above",
	"admin_undo.go":        "an undo restores the exact snapshot the audit row captured, which is an absolute write by definition and must not be re-derived",
	"actions.go":           "the GM's grant and bulk grant clamp the mirror at zero rather than refusing an overdraft, which is the lever's designed behaviour and not a player's",
}

var (
	sheetWriteRE = regexp.MustCompile(`UPDATE characters SET[^` + "`" + `]*spirit_stones`)
	purseWriteRE = regexp.MustCompile(`(INSERT INTO|UPDATE) currency_wallets`)
)

func TestThePurseHasOneDoor(t *testing.T) {
	roots := []string{".", filepath.Join("..", "simulation")}
	offenders := []string{}
	seen := map[string]bool{}
	for _, root := range roots {
		entries, err := os.ReadDir(root)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			name := entry.Name()
			if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(root, name))
			if err != nil {
				t.Fatal(err)
			}
			src := string(raw)
			if !sheetWriteRE.MatchString(src) && !purseWriteRE.MatchString(src) {
				continue
			}
			seen[name] = true
			if _, allowed := purseWritersAllowed[name]; allowed {
				continue
			}
			offenders = append(offenders, filepath.Join(root, name))
		}
	}
	if len(offenders) > 0 {
		t.Fatalf("these write a player's money without going through walletDeltaTx, so the purse and the\n"+
			"sheet's mirror of it can drift apart. Move them onto walletDeltaTx, or name the file in\n"+
			"purseWritersAllowed with the reason it may write directly:\n  %s",
			strings.Join(offenders, "\n  "))
	}
	for name, reason := range purseWritersAllowed {
		if !seen[name] {
			t.Errorf("purseWritersAllowed names %q, which no longer writes money at all", name)
		}
		if len(strings.TrimSpace(reason)) < 20 {
			t.Errorf("purseWritersAllowed[%q] needs a reason worth reading", name)
		}
	}
}

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
