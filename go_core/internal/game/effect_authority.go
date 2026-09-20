package game

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const (
	pillToxicityDecayMinutes = int64(12 * 60)
	pillToxicityDecayAmount  = int64(1)

	// pillToxicitySaturated is the toxicity at which the meridians are
	// carrying more medicine than they can hold: the shared penalty effect
	// appears here, and from here a purge can scorch. It was the bare literal
	// 40 in both readers below and nowhere else, which was fine while it meant
	// one thing and is not now that `alchemy.purge` hangs a risk on it
	// (v1.0.0-rc.58).
	pillToxicitySaturated = int64(40)
)

type canonicalWorldClock struct {
	AnchorGameMinute int64   `json:"anchor_game_minute"`
	AnchorRealTS     float64 `json:"anchor_real_ts"`
	Scale            int64   `json:"scale"`
}

// The clock's seed, and the only place the world's starting rate is decided
// (v1.0.0-rc.39). WORLD_TIME_SCALE used to be read by Python alone, and the
// engine container was never given it - so the rate an operator set in .env
// reached the world only because `Database.get_world_clock` re-anchored the
// row behind the engine's back, which also silently undid a rate a GM had set
// on the dashboard. The key is the engine's now, compose passes it through,
// and it seeds a *new* world only: after that the stored scale is the last
// word and `admin.world.advance_time` is the one thing that changes it.
const (
	defaultClockAnchorGameMinute = int64(8 * 60)
	fallbackClockScale           = int64(4)
	maxClockScale                = int64(60)
)

func clockScaleFromEnv() int64 {
	raw := strings.TrimSpace(os.Getenv("WORLD_TIME_SCALE"))
	if raw == "" {
		return fallbackClockScale
	}
	scale, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || scale < 0 || scale > maxClockScale {
		return fallbackClockScale
	}
	return scale
}

func defaultWorldClock(now float64) canonicalWorldClock {
	return canonicalWorldClock{
		AnchorGameMinute: defaultClockAnchorGameMinute,
		AnchorRealTS:     now,
		Scale:            clockScaleFromEnv(),
	}
}

