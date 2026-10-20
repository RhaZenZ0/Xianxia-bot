package game

import (
	"fmt"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The additive levers - a currency grant, an item adjustment - are undone by
// the amount they moved, not by the number they started from. A snapshot of the
// balance before the grant is the balance of a moment that play has since
// moved on from: grant 500, the player earns 300, and putting the absolute 0
// back takes the 300 the lever never gave. The audit row's `moved` is the
// signed amount the lever actually put on the row (an adjustment floored at
// zero moved less than it asked), so the undo is its negative and the redo its
// own positive, both clamped at zero when play has spent below it.
//
// A row written before `moved` existed carries no such key and keeps the
// reversal it was written for: the absolute snapshot. A GM can still undo it;
// it just overwrites what play has since done, as it always did.

// reverseGrantCurrency undoes (or redoes) admin.player.grant_currency.
func reverseGrantCurrency(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	currency := fmt.Sprint(after["currency"])
	amount := i64(after["amount"])
	if _, ok := after["moved"]; ok {
		delta := -i64(after["moved"])
		if redo {
			delta = i64(after["moved"])
		}
		return []sqlStmt{{run: func(conn *storage.Conn, catalog worlddata.Catalog) (int64, error) {
			return walletDeltaClamped(conn, catalog, uid, currency, delta)
		}}}, nil
	}
	// The row of an older release: the absolute balance it snapshotted, and the
	// sheet mirror moved only for the Mortal stone by name.
	snap := pickSnapshot(before, after, redo)
	walletDelta := -amount
	if redo {
		walletDelta = amount
	}
	stmts := []sqlStmt{{sql: `UPDATE currency_wallets SET balance=? WHERE user_id=? AND currency_id=?`, args: []any{i64(snap["balance"]), uid, currency}}}
	if currency == "low_spirit_stone" {
		stmts = append(stmts, sqlStmt{sql: `UPDATE characters SET spirit_stones=MAX(0,spirit_stones+?) WHERE user_id=?`, args: []any{walletDelta, uid}})
	}
	return stmts, nil
}

// walletDeltaClamped moves one wallet by a signed amount without refusing a
// debit the purse cannot cover (play may have spent it), and sets the sheet's
// mirror by the rule the grant itself used: the mirror follows the base currency
// of the world the character stands in now, so a wallet of another world never
// writes it - which the old reversal's by-name test for the Mortal stone got
// wrong in every world but the first.
func walletDeltaClamped(conn *storage.Conn, catalog worlddata.Catalog, uid int64, currency string, delta int64) (int64, error) {
	balance, err := walletBalanceTx(conn, uid, currency)
	if err != nil {
		return 0, err
	}
	next := balance + delta
	if next < 0 {
		next = 0
	}
	if _, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance`, []any{uid, currency, next}); err != nil {
		return 0, err
	}
	rows := int64(1)
	base, err := characterBaseCurrencyTx(conn, catalog, uid)
	if err != nil {
		return 0, err
	}
	if currency == base {
		// By what the wallet actually moved, as the grant's own mirror was.
		r, err := conn.Execute(`UPDATE characters SET spirit_stones=MAX(0,spirit_stones+?),updated_at=? WHERE user_id=?`, []any{next - balance, float64(time.Now().UnixNano()) / 1e9, uid})
		if err != nil {
			return 0, err
		}
		rows += r.RowsAffected
	}
	return rows, nil
}

// reverseAdjustItem undoes (or redoes) admin.player.adjust_item.
func reverseAdjustItem(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	if _, ok := after["moved"]; ok {
		itemID := fmt.Sprint(after["item_id"])
		delta := -i64(after["moved"])
		if redo {
			delta = i64(after["moved"])
		}
		if delta >= 0 {
			return []sqlStmt{{sql: `INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`,
				args: []any{uid, itemID, delta}}}, nil
		}
		return []sqlStmt{
			{sql: `UPDATE inventory SET quantity=MAX(0,quantity+?) WHERE user_id=? AND item_id=?`, args: []any{delta, uid, itemID}},
			{sql: `DELETE FROM inventory WHERE user_id=? AND item_id=? AND quantity<=0`, args: []any{uid, itemID}},
		}, nil
	}
	// The row of an older release: the absolute quantity it snapshotted.
	snap := pickSnapshot(before, after, redo)
	itemID := fmt.Sprint(snap["item_id"])
	quantity := i64(snap["quantity"])
	if quantity <= 0 {
		return []sqlStmt{{sql: `DELETE FROM inventory WHERE user_id=? AND item_id=?`, args: []any{uid, itemID}}}, nil
	}
	return []sqlStmt{{sql: `INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=excluded.quantity`,
		args: []any{uid, itemID, quantity}}}, nil
}

// sceneSnapshot reads the scene row a teleport is about to overwrite, nil when
// the player has none (the teleport's own UPDATE then writes nothing either).
func sceneSnapshot(conn *storage.Conn, uid int64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json FROM player_scene_state WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, nil
	}
	snap := map[string]any{}
	for _, col := range []string{"physical_location", "scene_type", "scene_key", "scene_label", "channel_id", "metadata_json"} {
		snap[col] = row[col]
	}
	return snap, nil
}

// reverseForceEndScene undoes (or redoes) admin.player.force_end_scene: the
// five scene columns the lever writes, from the `scene` snapshot the row has
// carried since v1.35.0 (nil for a player who had no scene row, whom the lever
// left alone and the undo leaves alone). A row without the key is an older
// force-end and keeps its older reversal, the type and the label.
func reverseForceEndScene(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	snap := pickSnapshot(before, after, redo)
	scene, hasScene := snap["scene"]
	if !hasScene {
		return []sqlStmt{{`UPDATE player_scene_state SET scene_type=?,scene_label=? WHERE user_id=?`,
			[]any{fmt.Sprint(snap["scene_type"]), fmt.Sprint(snap["scene_label"]), uid}, nil}}, nil
	}
	sceneRow, _ := scene.(map[string]any)
	return []sqlStmt{{run: func(conn *storage.Conn, catalog worlddata.Catalog) (int64, error) {
		if sceneRow == nil {
			return 0, nil
		}
		var channel any
		if sceneRow["channel_id"] != nil {
			channel = i64(sceneRow["channel_id"])
		}
		now := float64(time.Now().UnixNano()) / 1e9
		r, err := conn.Execute(`UPDATE player_scene_state SET scene_type=?,scene_key=?,scene_label=?,channel_id=?,metadata_json=?,updated_at=? WHERE user_id=?`,
			[]any{fmt.Sprint(sceneRow["scene_type"]), fmt.Sprint(sceneRow["scene_key"]), fmt.Sprint(sceneRow["scene_label"]), channel, fmt.Sprint(sceneRow["metadata_json"]), now, uid})
		if err != nil {
			return 0, err
		}
		return r.RowsAffected, nil
	}}}, nil
}

// reverseTeleport undoes (or redoes) admin.player.teleport.
//
// A teleport is a journey: it went through moveCharacterTx, which converted the
// purse if it crossed a world, and wrote the scene row. Putting the character
// back by a bare UPDATE of the location left the purse converted and the scene
// at the destination, so the undo goes the same way home - the door, then the
// scene row the snapshot holds. A snapshot carries `scene` (a map, or null for a
// player with no scene row) from this release on; a row without the key is an
// older teleport and keeps its older reversal, the location alone, written
// without the purse or the scene (legacyLocationRestore, in admin_undo.go, the
// one bare location write the package keeps).
func reverseTeleport(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	snap := pickSnapshot(before, after, redo)
	location := fmt.Sprint(snap["location"])
	scene, hasScene := snap["scene"]
	if !hasScene {
		return []sqlStmt{legacyLocationRestore(uid, location)}, nil
	}
	sceneRow, _ := scene.(map[string]any)
	return []sqlStmt{{run: func(conn *storage.Conn, catalog worlddata.Catalog) (int64, error) {
		now := float64(time.Now().UnixNano()) / 1e9
		if _, err := moveCharacterTx(conn, catalog, uid, location, now); err != nil {
			return 0, err
		}
		rows := int64(1)
		if sceneRow == nil {
			return rows, nil
		}
		var channel any
		if sceneRow["channel_id"] != nil {
			channel = i64(sceneRow["channel_id"])
		}
		r, err := conn.Execute(`UPDATE player_scene_state SET physical_location=?,scene_type=?,scene_key=?,scene_label=?,channel_id=?,metadata_json=?,updated_at=? WHERE user_id=?`,
			[]any{fmt.Sprint(sceneRow["physical_location"]), fmt.Sprint(sceneRow["scene_type"]), fmt.Sprint(sceneRow["scene_key"]),
				fmt.Sprint(sceneRow["scene_label"]), channel, fmt.Sprint(sceneRow["metadata_json"]), now, uid})
		if err != nil {
			return 0, err
		}
		return rows + r.RowsAffected, nil
	}}}, nil
}
