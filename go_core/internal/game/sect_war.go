package game

// How a war between two sects begins and ends (v1.24.0).
//
// A war had two writers - `territory.claim` for a player's sect and
// `npcSectWars` for the world's own - and each opened one in its own words, and
// it had two resolvers - `war.act` and the daily siege tick - and each ended one
// in its own words, with a different unrest. Neither told the world a war had
// ended, neither touched the standing between the two sects, and nothing kept
// a sect that had just been thrown back from declaring on the same ground the
// next minute. And the people who fought were paid nothing: an act in a war was
// the one thing a sect member could do for their sect that earned no
// contribution.
//
// `DeclareWarTx` and `ResolveWarTx` are the one door each now, and the
// simulation calls into them the way it calls `WalletDeltaTx` and
// `StallSaleTx`, because a rule it must not copy is a rule it calls.
//
// Six rules, all content (`war_system`, read by `warRules`):
//
//   - **Fighting is paid.** Each act earns `act_points` of sect contribution
//     through `creditSectContributionTx`, the one door points come in by, up to
//     `war_points_cap` a war - counted off `territory_war_actions`, the rows
//     the act itself writes, so the cap needs no storage of its own. A win
//     pays `victory_points` once to every member who fought on the winning
//     side and is still sworn to it.
//   - **Defense is real.** `territory_state.defense` was seeded at 50 and
//     written by nothing, so the war step's "weakly held" filter admitted
//     everything. A defender's fortify raises it, a held siege raises it more,
//     a fall leaves the walls at `fall_defense`, and every
//     `siege_defense_divisor` points of it blunt an attacking blow.
//   - **A failed attack buys a truce.** The sect thrown back may not move on
//     that ground again for `truce_days`.
//   - **A fall is an occupation first.** For `occupation_days` the former
//     holder may strike back - the truce binds only a failed attacker - and
//     the war step looks there first.
//   - **A war costs standing.** Declaring lowers the two sects'
//     `sect_relations` score by `declare_relation_drop`, an ending by
//     `end_relation_drop`.
//   - **The world hears both ends.** A declaration and a resolution are each
//     a public `world_history_events` row.
//   - **A war is over a whole place.** A sect holds a city, not one of its
//     streets (`TerritoryGround`): a declaration over a part is refused, a war
//     found over one is set aside with no victor, and nothing the war would
//     have moved - a banner, the walls, the standing, a truce, an occupation -
//     moves.

