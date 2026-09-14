package simulation

// The world should already have had a past when the player arrives
// (v1.0.0-rc.24).
//
// Bootstrap wrote all 574 catalogue NPCs as 'single', with no spouse, no
// children, and an age between eight and thirty-three percent of the way
// through their span. So a freshly created world was five hundred and
// seventy-four strangers, not one of whom had ever married anybody, not one
// of whom had a child, and not one of whom was near enough to the end of a
// life for the first funeral to fall inside a player's first year.
//
// The courtship tick can build the first of those from nothing, but it takes
// game-years to do it, and a world that opens with no grandparents and no
// children in it does not read as a place that was there before the player
// was. The same goes for the old: `npc_life` has always known how to bury
// people and had nobody to bury.
//
// Everything here writes columns that already existed and had only ever been
// written with their defaults, and all of it is keyed off `hash64` the way
// the rest of bootstrap is, so the same content makes the same world twice.

import (
	"fmt"
	"sort"
	"strings"

	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
)

const (
	// Households seeded at creation. Deliberately short of half: a town
	// where everybody is married reads as odd as one where nobody is.
	householdShare    = 38
	householdMinAge   = 24
	householdRealmGap = 3

	// Children. The gap is what keeps a twenty-four-year-old from being
	// seeded as somebody's mother at six.
	householdMaxKids   = 3
	householdChildMin  = 2
	householdParentGap = 18

	// The old. `BootstrapAge` puts nobody past a third of their span, so
	// this share is what gives the world elders at all - people with a rank,
	// a career behind them and not much time left in front.
	elderShare       = 9
	elderFloorPct    = 86
	elderSpreadPct   = 11
	elderHealthFloor = 42
)

// bootstrapElderly picks the share who begin near the end. It reads its own
// slice of the seed so it does not correlate with wealth, ambition or age.
func bootstrapElderly(seed uint64) bool {
	return (seed/17)%100 < elderShare
}

// bootstrapElderAge walks an NPC most of the way to the end of their span, and
// takes some of their health with it.
//
// The number that matters is the realm ceiling, not the natural span:
// `Evaluate` adds the cultivation bonus on top, so an NPC actually dies at the
// ceiling. Returns false for the ageless, who have no end to be near.
func bootstrapElderAge(realmIndex, phase, natural int64, seed uint64) (int64, int64, bool) {
	ceiling := lifespanmodel.RealmCeiling(realmIndex, phase, natural)
	if ceiling == nil {
		return 0, 0, false
	}
	pct := int64(elderFloorPct) + int64((seed/23)%uint64(elderSpreadPct))
	age := *ceiling * pct / 100
	if age < lifespanmodel.DefaultStartingYears {
		age = lifespanmodel.DefaultStartingYears
	}
	return age, elderHealthFloor + int64((seed/29)%26), true
}

// seedHouseholds marries a share of the world's people to each other and gives
// them the children they would already have had.
//
// Pairs are found the way `npc_romance` finds them - anyone within reach, not
// only anyone standing on the same tile - because the catalogue puts each NPC
// at their work rather than at their home, and a married couple in a city are
// two people at two different addresses in it.
func (r *Runner) seedHouseholds(conn *storage.Conn, gameMinute int64, out *BootstrapResult) error {
	if !simTableExists(conn, "npc_life_state") || !simTableExists(conn, "npc_social_relations") {
		return nil
	}
	now := nowFloat()
	res, err := conn.Execute(`SELECT c.npc_name,c.current_location,c.world_name,c.realm_index,
            l.birth_game_minute,l.age_at_creation_years
        FROM npc_civilization_state c
        JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.status='alive' AND l.relationship_status='single'
        ORDER BY c.npc_name`, nil)
	if err != nil {
		return err
	}
	people := make([]romanceCandidate, 0, len(res.Rows))
	atLocation := map[string][]romanceCandidate{}
	for _, row := range res.Rows {
		person := romanceCandidate{
			name: fmt.Sprint(row[0]), location: fmt.Sprint(row[1]),
			world: fmt.Sprint(row[2]), realm: i64(row[3]),
			age: lifespanmodel.AgeYears(gameMinute, i64(row[4]), i64(row[5])),
		}
		if person.age < householdMinAge {
			continue
		}
		people = append(people, person)
		atLocation[person.location] = append(atLocation[person.location], person)
	}

	taken := map[string]bool{}
	for _, one := range people {
		if taken[one.name] {
			continue
		}
		other, ok := r.seedPartnerFor(one, atLocation, taken)
		if !ok {
			continue
		}
		if hash64("household", one.name, other.name)%100 >= householdShare {
			// Not married, but not reconsidered either: without this they
			// would be offered to every later candidate in turn until one
			// of the rolls landed, which is not a share, it is a queue.
			taken[one.name] = true
			continue
		}
		if err := r.marryPair(conn, one.name, other.name, gameMinute, now); err != nil {
			return err
		}
		taken[one.name], taken[other.name] = true, true
		out.NPCHouseholdsSeeded++

		born, err := r.seedChildren(conn, one, other, gameMinute, now)
		if err != nil {
			return err
		}
		out.NPCChildrenSeeded += born
	}
	return nil
}

