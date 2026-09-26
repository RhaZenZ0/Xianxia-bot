package game

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
	case "formation", "inscription":
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
	case "formation", "inscription":
		// A talisman bench and an array table are the same room here - the
		// sect manor's own description says the grand defensive array doubles
		// as an inscription workshop (app/rules/sect_manor.py:64). Splitting
		// the talismans out of Formation without this would have quietly
		// stripped every one of them of its workshop and manor bonus.
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
	case "formation", "inscription":
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
		// The sect residence (v0.30.1) has the same workshops, for its holder.
		level, _, found, residenceErr := sectResidenceFacilityLevel(conn, userID, location, column)
		if residenceErr != nil {
			return 0, residenceErr
		}
		if found {
			return level * 2, nil
		}
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

// describeMaterials turns a shortfall map into the sentence a refusal shows.
// `consumeInventoryTx` has always computed exactly which materials are short
// and by how much, and until v1.0.1 every caller threw that away and refused
// with the bare words "missing materials" - so a player who had learned a
// recipe was told they could not make it and never which of its two inputs
// they lacked. Nothing else in the game names a recipe's cost either, so that
// refusal was the only place the information could have reached them.
//
// Names come from `itemDisplayName` rather than the raw ids, because the player
// has to go and buy the thing - and from that helper rather than a second
// lookup here, since "what an item is called" is already stated once. The ids
// are sorted, because a map range would make the same refusal read differently
// between two runs.
func describeMaterials(catalog worlddata.Catalog, shortfall map[string]int64) string {
	ids := make([]string, 0, len(shortfall))
	for id := range shortfall {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	parts := make([]string, 0, len(ids))
	for _, id := range ids {
		parts = append(parts, fmt.Sprintf("%s x%d", itemDisplayName(catalog, id), shortfall[id]))
	}
	return strings.Join(parts, ", ")
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

// craftFailureRefund is the share of a failed craft's inputs that comes back:
// half of each, rounded down, so one unit of anything is the stake. It is a
// function so the refund and the reply cannot state the share differently.
func craftFailureRefund(cost map[string]int64) map[string]int64 {
	out := map[string]int64{}
	for item, qty := range cost {
		if back := qty / 2; back > 0 {
			out[item] = back
		}
	}
	return out
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
	case "alchemy", "formation", "inscription":
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

	// The two halves of the learning step (v1.0.0-rc.20).
	//
	// Until now every recipe in the game was craftable by anyone from character
	// creation, materials permitting - `/craft`'s own description said "from a
	// known recipe" and nothing tracked knowledge. A method is now two things: a
	// thing you have been taught, and work you are good enough to do. They are
	// checked separately because the refusals mean different things, and a
	// player who is told the wrong one goes looking in the wrong place.
	//
	// Knowledge is asked for on every recipe, including the common ones: a
	// cultivator is taught those by their own household on the way out the door
	// (`teachHouseholdMethodsTx`), so "common" means freely taught rather than
	// silently assumed - and the one place it is granted is the one place to
	// look when somebody cannot make a thing they should be able to.
	known, err := knowsRecipeTx(conn, userID, p.Recipe)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !known {
		return authoritativeMutation{}, fmt.Errorf("you do not know the method for %s; it is carried on a jade slip", p.Recipe)
	}
	if level < recipe.MinLevel {
		return authoritativeMutation{}, fmt.Errorf("%s asks for %s %d and you are %d", p.Recipe, profession, recipe.MinLevel, level)
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
	// The household's tradition (v1.0.0-rc.31): +2 in the trade it teaches,
	// whichever trade that is - until now only one archetype string earned
	// it, and only on Alchemy.
	familyBonus, familyTrade, err := householdTradeBonusTx(conn, catalog, userID, profession)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// And what a past life's hands remember (v1.0.0-rc.32), as far as the
	// soul's memory has woken.
	craftEcho, craftEchoLife, craftEchoLevel, err := craftEchoTx(conn, userID, profession)
	if err != nil {
		return authoritativeMutation{}, err
	}
	contextBonus := effectBonus + facilityBonus + manorFacilityBonus + familyBonus + craftEcho
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
		return authoritativeMutation{}, fmt.Errorf("missing materials: %s",
			describeMaterials(catalog, missing))
	}

	qkey, qlabel, xpbonus, qpoints := "", "", int64(0), int64(0)
	if strings.EqualFold(profession, "Alchemy") {
		qkey, qlabel, _, xpbonus = alchemyQualityGo(margin, success)
		if margin > 0 {
			qpoints = margin
		}
	} else {
		qkey, qlabel, xpbonus, qpoints, _ = craftQuality(margin, success)
	}
	// The grade (v1.7.0). Quality used to multiply an alchemy batch and do
	// nothing for the other three trades; it is spent on the grade of what is
	// made now, capped by the crafter's rank in this trade.
	gradeIndex, gradeReached := craftGradeIndex(catalog, qkey, margin, level)
	output := map[string]int64{}
	for k, v := range recipe.Output {
		output[gradedID(catalog, k, gradeIndex)] = v
	}
	// What a miss leaves you (v1.3.0). The inputs were consumed above whether
	// or not the roll landed, and until now a failure kept all of them: the
	// tutorial's forge cost three spirit iron and another dig on one miss in
	// seven. Half of each input comes back, rounded down, so a single unit of
	// anything is still spent - a craft that could be retried for free would
	// be a roll with no stake.
	returned := map[string]int64{}
	if success {
		if err := addInventoryTx(conn, userID, output); err != nil {
			return authoritativeMutation{}, err
		}
	} else {
		returned = craftFailureRefund(recipe.Cost)
		if err := addInventoryTx(conn, userID, returned); err != nil {
			return authoritativeMutation{}, err
		}
	}

	xp := int64(5)
	if success {
		xp = 12 + xpbonus
	}
	now := float64(time.Now().UnixNano()) / 1e9
	// The rank before the craft, so a rank reached by this one can be told
	// from a rank the candidate walked in holding (v1.0.0-rc.45).
	rankBefore, err := professionLevelTx(conn, userID, profession)
	if err != nil {
		return authoritativeMutation{}, err
	}
	prog, err := advanceProfessionTx(conn, userID, profession, success, xp, qpoints, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// `advanceProfessionTx` is deliberately untouched and still raises a rank
	// on XP alone - the examination is what the rank is worth, never a toll on
	// reaching it. The offer is made here rather than in the advance because
	// this is the only caller whose trade has examinations at all, and the
	// only one holding the catalogue and the canonical minute.
	//
	// A craft generous enough to cross two ranks offers the higher one: the
	// examination certifies what the candidate now is, and the action itself
	// only ever sits the rank they currently hold.
	examOffered := ""
	if rankAfter := i64(prog["level"]); rankAfter > rankBefore {
		if examOffered, err = offerProfessionExamTx(conn, catalog, userID, profession, rankAfter, gameMinute); err != nil {
			return authoritativeMutation{}, err
		}
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
		"recipe":     p.Recipe,
		"profession": profession,
		// The whole check, degree and odds included: what the reply prints
		// (v1.0.3). The flat d1/d2 beside it are not what `roll_line` reads,
		// and craft was the last caller still shipping only those - the same
		// omission v1.0.1 fixed for forage and did not carry across the file.
		"roll":          roll,
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
		"returned":             returned,
		"profession_progress":  prog,
		"exam_offered":         examOffered,
		"profession_bonus":     level,
		"grade":                craftGradeLabel(catalog, gradeIndex),
		"grade_reached":        craftGradeLabel(catalog, gradeReached),
		"grade_reached_rank":   craftGradeRank(catalog, gradeReached),
		"location":             location,
		"game_minute":          gameMinute,
		"effect_bonus":         effectBonus,
		"facility_bonus":       facilityBonus,
		"manor_facility_bonus": manorFacilityBonus,
		"family_bonus":         familyBonus,
		"family_trade":         familyTrade,
		"craft_echo":           craftEcho,
		"craft_echo_life":      craftEchoLife,
		"craft_echo_level":     craftEchoLevel,
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
	remaining, err := cooldownRemaining(conn, userID, cooldownForage, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		// The hunt's shape (v1.3.1), so the bot's cooldown regex words it in
		// hours and minutes rather than printing raw seconds.
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
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
		} else if level, base, found, residenceErr := sectResidenceFacilityLevel(conn, userID, physicalLocation, "herb_garden_level"); residenceErr != nil {
			return authoritativeMutation{}, residenceErr
		} else if found {
			// The sect residence's garden (v0.30.1): foraged at the sect's seat.
			if base != "" {
				forageLocation = base
			}
			gardenLevel = level
		}
	}
	if forageLocation == "" {
		return authoritativeMutation{}, errors.New("character location is required")
	}

	// The hills are Alchemy's gathering half, so the tradition bonus here is
	// the Alchemy houses' - both of them, now that it is keyed on the trade
	// rather than on one archetype's name.
	familyBonus, familyTrade, familyErr := householdForageBonusTx(conn, catalog, userID)
	if familyErr != nil {
		return authoritativeMutation{}, familyErr
	}
	craftEcho, craftEchoLife, craftEchoLevel, echoErr := craftEchoTx(conn, userID, "Foraging")
	if echoErr != nil {
		return authoritativeMutation{}, echoErr
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
	contextBonus := effectBonus + familyBonus + gardenBonus + craftEcho

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
	// The world's own herb (v1.0.0-rc.19). This was the literal "spirit_herb"
	// in all four worlds, so a Celestial forager gathered Mortal weeds - the
	// one profession whose whole output ignored the tier it was practised in.
	// Resolved through the same `EventSites.Material` the world-event sites,
	// the birth-family send-off and the sect tribute use, with the same guard:
	// a ref the item catalogue does not carry falls back to the Mortal herb
	// rather than producing a row for an item that does not exist.
	commonHerb := catalog.EventSites.Material(worldName, "@herb")
	if _, _, ok := itemDef(catalog, commonHerb); !ok || commonHerb == "" {
		commonHerb = "spirit_herb"
	}
	lootPlan := map[string]int64{commonHerb: minI64(5, commonQty)}
	if gardenLevel > 0 {
		lootPlan[commonHerb] += maxI64(1, gardenLevel/2)
	}

	type rareCandidate struct {
		Item string
		Base int64
	}
	rarePool := []rareCandidate{}
	if _, _, ok := itemDef(catalog, "fire_spirit_root_herb"); ok {
		rarePool = append(rarePool, rareCandidate{"fire_spirit_root_herb", 35})
	}
	if _, _, ok := itemDef(catalog, "ice_spirit_blazing_grass"); ok && worldTier >= 1 {
		rarePool = append(rarePool, rareCandidate{"ice_spirit_blazing_grass", 24})
	}
	if _, _, ok := itemDef(catalog, "twin_extremes_fruit"); ok && worldTier >= 1 {
		rarePool = append(rarePool, rareCandidate{"twin_extremes_fruit", 15})
	}
	if _, _, ok := itemDef(catalog, "jade_life_herb"); ok && worldTier >= 2 {
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

	// The makings of the other three crafts (v1.0.0-rc.21).
	//
	// `talisman_paper`, `spirit_ink` and `array_disk_blank` were named by
	// items, recipes, shops and merchants and by no gathering path at all - no
	// event-site node, no secret-realm treasure, no forage table - so Alchemy
	// and Forging could be gathered into and Inscription and Formation could
	// only be bought into. A forager brings them back now, which is also what
	// stops Foraging being a herb feeder for one profession out of four.
	//
	// The roster is content (`forage_materials`), and the iteration is over
	// sorted keys rather than the map itself: a Go map range is randomised, so
	// an unsorted loop would spend the RNG in a different order every call and
	// no seeded test could pin it.
	materialsFound := map[string]int64{}
	materialIDs := make([]string, 0, len(catalog.ForageMaterials))
	for id := range catalog.ForageMaterials {
		materialIDs = append(materialIDs, id)
	}
	sort.Strings(materialIDs)
	for _, id := range materialIDs {
		spec := catalog.ForageMaterials[id]
		// The same guard the tiered herb uses: content naming an item the
		// catalogue does not carry must not write an inventory row for a
		// thing that does not exist.
		if _, _, ok := itemDef(catalog, id); !ok {
			continue
		}
		if spec.Chance <= 0 || spec.Max <= 0 || resources < spec.MinResources {
			continue
		}
		// Richness and a practised eye both help, and the cap is the one the
		// rare pool already uses so no single find becomes reliable.
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
	} else {
		// Nothing is carried home from a failed forage, so nothing is
		// reported as found. The plan is built before the roll because the
		// roll needs no part of it, not because a miss still finds things.
		materialsFound = map[string]int64{}
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
	cooldownSeconds := cooldownSecondsFor(cooldownForage)
	if err := setCooldown(conn, userID, cooldownForage, cooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}

	result := map[string]any{
		"roll":                roll, // the whole check, degree and odds included: what the reply prints
		"d1":                  d1,
		"d2":                  d2,
		"modifier":            mod,
		"tn":                  tn,
		"total":               total,
		"margin":              margin,
		"success":             success,
		"loot":                loot,
		"rare_found":          rareFound,
		"materials_found":     materialsFound,
		"spirit_resources":    resources,
		"resource_bonus":      resourceBonus,
		"world_name":          worldName,
		"physical_location":   physicalLocation,
		"location":            forageLocation,
		"realm_index":         cr.RealmIndex,
		"effect_bonus":        effectBonus,
		"game_minute":         gameMinute,
		"family_bonus":        familyBonus,
		"family_trade":        familyTrade,
		"craft_echo":          craftEcho,
		"craft_echo_life":     craftEchoLife,
		"craft_echo_level":    craftEchoLevel,
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

// --- the learning step ----------------------------------------------------
//
// You must hold the slip, and the first reading records the method and spends
// the slip: a jade slip carries one impression of a method and is blank after
// it is taken. So a method reaches a second cultivator only by a second slip,
// which is what keeps a shop's stock worth buying and a rare method worth
// guarding. Reading one whose method you already carry costs nothing and
// spends nothing: it reports that you know it and leaves the slip in the bags,
// because a slip burnt for nothing would be a trap rather than a rule.

// knowsRecipeTx is whether this cultivator has been taught a method.
func knowsRecipeTx(conn *storage.Conn, userID int64, recipe string) (bool, error) {
	res, err := conn.Execute(`SELECT 1 AS known FROM character_recipes WHERE user_id=? AND recipe=?`, []any{userID, recipe})
	if err != nil {
		return false, err
	}
	return firstRowMap(res) != nil, nil
}

type recipeLearnPayload struct {
	ItemID     string `json:"item_id"`
	GameMinute int64  `json:"game_minute"`
}

// recipeLearnAction reads a method slip and records the method.
func recipeLearnAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p recipeLearnPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	itemID := strings.TrimSpace(p.ItemID)
	item, _, ok := itemDef(catalog, itemID)
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("unknown item: %s", itemID)
	}
	recipeName := item.Learns()
	if recipeName == "" {
		return authoritativeMutation{}, errors.New("that is not a method slip")
	}
	recipe, ok := catalog.Recipes[recipeName]
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("the slip carries a method this world does not have: %s", recipeName)
	}
	held, err := inventoryQuantityTx(conn, userID, itemID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if held <= 0 {
		return authoritativeMutation{}, errors.New("you are not carrying that slip")
	}
	known, err := knowsRecipeTx(conn, userID, recipeName)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !known {
		if _, err = conn.Execute(
			`INSERT INTO character_recipes(user_id,recipe,learned_game_minute,source,created_at)
			 VALUES(?,?,?,?,?) ON CONFLICT(user_id,recipe) DO NOTHING`,
			[]any{userID, recipeName, gameMinute, "method_slip:" + itemID, nowSeconds()}); err != nil {
			return authoritativeMutation{}, err
		}
		// One impression, one reading. The knowledge is written first and the
		// slip spent second, both inside the one action transaction, so a
		// failure between them cannot leave a cultivator charged and untaught.
		missing, spendErr := consumeInventoryTx(conn, userID, map[string]int64{itemID: 1})
		if spendErr != nil {
			return authoritativeMutation{}, spendErr
		}
		if len(missing) > 0 {
			return authoritativeMutation{}, fmt.Errorf("you are not carrying that slip")
		}
	}
	// The level is reported rather than required: a method can be studied
	// before the hands are ready for it, which is the ordinary way of things.
	level, err := professionLevelTx(conn, userID, recipe.Profession)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"item_id": itemID, "recipe": recipeName, "profession": recipe.Profession,
		"min_level": recipe.MinLevel, "level": level, "already_known": known,
		"ready": level >= recipe.MinLevel, "game_minute": gameMinute,
		"slip_consumed": !known,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{
		Domain: "crafting", EventType: "recipe.learn", EntityType: "character",
		EntityID: fmt.Sprint(userID), GameMinute: gameMinute, Payload: out}}, nil
}
