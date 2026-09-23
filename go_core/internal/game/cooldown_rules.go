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

// cultivateWaitMinutes is the pace of the whole game: a session is a share of
// the stage it fills, so this knob is what turns "about a hundred sessions a
// realm" into a calendar. It was 180 from rc.56, which is what Python used to
// send, and is 30 from v1.0.13 on the owner's call.
//
// `seclusionSessionsPerGameDay` divides by it, so a retreat stays the same
// *share* of active play at any value here and its absolute rate follows this
// number - which is the point of deriving it rather than storing it.
const cultivateWaitMinutes = 30

// slowProgressionWaitMinutes is the wait an aptitude evolution and a
// dao-partnered session serve, and until v1.0.13 it was not a number of its
// own: both were **paced with cultivation**, sharing `CULTIVATE_COOLDOWN_MINUTES`
// because Python had sent `max(300, cultivate)` for them since before rc.56
// moved ownership.
//
// Cutting the cultivate wait to thirty minutes is what separated them, on the
// owner's call, and the reason is what each of these two actually is. An
// `aptitude.evolve` is a climb up the six-rung root ladder - 2d10 against
// `13 + idx`, costing stability on a failure and risking a forced mutation at
// margin <= -7 - and since rc.55 the rung reached decides 0.88x-1.34x
// cultivation and -1 to +3 on every breakthrough for the rest of that life. A
// dao partnership is the same shape at two people's expense. Offering either
// every half hour would turn a gated, costly climb into something to grind
// between sessions; ordinary cultivation getting faster is not a reason for
// the rare things to.
//
// They keep 180 and they take **their own environment keys**, which is the
// half that cannot be skipped. A shared key with unshared defaults is worse
// than either: an operator who set the one key would silently move all three
// back together, and no value of it would restore what shipped.
// `test_the_three_statements_of_a_default_agree` refuses that shape by name.
const slowProgressionWaitMinutes = 180

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
	cooldownCultivate:    {cultivateWaitMinutes, "CULTIVATE_COOLDOWN_MINUTES"},
	cooldownExplore:      {20, "EXPLORE_COOLDOWN_MINUTES"},
	cooldownHunt:         {30, "HUNT_COOLDOWN_MINUTES"},
	cooldownSecretRealm:  {15, "SECRET_REALM_COOLDOWN_MINUTES"},
	cooldownPerfectQuest: {60, "PERFECT_QUEST_COOLDOWN_MINUTES"},
	cooldownPerfectTrial: {360, "PERFECT_TRIAL_COOLDOWN_MINUTES"},
	// The two that stopped following cultivation in v1.0.13; see
	// `slowProgressionWaitMinutes` for why, and for why they needed keys of
	// their own the moment they stopped. The five-minute floor Python used to
	// apply sits far below both, so it only ever bit an operator who had
	// shortened them.
	cooldownAptitude: {slowProgressionWaitMinutes, "APTITUDE_COOLDOWN_MINUTES"},
	cooldownDaoDual:  {slowProgressionWaitMinutes, "DAO_DUAL_COOLDOWN_MINUTES"},
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