// seedPartnerFor is the same compatibility the courtship uses, without the
// scoring: at world creation there is no history to prefer one match over
// another, so the first plausible one in name order is the honest choice.
func (r *Runner) seedPartnerFor(
	one romanceCandidate, atLocation map[string][]romanceCandidate, taken map[string]bool,
) (romanceCandidate, bool) {
	reach := r.romanceReach(one.location, one.world, one.realm)
	places := make([]string, 0, len(reach))
	for place := range reach {
		places = append(places, place)
	}
	sort.Strings(places)
	for _, place := range places {
		for _, other := range atLocation[place] {
			if other.name == one.name || taken[other.name] {
				continue
			}
			gap := one.realm - other.realm
			if gap < 0 {
				gap = -gap
			}
			if gap > householdRealmGap {
				continue
			}
			if !romanceAgesAgree(one.age, other.age) {
				continue
			}
			return other, true
		}
	}
	return romanceCandidate{}, false
}

// seedChildren gives a seeded couple the children their ages allow. A child is
// at least two years old and at least eighteen years younger than the younger
// parent, so nobody is seeded as a mother at six.
func (r *Runner) seedChildren(conn *storage.Conn, a, b romanceCandidate, gameMinute int64, now float64) (int64, error) {
	if !simTableExists(conn, "npc_descendants") {
		return 0, nil
	}
	youngest := a.age
	if b.age < youngest {
		youngest = b.age
	}
	span := int64(youngest) - householdParentGap - householdChildMin
	if span < 0 {
		return 0, nil
	}
	want := int64(hash64("household-children", a.name, b.name) % uint64(householdMaxKids+1))
	born := int64(0)
	for nth := int64(0); nth < want; nth++ {
		name, err := r.seededChildName(conn, a.name, nth)
		if err != nil {
			return born, err
		}
		if name == "" {
			break
		}
		age := householdChildMin + int64(hash64("child-age", a.name, b.name, fmt.Sprint(nth))%uint64(span+1))
		root := "Mortal Root"
		if len(r.World.Roots) > 0 {
			root = r.World.Roots[hash64("child-root", name)%uint64(len(r.World.Roots))]
		}
		gender := []string{"male", "female"}[hash64("child-gender", name)%2]
		if _, err := conn.Execute(`INSERT INTO npc_descendants
            (child_name,parent_a,parent_b,birth_game_minute,gender,spiritual_root,realm_index,phase,status,generated_as_npc,created_at,updated_at)
            VALUES(?,?,?,?,?,?,0,1,'alive',0,?,?) ON CONFLICT(child_name) DO NOTHING`,
			[]any{name, a.name, b.name, max64(0, gameMinute-age*minutesPerYear), gender, root, now, now}); err != nil {
			return born, err
		}
		born++
	}
	if born > 0 {
		if _, err := conn.Execute(`UPDATE npc_life_state SET children_count=children_count+?,updated_at=? WHERE npc_name IN (?,?)`,
			[]any{born, now, a.name, b.name}); err != nil {
			return born, err
		}
	}
	return born, nil
}

// seededChildName is `freeChildName`'s deterministic twin. It walks the same
// name pool looking for one nobody carries, but starts from a hash instead of
// a die, so a world bootstrapped twice from the same content gets the same
// children rather than a different family each time.
func (r *Runner) seededChildName(conn *storage.Conn, parent string, nth int64) (string, error) {
	surname := parent
	if parts := strings.Fields(parent); len(parts) > 0 {
		surname = parts[0]
	}
	pool := r.World.EventSites.NamePool
	if len(pool) == 0 {
		return "", nil
	}
	start := int(hash64("child-name", parent, fmt.Sprint(nth)) % uint64(len(pool)))
	for i := 0; i < len(pool); i++ {
		entry := pool[(start+i)%len(pool)]
		given := entry
		if parts := strings.Fields(entry); len(parts) > 1 {
			given = strings.Join(parts[1:], " ")
		}
		candidate := surname + " " + given
		if _, taken := r.World.NPCs[candidate]; taken {
			continue
		}
		res, err := conn.Execute(`SELECT 1 FROM npc_descendants WHERE child_name=?
            UNION ALL SELECT 1 FROM npc_civilization_state WHERE npc_name=? LIMIT 1`,
			[]any{candidate, candidate})
		if err != nil {
			return "", err
		}
		if len(res.Rows) == 0 {
			return candidate, nil
		}
	}
	return "", nil
}
