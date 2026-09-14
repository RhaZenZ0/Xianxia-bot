package simulation

// Courtship, and why the world was not reproducing (v1.0.0-rc.23).
//
// `npcLife` used to marry people by walking one globally sorted list of
// singles two at a time - slots (0,1), (2,3), (4,5) - and keeping a pair only
// if both happened to land on the same `current_location`. Measured against
// the real catalogue that is fifteen usable pairs out of the two hundred and
// forty that exist, because the list is ordered by location and a place with
// an odd number of singles bleeds its last one into the next place, where the
// location check throws it away. Six percent of fifteen is 0.9 weddings a
// tick, and `npc_life` ticks every seven game days: four marriages a month in
// a world of five hundred and seventy-four people. Childbirth then had almost
// no couples to work with, so a whole first month produced less than one
// child anywhere in the world.
//
// The deeper reason is geography. Four hundred and seventy-seven places hold
// those NPCs and three hundred and ninety-two of them are the only person
// standing where they stand; the median place holds exactly one. Under a rule
// that both must be at the same location, those three hundred and ninety-two
// can never marry anybody, ever. Counting a whole city instead - which is what
// `game.WhereAnNPCCanWalk` already models, district to city and back out
// again - drops that number from 392 to 56.
//
// So: reach instead of coincidence, a scored match instead of adjacency in a
// sorted list, and a courtship that has to hold together for a few ticks
// instead of a coin flip that marries two strangers who have never met. None
// of it needs a new column. `npc_social_relations` already carries affinity,
// trust and a free-text `relation_type`, and `relationship_status` is free
// text too - it has simply only ever held 'single' and 'married'.
//
// There is deliberately no gender rule. Not one of the 574 catalogue NPCs
// carries a gender field, so a rule would not be reading content, it would be
// inventing it.

import (
	"fmt"
	"math"
	"sort"

	"xianxia/core/internal/gamerng"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
)

const (
	// A courtship begins when two people who can reach each other take to
	// each other. It is not a wedding; it is the reason a wedding later
	// makes sense.
	courtshipChance   = 30
	courtshipStartCap = 8
	courtshipAffinity = 20
	courtshipTrust    = 15

	// What it is worth to go on seeing each other, and what it costs not to.
	// Travel moves people, and a courtship that drifts apart should cool
	// rather than wait forever.
	courtshipGain  = 14
	courtshipDrift = 7

	// Where it ends up, in both directions.
	courtshipMarryAt = 70
	courtshipWedCap  = 6

	// Who is a plausible match. A Qi Condensation farmer and a Nascent Soul
	// elder is a story, not a marriage. The age gap is generous because
	// cultivators live a long time. The grudge ceiling is what stops the
	// victim of a robbery courting the person who robbed them - which only
	// became possible at all once something in this engine finally raised
	// that column.
	courtshipRealmGap  = 3
	courtshipAgeGap    = 60
	courtshipGrudgeMax = 15
	// Sixty years is a lifetime to a mortal and a rounding error to a
	// Nascent Soul elder, so the absolute gap is a floor and the real test
	// is proportional. Without this, two four-hundred-year-old cultivators
	// eighty years apart are refused each other while two mortals fifty
	// years apart are waved through.
	romanceAgeFraction = 0.35

	// A widow may marry again, and it is harder in both of the ways that
	// matter. Nobody is courted the week after a funeral, so there is a
	// mourning period first; and afterwards the roll is colder, because the
	// second time is not the first time and a town knows it.
	mourningDays     = 45
	widowChanceShare = 45

	// A marriage that is a treaty rather than an affection. Bootstrap has
	// always written clan `marriage_pact` rows and nothing has ever made one
	// since, so the idea existed in this world and only ever described its
	// own past. Two sects on speaking terms and short of an alliance have an
	// obvious use for a wedding, and the people in it are not asked.
	politicalChance       = 25
	politicalCap          = 2
	politicalMinInfluence = 55
	politicalScoreFloor   = -15
	politicalScoreCeiling = 70
	politicalScoreGain    = 12
	// Arranged, so the pair begin with standing rather than warmth: trust
	// enough to hold a treaty, affection they may or may not find later.
	politicalAffinity = 10
	politicalTrust    = 30
)