// loadCanonicalWorldClock is the one read of the clock row: the SELECT, the
// decode, and the two clamps. `seeded` is false when the world has no row yet,
// which is what lets one caller insert the default and the read-only ones
// leave the database alone.
func loadCanonicalWorldClock(conn *storage.Conn, now float64) (canonicalWorldClock, bool, error) {
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='world_clock'`, nil)
	if err != nil {
		return canonicalWorldClock{}, false, err
	}
	state := defaultWorldClock(now)
	seeded := false
	if row := firstRowMap(res); row != nil {
		seeded = true
		if err := json.Unmarshal([]byte(fmt.Sprint(row["value_json"])), &state); err != nil {
			return canonicalWorldClock{}, true, fmt.Errorf("invalid canonical world clock: %w", err)
		}
	}
	if state.Scale < 0 {
		state.Scale = 0
	}
	if state.AnchorRealTS <= 0 {
		state.AnchorRealTS = now
	}
	return state, seeded, nil
}

// worldClockGameMinute is the world clock's arithmetic, and the only copy of
// it: the minute is the anchor plus the real minutes since the anchor, at the
// stored rate. Scale 0 freezes the world at its anchor.
func worldClockGameMinute(state canonicalWorldClock, now float64) int64 {
	elapsedRealMinutes := (now - state.AnchorRealTS) / 60.0
	if elapsedRealMinutes < 0 {
		elapsedRealMinutes = 0
	}
	gameMinute := state.AnchorGameMinute + int64(elapsedRealMinutes*float64(state.Scale))
	if gameMinute < 0 {
		return 0
	}
	return gameMinute
}

func canonicalWorldGameMinute(conn *storage.Conn) (int64, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	state, seeded, err := loadCanonicalWorldClock(conn, now)
	if err != nil {
		return 0, err
	}
	if !seeded {
		encoded, _ := json.Marshal(state)
		if _, err := conn.Execute(
			`INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,?)`,
			[]any{string(encoded), now},
		); err != nil {
			return 0, err
		}
	}
	return worldClockGameMinute(state, now), nil
}

// CanonicalWorldGameMinute is the world clock as the engine computes it, for
// callers outside this package. Read-only: unlike canonicalWorldGameMinute it
// never inserts a default clock row, so a scheduled tick cannot create one.
//
// It exists because "what time is it" must not be a request parameter. The
// authoritative action path has always rejected a caller-supplied game_minute
// (rejectCallerGameMinute); the simulation used to accept one, which let a
// trusted client hand the world an arbitrary minute and make the tick catch up
// to it. Both paths now derive it here.
func CanonicalWorldGameMinute(conn *storage.Conn) (int64, error) {
	return readCanonicalWorldGameMinute(conn)
}

func readCanonicalWorldGameMinute(conn *storage.Conn) (int64, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	state, _, err := loadCanonicalWorldClock(conn, now)
	if err != nil {
		return 0, err
	}
	return worldClockGameMinute(state, now), nil
}

// readCanonicalWorldClock is the read-only counterpart of canonicalWorldGameMinute:
// it never inserts a default row (queries shouldn't need write access), and it
// hands back the anchor/scale themselves, not just the derived current minute,
// so a caller can convert *any* absolute game-minute (like a travel arrival) to
// a real-world Unix timestamp with the same anchor - not just "now".
func readCanonicalWorldClock(conn *storage.Conn) (canonicalWorldClock, error) {
	state, _, err := loadCanonicalWorldClock(conn, float64(time.Now().UnixNano())/1e9)
	return state, err
}

// realTimestampForGameMinute converts an absolute game-clock minute to a real
// Unix timestamp (seconds) using the world clock's own anchor/scale, the
// inverse of the anchor + elapsed*scale formula the clock itself advances by.
// ok is false when the clock is frozen (scale 0) and the target lies in the
// future relative to the anchor, since there is then no real time at which it
// is ever reached.
func realTimestampForGameMinute(clock canonicalWorldClock, targetGameMinute int64) (ts float64, ok bool) {
	if clock.Scale <= 0 {
		if targetGameMinute <= clock.AnchorGameMinute {
			return clock.AnchorRealTS, true
		}
		return 0, false
	}
	gameMinutesFromAnchor := float64(targetGameMinute - clock.AnchorGameMinute)
	return clock.AnchorRealTS + (gameMinutesFromAnchor/float64(clock.Scale))*60.0, true
}

func previewPillToxicity(conn *storage.Conn, userID, gameMinute int64) (int64, error) {
	res, err := conn.Execute(
		`SELECT pill_toxicity,last_toxicity_game_minute FROM alchemy_state WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, nil
	}
	toxicity := clamp(storage.ParseInt(row["pill_toxicity"]), 0, 100)
	last := storage.ParseInt(row["last_toxicity_game_minute"])
	if last <= 0 {
		return toxicity, nil
	}
	current := maxI64(last, gameMinute)
	steps := maxI64(0, (current-last)/pillToxicityDecayMinutes)
	return maxI64(0, toxicity-steps*pillToxicityDecayAmount), nil
}

func pillToxicityEffectView(userID, toxicity, gameMinute int64) (map[string]any, error) {
	if toxicity < pillToxicitySaturated {
		return nil, nil
	}
	raw, err := medicineToxicityEffectJSON(toxicity)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{}
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return nil, err
	}
	payload["effect_key"] = "pill_toxicity"
	payload["name"] = "Pill Toxicity"
	payload["source_type"] = "alchemy"
	payload["source_id"] = "pill_toxicity"
	payload["stacks"] = int64(1)
	payload["starts_game_minute"] = gameMinute
	payload["ends_game_minute"] = nil
	payload["user_id"] = userID
	return payload, nil
}

func currentEffectsAuthorityQuery(conn *storage.Conn, userID int64) (map[string]any, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("actor_id must be positive")
	}
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	toxicity, err := previewPillToxicity(conn, userID, gameMinute)
	if err != nil {
		return nil, err
	}
	effect, err := pillToxicityEffectView(userID, toxicity, gameMinute)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"game_minute":          gameMinute,
		"pill_toxicity":        toxicity,
		"pill_toxicity_effect": effect,
	}, nil
}

