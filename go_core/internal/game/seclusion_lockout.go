package game

// The doors stay shut (v1.0.0-rc.56).
//
// `/cultivation → Cultivate → Seclusion` has told the player in as many words
// since v0.30.0 that *"any state-changing command will remain locked until
// you use /cultivation → Cultivate → End"* - a leaf actually labelled
// **Seclusion End**, which nobody noticed because nothing was ever locked.
// The engine blocked exactly one
// thing behind a closed door - a Hearth-Return talisman - and Python held a
// half-gate that covered only the ~141 handlers wearing one decorator,
// exempted by a function-name *prefix*, and settled before it checked. So a
// retreat cost the player nothing, which is why the rate could be a discount
// and nobody minded.
//
// Two rules make a lockout safe to have at all:
//
//   - **It self-clears, unconditionally.** A gate that refuses every action,
//     on state that only an action can clear, is a deadlock. This settles,
//     pays and completes an expired retreat and then lets the action through,
//     the way `ensureRoadTransitReadyTx` clears a finished journey. It must
//     never depend on the `background_seclusion` automation flag: a GM
//     switching that off would otherwise lock every secluded player out for
//     good.
//   - **The way out is always open.** `seclusion.settle` is exempt, so
//     `/cultivation → Cultivate → Seclusion End` works whatever else is refused -
//     including for a retreat grandfathered from before schema 57, whose
//     deadline is a game minute that a frozen world clock may never reach.
//
// Every `admin.*` lever falls through to the switch in `ApplyWithWorld`
// rather than coming through `applyAuthoritative`, so a GM is immune by
// construction - the same asymmetry `TestAClosedWorldIsStillTheGMsToOpen`
// holds for maintenance.
//
// Scope, stated the way `moderation.go` states its own: this cannot
// intercept a raw `/v1/db` write or the simulation runner, and it cannot see
// a read - `/sheet` and every other card answer out of the presentation
// layer's own SQL and never reach this path. `app/bot/seclusion.py` is the
// other half, at the four doors a player has.

import (
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// seclusionExemptOperations is everything a secluded cultivator may still do.
// It is deliberately tiny: the retreat is the trade, and a long list of
// exceptions would make the premium unearned.
var seclusionExemptOperations = map[string]bool{
	// The way out, and the only one. A retreat this gate cannot end is a
	// character nobody can play again.
	"seclusion.settle": true,
}

// checkPlayerSeclusionTx refuses an action behind a closed door, and ends a
// retreat whose time is up rather than refusing on it.
func checkPlayerSeclusionTx(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64, operation string) error {
	if seclusionExemptOperations[operation] {
		return nil
	}
	res, err := conn.Execute(`SELECT ends_game_minute,ends_real_ts,mode FROM seclusion_sessions WHERE user_id=? AND status='active'`, []any{userID})
	if err != nil {
		// Fails towards play, the rule `app/bot/seclusion.py` states about
		// itself: a world without the table is a world where nobody is
		// secluded, and a read that fails for any other reason must not turn
		// into a lockout nothing can lift.
		return nil
	}
	row := firstRowMap(res)
	if row == nil {
		return nil
	}
	if seclusionIsOver(row, gameMinute) {
		// Settle it here rather than merely letting the action through: the
		// payout is the player's and must not wait on the sweep, which is
		// flag-gated and may be off.
		if _, _, err := SettleSeclusionTx(conn, catalog, userID, gameMinute, false, ""); err != nil {
			return err
		}
		return nil
	}
	return fmt.Errorf("you are in closed-door %s seclusion; %s. Use /cultivation → Cultivate → Seclusion End to emerge early",
		firstNonempty(fmt.Sprint(row["mode"]), "qi"), seclusionRemainingPhrase(row, gameMinute))
}

// seclusionIsOver reads the deadline the way the settle does: the real one
// when there is one (schema 57), and the game-minute end for a retreat
// started before that column existed.
func seclusionIsOver(row map[string]any, gameMinute int64) bool {
	if endsReal, ok := seclusionRealDeadline(row); ok {
		return nowSeconds() >= endsReal
	}
	return gameMinute >= i64(row["ends_game_minute"])
}

// seclusionRemainingPhrase says how much longer, in whichever clock the
// retreat is actually running on - a real deadline is a real answer, and a
// grandfathered one can only be given in world time.
func seclusionRemainingPhrase(row map[string]any, gameMinute int64) string {
	if endsReal, ok := seclusionRealDeadline(row); ok {
		minutes := int64((endsReal - nowSeconds()) / 60)
		if minutes < 1 {
			minutes = 1
		}
		if minutes >= 60 {
			return fmt.Sprintf("the doors open in about %dh %dm", minutes/60, minutes%60)
		}
		return fmt.Sprintf("the doors open in about %d minutes", minutes)
	}
	remaining := i64(row["ends_game_minute"]) - gameMinute
	if remaining < 1 {
		remaining = 1
	}
	return fmt.Sprintf("about %d world-minutes remain", remaining)
}
