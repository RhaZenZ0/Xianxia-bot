package game

// Whether the thing is what it claims to be.
//
// `item_provenance.authenticity` has existed since the provenance table was
// written and every one of its five writers passed the literal 100 - the
// black-market broker, the sect inheritance, the caravan, the family send-off,
// the hidden-sect grant - so the column was a constant that `/provenance`
// printed and no rule consulted. It recorded, precisely, that nothing in the
// world was ever fake.
//
// Some of what the underworld sells is fake. A broker's goods now enter at a
// rolled authenticity rather than a flat hundred, an appraiser is the one who
// can tell you which you are holding, and a keeper pays for a forgery what a
// forgery is worth - so passing one off at full price depends on the buyer not
// having had it read. Nothing here touches what an item *does*: authenticity is
// information and money, not power.

import (
	"fmt"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// Genuine. Every honest source writes this.
	authenticityGenuine = 100
	// An underworld broker's stock runs from "a good copy" to "genuine, and
	// do not ask where it came from".
	authenticityUnderworldFloor = 55
	authenticityUnderworldSpan  = 46 // 55..100 inclusive
	// Below this a thing is a forgery rather than merely uncertain.
	authenticityForgery = 80
)

// rollUnderworldAuthenticity is what a broker is actually handing over.
func rollUnderworldAuthenticity() (int64, error) {
	n, err := gamerng.Intn(authenticityUnderworldSpan)
	if err != nil {
		return authenticityGenuine, err
	}
	return int64(authenticityUnderworldFloor + n), nil
}

// itemAuthenticityTx is the worst provenance the holder has for this item -
// worst, because if one of the three seals in your bag is a forgery you cannot
// know which, and a keeper pricing them assumes the same. No row at all means
// nothing has ever cast doubt on it.
func itemAuthenticityTx(conn *storage.Conn, userID int64, itemID string) (int64, bool, error) {
	res, err := conn.Execute(
		`SELECT MIN(authenticity) AS worst, COUNT(*) AS n FROM item_provenance WHERE user_id=? AND item_id=?`,
		[]any{userID, itemID})
	if err != nil {
		return authenticityGenuine, false, err
	}
	row := firstRowMap(res)
	if row == nil || i64(row["n"]) == 0 {
		return authenticityGenuine, false, nil
	}
	worst := i64(row["worst"])
	if worst <= 0 || worst > authenticityGenuine {
		worst = authenticityGenuine
	}
	return worst, true, nil
}

// authenticityPrice scales a payout by how genuine the goods are. A keeper who
// can see the thing is a copy does not pay for the original.
func authenticityPrice(unit, authenticity int64) int64 {
	if authenticity >= authenticityGenuine {
		return unit
	}
	scaled := unit * maxI64(1, authenticity) / authenticityGenuine
	return maxI64(1, scaled)
}

// authenticityWord is what a reading reports.
func authenticityWord(authenticity int64) string {
	switch {
	case authenticity >= authenticityGenuine:
		return "genuine"
	case authenticity >= authenticityForgery:
		return "genuine, with an irregularity in the making"
	case authenticity >= 65:
		return "a good copy"
	default:
		return "a forgery"
	}
}

func authenticityNote(authenticity int64) string {
	return fmt.Sprintf("%s (%d%%)", authenticityWord(authenticity), authenticity)
}
