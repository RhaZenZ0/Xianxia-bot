package game

import (
	"errors"
	"fmt"
	"math"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What a home holds (v1.30.0). Asked as "an update on player (sect) abode -
// show what is there": `/abode status` and the sect residence's status printed
// each facility's level and nothing else, so a Cultivation Chamber at Lv.3 and
// an Alchemy Furnace at Lv.2 said nothing about what either was worth, what the
// next level would add, or what it would cost - every one of those numbers
// lived in a rule the card could not see.
//
// `property.overview` is the one read, and it answers from the same helpers the
// rules call, so the card and the rule cannot disagree: homeCultivationMult
// (the cultivation chamber, at a hand-sat session and in a retreat),
// abodeArrayMultiplier (the formation core's gathering array),
// abodeFacilityRollBonus (every workshop, the herb garden and the beast pen),
// propertyStorageSlotsPerLevel, PropertyWardShare (the defensive formation, read
// by the bounty tick) and stallSlotsAndFee (the merchant hall), and the two
// upgrade costs the upgrade actions charge. TestEveryHomeRuleAsksTheOverviewsHelpers
// holds each rule site to its helper.

// abodeFacilityRollPerLevel is what a level of a workshop, the herb garden or
// the beast pen adds to its roll: a craft in that trade, a forage, a beast's
// training.
const abodeFacilityRollPerLevel = int64(2)

// homeCultivationMult is what a home's cultivation chamber is worth as ground:
// 1.05 at the door, +0.05 a level, never past 1.45. It was written out three
// times - the hand-sat session and both kinds of retreat.
func homeCultivationMult(level int64) float64 {
	return math.Min(1.45, 1.05+0.05*float64(max64(0, level)))
}

// abodeFacilityRollBonus is a workshop's, a garden's or a pen's roll bonus.
func abodeFacilityRollBonus(level int64) int64 {
	return maxI64(0, level) * abodeFacilityRollPerLevel
}

// PropertyWardShare is the share of a bounty hunter's capture progress that
// still lands while the quarry stands inside their own property (v1.28.0): a
// tenth less a level of Defensive Formation, nothing at ten.
func PropertyWardShare(defenseLevel int64) float64 {
	return math.Max(0, 1.0-0.1*float64(max64(0, defenseLevel)))
}

// homesteadUpgradeCost is what raising a homestead facility from `current`
// costs, in the money of the world the owner stands in.
func homesteadUpgradeCost(catalog worlddata.Catalog, current int64) int64 {
	base := i64(catalog.AbodeSystem["upgrade_base_cost"])
	if base <= 0 {
		base = 100
	}
	return base * (current + 1) * (current + 1)
}

// homesteadMaxLevel is the highest level a homestead facility reaches.
func homesteadMaxLevel(catalog worlddata.Catalog) int64 {
	if maxlvl := i64(catalog.AbodeSystem["max_level"]); maxlvl > 0 {
		return maxlvl
	}
	return 9
}

// residenceUpgradeCost is what raising a sect residence facility to `next`
// costs in contribution points.
func residenceUpgradeCost(catalog worlddata.Catalog, next int64) int64 {
	return sectAbodeSystemInt(catalog, "upgrade_base_points", 40) * next * next
}

// facilityDoes is what one facility does at one level, keyed for the bot to
// put into words. A level of 0 does nothing at all: an unbuilt workshop adds
// no roll and an unbuilt storehouse no room. The chamber is the exception -
// any home is ground worth 1.05, built or not.
func facilityDoes(catalog worlddata.Catalog, key string, level int64) map[string]any {
	out := map[string]any{}
	switch key {
	case "cultivation":
		out["cultivation_mult"] = round4(homeCultivationMult(level))
	case "formation":
		if level > 0 {
			out["array_mult"] = abodeArrayMultiplier(map[string]any{"formation_level": level})
			out["craft_bonus"] = abodeFacilityRollBonus(level)
			out["trades"] = []string{"Formation", "Inscription"}
		}
	case "alchemy":
		if level > 0 {
			out["craft_bonus"] = abodeFacilityRollBonus(level)
			out["trades"] = []string{"Alchemy"}
		}
	case "forge":
		if level > 0 {
			out["craft_bonus"] = abodeFacilityRollBonus(level)
			out["trades"] = []string{"Forging"}
		}
	case "herb_garden":
		if level > 0 {
			out["forage_bonus"] = abodeFacilityRollBonus(level)
		}
	case "beast_pen":
		if level > 0 {
			out["beast_training_bonus"] = abodeFacilityRollBonus(level)
		}
	case "storage":
		if level > 0 {
			out["storage_slots"] = level * propertyStorageSlotsPerLevel
		}
	case "defense":
		if level > 0 {
			out["capture_slowed_percent"] = int64(math.Round((1 - PropertyWardShare(level)) * 100))
		}
	case "merchant":
		slots, fee := stallSlotsAndFee(catalog, level)
		out["stall_slots"] = slots
		out["stall_fee_percent"] = fee
	}
	if effectID := abodeFacilityEffects[key]; effectID != "" && level > 0 {
		if _, name, err := specialEffectPayload(catalog, effectID); err == nil && name != "" {
			out["focus_effect"] = name
		}
	}
	return out
}

func propertyOverviewQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	if userID <= 0 {
		return nil, errors.New("a cultivator is required")
	}
	out := map[string]any{"homestead": nil, "residence": nil}

	home, err := abodeByOwnerGo(conn, userID)
	if err != nil {
		return nil, err
	}
	if home != nil {
		currency, curErr := characterBaseCurrencyTx(conn, catalog, userID)
		if curErr != nil {
			return nil, curErr
		}
		maxlvl := homesteadMaxLevel(catalog)
		facilities := []map[string]any{}
		for _, key := range homesteadFacilities(catalog) {
			col := abodeFacilityCols[key]
			if col == "" {
				continue
			}
			level := i64(home[col])
			row := map[string]any{"key": key, "level": level, "max_level": maxlvl, "does": facilityDoes(catalog, key, level)}
			if level < maxlvl {
				row["next"] = facilityDoes(catalog, key, level+1)
				row["next_cost"] = homesteadUpgradeCost(catalog, level)
				row["next_currency"] = currency
			}
			facilities = append(facilities, row)
		}
		guests := int64(0)
		if r, gErr := conn.Execute(`SELECT COUNT(*) FROM cave_abode_access WHERE owner_user_id=?`, []any{userID}); gErr == nil && len(r.Rows) > 0 {
			guests = storage.ParseInt(r.Rows[0][0])
		}
		out["homestead"] = map[string]any{
			"name": fmt.Sprint(home["name"]), "base_location": fmt.Sprint(home["base_location"]),
			"grade": fmt.Sprint(home["grade"]), "guests": guests, "facilities": facilities,
		}
	}

	if tableExistsTx(conn, "sect_abodes") {
		r, rErr := conn.Execute(`SELECT * FROM sect_abodes WHERE user_id=?`, []any{userID})
		if rErr != nil {
			return nil, rErr
		}
		if residence := firstRowMap(r); residence != nil {
			rankLevel, rankName, points := int64(0), "", int64(0)
			if m, mErr := conn.Execute(`SELECT rank_level,rank_name,contribution_points FROM sect_membership WHERE user_id=?`, []any{userID}); mErr == nil {
				if mem := firstRowMap(m); mem != nil {
					rankLevel, rankName, points = i64(mem["rank_level"]), fmt.Sprint(mem["rank_name"]), i64(mem["contribution_points"])
				}
			}
			realm := int64(0)
			if c, cErr := conn.Execute(`SELECT realm_index FROM characters WHERE user_id=?`, []any{userID}); cErr == nil {
				if ch := firstRowMap(c); ch != nil {
					realm = i64(ch["realm_index"])
				}
			}
			maxlvl := sectAbodeSystemInt(catalog, "max_level", 9)
			rankCap := sectAbodeLevelCap(catalog, rankLevel)
			facilities := []map[string]any{}
			for _, key := range sectAbodeFacilities(catalog) {
				col := sectAbodeFacilityCols[key]
				if col == "" {
					continue
				}
				level := i64(residence[col])
				row := map[string]any{"key": key, "level": level, "max_level": maxlvl, "does": facilityDoes(catalog, key, level)}
				if next := level + 1; next <= maxlvl {
					row["next"] = facilityDoes(catalog, key, next)
					row["next_cost"] = residenceUpgradeCost(catalog, next)
					row["next_currency"] = "contribution"
					// The two gates the upgrade asks, said before it is asked:
					// the rank cap and the cultivation floor.
					if next > rankCap {
						if name, need := sectAbodeRankForLevel(catalog, next); name != "" {
							row["next_needs"] = fmt.Sprintf("%s (rank %d)", name, need)
						} else {
							row["next_needs"] = "a rank no sect grants"
						}
					} else if floor := (next - 1) * sectAbodeSystemInt(catalog, "realm_floor_per_level", 1); realm < floor {
						row["next_needs"] = realmNameGo(catalog, floor)
					}
				}
				facilities = append(facilities, row)
			}
			out["residence"] = map[string]any{
				"name": fmt.Sprint(residence["name"]), "sect_name": fmt.Sprint(residence["sect_name"]),
				"base_location": fmt.Sprint(residence["base_location"]), "rank_name": rankName,
				"contribution_points": points, "rank_cap": rankCap, "facilities": facilities,
			}
		}
	}
	out["storage_slots_from_homes"] = propertyStorageSlotsTx(conn, userID)
	return out, nil
}

// homesteadFacilities is the homestead's facility roster, in the content's
// order, falling back to the column map's keys when the content names none.
func homesteadFacilities(catalog worlddata.Catalog) []string {
	out := []string{}
	if raw, ok := catalog.AbodeSystem["facilities"].([]any); ok {
		for _, v := range raw {
			if key := fmt.Sprint(v); abodeFacilityCols[key] != "" {
				out = append(out, key)
			}
		}
	}
	if len(out) == 0 {
		out = append(out, "cultivation", "alchemy", "forge", "formation", "defense", "storage", "herb_garden", "beast_pen", "merchant")
	}
	return out
}
