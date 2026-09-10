package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The residence a public sect assigns (v0.30.1). It begins as a cultivation
// chamber and a storeroom and grows the way a homestead does, except that
// the price is sect contribution points and the ceiling is the disciple's
// standing: each rank caps the level a facility may reach, and each level
// asks a cultivation stage. The content (sect_abode_system) holds the
// facilities, the cost curve and both gates; this file applies them.

var sectAbodeFacilityCols = map[string]string{
	"cultivation": "cultivation_level", "alchemy": "alchemy_level", "forge": "forge_level",
	"formation": "formation_level", "storage": "storage_level", "herb_garden": "herb_garden_level",
}

type sectAbodeUpgradePayload struct {
	Facility   string `json:"facility"`
	GameMinute int64  `json:"game_minute"`
}

func sectAbodeSystemInt(c worlddata.Catalog, key string, fallback int64) int64 {
	if v, ok := c.SectAbodeSystem[key]; ok {
		if n := i64(v); n > 0 {
			return n
		}
	}
	return fallback
}

// sectAbodeFacilities is the content's facility list, restricted to the
// columns this table has.
func sectAbodeFacilities(c worlddata.Catalog) []string {
	raw, _ := c.SectAbodeSystem["facilities"].([]any)
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		key := strings.TrimSpace(fmt.Sprint(v))
		if _, ok := sectAbodeFacilityCols[key]; ok {
			out = append(out, key)
		}
	}
	if len(out) == 0 {
		for key := range sectAbodeFacilityCols {
			out = append(out, key)
		}
		sort.Strings(out)
	}
	return out
}

type sectAbodeRankCap struct {
	RankLevel int64
	MaxLevel  int64
}

func sectAbodeRankCaps(c worlddata.Catalog) []sectAbodeRankCap {
	raw, _ := c.SectAbodeSystem["rank_caps"].([]any)
	caps := make([]sectAbodeRankCap, 0, len(raw))
	for _, v := range raw {
		row, _ := v.(map[string]any)
		if row == nil {
			continue
		}
		caps = append(caps, sectAbodeRankCap{RankLevel: i64(row["rank_level"]), MaxLevel: i64(row["max_level"])})
	}
	sort.Slice(caps, func(a, b int) bool { return caps[a].RankLevel < caps[b].RankLevel })
	return caps
}

// sectAbodeLevelCap is the highest facility level a member of this rank may
// hold; without caps in the content every rank reaches the maximum.
func sectAbodeLevelCap(c worlddata.Catalog, rankLevel int64) int64 {
	caps := sectAbodeRankCaps(c)
	if len(caps) == 0 {
		return sectAbodeSystemInt(c, "max_level", 9)
	}
	cap := int64(0)
	for _, row := range caps {
		if rankLevel >= row.RankLevel {
			cap = row.MaxLevel
		}
	}
	return cap
}

// sectAbodeRankForLevel names the lowest rank whose cap reaches the level.
func sectAbodeRankForLevel(c worlddata.Catalog, level int64) (string, int64) {
	for _, row := range sectAbodeRankCaps(c) {
		if row.MaxLevel >= level {
			return sectRankName(c, row.RankLevel), row.RankLevel
		}
	}
	return "", 0
}

// sectRankName is the content's name for a rank level (sect_system.ranks).
func sectRankName(c worlddata.Catalog, level int64) string {
	ranks, _ := c.SectSystem["ranks"].([]any)
	for _, v := range ranks {
		row, _ := v.(map[string]any)
		if row != nil && i64(row["level"]) == level {
			return fmt.Sprint(row["name"])
		}
	}
	return fmt.Sprintf("rank %d", level)
}

func sectAbodeUpgradeAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectAbodeUpgradePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.Facility = strings.ToLower(strings.TrimSpace(p.Facility))
	column := sectAbodeFacilityCols[p.Facility]
	allowed := sectAbodeFacilities(catalog)
	if column == "" || !containsString(allowed, p.Facility) {
		return authoritativeMutation{}, fmt.Errorf("a sect residence develops one of: %s", strings.Join(allowed, ", "))
	}
	res, err := conn.Execute(`SELECT sect_name,rank_name,rank_level,contribution_points FROM sect_membership WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	membership := firstRowMap(res)
	if membership == nil {
		return authoritativeMutation{}, errors.New("only a public sect member holds a sect residence")
	}
	res, err = conn.Execute(`SELECT * FROM sect_abodes WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	abode := firstRowMap(res)
	if abode == nil || fmt.Sprint(abode["sect_name"]) != fmt.Sprint(membership["sect_name"]) {
		return authoritativeMutation{}, errors.New("open your sect residence first (/sect abode) so the sect assigns it")
	}
	res, err = conn.Execute(`SELECT realm_index FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	character := firstRowMap(res)
	if character == nil {
		return authoritativeMutation{}, errors.New("living character not found")
	}
	current := i64(abode[column])
	next := current + 1
	if maxLevel := sectAbodeSystemInt(catalog, "max_level", 9); next > maxLevel {
		return authoritativeMutation{}, errors.New("that facility is already at its highest level")
	}
	rankLevel := i64(membership["rank_level"])
	if cap := sectAbodeLevelCap(catalog, rankLevel); next > cap {
		name, level := sectAbodeRankForLevel(catalog, next)
		if name == "" {
			return authoritativeMutation{}, fmt.Errorf("no rank may develop a residence facility to level %d", next)
		}
		return authoritativeMutation{}, fmt.Errorf("a %s may develop the residence to level %d; level %d asks %s (rank %d) or higher",
			fmt.Sprint(membership["rank_name"]), cap, next, name, level)
	}
	floor := (next - 1) * sectAbodeSystemInt(catalog, "realm_floor_per_level", 1)
	if realm := i64(character["realm_index"]); realm < floor {
		return authoritativeMutation{}, fmt.Errorf("level %d asks a cultivation stage of %s (realm %d); yours is %s",
			next, realmNameGo(catalog, floor), floor, realmNameGo(catalog, realm))
	}
	base := sectAbodeSystemInt(catalog, "upgrade_base_points", 40)
	cost := base * next * next
	points := i64(membership["contribution_points"])
	if points < cost {
		return authoritativeMutation{}, fmt.Errorf("not enough sect contribution points: %d needed, %d held", cost, points)
	}
	now := nowSeconds()
	if _, err = conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points-? WHERE user_id=?`, []any{cost, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(fmt.Sprintf(`UPDATE sect_abodes SET %s=?,updated_at=? WHERE user_id=?`, column), []any{next, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	res, _ = conn.Execute(`SELECT * FROM sect_abodes WHERE user_id=?`, []any{userID})
	abode = firstRowMap(res)
	out := map[string]any{
		"facility": p.Facility, "level": next, "built": current == 0, "cost": cost,
		"remaining_points": points - cost, "rank_cap": sectAbodeLevelCap(catalog, rankLevel), "abode": abode,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect_abode.upgrade", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// sectResidenceFacilityLevel answers for the sect abode what the cave_abodes
// lookups answer for a homestead: the level of one facility at the private
// location the character stands in, for the member it belongs to. A sect
// residence has no guest list, so only its holder benefits.
func sectResidenceFacilityLevel(conn *storage.Conn, userID int64, locationKey, column string) (level int64, baseLocation string, found bool, err error) {
	if !strings.HasPrefix(locationKey, "sect_abode:") {
		return 0, "", false, nil
	}
	res, err := conn.Execute(fmt.Sprintf(`SELECT base_location,%s AS facility_level FROM sect_abodes WHERE location_key=? AND user_id=?`, column), []any{locationKey, userID})
	if err != nil {
		return 0, "", false, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, "", false, nil
	}
	return maxI64(0, i64(row["facility_level"])), strings.TrimSpace(fmt.Sprint(row["base_location"])), true, nil
}

// homesteadFoundingRankGo is the standing a public sect asks before a member
// may found a homestead of their own (abode_system.founding_rank_level); zero
// means no gate.
func homesteadFoundingRankGo(c worlddata.Catalog) int64 {
	return maxI64(0, i64(c.AbodeSystem["founding_rank_level"]))
}
