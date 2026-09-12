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

// The qi body (v1.0.0-rc.7). Until this release a cultivator's qi pool was set
// once at creation from their spirit attribute - about fourteen points - and
// never grew again, while a technique cost one to thirteen of it. Qi was a
// tactical resource in a battle and nothing at all outside one.
//
// A cultivator now has a qi body: the meridians qi runs through, and the three
// dantian they feed.
//
//	meridians (0..108)  one opens with each stage, more by hand or by pill
//	  lower dantian     what you can hold: capacity
//	  middle dantian    how clean it is: purity, which sets what a skill costs
//	  upper dantian     how far you can feel: spiritual sense, from Nascent Soul
//
// Qi regenerates per game minute and is settled when it is read or spent, so
// nothing ticks in the background. Capacity is a share of what the realm
// itself costs, so it grows with the ladder; every qi cost and restore in the
// game is scaled by the same measure, which keeps a technique the same
// fraction of the pool it has always been.

const (
	// The channels: twelve is an ordinary cultivator, thirty-six a genius, a
	// hundred and eight the ceiling nobody is promised.
	meridianCeiling     = 108
	meridianStartOpen   = 12
	meridianCapacityAdd = 0.02 // each open meridian widens the pool
	meridianRegenAdd    = 0.01 // and quickens it

	// Purity: a skill costs (2 - purity/100) times its base, so fifty percent
	// purity is half again and a hundred is the base cost.
	purityStart = 50
	purityFloor = 5
	purityCap   = 100

	// The pool refills in this many game minutes at the base rate.
	qiRefillGameMinutes = 240

	// What a breakthrough attempt spends from the pool.
	breakthroughQiShare = 4

	// The pool a character was created with before the qi body existed:
	// `8 + 2 x spirit`. A content qi number is read as a fraction of that
	// pool and charged as the same fraction of the real one, so a technique
	// keeps the weight it was balanced at instead of being anchored to one
	// arbitrary number.
	legacyQiBaseline = 14.0
	// No single action may ask for more than this share of the dantian before
	// purity is applied, so nothing in the content can become unaffordable.
	qiCostShareCeiling = 0.5

	// The floor under any capacity, so a fresh mortal is not left with none.
	qiCapacityFloor = 120
)

// dantianStateMultiplier is what a cracked or shattered dantian holds.
func dantianStateMultiplier(state string) float64 {
	switch strings.TrimSpace(state) {
	case "cracked":
		return 0.5
	case "shattered":
		return 0.2
	}
	return 1.0
}

// manualCapacityMultiplier is what the practised method is worth to the pool:
// a mortal pamphlet widens it a tenth, a Dao method doubles it.
func manualCapacityMultiplier(grade string) float64 {
	switch grade {
	case "Mortal":
		return 1.10
	case "Earth":
		return 1.20
	case "Spirit":
		return 1.35
	case "Heaven":
		return 1.50
	case "Immortal":
		return 1.75
	case "Dao":
		return 2.00
	}
	return 1.0
}

type qiBody struct {
	Purity            int64
	MeridiansOpen     int64
	MeridiansDamaged  int64
	DantianState      string
	SettledGameMinute int64
	// v1.0.0-rc.8, the ghost road: which qi this dantian holds, the residue
	// death qi leaves behind, and what that residue has made of the body.
	QiType     string
	Corruption int64
	GhostForm  int64
}

// isDeathQi is whether this dantian holds death qi rather than spirit qi.
func (q qiBody) isDeathQi() bool { return q.QiType == deathQiType }

// effectiveMeridians is what actually carries qi: the open channels less the
// damaged ones.
func (q qiBody) effectiveMeridians() int64 {
	return clampI64(q.MeridiansOpen-q.MeridiansDamaged, 0, meridianCeiling)
}

// skillCostMultiplier is what impure qi does to every technique: at fifty
// purity a skill costs half again, at a hundred it costs its base. A damaged
// meridian doubles it on top, as a rupture should.
func (q qiBody) skillCostMultiplier() float64 {
	multiplier := 2.0 - float64(clampI64(q.Purity, 0, purityCap))/100.0
	if q.MeridiansDamaged > 0 {
		multiplier *= 2.0
	}
	return round4(math.Max(0.8, multiplier))
}