import (
	"errors"
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const warMinutesPerDay = int64(1440)

// WarRuleSet is the war roster with its defaults filled in.
type WarRuleSet struct {
	ActPoints           int64
	WarPointsCap        int64
	VictoryPoints       int64
	TruceDays           int64
	OccupationDays      int64
	FortifyDefenseGain  int64
	DefenseCap          int64
	FallDefense         int64
	HoldDefenseGain     int64
	SiegeDefenseDivisor int64
	DeclareRelationDrop int64
	EndRelationDrop     int64
	TickDaysCap         int64
	WearinessPerDay     int64
	AllyMinRelation     int64
	AllyRelationDrop    int64
	AllyStrengthPercent int64
	AllyStrengthCap     int64
	DisciplesPerPoint   int64
	DiscipleStrengthCap int64
	DisciplesAtTheWalls int64
	PeaceMinDays        int64
	PeaceMinRankLevel   int64
	PeaceCostPoints     int64
	PeaceCedeSiege      int64
	PeaceRelationGain   int64
	NPCPeaceMorale      int64
}

// WarRules reads `war_system`. A key the content leaves out takes the value
// the roster shipped with, so an older content file fights the same war.
func WarRules(catalog worlddata.Catalog) WarRuleSet {
	s := catalog.WarSystem
	r := WarRuleSet{
		ActPoints: s.ActPoints, WarPointsCap: s.WarPointsCap, VictoryPoints: s.VictoryPoints,
		TruceDays: s.TruceDays, OccupationDays: s.OccupationDays,
		FortifyDefenseGain: s.FortifyDefenseGain, DefenseCap: s.DefenseCap, FallDefense: s.FallDefense,
		HoldDefenseGain: s.HoldDefenseGain, SiegeDefenseDivisor: s.SiegeDefenseDivisor,
		DeclareRelationDrop: s.DeclareRelationDrop, EndRelationDrop: s.EndRelationDrop,
		TickDaysCap: s.TickDaysCap, WearinessPerDay: s.WearinessPerDay,
		AllyMinRelation: s.AllyMinRelationScore, AllyRelationDrop: s.AllyRelationDrop,
		AllyStrengthPercent: s.AllyStrengthPercent, AllyStrengthCap: s.AllyStrengthCap,
		DisciplesPerPoint: s.DisciplesPerPoint, DiscipleStrengthCap: s.DiscipleStrengthCap, DisciplesAtTheWalls: s.DisciplesAtTheWalls,
		PeaceMinDays: s.PeaceMinDays, PeaceMinRankLevel: s.PeaceMinRankLevel, PeaceCostPoints: s.PeaceCostPoints,
		PeaceCedeSiege: s.PeaceCedeSiege, PeaceRelationGain: s.PeaceRelationGain, NPCPeaceMorale: s.NPCPeaceMorale,
	}
	def := func(v *int64, d int64) {
		if *v <= 0 {
			*v = d
		}
	}
	def(&r.ActPoints, 8)
	def(&r.WarPointsCap, 80)
	def(&r.VictoryPoints, 120)
	def(&r.TruceDays, 30)
	def(&r.OccupationDays, 30)
	def(&r.FortifyDefenseGain, 2)
	def(&r.DefenseCap, 90)
	def(&r.FallDefense, 30)
	def(&r.HoldDefenseGain, 10)
	def(&r.SiegeDefenseDivisor, 20)
	def(&r.DeclareRelationDrop, 15)
	def(&r.EndRelationDrop, 10)
	def(&r.TickDaysCap, 7)
	def(&r.WearinessPerDay, 1)
	def(&r.AllyMinRelation, 30)
	def(&r.AllyRelationDrop, 5)
	def(&r.AllyStrengthPercent, 50)
	def(&r.AllyStrengthCap, 4)
	def(&r.DisciplesPerPoint, 5)
	def(&r.DiscipleStrengthCap, 4)
	def(&r.DisciplesAtTheWalls, 3)
	def(&r.PeaceMinDays, 3)
	def(&r.PeaceMinRankLevel, 40)
	def(&r.PeaceCostPoints, 100)
	def(&r.PeaceCedeSiege, 60)
	def(&r.PeaceRelationGain, 10)
	def(&r.NPCPeaceMorale, 25)
	r.FallDefense = min64(r.FallDefense, r.DefenseCap)
	return r
}

// warRules is the package's own name for it.
func warRules(catalog worlddata.Catalog) WarRuleSet { return WarRules(catalog) }

// WarDefenseBlunt is how much a territory's defense takes off one attacking
// blow: one point for every SiegeDefenseDivisor of it.
func WarDefenseBlunt(rules WarRuleSet, defense int64) int64 {
	return max64(0, defense) / rules.SiegeDefenseDivisor
}

// errWarTruce is a declaration the truce refuses.
var errWarTruce = errors.New("truce")

// WarTruceUntilTx is the game minute a sect's truce on a territory ends, or 0
// when there is none: the sect gave way there - thrown back from an attack,
// a peace made as the attacker, or the ground ceded as its holder - less than
// TruceDays ago. A holder that lost the ground on the walls is not bound: that
// is exactly who an occupation is waiting for.
func WarTruceUntilTx(conn *storage.Conn, catalog worlddata.Catalog, sect, territory string, gm int64) int64 {
	if !tableExistsTx(conn, "territory_war_operations") {
		return 0
	}
	r, err := conn.Execute(`SELECT MAX(w.updated_game_minute) AS ended FROM territory_wars w
        JOIN territory_war_operations o ON o.war_id=w.war_id
        WHERE w.territory_key=? AND w.status='resolved'
          AND ((w.attacker_key=? AND o.resolution IN ('defender_holds','peace')) OR (w.defender_key=? AND o.resolution='ceded'))`,
		[]any{territory, sect, sect})
	if err != nil {
		return 0
	}
	row := firstRowMap(r)
	if row == nil || row["ended"] == nil {
		return 0
	}
	until := i64(row["ended"]) + warRules(catalog).TruceDays*warMinutesPerDay
	if until <= gm {
		return 0
	}
	return until
}

// WarOccupiedFromTx names the sect a territory was taken from while it is
// still under occupation, or "" when it is not occupied.
func WarOccupiedFromTx(conn *storage.Conn, territory string, gm int64) string {
	if !tableExistsTx(conn, "territory_war_operations") {
		return ""
	}
	r, err := conn.Execute(`SELECT w.defender_key FROM territory_wars w
        JOIN territory_war_operations o ON o.war_id=w.war_id
        WHERE w.territory_key=? AND w.status='resolved' AND o.resolution='attacker_occupation'
          AND o.occupation_until_game_minute>?
        ORDER BY w.war_id DESC LIMIT 1`, []any{territory, gm})
	if err != nil {
		return ""
	}
	if row := firstRowMap(r); row != nil {
		return strings.TrimSpace(fmt.Sprint(row["defender_key"]))
	}
	return ""
}

// warSectRelationTx moves the standing between two sects by delta. The
// bootstrap wrote each pair once in whichever order it met them, so a pair is
// found either way round; a pair nobody wrote (the hidden sect) is written in
// sorted order.
func warSectRelationTx(conn *storage.Conn, a, b string, delta int64, now float64) error {
	if delta == 0 || a == b || !tableExistsTx(conn, "sect_relations") {
		return nil
	}
	res, err := conn.Execute(`UPDATE sect_relations SET relation_score=MAX(-100,MIN(100,relation_score+?)),updated_at=?
        WHERE (sect_a=? AND sect_b=?) OR (sect_a=? AND sect_b=?)`, []any{delta, now, a, b, b, a})
	if err != nil {
		return err
	}
	if res.RowsAffected > 0 {
		return nil
	}
	pair := []string{a, b}
	sort.Strings(pair)
	_, err = conn.Execute(`INSERT INTO sect_relations(sect_a,sect_b,relation_score,relation_type,treaty_status,updated_at)
        VALUES(?,?,?,'neutral','none',?) ON CONFLICT(sect_a,sect_b) DO NOTHING`,
		[]any{pair[0], pair[1], clamp(delta, -100, 100), now})
	return err
}

// territoryName is what a territory is called on its row, or its key.
func territoryName(conn *storage.Conn, territory string) string {
	r, err := conn.Execute(`SELECT name FROM territory_state WHERE territory_key=?`, []any{territory})
	if err == nil {
		if row := firstRowMap(r); row != nil {
			if n := strings.TrimSpace(fmt.Sprint(row["name"])); n != "" && n != "<nil>" {
				return n
			}
		}
	}
	return territory
}

// DeclareWarTx opens a war: the truce and an active war refuse, the war and
// its operation row are written, the two sects' standing falls, and the world
// is told. It is the one door - `territory.claim` and the world's own war step
// both come through it.
func DeclareWarTx(conn *storage.Conn, catalog worlddata.Catalog, attacker, defender, territory string, gm int64, now float64) (int64, error) {
	attacker, defender, territory = strings.TrimSpace(attacker), strings.TrimSpace(defender), strings.TrimSpace(territory)
	if attacker == "" || defender == "" || attacker == defender {
		return 0, errors.New("a war needs two sects")
	}
	// A war is fought over a whole place: its banner, its walls and its
	// occupation are all the city's row, and a part of a city has none the
	// rest of the game reads. The world's own war step skips parts so this
	// refusal never ends its tick (one system's error ends the tick).
	if whole, part := TerritoryGround(catalog, territory); part {
		if whole == "" {
			return 0, fmt.Errorf("%s is no ground a sect can hold; a war is fought over a whole place", territory)
		}
		return 0, fmt.Errorf("%s is part of %s; a war is fought over a whole place", territory, whole)
	}
	if until := WarTruceUntilTx(conn, catalog, attacker, territory, gm); until > 0 {
		days := (until - gm + warMinutesPerDay - 1) / warMinutesPerDay
		return 0, fmt.Errorf("%w: %s was thrown back from %s and may not move on it again for %d more day(s)", errWarTruce, attacker, territoryName(conn, territory), days)
	}
	r, err := conn.Execute(`SELECT war_id FROM territory_wars WHERE territory_key=? AND status='active' LIMIT 1`, []any{territory})
	if err != nil {
		return 0, err
	}
	if firstRowMap(r) != nil {
		return 0, errors.New("an active war already contests that territory")
	}
	ins, err := conn.Execute(`INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_game_minute,updated_game_minute,created_at,updated_at) VALUES(?,?,?,'active',?,?,?,?)`,
		[]any{attacker, defender, territory, gm, gm, now, now})
	if err != nil {
		return 0, err
	}
	if tableExistsTx(conn, "territory_war_operations") {
		if err = ensureWarOperationGo(conn, ins.LastInsertID, gm, now); err != nil {
			return 0, err
		}
	}
	rules := warRules(catalog)
	if err = warSectRelationTx(conn, attacker, defender, -rules.DeclareRelationDrop, now); err != nil {
		return 0, err
	}
	name := territoryName(conn, territory)
	retake := WarOccupiedFromTx(conn, territory, gm) == attacker
	summary := fmt.Sprintf("%s has moved on %s, held by %s. The border is contested.", attacker, name, defender)
	if retake {
		summary = fmt.Sprintf("%s has come back for %s, taken from it by %s. The occupation is contested.", attacker, name, defender)
	}
	if err = recordWorldHistoryTx(conn, fmt.Sprintf("sect_war:%s:%s:%d", attacker, territory, gm), "territory_war",
		attacker+" declares on "+defender, summary, 80, "public", territory, attacker,
		"faction", attacker, attacker, "faction", defender, defender, nil, "",
		[]string{"territory", "war", territory}, gm, map[string]any{"war_id": ins.LastInsertID, "retake": retake}, now); err != nil {
		return 0, err
	}
	return ins.LastInsertID, nil
}

// WarSpoil is what one fighter was paid when their side won.
type WarSpoil struct {
	UserID   int64  `json:"user_id"`
	Points   int64  `json:"points"`
	Promoted string `json:"promoted,omitempty"`
}

// WarSetAside is the verdict on a war fought over a part of a city.
const WarSetAside = "set_aside"

// WarVerdict is how a war over this territory may end. A war over a part of a
// city (`TerritoryGround`) is set aside with no victor whatever was won on its
// walls: a banner on a street is not the city's, and the city is what every
// reader of a banner looks at. Anything else ends as it was decided, and a war
// with no verdict yet has none to set aside. It is idempotent, so a caller
// that wants to report the verdict may ask it before `ResolveWarTx` asks it
// again as the guard.
func WarVerdict(catalog worlddata.Catalog, territory, winner, resolution string) (string, string) {
	if resolution == "" {
		return winner, resolution
	}
	if _, part := TerritoryGround(catalog, territory); part {
		return "", WarSetAside
	}
	return winner, resolution
}

// ResolveWarTx ends an active war in winner's favour. It is idempotent: a war
// that is no longer active is left alone and answers (nil, false). It writes
// the operation row's verdict, the territory's new banner, unrest and walls,
// the standing between the two sects, the history row, and the victors' pay.
// A war over a part of a city ends with no victor and moves none of that
// (`WarVerdict`), so no resolver - the tick, `war.act`, `war.peace` - can hand
// a street to a sect.
func ResolveWarTx(conn *storage.Conn, catalog worlddata.Catalog, warID int64, winner, resolution string, gm int64, now float64) ([]WarSpoil, bool, error) {
	r, err := conn.Execute(`SELECT * FROM territory_wars WHERE war_id=?`, []any{warID})
	if err != nil {
		return nil, false, err
	}
	war := firstRowMap(r)
	if war == nil {
		return nil, false, nil
	}
	res, err := conn.Execute(`UPDATE territory_wars SET status='resolved',updated_game_minute=?,updated_at=? WHERE war_id=? AND status='active'`, []any{gm, now, warID})
	if err != nil {
		return nil, false, err
	}
	if res.RowsAffected == 0 {
		return nil, false, nil
	}
	rules := warRules(catalog)
	attacker, defender := fmt.Sprint(war["attacker_key"]), fmt.Sprint(war["defender_key"])
	territory := fmt.Sprint(war["territory_key"])
	winner, resolution = WarVerdict(catalog, territory, winner, resolution)
	setAside := resolution == WarSetAside
	negotiated := resolution == "peace" || resolution == "ceded" || setAside
	occupation := int64(0)
	if winner == attacker && !negotiated {
		occupation = gm + rules.OccupationDays*warMinutesPerDay
	}
	if tableExistsTx(conn, "territory_war_operations") {
		if _, err = conn.Execute(`UPDATE territory_war_operations SET winner_key=?,resolution=?,occupation_until_game_minute=?,last_tick_game_minute=?,updated_at=? WHERE war_id=?`,
			[]any{winner, resolution, occupation, gm, now, warID}); err != nil {
			return nil, false, err
		}
	}
	switch {
	case setAside:
		// A part of a city is no sect's ground: nothing on it moves.
	case resolution == "peace":
		// The holder keeps the ground and nobody's walls move.
		_, err = conn.Execute(`UPDATE territory_state SET unrest=MAX(0,unrest-5),updated_game_minute=?,updated_at=? WHERE territory_key=?`,
			[]any{gm, now, territory})
	case resolution == "ceded":
		// Handed over at a table: the walls are whole and nothing is occupied.
		if err = endOtherOccupationsTx(conn, warID, territory, now); err != nil {
			return nil, false, err
		}
		_, err = conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MIN(100,unrest+10),updated_game_minute=?,updated_at=? WHERE territory_key=?`,
			[]any{attacker, gm, now, territory})
	case winner == attacker:
		// A fall ends whatever occupation the ground was under: the banner
		// it is taken from was itself an occupier, and its 30 days must not
		// come due later and hand the ground back to it.
		if err = endOtherOccupationsTx(conn, warID, territory, now); err != nil {
			return nil, false, err
		}
		_, err = conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MIN(100,unrest+30),defense=?,updated_game_minute=?,updated_at=? WHERE territory_key=?`,
			[]any{winner, rules.FallDefense, gm, now, territory})
	default:
		_, err = conn.Execute(`UPDATE territory_state SET unrest=MAX(0,unrest-10),defense=MIN(?,defense+?),updated_game_minute=?,updated_at=? WHERE territory_key=?`,
			[]any{rules.DefenseCap, rules.HoldDefenseGain, gm, now, territory})
	}
	if err != nil {
		return nil, false, err
	}
	// A war set aside was never a war over anything: the declaration's drop
	// stands and nothing warms or cools it again.
	if !setAside {
		standing := -rules.EndRelationDrop
		if negotiated {
			standing = rules.PeaceRelationGain
		}
		if err = warSectRelationTx(conn, attacker, defender, standing, now); err != nil {
			return nil, false, err
		}
	}
	loser := defender
	if winner == defender {
		loser = attacker
	}
	// What a war's end does to the two sects (v1.29.0): a sect that wins a war
	// is stronger for it and one that loses is weaker, and nothing said so -
	// `sect_politics_state` only ever fed a war's strength, never heard how it
	// went. A peace moves neither; a cession is a loss taken at the table.
	if resolution != "peace" && winner != "" {
		if err = warPoliticsOutcomeTx(conn, winner, loser, now); err != nil {
			return nil, false, err
		}
	}
	name := territoryName(conn, territory)
	title := winner + " takes " + name
	summary := fmt.Sprintf("%s has taken %s from %s. It is occupied for now; the old banner may yet come back for it.", winner, name, loser)
	// Who the row is about: the winner and the loser, or - with no winner -
	// the two sects the war was between. It stays public and, set aside,
	// under the Quest Forge's floor: nobody won anything worth a quest.
	faction, actor, target, significance := winner, winner, loser, int64(80)
	switch {
	case setAside:
		faction, actor, target, significance = "", attacker, defender, 50
		title = "The war over " + name + " is set aside"
		if whole, _ := TerritoryGround(catalog, territory); whole != "" {
			summary = fmt.Sprintf("%s is part of %s, and a sect holds a place, not one of its streets. The war between %s and %s over it ends with no victor.", name, whole, attacker, defender)
		} else {
			summary = fmt.Sprintf("%s is no ground a sect can hold. The war between %s and %s over it ends with no victor.", name, attacker, defender)
		}
	case resolution == "peace":
		title = attacker + " and " + defender + " make peace"
		summary = fmt.Sprintf("%s and %s have made peace over %s. %s keeps it, and %s may not move on it again for a while.", attacker, defender, name, defender, attacker)
	case resolution == "ceded":
		title = defender + " cedes " + name
		summary = fmt.Sprintf("%s has ceded %s to %s at the table rather than on the walls.", defender, name, attacker)
	case winner == defender:
		title = winner + " holds " + name
		summary = fmt.Sprintf("%s has thrown %s back from %s. The walls stand higher for it.", winner, loser, name)
	}
	if err = recordWorldHistoryTx(conn, fmt.Sprintf("sect_war_end:%d", warID), "territory_war_resolved", title, summary, significance, "public",
		territory, faction, "faction", actor, actor, "faction", target, target, nil, "",
		[]string{"territory", "war", territory}, gm, map[string]any{"war_id": warID, "resolution": resolution}, now); err != nil {
		return nil, false, err
	}
	// A victory is paid on the walls; a peace or a cession pays nobody.
	if negotiated {
		return []WarSpoil{}, true, nil
	}
	spoils, err := payWarVictorsTx(conn, catalog, warID, winner, winner == attacker)
	if err != nil {
		return nil, false, err
	}
	return spoils, true, nil
}

