package simulation

// What a sect's own disciples hand in.
//
// `sect_treasury` is the storehouse a disciple spends contribution points at,
// and its only writer was `sect.contribute`: a player walking in with something
// in their bags. So the treasury of a sect with no player members was empty
// when the world was made and empty a hundred years later, and the content's
// own `resource_policy` - "contribution points can be exchanged for stocked
// sect resources" - was true of no sect at all until somebody stocked one by
// hand. A disciple could earn points and find nothing on the shelves.
//
// The sects have their own people. Thirteen of them carry NPC disciples in
// `npc_civilization_state.faction`, and since v1.0.0-rc.15 those rolls change:
// `npcSectChanges` swears NPCs in and walks them out every tick. Those are the
// disciples whose tribute this is - they gather, they hand in, and the
// storehouse fills at a rate their own numbers set.
//
// What they bring is content (`sect_system.tribute`), resolved against the
// tier of the world the sect's gate stands in through the same
// `EventSites.Material` the world events and the birth-family send-off use, so
// one three-line list is correct for an Azure Cloud outer disciple and a
// Celestial Mandate Academy elder alike.

import (
	"fmt"
	"strings"
	"xianxia/core/internal/game"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// sectTributeCapFallback is used when content sets no cap. A storehouse that
// grows without limit stops being one.
const sectTributeCapFallback = int64(60)

// sectWorld is the world a sect belongs to: the world of the gate its trial is
// held at. A hidden sect has no public gate, so it has no tier here and takes
// no tribute - which is right for the Heaven-Devouring Demon Sect, whose
// disciples do not hand things in at a counter.
func (r *Runner) sectWorld(sectName string) string {
	sect, ok := r.World.Sects[sectName]
	if !ok || sect.Hidden {
		return ""
	}
	gate := strings.TrimSpace(sect.Recruitment.Location)
	if gate == "" {
		return ""
	}
	return r.World.Locations[gate].World
}

// sectTributeItems resolves the content list against one world's tier, keeping
// only what the item catalogue actually carries - the same guard the
// birth-family send-off and the world-event sites use, because a treasury row
// for an item that does not exist is a shelf nobody can buy from.
func sectTributeItems(catalog worlddata.Catalog, tribute worlddata.SectTribute, world string) []string {
	out := make([]string, 0, len(tribute.Materials))
	seen := map[string]bool{}
	for _, ref := range tribute.Materials {
		itemID := catalog.EventSites.Material(world, ref)
		if itemID == "" || seen[itemID] {
			continue
		}
		if _, _, ok := game.ItemDef(catalog, itemID); !ok {
			continue
		}
		seen[itemID] = true
		out = append(out, itemID)
	}
	return out
}

// sectTribute stocks every sect's storehouse from its own disciples' work.
//
// The caller owns the transaction; the count is how many treasury rows moved.
func (r *Runner) sectTribute(conn *storage.Conn, steps int64) (int64, error) {
	tribute := r.World.SectTribute()
	if len(tribute.Materials) == 0 {
		return 0, nil
	}
	if !simTableExists(conn, "sect_treasury") || !simTableExists(conn, "npc_civilization_state") {
		return 0, nil
	}
	perLot := tribute.DisciplesPerLot
	if perLot < 1 {
		perLot = 1
	}
	limit := tribute.Cap
	if limit < 1 {
		limit = sectTributeCapFallback
	}
	res, err := conn.Execute(`SELECT faction,COUNT(*) AS disciples FROM npc_civilization_state
        WHERE status='alive' AND faction<>'' AND faction<>'Independent' GROUP BY faction ORDER BY faction`, nil)
	if err != nil {
		return 0, err
	}
	// A sect's resources say how much of what its disciples gather it can
	// actually keep: a sect under pressure is eating its own stores.
	resources, err := r.sectResources(conn)
	if err != nil {
		return 0, err
	}
	now := nowFloat()
	stocked := int64(0)
	for _, row := range res.Rows {
		sectName := fmt.Sprint(row[0])
		disciples := i64(row[1])
		world := r.sectWorld(sectName)
		if world == "" {
			continue
		}
		items := sectTributeItems(r.World, tribute, world)
		if len(items) == 0 {
			continue
		}
		lots := disciples / perLot
		if lots < 1 {
			// One disciple in a sect that asks for two still brings
			// something in; they are just slower about it.
			lots = 1
		}
		lots *= max1(min64(steps, 4))
		if level, ok := resources[sectName]; ok && level < 25 {
			// Below a quarter the sect is spending faster than it gathers.
			continue
		}
		for _, itemID := range items {
			moved, err := stockSectTreasury(conn, sectName, itemID, lots, limit, now)
			if err != nil {
				return stocked, err
			}
			stocked += moved
		}
	}
	held, err := r.territoryTribute(conn, steps, limit, now)
	if err != nil {
		return stocked, err
	}
	return stocked + held, nil
}

// territoryResourceRefs is what a held place yields, by the `resource_type`
// its seeding read off its description. A "mixed" place yields the one of the
// three its name picks, so a sect holding several does not stock everything
// from each.
var territoryResourceRefs = map[string]string{"spirit_herbs": "@herb", "ore": "@ore", "beast_grounds": "@core"}

// territoryTribute is what held ground pays its sect (v1.29.0). A territory's
// `resource_type` was worked out at seeding so that "wars, resource control
// and caravans" had a canonical map, and nothing read it: holding a place,
// winning it in a war or claiming it at all stocked nothing. Each place a sect
// holds now sends one lot a week of what it yields, one more where the city is
// prospering (75 or above) and one less where the ground is restless (unrest
// 50 or above), through the same storehouse cap the disciples' tribute meets.
// The material is resolved in the world the place stands in.
func (r *Runner) territoryTribute(conn *storage.Conn, steps, limit int64, now float64) (int64, error) {
	if !simTableExists(conn, "territory_state") {
		return 0, nil
	}
	res, err := conn.Execute(`SELECT territory_key,controller_key,resource_type,unrest FROM territory_state
        WHERE controller_type='sect' AND controller_key<>'' ORDER BY controller_key,territory_key`, nil)
	if err != nil {
		return 0, err
	}
	stocked := int64(0)
	for _, row := range res.Rows {
		key, sect, kind := fmt.Sprint(row[0]), fmt.Sprint(row[1]), fmt.Sprint(row[2])
		ref, ok := territoryResourceRefs[kind]
		if !ok {
			refs := []string{"@herb", "@ore", "@core"}
			ref = refs[int(hash64(key)%uint64(len(refs)))]
		}
		loc, known := r.World.Locations[key]
		if !known || loc.World == "" {
			continue
		}
		itemID := r.World.EventSites.Material(loc.World, ref)
		if itemID == "" {
			continue
		}
		if _, _, ok := game.ItemDef(r.World, itemID); !ok {
			continue
		}
		lots := territoryLots(r.territoryProsperity(conn, key), i64(row[3]))
		if lots <= 0 {
			continue
		}
		moved, err := stockSectTreasury(conn, sect, itemID, lots*max1(min64(steps, 4)), limit, now)
		if err != nil {
			return stocked, err
		}
		stocked += moved
	}
	return stocked, nil
}

// territoryLots is how many lots a held place sends a week.
func territoryLots(prosperity, unrest int64) int64 {
	lots := int64(1)
	if prosperity >= 75 {
		lots++
	}
	if unrest >= 50 {
		lots--
	}
	return lots
}

// territoryProsperity is the city's prosperity off `civilization_regions` -
// the live number shops and stalls move - and 50 where there is none.
func (r *Runner) territoryProsperity(conn *storage.Conn, location string) int64 {
	if !simTableExists(conn, "civilization_regions") {
		return 50
	}
	res, err := conn.Execute(`SELECT prosperity FROM civilization_regions WHERE location=?`, []any{location})
	if err != nil || len(res.Rows) == 0 {
		return 50
	}
	return i64(res.Rows[0][0])
}

// sectResources is every sect's resource level, read once rather than per row.
func (r *Runner) sectResources(conn *storage.Conn) (map[string]int64, error) {
	out := map[string]int64{}
	if !simTableExists(conn, "sect_politics_state") {
		return out, nil
	}
	res, err := conn.Execute(`SELECT sect_name,resources FROM sect_politics_state`, nil)
	if err != nil {
		return out, err
	}
	for _, row := range res.Rows {
		out[fmt.Sprint(row[0])] = i64(row[1])
	}
	return out, nil
}

// stockSectTreasury adds one material to a sect's storehouse, up to the cap.
//
// The cap is applied to what tribute has put there rather than to the row as a
// whole, so a full storehouse refuses the disciples' next delivery and never
// touches what players contributed - a treasury the tick could shrink would be
// a way to lose what you handed in.
func stockSectTreasury(conn *storage.Conn, sectName, itemID string, lots, limit int64, now float64) (int64, error) {
	res, err := conn.Execute(`SELECT quantity FROM sect_treasury WHERE sect_name=? AND item_id=?`, []any{sectName, itemID})
	if err != nil {
		return 0, err
	}
	held := int64(0)
	if row := firstMap(res); row != nil {
		held = i64(row["quantity"])
	}
	if held >= limit {
		return 0, nil
	}
	add := lots
	if held+add > limit {
		add = limit - held
	}
	if add <= 0 {
		return 0, nil
	}
	if simTableExists(conn, "sects") {
		if _, err = conn.Execute(`INSERT INTO sects(sect_name,updated_at) VALUES(?,?) ON CONFLICT(sect_name) DO NOTHING`, []any{sectName, now}); err != nil {
			return 0, err
		}
	}
	if _, err = conn.Execute(`INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES(?,?,?)
        ON CONFLICT(sect_name,item_id) DO UPDATE SET quantity=sect_treasury.quantity+excluded.quantity`,
		[]any{sectName, itemID, add}); err != nil {
		return 0, err
	}
	return add, nil
}
