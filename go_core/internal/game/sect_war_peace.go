package game

// The three things a war did not do (v1.24.0, on the owner's call): the world's
// own sects never fought beside an ally, a war could only end on the walls or
// on morale, and a sect's NPC disciples - the people on its rolls - took no part
// in its wars at all. The rules are here; the tick that fights the world's
// sieges calls them, and `war.peace` is a member's own door.

import (
	"errors"
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// endOtherOccupationsTx ends every occupation still running on a territory
// other than warID's: whoever holds the ground now, an older occupier's days
// must not come due and hand it back.
func endOtherOccupationsTx(conn *storage.Conn, warID int64, territory string, now float64) error {
	if !tableExistsTx(conn, "territory_war_operations") {
		return nil
	}
	_, err := conn.Execute(`UPDATE territory_war_operations SET resolution='occupation_lost',occupation_until_game_minute=0,updated_at=?
        WHERE resolution='attacker_occupation' AND war_id<>? AND war_id IN (SELECT war_id FROM territory_wars WHERE territory_key=?)`,
		[]any{now, warID, territory})
	return err
}

// warActionsHaveSect reports whether migration 78 has given the blow-by-blow
// the sect each blow was struck for. Before it a war is fought exactly as it
// was; the column only sharpens who joined and lets an ally sect's blows be
// told apart from the field's.
func warActionsHaveSect(conn *storage.Conn) bool {
	ok, err := tableHasColumns(conn, "territory_war_actions", "sect_name")
	return err == nil && ok
}

// WarActionsHaveSect is warActionsHaveSect for the simulation.
func WarActionsHaveSect(conn *storage.Conn) bool { return warActionsHaveSect(conn) }

// WarAllyJoinsTx is warAllyJoinsTx for the world's own sects, which also tells
// the world: a sect marching beside its ally is news, a cultivator is not.
func WarAllyJoinsTx(conn *storage.Conn, catalog worlddata.Catalog, warID int64, ally, beside, enemy, territory string, gm int64, now float64) (bool, error) {
	joined, err := warAllyJoinsTx(conn, catalog, warID, ally, enemy, now)
	if err != nil || !joined {
		return joined, err
	}
	name := territoryName(conn, territory)
	return true, recordWorldHistoryTx(conn, fmt.Sprintf("sect_war_ally:%d:%s", warID, ally), "territory_war_ally",
		ally+" marches beside "+beside, fmt.Sprintf("%s has sent its people to fight beside %s for %s, against %s.", ally, beside, name, enemy),
		70, "public", territory, ally, "faction", ally, ally, "faction", enemy, enemy, nil, "",
		[]string{"territory", "war", "alliance", territory}, gm, map[string]any{"war_id": warID}, now)
}

// WarAlliesTx is every sect the politics tick knows that stands allied to
// exactly one side of a war: the attacker's allies, then the defender's, each
// sorted. A sect allied to both stays out, as a cultivator of one must.
func WarAlliesTx(conn *storage.Conn, catalog worlddata.Catalog, attacker, defender string) (forAttacker, forDefender []string) {
	if !tableExistsTx(conn, "sect_politics_state") {
		return nil, nil
	}
	r, err := conn.Execute(`SELECT sect_name FROM sect_politics_state ORDER BY sect_name`, nil)
	if err != nil {
		return nil, nil
	}
	for _, row := range r.Rows {
		sect := fmt.Sprint(row[0])
		if sect == attacker || sect == defender {
			continue
		}
		withA, withD := SectsAlliedTx(conn, catalog, sect, attacker), SectsAlliedTx(conn, catalog, sect, defender)
		switch {
		case withA && !withD:
			forAttacker = append(forAttacker, sect)
		case withD && !withA:
			forDefender = append(forDefender, sect)
		}
	}
	return forAttacker, forDefender
}

// WarAllyStrength is what a side's allies lend it on a day of siege, given
// each ally's own strength: AllyStrengthPercent of the sum, at most
// AllyStrengthCap.
func WarAllyStrength(catalog worlddata.Catalog, strengths []float64) float64 {
	rules := warRules(catalog)
	sum := 0.0
	for _, s := range strengths {
		sum += s
	}
	return minFloat(float64(rules.AllyStrengthCap), sum*float64(rules.AllyStrengthPercent)/100)
}

// WarDisciplesTx is how many living NPC disciples a sect counts, read off the
// rolls the politics tick keeps (`npc_civilization_state.faction`).
func WarDisciplesTx(conn *storage.Conn, sect string) int64 {
	if !tableExistsTx(conn, "npc_civilization_state") {
		return 0
	}
	r, err := conn.Execute(`SELECT COUNT(*) FROM npc_civilization_state WHERE faction=? AND status='alive'`, []any{sect})
	if err != nil || len(r.Rows) == 0 {
		return 0
	}
	return i64(r.Rows[0][0])
}

// WarDiscipleStrength is what a sect's disciples add to a day of siege: one
// for every DisciplesPerPoint of them, at most DiscipleStrengthCap.
func WarDiscipleStrength(catalog worlddata.Catalog, disciples int64) float64 {
	rules := warRules(catalog)
	return minFloat(float64(rules.DiscipleStrengthCap), float64(max64(0, disciples))/float64(rules.DisciplesPerPoint))
}

// MusterDisciplesTx names a sect's weightiest living disciples at a siege, so
// `/civilization` and the GM's NPC card say where they are. It returns how
// many were named and never fails the siege over it.
func MusterDisciplesTx(conn *storage.Conn, catalog worlddata.Catalog, sect, territory string, now float64) int64 {
	n := warRules(catalog).DisciplesAtTheWalls
	if n <= 0 || !tableExistsTx(conn, "npc_civilization_state") {
		return 0
	}
	activity := fmt.Sprintf("At the siege of %s, for %s", territoryName(conn, territory), sect)
	res, err := conn.Execute(`UPDATE npc_civilization_state SET activity=?,updated_at=? WHERE npc_name IN (
        SELECT npc_name FROM npc_civilization_state WHERE faction=? AND status='alive' ORDER BY influence DESC,npc_name LIMIT ?)`,
		[]any{activity, now, sect, n})
	if err != nil {
		return 0
	}
	return res.RowsAffected
}

// PeaceTerms is how a war ends at the table: below PeaceCedeSiege the holder
// keeps the ground (peace), at or above it the ground is ceded to the
// besieger. The winner named is whoever holds the ground after.
func PeaceTerms(catalog worlddata.Catalog, attacker, defender string, siege int64) (winner, resolution string) {
	if siege >= warRules(catalog).PeaceCedeSiege {
		return attacker, "ceded"
	}
	return defender, "peace"
}

// NPCSuesForPeace is whether the world's own sects end a war at the table:
// once it is PeaceMinDays old and either side's morale has fallen to
// NPCPeaceMorale. It names the terms PeaceTerms gives.
func NPCSuesForPeace(catalog worlddata.Catalog, attacker, defender string, created, gm, siege, attackerMorale, defenderMorale int64) (winner, resolution string, ok bool) {
	rules := warRules(catalog)
	if gm < created+rules.PeaceMinDays*warMinutesPerDay {
		return "", "", false
	}
	if attackerMorale > rules.NPCPeaceMorale && defenderMorale > rules.NPCPeaceMorale {
		return "", "", false
	}
	winner, resolution = PeaceTerms(catalog, attacker, defender, siege)
	return winner, resolution, true
}

// warPeaceActionGo is a member of a warring sect suing for peace: a
// belligerent's own member - an ally has no standing to make terms - at
// PeaceMinRankLevel or above, once the war is PeaceMinDays old, paying
// PeaceCostPoints of their own contribution. The terms are PeaceTerms'.
func warPeaceActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID, warID, gm int64) (map[string]any, error) {
	rules := warRules(catalog)
	mem, err := sectMembershipRow(conn, userID)
	if err != nil {
		return nil, err
	}
	if mem == nil {
		return nil, errors.New("sect membership is required")
	}
	r, err := conn.Execute(`SELECT w.*,o.siege_progress FROM territory_wars w LEFT JOIN territory_war_operations o ON o.war_id=w.war_id WHERE w.war_id=? AND w.status='active'`, []any{warID})
	if err != nil {
		return nil, err
	}
	war := firstRowMap(r)
	if war == nil {
		return nil, errors.New("active war not found")
	}
	sect := fmt.Sprint(mem["sect_name"])
	attacker, defender := fmt.Sprint(war["attacker_key"]), fmt.Sprint(war["defender_key"])
	if sect != attacker && sect != defender {
		return nil, errors.New("only a sect fighting the war may make its peace; an ally has no standing at the table")
	}
	if rank := i64(mem["rank_level"]); rank < rules.PeaceMinRankLevel {
		return nil, fmt.Errorf("peace is made by a %s or above; you are a %s", sectRankName(catalog, rules.PeaceMinRankLevel), sectRankName(catalog, rank))
	}
	created := i64(war["created_game_minute"])
	if ready := created + rules.PeaceMinDays*warMinutesPerDay; gm < ready {
		return nil, fmt.Errorf("a war %d day(s) old is not ready for terms; peace may be sued for in %d more day(s)",
			max64(0, (gm-created)/warMinutesPerDay), (ready-gm+warMinutesPerDay-1)/warMinutesPerDay)
	}
	if held := i64(mem["contribution_points"]); held < rules.PeaceCostPoints {
		return nil, fmt.Errorf("suing for peace costs %d sect contribution; you hold %d", rules.PeaceCostPoints, held)
	}
	// The balance only: spending never costs a rank (v1.8.0).
	if _, err = conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points-? WHERE user_id=?`, []any{rules.PeaceCostPoints, userID}); err != nil {
		return nil, err
	}
	siege := i64(war["siege_progress"])
	winner, resolution := PeaceTerms(catalog, attacker, defender, siege)
	if _, _, err = ResolveWarTx(conn, catalog, warID, winner, resolution, gm, nowSeconds()); err != nil {
		return nil, err
	}
	territory := fmt.Sprint(war["territory_key"])
	return map[string]any{"war_id": warID, "status": "resolved", "resolution": resolution, "winner_key": winner,
		"attacker_key": attacker, "defender_key": defender, "territory_key": territory, "territory_name": territoryName(conn, territory),
		"siege_progress": siege, "cost": rules.PeaceCostPoints, "sued_by": sect}, nil
}

func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
