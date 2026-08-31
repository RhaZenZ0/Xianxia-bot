# Xianxia RP v0.19 — Cultivation Depth & Consistency Pass

Release date: 2026-08-31
Schema: 24 (unchanged from v0.18 — every fix in this release is logic-only, no new
tables or columns)
Prior release: see `V018_RELEASE_NOTES.md` and `V018_BUILD_HISTORY.md` for the
full v0.18 authority migration and its post-release additions.

## Verification standard

This release was built without a Go compiler or pytest available in the build
environment — every fix was a traced static read, not compiler-verified, at
the time it was made. **Update:** all Go tests now pass on merged `main`
(commit `1f2a229`) — build clean, every package `ok`. That confirms every Go
change in this release actually compiles and doesn't regress anything the
existing suite covers. It does **not** by itself confirm the *new* behavior
introduced this session is correct — none of it (old-age death enforcement,
the sect-recruitment forged-roll fix, the `family_id` ownership fix, the
tribulation dual-path selector, combat-technique parity) has dedicated test
coverage unless that was added separately as part of the same merge.
`python -m pytest -q` status is still unconfirmed. The one thing that *was*
run directly and repeatedly during this release, independent of the above,
is `dashboard_implementation_issues()` (pure-stdlib Python, no toolchain
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

A later pass in this same release archived the three remaining V017-era docs
(`V017_COMPARISON_AND_MERGE.md`, `V017_RELEASE_NOTES.md`,
`V017_REMAINING_AUTHORITY_GAPS.md`) into `docs/migration_history/` alongside
the existing V015/V016 archive — confirmed unreferenced elsewhere first —
and fixed `README.md`, which had drifted to a mix of "v0.18" section
headings and schema **21**/**22** references despite the code being on
schema **24** and the `VERSION` file already reading 0.19.

## Combat authority migration (this session's follow-on pass)

A further pass over the same "Go owns canonical mechanics, Python owns Discord/presentation" boundary,
targeted at the remaining Python-side mutation of canonical battle state:

- **`combat.technique` was still missing the equipment-durability cost `combat.turn` and boss combat both
  apply.** The counter-attack/injury/fatality parity fix above (Cultivation Depth audit) covered the risk
  side; the durability side was a separate gap in the same function. A technique use could be repeated as a
  durability-free alternative to a normal attack. Added the same `damageEquipmentGo(conn, userID, 1)` call
  `combat.turn` makes, so every offensive action in a battle now costs durability consistently.
- **New `combat.start` Go action, replacing Python's `Database.create_battle`.** 1v1 challenge and event
  battles previously had their opponent HP/realm/stage curve and the player's starting HP snapshot computed
  in Python and inserted directly into `battles`, unlike raid/boss combat which already goes through a Go
  `boss.start` action. `combat.start` mirrors that: it loads the actor's canonical `characters` row itself
  (ignoring any caller-supplied stat fields), computes the opponent curve server-side for both `challenge` and
  `event` kinds, rejects a second active battle against a `target_key` already locked by another user, and
  abandons any stray active battle the same user already had before opening the new one. `/battle challenge`
  and the event "Battle" button in `app/bot/main.py` now call `CombatService.start()` instead of
  `Database.create_battle`. `Database.create_battle` itself was kept (unused by the bot) because
  `tests/python/integration/test_battle.py` still exercises it directly against a local SQLite fixture.
  Covered by 5 new Go tests in `combat_start_test.go`, including one that proves the `event` kind ignores a
  payload's forged `npc_realm_index`/`npc_stage` and derives the opponent from the caller's own character.
- **`combat.recovery_item` was leaving `battles.player_hp` stale.** Mid-battle healing item use already went
  through the Go `combat.recovery_item` action and correctly updated `characters.vitality`, but never wrote
  the matching `battles.player_hp` — the two per-battle HP tracks that every other combat mutation
  (`combat.turn`, `combat.technique`) keeps in lockstep. This meant the battle panel's HP bar stayed stale
  after a mid-fight heal until the next turn recomputed it — a real, pre-existing bug independent of the
  migration below, since the battle-panel item-use flow already called this action. Fixed by writing
  `battles.player_hp`/`player_hp_max` alongside `characters.vitality` whenever the item restores vitality.
  Covered by a new Go test asserting both tracks move together.
- **`/use item`'s mid-battle healing path migrated to `combat.recovery_item`.** The command previously called
  `Database.restore_resources` directly during an active battle, bypassing the authoritative dispatcher (and,
  until the fix above, would have kept desyncing `battles.player_hp` even if it had gone through Go).
  `use_item_command` now detects an active battle and routes instant qi/vitality restores through
  `CombatService.recovery_item()`, falling back to the original `consume_item`/`restore_resources` path
  unchanged when there is no active battle. Item consumption happens exactly once on either path.
- **`boss.act`'s `technique` style was a flat, unconditional +5 accuracy bonus available to every raider**,
  regardless of whether they had actually unlocked the underlying Law technique — unlike `combat.technique`,
  which already gates on Law stage/realm and scales with comprehension. `bossActActionGo` now looks up the
  caller's own `law_progress` row for the chosen technique's Law, rejects the action if the stage/realm
  requirement isn't met, and scales the bonus with comprehension (`2 + comprehension/20`) instead of a flat 5.
  `/boss act` gained a `technique` option (autocompleted from the same `law_technique_autocomplete` the
  `/law technique` command uses) and now requires it when `style=technique`. Shipped without dedicated Go
  test coverage — `go_core/internal/game` has no existing `boss_encounters` fixture to extend cheaply.
- **`pvp.act` surrender granted Martial Society reputation to both sides even when no exchange had happened**,
  letting a challenge → accept → surrender cycle farm reputation for zero real risk. `pvpActAction` now only
  awards the win/honor reputation on surrender when the match's `version` shows at least one real
  attack/defend exchange occurred first (`version` only advances on those), and reports whether reputation was
  awarded in the result payload (`reputation_awarded`). Also shipped without dedicated Go test coverage, for
  the same reason as the `boss.act` fix above.

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
