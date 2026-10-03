package game

import (
	"math"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What tempers the body besides sitting down to train it (v1.20.0).
//
// The body path was the slower ladder for three reasons: its stages cost more
// essence than the qi path's at the same realm, nothing anybody could buy
// sped it up, and every multiplier written for qi - the manor's array, a qi
// storm, a deployed array - is qi-path weather and skips it. The pills are
// content (Bone-Tempering and Iron-Blood, beside their qi twins on the
// apothecary shelves). This file is the other two, both content-tuned
// (`body_tempering` in world.json):
//
//   - **Tempered by use.** A successful hunt, a successful dig and a battle
//     won each add a share of one body session to the stage being filled.
//     The share is of `stagePace`, the session's worth before multipliers, so
//     the same deed is worth the same part of a stage at every realm, and it
//     never fills past what the stage can hold - a breakthrough is still the
//     player's to attempt.
//   - **Tempering grounds.** A hunting ground, a city's forge terraces and
//     garrison ward, and the wild places beyond the roads multiply a body
//     session the way a shrine multiplies a qi one. Read by both cultivation
//     doors, the hand-sat session (`placeMultiplierForPath`) and a body
//     retreat (`seclusionEnvironmentGo`), because v1.2.3 made the ground one
//     rule at both.

// bodyTemperingGround is the body path's ground at a place: the place's name
// and its multiplier, or "" and 1 where the place tempers nothing in particular.
func bodyTemperingGround(catalog worlddata.Catalog, location string) (string, float64) {
	def, ok := catalog.Locations[location]
	if !ok {
		return "", 1
	}
	grounds := catalog.BodyTempering.Grounds
	if mult := grounds.RoadSites[def.RoadSite]; def.RoadSite != "" && mult > 0 && mult != 1 {
		return location, round4(mult)
	}
	if mult := grounds.Districts[def.District]; def.District != "" && mult > 0 && mult != 1 {
		return location, round4(mult)
	}
	if def.WildsOf != "" && grounds.Wilds > 0 && grounds.Wilds != 1 {
		return location, round4(grounds.Wilds)
	}
	return "", 1
}

// temperBodyByUseTx adds a share of one body session to the body stage being
// filled, for the deed named (a key of `body_tempering.by_use`). It answers
// what it added: 0 for a deed the content does not name, and 0 once the stage
// is full. It is called only after the deed has succeeded.
func temperBodyByUseTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, deed string, now float64) (int64, error) {
	share := catalog.BodyTempering.ByUse[deed]
	if share <= 0 {
		return 0, nil
	}
	r, err := conn.Execute(`SELECT body_realm_index,body_phase,body_cultivation FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	row := firstRowMap(r)
	if row == nil {
		return 0, nil
	}
	realm, phase, current := i64(row["body_realm_index"]), i64(row["body_phase"]), i64(row["body_cultivation"])
	cost, err := phaseCost(catalog.BodyRealms, realm, phase)
	if err != nil {
		// A body ladder with no stage here (past the top) tempers nothing.
		return 0, nil
	}
	room := cost - current
	if room <= 0 {
		return 0, nil
	}
	gain := maxI64(1, int64(math.Round(float64(stagePace(cost, realm))*share)))
	if gain > room {
		gain = room
	}
	if _, err := conn.Execute(`UPDATE characters SET body_cultivation=body_cultivation+?,updated_at=? WHERE user_id=?`, []any{gain, now, userID}); err != nil {
		return 0, err
	}
	return gain, nil
}
