package game

// The Marrow-Tempering Pill (v1.8.0): a permanent rise in max vitality.
//
// Until now max vitality grew only by breaking through on the body path, by
// Perfection and by a secret realm's inheritance - nothing a cultivator could
// make or be issued. The pill is both: an Alchemy method and a sect-issued
// item. Money must not be able to buy an unbounded body, so the marrow takes
// only marrowTemperingPerBodyRealm pills at any one body realm, per life; the
// allowance opens again at the next body realm, so the pill keeps pace with
// the path rather than replacing it.
//
// The record is the event log, per life, exactly as the household lesson's
// is (rc.34): samsara starts a new life and so a fresh allowance, and a reset
// sweeps the rows with everything else. The key is the body realm the pill
// was taken at, never "since the last breakthrough", so a GM lowering and
// restoring somebody's body realm cannot refill it.

import (
	"encoding/json"
	"errors"
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const (
	// marrowTemperingEvent is the event_log type a tempering is recorded under.
	marrowTemperingEvent = "marrow_tempering"
	// marrowTemperingPerBodyRealm is how many pills the marrow takes at one
	// body realm in one life. A rule rather than content, the way
	// characterResetAllowance is: an item cannot author its own ceiling.
	marrowTemperingPerBodyRealm = 3
)

type marrowTemperingRecord struct {
	Life      int64  `json:"life"`
	BodyRealm int64  `json:"body_realm"`
	ItemID    string `json:"item_id"`
	Gain      int64  `json:"gain"`
}

// temperingItem reports whether an item tempers the marrow.
func temperingItem(item worlddata.Item) bool {
	return item.Use.VitalityMaxPercent > 0 || item.Use.VitalityMaxMin > 0
}

// marrowTemperingGain is what one pill adds to a maximum of vmax: the item's
// share, never under its minimum, scaled by the pill's grade.
func marrowTemperingGain(vmax int64, item worlddata.Item, grade float64) int64 {
	base := maxI64(item.Use.VitalityMaxMin, maxI64(0, vmax)*item.Use.VitalityMaxPercent/100)
	return gradedAmount(maxI64(1, base), grade)
}

// marrowTemperingsTx counts the pills this life has taken at bodyRealm. With
// no event log there is no way to keep the bound, so it refuses rather than
// letting the pill through unbounded.
func marrowTemperingsTx(conn *storage.Conn, userID, life, bodyRealm int64) (int64, error) {
	if !tableExistsTx(conn, "event_log") {
		return 0, errors.New("the tempering record is unavailable")
	}
	r, err := conn.Execute(`SELECT payload_json FROM event_log WHERE user_id=? AND event_type=?`, []any{userID, marrowTemperingEvent})
	if err != nil {
		return 0, err
	}
	count := int64(0)
	for _, row := range r.Rows {
		var rec marrowTemperingRecord
		if json.Unmarshal([]byte(fmt.Sprint(row[0])), &rec) != nil {
			continue
		}
		if rec.Life == life && rec.BodyRealm == bodyRealm {
			count++
		}
	}
	return count, nil
}

func recordMarrowTemperingTx(conn *storage.Conn, userID int64, rec marrowTemperingRecord) error {
	encoded, _ := json.Marshal(rec)
	_, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, marrowTemperingEvent, string(encoded), nowSeconds()})
	return err
}

// marrowTemperingPlan is the check made before the pill is spent.
type marrowTemperingPlan struct {
	life, bodyRealm, used, gain int64
	bodyRealmName               string
}

// planMarrowTemperingTx refuses a tempering the marrow will not take - in a
// battle, or once this body realm's allowance is spent - before anything is
// consumed, and otherwise says what the pill will add.
func planMarrowTemperingTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, item worlddata.Item, grade float64) (marrowTemperingPlan, error) {
	fought, err := conn.Execute(`SELECT 1 FROM battles WHERE user_id=? AND status='active' LIMIT 1`, []any{userID})
	if err != nil {
		return marrowTemperingPlan{}, err
	}
	if len(fought.Rows) > 0 {
		return marrowTemperingPlan{}, errors.New("the marrow cannot be tempered in the middle of a battle")
	}
	r, err := conn.Execute(`SELECT vitality_max,body_realm_index FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return marrowTemperingPlan{}, err
	}
	if len(r.Rows) == 0 {
		return marrowTemperingPlan{}, errors.New("character not found")
	}
	vmax, bodyRealm := i64(r.Rows[0][0]), i64(r.Rows[0][1])
	plan := marrowTemperingPlan{life: soulLifeTx(conn, userID), bodyRealm: bodyRealm, bodyRealmName: realmName(catalog.BodyRealms, bodyRealm)}
	if plan.used, err = marrowTemperingsTx(conn, userID, plan.life, bodyRealm); err != nil {
		return marrowTemperingPlan{}, err
	}
	if plan.used >= marrowTemperingPerBodyRealm {
		return marrowTemperingPlan{}, fmt.Errorf("your marrow has taken all it can at %s (%d of %d); temper it again after your next body realm", plan.bodyRealmName, plan.used, marrowTemperingPerBodyRealm)
	}
	plan.gain = marrowTemperingGain(vmax, item, grade)
	return plan, nil
}

// applyMarrowTemperingTx raises the maximum and the current value together
// and writes the record, returning what the reply reports.
func applyMarrowTemperingTx(conn *storage.Conn, userID int64, itemID string, plan marrowTemperingPlan, now float64) (map[string]any, error) {
	if _, err := conn.Execute(`UPDATE characters SET vitality_max=vitality_max+?,vitality=vitality+?,updated_at=? WHERE user_id=?`, []any{plan.gain, plan.gain, now, userID}); err != nil {
		return nil, err
	}
	if err := recordMarrowTemperingTx(conn, userID, marrowTemperingRecord{Life: plan.life, BodyRealm: plan.bodyRealm, ItemID: itemID, Gain: plan.gain}); err != nil {
		return nil, err
	}
	r, err := conn.Execute(`SELECT vitality,vitality_max FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if len(r.Rows) == 0 {
		return nil, errors.New("character not found")
	}
	return map[string]any{
		"vitality_max_gain":   plan.gain,
		"vitality":            i64(r.Rows[0][0]),
		"vitality_max":        i64(r.Rows[0][1]),
		"tempering_used":      plan.used + 1,
		"tempering_allowance": int64(marrowTemperingPerBodyRealm),
		"body_realm_index":    plan.bodyRealm,
		"body_realm_name":     plan.bodyRealmName,
	}, nil
}
