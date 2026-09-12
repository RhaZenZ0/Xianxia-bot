package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The ghost road (v1.0.0-rc.8).
//
// Death qi is what a place keeps after something has died in it. A cultivator
// born to one of the two ghost households gathers that instead of spirit qi,
// in the same three dantian: richer where the living are gone and at night,
// thin in a temple quarter and under the sun. It leaves a residue - corruption
// - which lowers how clean the middle dantian can keep what it holds, tears
// channels once it is deep, and step by step remakes the body into something
// the living cross the road to avoid.
//
// Everything the road reads is content (`death_qi_system` in world.json): the
// ground, the hours, the corruption costs and the ghost-form ladder.

const (
	spiritQiType  = "spirit"
	deathQiType   = "death"
	corruptionCap = 100
	// A harvest is worth this share of the dantian before the ground is
	// priced in, which is why a ghost cultivator does not simply wait for
	// the pool to refill.
	ghostHarvestShare = 0.35
	// The ground a harvest needs: anything short of this is too full of the
	// living to hold a residue worth taking.
	ghostHarvestGroundFloor = 1.15
	// The ground incense needs: a place the living keep for their dead.
	ghostAppeaseGroundCeiling = 0.75
)

func deathQiPathName(catalog worlddata.Catalog) string {
	return firstNonempty(strings.TrimSpace(catalog.DeathQi.Path), "Ghost Cultivator")
}

// ghostBornFamily is whether this birth-family archetype raises children who
// can walk the road at all.
func ghostBornFamily(catalog worlddata.Catalog, archetype string) bool {
	archetype = strings.TrimSpace(archetype)
	if archetype == "" {
		return false
	}
	for _, id := range catalog.DeathQi.Families {
		if strings.TrimSpace(id) == archetype {
			return true
		}
	}
	return false
}

// isGhostPath is whether this cultivation path is the birth-gated one.
func isGhostPath(catalog worlddata.Catalog, path string) bool {
	return strings.EqualFold(strings.TrimSpace(path), deathQiPathName(catalog))
}

// normaliseQiType makes a dantian hold what its owner's path says it holds.
// The path is the single source of truth, so a ghost cultivator is never left
// gathering spirit qi because their qi body row has not been written yet, and
// a reincarnation off the road switches back.
func normaliseQiType(catalog worlddata.Catalog, body qiBody, path string) qiBody {
	wanted := spiritQiType
	if isGhostPath(catalog, path) {
		wanted = deathQiType
	}
	body.QiType = wanted
	return body
}

// ---------------------------------------------------------------------------
// What the residue makes of the body
// ---------------------------------------------------------------------------

// ghostFormIndex is the deepest form this cultivator's corruption and realm
// have earned. Form 0 is living flesh, which is what everyone starts as.
func ghostFormIndex(catalog worlddata.Catalog, corruption, realm int64) int64 {
	best := int64(0)
	for index, form := range catalog.DeathQi.GhostForms {
		if corruption >= form.Corruption && realm >= form.MinRealmIndex {
			best = int64(index)
		}
	}
	return best
}

func ghostFormAt(catalog worlddata.Catalog, index int64) worlddata.GhostForm {
	forms := catalog.DeathQi.GhostForms
	if len(forms) == 0 {
		return worlddata.GhostForm{Name: "Living Flesh", CapacityMult: 1}
	}
	return forms[clampI64(index, 0, int64(len(forms)-1))]
}

// ghostFormCapacityMultiplier is what the form is worth to the lower dantian.
// A living cultivator is never touched by it.
func ghostFormCapacityMultiplier(catalog worlddata.Catalog, body qiBody) float64 {
	if !body.isDeathQi() {
		return 1
	}
	mult := ghostFormAt(catalog, body.GhostForm).CapacityMult
	if mult <= 0 {
		return 1
	}
	return mult
}

