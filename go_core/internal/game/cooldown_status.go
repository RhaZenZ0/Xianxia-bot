package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A cooldown is written in one place and read in another, and until now
// nothing in this repo knew the whole set: `admin.player.reset_cooldowns`
// deletes rows wholesale precisely because there was no list to walk, and a
// player could only discover a wait by trying the action and reading the
// refusal. This file is that list, and cooldown_roster_test.go scans the
// package source to keep it true - a cooldown added later cannot go unnamed.
//
// Three clocks meet here. `cooldowns.available_at` is wall-clock seconds; a
// road journey and closed-door seclusion are game minutes; moderation and the
// Samsara wait have their own wall-clock columns. Everything leaves on one
// axis, because a card that mixes clocks is a card nobody can read.

type cooldownGate int

const (
	gateNone cooldownGate = iota
	gateGhostPath
	gatePerfection
	gateBodyPerfection
)

// cooldownFamily is one family of cooldown rows. A flat family writes exactly
// Key; a composite family writes Prefix followed by the subject it is about.
type cooldownFamily struct {
	Family string
	Key    string
	Prefix string
	Gate   cooldownGate
}

var cooldownFamilies = []cooldownFamily{
	{Family: "cultivate", Key: "cultivate"},
	{Family: "body_cultivate", Key: "body_cultivate"},
	{Family: "explore", Key: "explore"},
	{Family: "hunt", Key: "hunt"},
	{Family: "secret_realm", Key: "secret_realm"},
	{Family: "perfect_quest", Key: "perfect_quest", Gate: gatePerfection},
	{Family: "perfect_trial", Key: "perfect_trial", Gate: gatePerfection},
	{Family: "body_perfect_quest", Key: "body_perfect_quest", Gate: gateBodyPerfection},
	{Family: "body_perfect_trial", Key: "body_perfect_trial", Gate: gateBodyPerfection},
	{Family: "support_vote", Key: "support_vote"},
	{Family: "alchemy_purge", Key: "alchemy_purge"},
	{Family: "alchemy_forage", Key: "alchemy_forage"},
	{Family: "beast_tame", Key: "beast_tame"},
	{Family: "meridian_heal", Key: "meridian_heal"},
	{Family: "qi_refine", Key: "qi_refine"},
	{Family: "ghost_harvest", Key: "ghost_harvest", Gate: gateGhostPath},
	{Family: "ghost_appease", Key: "ghost_appease", Gate: gateGhostPath},
	{Family: "dao_dual_cultivation", Key: "dao_dual_cultivation"},

	// Composite families are listed only while a row exists. There is no
	// "ready now" line for them: the world holds more manuals than a card can
	// show, and a cooldown that was never set is indistinguishable from a
	// subject the cultivator has never touched.
	{Family: "beast_feed", Prefix: "beast_feed:"},
	{Family: "beast_train", Prefix: "beast_train:"},
	{Family: "artifact_bond", Prefix: "artifact_bond:"},
	{Family: "aptitude_temper", Prefix: "aptitude_temper:"},
	{Family: "aptitude_harmonize", Prefix: "aptitude_harmonize:"},
	{Family: "aptitude_awaken", Prefix: "aptitude_awaken:"},
	{Family: "aptitude_evolve", Prefix: "aptitude_evolve:"},
	{Family: "law", Prefix: "law:"},
	{Family: "manual", Prefix: "manual:"},
	{Family: "war_action", Prefix: "war_action:"},
}

// cooldownExternalFamilies are the waits that are not cooldown rows at all.
// The scanner does not check these - nothing calls setCooldown for them - but
// the Python labels are held to this list, so a card cannot draw a wait it has
// no words for.
var cooldownExternalFamilies = []string{
	"road_transit", "seclusion", "sect_trial_retry", "secret_realm_run",
	"reincarnation", "muted", "frozen",
}

// The sect entrance trial may be retried after one world day (sect_actions.go).
const sectTrialRetryGameMinutes int64 = 1440

// optionalRows runs a read against a table a partially-migrated database may
// not carry yet. A cooldown card is a convenience: a missing table means
// "nothing waiting from that quarter", never an error the player has to read.
func optionalRows(conn *storage.Conn, sql string, args []any) (storage.Result, error) {
	res, err := conn.Execute(sql, args)
	if err != nil && strings.Contains(strings.ToLower(err.Error()), "no such table") {
		return storage.Result{}, nil
	}
	return res, err
}