// payWarVictorsTx pays VictoryPoints to every member who fought on the
// winning side and still stands with it - sworn to the winning sect, or to a
// sect still allied to it. A fighter who has left fought for a side that is
// no longer theirs.
func payWarVictorsTx(conn *storage.Conn, catalog worlddata.Catalog, warID int64, winner string, attackerWon bool) ([]WarSpoil, error) {
	if !tableExistsTx(conn, "territory_war_actions") || !tableExistsTx(conn, "sect_membership") {
		return nil, nil
	}
	side := "defender"
	if attackerWon {
		side = "attacker"
	}
	r, err := conn.Execute(`SELECT DISTINCT a.user_id,m.sect_name FROM territory_war_actions a
        JOIN sect_membership m ON m.user_id=a.user_id
        WHERE a.war_id=? AND a.side=? AND a.user_id IS NOT NULL
        ORDER BY a.user_id`, []any{warID, side})
	if err != nil {
		return nil, err
	}
	points := warRules(catalog).VictoryPoints
	spoils := []WarSpoil{}
	for _, row := range rowsToMaps(r) {
		uid := i64(row["user_id"])
		if sect := fmt.Sprint(row["sect_name"]); sect != winner && !SectsAlliedTx(conn, catalog, sect, winner) {
			continue
		}
		promoted, err := creditSectContributionTx(conn, catalog, uid, points, max64(1, points/10))
		if err != nil {
			return nil, err
		}
		spoils = append(spoils, WarSpoil{UserID: uid, Points: points, Promoted: promoted})
	}
	return spoils, nil
}