// loadQiBody reads a cultivator's qi body, creating the ordinary one for a
// character who predates the table.
func loadQiBody(conn *storage.Conn, userID int64) (qiBody, error) {
	res, err := conn.Execute(`SELECT purity,meridians_open,meridians_damaged,dantian_state,settled_game_minute,qi_type,corruption,ghost_form FROM character_qi_body WHERE user_id=?`, []any{userID})
	if err != nil {
		return qiBody{}, err
	}
	if row := firstRowMap(res); row != nil {
		return qiBody{
			Purity:            clampI64(i64(row["purity"]), 0, purityCap),
			MeridiansOpen:     clampI64(i64(row["meridians_open"]), 0, meridianCeiling),
			MeridiansDamaged:  maxI64(0, i64(row["meridians_damaged"])),
			DantianState:      fmt.Sprint(row["dantian_state"]),
			SettledGameMinute: i64(row["settled_game_minute"]),
			QiType:            firstNonempty(fmt.Sprint(row["qi_type"]), spiritQiType),
			Corruption:        clampI64(i64(row["corruption"]), 0, corruptionCap),
			GhostForm:         maxI64(0, i64(row["ghost_form"])),
		}, nil
	}
	return qiBody{Purity: purityStart, MeridiansOpen: meridianStartOpen, DantianState: "intact", QiType: spiritQiType}, nil
}

func saveQiBody(conn *storage.Conn, userID int64, body qiBody, now float64) error {
	_, err := conn.Execute(
		`INSERT INTO character_qi_body(user_id,purity,meridians_open,meridians_damaged,dantian_state,settled_game_minute,qi_type,corruption,ghost_form,created_at,updated_at)
		 VALUES(?,?,?,?,?,?,?,?,?,?,?)
		 ON CONFLICT(user_id) DO UPDATE SET purity=excluded.purity,meridians_open=excluded.meridians_open,meridians_damaged=excluded.meridians_damaged,dantian_state=excluded.dantian_state,settled_game_minute=excluded.settled_game_minute,qi_type=excluded.qi_type,corruption=excluded.corruption,ghost_form=excluded.ghost_form,updated_at=excluded.updated_at`,
		[]any{userID, clampI64(body.Purity, 0, purityCap), clampI64(body.MeridiansOpen, 0, meridianCeiling), maxI64(0, body.MeridiansDamaged), firstNonempty(body.DantianState, "intact"), body.SettledGameMinute, firstNonempty(body.QiType, spiritQiType), clampI64(body.Corruption, 0, corruptionCap), maxI64(0, body.GhostForm), now, now},
	)
	return err
}

// realmQiBase is the pool a realm is worth: half of what the whole realm costs
// in essence, shared across its nine stages, so capacity climbs the same
// ladder the cultivation does.
func realmQiBase(realms []worlddata.Realm, index, phase int64) int64 {
	if index < 0 || index >= int64(len(realms)) {
		return qiCapacityFloor
	}
	total := int64(0)
	for _, cost := range realms[index].PhaseCosts {
		total += cost
	}
	stageShare := 0.6 + 0.4*float64(clampI64(phase, 1, 9))/9.0
	return maxI64(qiCapacityFloor, int64(math.Round(float64(total)/2.0*stageShare)))
}

// qiCapacityFor is the lower dantian: what this cultivator can hold.
func qiCapacityFor(realms []worlddata.Realm, index, phase, spirit int64, body qiBody, manualGrade string) int64 {
	base := float64(realmQiBase(realms, index, phase))
	base *= attributeQuality(spirit)
	base *= 1 + meridianCapacityAdd*float64(body.effectiveMeridians())
	base *= manualCapacityMultiplier(manualGrade)
	base *= dantianStateMultiplier(body.DantianState)
	return maxI64(1, int64(math.Round(base)))
}

// qiRegenPerGameMinute is what the pool recovers while the world turns.
func qiRegenPerGameMinute(capacity int64, body qiBody, manualGrade string) float64 {
	rate := float64(capacity) / qiRefillGameMinutes
	rate *= 1 + meridianRegenAdd*float64(body.effectiveMeridians())
	rate *= 1 + (manualCapacityMultiplier(manualGrade)-1)/2
	if body.MeridiansDamaged > 0 {
		rate *= 0.5
	}
	return math.Max(0, rate)
}