// settleDueToxicityTx brings the shared pill-toxicity effect row in line with
// the time that has passed, before anything reads it.
//
// Toxicity decays on the clock, but the row that carries its penalty is only
// rewritten when the player acts. Until v0.23.0 the sync ran from Python, and
// only when someone happened to open `/alchemy status` - so a player who took
// a heavy dose and then simply waited kept the full penalty on every
// cultivation attempt until they thought to check a screen. Reading the effect
// table is now the trigger, which is the only moment it can matter.
//
// It no-ops when there is nothing to settle: no alchemy_state table (a narrow
// test fixture, or a database mid-migration) or no row for this player, so it
// never conjures state for someone who has never touched alchemy.
func settleDueToxicityTx(conn *storage.Conn, userID, gameMinute int64) error {
	if !tableExistsTx(conn, "alchemy_state") || !tableExistsTx(conn, "active_effects") {
		return nil
	}
	res, err := conn.Execute(`SELECT 1 FROM alchemy_state WHERE user_id=? LIMIT 1`, []any{userID})
	if err != nil || len(res.Rows) == 0 {
		return err
	}
	_, err = settlePillToxicityEffectTx(conn, userID, gameMinute)
	return err
}

func settlePillToxicityEffectTx(conn *storage.Conn, userID, gameMinute int64) (int64, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(
		`SELECT pill_toxicity,last_toxicity_game_minute FROM alchemy_state WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}

	toxicity := int64(0)
	last := maxI64(0, gameMinute)
	if row := firstRowMap(res); row == nil {
		if _, err := conn.Execute(
			`INSERT INTO alchemy_state(
				user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
				successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
			) VALUES(?,0,?,0,0,0,-99,'',?)`,
			[]any{userID, last, now},
		); err != nil {
			return 0, err
		}
	} else {
		toxicity = clamp(storage.ParseInt(row["pill_toxicity"]), 0, 100)
		last = storage.ParseInt(row["last_toxicity_game_minute"])
		current := maxI64(last, gameMinute)
		if last <= 0 {
			last = current
			if _, err := conn.Execute(
				`UPDATE alchemy_state SET last_toxicity_game_minute=?,updated_at=? WHERE user_id=?`,
				[]any{last, now, userID},
			); err != nil {
				return 0, err
			}
		} else {
			steps := maxI64(0, (current-last)/pillToxicityDecayMinutes)
			if steps > 0 {
				toxicity = maxI64(0, toxicity-steps*pillToxicityDecayAmount)
				last += steps * pillToxicityDecayMinutes
				if _, err := conn.Execute(
					`UPDATE alchemy_state
					 SET pill_toxicity=?,last_toxicity_game_minute=?,updated_at=?
					 WHERE user_id=?`,
					[]any{toxicity, last, now, userID},
				); err != nil {
					return 0, err
				}
			}
		}
	}

	if toxicity < pillToxicitySaturated {
		if _, err := conn.Execute(
			`DELETE FROM active_effects
			 WHERE user_id=? AND effect_key='pill_toxicity'
			   AND source_type='alchemy' AND source_id='pill_toxicity'`,
			[]any{userID},
		); err != nil {
			return 0, err
		}
		return toxicity, nil
	}

	effectJSON, err := medicineToxicityEffectJSON(toxicity)
	if err != nil {
		return 0, err
	}
	if _, err := conn.Execute(
		`INSERT INTO active_effects(
			user_id,effect_key,name,source_type,source_id,effect_json,stacks,
			starts_game_minute,ends_game_minute,created_at
		) VALUES(?,'pill_toxicity','Pill Toxicity','alchemy','pill_toxicity',?,1,?,NULL,?)
		ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET
			name=excluded.name,effect_json=excluded.effect_json,stacks=excluded.stacks,
			starts_game_minute=excluded.starts_game_minute,
			ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at`,
		[]any{userID, effectJSON, gameMinute, now},
	); err != nil {
		return 0, err
	}
	return toxicity, nil
}

func medicineToxicityEffectJSON(toxicity int64) (string, error) {
	toxicity = clamp(toxicity, 0, 100)
	cultivationMult := 0.95
	willPenalty := int64(0)
	switch {
	case toxicity >= 80:
		cultivationMult = 0.70
		willPenalty = -2
	case toxicity >= 60:
		cultivationMult = 0.85
		willPenalty = -1
	}
	alchemyPenalty := -maxI64(2, toxicity/10)
	modifiers := []map[string]any{
		{"stat": "cultivation_gain", "operation": "mul", "value": cultivationMult},
		{"stat": "alchemy_bonus", "operation": "add", "value": alchemyPenalty},
	}
	if willPenalty != 0 {
		modifiers = append(modifiers, map[string]any{
			"stat": "will", "operation": "add", "value": willPenalty,
		})
	}
	payload := map[string]any{
		"name":            "Pill Toxicity",
		"description":     "Accumulated medicinal residue makes further cultivation and medicine harder to assimilate.",
		"category":        "Medicine",
		"severity":        maxI64(1, toxicity/20),
		"special":         false,
		"resistance_stat": "",
		"modifiers":       modifiers,
		"tags":            []string{"pill", "toxicity", "medicine"},
		"stacking":        "replace",
		"max_stacks":      1,
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	return string(encoded), nil
}

func canonicalAdditiveEffectBonus(
	conn *storage.Conn,
	catalog worlddata.Catalog,
	userID int64,
	location string,
	gameMinute int64,
	stat string,
) (int64, error) {
	total := 0.0

	if err := settleDueToxicityTx(conn, userID, gameMinute); err != nil {
		return 0, err
	}
	res, err := conn.Execute(
		`SELECT effect_json,stacks FROM active_effects
		 WHERE user_id=? AND starts_game_minute<=?
		   AND (ends_game_minute IS NULL OR ends_game_minute>?)`,
		[]any{userID, gameMinute, gameMinute},
	)
	if err != nil {
		return 0, err
	}
	for _, row := range res.Rows {
		total += additiveEffectJSONStat(fmt.Sprint(row[0]), stat, maxI64(1, storage.ParseInt(row[1])))
	}

	location = strings.TrimSpace(location)
	if location != "" {
		res, err = conn.Execute(
			`SELECT effect_json FROM deployed_location_arrays
			 WHERE location=? AND starts_game_minute<=? AND ends_game_minute>?
			 ORDER BY starts_game_minute DESC LIMIT 1`,
			[]any{location, gameMinute, gameMinute},
		)
		if err != nil {
			return 0, err
		}
		if len(res.Rows) > 0 {
			total += additiveEffectJSONStat(fmt.Sprint(res.Rows[0][0]), stat, 1)
		}
	}

	res, err = conn.Execute(`SELECT mutation FROM character_spiritual_roots WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) > 0 {
		if definition, ok := catalog.SpiritualRootSystem.Mutations[fmt.Sprint(res.Rows[0][0])]; ok {
			total += additiveWorldModifiersStat(definition.Modifiers, stat)
		}
	}

	res, err = conn.Execute(
		`SELECT bloodline_id,state,evolution_stage FROM character_bloodlines
		 WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	if len(res.Rows) > 0 {
		id := fmt.Sprint(res.Rows[0][0])
		state := strings.TrimSpace(fmt.Sprint(res.Rows[0][1]))
		stage := intFromDB(res.Rows[0][2])
		if definition, ok := catalog.Bloodlines[id]; ok &&
			(state == "awakened" || state == "evolved" || state == "mutated") &&
			len(definition.Evolutions) > 0 {
			if stage < 1 {
				stage = 1
			}
			if stage > len(definition.Evolutions) {
				stage = len(definition.Evolutions)
			}
			total += additiveWorldModifiersStat(definition.Evolutions[stage-1].Modifiers, stat)
		}
	}

	res, err = conn.Execute(
		`SELECT physique_id,state,evolution_stage FROM character_physiques WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	if len(res.Rows) > 0 {
		id := fmt.Sprint(res.Rows[0][0])
		state := strings.TrimSpace(fmt.Sprint(res.Rows[0][1]))
		stage := intFromDB(res.Rows[0][2])
		if id != "ordinary_mortal_body" && (state == "awakened" || state == "evolved") {
			if definition, ok := catalog.Physiques[id]; ok {
				if len(definition.Evolutions) > 0 {
					if stage < 1 {
						stage = 1
					}
					if stage > len(definition.Evolutions) {
						stage = len(definition.Evolutions)
					}
					total += additiveWorldModifiersStat(definition.Evolutions[stage-1].Modifiers, stat)
				}
				total += additiveWorldModifiersStat(definition.DrawbackModifiers, stat)
			}
		}
	}

	return int64(total), nil
}

func additiveEffectJSONStat(raw, stat string, stacks int64) float64 {
	var payload effectPayload
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return 0
	}
	if stacks < 1 {
		stacks = 1
	}
	total := 0.0
	for _, modifier := range payload.Modifiers {
		if strings.TrimSpace(modifier.Stat) != stat {
			continue
		}
		operation := strings.ToLower(strings.TrimSpace(modifier.Operation))
		if operation == "" || operation == "add" {
			total += modifier.Value * float64(stacks)
		}
	}
	return total
}

