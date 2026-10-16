package game

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// The additive levers and the teleport are undone against a character who
// stands at a place the catalogue carries. The shared admin fixture puts her at
// "Old Place", which belongs to no world, so every world-dependent step of an
// undo (the sheet's mirror, the purse crossing) falls back to the Mortal stone
// there and the faults below cannot show.
func setupUndoLeverDB(t *testing.T, location string) string {
	t.Helper()
	path := setupAdminDB(t)
	batch4Exec(t, path, `INSERT OR IGNORE INTO content_locations VALUES('Spirit Jade Capital','{}',0)`)
	batch4Exec(t, path, `INSERT OR IGNORE INTO content_locations VALUES('Greenriver Town','{}',0)`)
	batch4Exec(t, path, fmt.Sprintf(`UPDATE characters SET location='%s' WHERE user_id=42`, location))
	return path
}

func leverWallet(t *testing.T, path, currency string) int64 {
	t.Helper()
	return storage.ParseInt(scalar(t, path, "SELECT COALESCE((SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id=?),0)", currency))
}

func leverSheet(t *testing.T, path string) int64 {
	t.Helper()
	return storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42"))
}

// A grant at Spirit Jade Capital pays the crystal, and the sheet follows the
// crystal; the undo used to look for the Mortal stone by name, so it left the
// crystal's mirror where the grant put it and moved the sheet for a stone grant
// the forward never put on it.
func TestUndoingAGrantMirrorsTheSheetByTheWorldsCoin(t *testing.T) {
	path := setupUndoLeverDB(t, "Spirit Jade Capital")
	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_crystal", "amount": 7, "reason": "reward"})
	if got := leverSheet(t, path); got != 17 {
		t.Fatalf("sheet=%d after a crystal grant at Spirit Jade Capital, want 17", got)
	}
	undoLast(t, path)
	if got := leverSheet(t, path); got != 10 {
		t.Fatalf("sheet=%d after the undo, want 10: the undo left the crystal mirror where the grant put it", got)
	}
	if got := leverWallet(t, path, "low_spirit_crystal"); got != 0 {
		t.Fatalf("crystal wallet=%d after the undo, want 0", got)
	}

	// A Mortal stone grant in the upper world never moved the sheet, so its undo must not.
	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_stone", "amount": 5, "reason": "reward"})
	if got := leverSheet(t, path); got != 10 {
		t.Fatalf("sheet=%d after a stone grant at Spirit Jade Capital, want 10 (the forward leaves it)", got)
	}
	undoLast(t, path)
	if got := leverSheet(t, path); got != 10 {
		t.Fatalf("sheet=%d after undoing a stone grant, want 10: the undo moved a mirror the grant never did", got)
	}
}

// Grant 500, the player earns 300, undo: the lever's 500 goes and the 300 stays;
// a redo puts the 500 back on top.
func TestUndoingAGrantKeepsTheMoneyEarnedSince(t *testing.T) {
	path := setupUndoLeverDB(t, "Greenriver Town")
	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_stone", "amount": 500, "reason": "reward"})
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=balance+300 WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=spirit_stones+300 WHERE user_id=42`)
	undoLast(t, path)
	if got := leverWallet(t, path, "low_spirit_stone"); got != 300 {
		t.Fatalf("wallet=%d after the undo, want the 300 play earned since the grant", got)
	}
	if got := leverSheet(t, path); got != 310 {
		t.Fatalf("sheet=%d after the undo, want 310 (10 + the 300 earned)", got)
	}
	undoLast(t, path) // the undo of the undo is a redo of the grant
	if got := leverWallet(t, path, "low_spirit_stone"); got != 800 {
		t.Fatalf("wallet=%d after the redo, want 800", got)
	}
	if got := leverSheet(t, path); got != 810 {
		t.Fatalf("sheet=%d after the redo, want 810", got)
	}

	// Money spent below the grant is not made negative by the undo.
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=100 WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=100 WHERE user_id=42`)
	undoLast(t, path)
	if got := leverWallet(t, path, "low_spirit_stone"); got != 0 {
		t.Fatalf("wallet=%d after undoing the grant with 100 left, want 0 (clamped, never negative)", got)
	}
}