// referenceQiPool is the pool a content qi number was balanced against: the
// one this character would have been created with.
func referenceQiPool(spirit int64) float64 {
	return math.Max(legacyQiBaseline, float64(8+2*maxI64(0, spirit)))
}

// qiShare reads a content qi number as a fraction of the pool it was written
// for.
func qiShare(base int64, reference float64) float64 {
	if base <= 0 || reference <= 0 {
		return 0
	}
	return float64(base) / reference
}

// scaledQiCost is one content-side qi number in this cultivator's terms: the
// same share of their dantian, priced by the purity of what they hold.
func scaledQiCost(base, capacity int64, reference float64, body qiBody) int64 {
	share := qiShare(base, reference)
	if share <= 0 {
		return 0
	}
	share = math.Min(share, qiCostShareCeiling)
	return maxI64(1, int64(math.Round(float64(capacity)*share*body.skillCostMultiplier())))
}

// scaledQiRestore is a pill's qi in this cultivator's terms. Purity does not
// enter: a pill gives what it gives.
func scaledQiRestore(base, capacity int64, reference float64) int64 {
	share := qiShare(base, reference)
	if share <= 0 {
		return 0
	}
	return maxI64(1, int64(math.Round(float64(capacity)*math.Min(1, share))))
}

// qiState is everything the engine needs about a cultivator's qi at one
// moment: the body, the pool, the rate, and what a skill costs them.
type qiState struct {
	Body        qiBody
	Capacity    int64
	Qi          int64
	Regen       float64
	Reference   float64
	ManualGrade string
	ManualName  string
}

// Cost is one content qi number in this cultivator's terms.
func (s qiState) Cost(base int64) int64 {
	return scaledQiCost(base, s.Capacity, s.Reference, s.Body)
}

// Restore is one content qi restore in this cultivator's terms.
func (s qiState) Restore(base int64) int64 {
	return scaledQiRestore(base, s.Capacity, s.Reference)
}

