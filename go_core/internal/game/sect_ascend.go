package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The way up into an allied sect (v1.18.0).
//
// A sect is for life: nothing lets a member leave one, and the recommendation
// and the trial both refuse "already belongs to a public sect". So a
// cultivator who took the sect road at realm 1 - which the beginner path hands
// everybody - carried a Mortal sect through three worlds and could never join
// the six sects above, which are half the sects in the game, nor take their
// members-only commissions, nor found their manors.
//
// `sect.ascend` is the second door. Each public sect names in content the
// allied sect one world above (`ascends_to`); a member standing at that sect's
// gate, at the world's own realm floor, carries a letter from their elders
// and is taken in as an Outer Disciple. The letter is the trial: no roll,
// because the sect above is reading its ally's word, not examining a
// stranger. Standing begins again - rank, contribution and the disciple bond
// are the sect's, not the cultivator's - which is the v1.8.0 rule that rank is
// earned in the sect it is held in. The gate is the catalogue's, never the
// caller's (v1.1.0): the payload carries nothing but the minute.
const sectAscentAttempt = "ascent"

type sectAscendPayload struct {
	GameMinute int64 `json:"game_minute"`
}

// sectAscendsTo is the sect `from` names above it, or "" when it names none.
func sectAscendsTo(catalog worlddata.Catalog, from string) string {
	def, ok := catalog.Sects[from]
	if !ok {
		return ""
	}
	return strings.TrimSpace(def.AscendsTo)
}

func sectAscendActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectAscendPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot carry a letter anywhere")
	}
	mem, err := sectMembershipRow(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("the way up is a member's: you belong to no sect")
	}
	from := strings.TrimSpace(fmt.Sprint(mem["sect_name"]))
	to := sectAscendsTo(catalog, from)
	if to == "" {
		return authoritativeMutation{}, fmt.Errorf("%s names no sect above it", from)
	}
	if _, ok := catalog.Sects[to]; !ok {
		return authoritativeMutation{}, fmt.Errorf("%s names %s above it, which the catalogue does not carry", from, to)
	}
	gate := sectGate(catalog, to)
	if gate == "" {
		return authoritativeMutation{}, fmt.Errorf("%s keeps no gate a letter can be carried to", to)
	}
	if c.Location != gate {
		return authoritativeMutation{}, fmt.Errorf("the way up into %s is taken at %s; you are at %s", to, gate, c.Location)
	}
	// The gate stands in the world above and carries that world's floor; a
	// GM's teleport can put somebody there below it, and the sect reads the
	// floor as its own.
	if floor := catalog.Locations[gate].MinRealmIndex; c.RealmIndex < floor {
		return authoritativeMutation{}, fmt.Errorf("the way up into %s asks for %s; you stand at %s", to, realmNameGo(catalog, floor), realmNameGo(catalog, c.RealmIndex))
	}
	now := nowSeconds()
	rankName, rankLevel := "Outer Disciple", int64(10)
	if _, err = conn.Execute(`UPDATE sect_membership SET sect_name=?,rank_name=?,rank_level=?,joined_at=? WHERE user_id=?`, []any{to, rankName, rankLevel, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	// A new sect is a new count (v1.8.0), and the old sect's points stay in
	// the old sect's hall.
	if sectEarnedColumn(conn) {
		if _, err = conn.Execute(`UPDATE sect_membership SET contribution_earned=0 WHERE user_id=?`, []any{userID}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if ok, e := tableHasColumns(conn, "sect_membership", "contribution_points"); e == nil && ok {
		if _, err = conn.Execute(`UPDATE sect_membership SET contribution_points=0 WHERE user_id=?`, []any{userID}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	// The disciple bond is the old sect's: a master left below, or disciples
	// left behind.
	if tableExistsTx(conn, "sect_lineage") {
		if _, err = conn.Execute(`DELETE FROM sect_lineage WHERE disciple_user_id=? OR master_user_id=?`, []any{userID, userID}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if _, err = conn.Execute(`INSERT OR IGNORE INTO character_sect_discoveries(user_id,sect_name,discovery_kind,source_key,discovered_game_minute,created_at) VALUES(?,?,?,?,?,?)`,
		[]any{userID, to, sectAscentAttempt, "sect:" + from, p.GameMinute, now}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = adjustReputationTx(conn, userID, to, 5, "Taken in on the letter of "+from, now); err != nil {
		return authoritativeMutation{}, err
	}
	details := map[string]any{"from": from, "letter": from + " to " + to}
	if err = recordSectAttemptGo(conn, userID, to, sectAscentAttempt, "", gate, "pass", 0, 0, 0, p.GameMinute, details, now); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"from": from, "to": to, "gate": gate, "rank_name": rankName, "rank_level": rankLevel, "outcome": "pass"}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect.ascend", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
