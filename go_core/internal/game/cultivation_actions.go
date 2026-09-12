package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type cultivationActionPayload struct {
	GameMinute      int64 `json:"game_minute"`
	CooldownSeconds int64 `json:"cooldown_seconds"`
	Confirm         bool  `json:"confirm"`
	// Reroll (v1.0.0-rc.4): seize the moment after a failed breakthrough at
	// this stage - one more roll for Insight XP, once a stage, qi path only.
	Reroll bool `json:"reroll"`
}

type timeCultivationModifiers struct {
	Period        string  `json:"period"`
	Season        string  `json:"season"`
	QiMult        float64 `json:"qi_mult"`
	BodyMult      float64 `json:"body_mult"`
	RootResonance bool    `json:"root_resonance"`
}

func cultivationTimeModifiers(gameMinute int64, spiritualRoot string) timeCultivationModifiers {
	if gameMinute < 0 {
		gameMinute = 0
	}
	hour := (gameMinute / 60) % 24
	month := (gameMinute / (60 * 24 * 30)) % 12
	period := "Night"
	qi, body := 1.15, 0.95
	switch {
	case hour >= 5 && hour < 7:
		period, qi, body = "Dawn", 1.10, 1.05
	case hour >= 7 && hour < 12:
		period, qi, body = "Morning", 1.05, 1.10
	case hour >= 12 && hour < 17:
		period, qi, body = "Afternoon", 1.00, 1.10
	case hour >= 17 && hour < 20:
		period, qi, body = "Evening", 1.08, 1.00
	}
	seasons := []string{"Spring", "Summer", "Autumn", "Winter"}
	season := seasons[minInt(3, int(month/3))]
	affinity := map[string][]string{
		"Spring": {"Wood", "Wind", "Wood Root", "Wind Root"},
		"Summer": {"Fire", "Lightning", "Fire Root", "Lightning Root"},
		"Autumn": {"Metal", "Earth", "Metal Root", "Earth Root"},
		"Winter": {"Water", "Ice", "Water Root", "Ice Root"},
	}
	resonance := contains(affinity[season], spiritualRoot)
	if resonance {
		qi *= 1.10
	}
	return timeCultivationModifiers{Period: period, Season: season, QiMult: round4(qi), BodyMult: round4(body), RootResonance: resonance}
}

func round4(v float64) float64 { return math.Round(v*10000) / 10000 }
func mulOrOne(m resolvedModifiers, stat string) float64 {
	if v := m.Mul[stat]; v != 0 {
		return v
	}
	return 1
}
func dualResonance(c mechanicsCharacter) bool {
	return c.RealmIndex == c.BodyRealmIndex && c.Phase == c.BodyPhase
}
func dualTrainingBonus(c mechanicsCharacter, base int64) int64 {
	if !dualResonance(c) {
		return 0
	}
	v := int64(math.Round(float64(maxI64(0, base)) * 0.10))
	if v < 1 {
		v = 1
	}
	return v
}
func dualCheckBonus(c mechanicsCharacter) int64 {
	if dualResonance(c) {
		return 1
	}
	return 0
}

func soulCultivationMultiplier(conn *storage.Conn, userID int64) (float64, error) {
	res, err := conn.Execute(`SELECT talent_echo,special_trait FROM soul_legacy WHERE user_id=?`, []any{userID})
	if err != nil {
		return 1, err
	}
	if len(res.Rows) == 0 {
		return 1, nil
	}
	talent := storage.ParseInt(res.Rows[0][0])
	if talent < 0 {
		talent = 0
	}
	if talent > 100 {
		talent = 100
	}
	mult := 1.0 + math.Min(0.10, float64(talent)/1000.0)
	trait := fmt.Sprint(res.Rows[0][1])
	switch trait {
	case "Born Knowing":
		mult += 0.03
	case "Old Soul":
		mult += 0.02
	case "Heaven-Defying Fate":
		mult += 0.05
	}
	return round4(mult), nil
}

