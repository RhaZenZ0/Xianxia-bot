package game

import (
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What the household teaches, and how well (v1.0.0-rc.31).
//
// Every one of the thirteen birth families names a trade in its send-off
// (`birth_family_sendoff` in world.json), and `teachHouseholdMethodsTx` has
// handed every child the entry methods of that trade since rc.20. What varied
// was nothing: the only bonus in the game was +2 on Alchemy rolls, keyed on
// the archetype string "alchemy_family" - so body_tempering_family, which
// teaches Alchemy, got nothing, and the five Forging houses, three Inscription
// and three Formation got nothing for the trade they teach. Two things vary
// now, and both read data the household already carries.
//
// Tradition is flat: +2 on rolls for the trade the send-off names, whichever
// family, whichever trade. Tutoring is what the household could afford: a
// head start in that trade's `profession_progress`, banded on the family's
// Wealth at the moment the child leaves. The ruined clan at 26 shows you the
// basics; the imperial clan at 82 retained a master and you leave as an
// Apprentice. It is capped at level 1 - a head start, not mastery - and it is
// handed over once, guarded on the row itself, so a dao-family rebirth or a
// samsara return that arrives with progress keeps it.

// householdTradeBonus is what a household's tradition adds to a roll in its
// own trade.
const householdTradeBonus = 2

// householdTradeFor is the trade a household teaches, or "" for an archetype
// whose send-off names none.
func householdTradeFor(catalog worlddata.Catalog, archetype string) string {
	return strings.TrimSpace(catalog.BirthFamilySendoff[strings.TrimSpace(archetype)].Trade)
}

// householdTutoring is the head start a household's Wealth buys in its trade:
// the profession level and XP to start at, and how to say who taught you.
//
// The bands are read off the shipped spread (26 to 82) so each holds real
// households: below 40 the fallen martial clan, the tomb-watch clan and the
// body-tempering family; 40-59 the eight middling houses; 60-79 the alchemy
// family; 80 and above the noble martial clan. Tier would not tell the fallen
// clan (26) from the martial household (42) - both are tier 2 - and the fallen
// clan should teach worse. professionXPNeeded(0) is 60, so 30 is halfway to
// Apprentice and 55 is one good craft short of it.
func householdTutoring(wealth int64) (level, xp int64, tutor string) {
	switch {
	case wealth >= 80:
		return 1, 0, "a master retained"
	case wealth >= 60:
		return 0, 55, "a hired tutor"
	case wealth >= 40:
		return 0, 30, "a journeyman in the family"
	default:
		return 0, 0, "shown the basics"
	}
}

// householdArchetypeTx is the archetype of the household a character was born
// into, or "" when they were not born into one.
func householdArchetypeTx(conn *storage.Conn, userID int64) (string, error) {
	res, err := conn.Execute(
		`SELECT f.archetype
		   FROM character_birth_family c
		   JOIN birth_families f ON f.family_id=c.family_id
		  WHERE c.user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return "", err
	}
	if row := firstRowMap(res); row != nil {
		return strings.TrimSpace(fmt.Sprint(row["archetype"])), nil
	}
	return "", nil
}

// householdTradeBonusTx is the tradition bonus a character carries into a
// roll of the given profession, and the trade their household teaches. Zero
// and "" for somebody with no household, or one whose trade this is not.
func householdTradeBonusTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, profession string) (int64, string, error) {
	archetype, err := householdArchetypeTx(conn, userID)
	if err != nil || archetype == "" {
		return 0, "", err
	}
	trade := householdTradeFor(catalog, archetype)
	if trade != "" && strings.EqualFold(trade, strings.TrimSpace(profession)) {
		return householdTradeBonus, trade, nil
	}
	return 0, trade, nil
}

// householdForageBonusTx is the tradition bonus on a forage: the hills are
// Alchemy's gathering half, so it is the Alchemy houses' bonus and nobody
// else's.
func householdForageBonusTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (int64, string, error) {
	return householdTradeBonusTx(conn, catalog, userID, "Alchemy")
}

// tutorHouseholdTradeTx hands over the household's head start in its trade,
// once. The `profession_progress` row is the memory: one already there - from
// a previous life, or from having crafted before the send-off reached this
// door - is left exactly as it is, so a rebirth can never reset what was
// earned. Reports what the household did either way, because the send-off
// prose says who taught you whether or not the row was new.
func tutorHouseholdTradeTx(conn *storage.Conn, catalog worlddata.Catalog, userID, familyID int64, archetype string, now float64) (map[string]any, error) {
	trade := householdTradeFor(catalog, archetype)
	if trade == "" || !tableExistsTx(conn, "profession_progress") {
		return nil, nil
	}
	res, err := conn.Execute(`SELECT wealth FROM birth_families WHERE family_id=?`, []any{familyID})
	if err != nil {
		return nil, err
	}
	wealth := int64(0)
	if row := firstRowMap(res); row != nil {
		wealth = i64(row["wealth"])
	}
	level, xp, tutor := householdTutoring(wealth)
	held, err := boolRow(conn, `SELECT 1 FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, trade})
	if err != nil {
		return nil, err
	}
	granted := false
	if !held {
		if _, err := conn.Execute(
			`INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
			 VALUES(?,?,?,?,0,0,0,?) ON CONFLICT(user_id,profession) DO NOTHING`,
			[]any{userID, trade, level, xp, now}); err != nil {
			return nil, err
		}
		granted = true
	}
	return map[string]any{
		"profession": trade, "tutor": tutor, "level": level, "xp": xp,
		"wealth": wealth, "granted": granted,
	}, nil
}
