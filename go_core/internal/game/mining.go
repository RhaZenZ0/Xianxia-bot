package game

// Mining (v1.1.0): the ore half of gathering.
//
// Foraging has been the game's one gathering action since v1.0.0-rc.21, and it
// brings back herbs, talisman paper, spirit ink and array blanks - never ore.
// So spirit iron, the thing every Forging entry method wants three of, came
// from a shop counter or off an Iron-Horn Boar, and Forging was the one trade a
// cultivator could learn at the household's table and then not practise
// without money. The owner's brief for the first hour names Mine beside Gather,
// Hunt and Forge, and this is it: `exploration.mine`, the seam's twin of the
// hills' forage, standing beside Explore and Hunt on `/world -> Act` because it
// is a thing you do to a place rather than a craft.
//
// It is deliberately forage's shape and not a copy of forage's code. The roll,
// the region's richness, the world-tier common material through
// `EventSites.Material` and the tier-flat roster (`mine_materials`, content,
// same struct as `forage_materials`) all follow the forage, so a reader who
// knows one knows the other; what differs is stated once each - the cooldown
// is the engine's table (forage still carries its own literal, which is a
// separate fix), the attributes are body and insight rather than insight and
// spirit, the household tradition is the Forging houses', and a rich seam pays
// a few spirit stones through the one reward door so they land in the money of
// the world you are standing in (rc.44).

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// miningProfession is the profession a mine advances, beside Foraging. It is a
// bare literal for the same reason forage's is: the Python roster
// (`PROFESSIONS`) and the content gate resolve it by this spelling.
const miningProfession = "Mining"

// mineRareOreChance is the base percentage that a seam gives up the next
// world's ore. A Mortal miner who turns up Spirit-Crystal Ore has something to
// forge above their station or to sell; it is rare enough that the shop stays
// the honest source of it.
const mineRareOreChance = 8

// mineVeinStonesMargin is the margin at which a seam is rich enough to pay
// spirit stones beside the ore, and mineVeinStonesBase what it pays.
const (
	mineVeinStonesMargin = 6
	mineVeinStonesBase   = 4
)

// mineWorldTier orders the worlds so a seam can name the ore one world up. It
// is read off the catalogue's own tier ranks (v1.2.3) rather than a literal
// list; the content file is the one statement of which worlds there are.
func mineWorldTier(catalog worlddata.Catalog) []string {
	worlds := make([]string, 0, len(catalog.EventSites.TierRank))
	for world := range catalog.EventSites.TierRank {
		worlds = append(worlds, world)
	}
	sort.Slice(worlds, func(i, j int) bool {
		if catalog.EventSites.TierRank[worlds[i]] != catalog.EventSites.TierRank[worlds[j]] {
			return catalog.EventSites.TierRank[worlds[i]] < catalog.EventSites.TierRank[worlds[j]]
		}
		return worlds[i] < worlds[j]
	})
	return worlds
}

type minePayload struct {
	GameMinute int64 `json:"game_minute"`
}

func explorationMineAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p minePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	cr, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if cr.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living character can mine")
	}
	// The same doors the hunt refuses: a seam is worked in the world, not in a
	// household, an abode or a world of your own making.
	if strings.HasPrefix(cr.Location, "abode:") || strings.HasPrefix(cr.Location, "sect_abode:") || strings.HasPrefix(cr.Location, "personal_world:") || strings.HasPrefix(cr.Location, "birth_family:") {
		return authoritativeMutation{}, errors.New("mining is unavailable inside a private residence or personal world")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if active, err := activeExplorationEventForUserTx(conn, userID, now); err != nil {
		return authoritativeMutation{}, err
	} else if active != nil {
		return authoritativeMutation{}, fmt.Errorf("resolve or leave active exploration event %s before mining", active.EventID)
	}
	remaining, err := cooldownRemaining(conn, userID, cooldownMine, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		// Hunt's shape, so the bot's cooldown regex words it in hours and
		// minutes rather than printing raw seconds.
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	siteKind := catalog.Locations[cr.Location].RoadSite
	if siteKind == "shrine" {
		return authoritativeMutation{}, errors.New("nobody breaks the ground of a shrine for ore")
	}

	// The region's richness and its world, read the way the forage reads them.
	resources := int64(50)
	worldName := "Mortal World"
	region, regionErr := conn.Execute(`SELECT world_name,spirit_resources FROM civilization_regions WHERE location=?`, []any{cr.Location})
	if regionErr != nil {
		return authoritativeMutation{}, regionErr
	}
	if row := firstRowMap(region); row != nil {
		resources = clamp(i64(row["spirit_resources"]), 0, 100)
		if value := strings.TrimSpace(fmt.Sprint(row["world_name"])); value != "" {
			worldName = value
		}
	}
	if loc, ok := catalog.Locations[cr.Location]; ok && strings.TrimSpace(loc.World) != "" && worldName == "Mortal World" {
		worldName = loc.World
	}
	resourceBonus := int64(0)
	switch {
	case resources >= 85:
		resourceBonus = 4
	case resources >= 70:
		resourceBonus = 3
	case resources >= 55:
		resourceBonus = 2
	case resources >= 35:
		resourceBonus = 1
	}
	worldTier := int64(0)
	worlds := mineWorldTier(catalog)
	for index, name := range worlds {
		if name == worldName {
			worldTier = int64(index)
		}
	}

	// The ore of the world you stand in, through the one resolver the event
	// sites, the send-off and the forage already use; a ref the catalogue does
	// not carry falls back to Mortal iron rather than to a row for nothing.
	commonOre := catalog.EventSites.Material(worldName, "@ore")
	if _, ok := catalog.Items[commonOre]; !ok || commonOre == "" {
		commonOre = "spirit_iron"
	}
	commonQty := maxI64(1, 1+resources/35+maxI64(0, cr.RealmIndex)/8)
	lootPlan := map[string]int64{commonOre: minI64(5, commonQty)}

	// One world up, rarely. Only where there is a world up: a Celestial seam
	// has nothing above it.
	rareFound := ""
	if worldTier+1 < int64(len(worlds)) {
		rareOre := catalog.EventSites.Material(worlds[worldTier+1], "@ore")
		if _, ok := catalog.Items[rareOre]; ok && rareOre != "" && rareOre != commonOre {
			chance := minI64(40, mineRareOreChance+maxI64(0, resources-50)/4+maxI64(0, cr.RealmIndex)/3)
			rareRoll, rollErr := gamerng.Intn(100)
			if rollErr != nil {
				return authoritativeMutation{}, rollErr
			}
			if int64(rareRoll) < chance {
				lootPlan[rareOre]++
				rareFound = rareOre
			}
		}
	}

	level := int64(0)
	if pr, prErr := conn.Execute(`SELECT level FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, miningProfession}); prErr == nil {
		if row := firstRowMap(pr); row != nil {
			level = i64(row["level"])
		}
	}

	// The tier-flat makings a seam gives up (`mine_materials`), walked in
	// sorted order for the reason the forage roster is: a map range is
	// randomised and would spend the dice differently on every call.
	materialsFound := map[string]int64{}
	materialIDs := make([]string, 0, len(catalog.MineMaterials))
	for id := range catalog.MineMaterials {
		materialIDs = append(materialIDs, id)
	}
	sort.Strings(materialIDs)
	for _, id := range materialIDs {
		spec := catalog.MineMaterials[id]
		if _, ok := catalog.Items[id]; !ok {
			continue
		}
		if spec.Chance <= 0 || spec.Max <= 0 || resources < spec.MinResources {
			continue
		}
		chance := minI64(65, spec.Chance+maxI64(0, resources-50)/3+level)
		materialRoll, rollErr := gamerng.Intn(100)
		if rollErr != nil {
			return authoritativeMutation{}, rollErr
		}
		if int64(materialRoll) >= chance {
			continue
		}
		found := int64(1)
		if spec.Max > 1 {
			extra, extraErr := gamerng.Intn(int(spec.Max))
			if extraErr != nil {
				return authoritativeMutation{}, extraErr
			}
			found = int64(extra) + 1
		}
		lootPlan[id] += found
		materialsFound[id] = found
	}

	// The Forging houses' tradition: the seam is Forging's gathering half as
	// the hills are Alchemy's, and the bonus is keyed on the trade the send-off
	// names rather than on any house's archetype (rc.31).
	familyBonus, familyTrade, familyErr := householdTradeBonusTx(conn, catalog, userID, "Forging")
	if familyErr != nil {
		return authoritativeMutation{}, familyErr
	}
	body, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "body")
	if err != nil {
		return authoritativeMutation{}, err
	}
	insight, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "insight")
	if err != nil {
		return authoritativeMutation{}, err
	}
	tn := maxI64(8, 12+worldTier*2-resourceBonus)
	mod := body + insight + level + familyBonus + resourceBonus
	roll, err := roll2d10(mod, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err := setCooldown(conn, userID, cooldownMine, cooldownSecondsFor(cooldownMine), now); err != nil {
		return authoritativeMutation{}, err
	}
	margin := i64(roll["margin"])
	success, _ := roll["success"].(bool)
	loot := map[string]int64{}
	stones := int64(0)
	if success {
		for k, v := range lootPlan {
			loot[k] = v
		}
		// A rich seam pays beside the ore. Through the one reward door, so the
		// stones are the money of the world the seam is in and the sheet's
		// mirror follows (rc.43, rc.44).
		if margin >= mineVeinStonesMargin {
			stones = mineVeinStonesBase + margin/2
		}
		if _, _, err := applyCanonicalRewardTx(conn, catalog, userID, cr, canonicalReward{SpiritStones: stones, Items: loot}, "mine_success", now); err != nil {
			return authoritativeMutation{}, err
		}
	} else {
		// Nothing is carried out of a failed dig, so nothing is reported found.
		materialsFound = map[string]int64{}
		rareFound = ""
	}
	xp := int64(4)
	if success {
		xp = 10
		if margin > 0 {
			xp += margin / 2
		}
	}
	prog, err := advanceProfessionTx(conn, userID, miningProfession, success, xp, maxI64(0, margin), now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"location":            cr.Location,
		"world":               worldName,
		"spirit_resources":    resources,
		"site_kind":           siteKind,
		"tn":                  tn,
		"roll":                roll,
		"success":             success,
		"loot":                loot,
		"rare_found":          rareFound,
		"materials_found":     materialsFound,
		"stones":              stones,
		"family_bonus":        familyBonus,
		"family_trade":        familyTrade,
		"profession_progress": prog,
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "exploration", EventType: "mine_resolved", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