// romanceAgesAgree is the age rule, on both the mortal and the cultivator
// scale.
func romanceAgesAgree(a, b float64) bool {
	gap := math.Abs(a - b)
	if gap <= courtshipAgeGap {
		return true
	}
	older := math.Max(a, b)
	return older > 0 && gap <= older*romanceAgeFraction
}

// romanceCandidate is one unattached NPC, in the shape the pairing reads them.
type romanceCandidate struct {
	name     string
	location string
	world    string
	realm    int64
	age      float64
	// widowed is carried because remarriage is not a first marriage: it
	// waits longer and it lands less often.
	widowed  bool
	freeFrom int64
}

// romanceBond is what `npc_social_relations` already says about a pair.
type romanceBond struct {
	relation string
	grudge   int64
}

// romancePairKey orders a pair the way the table's own CHECK(npc_a<npc_b)
// requires, and returns the map key beside it.
func romancePairKey(a, b string) (string, string, string) {
	pair := []string{a, b}
	sort.Strings(pair)
	return pair[0], pair[1], pair[0] + "\x00" + pair[1]
}

// npcRomance is the whole of it, run as one step of the `npc_life` batch.
//
// Courtships already under way move first and new ones begin second, so a
// pair cannot possibly meet and wed inside the same tick - which is the same
// reason `npc_deeds` runs after the feuds rather than before them.
func (r *Runner) npcRomance(conn *storage.Conn, steps, gm int64) (int64, int64, int64, error) {
	if !simTableExists(conn, "npc_social_relations") || !simTableExists(conn, "npc_life_state") {
		return 0, 0, 0, nil
	}
	now := nowFloat()
	wed, ended, err := r.advanceCourtships(conn, gm, now)
	if err != nil {
		return 0, wed, ended, err
	}
	started, err := r.beginCourtships(conn, steps, gm, now)
	if err != nil {
		return started, wed, ended, err
	}
	// Last, so a treaty never takes somebody who was already being courted
	// this tick.
	arranged, err := r.npcPoliticalMarriages(conn, gm, now)
	if err != nil {
		return started, wed + arranged, ended, err
	}
	return started, wed + arranged, ended, nil
}

// npcPoliticalMarriages marries two sects to each other through two of their
// people. It looks for a pair already on speaking terms and short of an
// alliance - too hostile and nobody would send a daughter, too close and there
// is nothing left for a wedding to buy - and takes the most influential
// unattached person each side has.
//
// The couple are not consulted. That is what makes it political, and it is
// why the bond opens with trust rather than affinity.
func (r *Runner) npcPoliticalMarriages(conn *storage.Conn, gm int64, now float64) (int64, error) {
	if !simTableExists(conn, "sect_relations") {
		return 0, nil
	}
	res, err := conn.Execute(`SELECT sect_a,sect_b,relation_score FROM sect_relations
        WHERE relation_score BETWEEN ? AND ?
        ORDER BY relation_score DESC,sect_a,sect_b LIMIT 40`,
		[]any{politicalScoreFloor, politicalScoreCeiling})
	if err != nil {
		return 0, err
	}
	made := int64(0)
	for _, row := range res.Rows {
		if made >= politicalCap {
			break
		}
		sectA, sectB := fmt.Sprint(row[0]), fmt.Sprint(row[1])
		if sectA == "" || sectB == "" || sectA == sectB {
			continue
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return made, err
		}
		if int64(roll) >= politicalChance {
			continue
		}
		bride, err := r.mostInfluentialFree(conn, sectA)
		if err != nil {
			return made, err
		}
		groom, err := r.mostInfluentialFree(conn, sectB)
		if err != nil {
			return made, err
		}
		if bride == "" || groom == "" || bride == groom {
			continue
		}
		if err := r.marryPairAs(conn, bride, groom, true, gm, now); err != nil {
			return made, err
		}
		if _, err := conn.Execute(`UPDATE sect_relations
            SET relation_score=MIN(100,relation_score+?),relation_type='marriage_pact',updated_at=?
            WHERE sect_a=? AND sect_b=?`, []any{politicalScoreGain, now, sectA, sectB}); err != nil {
			return made, err
		}
		made++
	}
	return made, nil
}

