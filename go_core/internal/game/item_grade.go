package game

import (
	"math"
	"sort"
	"strings"

	"xianxia/core/internal/worlddata"
)

// Item grades (v1.7.0).
//
// A crafted item carries one of the grades in `item_grade_system`, stored as a
// suffix on its id: "qi_pill@high". The bare id is the first grade, so every
// row written before grades existed is simply Low and nothing is migrated. The
// suffix rides through every table that stores an item id as text - the bag,
// storage, a stall, the auction floor, a trade offer - without a schema change.
//
// Which items are graded is not a second list: it is every recipe's output. A
// material, a manual or a key has no recipe making it, so it has no grade and
// "spirit_herb@high" is refused as unknown.
//
// itemDef is the one door from an item id to its definition. A bare
// `catalog.Items[id]` would answer "unknown" for every graded item, so
// TestTheCatalogueIsReadByOneDoor holds production Go to this function.

// itemGradeSeparator joins a base id to its grade. It is part of how a row is
// stored, not a tuning knob: changing it would orphan every graded row already
// written, so it is a constant here and in app/rules/item_grades.py.
const itemGradeSeparator = "@"

// keeperGradeCeiling is the first rung no NPC keeper sells or buys: Low and
// Mid are shelf goods, High and above are priced by players.
const keeperGradeCeiling = 2

// splitItemGrade parses "base@grade" into its halves. An id with no separator
// is its own base at grade "".
func splitItemGrade(itemID string) (string, string) {
	if i := strings.LastIndex(itemID, itemGradeSeparator); i > 0 {
		return itemID[:i], itemID[i+len(itemGradeSeparator):]
	}
	return itemID, ""
}

// itemGradeRung answers the ladder's rung for a grade key; "" is the first rung.
func itemGradeRung(catalog worlddata.Catalog, key string) (worlddata.ItemGrade, int, bool) {
	grades := catalog.ItemGrades.Grades
	if key == "" {
		if len(grades) == 0 {
			return worlddata.ItemGrade{Key: "", Label: "", EffectMult: 1, PriceMult: 1}, 0, true
		}
		return grades[0], 0, true
	}
	for i, g := range grades {
		if g.Key == key {
			return g, i, true
		}
	}
	return worlddata.ItemGrade{}, 0, false
}

// isGradedItem reports whether a base id is some recipe's output.
func isGradedItem(catalog worlddata.Catalog, base string) bool {
	for _, key := range sortedRecipeKeys(catalog) {
		if _, ok := catalog.Recipes[key].Output[base]; ok {
			return true
		}
	}
	return false
}

