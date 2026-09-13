package game

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
)

const minutesPerYear int64 = lifespanmodel.MinutesPerYear

const playerLifespanInactivityPauseAfter = 7 * 24 * time.Hour

type LifespanStatus = lifespanmodel.Status

type PlayerLifespanStatus struct {
	LifespanStatus
	AgingPaused                 bool  `json:"aging_paused"`
	PausedGameMinutes           int64 `json:"paused_game_minutes"`
	InactivityPauseAfterSeconds int64 `json:"inactivity_pause_after_seconds"`
}

type playerLifespanClockState struct {
	LastActiveAt         float64 `json:"last_active_at"`
	LastActiveGameMinute int64   `json:"last_active_game_minute"`
	PausedGameMinutes    int64   `json:"paused_game_minutes"`
}

type playerLifespanClockSnapshot struct {
	EffectiveGameMinute int64
	PausedGameMinutes   int64
	AgingPaused         bool
}

func realmLifespanCeiling(realm, phase, natural int64) *int64 {
	return lifespanmodel.RealmCeiling(realm, phase, natural)
}

func lifespanStatus(c CharacterState, currentGameMinute int64) LifespanStatus {
	return lifespanmodel.Evaluate(lifespanmodel.Subject{
		RealmIndex:         c.RealmIndex,
		Phase:              c.Phase,
		BodyRealmIndex:     c.BodyRealmIndex,
		BodyPhase:          c.BodyPhase,
		NaturalYears:       c.NaturalLifespanYears,
		ExtensionYears:     c.LifeExtensionYears,
		BirthGameMinute:    c.CreatedGameMinute,
		AgeAtCreationYears: c.AgeAtCreationYears,
	}, currentGameMinute)
}

func playerLifespanStateKey(userID int64) string {
	return fmt.Sprintf("player_lifespan_clock:%d", userID)
}

func tableExists(conn *storage.Conn, name string) (bool, error) {
	res, err := conn.Execute(
		`SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1`,
		[]any{name},
	)
	if err != nil {
		return false, err
	}
	return len(res.Rows) > 0, nil
}

func storedFloat64(value any) (float64, error) {
	switch v := value.(type) {
	case float64:
		return v, nil
	case int64:
		return float64(v), nil
	case int:
		return float64(v), nil
	default:
		parsed, err := strconv.ParseFloat(fmt.Sprint(v), 64)
		if err != nil {
			return 0, err
		}
		return parsed, nil
	}
}