// mostInfluentialFree is whoever a sect would actually offer: its weightiest
// unattached member, which is the same person it would send to any other
// negotiation.
func (r *Runner) mostInfluentialFree(conn *storage.Conn, faction string) (string, error) {
	res, err := conn.Execute(`SELECT c.npc_name FROM npc_civilization_state c
        JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.status='alive' AND c.faction=? AND c.influence>=?
          AND l.relationship_status IN ('single','widowed')
        ORDER BY c.influence DESC,c.npc_name LIMIT 1`, []any{faction, politicalMinInfluence})
	if err != nil {
		return "", err
	}
	if len(res.Rows) == 0 {
		return "", nil
	}
	return fmt.Sprint(res.Rows[0][0]), nil
}

// romanceReach is everywhere an NPC could plausibly meet somebody: where they
// are standing, and one step from it. It is `neighbours` - the engine's own
// map rule, which knows a district is part of its city - and not a second
// copy of it kept in this file.
func (r *Runner) romanceReach(location, world string, realm int64) map[string]bool {
	out := map[string]bool{location: true}
	for _, place := range r.neighbours(location, world, realm) {
		out[place] = true
	}
	return out
}

// romanceKin reads who may not court whom. `npc_descendants` is the only
// record this world keeps of a parent and a child, so it is also the only
// thing that can stop a brother marrying his sister once the simulation
// starts producing siblings.
func (r *Runner) romanceKin(conn *storage.Conn) (map[string]map[string]bool, error) {
	parents := map[string]map[string]bool{}
	if !simTableExists(conn, "npc_descendants") {
		return parents, nil
	}
	res, err := conn.Execute(`SELECT child_name,parent_a,parent_b FROM npc_descendants`, nil)
	if err != nil {
		return nil, err
	}
	for _, row := range res.Rows {
		child := fmt.Sprint(row[0])
		set := map[string]bool{}
		for _, parent := range []string{fmt.Sprint(row[1]), fmt.Sprint(row[2])} {
			if parent != "" {
				set[parent] = true
			}
		}
		parents[child] = set
	}
	return parents, nil
}

// romanceAreKin is parent-and-child in either direction, or two children who
// share a parent.
func romanceAreKin(parents map[string]map[string]bool, a, b string) bool {
	if parents[a][b] || parents[b][a] {
		return true
	}
	for parent := range parents[a] {
		if parents[b][parent] {
			return true
		}
	}
	return false
}

// romanceBonds reads every live relation once, so the pairing can check a
// grudge or an existing attachment without a query per candidate pair.
func (r *Runner) romanceBonds(conn *storage.Conn) (map[string]romanceBond, error) {
	bonds := map[string]romanceBond{}
	res, err := conn.Execute(`SELECT npc_a,npc_b,relation_type,grudge FROM npc_social_relations WHERE status='active'`, nil)
	if err != nil {
		return nil, err
	}
	for _, row := range res.Rows {
		_, _, key := romancePairKey(fmt.Sprint(row[0]), fmt.Sprint(row[1]))
		bonds[key] = romanceBond{relation: fmt.Sprint(row[2]), grudge: i64(row[3])}
	}
	return bonds, nil
}