// warActPointsTx is what one act earns, read before the act's own row is
// written: ActPoints, or what is left of WarPointsCap for this member in this
// war.
func warActPointsTx(conn *storage.Conn, catalog worlddata.Catalog, warID, userID int64) (int64, error) {
	left, err := warActPointsLeftTx(conn, catalog, warID, userID)
	if err != nil {
		return 0, err
	}
	return min64(warRules(catalog).ActPoints, left), nil
}

// fortifyTerritoryTx raises a territory's walls by a defender's fortify,
// never past DefenseCap.
func fortifyTerritoryTx(conn *storage.Conn, catalog worlddata.Catalog, territory string, gm int64, now float64) (int64, error) {
	rules := warRules(catalog)
	if _, err := conn.Execute(`UPDATE territory_state SET defense=MIN(?,defense+?),updated_game_minute=?,updated_at=? WHERE territory_key=?`,
		[]any{rules.DefenseCap, rules.FortifyDefenseGain, gm, now, territory}); err != nil {
		return 0, err
	}
	return territoryDefenseTx(conn, territory), nil
}

// territoryDefenseTx is a territory's walls, 50 for one that has no row.
func territoryDefenseTx(conn *storage.Conn, territory string) int64 {
	r, err := conn.Execute(`SELECT defense FROM territory_state WHERE territory_key=?`, []any{territory})
	if err != nil {
		return 50
	}
	if row := firstRowMap(r); row != nil {
		return i64(row["defense"])
	}
	return 50
}