func additiveWorldModifiersStat(modifiers []worlddata.Modifier, stat string) float64 {
	total := 0.0
	for _, modifier := range modifiers {
		if strings.TrimSpace(modifier.Stat) != stat {
			continue
		}
		operation := strings.ToLower(strings.TrimSpace(modifier.Operation))
		if operation == "" || operation == "add" {
			total += modifier.Value
		}
	}
	return total
}

// specialEffectPayload is the one lookup of `special_effects` (v1.0.0-rc.58).
//
// There were two, and they gave different answers to the same question. The
// law path took the comma-ok and refused an id the catalogue does not carry.
// The abode path took the bare index, so an unknown id yielded a nil map,
// `json.Marshal` wrote the literal `null` into `active_effects.effect_json`,
// the name fell back to the facility's own, and `abode.focus` *succeeded
// applying nothing* - which is the rc.19 fault the comment a few lines above
// that index already records, arriving a second time through another door.
//
// It is the `seller_user_id=0` lesson once more: the zero value of a map
// index looks like a value and is not a sentinel.
func specialEffectPayload(catalog worlddata.Catalog, effectID string) (map[string]any, string, error) {
	effect, known := catalog.SpecialEffects[effectID]
	if !known {
		return nil, "", fmt.Errorf("special effect %q is not in the catalogue", effectID)
	}
	return effect, strings.TrimSpace(fmt.Sprint(effect["name"])), nil
}

