package game

import (
	"os"
	"strconv"
	"strings"
)

// How long a player waits between actions (v1.0.0-rc.56).
//
// Every one of these was the caller's to choose. `cultivation.train` read
// `cooldown_seconds` off the payload and only defaulted it when absent, and
// thirteen other actions did the same - seven of them with no floor at all, so
// a caller sending 0 had no cooldown whatsoever. The bound lived in
// `app/ops/config.py` and was mailed to the engine on every request, which is
// the same fault `rejectCallerGameMinute` exists to refuse: a bound that lives
// in the client is not a bound. The playtest harnesses relied on it, sending
// `cooldown_seconds: 1` to drive a loop - a legitimate use of an illegitimate
// door, and they use `admin.player.reset_cooldowns` now.
//
// The keys are the engine's own, read the way `WORLD_TIME_SCALE` is (v1.0.0-rc.39):
// `.env` is the baseline, compose passes them through, and the defaults here
// are exactly what Python used to send so a live world does not change pace.
//
// Anything already owned by the engine stays where it is - the beast and
// artifact constants, the alchemy purge, the support vote, the forage, and the
// Law's `comprehend_cooldown_minutes`, which is content.

const (
	cooldownCultivate    = "cultivate"
	cooldownExplore      = "explore"
	cooldownHunt         = "hunt"
	cooldownSecretRealm  = "secret_realm"
	cooldownAptitude     = "aptitude"
	cooldownPerfectQuest = "perfection_quest"
	cooldownPerfectTrial = "perfection_trial"
	cooldownForbidden    = "forbidden_manual"
	cooldownQiRefine     = "qi_refine"
	cooldownDaoDual      = "dao_dual_cultivation"
	cooldownGhostHarvest = "ghost_harvest"
	cooldownGhostAppease = "ghost_appease"
)

// cooldownRule is one wait: the minutes it lasts and the environment key an
// operator may retune it with. An empty key is a wait the operator does not
// set - it is the engine's alone.
type cooldownRule struct {
	Minutes int64
	EnvKey  string
}

// actionCooldowns is the one statement of every wait a player serves. A
// reader looking for "how long until I can cultivate again" finds it here and
// nowhere else.
var actionCooldowns = map[string]cooldownRule{
	cooldownCultivate:    {180, "CULTIVATE_COOLDOWN_MINUTES"},
	cooldownExplore:      {20, "EXPLORE_COOLDOWN_MINUTES"},
	cooldownHunt:         {30, "HUNT_COOLDOWN_MINUTES"},
	cooldownSecretRealm:  {15, "SECRET_REALM_COOLDOWN_MINUTES"},
	cooldownPerfectQuest: {60, "PERFECT_QUEST_COOLDOWN_MINUTES"},
	cooldownPerfectTrial: {360, "PERFECT_TRIAL_COOLDOWN_MINUTES"},
	// An aptitude evolution is paced with cultivation and always was: Python
	// sent `max(300, cultivate)`, and the five-minute floor is below the
	// cultivate default, so it only ever bit an operator who had shortened it.
	cooldownAptitude: {180, "CULTIVATE_COOLDOWN_MINUTES"},
	cooldownDaoDual:  {180, "CULTIVATE_COOLDOWN_MINUTES"},
	// These three were hard-coded on the Python side or defaulted in the
	// handler; no operator has ever been able to set them, so they take no key.
	cooldownForbidden:    {45, ""},
	cooldownQiRefine:     {30, ""},
	cooldownGhostHarvest: {15, ""},
	cooldownGhostAppease: {60, ""},
}

// cooldownSecondsFor is how long the wait is, now. An unknown action is an
// hour rather than nothing: a wait this table forgot must not become a free
// action, which is the failure the caller-supplied field already had.
func cooldownSecondsFor(action string) int64 {
	rule, known := actionCooldowns[action]
	if !known {
		return 3600
	}
	minutes := rule.Minutes
	if rule.EnvKey != "" {
		if raw := strings.TrimSpace(os.Getenv(rule.EnvKey)); raw != "" {
			if parsed, err := strconv.ParseInt(raw, 10, 64); err == nil && parsed > 0 && parsed <= 7*24*60 {
				minutes = parsed
			}
		}
	}
	if minutes < 1 {
		minutes = 1
	}
	return minutes * 60
}

// A payload that still carries a wait is refused centrally, beside the
// caller-supplied `game_minute` it is the twin of: see `callerOwnedNothing` in
// authoritative.go. One refusal covers every action rather than fourteen.
