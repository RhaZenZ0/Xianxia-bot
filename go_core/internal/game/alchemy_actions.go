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
)

type alchemyPurgePayload struct {
	GameMinute int64 `json:"game_minute"`
}

// alchemyPurgeQiCost is `min(12, max(4, current // 8))` from the old command.
func alchemyPurgeQiCost(toxicity int64) int64 {
	return minI64(alchemyPurgeMaxQiCost, maxI64(alchemyPurgeMinQiCost, toxicity/8))
}

// alchemyPurgeAmount is `min(current, 8 + will//2 + spirit//3)`.
func alchemyPurgeAmount(toxicity, will, spirit int64) int64 {
	return minI64(toxicity, 8+will/2+spirit/3)
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
	purged := alchemyPurgeAmount(toxicity, storage.ParseInt(attrs["will"]), storage.ParseInt(attrs["spirit"]))
	if purged < 0 {
		purged = 0
	}
	remainingToxicity := maxI64(0, toxicity-purged)

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
	// under 40 has to delete it, and staying above has to rewrite the band.
	settled, err := settlePillToxicityEffectTx(conn, userID, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, "alchemy_purge", alchemyPurgeCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}

	result := map[string]any{
		"qi_cost":        qiCost,
		"qi":             maxI64(0, qi-qiCost),
		"qi_max":         state.Capacity,
		"purged":         purged,
		"pill_toxicity":  settled,
		"before":         toxicity,
		"cooldown_ready": now + float64(alchemyPurgeCooldownSeconds),
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
