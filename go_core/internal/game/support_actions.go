package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
	// The weekend the gift doubles on is measured in the operator's own zone,
	// and neither the engine image nor a CI container carries an OS timezone
	// database. Embedding the IANA data is what makes time.LoadLocation work
	// in both; without it the window would silently fall back to UTC and open
	// an hour or two off local midnight.
	_ "time/tzdata"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The patron's gift is the server's own, and nothing outside the world is
// involved in it: no listing site, no API, no inbound endpoint. It began as a
// reward for voting on a list and spent a while shaped around one, which is
// why the operations are still called `vote`. What it actually is now is a
// claim a cultivator may make once a cooldown, and the engine owns all three
// parts that matter - the cadence, the size of the gift, and the receipt.
//
// Because nothing external gates it, there is nothing to verify and nothing to
// take on trust: every claim is honest by construction, and the cooldown is
// the only thing that decides how often one may be made.
const (
	// Twice a day. While the gift was tied to a listing this number had to be
	// whatever that site reset a vote on; now that it is the server's own, it
	// is a balance dial and nothing else - short enough that logging in twice
	// is worth it, long enough that the gift stays a gift rather than income.
	// It is stated to players in app/bot/commands/support.py, and a test holds
	// the two together.
	supportVoteCooldownSeconds int64 = 12 * 60 * 60
	// The gift is the cultivator's, not a flat number. A flat 15 was wrong at
	// both ends: shop lines run 3-59 in the Mortal World's low-grade stone and
	// 6-468 in the Celestial World's, so one number is a windfall to a beginner
	// and an insult to an ancestor. Fifteen, and five more for every realm
	// climbed inside the current world, keeps it a few cheap wares at every
	// tier - still far under what playing pays (a commission 30-150, a boss
	// 120-420) - and never less than the flat fifteen it replaces, so nobody's
	// gift shrank the day this shipped.
	supportVoteBaseReward  int64 = 15
	supportVoteRewardStep  int64 = 5
	supportVoteMaxDepth    int64 = 7
	supportVoteWeekendMult int64 = 2
	supportVoteAction            = "support_vote"
	// The operator's own weekend, not UTC: the bonus should open at local
	// midnight all year, which means a zone that follows DST rather than a
	// fixed offset. cmd/xianxia-core blank-imports time/tzdata so this
	// resolves inside a container with no OS timezone database.
	supportWeekendZoneName = "Europe/Amsterdam"
)

var (
	supportWeekendZoneOnce sync.Once
	supportWeekendZone     *time.Location
)

// supportWeekendLocation is the zone the weekend is measured in. A missing
// timezone database must not take the gift down with it, so an unresolvable
// zone falls back to UTC - the bonus then runs an hour or two off local
// midnight, which is wrong but harmless, where an error would refuse a claim.
func supportWeekendLocation() *time.Location {
	supportWeekendZoneOnce.Do(func() {
		loc, err := time.LoadLocation(supportWeekendZoneName)
		if err != nil || loc == nil {
			supportWeekendZone = time.UTC
			return
		}
		supportWeekendZone = loc
	})
	return supportWeekendZone
}

// supportWeekendWindow answers whether `at` falls in a bonus weekend, and the
// window it is in or the next one coming. The window is Friday 00:00 through
// Sunday 23:59:59 in the operator's zone; the returned close is the exclusive
// Monday 00:00, which is what a countdown wants. Pure over its argument, so
// the tests pin instants rather than the wall clock.
func supportWeekendWindow(at time.Time) (bool, time.Time, time.Time) {
	loc := supportWeekendLocation()
	local := at.In(loc)
	midnight := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, loc)
	var opens time.Time
	switch local.Weekday() {
	case time.Friday:
		opens = midnight
	case time.Saturday:
		opens = midnight.AddDate(0, 0, -1)
	case time.Sunday:
		opens = midnight.AddDate(0, 0, -2)
	default:
		// Monday..Thursday: name the window that is coming, so a caller can
		// say when the next one opens without repeating this arithmetic.
		ahead := (int(time.Friday) - int(local.Weekday()) + 7) % 7
		opens = midnight.AddDate(0, 0, ahead)
		return false, opens, opens.AddDate(0, 0, 3)
	}
	// AddDate works on calendar fields, so three days from a local Friday
	// midnight is the local Monday midnight even across a DST change.
	return true, opens, opens.AddDate(0, 0, 3)
}