// corruptionPurityPenalty is how much of the middle dantian's ceiling the
// residue has eaten.
func corruptionPurityPenalty(catalog worlddata.Catalog, body qiBody) int64 {
	if !body.isDeathQi() {
		return 0
	}
	per := int64(catalog.DeathQi.Corruption.PurityCeilingPenaltyPerTen)
	if per <= 0 {
		per = 3
	}
	return clampI64(body.Corruption, 0, corruptionCap) / 10 * per
}

// ---------------------------------------------------------------------------
// The ground and the hours, read the other way round
// ---------------------------------------------------------------------------

// deathQiGroundMultiplier prices a place for a ghost cultivator: a ruin is
// rich, a shrine is hostile, a crowded city is thin.
func deathQiGroundMultiplier(catalog worlddata.Catalog, location string) (string, float64) {
	ground := catalog.DeathQi.Ground
	base := ground.Default
	if base <= 0 {
		base = 1
	}
	def, ok := catalog.Locations[location]
	if !ok {
		return "", round4(base)
	}
	if mult, ok := ground.RoadSites[def.RoadSite]; ok && mult > 0 {
		return "the " + strings.ReplaceAll(def.RoadSite, "_", " "), round4(mult)
	}
	if mult, ok := ground.Districts[def.District]; ok && mult > 0 {
		return "the " + def.District + " quarter", round4(mult)
	}
	// A living city is a poor place to gather what the dead leave.
	if def.SettlementType != "" && ground.CityPenalty > 0 {
		return "the streets of the living", round4(ground.CityPenalty)
	}
	return "", round4(base)
}

// deathQiHourMultiplier is the hour, which runs the other way from a living
// cultivator's: night is the ghost road's noon.
func deathQiHourMultiplier(catalog worlddata.Catalog, period string) float64 {
	if mult, ok := catalog.DeathQi.Hours[period]; ok && mult > 0 {
		return round4(mult)
	}
	return 1
}

// ghostDaylightPenalty is what the form pays for gathering under the sun. It
// is folded into the hour, so it only ever bites in daylight.
func ghostDaylightPenalty(catalog worlddata.Catalog, body qiBody, period string) float64 {
	if !body.isDeathQi() {
		return 0
	}
	switch period {
	case "Morning", "Afternoon", "Dawn":
		return ghostFormAt(catalog, body.GhostForm).DaylightPenalty
	}
	return 0
}

// ---------------------------------------------------------------------------
// Corruption
// ---------------------------------------------------------------------------

// addCorruption deepens the residue and promotes the ghost form when the new
// depth has earned one. It returns the body as it now stands and the name of
// the form if this is the step that crossed into it.
func addCorruption(conn *storage.Conn, catalog worlddata.Catalog, userID int64, body qiBody, amount, realm int64, now float64) (qiBody, string, error) {
	if !body.isDeathQi() || amount == 0 {
		return body, "", nil
	}
	body.Corruption = clampI64(body.Corruption+amount, 0, corruptionCap)
	risen := ""
	if form := ghostFormIndex(catalog, body.Corruption, realm); form > body.GhostForm {
		body.GhostForm = form
		risen = ghostFormAt(catalog, form).Name
	}
	return body, risen, saveQiBody(conn, userID, body, now)
}

// corruptionRupture is the channel the residue tears once it is deep enough.
// It is rolled once a session, and only above the content's threshold.
func corruptionRupture(conn *storage.Conn, catalog worlddata.Catalog, userID int64, body qiBody, now float64) (bool, error) {
	settings := catalog.DeathQi.Corruption
	threshold := int64(settings.RuptureThreshold)
	chance := settings.RuptureChancePercent
	if !body.isDeathQi() || threshold <= 0 || chance <= 0 || body.Corruption < threshold {
		return false, nil
	}
	roll, err := gamerng.Intn(100)
	if err != nil {
		return false, err
	}
	if roll >= chance {
		return false, nil
	}
	if _, err = damageMeridian(conn, userID, now); err != nil {
		return false, err
	}
	return true, nil
}

// ---------------------------------------------------------------------------
// The actions
// ---------------------------------------------------------------------------