// cooldownSeconds coerces a stored timestamp to seconds. SQLite hands a REAL
// back as float64 and an integral one as int64, and cooldownRemaining already
// has to make the same choice (aptitude_actions.go:222).
func cooldownSeconds(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case int64:
		return float64(typed)
	case nil:
		return 0
	}
	var out float64
	fmt.Sscan(fmt.Sprint(value), &out)
	return out
}

func cooldownRow(key, family, subject string, availableAt float64, now float64, source string) map[string]any {
	remaining := int64(math.Ceil(availableAt - now))
	if remaining < 0 {
		remaining = 0
	}
	return map[string]any{
		"key": key, "family": family, "subject": subject,
		"ready": remaining <= 0, "remaining_seconds": remaining,
		"available_at_unix": int64(availableAt), "source": source, "scheduled": true,
	}
}

// splitCooldownKey names the family a stored key belongs to. An unknown key is
// its own family, which is what makes the roster test load-bearing rather than
// cosmetic: the card still draws it, and the test is what says it should have
// been named.
func splitCooldownKey(key string) (family, subject string) {
	for _, f := range cooldownFamilies {
		if f.Prefix != "" && strings.HasPrefix(key, f.Prefix) {
			return f.Family, strings.TrimPrefix(key, f.Prefix)
		}
		if f.Key != "" && key == f.Key {
			return f.Family, ""
		}
	}
	if at := strings.Index(key, ":"); at > 0 {
		return key[:at], key[at+1:]
	}
	return key, ""
}

func cooldownGateOpen(conn *storage.Conn, catalog worlddata.Catalog, c mechanicsCharacter, userID int64, gate cooldownGate) (bool, error) {
	switch gate {
	case gateNone:
		return true, nil
	case gateGhostPath:
		// The ghost road is defined by content (death_qi_system.path), so the
		// decision belongs here rather than in a Python list of path names.
		return isGhostPath(catalog, c.Path), nil
	case gatePerfection:
		res, err := optionalRows(conn, `SELECT 1 FROM realm_perfection WHERE user_id=? AND active=1 LIMIT 1`, []any{userID})
		if err != nil {
			return false, err
		}
		return len(res.Rows) > 0, nil
	case gateBodyPerfection:
		res, err := optionalRows(conn, `SELECT 1 FROM body_realm_perfection WHERE user_id=? AND active=1 LIMIT 1`, []any{userID})
		if err != nil {
			return false, err
		}
		return len(res.Rows) > 0, nil
	}
	return true, nil
}