// ManorDefensePower is the manor's arrays standing behind a defender, in the
// units a player's war power is counted in, or 0 when the sect's manor is
// not on that ground.
func ManorDefensePower(conn *storage.Conn, sect, territory string) int64 {
	return manorDefensePowerGo(conn, sect, territory)
}

// SectsAlliedTx reports whether two sects stand allied: a marriage pact
// between them, or standing of AllyMinRelation or more. A sect is not its own
// ally - it is the belligerent.
func SectsAlliedTx(conn *storage.Conn, catalog worlddata.Catalog, a, b string) bool {
	if a == "" || b == "" || a == b || !tableExistsTx(conn, "sect_relations") {
		return false
	}
	r, err := conn.Execute(`SELECT relation_score,relation_type FROM sect_relations
        WHERE (sect_a=? AND sect_b=?) OR (sect_a=? AND sect_b=?) LIMIT 1`, []any{a, b, b, a})
	if err != nil {
		return false
	}
	row := firstRowMap(r)
	if row == nil {
		return false
	}
	return fmt.Sprint(row["relation_type"]) == "marriage_pact" || i64(row["relation_score"]) >= warRules(catalog).AllyMinRelation
}

// warSideTx is the side a sect fights on in a war and the belligerent it
// fights for: its own, or - for a sect allied to exactly one of the two - its
// ally's (v1.24.0). A sect allied to both may not choose between them.
func warSideTx(conn *storage.Conn, catalog worlddata.Catalog, sect string, war map[string]any) (side, fightsFor string, err error) {
	attacker, defender := fmt.Sprint(war["attacker_key"]), fmt.Sprint(war["defender_key"])
	switch sect {
	case attacker:
		return "attacker", attacker, nil
	case defender:
		return "defender", defender, nil
	}
	withA, withD := SectsAlliedTx(conn, catalog, sect, attacker), SectsAlliedTx(conn, catalog, sect, defender)
	switch {
	case withA && withD:
		return "", "", fmt.Errorf("%s is allied to both %s and %s and may not take a side", sect, attacker, defender)
	case withA:
		return "attacker", attacker, nil
	case withD:
		return "defender", defender, nil
	}
	return "", "", errors.New("your sect is not a belligerent in that war, nor allied to one")
}

