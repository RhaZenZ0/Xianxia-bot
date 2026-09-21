package game

import (
	"encoding/json"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// An era belongs to one world (v1.0.7).
//
// There was one era for the whole game. Every reader asked
// `WHERE active=1 ORDER BY era_id DESC LIMIT 1` and got the same row whether it
// was pricing a siege in the Celestial World or a cultivation session in a
// Mortal village - while the realm capitals have been split per world since
// schema 4, the auction floors since schema 35 and a world's *news* since
// schema 56. The era was the last thing in the game that pretended the four
// worlds were one place.
//
// **Every reader was already about something that has a location**, which is
// what made the split small rather than structural: a cultivator stands
// somewhere, a territory is somewhere, a war is over somewhere, a caravan runs
// between two somewheres, and since v1.0.6 the bounty sweep reads its quarry's
// location too. So each one resolves the world it was already talking about
// instead of taking the only row there was.
//
// `DefaultEraWorld` is the answer when a location resolves to no world at all -
// a private residence (`birth_family:<id>`), an inner world
// (`personal_world:<uid>`), an abode, or the literal `Unknown` two writers can
// still produce. It is deliberately the Mortal World rather than "no era":
// schema 60 backfills every existing row to the Mortal World for the same
// reason, and a cultivator meditating in their own household should be under
// *some* age of the world rather than outside history. This is the one place
// the tree spells that default, unlike `world_of_location` on the Python side,
// which answers None precisely so a household's news is not filed as a world's.
const DefaultEraWorld = "Mortal World"

// EraWorldOf names the world an era question is being asked about.
func EraWorldOf(catalog worlddata.Catalog, location string) string {
	if loc, ok := catalog.Locations[strings.TrimSpace(location)]; ok {
		if world := strings.TrimSpace(loc.World); world != "" {
			return world
		}
	}
	return DefaultEraWorld
}

// ActiveEra is the one door onto a world's current age: its name and the
// modifiers it is applying. A world with no row yet - every world but the
// Mortal one, until the first tick after schema 60 - answers an empty name and
// an empty map, which every caller reads as "no era term", never as a zero.
func ActiveEra(conn *storage.Conn, world string) (string, map[string]float64, error) {
	world = strings.TrimSpace(world)
	if world == "" {
		world = DefaultEraWorld
	}
	res, err := conn.Execute(
		`SELECT name,modifiers_json FROM world_eras WHERE active=1 AND world=? ORDER BY era_id DESC LIMIT 1`,
		[]any{world},
	)
	if err != nil {
		return "", nil, err
	}
	if len(res.Rows) == 0 {
		return "", map[string]float64{}, nil
	}
	name := fmt.Sprint(res.Rows[0][0])
	raw := map[string]any{}
	_ = json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][1])), &raw)
	mods := make(map[string]float64, len(raw))
	for key, value := range raw {
		switch x := value.(type) {
		case float64:
			mods[key] = x
		case int64:
			mods[key] = float64(x)
		}
	}
	return name, mods, nil
}

// eraTerm is one key off an era's already-loaded modifiers.
//
// It exists so that every read of an era modifier in this tree is an *argument
// position* rather than a bare map index. That is not style: it is what lets
// `era_vocabulary_test.go` tell a rule fetching a key from the content file
// declaring one, which is the rc.58 "fetched, not named" distinction. The gate
// found this on its own first run - `eraCultivationMultiplier` indexed the map
// directly, so the one modifier every cultivator feels looked unread.
func eraTerm(mods map[string]float64, key string, def float64) float64 {
	if v, ok := mods[key]; ok {
		return v
	}
	return def
}

// EraModifier is a world's multiplier for one key, or `def` when that world has
// no era or its era is silent about it.
//
// A missing key is `def` rather than zero, and that distinction is the
// `seller_user_id=0` lesson in a place where it would be invisible: every one
// of these keys is a *multiplier*, so a key read as 0 would not soften a rule,
// it would delete it - no cultivation gain at all, no siege power, no caravan
// ever arriving.
func EraModifier(conn *storage.Conn, world, key string, def float64) float64 {
	_, mods, err := ActiveEra(conn, world)
	if err != nil {
		return def
	}
	return eraTerm(mods, key, def)
}

// characterEraWorld is the world a cultivator is standing in, for the readers
// that hold a user id and no location.
//
// It answers `DefaultEraWorld` for every failure rather than propagating one,
// because its caller is `loadSeclusionCarried`, which rc.55 wrote to never fail:
// a retreat must not become refusable because a row could not be read. The cost
// of being wrong is one era term, and the Mortal World's cycle is the one
// schema 60 already backfills every existing row to.
func characterEraWorld(conn *storage.Conn, catalog worlddata.Catalog, userID int64) string {
	res, err := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if err != nil || len(res.Rows) == 0 {
		return DefaultEraWorld
	}
	return EraWorldOf(catalog, fmt.Sprint(res.Rows[0][0]))
}