func sortedRecipeKeys(catalog worlddata.Catalog) []string {
	keys := make([]string, 0, len(catalog.Recipes))
	for k := range catalog.Recipes {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// itemDef is the one door from an item id to its definition: the base item,
// the rung it stands on, and whether the id names anything at all. A graded id
// is known only when its base is a recipe's output and its grade is a rung.
// The first rung is written bare, so "qi_pill@low" is refused rather than
// becoming a second name for the same thing.
func itemDef(catalog worlddata.Catalog, itemID string) (worlddata.Item, worlddata.ItemGrade, bool) {
	base, key := splitItemGrade(itemID)
	item, ok := catalog.Items[base]
	if !ok {
		return worlddata.Item{}, worlddata.ItemGrade{}, false
	}
	rung, index, known := itemGradeRung(catalog, key)
	if !known {
		return worlddata.Item{}, worlddata.ItemGrade{}, false
	}
	if key != "" && (index == 0 || !isGradedItem(catalog, base)) {
		return worlddata.Item{}, worlddata.ItemGrade{}, false
	}
	if rung.EffectMult <= 0 {
		rung.EffectMult = 1
	}
	if rung.PriceMult <= 0 {
		rung.PriceMult = 1
	}
	// The definition at its grade: what it is worth is priced here, once, so
	// every reader of BasePrice and SectValue - a sect's contribution, an
	// appraiser's fee, a merchant's valuation - is paid the grade's worth
	// without knowing grades exist. What it does is scaled where it is used.
	item.BasePrice *= rung.PriceMult
	item.SectValue *= rung.PriceMult
	return item, rung, true
}

// gradedID writes a base id at a rung; the first rung is the bare id.
func gradedID(catalog worlddata.Catalog, base string, index int) string {
	grades := catalog.ItemGrades.Grades
	if index <= 0 || index >= len(grades) {
		return base
	}
	return base + itemGradeSeparator + grades[index].Key
}

// itemBaseID strips any grade: what a recipe cost, a quest target or an
// equipment table is keyed on.
func itemBaseID(itemID string) string {
	base, _ := splitItemGrade(itemID)
	return base
}

// itemGradeIndex is the rung an id stands on (0 for a bare or unknown id).
func itemGradeIndex(catalog worlddata.Catalog, itemID string) int {
	_, key := splitItemGrade(itemID)
	_, index, ok := itemGradeRung(catalog, key)
	if !ok {
		return 0
	}
	return index
}

// itemEffectMult is what a grade multiplies an item's use by: restores,
// lifespan, modifier values and duration, and a bound item's stats.
func itemEffectMult(catalog worlddata.Catalog, itemID string) float64 {
	_, rung, ok := itemDef(catalog, itemID)
	if !ok || rung.EffectMult <= 0 {
		return 1
	}
	return rung.EffectMult
}

// itemBasePrice and itemSectValue are the item's worth at its grade.
func itemBasePrice(catalog worlddata.Catalog, itemID string) int64 {
	item, _, ok := itemDef(catalog, itemID)
	if !ok {
		return 0
	}
	return item.BasePrice
}

func itemSectValue(catalog worlddata.Catalog, itemID string) int64 {
	item, _, ok := itemDef(catalog, itemID)
	if !ok {
		return 0
	}
	return item.SectValue
}

// craftGradeIndex is the rung a craft reaches: the highest rung whose quality
// or margin the roll met, then capped by the crafter's rank in the trade. The
// second value is the rung before the cap, so a reply can say what a higher
// rank would have made.
func craftGradeIndex(catalog worlddata.Catalog, quality string, margin, rank int64) (int, int) {
	reached := 0
	for i, g := range catalog.ItemGrades.Grades {
		hit := false
		for _, q := range g.Qualities {
			if q == quality {
				hit = true
			}
		}
		if g.MinMargin != nil && margin >= *g.MinMargin {
			hit = true
		}
		if hit && i > reached {
			reached = i
		}
	}
	capped := reached
	for capped > 0 && catalog.ItemGrades.Grades[capped].MinRank > rank {
		capped--
	}
	return capped, reached
}

// itemDisplayNameOrEmpty is an item's name at its grade ("Qi Pill (High)"), or
// "" for an id the catalogue does not carry. The first rung shows no label.
func itemDisplayNameOrEmpty(catalog worlddata.Catalog, itemID string) string {
	item, rung, ok := itemDef(catalog, itemID)
	if !ok || item.Name == "" {
		return ""
	}
	if itemGradeIndex(catalog, itemID) == 0 || rung.Label == "" {
		return item.Name
	}
	return item.Name + " (" + rung.Label + ")"
}

// ItemDef, ItemBasePrice and ItemSectValue are the one door for the simulation
// package, which must not keep a copy of how a graded id resolves.
func ItemDef(catalog worlddata.Catalog, itemID string) (worlddata.Item, worlddata.ItemGrade, bool) {
	return itemDef(catalog, itemID)
}

func ItemBasePrice(catalog worlddata.Catalog, itemID string) int64 {
	return itemBasePrice(catalog, itemID)
}

func ItemSectValue(catalog worlddata.Catalog, itemID string) int64 {
	return itemSectValue(catalog, itemID)
}

// craftGradeLabel and craftGradeRank describe a rung for a craft's reply: its
// label, and the trade rank that may make it.
func craftGradeLabel(catalog worlddata.Catalog, index int) string {
	grades := catalog.ItemGrades.Grades
	if index < 0 || index >= len(grades) {
		return ""
	}
	return grades[index].Label
}

func craftGradeRank(catalog worlddata.Catalog, index int) int64 {
	grades := catalog.ItemGrades.Grades
	if index < 0 || index >= len(grades) {
		return 0
	}
	return grades[index].MinRank
}

// gradedAmount scales a whole-number quantity of an item's use by its grade,
// rounded, and never below the base: a grade only ever adds.
func gradedAmount(base int64, mult float64) int64 {
	if base <= 0 {
		return maxI64(0, base)
	}
	return maxI64(base, int64(math.Round(float64(base)*mult)))
}

// gradedEffectPayload scales an effect's modifiers by a grade. An additive
// value is multiplied; a multiplier's distance from 1 is (so a x1.2 at grade
// x1.5 is x1.3); a set or a flag is a fact, not a strength, and is left alone.
func gradedEffectPayload(payload map[string]any, mult float64) map[string]any {
	if mult == 1 {
		return payload
	}
	mods, _ := payload["modifiers"].([]map[string]any)
	for _, m := range mods {
		v := toFloat(m["value"])
		switch m["operation"] {
		case "add":
			m["value"] = v * mult
		case "mul":
			m["value"] = 1 + (v-1)*mult
		}
	}
	return payload
}

// equipmentQualityMult is what a bound item's stored quality multiplies its
// stats by. It was two formulas - 1+(q-100)/200 in solo combat and the gear
// card, q/100 in group combat - that agreed only at 100, the one value anything
// had ever written. A grade writes others (v1.7.0), so there is one now.
func equipmentQualityMult(quality int64) float64 {
	return math.Max(0.5, 1+(float64(quality)-100)/200)
}

// gradeEquipmentQuality is the stored quality a grade binds at: the inverse of
// equipmentQualityMult, so a High sword (x1.5) hits for one and a half times
// a Low one.
func gradeEquipmentQuality(mult float64) int64 {
	return int64(math.Round(100 + 200*(mult-1)))
}