// warAllyJoinsTx lowers an ally's standing with the side it fights against,
// the first time one of its members acts in this war. Read before the act's
// own row is written.
func warAllyJoinsTx(conn *storage.Conn, catalog worlddata.Catalog, warID int64, ally, enemy string, now float64) (bool, error) {
	query := `SELECT 1 FROM territory_war_actions a JOIN sect_membership m ON m.user_id=a.user_id
        WHERE a.war_id=? AND m.sect_name=? LIMIT 1`
	if warActionsHaveSect(conn) {
		query = `SELECT 1 FROM territory_war_actions WHERE war_id=? AND sect_name=? LIMIT 1`
	}
	r, err := conn.Execute(query, []any{warID, ally})
	if err != nil {
		return false, err
	}
	if firstRowMap(r) != nil {
		return false, nil
	}
	return true, warSectRelationTx(conn, ally, enemy, -warRules(catalog).AllyRelationDrop, now)
}

// warFrontsQuery is every active war the caller may fight in, with the side
// war.act would put them on and what is left of their pay in it (v1.24.0).
// It asks warSideTx, the rule war.act asks, so a picker built from it never
// offers a war the act would refuse. Somebody in no sect may fight in none.
func warFrontsQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	out := map[string]any{"sect_name": "", "wars": []map[string]any{}}
	if !tableExistsTx(conn, "territory_wars") || !tableExistsTx(conn, "sect_membership") {
		return out, nil
	}
	mem, err := sectMembershipRow(conn, userID)
	if err != nil || mem == nil {
		return out, err
	}
	sect := fmt.Sprint(mem["sect_name"])
	out["sect_name"] = sect
	r, err := conn.Execute(`SELECT w.*,o.siege_progress,o.attacker_morale,o.defender_morale FROM territory_wars w
        LEFT JOIN territory_war_operations o ON o.war_id=w.war_id WHERE w.status='active' ORDER BY w.war_id DESC`, nil)
	if err != nil {
		return nil, err
	}
	wars := []map[string]any{}
	for _, war := range rowsToMaps(r) {
		side, fightsFor, refusal := warSideTx(conn, catalog, sect, war)
		if refusal != nil {
			continue
		}
		warID, territory := i64(war["war_id"]), fmt.Sprint(war["territory_key"])
		left, err := warActPointsLeftTx(conn, catalog, warID, userID)
		if err != nil {
			return nil, err
		}
		wars = append(wars, map[string]any{
			"war_id": warID, "attacker_key": war["attacker_key"], "defender_key": war["defender_key"],
			"territory_key": territory, "territory_name": territoryName(conn, territory),
			"territory_defense": territoryDefenseTx(conn, territory),
			"side":              side, "fights_for": fightsFor, "ally": fightsFor != sect,
			"siege_progress": i64(war["siege_progress"]), "attacker_morale": i64(war["attacker_morale"]),
			"defender_morale": i64(war["defender_morale"]), "points_left": left,
		})
	}
	out["wars"] = wars
	return out, nil
}

