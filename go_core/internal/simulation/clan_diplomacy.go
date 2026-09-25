package simulation

// The column that pointed at nobody (v1.0.1).
//
// `martial_clan_relations.partner_family_id` is foreign-keyed to
// `birth_families` and nullable, and the one statement that had ever inserted
// a row wrote it `nil` - because the partner it named was invented:
// `clanPartnerSurnames[roll] + " Martial Clan"`, a house with no members, no
// town, no wealth and no opinion about anything. So every household in the
// world held exactly one relation, with somebody who does not exist, and the
// only thing that could ever happen to it was the plus or minus one a tick
// that `clans` applies. Four households meant four relations on the day the
// world opened and four relations for ever after.
//
// The comment that names this fault is already in the tree, and it fixed the
// neighbouring table. `npc_romance.go` says "Bootstrap has always written
// clan `marriage_pact` rows and nothing has ever made one since, so the idea
// existed in this world and only ever described its own past" - and
// `npcPoliticalMarriages`, directly beneath it, writes `sect_relations`. The
// sect half has worked since v1.0.0-rc.24. The clan half it is named after
// was never built.
//
// Three decisions are worth knowing before changing this.
//
// **The gate is the world, not the street.** Every one of the thirteen
// archetypes has exactly one city per world (`birthFamilyHomelands`), so a
// same-location rule would let a house treat only with houses of its own
// archetype, and a one-road-step rule would hand each archetype a fixed set of
// partners it could never grow out of. That is rc.24's geography fault in a
// new hat - three hundred and ninety-two people who could never marry anybody
// because of where they happened to stand. A clan does not walk anywhere: it
// sends somebody, and the roads and the caravans between two cities of one
// world already exist. So the world is the gate, and being within one step is
// a bonus to the roll rather than a condition of it.
//
// **A relation is written from both sides or not at all.** Every reader is
// `WHERE family_id=?` (`/family clan`, `world_status_queries.go`, the standing
// term in `family.support`), so a single row is a treaty one of the two houses
// has never heard of.
//
// **A blood feud is not formed here.** `combat_aftermath.go` writes one, when
// a player kills a family head, and that is the only thing in the game that
// should be able to make two houses enemies over a body. What diplomacy can
// make is the four that can be signed.
//
// Deliberately left alone: nothing ends a relation. `active` is written 1 by
// every INSERT and read by every SELECT and set to 0 by nothing at all, and
// what a broken alliance leaves behind - a rivalry, or simply nothing - is a
// decision rather than a default. The invented bootstrap partners are left
// alone too, and on purpose: on a world with one household they are the only
// relation there can be.

import (
	"fmt"
	"strings"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// How often two houses that have never dealt with each other sign
	// something, and how many such signings one tick may see. `clan_dynamics`
	// runs every thirty in-world days, so this is a slow diplomacy on purpose:
	// a treaty is supposed to be worth something when it lands.
	clanDiplomacyChance = 18
	clanDiplomacyCap    = 2

	// Being close enough to see each other is worth a better roll, not a
	// different rule. A house one road step away shares a market and a
	// magistrate; a house four cities off has to be sent for.
	clanNeighbourBonus = 14

	// What the two houses are decides what they sign. The alignment gap is the
	// first question because it is the one that can make enemies: past
	// `clanAlignmentOpposes` there is nothing to negotiate, and between the two
	// figures the houses are neither friends nor enemies and sign nothing.
	clanAlignmentAgrees  = 25
	clanAlignmentOpposes = 55

	// Then what each brings. Two rich houses trade; two weighty ones ally;
	// anybody else marries, which is what a house with neither money nor
	// influence has always had to offer.
	clanTradeWealth   = 45
	clanPactInfluence = 35
	clanMinInfluence  = 8
	clanDiplomacyScan = 60
)

// clanRelationOpeningScore is what a relation is worth on the day it is made.
// Bootstrap and diplomacy read the same map, so an ancestral pact and a fresh
// one cannot silently be worth different amounts; what tells them apart is
// `started_game_minute`, which is the only thing about a relation's age the
// table has ever carried.
var clanRelationOpeningScore = map[string]int64{
	"alliance": 45, "marriage_pact": 35, "trade_pact": 25, "rivalry": -30, "blood_feud": -65,
}