// advanceCourtships moves every courtship already under way: closer if the two
// are still within reach of each other, cooler if the roads have taken one of
// them away. A courtship that runs out of affinity simply ends, and both walk
// away single.
func (r *Runner) advanceCourtships(conn *storage.Conn, gm int64, now float64) (int64, int64, error) {
	res, err := conn.Execute(`SELECT s.npc_a,s.npc_b,s.affinity,
            a.current_location,a.world_name,a.realm_index,b.current_location
        FROM npc_social_relations s
        JOIN npc_civilization_state a ON a.npc_name=s.npc_a
        JOIN npc_civilization_state b ON b.npc_name=s.npc_b
        WHERE s.status='active' AND s.relation_type='courtship'
          AND a.status='alive' AND b.status='alive'
        ORDER BY s.affinity DESC,s.npc_a`, nil)
	if err != nil {
		return 0, 0, err
	}
	wed, ended := int64(0), int64(0)
	for _, row := range res.Rows {
		a, b := fmt.Sprint(row[0]), fmt.Sprint(row[1])
		affinity := i64(row[2])
		reach := r.romanceReach(fmt.Sprint(row[3]), fmt.Sprint(row[4]), i64(row[5]))
		near := reach[fmt.Sprint(row[6])]

		if near {
			affinity += courtshipGain
		} else {
			affinity -= courtshipDrift
		}
		if affinity > 100 {
			affinity = 100
		}

		switch {
		case affinity >= courtshipMarryAt && wed < courtshipWedCap:
			if err := r.marryPair(conn, a, b, gm, now); err != nil {
				return wed, ended, err
			}
			wed++
		case affinity <= 0:
			if err := r.endCourtship(conn, a, b, gm, now); err != nil {
				return wed, ended, err
			}
			ended++
		default:
			if _, err := conn.Execute(`UPDATE npc_social_relations
                SET affinity=?,trust=MIN(100,trust+?),last_interaction_game_minute=?,updated_at=?
                WHERE npc_a=? AND npc_b=?`,
				[]any{affinity, map[bool]int64{true: 4, false: 0}[near], gm, now, a, b}); err != nil {
				return wed, ended, err
			}
		}
	}
	return wed, ended, nil
}

// beginCourtships finds people who can reach each other and have no reason not
// to, and starts something. Candidates are grouped by where they stand, so a
// courter looks only at the handful of places they could actually walk to
// rather than at all five hundred people in the world.
func (r *Runner) beginCourtships(conn *storage.Conn, steps, gm int64, now float64) (int64, error) {
	// A widow may marry again; that is the whole reason widowing sets this
	// column to something the pairing still reads.
	res, err := conn.Execute(`SELECT c.npc_name,c.current_location,c.world_name,c.realm_index,
            l.birth_game_minute,l.age_at_creation_years,l.relationship_status,l.last_social_game_minute
        FROM npc_civilization_state c
        JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.status='alive' AND l.health>0
          AND l.relationship_status IN ('single','widowed')
        ORDER BY c.npc_name LIMIT 1000`, nil)
	if err != nil {
		return 0, err
	}
	mourning := int64(mourningDays) * minutesPerDay
	people := make([]romanceCandidate, 0, len(res.Rows))
	atLocation := map[string][]romanceCandidate{}
	for _, row := range res.Rows {
		person := romanceCandidate{
			name: fmt.Sprint(row[0]), location: fmt.Sprint(row[1]),
			world: fmt.Sprint(row[2]), realm: i64(row[3]),
			age:     lifespanmodel.AgeYears(gm, i64(row[4]), i64(row[5])),
			widowed: fmt.Sprint(row[6]) == "widowed",
		}
		if person.widowed {
			// `last_social_game_minute` is stamped by the widowing itself, so
			// the mourning period needs no column of its own.
			person.freeFrom = i64(row[7]) + mourning
			if gm < person.freeFrom {
				continue
			}
		}
		people = append(people, person)
		atLocation[person.location] = append(atLocation[person.location], person)
	}
	parents, err := r.romanceKin(conn)
	if err != nil {
		return 0, err
	}
	bonds, err := r.romanceBonds(conn)
	if err != nil {
		return 0, err
	}

	// A longer tick is a longer stretch of world time for something to have
	// begun in, the way every other rate in this package scales.
	chance := min64(int64(courtshipChance)*max1(min64(3, steps)), 75)
	taken := map[string]bool{}
	started := int64(0)
	for _, courter := range people {
		if started >= courtshipStartCap {
			break
		}
		if taken[courter.name] {
			continue
		}
		match, ok := r.bestMatch(courter, atLocation, parents, bonds, taken)
		if !ok {
			continue
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return started, err
		}
		theirs := chance
		if courter.widowed || match.widowed {
			theirs = max1(chance * widowChanceShare / 100)
		}
		if int64(roll) >= theirs {
			continue
		}
		if err := r.openCourtship(conn, courter.name, match.name, gm, now); err != nil {
			return started, err
		}
		taken[courter.name], taken[match.name] = true, true
		started++
	}
	return started, nil
}