func TestUndoingAnItemAdjustmentKeepsWhatPlayAddedSince(t *testing.T) {
	path := setupUndoLeverDB(t, "Greenriver Town")
	qty := func() any {
		return scalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")
	}
	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": 3, "reason": "grant"})
	batch4Exec(t, path, `UPDATE inventory SET quantity=quantity+10 WHERE user_id=42 AND item_id='spirit_herb'`)
	undoLast(t, path)
	if got := storage.ParseInt(qty()); got != 10 {
		t.Fatalf("herbs=%d after the undo, want the 10 play added since (the undo deleted the row)", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(qty()); got != 13 {
		t.Fatalf("herbs=%d after the redo, want 13", got)
	}

	// A removal floored at zero moved less than it asked: the undo gives back what was taken.
	batch4Exec(t, path, `DELETE FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`)
	batch4Exec(t, path, `INSERT INTO inventory VALUES(42,'spirit_herb',2)`)
	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": -5, "reason": "take"})
	if qty() != nil {
		t.Fatalf("herbs=%v after removing 5 of 2, want the row gone", qty())
	}
	undoLast(t, path)
	if got := storage.ParseInt(qty()); got != 2 {
		t.Fatalf("herbs=%d after undoing a removal floored at zero, want the 2 it took (not the 5 it asked)", got)
	}

	// Play used the herbs up: an undo of a grant clamps at nothing and leaves no row of zero.
	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": 4, "reason": "grant"})
	batch4Exec(t, path, `UPDATE inventory SET quantity=1 WHERE user_id=42 AND item_id='spirit_herb'`)
	undoLast(t, path)
	if qty() != nil {
		t.Fatalf("herbs=%v, want no row when play has spent below what the lever gave", qty())
	}
}

// 12,345 stones teleported up the ladder come out as 45 stones and 123 crystals;
// the undo goes home through the same door, so the crystals become stones again
// and the player is on the scene they left.
func TestUndoingATeleportGoesHomeThroughTheDoor(t *testing.T) {
	path := setupUndoLeverDB(t, "Greenriver Town")
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=12345 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO currency_wallets VALUES(42,'low_spirit_stone',12345)`)
	batch4Exec(t, path, `INSERT INTO player_scene_state VALUES(42,'Greenriver Town','event','market_brawl','The Market Brawl',777,'{"round":2}',0)`)

	applyAdmin(t, path, "admin.player.teleport", map[string]any{"user_id": 42, "location": "Spirit Jade Capital", "reason": "story"})
	if leverWallet(t, path, "low_spirit_stone") != 45 || leverWallet(t, path, "low_spirit_crystal") != 123 {
		t.Fatalf("after the teleport: %d stones, %d crystals, want 45 and 123 (the fixture is wrong, not the undo)",
			leverWallet(t, path, "low_spirit_stone"), leverWallet(t, path, "low_spirit_crystal"))
	}

	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT location FROM characters WHERE user_id=42")); got != "Greenriver Town" {
		t.Fatalf("location=%q after the undo, want Greenriver Town", got)
	}
	if got := leverWallet(t, path, "low_spirit_stone"); got != 12345 {
		t.Fatalf("stones=%d after the undo, want 12345: the purse stayed converted", got)
	}
	if got := leverWallet(t, path, "low_spirit_crystal"); got != 0 {
		t.Fatalf("crystals=%d after the undo, want 0", got)
	}
	if got := leverSheet(t, path); got != 12345 {
		t.Fatalf("sheet=%d after the undo, want 12345", got)
	}
	scene := fmt.Sprint(scalar(t, path, "SELECT physical_location||'|'||scene_type||'|'||scene_key||'|'||scene_label||'|'||channel_id||'|'||metadata_json FROM player_scene_state WHERE user_id=42"))
	if scene != `Greenriver Town|event|market_brawl|The Market Brawl|777|{"round":2}` {
		t.Fatalf("scene=%q after the undo, want the one the teleport overwrote", scene)
	}

	undoLast(t, path) // the redo is the teleport again
	if got := fmt.Sprint(scalar(t, path, "SELECT location FROM characters WHERE user_id=42")); got != "Spirit Jade Capital" {
		t.Fatalf("location=%q after the redo, want Spirit Jade Capital", got)
	}
	if leverWallet(t, path, "low_spirit_crystal") != 123 {
		t.Fatalf("crystals=%d after the redo, want 123", leverWallet(t, path, "low_spirit_crystal"))
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT scene_type||'|'||scene_label FROM player_scene_state WHERE user_id=42")); got != "world|Spirit Jade Capital" {
		t.Fatalf("scene=%q after the redo, want the world scene at the destination", got)
	}
}

// A player with no scene row is teleported and undone without one being made.
func TestUndoingATeleportMakesNoSceneRow(t *testing.T) {
	path := setupUndoLeverDB(t, "Greenriver Town")
	applyAdmin(t, path, "admin.player.teleport", map[string]any{"user_id": 42, "location": "Spirit Jade Capital", "reason": "story"})
	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT location FROM characters WHERE user_id=42")); got != "Greenriver Town" {
		t.Fatalf("location=%q after the undo, want Greenriver Town", got)
	}
	if n := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM player_scene_state WHERE user_id=42")); n != 0 {
		t.Fatalf("%d scene rows after the undo, want none", n)
	}
}

// Audit rows written before the levers recorded what they moved carry no
// `moved` and no `scene`, and keep the reversal they were written for: the
// absolute snapshot, the bare location. They are undone, never refused.
func TestAnAuditRowFromBeforeTheDeltaKeepsItsOldReversal(t *testing.T) {
	audit := func(t *testing.T, path, action, target string, before, after map[string]any) {
		t.Helper()
		b, _ := json.Marshal(before)
		a, _ := json.Marshal(after)
		batch4Exec(t, path, fmt.Sprintf(`INSERT INTO admin_audit_log(admin_user_id,action,target,before_json,after_json,reason,created_at) VALUES(0,'%s','%s','%s','%s','',0)`, action, target, b, a))
	}

	t.Run("grant restores the absolute balance and the Mortal stone mirror", func(t *testing.T) {
		path := setupUndoLeverDB(t, "Old Place")
		batch4Exec(t, path, `INSERT INTO currency_wallets VALUES(42,'low_spirit_stone',500)`)
		batch4Exec(t, path, `UPDATE characters SET spirit_stones=510 WHERE user_id=42`)
		audit(t, path, "admin.player.grant_currency", "user:42",
			map[string]any{"currency": "low_spirit_stone", "balance": 0},
			map[string]any{"currency": "low_spirit_stone", "amount": 500, "balance": 500})
		undoLast(t, path)
		if got := leverWallet(t, path, "low_spirit_stone"); got != 0 {
			t.Fatalf("wallet=%d, want the snapshotted 0", got)
		}
		if got := leverSheet(t, path); got != 10 {
			t.Fatalf("sheet=%d, want 10", got)
		}
	})
	t.Run("adjust_item restores the absolute quantity", func(t *testing.T) {
		path := setupUndoLeverDB(t, "Old Place")
		batch4Exec(t, path, `INSERT INTO inventory VALUES(42,'spirit_herb',9)`)
		audit(t, path, "admin.player.adjust_item", "user:42",
			map[string]any{"item_id": "spirit_herb", "quantity": 4},
			map[string]any{"item_id": "spirit_herb", "quantity": 9, "delta": 5})
		undoLast(t, path)
		if got := storage.ParseInt(scalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")); got != 4 {
			t.Fatalf("herbs=%d, want the snapshotted 4", got)
		}
	})
	t.Run("teleport restores the location alone", func(t *testing.T) {
		path := setupUndoLeverDB(t, "Spirit Jade Capital")
		batch4Exec(t, path, `INSERT INTO currency_wallets VALUES(42,'low_spirit_crystal',123)`)
		audit(t, path, "admin.player.teleport", "user:42",
			map[string]any{"location": "Greenriver Town"},
			map[string]any{"location": "Spirit Jade Capital"})
		undoLast(t, path)
		if got := fmt.Sprint(scalar(t, path, "SELECT location FROM characters WHERE user_id=42")); got != "Greenriver Town" {
			t.Fatalf("location=%q, want Greenriver Town", got)
		}
		if got := leverWallet(t, path, "low_spirit_crystal"); got != 123 {
			t.Fatalf("crystals=%d, want the purse left as it was (123): an old row is undone as it was written", got)
		}
	})
}

// The automation switch accepts every system the simulator reads.
func TestTheAutomationSwitchAcceptsEverySystemTheSimulatorReads(t *testing.T) {
	path := setupAdminDB(t)
	for _, system := range []string{"npc_consignments", "secret_realms"} {
		out := applyAdmin(t, path, "admin.automation.set", map[string]any{"system": system, "enabled": false, "reason": "test"})
		got, _ := out.(map[string]any)
		settings, _ := got["settings"].(map[string]bool)
		if v, ok := settings[system]; !ok || v {
			t.Fatalf("%s: settings=%v, want the key present and off", system, got["settings"])
		}
	}
}
