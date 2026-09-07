package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
)

// Two small player-side writes that Discord used to make itself (v0.23.0, the
// v0.21 Authority I backlog): setting a character's sex, and recording that a
// player has learned a sect exists.
//
// Neither is dramatic, which is exactly why they lasted this long. They are
// here because the rule is that gameplay tables are written in one place, not
// because either was going wrong.

var characterGenders = map[string]bool{"male": true, "female": true, "neutral": true}

type setGenderPayload struct {
	Gender     string `json:"gender"`
	GameMinute int64  `json:"game_minute"`
}

// setGenderAction changes titles and nothing else - the command has always
// told players so. It is authoritative anyway: it writes the characters table,
// and a player-initiated write to that table gets a receipt like every other,
// so a double-submitted interaction replays rather than racing.
func setGenderAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p setGenderPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	gender := strings.ToLower(strings.TrimSpace(p.Gender))
	if !characterGenders[gender] {
		// Python silently coerced anything unrecognised to "neutral", which
		// turns a typo into a quiet change the player did not ask for.
		return authoritativeMutation{}, fmt.Errorf("unknown gender: %q", p.Gender)
	}
	res, err := conn.Execute(
		`SELECT gender,realm_index,body_realm_index FROM characters WHERE user_id=? AND life_status='alive'`,
		[]any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	row := firstRowMap(res)
	if row == nil {
		return authoritativeMutation{}, errors.New("living character not found")
	}
	before := fmt.Sprint(row["gender"])
	if _, err = conn.Execute(
		`UPDATE characters SET gender=?,updated_at=? WHERE user_id=?`,
		[]any{gender, float64(time.Now().UnixNano()) / 1e9, userID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"user_id":          userID,
		"gender":           gender,
		"previous_gender":  before,
		"realm_index":      storage.ParseInt(row["realm_index"]),
		"body_realm_index": storage.ParseInt(row["body_realm_index"]),
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:     "character",
			EventType:  "gender_set",
			EntityType: "character",
			EntityID:   fmt.Sprint(userID),
			GameMinute: p.GameMinute,
			Payload:    result,
		},
	}, nil
}

// sectDiscoverAction records that a player now knows a sect exists.
//
// It takes a list rather than one name because both callers had one: exploring
// into a location reveals every sect recruiting there, and the sect screen
// reconciles all known locations at once. As separate calls that was a write
// per sect, each its own round trip, with no way to say which of them were
// actually new. It is a plain action rather than an authoritative one on
// purpose: the reconciling caller runs on a read path, every open of the sect
// screen, and an authoritative write there would bump the player's state
// version for learning nothing.
func sectDiscoverAction(conn *storage.Conn, userID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	rawSects, _ := p["sects"].([]any)
	kind := stringField(p, "discovery_kind")
	if kind == "" {
		kind = "rumor"
	}
	if len(kind) > 40 {
		kind = kind[:40]
	}
	source := stringField(p, "source_key")
	if len(source) > 160 {
		source = source[:160]
	}
	// Sects can be learned from different places in the same call: the sect
	// screen reconciles every known recruitment location at once, and each
	// sect's row should record the location that actually revealed it rather
	// than whichever one happened to be passed at the top level.
	perSectSource := map[string]string{}
	if raw, ok := p["source_keys"].(map[string]any); ok {
		for name, value := range raw {
			key := strings.TrimSpace(fmt.Sprint(value))
			if len(key) > 160 {
				key = key[:160]
			}
			perSectSource[strings.TrimSpace(name)] = key
		}
	}
	gameMinute, err := requiredInt(p, "game_minute")
	if err != nil {
		return nil, err
	}
	if gameMinute < 0 {
		gameMinute = 0
	}

	// De-duplicate before touching the database: a caller that names the same
	// sect twice should not depend on INSERT OR IGNORE to sort it out.
	wanted := make([]string, 0, len(rawSects))
	seen := map[string]bool{}
	for _, item := range rawSects {
		name := strings.TrimSpace(fmt.Sprint(item))
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		wanted = append(wanted, name)
	}
	if len(wanted) == 0 {
		return map[string]any{"discovered": []string{}, "already_known": []string{}}, nil
	}

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	if _, err := characterRowTx(conn, userID); err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	discovered := []string{}
	alreadyKnown := []string{}
	for _, name := range wanted {
		rowSource := source
		if specific, ok := perSectSource[name]; ok {
			rowSource = specific
		}
		res, err := conn.Execute(
			`INSERT OR IGNORE INTO character_sect_discoveries(
				user_id,sect_name,discovery_kind,source_key,discovered_game_minute,created_at
			 ) VALUES(?,?,?,?,?,?)`,
			[]any{userID, name, kind, rowSource, gameMinute, now},
		)
		if err != nil {
			return nil, err
		}
		if res.RowsAffected > 0 {
			discovered = append(discovered, name)
		} else {
			alreadyKnown = append(alreadyKnown, name)
		}
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	sort.Strings(discovered)
	sort.Strings(alreadyKnown)
	return map[string]any{"discovered": discovered, "already_known": alreadyKnown}, nil
}
