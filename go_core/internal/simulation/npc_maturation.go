package simulation

// Children who grow up (v1.0.0-rc.27, schema 49).
//
// `npcChildbirth` has been writing `npc_descendants` rows since the life cycle
// was built, and every one of them sets `generated_as_npc` to 0. Nothing has
// ever set it to 1, and nothing has ever read it, because until schema 49
// there was nowhere to promote a child *into*: `catalog_npcs` is wiped and
// rewritten from `content/world.json` at every boot, and
// `npc_civilization_state` has no room for who somebody is.
//
// So the world's own children were named, recorded, counted in their parents'
// `children_count` - and then nothing. They never aged into anybody, never
// stood anywhere, could not be talked to, could not marry, and could not have
// children of their own. A world that produced people who were permanently
// four years old is not reproducing; it is keeping a list.
//
// This is the step that finishes the loop. At `maturityYears` a descendant
// becomes a person: prose out of `npc_generated_traits`, a row in
// `npc_registry` so `/talk` can find them, and rows in the two simulation
// tables so every tick that reads `WHERE status='alive'` starts offering them.
// From that moment they are an ordinary NPC - the romance step can court them,
// the deeds step can send them out, and `npcNaturalDeaths` will eventually
// bury them.

import (
	"fmt"

	"xianxia/core/internal/game"
	"xianxia/core/internal/storage"
)

const (
	// Eighteen, the same age a player's character starts at. A child of this
	// world should not come of age on a different clock from a child of it
	// who happens to be played.
	maturityYears = 18
	// Per tick, for the reason every other step in this package is capped: a
	// world where forty strangers appear overnight is not a generation, it is
	// a crowd.
	maturityCap = 6
	// What they are worth to the chronicle. Well under the Quest Forge's bar
	// of 80 on purpose - somebody growing up is a thing a town notices and not
	// a thing it needs a cultivator to go and do something about.
	maturitySignificance = 26
)