// bestMatch is the scored half - the part that makes this courtship rather
// than whoever happened to be next in a sorted list. Closer in realm and
// closer in age score higher, and standing in the same place beats being one
// step away, because the people you actually see are the people you marry.
func (r *Runner) bestMatch(
	courter romanceCandidate,
	atLocation map[string][]romanceCandidate,
	parents map[string]map[string]bool,
	bonds map[string]romanceBond,
	taken map[string]bool,
) (romanceCandidate, bool) {
	reach := r.romanceReach(courter.location, courter.world, courter.realm)
	places := make([]string, 0, len(reach))
	for place := range reach {
		places = append(places, place)
	}
	sort.Strings(places)

	best, bestScore, found := romanceCandidate{}, int64(-1), false
	for _, place := range places {
		for _, other := range atLocation[place] {
			if other.name == courter.name || taken[other.name] {
				continue
			}
			realmGap := courter.realm - other.realm
			if realmGap < 0 {
				realmGap = -realmGap
			}
			if realmGap > courtshipRealmGap {
				continue
			}
			if !romanceAgesAgree(courter.age, other.age) {
				continue
			}
			if romanceAreKin(parents, courter.name, other.name) {
				continue
			}
			_, _, key := romancePairKey(courter.name, other.name)
			if bond, ok := bonds[key]; ok {
				if bond.grudge > courtshipGrudgeMax || bond.relation == "marriage" || bond.relation == "courtship" {
					continue
				}
			}
			score := 40 - realmGap*8 - int64(math.Abs(courter.age-other.age)/6)
			if other.location == courter.location {
				score += 12
			}
			if score > bestScore || (score == bestScore && found && other.name < best.name) {
				best, bestScore, found = other, score, true
			}
		}
	}
	return best, found
}

// openCourtship writes the beginning of it: a relation row the pair did not
// have, and a status on each of them that keeps the pairing from offering
// either to anybody else.
func (r *Runner) openCourtship(conn *storage.Conn, a, b string, gm int64, now float64) error {
	first, second, _ := romancePairKey(a, b)
	if _, err := conn.Execute(`INSERT INTO npc_social_relations(
            npc_a,npc_b,affinity,trust,grudge,relation_type,status,
            started_game_minute,last_interaction_game_minute,updated_at)
        VALUES(?,?,?,?,0,'courtship','active',?,?,?)
        ON CONFLICT(npc_a,npc_b) DO UPDATE SET
            relation_type='courtship',status='active',
            affinity=MAX(npc_social_relations.affinity,?),
            trust=MAX(npc_social_relations.trust,?),
            last_interaction_game_minute=excluded.last_interaction_game_minute,
            updated_at=excluded.updated_at`,
		[]any{first, second, courtshipAffinity, courtshipTrust, gm, gm, now,
			courtshipAffinity, courtshipTrust}); err != nil {
		return err
	}
	if _, err := conn.Execute(`UPDATE npc_life_state
        SET relationship_status='courting',last_social_game_minute=?,updated_at=?
        WHERE npc_name IN (?,?)`, []any{gm, now, a, b}); err != nil {
		return err
	}
	return nil
}