func loadPlayerLifespanClockState(
	conn *storage.Conn,
	userID int64,
	currentGameMinute int64,
	now time.Time,
) (playerLifespanClockState, bool, error) {
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.UnixNano()) / 1e9,
		LastActiveGameMinute: currentGameMinute,
	}
	hasWorldState, err := tableExists(conn, "world_state")
	if err != nil {
		return state, false, err
	}
	if hasWorldState {
		res, queryErr := conn.Execute(
			`SELECT value_json FROM world_state WHERE key=?`,
			[]any{playerLifespanStateKey(userID)},
		)
		if queryErr != nil {
			return state, false, queryErr
		}
		if len(res.Rows) > 0 {
			if err := json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &state); err != nil {
				return state, false, fmt.Errorf("decode player lifespan activity: %w", err)
			}
			return state, true, nil
		}
	}

	hasReceipts, err := tableExists(conn, "authoritative_action_receipts")
	if err != nil {
		return state, false, err
	}
	hasEvents, err := tableExists(conn, "domain_events")
	if err != nil {
		return state, false, err
	}
	if !hasReceipts || !hasEvents {
		return state, false, nil
	}
	res, err := conn.Execute(
		`SELECT r.created_at,e.game_minute
		 FROM authoritative_action_receipts r
		 JOIN domain_events e ON e.event_uid=r.action_id || ':event'
		 WHERE r.actor_id=?
		 ORDER BY r.created_at DESC,r.action_id DESC
		 LIMIT 1`,
		[]any{userID},
	)
	if err != nil {
		return state, false, err
	}
	if len(res.Rows) > 0 {
		parsed, parseErr := storedFloat64(res.Rows[0][0])
		if parseErr != nil {
			return state, false, fmt.Errorf("decode player lifespan activity timestamp: %w", parseErr)
		}
		state.LastActiveAt = parsed
		state.LastActiveGameMinute = storage.ParseInt(res.Rows[0][1])
		return state, true, nil
	}

	res, err = conn.Execute(
		`SELECT created_at,game_minute
		 FROM domain_events
		 WHERE actor_id=?
		 ORDER BY created_at DESC,event_uid DESC
		 LIMIT 1`,
		[]any{userID},
	)
	if err != nil {
		return state, false, err
	}
	if len(res.Rows) > 0 {
		parsed, parseErr := storedFloat64(res.Rows[0][0])
		if parseErr != nil {
			return state, false, fmt.Errorf("decode player lifespan event timestamp: %w", parseErr)
		}
		state.LastActiveAt = parsed
		state.LastActiveGameMinute = storage.ParseInt(res.Rows[0][1])
		return state, true, nil
	}

	hasCharacters, err := tableExists(conn, "characters")
	if err != nil || !hasCharacters {
		return state, false, err
	}
	hasCharacterActivityColumns, err := tableHasColumns(
		conn,
		"characters",
		"created_game_minute",
		"updated_at",
	)
	if err != nil || !hasCharacterActivityColumns {
		return state, false, err
	}
	res, err = conn.Execute(
		`SELECT updated_at,created_game_minute FROM characters WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return state, false, err
	}
	if len(res.Rows) == 0 {
		return state, false, nil
	}
	parsed, parseErr := storedFloat64(res.Rows[0][0])
	if parseErr != nil {
		return state, false, fmt.Errorf("decode legacy character activity timestamp: %w", parseErr)
	}
	state.LastActiveAt = parsed
	state.LastActiveGameMinute = storage.ParseInt(res.Rows[0][1])
	return state, true, nil
}

func tableHasColumns(conn *storage.Conn, table string, required ...string) (bool, error) {
	res, err := conn.Execute(fmt.Sprintf("PRAGMA table_info(%s)", table), nil)
	if err != nil {
		return false, err
	}
	found := make(map[string]bool, len(res.Rows))
	for _, row := range res.Rows {
		if len(row) > 1 {
			found[fmt.Sprint(row[1])] = true
		}
	}
	for _, column := range required {
		if !found[column] {
			return false, nil
		}
	}
	return true, nil
}

func playerSeclusionOverlapMinutes(
	conn *storage.Conn,
	userID int64,
	fromGameMinute int64,
	toGameMinute int64,
) (int64, error) {
	if toGameMinute <= fromGameMinute {
		return 0, nil
	}
	exists, err := tableExists(conn, "seclusion_sessions")
	if err != nil || !exists {
		return 0, err
	}
	hasIntervalColumns, err := tableHasColumns(
		conn,
		"seclusion_sessions",
		"started_game_minute",
		"ends_game_minute",
	)
	if err != nil {
		return 0, err
	}
	if hasIntervalColumns {
		res, queryErr := conn.Execute(
			`SELECT started_game_minute,ends_game_minute
			 FROM seclusion_sessions
			 WHERE user_id=?
			   AND started_game_minute<?
			   AND ends_game_minute>?
			 LIMIT 1`,
			[]any{userID, toGameMinute, fromGameMinute},
		)
		if queryErr != nil {
			return 0, queryErr
		}
		if len(res.Rows) == 0 {
			return 0, nil
		}
		start := storage.ParseInt(res.Rows[0][0])
		end := storage.ParseInt(res.Rows[0][1])
		if start < fromGameMinute {
			start = fromGameMinute
		}
		if end > toGameMinute {
			end = toGameMinute
		}
		if end <= start {
			return 0, nil
		}
		return end - start, nil
	}

	res, err := conn.Execute(
		`SELECT 1 FROM seclusion_sessions WHERE user_id=? AND status='active' LIMIT 1`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	if len(res.Rows) > 0 {
		return toGameMinute - fromGameMinute, nil
	}
	return 0, nil
}

func resolvePlayerLifespanClock(
	state playerLifespanClockState,
	currentGameMinute int64,
	now time.Time,
	unpausableGameMinutes int64,
) playerLifespanClockSnapshot {
	paused := state.PausedGameMinutes
	if paused < 0 {
		paused = 0
	}
	lastGameMinute := state.LastActiveGameMinute
	if lastGameMinute < 0 {
		lastGameMinute = 0
	}
	elapsedGameMinutes := currentGameMinute - lastGameMinute
	if elapsedGameMinutes < 0 {
		elapsedGameMinutes = 0
	}
	if unpausableGameMinutes < 0 {
		unpausableGameMinutes = 0
	}
	if unpausableGameMinutes > elapsedGameMinutes {
		unpausableGameMinutes = elapsedGameMinutes
	}

	lastActiveAt := time.Unix(0, int64(state.LastActiveAt*1e9))
	inactiveFor := now.Sub(lastActiveAt)
	pausableGameMinutes := elapsedGameMinutes - unpausableGameMinutes
	agingPaused := state.LastActiveAt > 0 &&
		inactiveFor >= playerLifespanInactivityPauseAfter &&
		pausableGameMinutes > 0
	if agingPaused {
		paused += pausableGameMinutes
	}

	effective := currentGameMinute - paused
	if effective < 0 {
		effective = 0
	}
	return playerLifespanClockSnapshot{
		EffectiveGameMinute: effective,
		PausedGameMinutes:   paused,
		AgingPaused:         agingPaused,
	}
}

func playerLifespanClock(
	conn *storage.Conn,
	userID int64,
	currentGameMinute int64,
	now time.Time,
) (playerLifespanClockSnapshot, error) {
	state, _, err := loadPlayerLifespanClockState(conn, userID, currentGameMinute, now)
	if err != nil {
		return playerLifespanClockSnapshot{}, err
	}
	seclusionMinutes, err := playerSeclusionOverlapMinutes(
		conn,
		userID,
		state.LastActiveGameMinute,
		currentGameMinute,
	)
	if err != nil {
		return playerLifespanClockSnapshot{}, err
	}
	return resolvePlayerLifespanClock(state, currentGameMinute, now, seclusionMinutes), nil
}

func refreshPlayerLifespanActivityTx(
	conn *storage.Conn,
	userID int64,
	currentGameMinute int64,
	now time.Time,
) error {
	hasCharacters, err := tableExists(conn, "characters")
	if err != nil || !hasCharacters {
		return err
	}
	res, err := conn.Execute(
		`SELECT life_status FROM characters WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return err
	}
	if len(res.Rows) == 0 || fmt.Sprint(res.Rows[0][0]) != "alive" {
		return nil
	}
	hasWorldState, err := tableExists(conn, "world_state")
	if err != nil || !hasWorldState {
		return err
	}
	snapshot, err := playerLifespanClock(conn, userID, currentGameMinute, now)
	if err != nil {
		return err
	}
	state := playerLifespanClockState{
		LastActiveAt:         float64(now.UnixNano()) / 1e9,
		LastActiveGameMinute: currentGameMinute,
		PausedGameMinutes:    snapshot.PausedGameMinutes,
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		return err
	}
	_, err = conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)
		 ON CONFLICT(key) DO UPDATE SET
		 	value_json=excluded.value_json,
		 	updated_at=excluded.updated_at`,
		[]any{
			playerLifespanStateKey(userID),
			string(encoded),
			float64(now.UnixNano()) / 1e9,
		},
	)
	return err
}

func playerLifespanStatus(
	conn *storage.Conn,
	c CharacterState,
	currentGameMinute int64,
	now time.Time,
) (PlayerLifespanStatus, error) {
	clock, err := playerLifespanClock(conn, c.UserID, currentGameMinute, now)
	if err != nil {
		return PlayerLifespanStatus{}, err
	}
	return PlayerLifespanStatus{
		LifespanStatus:              lifespanStatus(c, clock.EffectiveGameMinute),
		AgingPaused:                 clock.AgingPaused,
		PausedGameMinutes:           clock.PausedGameMinutes,
		InactivityPauseAfterSeconds: int64(playerLifespanInactivityPauseAfter / time.Second),
	}, nil
}

// Defaults mirror REINCARNATION_BASE_SAMSARA_YEARS / REINCARNATION_MAX_WAIT_SECONDS
// in app/config.py, since an automatic old-age death has no caller payload to
// take these from.
const oldAgeDeathBaseSamsaraYears int64 = 320
const oldAgeDeathMaxWaitSeconds int64 = 300

// checkPlayerOldAgeDeathTx evaluates whether a living player character has
// exceeded their (activity-paused) natural lifespan and, if so, routes them
// through the same true-death/samsara pipeline as a player-initiated death.
// It uses the activity-paused effective game minute from playerLifespanClock:
// world time that elapsed while the player was inactive for longer than
// playerLifespanInactivityPauseAfter never counts toward this check, so a
// long-absent character is paused, not silently aged to death while no one
// was there to respond. Returns nil, nil when no death occurs (character
// missing, already deceased, still within lifespan, or missing the
// birth-family record the samsara pipeline requires - that last case is left
// for a player-initiated lifecycle.true_death call to surface properly
// instead of silently blocking whatever unrelated action triggered this
// check).
func checkPlayerOldAgeDeathTx(conn *storage.Conn, userID, currentGameMinute int64, now time.Time) (*authoritativeMutation, error) {
	res, err := conn.Execute(
		`SELECT life_status,realm_index,phase,body_realm_index,body_phase,natural_lifespan_years,
		        life_extension_years,created_game_minute,age_at_creation_years
		   FROM characters WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil || fmt.Sprint(row["life_status"]) != "alive" {
		return nil, nil
	}
	subject := lifespanmodel.Subject{
		RealmIndex:         i64(row["realm_index"]),
		Phase:              i64(row["phase"]),
		BodyRealmIndex:     i64(row["body_realm_index"]),
		BodyPhase:          i64(row["body_phase"]),
		NaturalYears:       i64(row["natural_lifespan_years"]),
		ExtensionYears:     i64(row["life_extension_years"]),
		BirthGameMinute:    i64(row["created_game_minute"]),
		AgeAtCreationYears: i64(row["age_at_creation_years"]),
	}
	clock, err := playerLifespanClock(conn, userID, currentGameMinute, now)
	if err != nil {
		return nil, err
	}
	if !lifespanmodel.OldAgeExpired(subject, clock.EffectiveGameMinute) {
		return nil, nil
	}
	out, err := recordTrueDeathAuthoritative(conn, userID, trueDeathPayload{
		GameMinute:       clock.EffectiveGameMinute,
		Reason:           "old_age",
		MinutesPerYear:   minutesPerYear,
		BaseSamsaraYears: oldAgeDeathBaseSamsaraYears,
		MaxWaitSeconds:   oldAgeDeathMaxWaitSeconds,
	})
	if err != nil {
		if strings.Contains(err.Error(), "birth family") {
			return nil, nil
		}
		return nil, err
	}
	actor := userID
	return &authoritativeMutation{
		Result: out,
		Event: eventledger.Event{
			Domain:     "lifecycle",
			EventType:  "true_death",
			EntityType: "character",
			EntityID:   fmt.Sprint(userID),
			ActorID:    &actor,
			GameMinute: clock.EffectiveGameMinute,
			Payload:    out,
		},
	}, nil
}
