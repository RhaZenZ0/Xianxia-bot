package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type craftResolvePayload struct {
	Recipe string `json:"recipe"`
}

const maxSectManorFacilityLevel int64 = 5

func craftEffectStat(profession string) string {
	switch strings.ToLower(strings.TrimSpace(profession)) {
	case "alchemy":
		return "alchemy_bonus"
	case "forging":
		return "forging_bonus"
	case "formation":
		return "formation_bonus"
	default:
		return ""
	}
}

func craftAbodeFacilityColumn(profession string) string {
	switch strings.ToLower(strings.TrimSpace(profession)) {
	case "alchemy":
		return "alchemy_level"
	case "forging":
		return "forge_level"
	case "formation":
		return "formation_level"
	default:
		return ""
	}
}

func craftManorFacilityColumn(profession string) string {
	switch strings.ToLower(strings.TrimSpace(profession)) {
	case "alchemy":
		return "alchemy_hall_level"
	case "forging":
		return "forge_pavilion_level"
	case "formation":
		return "defense_array_level"
	default:
		return ""
	}
}

func canonicalCraftAbodeBonus(conn *storage.Conn, userID int64, location, profession string) (int64, error) {
	column := craftAbodeFacilityColumn(profession)
	location = strings.TrimSpace(location)
	if column == "" || location == "" {
		return 0, nil
	}

	res, err := conn.Execute(
		fmt.Sprintf(`SELECT user_id,%s AS facility_level FROM cave_abodes WHERE location_key=?`, column),
		[]any{location},
	)
	if err != nil {
		return 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, nil
	}
	ownerID := i64(row["user_id"])
	canAccess := ownerID == userID
	if !canAccess {
		access, accessErr := conn.Execute(
			`SELECT 1 FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?`,
			[]any{ownerID, userID},
		)
		if accessErr != nil {
			return 0, accessErr
		}
		canAccess = firstRowMap(access) != nil
	}
	if !canAccess {
		return 0, nil
	}
	return maxI64(0, i64(row["facility_level"])) * 2, nil
}

func canonicalCraftManorBonus(conn *storage.Conn, userID int64, location, profession string) (int64, error) {
	column := craftManorFacilityColumn(profession)
	location = strings.TrimSpace(location)
	if column == "" || location == "" {
		return 0, nil
	}

	res, err := conn.Execute(
		fmt.Sprintf(
			`SELECT m.base_location,m.%s AS facility_level
			   FROM sect_membership sm
			   JOIN sect_manors m ON m.sect_name=sm.sect_name
			  WHERE sm.user_id=?`,
			column,
		),
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	row := firstRowMap(res)
	if row == nil || strings.TrimSpace(fmt.Sprint(row["base_location"])) != location {
		return 0, nil
	}
	level := clamp(i64(row["facility_level"]), 0, maxSectManorFacilityLevel)
	return level * 2, nil
}

func canonicalCraftFamilyBonus(conn *storage.Conn, userID int64, profession string) (int64, error) {
	if !strings.EqualFold(strings.TrimSpace(profession), "Alchemy") {
		return 0, nil
	}
	res, err := conn.Execute(
		`SELECT f.archetype
		   FROM character_birth_family c
		   JOIN birth_families f ON f.family_id=c.family_id
		  WHERE c.user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	if row := firstRowMap(res); row != nil && strings.TrimSpace(fmt.Sprint(row["archetype"])) == "alchemy_family" {
		return 2, nil
	}
	return 0, nil
}

func professionXPNeeded(level int64) int64 {
	if level < 0 {
		level = 0
	}
	return 60 + level*40
}

func advanceProfessionTx(conn *storage.Conn, userID int64, profession string, success bool, xpGain, qualityPoints int64, now float64) (map[string]any, error) {
	succ, fail := int64(0), int64(0)
	if success {
		succ = 1
	} else {
		fail = 1
	}
	if xpGain < 0 {
		xpGain = 0
	}
	if qualityPoints < 0 {
		qualityPoints = 0
	}
	_, err := conn.Execute(`INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
VALUES(?,?,0,?,?,?,?,?) ON CONFLICT(user_id,profession) DO UPDATE SET xp=profession_progress.xp+excluded.xp,successes=profession_progress.successes+excluded.successes,failures=profession_progress.failures+excluded.failures,quality_points=profession_progress.quality_points+excluded.quality_points,updated_at=excluded.updated_at`, []any{userID, profession, xpGain, succ, fail, qualityPoints, now})
	if err != nil {
		return nil, err
	}
	res, err := conn.Execute(`SELECT level,xp,successes,failures,quality_points FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, profession})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("profession progress missing after update")
	}
	level, xp := i64(row["level"]), i64(row["xp"])
	for level < 6 && xp >= professionXPNeeded(level) {
		xp -= professionXPNeeded(level)
		level++
	}
	_, err = conn.Execute(`UPDATE profession_progress SET level=?,xp=?,updated_at=? WHERE user_id=? AND profession=?`, []any{level, xp, now, userID, profession})
	if err != nil {
		return nil, err
	}
	return map[string]any{"profession": profession, "level": level, "xp": xp, "successes": i64(row["successes"]), "failures": i64(row["failures"]), "quality_points": i64(row["quality_points"])}, nil
}