type ghostActionPayload struct {
	GameMinute      int64 `json:"game_minute"`
	CooldownSeconds int64 `json:"cooldown_seconds"`
}

// requireGhostCultivator refuses every ghost action to a cultivator who was
// not born to the road.
func requireGhostCultivator(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (mechanicsCharacter, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return mechanicsCharacter{}, err
	}
	if c.LifeStatus != "alive" {
		return mechanicsCharacter{}, errors.New("a deceased incarnation walks no road at all")
	}
	if !isGhostPath(catalog, c.Path) {
		return mechanicsCharacter{}, fmt.Errorf("the ghost road is walked only by a %s, and only those born to a ghost household may become one", deathQiPathName(catalog))
	}
	return c, nil
}

// ghostHarvestAction takes the residue a place is holding: fast qi where the
// living are gone, paid for in corruption and in karma.
func ghostHarvestAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p ghostActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := requireGhostCultivator(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if rem, err := cooldownRemaining(conn, userID, "ghost_harvest", now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("what this place held has not gathered again: %d seconds", rem)
	}
	groundName, groundMult := deathQiGroundMultiplier(catalog, c.Location)
	if groundMult < ghostHarvestGroundFloor {
		return authoritativeMutation{}, errors.New("nothing died here recently enough to leave anything worth taking; stand among ruins or on a hunting ground")
	}
	state, err := settleQi(conn, catalog, userID, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if state.Qi >= state.Capacity {
		return authoritativeMutation{}, errors.New("the dantian is already full; there is nowhere to put it")
	}
	gained := minI64(state.Capacity-state.Qi, maxI64(1, int64(math.Round(float64(state.Capacity)*ghostHarvestShare*groundMult))))
	if _, err = conn.Execute(`UPDATE characters SET qi=?,updated_at=? WHERE user_id=?`, []any{state.Qi + gained, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	cost := int64(catalog.DeathQi.Corruption.PerHarvest)
	if cost <= 0 {
		cost = 6
	}
	body, risen, err := addCorruption(conn, catalog, userID, state.Body, cost, c.RealmIndex, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`UPDATE characters SET karma_score=MAX(-1000,MIN(1000,karma_score-2)),updated_at=? WHERE user_id=?`, []any{now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	cool := p.CooldownSeconds
	if cool <= 0 {
		cool = 900
	}
	if err = setCooldown(conn, userID, "ghost_harvest", cool, now); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"qi_gained": gained, "qi": state.Qi + gained, "qi_max": state.Capacity,
		"ground_name": groundName, "ground_mult": groundMult,
		"corruption": body.Corruption, "corruption_gain": cost,
		"ghost_form": body.GhostForm, "ghost_form_name": ghostFormAt(catalog, body.GhostForm).Name,
		"form_risen": risen, "karma_delta": int64(-2),
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "death_qi_harvested", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// ghostAppeaseAction burns incense where the living keep their dead, which is
// the one thing that sheds the residue - and the one ground a ghost
// cultivator gathers nothing on.
func ghostAppeaseAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p ghostActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := requireGhostCultivator(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	body, err := loadQiBody(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	body = normaliseQiType(catalog, body, c.Path)
	if body.Corruption <= 0 {
		return authoritativeMutation{}, errors.New("nothing clings to you that incense would lift")
	}
	if rem, err := cooldownRemaining(conn, userID, "ghost_appease", now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("the rites cannot be repeated so soon: %d seconds", rem)
	}
	groundName, groundMult := deathQiGroundMultiplier(catalog, c.Location)
	if groundMult > ghostAppeaseGroundCeiling {
		return authoritativeMutation{}, errors.New("incense must be burned where the living keep their dead: a wayside shrine or a temple quarter")
	}
	settings := catalog.DeathQi.Corruption
	relief := int64(settings.AppeaseRelief)
	if relief <= 0 {
		relief = 9
	}
	stones := int64(settings.AppeaseStoneCost)
	if stones <= 0 {
		stones = 25
	}
	stones = stones * (1 + body.Corruption/25)
	held, err := conn.Execute(`SELECT spirit_stones FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	purse := int64(0)
	if row := firstRowMap(held); row != nil {
		purse = i64(row["spirit_stones"])
	}
	if purse < stones {
		return authoritativeMutation{}, fmt.Errorf("the rites cost %d spirit stones; you have %d", stones, purse)
	}
	if _, err = conn.Execute(`UPDATE characters SET spirit_stones=spirit_stones-?,karma_score=MAX(-1000,MIN(1000,karma_score+1)),updated_at=? WHERE user_id=?`, []any{stones, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	before := body.Corruption
	body.Corruption = clampI64(body.Corruption-relief, 0, corruptionCap)
	// The form does not fall back with the residue: what the body has become
	// it stays. Only the ceiling on purity and the tearing ease off.
	if err = saveQiBody(conn, userID, body, now); err != nil {
		return authoritativeMutation{}, err
	}
	cool := p.CooldownSeconds
	if cool <= 0 {
		cool = 3600
	}
	if err = setCooldown(conn, userID, "ghost_appease", cool, now); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"corruption": body.Corruption, "corruption_shed": before - body.Corruption,
		"stones_spent": stones, "ground_name": groundName, "ground_mult": groundMult,
		"ghost_form": body.GhostForm, "ghost_form_name": ghostFormAt(catalog, body.GhostForm).Name,
		"karma_delta": int64(1),
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "death_qi_appeased", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// ghostStatusQuery is the ghost road's own sheet: what the body has become,
// what the ground and the hour are worth here and now, and what the residue
// is costing.
func ghostStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	body, err := loadQiBody(conn, userID)
	if err != nil {
		return nil, err
	}
	body = normaliseQiType(catalog, body, c.Path)
	walking := isGhostPath(catalog, c.Path)
	groundName, groundMult := deathQiGroundMultiplier(catalog, c.Location)
	tm := cultivationTimeModifiers(gameMinute, c.SpiritualRoot)
	form := ghostFormAt(catalog, body.GhostForm)
	next := map[string]any(nil)
	if body.GhostForm+1 < int64(len(catalog.DeathQi.GhostForms)) {
		step := catalog.DeathQi.GhostForms[body.GhostForm+1]
		next = map[string]any{
			"name": step.Name, "corruption": step.Corruption, "min_realm_index": step.MinRealmIndex,
			"capacity_mult": step.CapacityMult, "note": step.Note,
		}
	}
	return map[string]any{
		"walking_the_road": walking, "path": c.Path, "ghost_path": deathQiPathName(catalog),
		"qi_type":    firstNonempty(body.QiType, spiritQiType),
		"corruption": body.Corruption, "corruption_cap": int64(corruptionCap),
		"ghost_form": body.GhostForm, "ghost_form_name": form.Name, "ghost_form_note": form.Note,
		"capacity_mult": form.CapacityMult, "daylight_penalty": form.DaylightPenalty,
		"next_form":   next,
		"ground_name": groundName, "ground_mult": groundMult,
		"period": tm.Period, "hour_mult": deathQiHourMultiplier(catalog, tm.Period),
		"purity_ceiling_penalty": corruptionPurityPenalty(catalog, body),
		"rupture_threshold":      int64(catalog.DeathQi.Corruption.RuptureThreshold),
		"can_harvest_here":       walking && groundMult >= ghostHarvestGroundFloor,
		"can_appease_here":       walking && groundMult <= ghostAppeaseGroundCeiling,
	}, nil
}

// ghostStoneCount is the purse read the appease rite needs; kept beside the
// action so the surface can quote a price without a second query shape.
func ghostStoneCount(conn *storage.Conn, userID int64) (int64, error) {
	res, err := conn.Execute(`SELECT spirit_stones FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if row := firstRowMap(res); row != nil {
		return storage.ParseInt(row["spirit_stones"]), nil
	}
	return 0, nil
}