// warActPointsLeftTx is what is left of WarPointsCap for a member in a war.
func warActPointsLeftTx(conn *storage.Conn, catalog worlddata.Catalog, warID, userID int64) (int64, error) {
	rules := warRules(catalog)
	r, err := conn.Execute(`SELECT COUNT(*) AS n FROM territory_war_actions WHERE war_id=? AND user_id=?`, []any{warID, userID})
	if err != nil {
		return 0, err
	}
	return max64(0, rules.WarPointsCap-i64(firstRowMap(r)["n"])*rules.ActPoints), nil
}

// warPoliticsWinnerGain and warPoliticsLoserLoss are what a war's end moves on
// each side's `sect_politics_state` influence and resources (v1.29.0).
const (
	warPoliticsWinnerGain = int64(5)
	warPoliticsLoserLoss  = int64(5)
)

func warPoliticsOutcomeTx(conn *storage.Conn, winner, loser string, now float64) error {
	if !tableExistsTx(conn, "sect_politics_state") || winner == loser {
		return nil
	}
	if _, err := conn.Execute(`UPDATE sect_politics_state SET influence=MIN(100,influence+?),resources=MIN(100,resources+?),updated_at=? WHERE sect_name=?`,
		[]any{warPoliticsWinnerGain, warPoliticsWinnerGain, now, winner}); err != nil {
		return err
	}
	_, err := conn.Execute(`UPDATE sect_politics_state SET influence=MAX(0,influence-?),resources=MAX(0,resources-?),updated_at=? WHERE sect_name=?`,
		[]any{warPoliticsLoserLoss, warPoliticsLoserLoss, now, loser})
	return err
}

