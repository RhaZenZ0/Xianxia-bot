package game

import (
	"fmt"

	"xianxia/core/internal/storage"
)

// Escrow that must not outlive the body (v0.23.1, external review finding #1).
//
// Reincarnation wipes inventory and wallets because they belong to one
// incarnation. Auctions, bids and caravans do not live in those tables: they
// are asynchronous records keyed by the persistent Discord user id, settled
// later by the maintenance sweep, which pays whoever that id names *at
// settlement time*. So:
//
//	incarnation A lists a rare item -> dies -> reincarnates ->
//	inventory is wiped -> the auction closes -> the item, or the proceeds,
//	land in incarnation B
//
// Bids are the same in reverse: a bid escrows currency, and being outbid
// refunds it - into whichever body holds the id when the refund fires.
//
// The review suggested stamping an incarnation id on every asynchronous record
// and checking it at settlement. This does the other thing: it resolves the
// escrow at the moment the body dies, so there is nothing left to check. A
// generation column would mean every settlement path must remember to compare
// it - which is the same omission that caused this, relocated somewhere harder
// to see. There is one place a life ends, and this runs there.
//
// Nothing here pays the dead. Refunds and returned goods go to the dead
// character's own rows, which reincarnation then wipes; if the player never
// reincarnates they sit with the corpse. The point is that the value leaves
// escrow while it still belongs to the incarnation that put it there.
func resolveIncarnationEscrowTx(conn *storage.Conn, userID int64, gameMinute int64, now float64) (map[string]any, error) {
	out := map[string]any{
		"auctions_cancelled": int64(0),
		"bids_refunded":      int64(0),
		"caravans_lost":      int64(0),
		"seclusions_ended":   int64(0),
	}
	// A missing table means there is no escrow of that kind - a database mid-
	// migration, or a narrow fixture. Dying must not fail because a feature's
	// table is absent: a character who cannot die is a worse bug than one
	// whose auction outlives them.
	if tableExistsTx(conn, "auctions") {
		cancelled, refunded, err := resolveDeadPlayersAuctionsTx(conn, userID, now)
		if err != nil {
			return nil, err
		}
		out["auctions_cancelled"] = cancelled
		out["bids_refunded"] = refunded
	}
	if tableExistsTx(conn, "caravans") {
		// 3. Caravans still on the road. Settlement pays owner_key, which is
		//    this id; there is nobody left to pay, so the venture is lost with
		//    its owner. `lost` is terminal - the sweep only settles `traveling`.
		lost, err := conn.Execute(
			`UPDATE caravans SET status='lost',updated_at=? WHERE owner_type='player' AND owner_key=? AND status='traveling'`,
			[]any{now, fmt.Sprint(userID)})
		if err != nil {
			return nil, err
		}
		out["caravans_lost"] = lost.RowsAffected
	}
	if tableExistsTx(conn, "seclusion_sessions") {
		// 4. Seclusion (review finding #4). A session is keyed by user_id with
		//    no incarnation of its own, and settlement reads the *current*
		//    character's realm, phase and attributes while keeping the old
		//    session's start, end and environment - so an old body's retreat
		//    could be scored against a new one. The retreat ends when the body
		//    does.
		ended, err := conn.Execute(
			`UPDATE seclusion_sessions SET status='completed',ended_reason='incarnation ended',
				last_settled_game_minute=MAX(last_settled_game_minute,?),updated_at=?
			 WHERE user_id=? AND status='active'`,
			[]any{gameMinute, now, userID})
		if err != nil {
			return nil, err
		}
		out["seclusions_ended"] = ended.RowsAffected
	}
	return out, nil
}

// resolveDeadPlayersAuctionsTx handles both sides of the auction house.
func resolveDeadPlayersAuctionsTx(conn *storage.Conn, userID int64, now float64) (int64, int64, error) {
	// 1. Lots this character was selling. The seller is dead, so the sale
	//    cannot complete: refund whoever is currently holding the high bid
	//    (their money is in escrow) and return the goods to the corpse.
	selling, err := conn.Execute(
		`SELECT auction_id,item_id,quantity,currency_id,current_bid,current_bidder_user_id
		 FROM auctions WHERE seller_user_id=? AND active=1`, []any{userID})
	if err != nil {
		return 0, 0, err
	}
	cancelled := int64(0)
	for _, row := range selling.Rows {
		a := rowMap(selling.Columns, row)
		auctionID := storage.ParseInt(a["auction_id"])
		if bidder := storage.ParseInt(a["current_bidder_user_id"]); bidder > 0 {
			if _, err = walletDeltaTx(conn, bidder, fmt.Sprint(a["currency_id"]),
				storage.ParseInt(a["current_bid"]), now); err != nil {
				return 0, 0, err
			}
		}
		if _, err = conn.Execute(
			`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
			 ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity`,
			[]any{userID, fmt.Sprint(a["item_id"]), maxI64(1, storage.ParseInt(a["quantity"]))},
		); err != nil {
			return 0, 0, err
		}
		if _, err = conn.Execute(
			`UPDATE auctions SET active=0,current_bidder_user_id=NULL,current_bid=0 WHERE auction_id=?`,
			[]any{auctionID}); err != nil {
			return 0, 0, err
		}
		cancelled++
	}

	// 2. Lots this character was winning. They cannot collect, so the escrow
	//    comes back and the lot reverts to no bid - the floor returns to
	//    starting_bid rather than staying propped up by a dead cultivator's
	//    offer, which would penalise every other bidder for a stranger's death.
	bidding, err := conn.Execute(
		`SELECT auction_id,currency_id,current_bid FROM auctions
		 WHERE current_bidder_user_id=? AND active=1`, []any{userID})
	if err != nil {
		return 0, 0, err
	}
	refunded := int64(0)
	for _, row := range bidding.Rows {
		a := rowMap(bidding.Columns, row)
		amount := storage.ParseInt(a["current_bid"])
		if amount > 0 {
			if _, err = walletDeltaTx(conn, userID, fmt.Sprint(a["currency_id"]), amount, now); err != nil {
				return 0, 0, err
			}
		}
		if _, err = conn.Execute(
			`UPDATE auctions SET current_bidder_user_id=NULL,current_bid=0 WHERE auction_id=?`,
			[]any{storage.ParseInt(a["auction_id"])}); err != nil {
			return 0, 0, err
		}
		refunded++
	}

	return cancelled, refunded, nil
}

// rowMap pairs a result's columns with one of its rows. firstRowMap only ever
// returns the first; this walks all of them.
func rowMap(columns []string, row []any) map[string]any {
	out := make(map[string]any, len(columns))
	for i, name := range columns {
		if i < len(row) {
			out[name] = row[i]
		}
	}
	return out
}
