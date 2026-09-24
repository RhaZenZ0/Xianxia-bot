package game

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Recording that a player has learned a sect exists - a small player-side
// write that Discord used to make itself (v0.23.0, the v0.21 Authority I
// backlog). It is here because the rule is that gameplay tables are written in
// one place, not because it was going wrong.
//
// `character.set_gender` stood beside it until v1.0.0-rc.15. Sex is chosen at
// creation, where `/begin` requires it before a character exists at all, so a
// second setter afterwards was a door onto a room the player had already
// furnished. The operation went with the command, the way `alchemy.refine`
// went with `/alchemy refine`.

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
func sectDiscoverAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (any, error) {
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
	// What time it is was never the caller's to say (rc.48). The wire field
	// is still accepted from an older bot and ignored (v1.2.3).
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
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
	// Which sects are discovered is the engine's to say (v1.3.1): a sect
	// whose gate stands on a place the cultivator knows. The caller's list is
	// still read - an older bot sends one - but only to narrow, never to
	// widen: a name the gates do not justify is dropped, so a client cannot
	// satisfy the trial's "discovered" check by asserting it.
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	known, err := knownLocationsTx(conn, catalog, userID, c)
	if err != nil {
		return nil, err
	}
	reachable := map[string]string{}
	for name := range catalog.Sects {
		if gate := sectGate(catalog, name); gate != "" && known[gate] {
			reachable[name] = gate
		}
	}
	asked := map[string]bool{}
	for _, item := range rawSects {
		if name := strings.TrimSpace(fmt.Sprint(item)); name != "" {
			asked[name] = true
		}
	}
	wanted := make([]string, 0, len(reachable))
	for name := range reachable {
		if len(asked) == 0 || asked[name] {
			wanted = append(wanted, name)
		}
	}
	sort.Strings(wanted)
	if len(wanted) == 0 {
		return map[string]any{"discovered": []string{}, "already_known": []string{}}, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	discovered := []string{}
	alreadyKnown := []string{}
	for _, name := range wanted {
		rowSource := source
		if rowSource == "" {
			rowSource = reachable[name]
		}
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
