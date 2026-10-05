package simulation

// Sects that want a thing go and take it.
//
// `territory_wars` has one writer in the whole codebase - `territory.claim`
// (`game/territory_actions.go:119`) - and it always makes the *acting player's*
// sect the attacker. So the siege resolver beside it, with its scores, morale,
// occupations and era war-pressure modifier, only ever ran on wars a player
// started. Thirteen sects in the world and not one of them could ever move on
// another's ground: the map was only ever contested by people at keyboards.
//
// The politics tick already maintains everything needed to decide who would.
// A sect with influence and resources and nothing holding it together at home
// looks outward; a territory held weakly by a rival is what it looks at.

import (
	"fmt"
	"sort"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// A sect needs standing and means before it moves on anyone.
	warMinInfluence = 62
	warMinResources = 55
	// ...and a reason: a border it can actually hold is not worth a war.
	warDefenceCeiling = 62
	// Rare. A world at permanent war is as dead as one at permanent peace.
	warDeclareChance = 12
)

// npcSectWars lets one ambitious sect declare on one weakly-held territory.
func (r *Runner) npcSectWars(conn *storage.Conn, steps, gm int64) (int64, error) {
	if !simTableExists(conn, "territory_wars") || !simTableExists(conn, "territory_state") ||
		!simTableExists(conn, "sect_politics_state") {
		return 0, nil
	}
	roll, err := gamerng.Intn(100)
	if err != nil {
		return 0, err
	}
	if int64(roll) >= min64(48, warDeclareChance*max1(min64(3, steps))) {
		return 0, nil
	}

	// Who is looking outward. Ordered, because map order must not decide who
	// goes to war.
	res, err := conn.Execute(`SELECT sect_name FROM sect_politics_state
        WHERE influence>=? AND resources>=? ORDER BY influence DESC,sect_name`,
		[]any{warMinInfluence, warMinResources})
	if err != nil || len(res.Rows) == 0 {
		return 0, err
	}
	candidates := make([]string, 0, len(res.Rows))
	for _, row := range res.Rows {
		candidates = append(candidates, fmt.Sprint(row[0]))
	}
	sort.Strings(candidates)
	pick, err := gamerng.Intn(len(candidates))
	if err != nil {
		return 0, err
	}
	attacker := candidates[pick]

	// What it wants: somebody else's ground, weakly held, not already contested.
	targets, err := conn.Execute(`SELECT t.territory_key,t.controller_key FROM territory_state t
        WHERE t.controller_type='sect' AND t.controller_key<>'' AND t.controller_key<>?
          AND t.defense<=?
          AND NOT EXISTS (SELECT 1 FROM territory_wars w WHERE w.territory_key=t.territory_key AND w.status='active')
        ORDER BY t.defense,t.territory_key`, []any{attacker, warDefenceCeiling})
	if err != nil || len(targets.Rows) == 0 {
		return 0, err
	}
	// A failed attacker's truce is the war door's rule, asked rather than
	// restated (v1.24.0); and ground taken from this sect and still under
	// occupation is where it looks first - an occupation is the window to
	// win it back.
	open, retakes := [][]any{}, [][]any{}
	for _, row := range targets.Rows {
		key := fmt.Sprint(row[0])
		if game.WarTruceUntilTx(conn, r.World, attacker, key, gm) > 0 {
			continue
		}
		open = append(open, row)
		if game.WarOccupiedFromTx(conn, key, gm) == attacker {
			retakes = append(retakes, row)
		}
	}
	if len(retakes) > 0 {
		open = retakes
	}
	if len(open) == 0 {
		return 0, nil
	}
	tp, err := gamerng.Intn(len(open))
	if err != nil {
		return 0, err
	}
	territory := fmt.Sprint(open[tp][0])
	defender := fmt.Sprint(open[tp][1])

	// The one door for a declaration (v1.24.0): the war, its operation row,
	// the standing between the two sects and the history row are
	// game.DeclareWarTx's, shared with a player's territory.claim.
	if _, err = game.DeclareWarTx(conn, r.World, attacker, defender, territory, gm, nowFloat()); err != nil {
		return 0, err
	}
	return 1, nil
}
