package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Alchemy purge (v0.23.0, the v0.21 Authority I backlog).
//
// `/alchemy purge` used to be four separate Python writes around a formula that
// lived in the Discord command body: spend Qi, reduce toxicity, rewrite the
// shared effect row, set a cooldown. Each was its own round trip, so a player
// who lost their connection between the second and the fourth paid the Qi,
// kept the reduced toxicity, and walked away with no cooldown - or, in the
// other order, paid and got nothing. The whole cycle is one transaction here.
//
// The formulas are copied from the Discord command as they stood, deliberately:
// this is a migration, not a rebalance. `will` and `spirit` are read from the
// character's stored attributes rather than through canonicalAttribute for the
// same reason, and for one of its own - toxicity applies a will penalty, so
// canonical attributes would make a heavy dose harder to purge in a way the
// old code never did. Changing that is a balance decision and belongs in its
// own change, with the reasoning written down.

const (
	alchemyPurgeCooldownSeconds = int64(60 * 60)
	alchemyPurgeMinQiCost       = int64(4)
	alchemyPurgeMaxQiCost       = int64(12)

	// The flame the Purging Phoenix Pill has always described (v1.0.0-rc.58).
	//
	// `items.purging_phoenix_pill` says "dangerous without cooling medicine"
	// and, on its effect, "without cooling support it may scorch meridians";
	// `physiques.nine_yang_solar_body` says "excess yang scorches the
	// meridians". Two pieces of content naming one mechanic the engine did
	// not have, and both naming `meridian_damage`, which it does - so this is
	// built from what the content already specifies rather than invented.
	//
	// Until now `alchemy.purge` had no risk at all: it spent qi and removed
	// toxicity and that was the entire action. A light purge still does
	// exactly that; the risk starts only once the meridians are saturated.
	alchemyScorchBaseTN      = int64(8)
	alchemyScorchHeatPerTN   = int64(6)
	alchemyScorchResistScale = int64(5)
	alchemyScorchSevereBand  = int64(-5)

	// alchemyDetoxScale turns `detox_power` - a 0-100 sort of number, authored
	// as 40 on the pill - into extra toxicity burned off. Four, so the pill is
	// worth +10, which roughly doubles a mid cultivator's purge and is what it
	// costs 26 stones for.
	alchemyDetoxScale = int64(4)
)

type alchemyPurgePayload struct {
	GameMinute int64 `json:"game_minute"`
}

// alchemyPurgeQiCost is `min(12, max(4, current // 8))` from the old command.
func alchemyPurgeQiCost(toxicity int64) int64 {
	return minI64(alchemyPurgeMaxQiCost, maxI64(alchemyPurgeMinQiCost, toxicity/8))
}

// alchemyPurgeAmount is `min(current, 8 + will//2 + spirit//3)` from the old
// command, plus whatever purging medicine the cultivator is carrying
// (v1.0.0-rc.58). `detox_power` is the number the Purging Phoenix Pill has
// always granted and nothing has ever read.
func alchemyPurgeAmount(toxicity, will, spirit, detox int64) int64 {
	return minI64(toxicity, 8+will/2+spirit/3+detox/alchemyDetoxScale)
}

// alchemyScorchTN is how hard the flame is to hold: nothing at all below
// saturation, then one point harder every six above it.
//
// It reads the toxicity *carried*, never the amount purged - a cultivator who
// drank a pill to burn off more must not be punished for the pill that is
// also their cooling.
func alchemyScorchTN(toxicity int64) int64 {
	return alchemyScorchBaseTN + (toxicity-pillToxicitySaturated)/alchemyScorchHeatPerTN
}

// alchemyScorchModifier is what holds the flame: the body it runs through, the
// will directing it, and the cooling they brought.
//
// The attributes are canonical here while `alchemyPurgeAmount`'s are stored,
// and the asymmetry is deliberate. The amount is a v0.23.0 transcription this
// file's own header says must not be rebalanced without its own change; the
// scorch is a new rule with nothing to preserve, and it is already reaching
// into `active_effects` for `fire_resistance`, so reading the cultivator as
// they actually are is the only consistent choice.
func alchemyScorchModifier(body, will, fireResistance int64) int64 {
	return body/2 + will/2 + fireResistance/alchemyScorchResistScale
}

func alchemyPurgeAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p alchemyPurgePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	cr, err := conn.Execute(
		`SELECT attributes_json,qi,qi_max FROM characters WHERE user_id=? AND life_status='alive'`,
		[]any{userID},
	)
	if err != nil {
		return authoritativeMutation{}, err
	}
	character := firstRowMap(cr)
	if character == nil {
		return authoritativeMutation{}, errors.New("living character not found")
	}

	now := float64(time.Now().UnixNano()) / 1e9
	remaining, err := cooldownRemaining(conn, userID, "alchemy_purge", now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}

	// Settling first applies the decay the player is owed for elapsed time, so
	// the purge is measured against the toxicity they actually carry.
	toxicity, err := settlePillToxicityEffectTx(conn, userID, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if toxicity <= 0 {
		return authoritativeMutation{}, errors.New("no pill toxicity to purge")
	}

	// Scaled into the cultivator's own pool (v1.0.0-rc.7).
	state, err := settleQi(conn, catalog, userID, p.GameMinute, nowSeconds())
	if err != nil {
		return authoritativeMutation{}, err
	}
	qiCost := state.Cost(alchemyPurgeQiCost(toxicity))
	qi := state.Qi
	if qi < qiCost {
		return authoritativeMutation{}, fmt.Errorf("insufficient qi: %d required, %d available", qiCost, qi)
	}

	attrs := decodeJSONMap(character["attributes_json"])
	detox, err := canonicalAdditiveEffectBonus(conn, catalog, userID, "", p.GameMinute, "detox_power")
	if err != nil {
		return authoritativeMutation{}, err
	}
	purged := alchemyPurgeAmount(toxicity, storage.ParseInt(attrs["will"]), storage.ParseInt(attrs["spirit"]), detox)
	if purged < 0 {
		purged = 0
	}
	remainingToxicity := maxI64(0, toxicity-purged)
	var scorchRoll, scorchedCondition map[string]any
	fireResisted := int64(0)

	if _, err = conn.Execute(
		`UPDATE characters SET qi=qi-?,updated_at=? WHERE user_id=?`,
		[]any{qiCost, now, userID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(
		`UPDATE alchemy_state SET pill_toxicity=?,updated_at=? WHERE user_id=?`,
		[]any{remainingToxicity, now, userID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	// Settling again is what keeps the shared effect row honest: crossing back
	// under `pillToxicitySaturated` has to delete it, and staying above has to
	// rewrite the band.
	settled, err := settlePillToxicityEffectTx(conn, userID, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, "alchemy_purge", alchemyPurgeCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}

	// The flame, once the meridians are saturated. It is rolled after the
	// purge has been written, because what it costs is a condition and not the
	// purge: a scorched cultivator still burned off what they burned off.
	if toxicity > pillToxicitySaturated {
		body, berr := canonicalAttribute(conn, catalog, userID, p.GameMinute, "body")
		if berr != nil {
			return authoritativeMutation{}, berr
		}
		will, werr := canonicalAttribute(conn, catalog, userID, p.GameMinute, "will")
		if werr != nil {
			return authoritativeMutation{}, werr
		}
		fireResistance, ferr := canonicalAdditiveEffectBonus(conn, catalog, userID, "", p.GameMinute, "fire_resistance")
		if ferr != nil {
			return authoritativeMutation{}, ferr
		}
		scorch, serr := rollCheck(alchemyScorchModifier(body, will, fireResistance), alchemyScorchTN(toxicity))
		if serr != nil {
			return authoritativeMutation{}, serr
		}
		scorchRoll = scorch
		fireResisted = fireResistance
		if !scorch["success"].(bool) {
			severity := int64(1)
			if i64(scorch["margin"]) <= alchemyScorchSevereBand {
				severity = 2
			}
			scorched, cerr := applyCombatCondition(conn, userID, "meridian_damage", severity, "alchemy", "purge", p.GameMinute)
			if cerr != nil {
				return authoritativeMutation{}, cerr
			}
			scorchedCondition = scorched
		}
	}

	result := map[string]any{
		"qi_cost":        qiCost,
		"qi":             maxI64(0, qi-qiCost),
		"qi_max":         state.Capacity,
		"purged":         purged,
		"pill_toxicity":  settled,
		"before":         toxicity,
		"cooldown_ready": now + float64(alchemyPurgeCooldownSeconds),
		"detox_power":    detox,
	}
	if scorchRoll != nil {
		result["scorch_roll"] = scorchRoll
		result["fire_resistance"] = fireResisted
		result["scorch_tn"] = alchemyScorchTN(toxicity)
	}
	if scorchedCondition != nil {
		result["scorched"] = scorchedCondition
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:     "alchemy",
			EventType:  "pill_toxicity_purged",
			EntityType: "character",
			EntityID:   fmt.Sprint(userID),
			GameMinute: p.GameMinute,
			Payload:    result,
		},
	}, nil
}
