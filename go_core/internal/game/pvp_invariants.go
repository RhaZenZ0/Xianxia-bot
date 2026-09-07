package game

// PvP invariants, checked at every gate rather than only the first
// (v0.22.3, review finding #5).
//
// A duel is only legitimate while four things hold: both cultivators alive,
// both in the same place, that place not a safe zone, and the two of them not
// the same person. All four were checked when a challenge was *created* and
// none of them when it was accepted or acted on - and a challenge lives for
// five minutes, which is plenty of time to walk somewhere else. So:
//
//	A and B stand together outside a safe zone
//	A challenges B
//	B walks into the city
//	B accepts
//	the duel begins between two people in different places, one of them
//	standing inside formations that are supposed to make this impossible
//
// The fix is one validator called at all three gates. The interesting half is
// not the check, it is what to do when it fails *during* a match: returning an
// error every time would strand the duel as permanently active, which is worse
// than the hole it closes. So a match that has become illegitimate is
// **resolved**, deterministically, by the rules in pvpBreach.

import (
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// pvpBreach names the way a duel's preconditions stopped holding, and carries
// the resolution for a match already in progress.
type pvpBreach struct {
	Reason string
	// Forfeiter is the participant whose own change broke the duel, and who
	// therefore loses it. 0 when nobody is at fault (both dead, both moved
	// into a safe zone together) and the match is simply void.
	Forfeiter int64
	// Winner is the other one, 0 for a void.
	Winner int64
}

func (b *pvpBreach) ok() bool { return b == nil }

// checkPvpParticipants is the single definition of "these two may duel".
//
// `requiredLocation` is empty when the pair only has to be *together* (a
// challenge being made or accepted) and set to the match's own location when a
// duel is already under way - a duel does not follow its participants around,
// so leaving where it started is leaving the duel.
func checkPvpParticipants(conn *storage.Conn, catalog worlddata.Catalog, actorID, opponentID int64, requiredLocation string) (*pvpBreach, error) {
	if actorID == opponentID {
		return &pvpBreach{Reason: "a cultivator cannot duel themselves"}, nil
	}
	actor, err := loadMechanicsCharacter(conn, actorID)
	if err != nil {
		return &pvpBreach{Reason: "a participant is unavailable", Forfeiter: actorID, Winner: opponentID}, nil
	}
	opponent, err := loadMechanicsCharacter(conn, opponentID)
	if err != nil {
		return &pvpBreach{Reason: "a participant is unavailable", Forfeiter: opponentID, Winner: actorID}, nil
	}

	actorAlive := actor.LifeStatus == "alive"
	opponentAlive := opponent.LifeStatus == "alive"
	switch {
	case !actorAlive && !opponentAlive:
		return &pvpBreach{Reason: "neither cultivator is alive"}, nil
	case !actorAlive:
		return &pvpBreach{Reason: "a cultivator is no longer alive", Forfeiter: actorID, Winner: opponentID}, nil
	case !opponentAlive:
		return &pvpBreach{Reason: "a cultivator is no longer alive", Forfeiter: opponentID, Winner: actorID}, nil
	}

	if requiredLocation != "" {
		// A duel in progress. Whoever left the ground it was fought on is the
		// one who broke it, which also removes the obvious exploit: walking
		// away from a duel you are losing must not be cheaper than losing it.
		actorHere := actor.Location == requiredLocation
		opponentHere := opponent.Location == requiredLocation
		switch {
		case !actorHere && !opponentHere:
			return &pvpBreach{Reason: "both cultivators have left the duel"}, nil
		case !actorHere:
			return &pvpBreach{Reason: "a cultivator has left the duel", Forfeiter: actorID, Winner: opponentID}, nil
		case !opponentHere:
			return &pvpBreach{Reason: "a cultivator has left the duel", Forfeiter: opponentID, Winner: actorID}, nil
		}
	} else if actor.Location != opponent.Location {
		return &pvpBreach{Reason: "both cultivators must be at the same location"}, nil
	}

	if loc, ok := catalog.Locations[actor.Location]; ok && loc.SafeZone {
		// Nobody is at fault for standing somewhere peaceful.
		return &pvpBreach{Reason: "local formations suppress PvP here"}, nil
	}
	return nil, nil
}

// finishBreachedMatch ends a duel whose preconditions stopped holding. A
// forfeit has a winner and no reputation on either side - a duel that ends
// because someone walked off is not an honourable victory and should not pay
// like one; a void has neither.
func finishBreachedMatch(conn *storage.Conn, matchID int64, breach *pvpBreach, now float64) (map[string]any, error) {
	var winner any
	if breach.Winner != 0 {
		winner = breach.Winner
	}
	if _, err := conn.Execute(
		`UPDATE pvp_matches SET status='finished',winner_user_id=?,version=version+1,updated_at=? WHERE match_id=?`,
		[]any{winner, now, matchID}); err != nil {
		return nil, err
	}
	updated, err := pvpMatchRow(conn, matchID)
	if err != nil {
		return nil, err
	}
	out := map[string]any{
		"match_id": matchID, "finished": true, "breach": breach.Reason,
		"reputation_awarded": false, "match": updated,
	}
	if breach.Winner != 0 {
		out["winner_user_id"] = breach.Winner
		out["forfeited_by"] = breach.Forfeiter
	} else {
		out["voided"] = true
	}
	return out, nil
}

// pvpMatchLocation is where a duel is being fought, or "" for a match started
// before the column existed - in which case the location rule is skipped
// rather than failing every act on an old row.
func pvpMatchLocation(match map[string]any) string {
	raw, ok := match["location"]
	if !ok || raw == nil {
		return ""
	}
	return fmt.Sprint(raw)
}