// npcMaturation promotes the grown children. Returns how many came of age.
func (r *Runner) npcMaturation(conn *storage.Conn, gm int64) (int64, error) {
	if !simTableExists(conn, "npc_descendants") || !simTableExists(conn, "npc_registry") {
		return 0, nil
	}
	grown := gm - int64(maturityYears)*minutesPerYear
	if grown <= 0 {
		return 0, nil
	}
	res, err := conn.Execute(`SELECT child_name,parent_a,parent_b,birth_game_minute,spiritual_root,realm_index,phase
        FROM npc_descendants
        WHERE status='alive' AND generated_as_npc=0 AND birth_game_minute>0 AND birth_game_minute<=?
        ORDER BY birth_game_minute,child_name LIMIT ?`, []any{grown, maturityCap})
	if err != nil {
		return 0, err
	}
	now := nowFloat()
	matured := int64(0)
	for _, row := range res.Rows {
		name := fmt.Sprint(row[0])
		parentA, parentB := fmt.Sprint(row[1]), fmt.Sprint(row[2])
		birth := i64(row[3])
		realmIndex, phase := i64(row[5]), max1(i64(row[6]))

		// Where the household is. A child comes of age where their parents
		// live, not where they were standing when the tick ran - and if both
		// parents are gone, at the home the elder of them kept.
		home, world, faction := r.householdOf(conn, parentA, parentB)
		if home == "" {
			// Nobody left to grow up beside and nowhere to put them. Leave the
			// row alone: a later tick may find a parent again, and inventing a
			// location for them would put a stranger in an arbitrary town.
			continue
		}

		person := game.GenerateNPCTraits(r.World.GeneratedTraits, name)
		person.Origin = game.NPCOriginDescendant
		person.Realm = r.realmName(realmIndex)
		person.Location = home
		person.SectAffiliation = ""
		person.SourceKey = fmt.Sprintf("descendant:%s", name)
		registered, err := game.RegisterNPCTx(conn, person, gm)
		if err != nil {
			return matured, err
		}
		if !registered {
			// The name is already the catalogue's, or already registered. Mark
			// the descendant done anyway so the query stops offering them
			// every tick forever.
			if _, err := conn.Execute(
				`UPDATE npc_descendants SET generated_as_npc=1,updated_at=? WHERE child_name=?`,
				[]any{now, name}); err != nil {
				return matured, err
			}
			continue
		}

		// The two simulation rows, shaped the way bootstrap shapes everybody
		// else's. `age_at_creation_years` is 0 and `birth_game_minute` is the
		// minute they were actually born, so lifespanmodel.AgeYears gives a
		// real age rather than eighteen forever.
		if _, err := conn.Execute(`INSERT INTO npc_civilization_state(
            npc_name,home_location,current_location,world_name,profession,faction,
            wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?, 'alive','Newly grown',?,?) ON CONFLICT(npc_name) DO NOTHING`,
			[]any{name, home, home, world, person.Role, faction,
				int64(10), int64(5), int64(40 + hash64(name, "ambition")%40),
				realmIndex, phase, gm, now}); err != nil {
			return matured, err
		}
		if _, err := conn.Execute(`INSERT INTO npc_life_state(
            npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,
            injury,injury_severity,sect_rank,career_progress,relationship_status,spouse_name,
            children_count,last_social_game_minute,last_cultivation_game_minute,updated_at)
            VALUES(?,?,0,?,100,'',0,'Independent Cultivator',0,'single','',0,?,?,?)
            ON CONFLICT(npc_name) DO NOTHING`,
			[]any{name, birth, int64(70 + hash64(name, "lifespan")%16), gm, gm, now}); err != nil {
			return matured, err
		}
		if _, err := conn.Execute(
			`UPDATE npc_descendants SET generated_as_npc=1,updated_at=? WHERE child_name=?`,
			[]any{now, name}); err != nil {
			return matured, err
		}
		r.recordNPCHistory(conn, "npc_coming_of_age", fmt.Sprintf("npc_coming_of_age:%s:%d", name, gm),
			name+" comes of age",
			fmt.Sprintf("%s, born to %s and %s, is grown and counted among the people of %s.",
				name, parentA, parentB, home),
			home, name, maturitySignificance, gm, now)
		matured++
	}
	return matured, nil
}

// householdOf is where a child grew up: the home of whichever parent still has
// one, preferring the first named. Returns ("", "", "") when neither is left.
func (r *Runner) householdOf(conn *storage.Conn, parentA, parentB string) (string, string, string) {
	if !simTableExists(conn, "npc_civilization_state") {
		return "", "", ""
	}
	for _, parent := range []string{parentA, parentB} {
		if parent == "" {
			continue
		}
		res, err := conn.Execute(
			`SELECT home_location,world_name,faction FROM npc_civilization_state WHERE npc_name=?`,
			[]any{parent})
		if err != nil || len(res.Rows) == 0 {
			continue
		}
		home := fmt.Sprint(res.Rows[0][0])
		if home == "" {
			continue
		}
		return home, fmt.Sprint(res.Rows[0][1]), fmt.Sprint(res.Rows[0][2])
	}
	return "", "", ""
}

// realmName is what the registry prints for a realm index, read off the
// content file rather than spelled out here.
//
// The bound is compared in int64 rather than narrowing the index to `int`
// first. `realmIndex` comes out of the database through ParseInt, and on a
// 32-bit build `int(realmIndex)` truncates - a stored value above 2^31 wraps
// to something small, passes a `< len()` test written that way, and then
// panics when the slice is actually indexed with the full 64-bit value. Go
// indexes a slice with any integer type, so the conversion buys nothing and
// the check is exact at every word size. (CodeQL, go/incorrect-integer-
// conversion, on the first version of this.)
func (r *Runner) realmName(realmIndex int64) string {
	if realmIndex >= 0 && realmIndex < int64(len(r.World.Realms)) {
		if name := r.World.Realms[realmIndex].Name; name != "" {
			return name
		}
	}
	return "Mortal"
}