// SetAsidePartialHoldingsTx is the repair for a world that holds banners over
// parts of cities. A sect holds a city, not one of its streets
// (`TerritoryGround`), but three writers once took any row - `territory.claim`,
// both banner branches of `ResolveWarTx` and the war step's targets - and
// migration 74 left an active war over each gate it neutralised, so a world
// that has run for a while can hold a banner on a street or fight over one.
// The writers hold the rule now; this puts right what they left. It runs every
// maintenance pass, outside the automation flags and ahead of the siege tick,
// and is idempotent: a clean world is a pair of reads.
//
//   - Every active war over a part is set aside (`ResolveWarTx` ends it with no
//     victor), so the tick never fights it and no resolver hands a street over.
//   - Where exactly one sect holds parts of a city and nobody holds the city or
//     fights for it, the sect is folded into the city - the claim it would
//     have made from that street today. Where two sects hold parts there, or
//     another holds the city, nobody is given anything.
//   - Every held part is released, so what it paid in tribute and what it
//     offered as a war target stops. Releasing a banner is the one thing a
//     migration could not do: it needs the catalogue to know which keys are
//     parts, and a migration must not read the content a later release changes.
//
// It returns how many wars it set aside and banners it released.
func SetAsidePartialHoldingsTx(conn *storage.Conn, catalog worlddata.Catalog, gm int64, now float64) (int64, error) {
	if !tableExistsTx(conn, "territory_state") || !tableExistsTx(conn, "territory_wars") {
		return 0, nil
	}
	changed := int64(0)
	r, err := conn.Execute(`SELECT war_id,territory_key FROM territory_wars WHERE status='active' ORDER BY war_id`, nil)
	if err != nil {
		return 0, err
	}
	for _, row := range r.Rows {
		if _, part := TerritoryGround(catalog, fmt.Sprint(row[1])); !part {
			continue
		}
		_, ended, err := ResolveWarTx(conn, catalog, storage.ParseInt(row[0]), "", WarSetAside, gm, now)
		if err != nil {
			return changed, err
		}
		if ended {
			changed++
		}
	}
	r, err = conn.Execute(`SELECT territory_key,controller_key FROM territory_state WHERE controller_type='sect' AND controller_key<>'' ORDER BY territory_key`, nil)
	if err != nil {
		return changed, err
	}
	parts := []string{}
	holders := map[string]map[string]bool{}
	for _, row := range r.Rows {
		key, sect := fmt.Sprint(row[0]), fmt.Sprint(row[1])
		whole, part := TerritoryGround(catalog, key)
		if !part {
			continue
		}
		parts = append(parts, key)
		if whole == "" {
			continue
		}
		if holders[whole] == nil {
			holders[whole] = map[string]bool{}
		}
		holders[whole][sect] = true
	}
	wholes := make([]string, 0, len(holders))
	for whole := range holders {
		wholes = append(wholes, whole)
	}
	sort.Strings(wholes)
	for _, whole := range wholes {
		if len(holders[whole]) != 1 {
			continue
		}
		sect := ""
		for s := range holders[whole] {
			sect = s
		}
		up, err := conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,updated_game_minute=?,updated_at=?
            WHERE territory_key=? AND (controller_type='neutral' OR controller_key='')
              AND NOT EXISTS (SELECT 1 FROM territory_wars w WHERE w.territory_key=territory_state.territory_key AND w.status='active')`,
			[]any{sect, gm, now, whole})
		if err != nil {
			return changed, err
		}
		if up.RowsAffected == 1 {
			RecordTerritoryClaimedTx(conn, catalog, sect, whole, gm, now)
		}
	}
	for _, key := range parts {
		if _, err := conn.Execute(`UPDATE territory_state SET controller_type='neutral',controller_key='',updated_game_minute=?,updated_at=? WHERE territory_key=?`,
			[]any{gm, now, key}); err != nil {
			return changed, err
		}
		changed++
	}
	return changed, nil
}