// clanHouse is one active household, in the shape diplomacy reads them.
type clanHouse struct {
	id        int64
	name      string
	location  string
	world     string
	realm     int64
	wealth    int64
	influence int64
	alignment int64
}

// clanRelationBetween is what two houses would sign, or "" for two houses with
// no reason to sign anything. It is deliberately total over the inputs and
// free of dice: the roll decides whether they get round to it, the houses
// decide what it is.
func clanRelationBetween(a, b clanHouse) string {
	gap := a.alignment - b.alignment
	if gap < 0 {
		gap = -gap
	}
	switch {
	case gap >= clanAlignmentOpposes:
		return "rivalry"
	case gap > clanAlignmentAgrees:
		return ""
	case a.wealth >= clanTradeWealth && b.wealth >= clanTradeWealth:
		return "trade_pact"
	case a.influence >= clanPactInfluence && b.influence >= clanPactInfluence:
		return "alliance"
	default:
		return "marriage_pact"
	}
}

// clanDiplomacy is the step of `clan_dynamics` that lets two real households
// form a relation with each other. It returns how many were made.
func (r *Runner) clanDiplomacy(conn *storage.Conn, gm int64) (int64, error) {
	if !simTableExists(conn, "birth_families") || !simTableExists(conn, "martial_clan_relations") {
		return 0, nil
	}
	houses, err := r.clanHouses(conn)
	if err != nil || len(houses) < 2 {
		return 0, err
	}
	paired, err := clanExistingPairs(conn)
	if err != nil {
		return 0, err
	}
	now := nowFloat()
	made := int64(0)
	scanned := 0
	for i, a := range houses {
		if made >= clanDiplomacyCap {
			break
		}
		// Whom this house could send to, computed once rather than per
		// candidate: `WhereAnNPCCanWalk` parses nothing but it does sort, and
		// the second loop below would ask it for every pair.
		near := map[string]bool{a.location: true}
		for _, place := range r.neighbours(a.location, a.world, a.realm) {
			near[place] = true
		}
		for _, b := range houses[i+1:] {
			if made >= clanDiplomacyCap {
				break
			}
			if scanned >= clanDiplomacyScan {
				break
			}
			if a.world == "" || a.world != b.world {
				continue
			}
			if paired[clanPairKey(a.id, b.id)] {
				continue
			}
			if a.influence < clanMinInfluence || b.influence < clanMinInfluence {
				continue
			}
			relation := clanRelationBetween(a, b)
			if relation == "" {
				continue
			}
			scanned++
			chance := int64(clanDiplomacyChance)
			if near[b.location] {
				chance += clanNeighbourBonus
			}
			roll, err := gamerng.Intn(100)
			if err != nil {
				return made, err
			}
			if int64(roll) >= chance {
				continue
			}
			if err := r.signClanRelation(conn, a, b, relation, gm, now); err != nil {
				return made, err
			}
			paired[clanPairKey(a.id, b.id)] = true
			made++
		}
	}
	return made, nil
}

// signClanRelation writes the treaty from both sides and records it once.
func (r *Runner) signClanRelation(conn *storage.Conn, a, b clanHouse, relation string, gm int64, now float64) error {
	// A map miss answers 0, and 0 is not a sentinel here - it is a treaty that
	// `family.support`'s `relation_score>0` reads as worthless and the drift
	// can never lift, because nothing re-types a row. The `seller_user_id=0`
	// lesson: refuse the relation rather than store a number that looks like
	// one.
	score, ok := clanRelationOpeningScore[relation]
	if !ok {
		return fmt.Errorf("clan relation %q has no opening score", relation)
	}
	for _, side := range [2][2]clanHouse{{a, b}, {b, a}} {
		if _, err := conn.Execute(`INSERT INTO martial_clan_relations(
family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
VALUES(?,?,?,?,?,1,?,?)`, []any{side[0].id, side[1].id, side[1].name, relation, score, gm, now}); err != nil {
			return err
		}
	}
	// One row for one treaty, written from the lower id so the pair's
	// `source_key` is the same whichever order the scan reached them in.
	actor, partner := a, b
	if b.id < a.id {
		actor, partner = b, a
	}
	return recordClanRelationHistory(conn, actor.id, actor.name, partner.name, relation, score, gm, now)
}