// supportVoteWeekendMultiplier is what the whole gift is multiplied by.
func supportVoteWeekendMultiplier(at time.Time) int64 {
	if weekend, _, _ := supportWeekendWindow(at); weekend {
		return supportVoteWeekendMult
	}
	return 1
}

type supportVotePayload struct {
	GameMinute int64 `json:"game_minute"`
}

// supportGift is what a claim will pay. The claim and the status read both
// compute it from this one function, because the command prints the number
// before the player presses and the two disagreeing would be a lie.
type supportGift struct {
	World      string
	Currency   string
	Amount     int64
	Depth      int64
	ItemID     string
	ItemQty    int64
	Weekend    bool
	Multiplier int64
	WindowEnds time.Time
}

// practisedProfessions names the professions a cultivator has actually worked
// at, most practised first, so a smith is given ore whatever they cultivate.
func practisedProfessions(conn *storage.Conn, userID int64) ([]string, error) {
	res, err := conn.Execute(
		`SELECT profession FROM profession_progress WHERE user_id=? AND (level>0 OR xp>0) ORDER BY level DESC, xp DESC, profession ASC`,
		[]any{userID},
	)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(res.Rows))
	for _, row := range res.Rows {
		if len(row) == 0 {
			continue
		}
		if name := strings.TrimSpace(fmt.Sprint(row[0])); name != "" {
			out = append(out, name)
		}
	}
	return out, nil
}

// worldTierIndex is which of the four worlds this is, 0 for the Mortal World
// through 3 for the Celestial. Taken from the same floor the depth term uses,
// so the size of a gift and the world it is paid in never disagree.
func worldTierIndex(catalog worlddata.Catalog, world string) int64 {
	index := worldMinRealm(catalog, world) / 8
	if index < 0 {
		index = 0
	}
	if index > 3 {
		index = 3
	}
	return index
}

func supportVoteGift(conn *storage.Conn, catalog worlddata.Catalog, c mechanicsCharacter, userID int64, at time.Time) (supportGift, error) {
	world := currentWorld(c, catalog)
	depth := c.RealmIndex - worldMinRealm(catalog, world)
	if depth < 0 {
		depth = 0
	}
	if depth > supportVoteMaxDepth {
		depth = supportVoteMaxDepth
	}
	weekend, _, closes := supportWeekendWindow(at)
	multiplier := int64(1)
	if weekend {
		multiplier = supportVoteWeekendMult
	}
	gift := supportGift{
		World:      world,
		Currency:   worldBaseCurrency(catalog, world),
		Amount:     (supportVoteBaseReward + depth*supportVoteRewardStep) * multiplier,
		Depth:      depth,
		Weekend:    weekend,
		Multiplier: multiplier,
		WindowEnds: closes,
	}
	professions, err := practisedProfessions(conn, userID)
	if err != nil {
		return supportGift{}, err
	}
	ref := catalog.PatronGift.Material(professions, c.Path)
	itemID := catalog.EventSites.Material(world, ref)
	// The same guard SpawnWorldEventNodes uses: a tier material this world
	// does not name, or one the item catalogue does not carry, would be a
	// phantom in the bag. Drop the item and keep the stones.
	if _, _, ok := itemDef(catalog, itemID); itemID != "" && ok {
		gift.ItemID = itemID
		// "@herb" and "@ore" name a richer item every world (spirit_herb 2 ->
		// heavenpetal_herb 20), so one of them is already tier-appropriate.
		// "@core" is beast_core in all four - the one material every world's
		// recipes and shops still trade in, which is why it cannot be split
		// per tier without rewriting them - so it is made worth the same by
		// arriving in greater number: one in the Mortal World, four in the
		// Celestial, which is the herb's own 2-to-20 ladder in another shape.
		quantity := int64(1)
		if catalog.PatronGift.Untiered(ref) {
			quantity = worldTierIndex(catalog, world) + 1
		}
		gift.ItemQty = quantity * multiplier
	}
	return gift, nil
}

func supportVoteClaimAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p supportVotePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	at := time.Now()
	now := float64(at.UnixNano()) / 1e9
	remaining, err := cooldownRemaining(conn, userID, supportVoteAction, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	gift, err := supportVoteGift(conn, catalog, c, userID, at)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// walletDeltaTx rather than a raw upsert: it guards the int64 overflow and
	// mirrors low_spirit_stone into characters.spirit_stones, which the sheet
	// and every price check read.
	balance, err := walletDeltaTx(conn, catalog, userID, gift.Currency, gift.Amount, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if gift.ItemID != "" && gift.ItemQty > 0 {
		if _, err = conn.Execute(
			`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
			 ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`,
			[]any{userID, gift.ItemID, gift.ItemQty},
		); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if err = setCooldown(conn, userID, supportVoteAction, supportVoteCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"world": gift.World, "currency": gift.Currency,
		"amount": gift.Amount, "balance": balance, "depth": gift.Depth,
		"item_id": gift.ItemID, "item_quantity": gift.ItemQty,
		"weekend": gift.Weekend, "multiplier": gift.Multiplier,
		"next_claim_seconds": supportVoteCooldownSeconds,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "support", EventType: "support.vote_claim", EntityType: "character",
			EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result,
		},
	}, nil
}

// supportVoteStatusQuery answers the cooldown and what the claim would pay,
// without granting anything, so the command can draw "claimable now" or "in
// 4h12m" and the real gift before the player has pressed. It computes the
// gift through supportVoteGift for that reason - a number advertised here and
// a different one paid there is the one bug this query can cause.
func supportVoteStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	if userID <= 0 {
		return nil, errors.New("actor_id must be positive")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	at := time.Now()
	now := float64(at.UnixNano()) / 1e9
	remaining, err := cooldownRemaining(conn, userID, supportVoteAction, now)
	if err != nil {
		return nil, err
	}
	gift, err := supportVoteGift(conn, catalog, c, userID, at)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"claimable":         remaining <= 0,
		"remaining_seconds": remaining,
		"cooldown_seconds":  supportVoteCooldownSeconds,
		"amount":            gift.Amount,
		"currency":          gift.Currency,
		"world":             gift.World,
		"depth":             gift.Depth,
		"item_id":           gift.ItemID,
		"item_quantity":     gift.ItemQty,
		"weekend":           gift.Weekend,
		"multiplier":        gift.Multiplier,
		"weekend_ends_unix": gift.WindowEnds.Unix(),
	}, nil
}

// supportWeekendQuery is world-level: the weekend belongs to the server, not
// to a cultivator, and the bot's announcement worker has no actor to ask for.
// Keeping the window here rather than recomputing it in Python is the point -
// a second copy of a rule is how two surfaces come to disagree.
func supportWeekendQuery() map[string]any {
	at := time.Now()
	weekend, opens, closes := supportWeekendWindow(at)
	multiplier := int64(1)
	if weekend {
		multiplier = supportVoteWeekendMult
	}
	return map[string]any{
		"weekend":     weekend,
		"multiplier":  multiplier,
		"opens_unix":  opens.Unix(),
		"closes_unix": closes.Unix(),
		// The Friday the window opens on, in the operator's zone: stable for
		// the whole window and different for the next one, which is exactly
		// what an "already announced this one" marker needs.
		"window_key": opens.Format("2006-01-02"),
		"zone":       supportWeekendLocation().String(),
	}
}