func eraCultivationMultiplier(conn *storage.Conn) (string, float64, error) {
	res, err := conn.Execute(`SELECT name,modifiers_json FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1`, nil)
	if err != nil {
		return "", 1, err
	}
	if len(res.Rows) == 0 {
		return "", 1, nil
	}
	name := fmt.Sprint(res.Rows[0][0])
	mods := map[string]any{}
	_ = json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][1])), &mods)
	mult := 1.0
	if v, ok := mods["cultivation_gain"]; ok {
		switch x := v.(type) {
		case float64:
			mult = x
		case int64:
			mult = float64(x)
		}
	} else if name == "Jade Meridian Awakening Era" {
		mult = 1.05
	}
	if mult < 0.25 {
		mult = 0.25
	}
	return name, mult, nil
}

func manorCultivationMultiplier(conn *storage.Conn, userID int64, location string) (string, float64, error) {
	res, err := conn.Execute(`SELECT m.name,m.base_location,m.qi_array_level FROM sect_membership sm JOIN sect_manors m ON m.sect_name=sm.sect_name WHERE sm.user_id=?`, []any{userID})
	if err != nil {
		return "", 1, err
	}
	if len(res.Rows) == 0 || fmt.Sprint(res.Rows[0][1]) != location {
		return "", 1, nil
	}
	level := storage.ParseInt(res.Rows[0][2])
	if level < 0 {
		level = 0
	}
	if level > 5 {
		level = 5
	}
	return fmt.Sprint(res.Rows[0][0]), 1.0 + 0.05*float64(level), nil
}

func qiStormBonus(conn *storage.Conn, location string, now float64) (int64, error) {
	res, err := conn.Execute(`SELECT payload_json FROM world_events WHERE active=1 AND ends_at>? AND location=?`, []any{now, location})
	if err != nil {
		return 0, err
	}
	for _, r := range res.Rows {
		var p map[string]any
		if json.Unmarshal([]byte(fmt.Sprint(r[0])), &p) == nil && fmt.Sprint(p["definition_id"]) == "qi_storm" {
			return 4, nil
		}
	}
	return 0, nil
}

func perfectionTraining(conn *storage.Conn, table string, userID, realmIndex int64, phase int64, cap int, bonus int64) (int64, error) {
	if phase != 9 || cap <= 0 {
		return 0, nil
	}
	q := fmt.Sprintf(`SELECT training_progress,active FROM %s WHERE user_id=? AND realm_index=?`, table)
	res, err := conn.Execute(q, []any{userID, realmIndex})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) == 0 || storage.ParseInt(res.Rows[0][1]) == 0 {
		return 0, nil
	}
	old := storage.ParseInt(res.Rows[0][0])
	r, err := gamerng.Intn(3)
	if err != nil {
		return 0, err
	}
	add := int64(1+r) + maxI64(0, bonus)
	next := minI64(int64(cap), old+add)
	delta := next - old
	if delta <= 0 {
		return 0, nil
	}
	q = fmt.Sprintf(`UPDATE %s SET training_progress=?,progress=MIN(100,progress+?),updated_at=? WHERE user_id=? AND realm_index=?`, table)
	_, err = conn.Execute(q, []any{next, delta, float64(time.Now().UnixNano()) / 1e9, userID, realmIndex})
	return delta, err
}