// cooldownStatusQuery answers one question - what is this cultivator waiting
// on, and what can they do right now - and writes nothing while doing it.
func cooldownStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	if userID <= 0 {
		return nil, errors.New("actor_id must be positive")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	waits := make([]map[string]any, 0, 16)
	// A family is "waiting" only while its row is still in the future. A spent
	// row is the same thing as no row at all, which is why this is keyed on the
	// remaining time rather than on the row existing.
	waiting := map[string]bool{}

	rows, err := conn.Execute(`SELECT action,available_at FROM cooldowns WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	for _, row := range rows.Rows {
		if len(row) < 2 {
			continue
		}
		key := strings.TrimSpace(fmt.Sprint(row[0]))
		if key == "" {
			continue
		}
		availableAt := cooldownSeconds(row[1])
		if availableAt <= now {
			continue
		}
		family, subject := splitCooldownKey(key)
		waiting[family] = true
		waits = append(waits, cooldownRow(key, family, subject, availableAt, now, "cooldowns"))
	}

	external, err := cooldownExternalWaits(conn, userID, now)
	if err != nil {
		return nil, err
	}
	waits = append(waits, external...)

	ready := make([]map[string]any, 0, len(cooldownFamilies))
	for _, family := range cooldownFamilies {
		// Composite families have no "ready" line: the world holds more manuals
		// than a card can show, and an unset cooldown is indistinguishable from
		// a subject the cultivator has never touched.
		if family.Key == "" || waiting[family.Family] {
			continue
		}
		open, gateErr := cooldownGateOpen(conn, catalog, c, userID, family.Gate)
		if gateErr != nil {
			return nil, gateErr
		}
		if !open {
			continue
		}
		ready = append(ready, map[string]any{
			"key": family.Key, "family": family.Family, "subject": "", "ready": true,
			"remaining_seconds": int64(0), "available_at_unix": int64(0),
			"source": "cooldowns", "scheduled": true,
		})
	}

	sort.SliceStable(waits, func(i, j int) bool {
		return storage.ParseInt(waits[i]["available_at_unix"]) < storage.ParseInt(waits[j]["available_at_unix"])
	})
	sort.SliceStable(ready, func(i, j int) bool {
		return fmt.Sprint(ready[i]["family"]) < fmt.Sprint(ready[j]["family"])
	})
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"now_unix": int64(now), "game_minute": gameMinute,
		"waits": waits, "ready": ready,
	}, nil
}

// cooldownExternalWaits gathers the waits that live outside the cooldowns
// table and puts them on the same axis as the rest. A game-minute wait can
// have no real time at all - the GM can stop the world clock - and that is
// reported as `scheduled: false` rather than as a zero timestamp a card would
// render as 1970.
func cooldownExternalWaits(conn *storage.Conn, userID int64, now float64) ([]map[string]any, error) {
	out := make([]map[string]any, 0, 6)
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	clock, err := readCanonicalWorldClock(conn)
	if err != nil {
		return nil, err
	}
	fromGameMinute := func(key, family, subject string, endsGameMinute int64) map[string]any {
		row := map[string]any{
			"key": key, "family": family, "subject": subject, "ready": false,
			"remaining_game_minutes": endsGameMinute - gameMinute,
			"source":                 "game_clock", "scheduled": true,
		}
		ts, ok := realTimestampForGameMinute(clock, endsGameMinute)
		if !ok {
			// The world clock is stopped, so there is no real moment at which
			// this ends. Say so rather than inventing one.
			row["scheduled"] = false
			row["available_at_unix"] = int64(0)
			row["remaining_seconds"] = int64(0)
			return row
		}
		row["available_at_unix"] = int64(ts)
		remaining := int64(math.Ceil(ts - now))
		if remaining < 0 {
			remaining = 0
		}
		row["remaining_seconds"] = remaining
		return row
	}

	// A road journey in progress.
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{roadTransitStateKey(userID)})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) > 0 && len(res.Rows[0]) > 0 {
		var state roadTransitState
		if json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &state) == nil && state.ArrivalGameMinute > gameMinute {
			out = append(out, fromGameMinute("road_transit", "road_transit", state.Destination, state.ArrivalGameMinute))
		}
	}

	// Closed-door seclusion.
	res, err = optionalRows(conn, `SELECT ends_game_minute FROM seclusion_sessions WHERE user_id=? AND status='active' ORDER BY ends_game_minute DESC LIMIT 1`, []any{userID})
	if err != nil {
		return nil, err
	}
	if row := firstRowMap(res); row != nil {
		if ends := i64(row["ends_game_minute"]); ends > gameMinute {
			out = append(out, fromGameMinute("seclusion", "seclusion", "", ends))
		}
	}

	// The sect entrance trial's retry wait, which is read off the attempt
	// rather than out of the cooldowns table - the one wait a GM can name
	// (`sect_trial`) that has never had a row of its own.
	res, err = optionalRows(conn, `SELECT game_minute FROM sect_recruitment_attempts WHERE user_id=? AND attempt_type='trial' AND result='fail' ORDER BY game_minute DESC LIMIT 1`, []any{userID})
	if err != nil {
		return nil, err
	}
	if row := firstRowMap(res); row != nil {
		if ends := i64(row["game_minute"]) + sectTrialRetryGameMinutes; ends > gameMinute {
			out = append(out, fromGameMinute("sect_trial_retry", "sect_trial_retry", "", ends))
		}
	}

	// A secret-realm run's seal, the reincarnation wait and moderation are all
	// already wall-clock, so they need no conversion.
	res, err = optionalRows(conn, `SELECT expires_at FROM secret_realm_runs WHERE user_id=? ORDER BY expires_at DESC LIMIT 1`, []any{userID})
	if err != nil {
		return nil, err
	}
	if row := firstRowMap(res); row != nil {
		if expires := cooldownSeconds(row["expires_at"]); expires > now {
			out = append(out, cooldownRow("secret_realm_run", "secret_realm_run", "", expires, now, "wall_clock"))
		}
	}
	res, err = optionalRows(conn, `SELECT reincarnation_ready_at FROM reincarnation_state WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if row := firstRowMap(res); row != nil {
		if ready := cooldownSeconds(row["reincarnation_ready_at"]); ready > now {
			out = append(out, cooldownRow("reincarnation", "reincarnation", "", ready, now, "wall_clock"))
		}
	}
	res, err = optionalRows(conn, `SELECT muted_until,frozen_until FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if row := firstRowMap(res); row != nil {
		if until := cooldownSeconds(row["muted_until"]); until > now {
			out = append(out, cooldownRow("muted", "muted", "", until, now, "wall_clock"))
		}
		if until := cooldownSeconds(row["frozen_until"]); until > now {
			out = append(out, cooldownRow("frozen", "frozen", "", until, now, "wall_clock"))
		}
	}
	return out, nil
}
