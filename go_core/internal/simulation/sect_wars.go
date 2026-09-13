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
	tp, err := gamerng.Intn(len(targets.Rows))
	if err != nil {
		return 0, err
	}
	territory := fmt.Sprint(targets.Rows[tp][0])
	defender := fmt.Sprint(targets.Rows[tp][1])

	now := nowFloat()
	ins, err := conn.Execute(`INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_game_minute,updated_game_minute,created_at,updated_at)
        VALUES(?,?,?,'active',?,?,?,?)`, []any{attacker, defender, territory, gm, gm, now, now})
	if err != nil {
		return 0, err
	}
	// The same row `territory.claim` opens for a player's war - the column
	// list is copied from `ensureWarOperationGo` (game/territory_actions.go:74)
	// rather than written afresh, because a siege with no operation row is a
	// war the tick cannot fight.
	if simTableExists(conn, "territory_war_operations") {
		if _, err = conn.Execute(`INSERT INTO territory_war_operations(war_id,siege_progress,attacker_morale,defender_morale,attacker_force,defender_force,last_tick_game_minute,winner_key,resolution,occupation_until_game_minute,updated_at) VALUES(?,0,100,100,0,0,?,'','',0,?) ON CONFLICT(war_id) DO NOTHING`,
			[]any{ins.LastInsertID, gm, now}); err != nil {
			return 0, err
		}
	}
	r.recordWarDeclared(conn, attacker, defender, territory, gm, now)
	return 1, nil
}

// recordWarDeclared puts it where the world can hear about it. A sect moving
// on another's ground is the loudest thing that happens in this world.
func (r *Runner) recordWarDeclared(conn *storage.Conn, attacker, defender, territory string, gm int64, now float64) {
	if !simTableExists(conn, "world_history_events") {
		return
	}
	title := attacker + " declares on " + defender
	summary := fmt.Sprintf("%s has moved on %s, held by %s. The border is contested.", attacker, territory, defender)
	source := fmt.Sprintf("sect_war:%s:%s:%d", attacker, territory, gm)
	_, _ = conn.Execute(`INSERT INTO world_history_events(
        source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
        actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
        related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?, 'public', '','',?, 'faction',?,?, 'faction',?,?, NULL,'',?,?,?,?,?)
        ON CONFLICT(source_key) DO NOTHING`,
		[]any{source, "territory_war", title, summary, 78, attacker,
			attacker, attacker, defender, defender, "territory war " + territory, gm, "{}", now, now})
}