func cultivationTrain(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, body bool) (authoritativeMutation, error) {
	var p cultivationActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot cultivate")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	key := "cultivate"
	if body {
		key = "body_cultivate"
	}
	if rem, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("cultivation cooldown remaining: %d", rem)
	}

	// Which path is being trained, and what the stage it is filling costs:
	// since v1.0.0-rc.5 the cost is what sets the session's worth, so it is
	// read before the gain rather than after it as a cap.
	realms := catalog.Realms
	realm, phase, current := c.RealmIndex, c.Phase, c.Cultivation
	column, eventType, perfectionTable := "cultivation", "cultivate", "realm_perfection"
	cap := catalog.Perfection.TrainingCap
	attr := "will"
	if body {
		realms = catalog.BodyRealms
		realm, phase, current = c.BodyRealmIndex, c.BodyPhase, c.BodyCultivation
		column, eventType, perfectionTable = "body_cultivation", "body_cultivate", "body_realm_perfection"
		cap = catalog.BodyPerfection.TrainingCap
		attr = "body"
	}
	cost, err := phaseCost(realms, realm, phase)
	if err != nil {
		return authoritativeMutation{}, err
	}
	pace := stagePace(cost)

	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	mods, err := loadEffectModifiers(conn, userID, p.GameMinute, bundle, c, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	rv, err := gamerng.Intn(7)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// The pace, worked by the cultivator: their attribute is the quality of
	// the work and the d7 is the day's variance, +/- a tenth.
	attrValue := mods.value(c.Attributes[attr], attr)
	quality := attributeQuality(attrValue)
	variance := 0.9 + float64(rv)/30.0
	base := maxI64(1, int64(math.Round(float64(pace)*quality*variance)))
	resonance := dualTrainingBonus(c, base)
	tm := cultivationTimeModifiers(p.GameMinute, c.SpiritualRoot)
	timeMult := tm.QiMult
	effectMult := mulOrOne(mods, "cultivation_gain")
	if body {
		timeMult = tm.BodyMult
		effectMult = mulOrOne(mods, "body_cultivation_gain")
	}
	soulMult, err := soulCultivationMultiplier(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	eraName, eraMult, err := eraCultivationMultiplier(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	stance, err := loadCultivationStance(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// The world's own qi density (v1.0.0-rc.5): thin in the Mortal World,
	// thick above it.
	worldName := realmWorld(realms, realm)
	worldMult := worldQiMultiplier(catalog, worldName)
	attempted := int64(math.Round(float64(base+resonance) * timeMult * effectMult * soulMult * eraMult * stance.GainMult * worldMult))

	// The ground, and what the sect built on it. The manor array and a qi
	// storm are qi-path weather; the ground itself counts for both paths.
	placeName, placeMult, err := placeCultivationMultiplier(conn, catalog, userID, c.Location, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	attempted = int64(math.Round(float64(attempted) * placeMult))
	manorName := ""
	manorMult := 1.0
	storm := int64(0)
	if !body {
		manorName, manorMult, err = manorCultivationMultiplier(conn, userID, c.Location)
		if err != nil {
			return authoritativeMutation{}, err
		}
		attempted = int64(math.Round(float64(attempted) * manorMult))
		storm, err = qiStormBonus(conn, c.Location, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if storm > 0 {
			// A storm was a flat +4, which is a gift at Body Tempering and
			// nothing at Nascent Soul. It is a share of the session now.
			storm = maxI64(storm, pace/4)
			attempted += storm
		}
	}

	room := cost - current
	if room < 0 {
		room = 0
	}
	gain := attempted
	if gain < 0 {
		gain = 0
	}
	if gain > room {
		gain = room
	}
	if _, err = conn.Execute(fmt.Sprintf(`UPDATE characters SET %s=%s+?,updated_at=? WHERE user_id=?`, column, column), []any{gain, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	refineBonus := int64(0)
	if stance.Key == stanceRefine {
		refineBonus = 1
	}
	pg, err := perfectionTraining(conn, perfectionTable, userID, realm, phase, cap, refineBonus)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// A session that gathered nothing banks nothing and risks nothing: a full
	// stage was an endless Insight XP farm under Refine before v1.0.0-rc.5.
	insightGain, deviation := int64(0), map[string]any(nil)
	if gain > 0 {
		insightGain, deviation, err = applyStanceToTraining(conn, userID, stance, p.GameMinute, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	cool := p.CooldownSeconds
	if cool <= 0 {
		cool = 300
	}
	if err = setCooldown(conn, userID, key, cool, now); err != nil {
		return authoritativeMutation{}, err
	}
	total := current + gain
	payload := map[string]any{"mode": map[bool]string{true: "body", false: "qi"}[body], "gain": gain, "attempted_gain": attempted, "total": total, "cost": cost, "base_gain": base, "pace": pace, "sessions_per_stage": int64(cultivationSessionsPerStage), "attribute": attr, "attribute_value": attrValue, "attribute_quality": round4(quality), "resonance_bonus": resonance, "period": tm.Period, "season": tm.Season, "time_mult": timeMult, "root_resonance": tm.RootResonance, "effect_mult": effectMult, "soul_mult": soulMult, "era_name": eraName, "era_mult": eraMult, "world_name": worldName, "world_mult": worldMult, "manor_name": manorName, "manor_mult": manorMult, "storm_bonus": storm, "perfection_gain": pg, "ready": total >= cost, "stance": stance.Key, "stance_label": stance.Label, "stance_mult": stance.GainMult, "insight_xp_gain": insightGain, "deviation": deviation, "place_name": placeName, "place_mult": placeMult, "place_quality": placeQuality(placeMult), "stage_full": room == 0}
	legacy, _ := json.Marshal(payload)
	_, _ = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, eventType, string(legacy), now})
	return authoritativeMutation{Result: payload, Event: eventledger.Event{Domain: "cultivation", EventType: eventType, EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: payload}}, nil
}

func nextStage(realms []worlddata.Realm, index, phase int64) (int64, int64, bool) {
	if phase < 9 {
		return index, phase + 1, true
	}
	if index+1 >= int64(len(realms)) {
		return 0, 0, false
	}
	return index + 1, 1, true
}
func realmName(realms []worlddata.Realm, index int64) string {
	if index < 0 || index >= int64(len(realms)) {
		return "Unknown Realm"
	}
	return realms[index].Name
}
func realmWorld(realms []worlddata.Realm, index int64) string {
	if index < 0 || index >= int64(len(realms)) {
		return "Unknown World"
	}
	return realms[index].World
}
func breakthroughTN(realms []worlddata.Realm, index, phase int64) int64 {
	tn := realms[index].BaseTN
	if phase == 9 {
		tn += 3
	}
	return tn
}
func boolRow(conn *storage.Conn, sql string, args []any) (bool, error) {
	r, e := conn.Execute(sql, args)
	return e == nil && len(r.Rows) > 0, e
}

func checkPerfectionChoice(conn *storage.Conn, table string, userID, realm, phase int64, confirm bool) error {
	if phase != 9 {
		return nil
	}
	res, err := conn.Execute(fmt.Sprintf(`SELECT active,completed FROM %s WHERE user_id=? AND realm_index=?`, table), []any{userID, realm})
	if err != nil {
		return err
	}
	active, completed := false, false
	if len(res.Rows) > 0 {
		active = storage.ParseInt(res.Rows[0][0]) != 0
		completed = storage.ParseInt(res.Rows[0][1]) != 0
	}
	if active {
		return errors.New("perfect-path progression is active; complete or abandon it before breakthrough")
	}
	if !completed && !confirm {
		return errors.New("stage 9 perfection choice requires explicit confirmation to skip perfection")
	}
	return nil
}
func checkAscensionGate(conn *storage.Conn, oldWorld, newWorld string, userID, realm int64) error {
	if oldWorld == newWorld {
		return nil
	}
	if realm != 7 && realm != 15 && realm != 23 {
		return nil
	}
	ok, err := boolRow(conn, `SELECT 1 FROM tribulation_state WHERE user_id=? AND gate_realm_index=? AND cleared=1`, []any{userID, realm})
	if err != nil {
		return err
	}
	if !ok {
		return errors.New("world-crossing tribulation must be cleared before this breakthrough")
	}
	return nil
}
func completedPerfection(conn *storage.Conn, table string, userID, realm int64) (bool, error) {
	return boolRow(conn, fmt.Sprintf(`SELECT 1 FROM %s WHERE user_id=? AND realm_index=? AND completed=1 LIMIT 1`, table), []any{userID, realm})
}
func awakenSoulMemoryGo(conn *storage.Conn, userID, amount int64, now float64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT memory_seed,awakened_memory FROM soul_legacy WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return map[string]any{"memory_seed": 0, "awakened_memory": 0}, nil
	}
	seed := storage.ParseInt(res.Rows[0][0])
	aw := storage.ParseInt(res.Rows[0][1])
	if seed > 0 {
		aw = minI64(seed, aw+maxI64(1, amount))
		_, err = conn.Execute(`UPDATE soul_legacy SET awakened_memory=?,updated_at=? WHERE user_id=?`, []any{aw, now, userID})
		if err != nil {
			return nil, err
		}
	}
	return map[string]any{"memory_seed": seed, "awakened_memory": aw}, nil
}
func rewardMasterGo(conn *storage.Conn, disciple int64, realmChanged bool, now float64) (map[string]any, error) {
	att, contrib, influence, xp := int64(2), int64(1), int64(0), int64(3)
	if realmChanged {
		att, contrib, influence, xp = 5, 4, 2, 8
	}
	res, err := conn.Execute(`SELECT sl.master_user_id,c.name FROM sect_lineage sl JOIN characters c ON c.user_id=sl.master_user_id WHERE sl.disciple_user_id=?`, []any{disciple})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return nil, nil
	}
	mid := storage.ParseInt(res.Rows[0][0])
	name := fmt.Sprint(res.Rows[0][1])
	if _, err = conn.Execute(`UPDATE sect_lineage SET attention=attention+? WHERE disciple_user_id=?`, []any{att, disciple}); err != nil {
		return nil, err
	}
	if _, err = conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points+?,influence=influence+? WHERE user_id=?`, []any{contrib, influence, mid}); err != nil {
		return nil, err
	}
	if _, err = conn.Execute(`UPDATE characters SET insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`, []any{xp, now, mid}); err != nil {
		return nil, err
	}
	return map[string]any{"master_user_id": mid, "master_name": name, "attention": att, "contribution": contrib, "influence": influence, "insight_xp": xp}, nil
}
func recordAscensionHistory(conn *storage.Conn, userID int64, c mechanicsCharacter, body bool, fromWorld, toWorld string, newRealm int64, realmLabel string, gameMinute int64, now float64) error {
	mode := "qi"
	title := fmt.Sprintf("%s ascended to %s", c.Name, toWorld)
	summary := fmt.Sprintf("After clearing the world-crossing tribulation, %s broke through from %s into %s, entering %s.", c.Name, fromWorld, toWorld, realmLabel)
	tags := "ascension breakthrough " + fromWorld + " " + toWorld
	if body {
		mode = "body"
		title = fmt.Sprintf("%s ascended by the Body Path to %s", c.Name, toWorld)
		summary = fmt.Sprintf("%s crossed the world boundary through body cultivation, advancing from %s into %s.", c.Name, fromWorld, toWorld)
		tags = "ascension body cultivation " + fromWorld + " " + toWorld
	}
	meta, _ := json.Marshal(map[string]any{"from_world": fromWorld, "to_world": toWorld, "realm_index": newRealm, "mode": mode})
	source := fmt.Sprintf("ascension:%s:%d:%d:%d", mode, userID, newRealm, gameMinute)
	_, err := conn.Execute(`INSERT INTO world_history_events(source_key,event_type,title,summary,significance,visibility,location,world_name,faction,actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,related_npc_name,tags,game_minute,metadata_json,created_at,updated_at) VALUES(?,?,?,?,98,'public',?,?,?,'player',?,?, 'world',?,?,?,'',?,?,?, ?,?) ON CONFLICT(source_key) DO NOTHING`, []any{source, "ascension", title, summary, c.Location, toWorld, "", fmt.Sprint(userID), c.Name, toWorld, toWorld, userID, tags, gameMinute, string(meta), now, now})
	return err
}

func cultivationBreakthrough(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, body bool) (authoritativeMutation, error) {
	var p cultivationActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot break through")
	}
	realms := catalog.Realms
	realm, phase, current := c.RealmIndex, c.Phase, c.Cultivation
	perfectionTable := "realm_perfection"
	if body {
		realms = catalog.BodyRealms
		realm, phase, current = c.BodyRealmIndex, c.BodyPhase, c.BodyCultivation
		perfectionTable = "body_realm_perfection"
	}
	if realm < 0 || realm >= int64(len(realms)) {
		return authoritativeMutation{}, errors.New("realm index out of range")
	}
	cost, err := phaseCost(realms, realm, phase)
	if err != nil {
		return authoritativeMutation{}, err
	}
	reroll := p.Reroll && !body
	rerollCost := insightRerollCost(realm)
	if reroll {
		available, err := rerollState(conn, userID, realm, phase)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if !available {
			return authoritativeMutation{}, errors.New("there is no moment to seize: the last breakthrough at this stage did not fail, or it was seized once already")
		}
		xp, err := readInsightXP(conn, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if xp < rerollCost {
			return authoritativeMutation{}, fmt.Errorf("seizing the moment costs %d Insight XP; you have %d", rerollCost, xp)
		}
	} else if current < cost {
		return authoritativeMutation{}, fmt.Errorf("need %d cultivation essence but have %d", cost, current)
	}
	if err = checkPerfectionChoice(conn, perfectionTable, userID, realm, phase, p.Confirm); err != nil {
		return authoritativeMutation{}, err
	}
	newRealm, newPhase, ok := nextStage(realms, realm, phase)
	if !ok {
		return authoritativeMutation{}, errors.New("current cultivation ceiling reached")
	}
	oldWorld, newWorld := realmWorld(realms, realm), realmWorld(realms, newRealm)
	if err = checkAscensionGate(conn, oldWorld, newWorld, userID, realm); err != nil {
		return authoritativeMutation{}, err
	}
	perfect, err := completedPerfection(conn, perfectionTable, userID, realm)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// The realm gate (v1.0.0-rc.3): the qi path crosses into a new realm only
	// with a completed Realm Perfection or an insight banked from Insight XP.
	viaInsight := false
	if !body && newRealm != realm {
		open, insight, err := realmGateOpen(conn, userID, realm)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if !open {
			return authoritativeMutation{}, fmt.Errorf("the realm gate into %s is closed: bank an insight (%d Insight XP, Cultivation → Insight) or complete Realm Perfection first", realmName(realms, newRealm), insightGateCost(realm))
		}
		viaInsight = insight
	}
	perfectBonus := int64(0)
	if perfect {
		perfectBonus = 2
	}
	resonance := dualCheckBonus(c)
	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	mods, err := loadEffectModifiers(conn, userID, p.GameMinute, bundle, c, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	innate := int64(math.Round(mods.Add["breakthrough_bonus"]))
	modifier := breakthroughModifier(c, mods, body, perfectBonus, resonance, innate)
	tn := breakthroughTN(realms, realm, phase)
	probability := breakthroughOdds(modifier, tn)
	roll, err := roll2d10(modifier, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := boolResult(roll)
	failureLoss := maxI64(5, cost/10)
	now := float64(time.Now().UnixNano()) / 1e9
	if reroll {
		if _, err = conn.Execute(`UPDATE characters SET insight_xp=insight_xp-?,updated_at=? WHERE user_id=?`, []any{rerollCost, now, userID}); err != nil {
			return authoritativeMutation{}, err
		}
		if err = writeWorldStateMap(conn, cultivationRerollKey(userID), map[string]any{"realm_index": realm, "stage": phase, "game_minute": p.GameMinute}, now); err != nil {
			return authoritativeMutation{}, err
		}
	}
	vitalityGain := int64(0)
	attributeGains := map[string]any(nil)
	if success {
		if body {
			vitalityGain = 2
			if newRealm != realm {
				vitalityGain = 5
			}
			_, err = conn.Execute(`UPDATE characters SET body_realm_index=?,body_phase=?,body_cultivation=body_cultivation-?,vitality_max=vitality_max+?,vitality=vitality_max+?,updated_at=? WHERE user_id=?`, []any{newRealm, newPhase, cost, vitalityGain, vitalityGain, now, userID})
		} else {
			_, err = conn.Execute(`UPDATE characters SET realm_index=?,phase=?,cultivation=MAX(0,cultivation-?),updated_at=? WHERE user_id=?`, []any{newRealm, newPhase, cost, now, userID})
		}
		if err == nil && !body {
			_, err = conn.Execute(`DELETE FROM world_state WHERE key IN (?,?)`, []any{cultivationLastFailKey(userID), cultivationRerollKey(userID)})
		}
		if err == nil && newRealm != realm {
			attributeGains, err = growAttributesOnRealmCrossing(conn, catalog, userID, c, body, now)
		}
	} else {
		col := "cultivation"
		if body {
			col = "body_cultivation"
		}
		_, err = conn.Execute(fmt.Sprintf(`UPDATE characters SET %s=MAX(0,%s-?),updated_at=? WHERE user_id=?`, col, col), []any{failureLoss, now, userID})
		if err == nil && !body {
			err = writeWorldStateMap(conn, cultivationLastFailKey(userID), map[string]any{"realm_index": realm, "stage": phase, "game_minute": p.GameMinute}, now)
		}
	}
	if err != nil {
		return authoritativeMutation{}, err
	}
	rerollAvailable := false
	if !success && !body {
		if rerollAvailable, err = rerollState(conn, userID, realm, phase); err != nil {
			return authoritativeMutation{}, err
		}
	}
	xpLeft, err := readInsightXP(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	insightSpent := false
	if success && viaInsight {
		if _, err = conn.Execute(`DELETE FROM world_state WHERE key=?`, []any{cultivationInsightKey(userID)}); err != nil {
			return authoritativeMutation{}, err
		}
		insightSpent = true
	}
	result := map[string]any{"mode": map[bool]string{true: "body", false: "qi"}[body], "roll": roll, "success": success, "tn": tn, "modifier": modifier, "probability": probability, "realm_gate": newRealm != realm, "gate_via_insight": viaInsight, "insight_spent": insightSpent, "reroll": reroll, "reroll_cost": rerollCost, "reroll_available": rerollAvailable, "insight_xp": xpLeft, "cost": cost, "failure_loss": failureLoss, "from_realm": realmName(realms, realm), "from_stage": phase, "to_realm": realmName(realms, newRealm), "to_stage": newPhase, "from_world": oldWorld, "to_world": newWorld, "perfect_bonus": perfectBonus, "resonance_bonus": resonance, "innate_breakthrough_bonus": innate, "vitality_gain": vitalityGain, "ascended": oldWorld != newWorld, "attribute_gains": attributeGains, "world_mult": worldQiMultiplier(catalog, newWorld)}
	if success {
		legacy, err := awakenSoulMemoryGo(conn, userID, map[bool]int64{true: 4, false: 5}[body], now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		result["soul_legacy"] = legacy
		master, err := rewardMasterGo(conn, userID, newRealm != realm, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if master != nil {
			result["master_reward"] = master
		}
		if oldWorld != newWorld {
			if err = recordAscensionHistory(conn, userID, c, body, oldWorld, newWorld, newRealm, realmName(realms, newRealm), p.GameMinute, now); err != nil {
				return authoritativeMutation{}, err
			}
		}
	}
	legacy, _ := json.Marshal(result)
	eventType := "breakthrough_attempt"
	domainType := "cultivation_breakthrough_attempted"
	if body {
		eventType = "body_breakthrough_attempt"
		domainType = "body_breakthrough_attempted"
	}
	_, err = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, eventType, string(legacy), now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: domainType, EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