func craftQuality(margin int64, success bool) (string, string, int64, int64, int64) {
	if !success {
		return "failed", "Failed", 0, 0, 1
	}
	if margin >= 9 {
		return "masterwork", "Masterwork", 8, 9, 1
	}
	if margin >= 6 {
		return "superior", "Superior", 5, 6, 1
	}
	if margin >= 3 {
		return "fine", "Fine", 3, 3, 1
	}
	if margin < 0 {
		margin = 0
	}
	return "ordinary", "Ordinary", 0, margin, 1
}
func alchemyQualityGo(margin int64, success bool) (string, string, int64, int64) {
	if !success {
		return "crude", "Crude", 1, 0
	}
	if margin >= 9 {
		return "flawless", "Flawless", 3, 10
	}
	if margin >= 5 {
		return "superior", "Superior", 2, 6
	}
	if margin >= 2 {
		return "refined", "Refined", 1, 3
	}
	return "ordinary", "Ordinary", 1, 0
}

func consumeInventoryTx(conn *storage.Conn, userID int64, costs map[string]int64) (map[string]int64, error) {
	missing := map[string]int64{}
	for item, qty := range costs {
		if qty <= 0 {
			continue
		}
		r, e := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, item})
		if e != nil {
			return nil, e
		}
		have := int64(0)
		if row := firstRowMap(r); row != nil {
			have = i64(row["quantity"])
		}
		if have < qty {
			missing[item] = qty - have
		}
	}
	if len(missing) > 0 {
		return missing, nil
	}
	for item, qty := range costs {
		if qty <= 0 {
			continue
		}
		if _, e := conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{qty, userID, item}); e != nil {
			return nil, e
		}
	}
	return missing, nil
}
func addInventoryTx(conn *storage.Conn, userID int64, items map[string]int64) error {
	for item, qty := range items {
		if qty <= 0 {
			continue
		}
		if _, e := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`, []any{userID, item, qty}); e != nil {
			return e
		}
	}
	return nil
}

func craftResolveAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var supplied map[string]json.RawMessage
	if err := json.Unmarshal(raw, &supplied); err != nil {
		return authoritativeMutation{}, err
	}
	if supplied == nil {
		return authoritativeMutation{}, errors.New("payload must be a JSON object")
	}
	for field := range supplied {
		if field != "recipe" {
			return authoritativeMutation{}, fmt.Errorf("client-supplied %s is forbidden", field)
		}
	}

	var p craftResolvePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Recipe = strings.TrimSpace(p.Recipe)
	if p.Recipe == "" {
		return authoritativeMutation{}, errors.New("recipe is required")
	}
	recipe, ok := catalog.Recipes[p.Recipe]
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("unknown recipe: %s", p.Recipe)
	}

	cr, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if cr.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living character can craft")
	}

	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	location := strings.TrimSpace(cr.Location)
	if location == "" {
		return authoritativeMutation{}, errors.New("character location is required")
	}

	attrs := cr.Attributes
	profession := strings.TrimSpace(recipe.Profession)
	base := attrs["insight"] + attrs["will"]
	switch strings.ToLower(profession) {
	case "alchemy", "formation":
		base = attrs["insight"] + attrs["spirit"]
	case "forging":
		base = attrs["insight"] + attrs["body"]
	}

	pr, err := conn.Execute(`SELECT level FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, profession})
	if err != nil {
		return authoritativeMutation{}, err
	}
	level := int64(0)
	if row := firstRowMap(pr); row != nil {
		level = i64(row["level"])
	}

	if strings.EqualFold(profession, "Alchemy") {
		if _, err := settlePillToxicityEffectTx(conn, userID, gameMinute); err != nil {
			return authoritativeMutation{}, err
		}
	}
	effectBonus := int64(0)
	if effectStat := craftEffectStat(profession); effectStat != "" {
		effectBonus, err = canonicalAdditiveEffectBonus(conn, catalog, userID, location, gameMinute, effectStat)
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	facilityBonus, err := canonicalCraftAbodeBonus(conn, userID, location, profession)
	if err != nil {
		return authoritativeMutation{}, err
	}
	manorFacilityBonus, err := canonicalCraftManorBonus(conn, userID, location, profession)
	if err != nil {
		return authoritativeMutation{}, err
	}
	familyBonus, err := canonicalCraftFamilyBonus(conn, userID, profession)
	if err != nil {
		return authoritativeMutation{}, err
	}
	contextBonus := effectBonus + facilityBonus + manorFacilityBonus + familyBonus
	mod := base + level + contextBonus

	roll, err := roll2d10(mod, recipe.TN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	d1, d2 := i64(roll["die1"]), i64(roll["die2"])
	total, margin := i64(roll["total"]), i64(roll["margin"])
	success, _ := roll["success"].(bool)

	missing, err := consumeInventoryTx(conn, userID, recipe.Cost)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(missing) > 0 {
		return authoritativeMutation{}, errors.New("missing materials")
	}

	output := map[string]int64{}
	for k, v := range recipe.Output {
		output[k] = v
	}
	qkey, qlabel, xpbonus, qpoints, mult := "", "", int64(0), int64(0), int64(1)
	if strings.EqualFold(profession, "Alchemy") {
		qkey, qlabel, mult, xpbonus = alchemyQualityGo(margin, success)
		if success && mult > 1 {
			for k, v := range output {
				output[k] = v * mult
			}
		}
		if margin > 0 {
			qpoints = margin
		}
	} else {
		qkey, qlabel, xpbonus, qpoints, mult = craftQuality(margin, success)
	}
	if success {
		if err := addInventoryTx(conn, userID, output); err != nil {
			return authoritativeMutation{}, err
		}
	}

	xp := int64(5)
	if success {
		xp = 12 + xpbonus
	}
	now := float64(time.Now().UnixNano()) / 1e9
	prog, err := advanceProfessionTx(conn, userID, profession, success, xp, qpoints, now)
	if err != nil {
		return authoritativeMutation{}, err
	}

	if strings.EqualFold(profession, "Alchemy") {
		outJSON, _ := json.Marshal(func() map[string]int64 {
			if success {
				return output
			}
			return map[string]int64{}
		}())
		_, err = conn.Execute(
			`INSERT INTO alchemy_batches(user_id,recipe_name,quality,margin,success,output_json,location,game_minute,created_at)
			 VALUES(?,?,?,?,?,?,?,?,?)`,
			[]any{
				userID,
				p.Recipe,
				qkey,
				margin,
				func() int64 {
					if success {
						return 1
					}
					return 0
				}(),
				string(outJSON),
				location,
				gameMinute,
				now,
			},
		)
		if err != nil {
			return authoritativeMutation{}, err
		}
		_, err = conn.Execute(
			`INSERT INTO alchemy_state(
				user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
				successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
			) VALUES(?,0,?,1,?,?,?, ?,?)
			ON CONFLICT(user_id) DO UPDATE SET
				total_refinements=alchemy_state.total_refinements+1,
				successful_refinements=alchemy_state.successful_refinements+excluded.successful_refinements,
				flawless_refinements=alchemy_state.flawless_refinements+excluded.flawless_refinements,
				best_margin=MAX(alchemy_state.best_margin,excluded.best_margin),
				last_quality=excluded.last_quality,
				updated_at=excluded.updated_at`,
			[]any{
				userID,
				gameMinute,
				func() int64 {
					if success {
						return 1
					}
					return 0
				}(),
				func() int64 {
					if success && qkey == "flawless" {
						return 1
					}
					return 0
				}(),
				margin,
				qkey,
				now,
			},
		)
		if err != nil {
			return authoritativeMutation{}, err
		}
	}

	result := map[string]any{
		"recipe":        p.Recipe,
		"profession":    profession,
		"d1":            d1,
		"d2":            d2,
		"modifier":      mod,
		"tn":            recipe.TN,
		"total":         total,
		"margin":        margin,
		"success":       success,
		"quality":       qkey,
		"quality_label": qlabel,
		"output": func() map[string]int64 {
			if success {
				return output
			}
			return map[string]int64{}
		}(),
		"profession_progress":  prog,
		"profession_bonus":     level,
		"output_multiplier":    mult,
		"location":             location,
		"game_minute":          gameMinute,
		"effect_bonus":         effectBonus,
		"facility_bonus":       facilityBonus,
		"manor_facility_bonus": manorFacilityBonus,
		"family_bonus":         familyBonus,
		"context_bonus":        contextBonus,
	}
	evp, _ := json.Marshal(result)
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:     "crafting",
			EventType:  "craft.resolved",
			GameMinute: gameMinute,
			Payload:    evp,
		},
	}, nil
}

func forageResolveAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var supplied map[string]json.RawMessage
	if err := json.Unmarshal(raw, &supplied); err != nil {
		return authoritativeMutation{}, err
	}
	for _, forbidden := range []string{
		"tn",
		"spirit_resources",
		"loot",
		"rare_found",
		"context_bonus",
		"effect_bonus",
		"location",
		"realm_index",
		"garden_level",
		"cooldown_seconds",
	} {
		if _, ok := supplied[forbidden]; ok {
			return authoritativeMutation{}, fmt.Errorf("client-supplied %s is forbidden", forbidden)
		}
	}
	cr, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if cr.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living character can forage")
	}

	now := float64(time.Now().UnixNano()) / 1e9
	remaining, err := cooldownRemaining(conn, userID, "alchemy_forage", now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("forage cooldown active: %d seconds remaining", remaining)
	}

	physicalLocation := strings.TrimSpace(cr.Location)
	forageLocation := physicalLocation
	gardenLevel := int64(0)
	if physicalLocation != "" {
		abode, abodeErr := conn.Execute(
			`SELECT user_id,base_location,herb_garden_level FROM cave_abodes WHERE location_key=?`,
			[]any{physicalLocation},
		)
		if abodeErr != nil {
			return authoritativeMutation{}, abodeErr
		}
		if row := firstRowMap(abode); row != nil {
			ownerID := i64(row["user_id"])
			canAccess := ownerID == userID
			if !canAccess {
				access, accessErr := conn.Execute(
					`SELECT 1 FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?`,
					[]any{ownerID, userID},
				)
				if accessErr != nil {
					return authoritativeMutation{}, accessErr
				}
				canAccess = firstRowMap(access) != nil
			}
			if canAccess {
				if base := strings.TrimSpace(fmt.Sprint(row["base_location"])); base != "" {
					forageLocation = base
				}
				gardenLevel = maxI64(0, i64(row["herb_garden_level"]))
			}
		}
	}
	if forageLocation == "" {
		return authoritativeMutation{}, errors.New("character location is required")
	}

	familyBonus := int64(0)
	family, familyErr := conn.Execute(
		`SELECT f.archetype
		   FROM character_birth_family c
		   JOIN birth_families f ON f.family_id=c.family_id
		  WHERE c.user_id=?`,
		[]any{userID},
	)
	if familyErr != nil {
		return authoritativeMutation{}, familyErr
	}
	if row := firstRowMap(family); row != nil && strings.TrimSpace(fmt.Sprint(row["archetype"])) == "alchemy_family" {
		familyBonus = 2
	}
	gardenBonus := gardenLevel * 2
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := settlePillToxicityEffectTx(conn, userID, gameMinute); err != nil {
		return authoritativeMutation{}, err
	}
	effectBonus, err := canonicalAdditiveEffectBonus(conn, catalog, userID, physicalLocation, gameMinute, "alchemy_bonus")
	if err != nil {
		return authoritativeMutation{}, err
	}
	contextBonus := effectBonus + familyBonus + gardenBonus

	resources := int64(50)
	worldName := "Mortal World"
	if region, regionErr := conn.Execute(
		`SELECT world_name,spirit_resources FROM civilization_regions WHERE location=?`,
		[]any{forageLocation},
	); regionErr == nil {
		if row := firstRowMap(region); row != nil {
			resources = clamp(i64(row["spirit_resources"]), 0, 100)
			if value := strings.TrimSpace(fmt.Sprint(row["world_name"])); value != "" {
				worldName = value
			}
		}
	} else {
		return authoritativeMutation{}, regionErr
	}
	if loc, ok := catalog.Locations[forageLocation]; ok && strings.TrimSpace(loc.World) != "" && worldName == "Mortal World" {
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
	switch worldName {
	case "Spiritual World":
		worldTier = 1
	case "Immortal World":
		worldTier = 2
	case "Celestial World":
		worldTier = 3
	}
	tn := maxI64(8, 12+worldTier*2-resourceBonus)
	commonQty := maxI64(1, 1+resources/35+maxI64(0, cr.RealmIndex)/8)
	lootPlan := map[string]int64{"spirit_herb": minI64(5, commonQty)}
	if gardenLevel > 0 {
		lootPlan["spirit_herb"] += maxI64(1, gardenLevel/2)
	}

	type rareCandidate struct {
		Item string
		Base int64
	}
	rarePool := []rareCandidate{}
	if _, ok := catalog.Items["fire_spirit_root_herb"]; ok {
		rarePool = append(rarePool, rareCandidate{"fire_spirit_root_herb", 35})
	}
	if _, ok := catalog.Items["ice_spirit_blazing_grass"]; ok && worldTier >= 1 {
		rarePool = append(rarePool, rareCandidate{"ice_spirit_blazing_grass", 24})
	}
	if _, ok := catalog.Items["twin_extremes_fruit"]; ok && worldTier >= 1 {
		rarePool = append(rarePool, rareCandidate{"twin_extremes_fruit", 15})
	}
	if _, ok := catalog.Items["jade_life_herb"]; ok && worldTier >= 2 {
		rarePool = append(rarePool, rareCandidate{"jade_life_herb", 6})
	}
	rareFound := ""
	if len(rarePool) > 0 {
		pick, pickErr := gamerng.Intn(len(rarePool))
		if pickErr != nil {
			return authoritativeMutation{}, pickErr
		}
		candidate := rarePool[pick]
		chanceBonus := maxI64(0, resources-50)/3 + maxI64(0, cr.RealmIndex)/2
		chance := minI64(65, candidate.Base+chanceBonus)
		rareRoll, rollErr := gamerng.Intn(100)
		if rollErr != nil {
			return authoritativeMutation{}, rollErr
		}
		if int64(rareRoll) < chance {
			lootPlan[candidate.Item]++
			rareFound = candidate.Item
		}
	}

	pr, _ := conn.Execute(`SELECT level FROM profession_progress WHERE user_id=? AND profession='Foraging'`, []any{userID})
	level := int64(0)
	if row := firstRowMap(pr); row != nil {
		level = i64(row["level"])
	}
	mod := cr.Attributes["insight"] + cr.Attributes["spirit"] + level + contextBonus + resourceBonus
	roll, err := roll2d10(mod, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	d1, d2 := i64(roll["die1"]), i64(roll["die2"])
	total, margin := i64(roll["total"]), i64(roll["margin"])
	success, _ := roll["success"].(bool)
	loot := map[string]int64{}
	if success {
		for k, v := range lootPlan {
			loot[k] = v
		}
		if err := addInventoryTx(conn, userID, loot); err != nil {
			return authoritativeMutation{}, err
		}
	}

	xp := int64(4)
	if success {
		xp = 10
		if margin > 0 {
			xp += margin / 2
		}
	}
	prog, err := advanceProfessionTx(conn, userID, "Foraging", success, xp, func() int64 {
		if margin > 0 {
			return margin
		}
		return 0
	}(), now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	const cooldownSeconds int64 = 20 * 60
	if err := setCooldown(conn, userID, "alchemy_forage", cooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}

	result := map[string]any{
		"d1":                  d1,
		"d2":                  d2,
		"modifier":            mod,
		"tn":                  tn,
		"total":               total,
		"margin":              margin,
		"success":             success,
		"loot":                loot,
		"rare_found":          rareFound,
		"spirit_resources":    resources,
		"resource_bonus":      resourceBonus,
		"world_name":          worldName,
		"physical_location":   physicalLocation,
		"location":            forageLocation,
		"realm_index":         cr.RealmIndex,
		"effect_bonus":        effectBonus,
		"game_minute":         gameMinute,
		"family_bonus":        familyBonus,
		"garden_level":        gardenLevel,
		"garden_bonus":        gardenBonus,
		"context_bonus":       contextBonus,
		"cooldown_seconds":    cooldownSeconds,
		"profession_progress": prog,
	}
	evp, _ := json.Marshal(result)
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:     "crafting",
			EventType:  "forage.resolved",
			GameMinute: gameMinute,
			Payload:    evp,
		},
	}, nil
}