// endCourtship is the other ending, and it leaves no grudge: two people who
// stopped seeing each other are not enemies, they are single again.
func (r *Runner) endCourtship(conn *storage.Conn, a, b string, gm int64, now float64) error {
	if _, err := conn.Execute(`UPDATE npc_social_relations
        SET status='ended',last_interaction_game_minute=?,updated_at=?
        WHERE npc_a=? AND npc_b=?`, []any{gm, now, a, b}); err != nil {
		return err
	}
	_, err := conn.Execute(`UPDATE npc_life_state
        SET relationship_status='single',spouse_name='',last_social_game_minute=?,updated_at=?
        WHERE npc_name IN (?,?) AND relationship_status='courting'`,
		[]any{gm, now, a, b})
	return err
}

// marryPair is the wedding at the end of a courtship.
func (r *Runner) marryPair(conn *storage.Conn, a, b string, gm int64, now float64) error {
	return r.marryPairAs(conn, a, b, false, gm, now)
}

// marryPairAs writes a marriage of either kind. The affinity floors for a love
// match are the ones the old inline pairing wrote, so a marriage made this way
// is indistinguishable from one made before - and a courtship that got this
// far has earned more than the floor anyway, which is what MAX is for. An
// arranged match opens the other way round: standing without warmth.
func (r *Runner) marryPairAs(conn *storage.Conn, a, b string, political bool, gm int64, now float64) error {
	if _, err := conn.Execute(`UPDATE npc_life_state
        SET relationship_status='married',spouse_name=?,last_social_game_minute=?,updated_at=?
        WHERE npc_name=?`, []any{b, gm, now, a}); err != nil {
		return err
	}
	if _, err := conn.Execute(`UPDATE npc_life_state
        SET relationship_status='married',spouse_name=?,last_social_game_minute=?,updated_at=?
        WHERE npc_name=?`, []any{a, gm, now, b}); err != nil {
		return err
	}
	affinity, trust := int64(55), int64(45)
	if political {
		affinity, trust = politicalAffinity, politicalTrust
	}
	first, second, _ := romancePairKey(a, b)
	if _, err := conn.Execute(`INSERT INTO npc_social_relations(
            npc_a,npc_b,affinity,trust,grudge,relation_type,status,
            started_game_minute,last_interaction_game_minute,updated_at)
        VALUES(?,?,?,?,0,'marriage','active',?,?,?)
        ON CONFLICT(npc_a,npc_b) DO UPDATE SET
            relation_type='marriage',status='active',
            affinity=MAX(npc_social_relations.affinity,?),
            trust=MAX(npc_social_relations.trust,?),
            last_interaction_game_minute=excluded.last_interaction_game_minute,
            updated_at=excluded.updated_at`,
		[]any{first, second, affinity, trust, gm, gm, now, affinity, trust}); err != nil {
		return err
	}
	location := ""
	if res, err := conn.Execute(`SELECT current_location FROM npc_civilization_state WHERE npc_name=?`, []any{a}); err == nil && len(res.Rows) > 0 {
		location = fmt.Sprint(res.Rows[0][0])
	}
	kind, significance := "npc_marriage", int64(35)
	title := first + " and " + second + " are married"
	summary := fmt.Sprintf("%s and %s were married at %s, after a courtship the town had time to notice.", first, second, location)
	if political {
		kind, significance = "npc_political_marriage", 55
		title = first + " is married to " + second
		summary = fmt.Sprintf("%s and %s were married at %s. Neither was asked; two banners were, and both said yes.", first, second, location)
	}
	r.recordNPCHistory(conn, kind, fmt.Sprintf("%s:%s:%s:%d", kind, first, second, gm),
		title, summary, location, first, significance, gm, now)
	return nil
}
