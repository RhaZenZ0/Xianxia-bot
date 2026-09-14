package game

// What a cultivator is carrying, and who can follow it.
//
// `item_provenance.tracking_strength` is the sibling of `authenticity`, and it
// had the same fault in the other direction: five writers set it with care - an
// underworld broker's goods enter at the post's own heat, a hidden sect's grant
// at seventy, a caravan's cargo at five or twenty depending on whether it is
// being smuggled, an honest source at zero - and then nothing on earth read it.
// `/provenance` printed a number that meant nothing, and a cultivator could
// walk into a city wearing a branded relic taken out of a night market and be
// no easier to find than one carrying nothing at all.
//
// `bounty_hunter_pursuits` was sitting right there. A hunter's whole problem is
// finding someone, and what marks a fugitive in this genre is precisely the
// thing they took: an artifact with somebody's seal still on it, a manual off
// a burned sect's shelf. So the trail is what the quarry still has on them, and
// it does two things and no more - it makes a hunter close faster, and it makes
// shaking one harder. It never touches what an item does, the way authenticity
// never did: this is information, not power.
//
// The lever is real and it is the obvious one. The trail is read off what is in
// the bags and what is worn, not off the provenance rows alone, so selling the
// relic, or leaving it in a storehouse, cools it - and a cultivator who wants
// to disappear gets rid of the thing that marks them, which is the choice the
// column was always describing and never asked anyone to make.

import (
	"xianxia/core/internal/storage"
)

const (
	// The most a trail can add to a hunter's work, as a percentage. At the
	// top of the range (a hidden sect's brand, at 70) a pursuit closes about
	// half again as fast; at the bottom it changes nothing measurable.
	trailPressureMaxPercent = int64(60)
	// And the most it can take off an escape. Kept below the pressure figure
	// because a fugitive who cannot run is a cutscene, not a game: even
	// carrying the worst of it, evading still works, it just costs more
	// attempts.
	trailEscapeMaxPercent = int64(40)
)

// carriedTrailTx is how strongly a cultivator's own goods mark them: the
// strongest tracking on anything they are still carrying or wearing.
//
// Strongest rather than the sum, because a trail is followed, not weighed - one
// branded relic is what a hunter walks towards, and a sack of faintly warm
// herbs is not twelve times that. Equipped items count: `equipment.bind` takes
// an item out of `inventory`, and a trail read off the bags alone would go cold
// the moment its owner put the thing on, which is the wrong way round.
func carriedTrailTx(conn *storage.Conn, userID int64) (int64, error) {
	res, err := conn.Execute(`SELECT MAX(strength) AS trail FROM (
            SELECT p.tracking_strength AS strength FROM item_provenance p
              JOIN inventory i ON i.user_id=p.user_id AND i.item_id=p.item_id AND i.quantity>0
             WHERE p.user_id=?
            UNION ALL
            SELECT p.tracking_strength AS strength FROM item_provenance p
              JOIN equipment_instances e ON e.user_id=p.user_id AND e.item_id=p.item_id
             WHERE p.user_id=?)`, []any{userID, userID})
	if err != nil {
		return 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, nil
	}
	return clamp(i64(row["trail"]), 0, 100), nil
}

// CarriedTrail is carriedTrailTx for the simulation's pursuit tick, which
// advances a hunter from outside this package. The caller owns the
// transaction, as it does for every other game rule it reaches into.
func CarriedTrail(conn *storage.Conn, userID int64) (int64, error) {
	return carriedTrailTx(conn, userID)
}

// TrailPressureBonus is trailPressureBonus for the same caller.
func TrailPressureBonus(step, trail int64) int64 { return trailPressureBonus(step, trail) }

// trailPressureBonus is the extra ground a hunter makes up this step because
// the quarry is carrying something that can be followed.
func trailPressureBonus(gain, trail int64) int64 {
	if trail <= 0 || gain <= 0 {
		return 0
	}
	return gain * clamp(trail, 0, 100) * trailPressureMaxPercent / 10000
}

// trailEscapePenalty is what the same goods cost an attempt to shake the
// pursuit. The floor is the point: an evade always makes *some* progress, so
// carrying the worst of it lengthens a chase rather than ending it.
func trailEscapePenalty(gain, trail int64) int64 {
	if trail <= 0 || gain <= 0 {
		return 0
	}
	penalty := gain * clamp(trail, 0, 100) * trailEscapeMaxPercent / 10000
	if penalty >= gain {
		penalty = gain - 1
	}
	if penalty < 0 {
		return 0
	}
	return penalty
}

// trailWord is how a pursuit card says what is giving the quarry away, in the
// same register `authenticityWord` reports a forgery.
func trailWord(trail int64) string {
	switch {
	case trail <= 0:
		return "nothing on you is marked"
	case trail < 20:
		return "something on you is faintly warm"
	case trail < 45:
		return "something you carry is being followed"
	case trail < 70:
		return "what you took still has a seal on it"
	default:
		return "what you carry is a signal fire"
	}
}