// settleQi brings a cultivator's pool up to the current game minute and
// writes it back, returning the state. Every read and every spend goes
// through here, which is why nothing has to tick.
func settleQi(conn *storage.Conn, catalog worlddata.Catalog, userID int64, gameMinute int64, now float64) (qiState, error) {
	body, err := loadQiBody(conn, userID)
	if err != nil {
		return qiState{}, err
	}
	res, err := conn.Execute(`SELECT realm_index,phase,qi,qi_max,attributes_json,path FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return qiState{}, err
	}
	row := firstRowMap(res)
	if row == nil {
		return qiState{}, errors.New("create a cultivation character first")
	}
	attributes := decodeJSONMap(row["attributes_json"])
	manualName, manualGrade, _, _, _, err := manualCultivationMultiplier(conn, catalog, userID)
	if err != nil {
		return qiState{}, err
	}
	realm, phase := i64(row["realm_index"]), i64(row["phase"])
	// v1.0.0-rc.8: which qi this dantian holds follows the path, so a ghost
	// cultivator never has to be switched over by hand and a reincarnation
	// out of the road switches back.
	body = normaliseQiType(catalog, body, fmt.Sprint(row["path"]))
	capacity := qiCapacityFor(catalog.Realms, realm, phase, i64(attributes["spirit"]), body, manualGrade)
	capacity = maxI64(1, int64(math.Round(float64(capacity)*ghostFormCapacityMultiplier(catalog, body))))
	regen := qiRegenPerGameMinute(capacity, body, manualGrade)

	qi := i64(row["qi"])
	// A character who predates the qi body carries the old tiny pool; scale
	// what they were holding into the new one rather than leaving them empty.
	if previous := i64(row["qi_max"]); previous > 0 && previous < capacity/4 {
		qi = int64(math.Round(float64(qi) / float64(previous) * float64(capacity)))
	}
	if body.SettledGameMinute > 0 && gameMinute > body.SettledGameMinute {
		qi += int64(math.Floor(float64(gameMinute-body.SettledGameMinute) * regen))
	}
	qi = clampI64(qi, 0, capacity)
	body.SettledGameMinute = gameMinute

	if _, err = conn.Execute(`UPDATE characters SET qi=?,qi_max=?,updated_at=? WHERE user_id=?`, []any{qi, capacity, now, userID}); err != nil {
		return qiState{}, err
	}
	if err = saveQiBody(conn, userID, body, now); err != nil {
		return qiState{}, err
	}
	return qiState{Body: body, Capacity: capacity, Qi: qi, Regen: round4(regen), Reference: referenceQiPool(i64(attributes["spirit"])), ManualGrade: manualGrade, ManualName: manualName}, nil
}

// spendQi takes qi from a settled pool, refusing when there is not enough.
func spendQi(conn *storage.Conn, userID, amount int64, state qiState, now float64) (int64, error) {
	if amount <= 0 {
		return state.Qi, nil
	}
	if state.Qi < amount {
		return state.Qi, fmt.Errorf("insufficient qi: %d required, %d in the dantian", amount, state.Qi)
	}
	left := state.Qi - amount
	_, err := conn.Execute(`UPDATE characters SET qi=?,updated_at=? WHERE user_id=?`, []any{left, now, userID})
	return left, err
}

// ---------------------------------------------------------------------------
// The meridians
// ---------------------------------------------------------------------------

// meridianOpenCost is the Insight XP the next channel asks for: it gets dearer
// the more are already open.
func meridianOpenCost(open int64) int64 {
	return 2 + maxI64(0, open)/6
}

// openMeridianOnStage opens one channel when a stage is crossed, which is the
// passive way a cultivator's body widens.
func openMeridianOnStage(conn *storage.Conn, userID int64, now float64) (int64, error) {
	body, err := loadQiBody(conn, userID)
	if err != nil {
		return 0, err
	}
	if body.MeridiansOpen >= meridianCeiling {
		return body.MeridiansOpen, nil
	}
	body.MeridiansOpen++
	return body.MeridiansOpen, saveQiBody(conn, userID, body, now)
}

// damageMeridian is what a severe qi deviation does to the channels.
func damageMeridian(conn *storage.Conn, userID int64, now float64) (int64, error) {
	body, err := loadQiBody(conn, userID)
	if err != nil {
		return 0, err
	}
	if body.MeridiansDamaged >= body.MeridiansOpen {
		// Every channel already ruptured: the dantian takes it instead.
		if body.DantianState == "intact" {
			body.DantianState = "cracked"
		}
	} else {
		body.MeridiansDamaged++
	}
	return body.MeridiansDamaged, saveQiBody(conn, userID, body, now)
}

type meridianPayload struct {
	GameMinute int64 `json:"game_minute"`
}

// meridianOpenAction forces the next channel open: Insight XP, a quarter of
// the pool, and a roll that gets harder the more are open. A failure spends
// everything and risks the qi running wild.
func meridianOpenAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p meridianPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation opens nothing")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	state, err := settleQi(conn, catalog, userID, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if state.Body.MeridiansDamaged > 0 {
		return authoritativeMutation{}, errors.New("a ruptured channel must be healed before another is opened")
	}
	if state.Body.MeridiansOpen >= meridianCeiling {
		return authoritativeMutation{}, fmt.Errorf("all %d meridians are open", meridianCeiling)
	}
	cost := meridianOpenCost(state.Body.MeridiansOpen)
	xp, err := readInsightXP(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if xp < cost {
		return authoritativeMutation{}, fmt.Errorf("opening the next meridian costs %d Insight XP; you have %d", cost, xp)
	}
	qiCost := maxI64(1, state.Capacity/4)
	if state.Qi < qiCost {
		return authoritativeMutation{}, fmt.Errorf("opening a meridian needs %d qi; the dantian holds %d", qiCost, state.Qi)
	}
	if _, err = conn.Execute(`UPDATE characters SET insight_xp=insight_xp-?,updated_at=? WHERE user_id=?`, []any{cost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = spendQi(conn, userID, qiCost, state, now); err != nil {
		return authoritativeMutation{}, err
	}
	// The channels resist more the more of them are already open.
	tn := 8 + state.Body.MeridiansOpen/4
	modifier := mods0(c.Attributes["body"]) + state.Body.Purity/20
	roll, err := roll2d10(modifier, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := boolResult(roll)
	body := state.Body
	deviation := map[string]any(nil)
	if success {
		body.MeridiansOpen++
		if err = saveQiBody(conn, userID, body, now); err != nil {
			return authoritativeMutation{}, err
		}
	} else {
		held, err := currentConditionSeverity(conn, userID, "qi_deviation")
		if err != nil {
			return authoritativeMutation{}, err
		}
		if deviation, err = applyCombatCondition(conn, userID, "qi_deviation", minI64(5, held+1), "cultivation", "meridian_open", p.GameMinute); err != nil {
			return authoritativeMutation{}, err
		}
	}
	after, err := settleQi(conn, catalog, userID, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"roll": roll, "success": success, "insight_spent": cost, "qi_spent": qiCost,
		"meridians_open": after.Body.MeridiansOpen, "meridians_damaged": after.Body.MeridiansDamaged,
		"meridian_ceiling": int64(meridianCeiling), "deviation": deviation,
		"qi": after.Qi, "qi_max": after.Capacity,
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "meridian_opened", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// meridianHealAction mends one ruptured channel. It asks for spirit stones and
// a day of quiet, which is the pill-and-rest arc of the genre.
func meridianHealAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p meridianPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	body, err := loadQiBody(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if body.MeridiansDamaged <= 0 && body.DantianState == "intact" {
		return authoritativeMutation{}, errors.New("nothing in your qi body is ruptured")
	}
	if rem, err := cooldownRemaining(conn, userID, "meridian_heal", now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("the channels need time between mendings: %d seconds", rem)
	}
	cost := int64(40) * maxI64(1, body.MeridiansDamaged)
	stones, err := conn.Execute(`SELECT spirit_stones FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	held := int64(0)
	if row := firstRowMap(stones); row != nil {
		held = i64(row["spirit_stones"])
	}
	if held < cost {
		return authoritativeMutation{}, fmt.Errorf("mending a channel costs %d spirit stones; you have %d", cost, held)
	}
	if _, err = conn.Execute(`UPDATE characters SET spirit_stones=spirit_stones-?,updated_at=? WHERE user_id=?`, []any{cost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	mended := ""
	if body.MeridiansDamaged > 0 {
		body.MeridiansDamaged--
		mended = "meridian"
	} else if body.DantianState == "cracked" {
		body.DantianState = "intact"
		mended = "dantian"
	}
	if err = saveQiBody(conn, userID, body, now); err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, "meridian_heal", 3600, now); err != nil {
		return authoritativeMutation{}, err
	}
	after, err := settleQi(conn, catalog, userID, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"mended": mended, "stones_spent": cost, "meridians_damaged": after.Body.MeridiansDamaged,
		"meridians_open": after.Body.MeridiansOpen, "dantian_state": after.Body.DantianState,
		"qi": after.Qi, "qi_max": after.Capacity,
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "meridian_healed", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// ---------------------------------------------------------------------------
// The middle dantian: purity
// ---------------------------------------------------------------------------

// purityCeilingFor is as clean as this cultivator's qi can get: the realm they
// stand in and the method they practise set it.
func purityCeilingFor(catalog worlddata.Catalog, realm int64, manualGrade string, body qiBody) int64 {
	ceiling := 55 + maxI64(0, realm)*2
	ceiling += int64(math.Round((manualCapacityMultiplier(manualGrade) - 1) * 40))
	// v1.0.0-rc.8: death qi leaves a residue, and a dantian holding a residue
	// cannot be kept as clean as one that never held it.
	ceiling -= corruptionPurityPenalty(catalog, body)
	return clampI64(ceiling, purityFloor, purityCap)
}

// refineQiAction is the middle dantian's work: a session spent cleaning what
// is already held rather than gathering more.
func refineQiAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p cultivationActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation refines nothing")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if rem, err := cooldownRemaining(conn, userID, "qi_refine", now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("refining cooldown remaining: %d", rem)
	}
	state, err := settleQi(conn, catalog, userID, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	ceiling := purityCeilingFor(catalog, c.RealmIndex, state.ManualGrade, state.Body)
	if state.Body.Purity >= ceiling {
		return authoritativeMutation{}, fmt.Errorf("your qi is already as clean as %s and this realm allow (%d%%); a better method raises the ceiling", firstNonempty(state.ManualName, "your method"), ceiling)
	}
	qiCost := maxI64(1, state.Capacity/5)
	if state.Qi < qiCost {
		return authoritativeMutation{}, fmt.Errorf("refining needs %d qi; the dantian holds %d", qiCost, state.Qi)
	}
	if _, err = spendQi(conn, userID, qiCost, state, now); err != nil {
		return authoritativeMutation{}, err
	}
	roll, err := gamerng.Intn(3)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gain := minI64(int64(2+roll), ceiling-state.Body.Purity)
	body := state.Body
	body.Purity += gain
	if err = saveQiBody(conn, userID, body, now); err != nil {
		return authoritativeMutation{}, err
	}
	cool := p.CooldownSeconds
	if cool <= 0 {
		cool = 1800
	}
	if err = setCooldown(conn, userID, "qi_refine", cool, now); err != nil {
		return authoritativeMutation{}, err
	}
	after, err := settleQi(conn, catalog, userID, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"purity": after.Body.Purity, "purity_gain": gain, "purity_ceiling": ceiling,
		"qi_spent": qiCost, "qi": after.Qi, "qi_max": after.Capacity,
		"skill_cost_mult": after.Body.skillCostMultiplier(),
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "qi_refined", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// losePurity is what forcing the gathering, or an untreated deviation, does to
// the cleanliness of what a cultivator holds.
func losePurity(conn *storage.Conn, userID, amount int64, now float64) error {
	if amount <= 0 {
		return nil
	}
	body, err := loadQiBody(conn, userID)
	if err != nil {
		return err
	}
	body.Purity = clampI64(body.Purity-amount, purityFloor, purityCap)
	return saveQiBody(conn, userID, body, now)
}

// ---------------------------------------------------------------------------
// The upper dantian, and the sheet
// ---------------------------------------------------------------------------

// upperDantianOpen is whether a cultivator has a spiritual sense at all: the
// upper dantian opens at Nascent Soul.
func upperDantianOpen(realm int64) bool { return realm >= 4 }

// spiritualSenseReach is what the upper dantian is worth to sensing, in the
// same units the sense action already uses.
func spiritualSenseReach(realm, purity int64) int64 {
	if !upperDantianOpen(realm) {
		return 0
	}
	return (realm - 3) * (50 + purity)
}

// qiBodyStatusQuery is the qi body as the sheet shows it.
func qiBodyStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	state, err := settleQi(conn, catalog, userID, gameMinute, now)
	if err != nil {
		return nil, err
	}
	xp, err := readInsightXP(conn, userID)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"qi": state.Qi, "qi_max": state.Capacity, "regen_per_game_minute": state.Regen,
		"purity": state.Body.Purity, "purity_ceiling": purityCeilingFor(catalog, c.RealmIndex, state.ManualGrade, state.Body),
		"skill_cost_mult": state.Body.skillCostMultiplier(),
		"meridians_open":  state.Body.MeridiansOpen, "meridians_damaged": state.Body.MeridiansDamaged,
		"meridian_ceiling": int64(meridianCeiling), "meridian_open_cost": meridianOpenCost(state.Body.MeridiansOpen),
		"dantian_state": state.Body.DantianState,
		"upper_open":    upperDantianOpen(c.RealmIndex), "sense_reach": spiritualSenseReach(c.RealmIndex, state.Body.Purity),
		"manual_name": state.ManualName, "manual_grade": state.ManualGrade,
		"manual_capacity_mult": manualCapacityMultiplier(state.ManualGrade),
		"insight_xp":           xp,
		"breakthrough_qi_cost": maxI64(1, state.Capacity/breakthroughQiShare),
		"qi_type":              firstNonempty(state.Body.QiType, spiritQiType),
		"corruption":           state.Body.Corruption,
		"ghost_form":           state.Body.GhostForm,
		"ghost_form_name":      ghostFormAt(catalog, state.Body.GhostForm).Name,
	}, nil
}

// mods0 is an attribute with no modifier resolution, for the few rolls that
// do not load the effect stack.
func mods0(value int64) int64 { return value }
