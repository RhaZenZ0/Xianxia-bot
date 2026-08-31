# Xianxia RP v0.19 — Cultivation Depth & Consistency Pass

Release date: 2026-08-31
Schema: 24 (unchanged from v0.18 — every fix in this release is logic-only, no new
tables or columns)
Prior release: see `V018_RELEASE_NOTES.md` and `V018_BUILD_HISTORY.md` for the
full v0.18 authority migration and its post-release additions.

## Verification standard

This release was built without a Go compiler or pytest available in the build
environment. Where something was actually executed, it says so explicitly
below. Everything else is a careful, traced static read — real bugs were found
and fixed this way, but **`go build ./...`, `go test ./...`, and
`python -m pytest -q` still need to be run for real before this ships.** The
one thing that *was* run directly and repeatedly during this release is
`dashboard_implementation_issues()` (pure-stdlib Python, no toolchain
required) — every dashboard change in this release passed that gate live.

## Player old-age death (was display-only, now enforced)

Player lifespan status has shown an activity-paused clock since the v0.18
patch line, but nothing ever actually killed a player for exceeding their
natural lifespan. This release wires real enforcement into the shared
authoritative-action dispatch path:
- Every mutation now checks `lifespanmodel.OldAgeExpired` against the
  paused-aware effective game minute before the requested action runs.
- A positive result routes through the *existing* `recordTrueDeathAuthoritative`
  pipeline (samsara/reincarnation, karma/legacy snapshot) — the same path a
  player-initiated `lifecycle.true_death` uses — instead of a bespoke "you're
  just dead now" state.
- Inactivity pause is preserved: world time elapsed while a player hasn't
  taken an action for longer than the configured grace period never counts
  toward their own aging clock.

## Ancestral Dynasty balance fix

`dynastyConflictAction`'s rounds-based escalation applied to the opposition
side twice at once (once in its base gain, once again in its roll modifier)
with no equivalent scaling for the player — a fight that ran long tilted
against the player on two compounding axes purely from the clock running.
Replaced with a single, shared escalation term applied equally to both sides,
so a long fight still trends toward resolution without structurally favoring
either party.

## Fate system: the grant path had zero callers

`fate.adjust` was fully implemented in Go and completely unreachable from
Discord or the dashboard — no way for anyone to grant fate outside the
automatic combat/mercy/tribulation triggers. Added `admin.player.fate`
(mirroring the existing `admin.player.karma` pattern exactly: own
transaction, bounds-checked delta, full audit trail) and a matching GM
dashboard control. The automatic triggers (`spendFateGo`/`addFateGo` in
combat and tribulations) were already correct and untouched.

## Dashboard coverage gaps closed

Five gaps found via a full operation × Discord × dashboard cross-reference:
household Discord-thread mapping and current household occupants (folded
into the existing Families view), and three new views — Party & Formations,
PvP, and Conditions — each backed by real queries against tables that
already existed but were never surfaced. Caught and fixed a column-collision
bug in the Conditions query before it shipped (`character_conditions.name`
vs `characters.name` silently overwriting each other). The `npc.lifespan`
orphaned query was wired into the existing `/admin npc npcinspect` command
rather than reimplemented in Python, specifically to avoid duplicating the
Go lifespan model in a second language.

## Cultivation Depth audit (this release's main body of work)

A full pass through realm progression, tribulations, spiritual-root/
bloodline/physique aptitude, and technique/dao/manual systems. Findings:

- **Breakthrough perfection bonus never re-scoped per realm.** Completing
  the perfection minigame once, at any single realm, granted +2 to every
  future breakthrough roll for the rest of the game — the gate that blocks
  skipping perfection was correctly scoped per-realm, but the function that
  grants the reward for having perfected wasn't. Fixed to match.
- **Tribulation double fate grant.** Clearing a world-crossing tribulation
  called `addFateGo` twice with identical arguments — a straightforward
  copy-paste duplication, granting +2 fate instead of +1 and writing two
  identical entries into the player's own fate history. Removed the
  duplicate call.
- **Dual-cultivators could get stuck on one tribulation path.** A character
  bottlenecked on both qi and body cultivation simultaneously (each at
  phase 9 of a different world-crossing gate) could only ever prepare/attempt
  the qi-path tribulation — the body path was silently unreachable until qi
  cleared. Added an explicit path selector (server-verified against the
  character's real state, not client-trusted) so both paths are independently
  reachable, plus matching Discord command options.
- **`combat.technique` was free, risk-free damage.** A normal attack via
  `combat.turn` carries a real NPC counter-attack that can cost vitality or
  trigger true death. `combat.technique` had none of that — no counter, no
  cost, no cooldown — making it strictly better than a normal attack once
  unlocked. Brought it to parity with `combat.turn`'s full risk pipeline
  (counter-attack, injury, fatality, fate-rescue-or-death).
- **`/law technique` crashed on every use during battle** — a pre-existing,
  unrelated bug found while fixing the item above: a stale caller passed the
  wrong argument count/types to the rendering function. Fixed the call and
  extended the Discord-side rendering to actually show the counter-attack/
  injury/death outcomes the fix above makes possible, including the
  true-death history side effect other death paths already record.
- Reviewed and found sound, no changes needed: `aptitudeTemper`/
  `aptitudeAwaken`/`aptitudeEvolve` (real RNG, real asymmetric consequences,
  consistent gate/reward scoping), `daoPartnershipActionGo` (correct
  multi-partner prevention, correctly capped dual-cultivation gain, fate
  milestone rewards correctly split between both partners), and the shared
  `progressionProblems` gate.

## Markdown cleanup

32 root-level docs consolidated to 14. Kept the six `V018_STAGE*_NOTES.md`
files (accurate design rationale) and the roadmap/release docs; folded 19
redundant AUDIT/VALIDATION/CHECKPOINT files — most stages had 2-4 files
re-reporting pass/fail on the same work — into `V018_BUILD_HISTORY.md`,
explicit about what was originally claimed vs. independently re-verified.
Fixed two dangling doc references the deletions created.

## Semantic audits run against the wider package

- **RNG audit**: checked every `authoritativeMutation`-returning action for
  a displayed danger/risk/chance value with no roll behind it (the pattern
  that caused the dynasty-quest bug fixed in the prior patch). No further
  instances found across `go_core/internal/game`.
- **IDOR audit**: sampled, not exhaustive — 112 candidate queries found
  (client-suppliable ID, no `user_id` in the same WHERE clause); checked 4
  diverse clusters (caravan, dynasty, pvp, party), all safe via one of two
  consistent patterns (Go-side ownership check after fetch, or legitimately
  public-by-ID resources). Not all 112 were individually verified.
- **Canonical-time audit**: exhaustive, not sampled — all 9 entries in
  `stage5CanonicalTimeNativeOperations` confirmed to resolve their own
  canonical time internally rather than trusting an unpopulated payload
  field.