// clanHouses reads the active households, ordered so a run is reproducible.
func (r *Runner) clanHouses(conn *storage.Conn) ([]clanHouse, error) {
	res, err := conn.Execute(`SELECT family_id,family_name,surname,location,head_realm_index,wealth,influence,alignment_bias
FROM birth_families WHERE line_status='active' ORDER BY family_id`, nil)
	if err != nil {
		return nil, err
	}
	out := make([]clanHouse, 0, len(res.Rows))
	for _, row := range res.Rows {
		location := strings.TrimSpace(fmt.Sprint(row[3]))
		name := firstNonemptyText(strings.TrimSpace(fmt.Sprint(row[1])),
			firstNonemptyText(strings.TrimSpace(fmt.Sprint(row[2])), "Clan")+" Clan")
		// A house whose town the catalogue does not carry has no world, and a
		// house with no world can treat with nobody - the rc.44 rule, which
		// `WhereAnNPCCanWalk` already enforces by refusing a destination in
		// another world.
		world := ""
		if loc, ok := r.World.Locations[location]; ok {
			world = loc.World
		}
		out = append(out, clanHouse{
			id: i64(row[0]), name: name, location: location, world: world,
			realm: i64(row[4]), wealth: i64(row[5]), influence: i64(row[6]), alignment: i64(row[7]),
		})
	}
	return out, nil
}

// clanExistingPairs is every pair of real houses already related, in either
// direction. The invented bootstrap partners carry no id and so appear here
// not at all, which is right: they are nobody, and standing one does not use
// up a house's willingness to deal with a real neighbour.
func clanExistingPairs(conn *storage.Conn) (map[string]bool, error) {
	res, err := conn.Execute(`SELECT family_id,partner_family_id FROM martial_clan_relations
WHERE active=1 AND partner_family_id IS NOT NULL`, nil)
	if err != nil {
		return nil, err
	}
	pairs := make(map[string]bool, len(res.Rows))
	for _, row := range res.Rows {
		pairs[clanPairKey(i64(row[0]), i64(row[1]))] = true
	}
	return pairs, nil
}

// clanPairKey names an unordered pair of houses.
func clanPairKey(a, b int64) string {
	if b < a {
		a, b = b, a
	}
	return fmt.Sprintf("%d:%d", a, b)
}

// clanRivalryOpens is the score a rivalry born of a broken treaty opens at:
// the rivalry's own opening score, read off the one map, so a rivalry that
// begins in a broken alliance is worth exactly what one signed cold is.
func clanRivalryOpens() int64 { return clanRelationOpeningScore["rivalry"] }