// multiplicativeEffectStat is canonicalAdditiveEffectBonus's `mul` twin, and
// it deliberately never fails: a reward must not become refusable because a
// row is missing, the way loadSeclusionCarried already answers 1 for every
// term it cannot read.
func multiplicativeEffectStat(conn *storage.Conn, userID, gameMinute int64, stat string) float64 {
	product := 1.0
	res, err := conn.Execute(
		`SELECT effect_json,stacks FROM active_effects
		 WHERE user_id=? AND starts_game_minute<=?
		   AND (ends_game_minute IS NULL OR ends_game_minute>?)`,
		[]any{userID, gameMinute, gameMinute},
	)
	if err != nil {
		return 1
	}
	for _, row := range res.Rows {
		var payload effectPayload
		if json.Unmarshal([]byte(fmt.Sprint(row[0])), &payload) != nil {
			continue
		}
		stacks := maxI64(1, storage.ParseInt(row[1]))
		for _, modifier := range payload.Modifiers {
			if strings.TrimSpace(modifier.Stat) != stat {
				continue
			}
			if strings.EqualFold(strings.TrimSpace(modifier.Operation), "mul") {
				product *= math.Pow(modifier.Value, float64(stacks))
			}
		}
	}
	return product
}

// grantInsightXPTx is the one door an insight-XP reward goes through
// (v1.0.0-rc.58).
//
// `items.heart_calming_pill` has carried `insight_gain x1.15` since it was
// authored - the "aids insight" of "Settles the mind, aids insight and
// suppresses heart-demon disturbances" - and eight statements added
// insight_xp without ever asking. A rate applied at one of eight places is
// the fault rc.56 took out of seclusion and rc.43 took out of the purse, so
// this is the same answer: one function, and `TestInsightXPHasOneDoor` holds
// every other writer to a named reason.
//
// It reads the canonical minute itself rather than taking one, which is
// rc.48's rule and also the practical answer: four of the six grant sites
// have no game minute in scope, and threading one through four signatures to
// reach a clock the engine owns would be the caller stating something the
// engine already knows.
//
// Neither the clock nor the multiplier can refuse a reward. A grant that
// became refusable because a row was missing would be a worse fault than the
// one this fixes, so an unreadable clock simply means no multiplier - the way
// loadSeclusionCarried answers 1 for every term it cannot read. And a
// positive grant never rounds away to nothing: a multiplier is a bonus, and
// the one thing a bonus must never do is take a reward off a player.
func grantInsightXPTx(conn *storage.Conn, userID, base int64, now float64) (int64, error) {
	if base <= 0 {
		return 0, nil
	}
	granted := base
	if gameMinute, err := canonicalWorldGameMinute(conn); err == nil {
		granted = int64(math.Round(float64(base) * multiplicativeEffectStat(conn, userID, gameMinute, "insight_gain")))
		if granted < 1 {
			granted = 1
		}
	}
	_, err := conn.Execute(
		`UPDATE characters SET insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`,
		[]any{granted, now, userID},
	)
	return granted, err
}
