package game

import (
	"fmt"
	"strings"

	"xianxia/core/internal/worlddata"
)

// Where a fight may not begin, and where it may not reach (v1.0.6).
//
// `content/world.json` says this twice, in two vocabularies, and until v1.0.6
// only one of them was read anywhere.
//
// **A safe zone is not the wilds.** `locations.<name>.safe_zone` is true on 446
// of 477 places - every town, gate, shop, shrine and hall - and false on the 31
// that are hunting grounds, ruins, open country, and Greenriver Town, the
// starting town, which is deliberately rough. So it is a statement about
// settlement rather than about sanctuary, and what it buys is that nobody may
// *start* a fight there. That rule was held for PvP in `pvp_invariants.go`
// ("local formations suppress PvP here") and for PvE in
// `app/bot/commands/battle.py` - in the bot, and nowhere else:
// `combat_actions.go` named `SafeZone` zero times. A bound that lives in the
// client is not a bound (v1.0.0-rc.48), for the fourth time in this tree, and
// the reason it stayed invisible is that the neighbouring kind of violence
// *was* engine-held.
//
// **A protected interior is a sanctuary, and that is a different claim.** All
// 48 auction houses carry `protected_interior: true` and every one of their
// descriptions says why - "protected by ancient formations and discreet hidden
// experts", "a jade array that suppresses every technique on the floor", "a
// blow struck on this floor is a blow against heaven". That is stronger than
// what the other 398 settled places claim, and it had its own field, which
// nothing read. So the one kind of place in this game that genuinely claims to
// suppress violence suppressed none of the violence that could actually reach
// a player standing in it: `advanceHunters` raised pressure, engaged and
// **captured** a fugitive with no reference to `characters.location` anywhere
// in it.
//
// The two are deliberately separate predicates rather than one, because they
// stop different things: a safe zone stops what a player starts, and a
// sanctuary also stops what the world does to them. Merging them would hand
// 446 locations the auction floor's guarantee, which would not be a sanctuary
// - it would be the end of the bounty system, since players live in towns.
//
// **What a safe zone refuses is a fight somebody chose to start**, and that is
// the whole rule - which is why two things that look like exceptions are not.
// A world event battle in a town is allowed (rc.49's asymmetry: being caught in
// something is not being handed it), and so is the auction door incident, which
// `auctionLeaveAction` stands at `EntranceLocation` - a safe zone for 47 of the
// 48 houses, Greenriver Town being the one rough entrance. Neither is a fight
// the player picked. A gate that refused them would delete the event battle in
// 446 of 477 places and the door risk in 47 of 48, which would not be enforcing
// the rule; it would be deleting two systems that the content describes.
//
// `door_rule` was the third statement and is retired rather than read. It said
// "the protection ends at the doors", which is the second half of the same
// sentence `protected_interior` opens; all 48 houses set it `true` and no
// house's prose can differ; and the engine already ends the protection at the
// door by creating the door incident's battle at `EntranceLocation`, outside.
// A switch content cannot turn off is not a switch.

// violenceSuppressed answers whether a place refuses a fight somebody chose to
// start, and the sentence the refusal is shown as.
func violenceSuppressed(catalog worlddata.Catalog, location string) (bool, string) {
	loc, ok := catalog.Locations[strings.TrimSpace(location)]
	if !ok || !loc.SafeZone {
		return false, ""
	}
	if house, sanctuary := LocationIsSanctuary(catalog, location); sanctuary {
		return true, fmt.Sprintf("violence is suppressed on the floor of %s; its protection ends at the doors", house)
	}
	return true, "local formations suppress violence here"
}

// LocationIsSanctuary names the auction house whose protected interior this
// location is, if it is one.
//
// It reads the location's own `auction_house` pointer rather than scanning the
// houses for one whose `location` matches, because the pointer is the cheaper
// read of the same fact and this runs inside the tick's per-pursuit loop.
// Exported for `internal/simulation`, which holds the same catalogue in
// `Runner.World` precisely so a tick step can reach a rule in `game` instead of
// keeping a second copy of it - `game.WalletDeltaTx` is the precedent.
func LocationIsSanctuary(catalog worlddata.Catalog, location string) (string, bool) {
	loc, ok := catalog.Locations[strings.TrimSpace(location)]
	if !ok {
		return "", false
	}
	houseID := strings.TrimSpace(loc.AuctionHouse)
	if houseID == "" {
		return "", false
	}
	house, ok := catalog.AuctionHouses[houseID]
	if !ok || !house.ProtectedInterior {
		return "", false
	}
	name := strings.TrimSpace(house.Name)
	if name == "" {
		name = houseID
	}
	return name, true
}