// endExhaustedClanRelations closes the relations that have run out (v1.3.3),
// after the tick's drift. Drift only ever moves a score away from zero, so
// what brings one there is an event - a head killed takes every relation of
// the house down (`combat_aftermath.go`) - and until now nothing looked: an
// alliance sat at 0, `family.support` read it as worthless, and `active` had
// been written 1 by every INSERT and 0 by nothing. On the owner's call:
//
//   - a treaty (alliance, marriage_pact, trade_pact) at or below 0 ends, and a
//     rivalry opens in its place from both sides, at the rivalry's own
//     opening score, with a public history row; a house whose partner is
//     already a rival or a feud gets no second row;
//   - a rivalry at or above 0 ends, and nothing follows - a rivalry that has
//     cooled to nothing is nothing;
//   - a blood_feud never ends here. It came from a body (`combat_aftermath`
//     owns it), and drift only deepens it.
//
// The invented bootstrap partners carry no family id, so their treaties end
// the same way and their rivalry is written from the one side that exists.
func endExhaustedClanRelations(conn *storage.Conn, gm int64, now float64) (int64, error) {
	rows, err := conn.Execute(`SELECT r.relation_id,r.family_id,r.partner_family_id,r.partner_name,r.relation_type,r.relation_score,
COALESCE(f.family_name,'') FROM martial_clan_relations r LEFT JOIN birth_families f ON f.family_id=r.family_id
WHERE r.active=1 AND ((r.relation_type IN ('alliance','marriage_pact','trade_pact') AND r.relation_score<=0)
   OR (r.relation_type='rivalry' AND r.relation_score>=0)) ORDER BY r.relation_id`, nil)
	if err != nil {
		return 0, err
	}
	ended := int64(0)
	for _, row := range rows.Rows {
		relationID, familyID := i64(row[0]), i64(row[1])
		partnerID := int64(0)
		if row[2] != nil {
			partnerID = i64(row[2])
		}
		partner := strings.TrimSpace(fmt.Sprint(row[3]))
		relation := strings.TrimSpace(fmt.Sprint(row[4]))
		familyName := strings.TrimSpace(fmt.Sprint(row[6]))
		if familyName == "" {
			familyName = fmt.Sprintf("Family %d", familyID)
		}
		if _, err := conn.Execute(`UPDATE martial_clan_relations SET active=0,updated_at=? WHERE relation_id=?`, []any{now, relationID}); err != nil {
			return ended, err
		}
		ended++
		if err := recordClanRelationEnded(conn, familyID, familyName, partner, relation, gm, now); err != nil {
			return ended, err
		}
		if relation == "rivalry" {
			continue
		}
		// The broken treaty leaves a rivalry, from both sides where the
		// partner is a real house - unless that pair is already rivals or
		// feuding, in which case the treaty was the row that was out of date.
		sides := [][2]any{{familyID, partnerID}}
		if partnerID > 0 {
			sides = append(sides, [2]any{partnerID, familyID})
		}
		for _, side := range sides {
			var partnerArg any = side[1]
			if i64(side[1]) == 0 {
				partnerArg = nil
			}
			standing, err := conn.Execute(`SELECT COUNT(*) FROM martial_clan_relations WHERE family_id=? AND active=1
AND relation_type IN ('rivalry','blood_feud') AND ((partner_family_id IS NOT NULL AND partner_family_id=?) OR (partner_family_id IS NULL AND partner_name=?))`,
				[]any{side[0], partnerArg, partner})
			if err != nil {
				return ended, err
			}
			if i64(standing.Rows[0][0]) > 0 {
				continue
			}
			name := partner
			if i64(side[0]) != familyID {
				name = familyName
			}
			if _, err := conn.Execute(`INSERT INTO martial_clan_relations(
family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
VALUES(?,?,?,'rivalry',?,1,?,?)`, []any{side[0], partnerArg, name, clanRivalryOpens(), gm, now}); err != nil {
				return ended, err
			}
		}
	}
	return ended, nil
}

// recordClanRelationEnded is the world's memory that a relation ran out. Keyed
// on the relation and the minute, so one treaty can end more than once across
// a long world without the second ending vanishing into ON CONFLICT.
func recordClanRelationEnded(conn *storage.Conn, familyID int64, familyName, partner, relation string, gm int64, now float64) error {
	label := strings.ReplaceAll(relation, "_", " ")
	title := familyName + "'s " + label + " with " + partner + " has ended"
	summary := "The " + label + " between " + familyName + " and " + partner + " has run its course; the martial world no longer counts them as bound."
	if relation != "rivalry" {
		summary = "The " + label + " between " + familyName + " and " + partner + " has broken, and the two houses count each other rivals now."
	}
	sourceKey := fmt.Sprintf("clan_relation_ended:%d:%s:%s:%d", familyID, relation, partner, gm)
	_, err := conn.Execute(`INSERT INTO world_history_events(
source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
VALUES(?,'clan_relation_ended',?,?,64,'public','','',?,'clan',?,?,'clan',?,?,NULL,'',?,?,'{}',?,?)
ON CONFLICT(source_key) DO NOTHING`, []any{
		sourceKey, title, summary, familyName, fmt.Sprint(familyID), familyName, partner, partner,
		"clan " + relation + " ended", gm, now, now,
	})
	return err
}
