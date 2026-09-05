# Xianxia RP v0.19 — Cultivation Depth & Consistency Pass

Shipping as **v0.19.31**. This file covers the whole v0.19 line: v0.19 itself, the v0.19.5
GUI release (the Components V2 hub layout), v0.19.6, which fixes the findings of an
external audit, v0.19.25, a production-review pass covering the late-Discord-ack
architecture, persistent event-scene timeouts, `/explore`'s road-discovery logic, and
live Discord-timestamp travel display, v0.19.26, which adds nine new GM controls
to the web Admin Console (character-sheet editing, NPC/world-state editing, player
moderation, and bulk/server-wide actions), v0.19.27, which adds six more Admin
Console controls for sect membership/rank and character progression detail (realm/
body-realm perfection, spiritual root, bloodline, physique, and tribulation state),
v0.19.28, which adds a database restore command (the Go engine's first ever
online-restore capability, always taking its own safety backup first) and a debuff/
condition-clearing admin control, and v0.19.29, which fixes dashboard write
attribution and a public-channel privacy leak in the `#xianxia-info` guide, wires up
world-time scale from the dashboard, adds a dynasty/samsara unstick control, four
crafting-adjacent admin controls, a mute/freeze moderation system (schema 27), and an
"undo the most recent admin action" control, v0.19.30, split stage 3: `/sect` moves
out of `main.py` into `app/bot/commands/sect.py`, the same way `/family` moved in stage 2
(v0.19.23), and v0.19.31, a hub-UI ephemeral-visibility fix and privacy pass merged in
(with two parts deliberately rejected) from a reviewed community patch — see "Components
V2 hub layout", "Release stamp corrected", "v0.19.6 — external audit findings, fixed",
"v0.19.25", "v0.19.26", "v0.19.27", "v0.19.28", "v0.19.29", "v0.19.30 — split stage 3" and
"v0.19.31 — ephemeral-visibility merge" near the end. The release is stamped 0.19.31 in
`app/version.py`, `VERSION`, the `Dockerfile` and `docker-compose.yml`, and carries
schema 27, unchanged from v0.19.29 (see "v0.19.29" for that migration; v0.19.30 and
v0.19.31 are logic/documentation-only).

Release date: 2026-08-31 (v0.19) / 2026-09-01 (v0.19.5, v0.19.6) / 2026-09-04 (v0.19.25,
v0.19.26, v0.19.27, v0.19.28) / 2026-09-05 (v0.19.29, v0.19.30)
Schema: 27 (unchanged from v0.18 through most of this release — every fix was
logic-only — until the "GM-authored per-channel messages" entry below added schema 25's
`channel_messages` table, the "#bugs forum channel" entry further below added schema
26's `bugs_channel_id` column, and v0.19.29's mute/freeze moderation system added schema
27's three `characters` columns: `is_muted`, `is_frozen`, `moderation_reason`)
Prior release: see `V018_RELEASE_NOTES.md` and `V018_BUILD_HISTORY.md` for the
full v0.18 authority migration and its post-release additions.

## Verification standard

This release was built without a Go compiler or pytest available in the build
environment — every fix was a traced static read, not compiler-verified, at
the time it was made. **Update:** all Go tests were later reported passing on
merged `main` (commit `1f2a229`) — but that check was run directly against
the `go_core` module, not through the actual `go_core/Dockerfile` build. A
real `docker build` (see "Docker build was broken end-to-end" below) turned
up two independent failures the direct `go test` run never hit, both now
fixed and verified by simulating the exact Dockerfile build steps. That
confirms every Go change in this release actually compiles, passes its own
suite, *and* survives the container build that ships it — but still not, by
itself, that the *new* behavior introduced this session is correct beyond
what each test asserts — none of it (old-age death enforcement, the
sect-recruitment forged-roll fix, the `family_id` ownership fix, the
tribulation dual-path selector, combat-technique parity) has dedicated test
coverage unless that was added separately as part of the same merge.
`python -m pytest -q` status is still unconfirmed. The one thing that *was*
run directly and repeatedly during this release, independent of the above,
is `dashboard_implementation_issues()` (pure-stdlib Python, no toolchain
required) — every dashboard change in this release passed that gate live.

## Bot container crashed on every single startup with `KeyError: 'act'`

Also reported live, one step further into the same deployment: `xianxia-game-engine`
and `xianxia-db-init` came up clean, but `xianxia-roleplay-bot` crashed immediately on
every restart attempt with `KeyError: 'act'` in `ActionRegistry.root()`
(`app/bot/registry.py:75`), raised from `register_command_surface()`
(`app/bot/main.py:12260`).

`register_command_surface()` iterated a hardcoded tuple of root-level command names -
`("begin", "me", "quests", "action", "act", "check", "admin")` - and looked each one up
in `ACTIONS.root(name)`. `"act"` was never a root command anywhere in the file: it only
ever appears as a *subcommand* name under five different groups (`/battle act`,
`/boss act`, `/hunter act`, `/war act`, `/duel act`, all registered via
`registered_group_command`, a separate path that never populates `ACTIONS._roots`).
Since nothing had ever bound a root command literally named `act`, this KeyError fired
unconditionally at import time, on every process start - the bot could never have come
up with this code path in place. Fixed by dropping the stray `"act"` entry from the
tuple; the five group subcommands it was presumably confused with are untouched and
still register normally through their own groups.

## Engine container couldn't open its own database on first real deployment

Reported live from an actual QNAP `docker compose up`: `xianxia-game-engine` logged
`open authoritative sqlite: unable to open database file` and never came up.

Root cause: `go_core/Dockerfile`'s final stage runs the engine as a non-root `xianxia`
user (a deliberate hardening choice), while `docker-compose.yml` bind-mounts the host's
`./data` directory to `/data` for the SQLite file. Docker auto-creates that host
directory on first run, owned by root with mode 0755 - a directory the non-root
`xianxia` process has no permission to write into, so `sqlite3_open_v2` fails before it
can even create the database file. `startup.sh`'s own `mkdir -p data` doesn't help,
since it's the *container's* internal UID that needs write access, not the host user
running the script, and that UID isn't something an operator can predict or chown to
ahead of time.

Fixed with the standard pattern for this exact situation: the container now starts as
root (via a new `go_core/docker-entrypoint.sh`), `chown -R`s whatever directory
`DATABASE_PATH` lives in to the `xianxia` user (now pinned to a fixed uid/gid `10001` so
it's reproducible across builds), then drops privileges with `gosu` before exec'ing the
actual engine binary. Root only ever runs that one `mkdir`+`chown` step; the engine
process itself, which is what parses network input, still runs unprivileged exactly as
before. Verified against the real compiled binary (not just reasoned about): reproduced
the reported failure by running it as an unprivileged user against a root-owned
directory, got the identical `unable to open database file` error, then confirmed a
`chown` ahead of the same run lets it start cleanly.

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

## Docker build was broken end-to-end (found via an actual `docker build`)

The "all Go tests now pass on merged main" claim above was not reproducible in a real
container build - `docker build -f go_core/Dockerfile .` failed at the `go test ./...`
step for two independent reasons, both fixed:

- **`go_core/Dockerfile`'s build stage flattened `go_core/` straight into `/src/`**
  (`COPY go_core/cmd ./cmd`, `COPY go_core/internal ./internal`), so every `_test.go`
  file's own path-derived lookup of `content/world.json` (three directories up from
  `go_core/internal/<pkg>/x_test.go`, which lands on the repo root in a normal checkout)
  resolved to `/content/world.json` instead - a path nothing had copied anything to. Every
  test in `internal/game` and `internal/simulation` that touches the world catalog failed
  with `stat /content/world.json: no such file or directory`, and the whole image build
  failed with it. Fixed by preserving `go_core/` as an actual subdirectory in the build
  stage and copying `content/` in alongside it at the same relative depth, so the
  three-levels-up path resolves the same way it does outside Docker.
- **Fixing that surfaced a second, independent failure**: 42 tests in `internal/game`
  failed with `no such column: natural_lifespan_years` (and, after the first round of
  schema fixes, `no such column: body_realm_index`). This session's own old-age-death
  enforcement (`checkPlayerOldAgeDeathTx`, added earlier in this same release) reads
  `natural_lifespan_years`, `life_extension_years`, `created_game_minute`,
  `age_at_creation_years`, `body_realm_index` and `body_phase` off `characters` ahead of
  *every* authoritative mutation - but two of this package's ad-hoc test fixtures
  (`setupBatch4AuthorityDB`, shared by the batch4/batch5/stage4/stage5/stage6/caravan/
  world-event tests, and `setupCharacterCreationAuthorityDB`) predated that check and
  never carried those columns. Added them with defaults matched to what real character
  creation uses (safe against every game-minute value this package's tests exercise).
- **One test assertion was left stale by this release's own "Tribulation double fate
  grant" fix** (see below): `TestBatch4TribulationPrepareAndAttemptPersistCanonicalOutcome`
  still asserted the pre-fix double-grant total (fate=3) after that duplicate
  `addFateGo` call had already been removed from `progression_actions.go`; corrected the
  assertion to the single-grant total (fate=2) the fixed code actually produces.

Verified for real this time: `docker build -f go_core/Dockerfile .` was simulated exactly
(same COPY layout, same `go test ./...` and `go build` invocations) without a Docker
daemon available in the build environment, and both steps now pass clean with no
remaining failures anywhere in `go_core`.

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

## Dashboard "Full Setup"/"Repair" now actually creates missing Discord channels

Reported live: clicking "Full Setup" in the web GM dashboard's Discord Server Setup
panel visibly did nothing when the base/realm-hub channels didn't already exist in the
guild. Tracing it: `ensure_base_xianxia_channels` and `ensure_realm_hub_channels`
(`app/bot/main.py`) only ever *validated and bound* channels that already existed by
name — there was no code path anywhere that called `guild.create_text_channel` or
`guild.create_category`, dashboard or not. That matched a test file's own hard
contract (`test_stage7_bot_never_provisions_server_channels`, since renamed — see
below) but not this project's own documented intent: both `STAGE.md` ("Stage 7:
Discord channel/category setup moved to the admin dashboard") and `README.md`
("channel/category provisioning is admin-dashboard-owned") describe the dashboard as
the thing that *owns* provisioning, not a thing that merely labels channels someone
created by hand first.

This is a deliberate behavior change, not a bug fix — it reverses a previously-tested
"the bot never creates channels, full stop" design decision, on request, in favor of
"the bot can create channels, but only when the web dashboard's Full Setup/Repair asks
for it":

- Both channel-provisioning functions gained a `create_missing: bool = False`
  parameter. When true *and* the bot actually holds Manage Channels in the guild, a
  missing category is created first (`📜 Xianxia RP` / `🌌 Realm Capitals`), then each
  missing channel is created inside it — `world-events`, `bot-logs`, and
  `xianxia-info` get a read-only overwrite (`send_messages=False` for `@everyone`) at
  creation time, matching how those channels are meant to be used; nothing else has
  its permissions touched. Whatever a channel resolves to — pre-existing, matched by
  name, or freshly created — is now unconditionally persisted to `server_config` via
  `DB.set_server_channels`/`DB.set_realm_hub_channel`, closing a gap where base
  channels that were only name-matched (not explicitly bound) were previously never
  saved at all.
- `_run_complete_server_setup` threads `create_missing` through to both functions.
  `dashboard_discord_control`'s `"setup"`/`"repair"` branch — the only thing behind
  the dashboard's Full Setup/Repair button — is the only caller that passes
  `create_missing=True`. The `/admin server setup` Discord slash command calls the
  same helper with no `create_missing` kwarg (defaulting `False`), so it keeps its
  original validate-and-bind-only behavior on purpose: channel *layout* still can't
  drift out from under the dashboard via a Discord-native command, only the dashboard
  can grow it. Without Manage Channels granted to the bot, `create_missing=True` is a
  no-op and the dashboard falls back to today's behavior (list what's missing, ask for
  manual creation) exactly as before.
- The dashboard's own Discord snapshot (`_dashboard_discord_snapshot`'s
  `permission_specs`) had its `manage_channels` description updated to say what it now
  actually does: "Lets the dashboard's Full Setup/Repair create missing channels and
  categories automatically; without it, create them manually in Discord and Full
  Setup/Repair will still bind them by name."
- Two pre-existing tests had the old "never creates channels" behavior baked in as a
  hard contract and needed updating to match the new, intentionally-narrower contract
  ("only the dashboard path can create, and only channels/categories, never
  permissions on channels that already existed"):
  `test_dashboard_owned_setup_does_not_provision_discord_channels` →
  `test_only_the_dashboard_path_can_provision_missing_discord_channels`
  (`tests/python/contracts/test_admin_server_setup_tool.py`), and
  `test_stage7_bot_never_provisions_server_channels` →
  `test_stage7_channel_provisioning_is_gated_behind_dashboard_create_missing` plus
  `test_stage7_dashboard_owned_channels_do_not_rewrite_existing_permissions`
  (`tests/python/unit/test_command_cleanup.py`). All five tests across both files
  pass. `README.md`'s provisioning line was updated to match; `STAGE.md`'s Stage 7
  entry is left as-is since it's a dated log of what was true at that point in the
  project's history, not a living spec.

## `/me` showed the raw internal location key instead of a place name

Reported live: the character sheet's "Location" field showed literally `birth_family:10`
instead of a readable place. `location_display` (the `/me` command, `app/bot/main.py`)
already translated two of the four internal location-key prefixes a character's
`location` column can hold — `abode:` (player property) and `personal_world:` — by
looking them up and substituting a name, but never checked the other two:
`sect_abode:` and `birth_family:`. A character standing in either of those (a sect
abode, or their birth family's shared household) fell through both `if`/`elif`
branches untranslated and displayed the raw DB key verbatim. Fixed by adding both
missing branches, reusing data the function was already fetching (`birth_family` was
already loaded a few lines earlier for a different field) and one new lookup
(`DB.get_sect_abode_by_location`, the same method the private-thread routing logic
already uses for this exact prefix). `admin_inspect`'s raw location line was left
alone on purpose — that's an explicitly-labeled hidden-canonical-state debug command
for admins, not a player-facing display, so showing the literal DB value there is
correct. Two other, lower-traffic spots (`/cultivation seclusion start`/`status`)
print `location`/`start_location` the same unresolved way and could show the same
`birth_family:`/`sect_abode:` leak if a player secludes from one of those two
locations specifically — not fixed here since it wasn't the reported case, flagged for
a follow-up pass if it comes up.

## Full Setup's new channel-creation code crashed immediately: "overwrites parameter expects a dict"

Reported live on the first real run of the previous fix: clicking Full Setup with
Manage Channels granted failed the whole action with `Discord bot control rejected
request: overwrites parameter expects a dict.` Root cause: `ensure_base_xianxia_channels`
built its per-channel `overwrites` argument as `{...} if name in READ_ONLY_BASE_CHANNELS
else None`, then always passed it to `guild.create_text_channel(overwrites=overwrites,
...)`. discord.py's channel-creation call only accepts a dict there (or the parameter
omitted entirely, via its internal `MISSING` sentinel) — passing `None` explicitly fails
that type check before any HTTP request is even made, so *every* base channel creation
failed, not just the three read-only ones. Fixed by defaulting to `{}` (an empty dict,
meaning "no overwrites") instead of `None` for the non-read-only case; behavior is
otherwise unchanged. `ensure_realm_hub_channels`'s own `create_text_channel` call never
passed `overwrites` at all, so it was never affected by this bug.

## Exploring from the birth-family household crashed with a confusing engine error

Reported live: a player's private Expedition Journal showed `Current location:
birth_family:2` (the raw DB key, not a place name), and using `/world → Explore` as the
thread's own instructions suggested failed with `Exploration could not proceed: current
location is not in the world catalog`.

Two independent bugs, both instances of the same gap: `birth_family:` is the fourth of
four internal location-key prefixes a character's `location` column can hold (the other
three are `abode:`, `sect_abode:`, `personal_world:`), added later than the other three
and never fully threaded through every place that already special-cased them:

- **Go, real bug**: `explorationExploreAction`'s private-location guard
  (`go_core/internal/game/exploration_actions.go`) checked for `abode:`, `sect_abode:`,
  and `personal_world:` and returned a friendly "unavailable inside a private residence"
  error for each — but not `birth_family:`, so a character standing in their birth
  family's household fell through to the world-catalog lookup a few lines down, which of
  course fails (`birth_family:2` isn't a catalog location), producing the confusing raw
  error instead of the friendly one. Added the missing prefix. While in there, found and
  fixed the same gap one function over: `explorationTravelAction`'s equivalent guard only
  checked `abode:`/`personal_world:`, silently allowing normal travel to be attempted
  from a sect abode or birth-family household instead of rejecting it with a "leave X
  first" message the way the other two already do. Added both.
- **Python, display bug**: the raw-key-instead-of-place-name issue from the `/me` fix
  earlier this release turned out to be broader than just `/me` — the same
  translate-two-of-four-prefixes gap existed independently in the expedition-journal
  thread header, the `/world` command, and `/sect abode status`. Rather than patch each
  copy separately, pulled the translation logic into one shared
  `character_location_display()` helper (handles all four prefixes) and pointed all four
  call sites at it, including refactoring `/me`'s own inline version from the earlier fix
  to use it too. One more instance (`SceneActionView`'s embed footer, in the `/action`
  panel) lives inside a synchronous `embed()` method that can't call an async DB lookup
  without a wider refactor of its callers - left as a known, lower-traffic follow-up
  rather than done here.
- **Python, routing robustness**: `/action`'s thread-routing fallback only excluded
  `personal_world:` from falling back to the expedition-journal thread if the proper
  private-location thread couldn't be resolved - not the other three prefixes. If
  `active_private_location_thread()` ever fails to resolve/create the right thread for a
  birth-family household or sect abode (a missing DB row, a deleted Discord thread, a
  lost permission - exactly the kind of failure that looks like it happened here, since
  the user ended up in an expedition thread instead of their household thread), the old
  code silently misrouted into an expedition journal instead - which is what put a
  character with a `birth_family:` location in a context where they'd naturally try
  `/world → Explore` and hit the bug above. Broadened the exclusion to all four
  prefixes; the fallback for those cases is now the public (non-thread) panel instead of
  a semantically-wrong expedition journal.

Verified: full `go test ./...` passes (`internal/game` 2.973s, everything else cached
green); all previously-touched Python contract/unit test files still pass; a broader
sweep across every `tests/python/**/test_*.py` module found no new regressions - the
only failures are pre-existing and unrelated (three files hit a `python3.11`-only
parser limitation on an unrelated f-string elsewhere in `main.py` that `python3.12`
parses fine; one file's tests are plain pytest-style functions `python -m unittest`
doesn't collect but all 5 pass when invoked directly; and a stale `VERSION == "0.18"`
assertion left over from before this release, unrelated to this fix).

## The raw location leak also lived in the hub panels' shared status header

Reported live via a screenshot: the World Hub panel (and, it turns out, every other hub
panel - Cultivation, Items, Combat, Travel, all of them) showed `📍 Location:
birth_family:2` in its status sidebar. This is a different, more central code path than
any of the spots fixed above: `_player_hub_status()` builds the five-field status block
(Realm/Vitality/Qi/Items/Location) shown at the top of every hub view in the game, and
its Location field was still printing `character.get('location')` raw - it predates the
`character_location_display()` helper added for the earlier round of fixes and was
missed because it isn't a `/command`-shaped screen, it's the shared chrome every hub
reuses. Pointed it at the same helper. Since this one function backs every hub's status
header, this was probably the single most-seen instance of the whole `birth_family:`
leak, more than any individual command. Verified: `tests/python/unit/test_gui_integrity.py`
(43 tests, hub rendering/limits/structure) and `tests/python/unit/test_command_cleanup.py`
both still pass unchanged.

A broader grep turned up roughly two dozen more `c.get('location')`/`c['location']`
call sites across `main.py`. Most are DB/query keys (teleport-array matching, black
market lookups, territory queries) that correctly need the raw sentinel value, not a
translated display string, so those are fine as-is. A handful of the rest are genuine
display text reachable only from code paths the Go-side fix above already restricts to
non-private locations (exploration/event/hunt flows), so they're safe by construction.
The remainder are lower-traffic display spots (NPC/scene/teleport-array/taming flavor
text) that weren't part of any reported issue and haven't been individually audited -
flagged here rather than swept blind, since distinguishing "needs translating" from
"correctly wants the raw key" requires reading each call site's context.

## Swept the rest of the `birth_family:`/private-location leak

Asked to chase down the remaining display spots flagged (but not yet fixed) in the
previous two rounds. Grepped every `c.get('location')`/`c['location']` site in
`app/bot/main.py` (~40 total) and read each one's surrounding code to sort "genuinely
reachable display bug" from "correctly wants the raw key" (a DB/query lookup, or a
comparison) from "unreachable in practice because an earlier gate already rejects a
private location before this line runs." Fixed everything in the first bucket:

- **`_character_here()`** (event-scene participation check) and the free-form
  roleplay realm-hub-channel mismatch reply in `on_message` - both tell a player their
  current location when it doesn't match what's required; both could show the raw key.
- **Two RAG-memory summaries** (free-form roleplay, and Scene Actions) that narrate
  "At `<location>`, `<player>` acted/said..." into memory later fed back into the AI
  narrator as context - left the actual `location=` field raw (memory lookups
  elsewhere filter by exact match against it) but translated the human-readable
  summary text, since an untranslated one could get echoed back verbatim in future
  narration output.
- **`/cultivation seclusion start` and `status`** - the `status` command needed a small
  variant of the helper call since it resolves a *stored* `start_location` snapshot
  rather than the character's live location.
- **`SceneActionView`'s embed footer** - this one needed a small structural change
  rather than a one-line swap: `embed()` is synchronous and can't itself await a DB
  lookup, so the view now takes a precomputed `location_display` at construction time
  (both of its two call sites build it with the shared helper) and stores it on
  `self` for `embed()` to read.
- **`/sense npc` and `/sense area`**, **`/use` (array deployment)**, **`/world → Scene`
  status**, **`/beast encounters`** (taming opportunities), **`/sect recruit
  recommendation`**'s NPC-presence check, and **`/sect manor establish`**.
- **Go, real gameplay bug found along the way**: `explorationHuntAction` had no
  private-location guard at all - unlike explore and travel (both already fixed
  earlier this release), hunting was never actually blocked from a birth-family
  household or sect abode, so `/world → Hunt` would silently succeed there and then
  display "hunted `<beast>` at birth_family:2." Added the same four-prefix guard
  explore already has ("hunting is unavailable inside a private residence or personal
  world"), matching the design intent the other two actions already follow.

Left alone, confirmed correct as-is: roughly two dozen more sites that use the raw
location as a DB/query key (territory, market, black-market, teleport-array lookups -
translating these would break the lookups, not fix a display bug) or as a pure
comparison (PvP same-location checks, travel-destination gating); `admin_inspect`'s
raw display (intentionally a hidden-canonical-state debug command); every exploration
success-path line that only runs after `exploration.explore` already succeeded, which
is now impossible from a private location thanks to the Go fix above; `/civilization
status`/`npcs` and `/market prices`, which are gated by `_location_is_visible()` /
"no market simulated here" checks that already reject an unregistered location key
like `birth_family:2` before reaching their display lines; and `array_list`, which is
unreachable for the same reason (no teleport array's `from` field will ever equal a
private-location sentinel).

Verified: full `go test ./...` still green (`internal/game` 2.259s) including the new
hunt guard and its existing test coverage; `python3.12 -c ast.parse(...)` clean on
`main.py`; a full sweep across every `tests/python/**/test_*.py` module (using
whichever of python3.11/3.12 actually has `httpx` installed in this sandbox, since
that's a sandbox-only gap and neither interpreter is the "real" one) turned up zero
regressions - the only failure anywhere is the same pre-existing, unrelated stale
`VERSION == "0.18"` assertion noted in the previous round.

## GM dashboard's Teleport (and five other player-targeting actions) always failed with "character not found"

Reported live: clicking **Teleport Player** on the GM web dashboard, after picking a
real player from the dropdown and a valid location, failed every time with
`character not found`.

Root cause: `dashboard/app.js`'s six player-targeting admin actions all wrapped the
selected player's Discord ID in `Number(...)` before sending it to the backend -
`teleportPlayer.onclick=()=>run('player.teleport',{user_id:Number(telePlayer.value),...})`
and the same pattern for `grant_currency`, `karma`, `fate`, `revive`, and
`clear_battle`. Discord snowflake IDs are 17-19 digit integers, well past
JavaScript's `Number.MAX_SAFE_INTEGER` (2^53-1 ≈ 9.007×10^15). Wrapping one in
`Number(...)` silently rounds off the low digits instead of throwing - reproduced
directly: `Number("847706123456789012")` → `847706123456789000`, a different,
nonexistent user. The dashboard then asked the Go engine to teleport that wrong ID,
which correctly reported `character not found` for a user that never existed - every
one of these six buttons was broken the same way, not just Teleport.

Fixed by dropping the `Number(...)` wrapper on all six `user_id:` fields in
`dashboard/app.js`, passing the `<option value="...">` string straight through
unchanged. Traced the full path end-to-end to confirm this is safe: the JS string
survives untouched through `fetch()`'s JSON body, `app/dashboard.py`'s request
handling, and `app/game_engine.py`'s `GameEngineClient.action()` (which forwards the
payload dict unmodified); on the Go side, `actions.go`'s `requiredInt()` calls
`storage.ParseInt()`, which for a JSON string value uses `strconv.ParseInt` and
preserves full precision (unlike its `float64` branch, which would hit the exact same
truncation if the value ever arrived as a JSON number instead of a string).

Verified: reproduced the precision loss directly with
`node -e "..."` (`847706123456789012` → `847706123456789000` after `Number()`,
confirmed non-round-tripping). Wrote a temporary Go test
(`TestSnowflakeStringRoundTrip`) asserting `requiredInt({"user_id": "847706123456789012"}, "user_id")`
returns the exact original value - passed - then deleted it, since it existed only to
prove this specific fix and isn't a permanent regression case. Confirmed no test in
`tests/python/**` references `dashboard/app.js` content at all, so no existing test
needed updating for this change.

## New: GM dashboard "Threads" page — one place to monitor every Discord thread the bot manages

Requested feature: a way to monitor the bot's Discord threads from the GM dashboard.
Previously there was no single place to see them - the Exploration page showed a raw
`expedition_threads` table (bare numeric IDs, no link) and the Families page separately
showed `birth_family_household_threads` the same way; battle threads, personal cave-abode
threads, and sect-abode threads had no dashboard visibility at all, and none of the
existing tables let a GM click straight through to the thread in Discord.

Added a new **Threads** tab that aggregates all six thread-bearing systems into one
sortable, filterable list: Expedition Journals, Birth Family Households, Sect Abodes,
Cave Abodes, World Event Scenes, and Battles. Each row shows its type, owner/context,
a type-specific detail (last known location, family name, NPC being fought, event
title...), status where the underlying table tracks one (a battle's `active`/`finished`,
an event thread's `active`/`closed`), last-activity time, and a direct **Open ↗** link
that jumps straight to the thread in Discord (`https://discord.com/channels/<guild>/<thread>`).
Summary cards up top give an at-a-glance count per thread type, and a type filter narrows
the table to one kind at a time.

Implementation: `ReadOnlyDashboardStore.threads()` (new, in `app/dashboard.py`) queries
each of the six source tables independently (via the existing `_fetchall_if_table` guard,
so a table that doesn't exist yet on an older schema just contributes zero rows instead of
erroring), normalizes them to one common shape, and builds the jump link server-side.
Three of the six tables (`expedition_threads`, `birth_family_household_threads`) already
store their own `guild_id` per row; the other four (`sect_abodes`, `cave_abodes`,
`event_threads`, `battles`) don't, because this bot only ever binds to one Discord guild -
for those, `threads()` falls back to the single row in `server_config`, which is always
that one guild. New read-only route `GET /api/threads`, new nav tab wired through the
existing `loaders` map in `dashboard/app.js`, and the view/endpoint pair was registered in
`app/dashboard_contract.py` (`DASHBOARD_GET_API_PATHS`, `DASHBOARD_VIEW_ENDPOINTS`) so the
standard `dashboard_implementation_issues()` gate keeps covering it going forward. This is
a pure read addition - no new tables, no schema change, no write path - so a rebuild alone
is enough to pick it up.

Verified: `dashboard_implementation_issues(root, schema_version=24)` returned zero issues at the time
(the project has since moved to schema 25; see the "GM-authored per-channel messages" entry below)
(browser ↔ API ↔ view-registry ↔ HTTP-route consistency all check out, same static gate
this release has used throughout). Ran the full `tests/python/integration/test_dashboard.py`
suite (10/10 pass), including `test_new_dashboard_api_routes_return_json`, which now
exercises the live `/api/threads` route end-to-end against a real seeded fixture database.
Beyond that, hand-seeded a temporary database with one row in each of the six source tables
(one deliberately using a 19-digit snowflake ID, to make sure the teleport fix above and
this feature don't reintroduce the same precision bug from opposite ends) and called
`store.threads()` directly: got back all 4 populated kinds with correct per-kind counts,
correct owner names via their joins, and correct jump URLs for both the tables that store
their own `guild_id` and the ones using the `server_config` fallback. Full repo-wide
Python test sweep re-run afterward turned up no new regressions - the only failures are
the same pre-existing, unrelated sandbox gaps already noted earlier in this document
(python3.11 can't parse a PEP 701 f-string elsewhere in `main.py`; the stale
`VERSION == "0.18"` assertion; `test_authority_boundary`'s bare pytest-style functions not
collected by plain `unittest`).

## New: `reset_database.sh` — a safe way to wipe the world and start over

Requested: a script to reset the database. Previously the only way to do this was to stop
the stack and delete `data/xianxia.sqlite3` by hand, which the README explicitly warns
against doing carelessly ("Do not copy a live WAL database file by hand as your primary
backup strategy") and which skips the safety net every other maintenance path in this
project takes for granted.

Added `reset_database.sh` at the project root, matching the existing `startup.sh`/
`stop.sh`/`update.sh` conventions (POSIX `/bin/sh`, `set -eu`, same `fail()`/directory-
resolution style). Running it:

1. Refuses to do anything if there's no database to reset (idempotent).
2. Prints exactly what will be destroyed and requires typing `RESET` to continue
   (or `--yes` for non-interactive use; refuses to proceed non-interactively without it).
3. Takes a safety backup unless `--no-backup` is passed - through the Go engine's
   transaction-safe SQLite backup API when the stack is running (the same method
   `update.sh` and the dashboard's "Create backup" button already use), or a direct file
   copy when the stack is already stopped (safe in that case, since nothing has the file
   open). Backups land in `data/backups/`, which this script never touches otherwise, and
   show up in the dashboard's own Backups list (the Go engine just globs that directory).
4. Stops the stack if it's running, deletes `xianxia.sqlite3` and any `-wal`/`-shm`/
   `-journal` files alongside it.
5. Restarts the stack unless `--no-restart` was passed - `xianxia-db-init` then creates a
   fresh empty schema automatically, exactly as it does on a brand-new install.

Discord itself (channels, threads, roles) is deliberately left untouched - only the game
state is wiped - so the notes point at **Server Setup → Repair** afterward for reconciling
anything that's now unbound.

Verified: `sh -n` clean. Ran the full script end-to-end against a disposable fake project
directory (stub `docker`/`startup.sh`/`stop.sh`) covering every path: no database present
(clean no-op), a real pty-driven interactive run typing the wrong confirmation word
(aborts, exit 1, database untouched), a real pty-driven run typing `RESET` (backs up,
deletes, restarts, exit 0), `--yes` with the simulated stack both running (exercises the
engine-API backup branch and calls `stop.sh`) and stopped (exercises the plain-copy backup
branch and skips `stop.sh`), non-interactive input without `--yes` (correctly refused
rather than silently reading stray stdin as a confirmation), `--no-backup` (skips the
backup step entirely, no `data/backups/` created), and `--help`. Every case produced the
expected file-system state and exit code.

## `reset_database.sh` and a new dashboard button now also clean up Discord

Requested follow-up to the reset script above: both `reset_database.sh` and a dashboard
button should also remove every Discord thread the bot was tracking and post an
announcement in the managed (announcement) channel, since wiping the database alone left
every expedition/household/abode/event/battle thread pointing at characters and events
that no longer existed.

New shared bot-control action `reset_world` (`app/bot/main.py`, `dashboard_discord_control`):
gathers every thread ID the bot has a database row for via a new
`Database.all_managed_thread_ids()` (`app/database/core.py` - unions `expedition_threads`,
`birth_family_household_threads`, `sect_abodes`, `cave_abodes`, `event_threads` and
`battles`), deletes each one from Discord (best-effort - already-deleted or
permission-denied threads are counted, not fatal), then posts a world-reset announcement
in the configured announcement channel. Requires `payload.confirm == "RESET"`, matching
the script's own typed-confirmation gate. This only touches Discord; it does not open or
modify the SQLite file, so it's safe to call from a live process without any database
coordination.

Two callers:
- **`reset_database.sh`**: now calls this action (via `docker compose exec -T xianxia-bot
  wget ...` against the bot's existing `/control/discord` endpoint, the same technique
  already used for the engine backup call) before backing up/stopping/wiping - but only
  when the bot container is actually running, since Discord cleanup needs a live
  discord.py connection. A failed or unreachable call warns and the reset continues
  rather than aborting, since the database wipe and the Discord cleanup are independent
  operations. New `--no-discord-cleanup` flag to skip it outright.
- **Dashboard**: new "💀 Reset World" danger-zone button on the Discord Setup tab
  (`dashboard/app.js`, `loadDiscordSetup`) that calls the same action through the existing
  `/api/discord/action` proxy. This button only performs the Discord half - the dashboard
  has no way to stop/restart the Docker stack itself, so it does not (and, short of adding
  Docker-socket access to the dashboard container, safely cannot) also wipe the database;
  its own text says to run `reset_database.sh` on the server for that, and notes that the
  script already calls this same cleanup automatically.

Verified: `Database.all_managed_thread_ids()` checked directly against a fixture database
seeded with one row in each of the six source tables (character birth-family row included
to satisfy the `birth_family_household_threads` foreign key) - returned all 6 thread IDs
with correct kinds. `dashboard_implementation_issues()` still returns zero issues (no new
routes were added - `reset_world` is a value of the existing `action` field on
`/api/discord/action`, not a new endpoint). Full `tests/python/integration/test_dashboard.py`
suite still 10/10, including the frontend/backend contract and static-admin-console checks
(both exercise `app.js`, which this change edited). `node --check` clean on `app.js`.
`sh -n` clean on `reset_database.sh`. Re-ran the same fake-project/fake-`docker` harness
from the reset script's original verification, extended to also stub the bot's
`/control/discord` response, covering: a successful Discord cleanup (backup, stop, wipe,
restart all still happen afterward), a failed/unreachable Discord cleanup (warns,
continues, does not abort the database reset), `--no-discord-cleanup` (skips cleanly, and
the pre-reset warning text correctly reverts to "Discord channels/threads are not
touched"), a missing `BOT_CONTROL_TOKEN`/`DASHBOARD_TOKEN` in `.env` (warns and skips
rather than crashing), and the bot container not running while the engine container is
(correctly skips Discord cleanup specifically, while still using the live engine for the
safety backup and still stopping the stack). Full repo-wide Python test sweep re-run
afterward: same pre-existing, unrelated sandbox gaps as every other round this release,
no new regressions.

## GM-authored per-channel welcome messages (schema 25)

Requested: "can we set a message for every channel" — a way for the GM to post a
persistent, editable welcome/orientation message in each of the game's shared channels,
rather than the terse one-line topic each channel already has. Ten slots: the six base
channels that carry roleplay or player-facing activity (`#world-events`, `#event-scenes`,
`#player-homes`, `#bot-logs`, `#begin-here`, `#expeditions` - `#xianxia-info` is
deliberately excluded, since it already has its own dedicated interactive guide message)
plus all four realm-capital hub channels (Azure Crown Imperial City, Spirit Jade Capital,
Nine-Heavens Immortal Court, Celestial Mandate Palace). Every slot ships with rich,
game-appropriate default text written for this release, so the feature is immediately
useful without requiring the GM to author anything first; any slot can be rewritten from
the dashboard at any time.

New schema-25 migration `gm_authored_channel_messages` adds one table,
`channel_messages(guild_id, channel_key, content, message_id, updated_at)`, primary-keyed
on `(guild_id, channel_key)`. `Database.get_channel_messages(guild_id)` reads the whole
set back keyed by slot; `Database.set_channel_message(guild_id, channel_key, content=,
message_id=)` upserts one slot.

`app/bot/main.py` adds `CHANNEL_MESSAGE_KEYS` (the ten slot keys - six base-channel keys,
plus `realm:<World Name>` for the four hubs), `DEFAULT_CHANNEL_MESSAGES` (the authored
default text per slot) and `CHANNEL_MESSAGE_LABELS` (display labels for the dashboard) as
three dicts/tuple that are asserted to share exactly the same ten keys.
`_resolve_channel_message_target(guild, channel_key)` resolves a slot to its live
`discord.TextChannel`, the same way for both base-channel and realm-hub keys, reusing the
existing `_base_channel_bindings`/`get_realm_hub_channels`/`_resolve_text_channel` helpers
rather than duplicating that resolution logic. `ensure_channel_message(guild, channel_key,
content)` mirrors the existing `ensure_xianxia_info_guide` pattern exactly: it keeps one
bot-managed message per slot, editing it in place on subsequent saves instead of
duplicating it, and deletes the message outright if the saved content is blank.
`ensure_all_channel_messages(guild)` applies every slot's current (customized-or-default)
content and is now called from Full Setup/Repair, so newly bound channels immediately get
their message with no extra dashboard click. New dashboard-control action
`set_channel_messages` (payload `{"messages": {channel_key: content, ...}}`) lets the GM
save one or many slots at once from the dashboard; it validates every key against
`CHANNEL_MESSAGE_KEYS`, calls `ensure_channel_message` per key, and is audited through the
existing `_audit_dashboard_discord` helper like every other Discord dashboard action.
`_dashboard_discord_snapshot()` now also returns a `channel_messages` list (key, label,
current effective content, whether that content is still the shipped default, the bound
message/channel id, and whether the channel itself is currently bound) so the dashboard can
render pre-filled, ready-to-edit boxes.

`dashboard/app.js`'s Discord Setup tab (`loadDiscordSetup`) gained a new "Channel Messages"
section between "Use Existing Channels" and the "Reset World" danger zone: one textarea per
slot, pre-filled from `channel_messages`, flagged `(default)` when unedited and `(channel
not bound)` when the underlying channel isn't configured yet. "Save Channel Messages" posts
every box's current text through the new `set_channel_messages` action in one call; clearing
a box and saving removes that slot's message. No new API route or nav tab was needed - this
reuses the existing `/api/discord` GET snapshot and `/api/discord/action` POST dispatcher
that every other Discord Setup control already goes through.

Verified: bumped `SCHEMA_VERSION` 24 → 25 and `DASHBOARD_REVIEWED_SCHEMA_VERSION` to match,
propagating the version across ~13 test files, `README.md`, `RELEASE.txt` and this file (a
repo-wide grep pass confirmed no other genuine schema-24 references remained - the handful
of other "24" matches found were unrelated history-length/slot-capacity constants).
`dashboard_implementation_issues()` returns zero issues. Hand-seeded a fixture database and
exercised `get_channel_messages`/`set_channel_message` directly: confirmed empty-by-default,
insert, upsert-overwrites-in-place, a `realm:` key with a colon and spaces round-trips
correctly, and results are scoped per `guild_id`. Full `tests/python/integration/
test_dashboard.py` suite still 10/10. `node --check` clean on `app.js`. An AST-level check
confirmed `CHANNEL_MESSAGE_KEYS`, `CHANNEL_MESSAGE_LABELS` and `DEFAULT_CHANNEL_MESSAGES`
all define exactly the same ten keys (import-testing `app/bot/main.py` directly isn't
possible in this sandbox - it needs `discord.py`, and its `httpx` dependency chain is only
installed under this environment's `python3.11`, which itself can't parse an unrelated
pre-existing PEP 701 f-string elsewhere in the same file - so the key-consistency check was
done statically against the parsed AST instead). Full repo-wide Python test sweep re-run
afterward, including two tests this migration itself required updating - `test_sect_manor`'s
hardcoded `current_version` assertion and `test_alchemy_beast_expansion`'s migration-replay
fixture, which deleted `schema_migrations` rows only up to version 24 and needed version 25
added to that list - plus `test_startup_health`'s "latest migration name" assertion, updated
from `ancestral_sites_dynasty_claims_conflicts` to `gm_authored_channel_messages`. No other
regressions; same pre-existing, unrelated sandbox gaps as every other round this release.

## New: #bugs forum channel for player bug reports (schema 26)

Requested: "Make a bugs channel with the forum option and make text info for the user. Make
it so we can get info from it" — a dedicated Discord forum channel where players report
bugs, with guidance text shown before they post, and a way for the GM to actually see what's
been reported without leaving the dashboard.

A forum channel is a different Discord channel type from every other channel this bot
manages (`discord.ForumChannel`, not `discord.TextChannel`) - it has no `send()` of its
own; "posting" means creating a new forum thread, and each thread is one bug report. That
ruled out folding this into the base-channel or channel-messages machinery built earlier in
this release (both assume a plain text channel), so it's its own small subsystem instead,
built to the same standard: dashboard-owned creation, GM-editable text, live status.

New schema-26 migration `bugs_forum_channel` adds `server_config.bugs_channel_id`, plus
`Database.set_bugs_channel_id(guild_id, channel_id)`. `app/bot/main.py` adds
`ensure_bugs_forum_channel(guild, category_name=, create_missing=)`, which follows the exact
same resolve-by-id → resolve-by-name → create-if-missing shape as
`ensure_base_xianxia_channels`/`ensure_realm_hub_channels`, but calls `guild.create_forum(...)`
with five starter tags (Open, Investigating, Fixed, Can't Reproduce, Duplicate) instead of
`guild.create_text_channel`, and is now called from `_run_complete_server_setup` alongside
the base/realm-hub provisioning, so Full Setup and Repair create/validate it automatically.
It also keeps the forum's guidelines text (what Discord shows a player before they start a
new post - the forum's `topic` field, same 1024-char field text channels use for their
one-line topic) in sync with whatever's saved in the database, defaulting to a written-out
`DEFAULT_BUGS_GUIDELINES` ("what you did / what you expected / what actually happened /
your character name") the first time, and re-applying the saved custom text (not the
hardcoded default) on every later Repair - reusing the `channel_messages` table from the
per-channel-messages feature above under one more key, `bugs-guidelines`, since that table
was already exactly "one editable piece of text per key, GM-customizable" and a second table
for one row would have been pure duplication.

"Get info from it": bug reports are threads players create themselves, so there was nothing
to track in SQLite the way bot-created threads are (expedition journals, sect abodes, etc.) -
`bugs_forum_reports(guild, channel)` reads them live from Discord instead, combining the
forum's cached active threads with a page of archived ones, and returning each report's
title, reporter, tags, open/archived status, message count, created time and a jump link.
`_dashboard_discord_snapshot()` now includes a `bugs` block (channel status, current
guidelines text, whether it's still the default, open-report count, and up to 20 recent
reports) so the dashboard can render it without a new API route - same `/api/discord`
GET/`/api/discord/action` POST pair every other Discord Setup control already uses. Two new
dashboard-control actions: `set_bugs_guidelines` (save + immediately re-apply new guidelines
text to the live channel) and `bugs_reports` (an on-demand re-read, for a lighter refresh
than a full status reload).

`dashboard/app.js`'s Discord Setup tab gained a "Bug Reports" metric card (open-report count,
or "NOT SET UP" before first Setup/Repair) and a new "Bug Reports" section: available tags,
an editable guidelines textarea with a save button, and a table of recent reports (each
title links straight to the Discord thread) with reporter, tags, status, message count and
open date.

Verified: bumped `SCHEMA_VERSION` 25 → 26 and `DASHBOARD_REVIEWED_SCHEMA_VERSION` to match,
propagating across the same ~13 test files (checked each hit by hand again rather than
blind-sedding, per the near-miss two entries up) plus `README.md`/`RELEASE.txt`/this file.
While re-running the full migration-replay test after this bump, found and fixed a second,
recurring instance of the same class of bug the schema-25 pass hit:
`test_alchemy_beast_expansion`'s replay fixture deleted `schema_migrations` rows only up to a
hardcoded version number, so it broke again the moment a new migration existed above that
number. Rather than bump the hardcoded list a third time, changed it to
`DELETE FROM schema_migrations WHERE version>=5` - a fix that can't go stale again the next
time a migration is added. `dashboard_implementation_issues()` returns zero issues. Hand-
seeded a fixture database and exercised `set_bugs_channel_id`/`get_server_config` and the
reused `channel_messages` table's `bugs-guidelines` key directly: round-trips correctly and
independently of each other. Found and fixed two call sites of `_run_complete_server_setup`
that still unpacked its old two-value return after this change made it three
(`dashboard_discord_control`'s setup/repair branch, and the `/admin setup` slash command's
own setup/repair path) - both now capture and surface the new bugs-channel warning the same
way they already surface base-channel warnings. `node --check` clean on `app.js`. Full
`tests/python/integration/test_dashboard.py` suite still 10/10. Full repo-wide Python test
sweep re-run afterward - same pre-existing, unrelated sandbox gaps as every other round this
release, no new regressions. `discord.py` itself isn't installable in this sandbox (no
package-index access), so `ensure_bugs_forum_channel`/`bugs_forum_reports`/the dashboard
snapshot's `bugs` block couldn't be exercised against a live or mocked Discord connection -
same limitation noted for the channel-messages feature above, addressed the same way
(AST-level static checks plus full-file `ast.parse` syntax verification) since that's what
this sandbox allows; the actual `discord.py` API calls used here (`guild.create_forum`,
`discord.ForumTag`, `ForumChannel.threads`/`.archived_threads()`, `Thread.applied_tags`) all
match documented `discord.py` 2.7.1 (the version this project pins) behavior.

## New: dashboard "Fresh Start" button — wipe channel messages without touching the database

Requested: "delete all message in the server at repair server for fresh look." Two design
questions needed the user's own call before building anything destructive and irreversible:
whether to wipe every channel in the guild or only the ones this bot manages, and whether to
wire deletion into Repair Server itself (which the dashboard's own copy calls "Safe and
idempotent" and is meant to be clickable anytime) or keep it a separate, deliberately
triggered action. Asked both; answered "All Xianxia-managed channels" and "New separate
danger-zone button" — matching the recommended option on both, and matching how `reset_world`
already handles the other existing irreversible action in this file.

While scoping "all Xianxia-managed channels," found a landmine worth calling out rather than
silently working around: three of the seven base channels aren't just message containers —
`#player-homes`, `#expeditions` and `#event-scenes` are *anchors* for players' live private
threads (cave/sect abodes, expedition journals, world-event scenes; see
`Database.all_managed_thread_ids`). Discord has no way to delete a channel's messages without
deleting the channel (bulk-delete only works on messages under 14 days old — anything older
needs deleting one by one, which is slow and heavily rate-limited across a whole server), and
deleting a channel deletes every thread under it too. For those three, that would silently
orphan the database rows still pointing at those thread ids — turning a cosmetic "fresh look"
into real game-state corruption the user never asked for. So the new action fully wipes
`#world-events`, `#bot-logs`, `#begin-here`, `#xianxia-info`, all four realm-capital hubs and
`#bugs` (delete + recreate — instant and complete regardless of message age, unlike a purge),
and deliberately leaves the three thread-anchor channels untouched. This is disclosed
up-front in the button's own warning text and in the confirmation dialog, not just here.

`app/bot/main.py` adds `CHANNEL_WIPE_KEYS` (the four wipeable base-channel keys) and
`clear_managed_channel_messages(guild)`, which deletes each wipeable base channel, every
realm hub, and the bugs forum, then calls the existing `_run_complete_server_setup(...,
create_missing=True)` to recreate everything from scratch exactly as Full Setup/Repair
would. No new bookkeeping was needed for the stale channel ids left behind by the deletes:
`ensure_base_xianxia_channels`/`ensure_realm_hub_channels`/`ensure_bugs_forum_channel` already
fall back to resolve-by-name when a stored id no longer resolves, and `xianxia-info`'s guide
message and every GM-authored channel message get reposted fresh afterward via the same
`ensure_xianxia_info_guide`/`ensure_all_channel_messages` calls Full Setup/Repair uses — all
of that machinery was already correct by construction from the earlier features in this
release; this action only had to sequence delete-then-recreate around it. `xianxia-info`'s
read-only permission overwrite is reapplied automatically on recreate (it's baked into
`ensure_base_xianxia_channels`'s creation path via `READ_ONLY_BASE_CHANNELS`); any *other*,
GM-added-by-hand permission overwrite on a wiped channel is not preserved, and recreated
channels land at the bottom of their category rather than their old position — both called
out in the button's warning text. New dashboard-control action `fresh_start`, gated on
`payload.confirm == "CLEAR"` (matching `reset_world`'s `"RESET"` gate), audited through the
existing `_audit_dashboard_discord` helper.

`dashboard/app.js` adds a "🧹 Fresh Start" danger-zone button next to "💀 Reset World" in the
Discord Setup tab, with its own explicit warning text (what gets wiped, what's protected and
why, the permission/position caveat) and its own confirmation dialog — entirely independent
of Repair Server, which is unchanged and still safe to click anytime.

Verified: no schema change was needed (the action only touches channel bindings that already
existed). `dashboard_implementation_issues()` returns zero issues (reuses the existing
`/api/discord` GET/`/api/discord/action` POST pair, no new route). An AST-level check
confirmed `CHANNEL_WIPE_KEYS` is a subset of `BASE_CHANNEL_SPECS`'s keys and disjoint from the
three protected anchor channels — i.e. it's structurally impossible for this action to
include `player-homes`/`expeditions`/`event-scenes` without someone deliberately editing the
constant. Full `tests/python/integration/test_dashboard.py` suite still 10/10. `node --check`
clean on `app.js`. Full repo-wide Python test sweep re-run afterward — same pre-existing,
unrelated sandbox gaps as every other round this release, no new regressions. As with the two
Discord-integration features above, `discord.py` isn't installable in this sandbox, so
`clear_managed_channel_messages` itself couldn't be exercised end-to-end against a live
connection; verification here leaned on the same static checks plus the fact that every
Discord call it makes (`channel.delete()`, then the already-tested `_run_complete_server_setup`/
`ensure_xianxia_info_guide`/`ensure_all_channel_messages`) is either a single documented
`discord.py` method or code this release already verified in the two features above.

## Fixed: every new character was silently routed into an exploration dead-end

Reported with two Discord screenshots: a freshly-created character's private thread showed
"Current location: Qin Clan Household," the bot's own onboarding text said to use `/world →
Explore`, and doing exactly that failed with "Exploration could not proceed: world exploration
is unavailable inside a private residence or personal world" — with nothing in the UI
explaining what to do about it. A second screenshot showed the World Hub panel stuck the same
way, and the report added that the character's household thread "did not spawn until i used
action."

Traced both to the same root mismatch. `character.create` in the Go engine (`createCharacterAuthoritative`,
`go_core/internal/game/authoritative.go`) has always set a brand-new character's `location`
column straight to `birth_family:<id>` — every character starts *inside* their birth-family
household by design, not out in the world. But the Discord-side onboarding flow after `/begin`
(the character-creation modal's `on_submit` in `app/bot/main.py`) didn't know that: it
unconditionally called `ensure_expedition_thread`, creating a "Private Expedition Journal"
thread and telling the player to use `/world → Explore` or `/action` next — both of which the
Go engine has always rejected from inside a household (`exploration_actions.go`'s explore/hunt
guards, unchanged, gate on the `birth_family:`/`abode:`/`sect_abode:`/`personal_world:`
location prefixes). Every single new character hit this since day one; the only way out,
`/family leave`, was never mentioned anywhere in the onboarding text or the error message
itself. The "thread didn't spawn until /action" half of the report was the same bug from a
different angle: the correct thread for a household-bound character
(`ensure_birth_family_household_thread`) already existed and was already wired up — but only
`/family enter` and `/action` (via `active_private_location_thread`) ever called it, never
character creation, so it silently stayed uncreated until the player happened to trigger one of
those two paths.

Two fixes, both in `app/bot/main.py`. First, the character-creation `on_submit` now checks the
new character's actual starting location: when it's inside the birth household (which it
always is today, but this is written as a runtime check, not a hardcoded assumption, in case
that ever changes), it calls `ensure_birth_family_household_thread` instead of
`ensure_expedition_thread`, and the follow-up message says so correctly ("You start here with
your family... `/family → Leave` when you're ready to step out... then `/world → Explore`")
instead of pointing at an action that's guaranteed to fail. The character-creation embed's
"Next steps" field got the same correction. Falls back to the expedition thread exactly as
before for the (currently theoretical) case of a character who doesn't start in a household.
Second, new helper `_explain_engine_error(exc)` appends an actionable hint - which private-
location "leave" command to use - onto that one specific Go engine error message wherever it's
surfaced (`/explore` and `/hunt`'s `except GameEngineError` handlers). Since the World Hub
panel's "Explore" button runs through the exact same `explore()` command function (the hub
system in `app/bot/hubs.py` calls registered command callbacks directly via
`action.handler(proxy, **supplied)`, confirmed by reading `_invoke_action`), this one change
fixes the error message in both the plain slash command and the interactive panel without
touching `hubs.py` at all. A player who's already stuck the same way the report showed (an
existing character sitting in `birth_family:<id>`) can already run `/family leave` right now to
get out — that command already existed and already worked, it just was never surfaced to them.

Verified: `python3.12 -c "import ast; ast.parse(...)"` clean on `app/bot/main.py`.
`dashboard_implementation_issues()` still returns zero issues (no dashboard/schema surface
touched by this fix). Extracted `_explain_engine_error` from the parsed AST and executed it
standalone (no `discord.py` needed for a pure string function) against the exact two error
strings the Go engine actually returns for explore and hunt — confirmed the hint is appended
correctly to both and that an unrelated error (a cooldown message) passes through byte-for-byte
unchanged. Traced the character-creation branch by hand against
`ensure_birth_family_household_thread`'s and `ensure_expedition_thread`'s real signatures and
`DB.get_birth_family`'s existing usage elsewhere in the file to confirm the call shapes match.
Full repo-wide Python test sweep re-run afterward — same pre-existing, unrelated sandbox gaps
as every other round this release, no new regressions. As with every other Discord-behavior
change this release, the actual Discord-side flow (posting into the newly-created household
thread, the World Hub panel round-trip) couldn't be exercised end-to-end in this sandbox
(`discord.py` isn't installable here) — verification leaned on the AST-extracted unit check
above plus reusing `ensure_birth_family_household_thread`, which this release didn't write and
which was already exercised by the existing `/family enter` command path.

## New: Components V2 hub layout, piloted on `/character`

The interactive hub panel put every action behind two dropdowns. A player opening
`/character` saw a card and two closed menus: pick a system from the first, pick an
action from the second. Nothing about a system was visible until a menu was open,
nothing could be reached in fewer than two interactions, and only the first three
actions on a page got real buttons. Worse, running any action *overwrote the card* —
`_HubResponseProxy` wrote the result into the hub message as `content`, so the panel
you were using was replaced by result text with orphaned components hanging under it.

`/character` now renders with Discord's Components V2 layout instead. Each action is
its own Section — emoji, label, description, and its own button on the right — so the
actions on a system are visible without opening anything and reachable in one tap. The
action dropdown is gone entirely; one dropdown survives, for jumping between the 18
systems, alongside prev/next system buttons and Refresh. Danger and success colouring
carries over from the old quick-action buttons, so `Leave` still reads red and `Join`
still reads green.

The panel is also no longer destroyed by using it. A Components V2 message may not
carry `content` or `embeds` at all, and the flag cannot be edited away, so the classic
"write the result over the card" path is impossible there — which turned out to be the
better behaviour. `_layout_targets_panel` detects when an interaction's original
response *is* a V2 panel and routes that output to a followup message instead, leaving
the card on screen, live and reusable, underneath the result. The distinction is
precise rather than blanket: component interactions raised by an input-step message (a
choice or member picker the hub opened) are not the panel, so those still edit
themselves in place exactly as before, and modal submits resolve against their own
deferred response.

Rollout is deliberately one hub. `LAYOUT_HUB_NAMES` in `app/bot/hubs.py` is the entire
switch — it currently holds `{"character"}`, and adding a name migrates that hub. The
other 15 player hubs and the admin panel are untouched and still render the classic
embed panel, whose code path is unchanged.

Three independent safety nets sit under this, because `discord.py` cannot be installed
in the build sandbox and the rendered result could not be checked against real Discord.
First, `LAYOUT_COMPONENTS_AVAILABLE` feature-detects `LayoutView`/`Container`/`Section`
/`TextDisplay`/`Separator`/`ActionRow` at import; on a discord.py older than 2.6 those
names are simply absent and every hub keeps the classic panel rather than raising at
import time. Second, `send_hub` builds *and sends* the layout inside a try/except, so
any disagreement between discord.py and this code about Components V2 logs the
exception and falls back to the classic panel instead of failing the player's command.
Third, the component budget is deliberately under-filled: Discord counts all 40
components including nested ones, and `_LAYOUT_ACTION_LIMIT` is 7 rather than the 8
that would also fit, so the busiest page in the game lands at 35/40 with headroom.
Pages with more actions than fit step through in chunks via two extra control buttons.

Verification: syntax-parsed the whole file, then built a stand-in `discord` module
reproducing the Components V2 surface (Container/Section/ActionRow/TextDisplay with
their real nesting and 1-3-children, 5-per-row and 25-option constraints asserted) and
executed `LayoutHubView.rebuild()` against it for real. All 18 `/character` systems
render inside limits, worst case 27/40 components and 680/4000 characters. The worst
page in the entire game — `/sect`, 25 leaf actions across three subgroups, which is not
in the pilot but will be if this rolls out — renders at 35/40 across four chunks, and
the chunk paging was verified to reach all 25 actions and wrap. The expired-panel
render and the full `_layout_targets_panel` truth table (panel button, input-step
button, modal submit, classic hub, unknown panel id) were asserted the same way. The
repo's own 18 GUI-integrity tests still pass, and the full 49-module Python suite was
run against both a pristine extract of the previous build and this one: 18 pass / 31
fail on both, byte-identical results, so the pre-existing sandbox import gaps are
unchanged and nothing regressed. What could not be verified here is how Discord itself
renders the layout — that is what piloting a single hub is for.

## Release stamp corrected (v0.19.5)

Setting the GUI release exposed that the version stamp had already drifted apart. `VERSION`
read `0.19`, while `app/version.py`, the `Dockerfile` label and both `xianxia.release` labels
in `docker-compose.yml` all still read `0.18` — and `app/version.py` is the one that actually
reaches people, since `RELEASE_VERSION` feeds the startup log, the `/health` snapshot, the
`xianxia_build_info` Prometheus metric, the admin panel's release line and the in-game realm
guide header. A v0.19 deployment was reporting itself as 0.18 in all five places.

The reason it went unnoticed is worth recording: `test_release_version.py` hardcoded the
literal `"0.18"` in eight separate assertions, so when `VERSION` moved to `0.19` the test
started failing and was treated as a stale test rather than as the drift alarm it was. All
four stamps now read `0.19.5`, and the test derives the expected value from `app/version.py`
instead of repeating it, keeping exactly one spelled-out literal for the current release. The
same drift now fails loudly against a single source of truth rather than silently shipping.

Two stale in-product version references were removed in the same pass: `/me` described itself
as the "v0.18 player dashboard" in its Discord command description, and the in-game realm
guide's Exploration section still called the scene engine "the v0.18 scene engine". Neither
carried information a player needed, so both dropped the version marker instead of being
re-pinned to a number that will drift again.

Verification: `tests.python.contracts.test_release_version` now passes (3 tests) where it had
been failing before this change; the assertion that all four stamps agree is what makes that
meaningful. Full 49-module suite re-run afterward with no change to the pre-existing sandbox
import failures.

## v0.19.6 — external audit findings, fixed

An independent review of the shipped v0.19 archive produced seven findings. Every one was
reproduced against the source before anything was changed, and every fix carries a
regression test that was confirmed to fail on the old code. One finding — the release
metadata inconsistency — had already been fixed in v0.19.5 and is not repeated here.

### Data loss: `/v1/db/batch` discarded writes when `transaction:false`

The worst of the batch. `storage.Conn.Execute` calls `maybeBeginImplicitLocked`, which
issues `BEGIN;` before any `INSERT`/`UPDATE`/`DELETE`/`REPLACE` when the connection is in
autocommit. `Conn.Close` rolls back if a transaction is still open. `dbBatch` only called
`Commit()` when `input.Transaction` was true, and it closes the connection with a `defer`.
So a non-transactional batch executed its statements, returned `HTTP 200` with a full
result set, and then had every write rolled back microseconds later by the deferred close.

`Transaction` is a plain `bool` on the request struct, so this is also what a client gets
by *omitting* the field — the failure mode is the default, not an opt-in. `dbBatch` now
commits unconditionally after the loop (`Commit()` is a documented no-op while the
connection is in autocommit, so read-only batches are unaffected), and on a mid-batch
failure in non-transactional mode it commits the statements that already succeeded before
returning, so the `"completed": N` in the error body is now true rather than aspirational.
`transaction:true` keeps strict all-or-nothing semantics.

Six tests in `go_core/internal/server/server_batch_test.go` cover this, and they count rows
through a *reopened* connection, which is the only way to observe the bug — the same
connection would have shown the uncommitted rows quite happily. Reverting the fix turns
three of them red with `non-transactional batch reported success but persisted 0 of 2 rows`.
Worth stating plainly: the only in-repo caller (`flush_slow_query_log`) passes
`transaction=True`, so shipped code was never losing data. The endpoint's contract was
broken, not the game.

### High: Discord snowflakes were rounded before the dashboard ever saw them

Every Discord ID is a snowflake — a 17–19 digit integer, far past
`Number.MAX_SAFE_INTEGER` (2^53−1 = 9007199254740991). JSON has no integer type of its own,
so a bare `847706123456789012` in a response body is parsed by `JSON.parse` into the
nearest IEEE-754 double and silently becomes `847706123456789000`. The dashboard then wrote
that rounded value into `<option value="...">`, posted it back on the next admin action,
and the lookup targeted a user id belonging to nobody — every teleport, currency grant,
karma adjustment and fate adjustment against such a player failed with "character not
found", with nothing in the logs pointing at rounding.

An earlier pass had already stopped `app.js` wrapping `user_id` in `Number()` and left a
comment saying the option value "is already the exact string from the DB". That comment was
wrong, and instructively so: the damage happened server-side, one layer earlier, before any
dashboard JavaScript could run. The fix has to live at the serialisation boundary, so
`app/dashboard.py` gained `json_safe_numbers`, which walks a payload and renders any integer
outside the safe range as a decimal string, and `_send_json` — the single chokepoint every
dashboard JSON response passes through — now routes through it. Booleans are excluded
explicitly, since `bool` subclasses `int` and would otherwise serialise as `"True"`.
`app.js` gained `id()` and `sameId()` helpers and now compares channel ids as strings
instead of `Number(current)===Number(c.id)`, and renders thread/guild/parent ids raw
instead of through the `n()` number formatter, which was rounding *and* adding thousands
separators to them.

Emitting ids as strings is what Discord's own API does, for exactly this reason, and costs
the front end nothing: these values are only ever compared, displayed and echoed back.

### Medium: a cleared channel message came back on the next Repair

Introduced by the schema-25 feature in this very release line. The dashboard offers "clear a
box and save to remove that channel's message", which stores `content=""`. But both
consumers read that back with `row.get("content") or DEFAULT_CHANNEL_MESSAGES.get(key, "")`,
and an intentionally empty string is falsy — so "explicitly disabled" was indistinguishable
from "never configured". The Discord message was deleted, `""` was stored, the dashboard
redisplayed the default as though it were live, and the next Full Setup/Repair posted it
again.

There are three states, and the code now says so: no stored row means use the default, a row
with text means the GM's custom message, a row with `""` means explicitly disabled.
`channel_message_state()` and `resolve_channel_message_content()` express that, and both
call sites use them. A secondary hole is closed at the same time: `ensure_channel_message`
used to return early when the target channel could not be resolved, *before* persisting, so
clearing a message while its channel was unbound silently kept the old text. It now saves
first and returns `None` afterward, so the caller still reports the slot as not-yet-posted
while the edit survives. The dashboard labels a cleared slot "(cleared — stays empty through
Repair)" and offers a "Restore default text" button, since the default text is otherwise
unreachable once cleared.

### Medium: `RELEASE_MANIFEST.sha256` was stale and nothing checked it

Measured on this tree before regenerating: 42 wrong hashes, 14 entries pointing at files
that no longer existed, 22 packaged files listed nowhere. The 14 "missing" turned out to be
benign — documentation that had moved into `docs/` and `docs/migration_history/`, which is
also where 14 of the 22 "unlisted" came from — but a manifest in that state is worse than
no manifest, because it looks like an integrity guarantee and is not one. `update.sh` never
verified it in any case.

`scripts/release_manifest.py` now generates and verifies it, with explicit documented
exclusions (`.env`, `data/`, caches, compiled artifacts, the manifest itself) and
deterministic ordering, in `sha256sum -c` format so it stays checkable with stock tools.
`update.sh` verifies the extracted archive against its own manifest *before* the running
install is touched, and refuses to proceed on a mismatch; it degrades to a warning only when
neither `sha256sum` nor `shasum` exists. `tests/python/contracts/test_release_manifest.py`
gates the whole thing, so the manifest cannot silently drift again — if it does, the test
says exactly which command regenerates it.

### Medium: backups taken in the same second overwrote each other

`"xianxia-" + time.Now().UTC().Format("20060102-150405")` has second resolution, so two
backups inside one second resolved to one filename — reachable by double-clicking the
dashboard button, and guaranteed whenever an automated backup lands with a manual one. The
operator was left with one file while the UI reported two. `reserveBackupPath` now stamps
milliseconds and reserves the name with `O_EXCL`, falling back to a numbered suffix, so two
concurrent requests cannot settle on the same name. The zero-byte placeholder is left in
place rather than removed, because a zero-length file is a valid empty SQLite database that
`sqlite3_open_v2(..., CREATE)` will happily write into — removing it would have reopened a
narrower version of the same race.

### Low: an adopted `#bugs` forum never got its tags

Only newly *created* forums received `available_tags`. When setup adopted an existing
`#bugs` channel it synced the topic and nothing else, while the dashboard reported
`BUGS_FORUM_TAGS` unconditionally — so a GM was told Open/Investigating/Fixed/Can't
Reproduce/Duplicate were available on a forum where Discord offered none of them.
`sync_bugs_forum_tags` now adds whatever is missing during setup/repair, preserving any tags
the GM added themselves and stopping short of Discord's 20-tag cap rather than clobbering
them. The snapshot reports the forum's real `available_tags` plus a `missing_tags` list, and
the dashboard surfaces the gap with a prompt to run Repair.

### Test coverage

The audit's closing observation was that none of the schema-25/26 dashboard operations had
tests, which is how the cleared-message bug coexisted with a green suite. Added:
`server_batch_test.go` (6 tests, batch durability and backup naming),
`test_snowflake_serialization.py` (12 tests, including a check that `_send_json` actually
routes through the sanitiser, and an `app.js` check that no id is rendered through the
number formatter), `test_channel_message_state.py` (11 tests over the three-state resolver,
lifted out of `main.py` by AST so they run without `discord.py`), and
`test_release_manifest.py` (4 tests). Each was confirmed to fail with its fix reverted —
they are regression tests, not just coverage.

### Verification

`go vet ./...` clean; all 7 Go packages pass. Python: the full 52-module suite was run
against both a pristine extract of the previous build and this one under two interpreters,
because this sandbox splits its dependencies — 3.11 has `httpx` (so it can import
`app.dashboard`) but cannot parse `main.py`'s PEP 701 f-string, and 3.12 is the reverse.
Under 3.11: 45 pass / 4 fail on both builds, byte-identical. Under 3.12: identical results
on every pre-existing module, plus the new ones. No regressions in either direction, and the
only movement is tests that did not exist before. `node --check dashboard/app.js`,
`sh -n update.sh`, `sh -n reset_database.sh`, `sh -n startup.sh` and
`dashboard_implementation_issues()` all pass. Not verifiable here, as ever: live Discord
rendering, since `discord.py` cannot be installed in this sandbox.

## v0.19.7 — hub layout, corrected against the live panel

v0.19.6 was the first build anyone could actually *see* the Components V2 layout in.
Four things only became decidable once it was on screen in a real Discord client, and
all four are fixed here. Nothing structural changed: same layout, same fallbacks, same
routing.

**The action rows carried a meaningless glyph.** Every row on a page rendered the same
`🔹`, because `_action_emoji` resolves the *leaf command* name (`propose`, `sever`,
`status`) against `_PAGE_EMOJIS`, which is keyed by hub root. Leaf names are almost never
in that table, so every action fell through to the generic fallback — five identical
diamonds down the Dao Partnership page, carrying no information and competing with the
labels they sat beside. `_mapped_action_emoji` now returns `None` when nothing specific
is mapped and the row simply omits the prefix. The fallback is still right for
`_action_listing`, the dense text listing in the classic panel, so that path is unchanged.

**The status header read as a wall.** Those values are built for embed *fields* — each in
its own labelled box — and the layout flattened them into one run-on line joined by `·`.
On a real client that wrapped badly: "Qi" ended a line and its bar began the next. Each
status is now its own line. The progress bars are also halved to five segments by
`_compact_status_value`, because at ten segments and 100% every cell is filled and the bar
renders as a solid rule rather than a meter. That compaction is local to the layout, so
the classic embed panel keeps its ten-segment bars, where they sit alone in a field and
read correctly.

**A red button that said "Open".** The danger colouring works — `Sever` came up red
without anyone reading the description, which is exactly the affordance the old dropdown
could never offer. But labelling it "Open" wasted the moment: the colour warned, the label
said nothing. Danger-styled buttons now carry their own verb (`Sever`, `Disband`,
`Abandon`); everything else keeps the single consistent "Open" so the column stays calm.

**Two navigations doing one job.** The layout kept a system dropdown alongside Prev/Next.
Seeing them together, the select spent a full row to display the system already named in
the heading directly above it. The dropdown is gone; stepping is the only navigation, and
the arrows wrap, so the far end of an 18-system hub is one Prev away rather than seventeen
Nexts. Removing that row also freed two components, which let `_LAYOUT_ACTION_LIMIT` go
from 7 back to 8: the character hub's worst page now renders at 25/40 components (was 27)
and `/sect`, still the busiest page in the game at 25 actions, at 36/40 across four chunks.

Verification: the same stand-in `discord` module as v0.19.5, re-run against the shipped
`hubs.py` — all 18 character systems inside limits, `/sect` chunking still reaching all 25
actions, expired-panel render and the full `_layout_targets_panel` truth table unchanged.
The Dao Partnership page was rendered through the real `rebuild()` and inspected
component by component to confirm each of the four fixes landed. Full Python suite
re-run against a pristine extract of v0.19.6 with byte-identical results.

## v0.19.8 — onboarding copy that points at commands players can reach

A player who cannot explore and is told to "use **/family leave**" is stuck twice: once by
the rule, and once by the instruction, because `/family leave` is not a command. Only 21
slash commands are registered with Discord - `/begin`, `/me`, `/quests`, `/action`,
`/check`, `/admin` and the 16 hubs (`register_command_surface`). Every gameplay command is
metadata bound into `ActionRegistry` and reached through a hub panel, never typed. The
correct form names the hub and then the action: **/family → Leave**.

That wrong form had shipped in six player-facing places, including both messages a stuck
player actually reads: the birth-household thread opener and the blocked-exploration hint.
All six now use the reachable form, and the copy around them was rewritten to lead with
the way out instead of burying it:

- **Household thread opener** — now opens with "You are inside the house", says plainly
  that exploring and hunting will refuse until you step out, names where **/family → Leave**
  puts you, and mentions **/family → Enter** for coming back. The shared-scene explanation
  moved below that, because it is not what a stuck player needs first.
- **Expedition journal opener** — is location-aware now. `_expedition_thread_intro` checks
  the character's real location and, when they are inside a private one, names the specific
  exit for where they actually are rather than telling them to explore, which would fail.
  Out in the world it reads as before.
- **Blocked-exploration hint** (`_explain_engine_error`) — leads with "You're **indoors**"
  so it reads as a rule rather than a malfunction, then lists all three exits in reachable
  form.
- **Character creation** — the "Next steps" field and the household follow-up now say
  outright that exploring and hunting are closed until you step outside, and that this is
  not a bug.
- **`/family view`** — the inside-the-household branch tells you how to leave and where you
  will land; both branches use the reachable form.
- **Ancestral investigation flow** — four `/family investigate|quest|conflict` hints
  corrected.

`private_location_exit()` centralises the mapping from a location prefix to its exit
command. Note the ordering inside it: `sect_abode:` is tested before `abode:`, because the
latter is a prefix of the former and would otherwise swallow it and send sect residents to
the wrong instruction. That case is asserted in the tests.

The real fix is the gate, not the six edits: this shape had already shipped twice, so
`tests/python/unit/test_player_facing_command_hints.py` now walks every non-docstring
string literal in `main.py` looking for a hub name followed by a bare word with no arrow.
Writing it immediately turned up seven false positives from ordinary compound phrases
(`NPC/sect locations`, `breakthrough/combat checks`, `GM/world decision`), which a
lookbehind for a preceding letter removes, and four more genuine instances nobody had
noticed. The suite also asserts the detector still catches the shape it exists for, since
a guard that cannot fail is worse than no guard.

Verification: the copy helpers were lifted out of `main.py` by AST and executed directly,
so the text in the notes above is the text the code emits - including both branches of the
expedition opener, the `sect_abode:`/`abode:` ordering, and an unrelated engine error
passing through untouched. Full Python suite re-run against a pristine extract of v0.19.7
with byte-identical results under both interpreters.

## v0.19.9 — the integrity check refused a good package on real hardware

Reported from the NAS, installing v0.19.8 over v0.19.6:

```
ERROR: Release integrity check failed - the package does not match its own manifest.
       Nothing was installed; the running release is untouched.
sha256sum: unrecognized option '--quiet'
```

The archive was fine. Verified afterwards with the portable form: 228 files, zero
mismatches. What failed was the checker. `--quiet` and `--strict` are GNU coreutils
extensions, and a QNAP NAS provides BusyBox, which rejects them, exits non-zero, and
never checksums anything at all. `verify_release_manifest` read that non-zero exit as
"the package is corrupt".

This is the worst possible place for that mistake. The check exists for exactly one
purpose - telling a sound package from a damaged one - and on the target hardware it
answered "damaged" for every package, including sound ones, while printing a reason that
looks like tampering. Worse, it is self-blocking: the running updater is the *installed*
one, so shipping a fixed `update.sh` inside a new archive does not help. Anyone on
v0.19.6 or later has to patch their installed updater by hand before any release can be
installed. That is a one-line change, in the deploy note below.

Only `-c` is portable, so that is all the check uses now. The per-file `OK` output that
`--quiet` was suppressing goes to a log file instead of the terminal, and `--strict` was
never load-bearing here: the generator writes well-formed lines and
`scripts/release_manifest.py --verify` is the strict path. The failure report also now
captures stdout and stderr together, because `sha256sum` prints its `FAILED` lines on
stdout - reporting stderr alone would have shown the summary warning and hidden which
files actually mismatched.

Two tests in `test_release_manifest.py` gate it: one rejecting GNU-only flags in that
block, one requiring the combined-stream capture. Writing the first one immediately trod
on the same rake as the fix itself - the explanatory comment above the check names
`--quiet` and `--strict` to say why they are banned, so the test flagged its own
documentation. It strips comment lines before scanning, the same way the channel-message
test parses the AST rather than grepping text.

Verification: `sha256sum -c` run against the shipped archive (228 files, exit 0); the new
gate confirmed to fail against the v0.19.6 `update.sh` and pass against this one; the
one-line patch below applied to a real copy of the v0.19.6 updater and re-parsed with
`sh -n`. Not verifiable here: BusyBox itself is not installed in this sandbox, so the
portability claim rests on which flags are GNU extensions rather than on an executed
BusyBox run.

## v0.19.10 — the layout rolled out to every hub, in four stages

`/character` had been the pilot since v0.19.5. All 17 hubs now use the Components V2
layout: the 16 player hubs and `/admin`. Rolled out in four stages, easiest shape first,
so a problem would surface on a simple hub before reaching one that stresses the budget:

1. **Multi-page, nothing over 5 actions** — npc, world, travel, craft, realm, items,
   combat, economy. Nothing chunks; the plainest case.
2. **Single-page hubs** — innerworld, beast, abode, family. A structurally new case: with
   one page there is no Prev/Next at all, and abode (11 actions) and family (14) chunk
   without it.
3. **Multi-page hubs that chunk or sit on the limit** — quest, cultivation (8 actions on
   Aptitudes, exactly the per-page limit), sect (25 across three nested subgroups).
4. **`/admin`** — last, deliberately, because it is how the server is operated.

Stage 4 needed a code change, which is the argument for staging rather than flipping one
set literal. `CommandHubView.interaction_check` re-checks `guild_permissions.administrator`
on *every* interaction, not just at invoke; `LayoutHubView` only checked ownership. `/admin`
is gated at invoke by `default_permissions` and `require_admin`, but a panel lives for 15
minutes, so without the re-check an administrator whose role was removed mid-session would
keep a working GM console until it timed out. The guard is ported, and the two views now
deny identically — verified against a table of six cases (administrator owner, owner who
lost the role, owner outside a guild, a different administrator, and both player-hub cases).

Every hub, every page and every chunk was rendered through the real `rebuild()` after each
stage. Worst cases: `/admin` and `/sect` at 36/40 components, `/abode` at 995/4000
characters. Nothing exceeds a limit, and the closest margin is four components.

Two findings came out of staging that would not have surfaced from reading the code:

- **`/family` puts Leave on the second chunk.** Actions sort alphabetically, so Leave is
  10th of 14 and lands past the 8-action window. The onboarding copy fixed in v0.19.8 says
  "**/family → Leave**", and a new player following it will open `/family` and not see it
  until they press More actions. This is not a regression — the classic panel buried Leave
  in a 14-entry dropdown — but it is not the win the layout should give, and the fix
  (ordering actions by relevance rather than alphabetically) is a design decision affecting
  every hub and both panels, so it is left for a deliberate call rather than taken here.
- **The first version of the admin-guard test passed while the guard was disabled.** It
  asserted on the substring `self.definition.name == "admin"`, which also appears in the
  ownership branch's message selection, so it could not tell the guard from the message.
  It now walks the AST for an `if` on the admin name whose body performs the permission
  check — and was confirmed to fail when the guard is disabled and pass when it is restored.

`tests/python/unit/test_hub_layout_rollout.py` gates the result: every declared hub is in
`LAYOUT_HUB_NAMES`, the classic panel and its feature-detection fallback still exist, both
views guard `/admin` the same way, and `_LAYOUT_ACTION_LIMIT` satisfies the arithmetic that
keeps the busiest page under 40 components — so raising it to "show more actions" fails the
build rather than the panel. Each of those was confirmed to fail against a deliberately
broken tree.

Rollback stays a one-line edit: remove a name from `LAYOUT_HUB_NAMES` and that hub returns
to the classic panel with no other change.

Verification: full 54-module Python suite run against a pristine extract of v0.19.9 under
both interpreters, identical on every pre-existing module; `go vet` clean and all 7 Go
packages passing; static gates (`node --check`, `sh -n` on all four scripts, the dashboard
contract check) clean. Not verifiable here, as always: how Discord renders 16 further
hubs — `/character` is the only one seen live so far.

## v0.19.11 — action ordering by relevance, and an external review answered

### Actions sort by usefulness now, not alphabetically

Staging the rollout surfaced this: `/family` has 14 actions, alphabetical order put
**Leave** tenth, and the 8-action window meant a new player following v0.19.8's onboarding
copy ("**/family → Leave**") opened the hub and did not see it. `_leaf_actions` sorted by
path, which is what a filesystem does, not what a player needs.

Actions now sort into four bands, then alphabetically within each:

0. **orienting** — status, view, info, sheet, history, list, inspect, check
1. **primary** — enter, leave, join, create, start, travel, use, talk, propose, respond…
2. **everything else**, unchanged and alphabetical
3. **irreversible** — sever, disband, abandon, purge, reset, revoke, reincarnate

`/family` chunk one now reads History, View, Claim, Enter, **Leave**, Ancestry, Child,
Clan — Leave fifth and visible. Dao Partnership reads Status, Dual Cultivate, Propose,
Respond, Sever, instead of burying Status last and putting Sever fourth.

The important design point is that this is a **different axis from
`_DANGER_ACTION_WORDS`**, which decides button colour, and the two must not be conflated.
"Leave" is red because it changes your state and is worth noticing, but it is ordinary
movement and belongs near the top; "Sever" is red *and* irreversible and belongs at the
bottom. Colour warns, order prioritises — sorting by the colour axis is precisely what
buries the action people need. A test asserts both: that `leave` is still styled danger,
and that it does *not* rank as rare.

Only frequent, meaningful verbs are listed. There are 166 distinct leaf verbs across the
game; anything unlisted stays in band 2 and alphabetical, which keeps the table small
enough to stay honest, with no per-hub tuning. Ordering is shared with the classic panel,
so its dropdown improves too.

### An external review of v0.19.7, answered

**Rejected — "Components V2 allows only 30 total components, drop the limit to 6."**
The limit is 40, and 0.19.10's rollout stands. Three primary sources, checked directly:
Discord's Component Reference ("Messages allow up to 40 total components"), discord.py's
own `discord/ui/view.py` (`if self._total_children > 40: raise ValueError('maximum number
of children exceeded (40)')`), and the discord.js guide ("Messages can have up to 40 total
components (nested components count!)"). The review cited a changelog entry for the "30"
figure; fetching that changelog shows no Components V2 entry at all, and the cited URL
carries a `utm_source=chatgpt.com` parameter. The recommendation to add a component-count
regression test was right and already shipped in v0.19.10 — with 40. The docstring now
names all three sources so nobody "corrects" it downward later, and a further test pins
that the layout uses exactly one top-level component.

**Fixed — first-run setup was misleading.** `.env.example` ships `DASHBOARD_ENABLED=true`
with `DASHBOARD_TOKEN=` empty, while `startup.sh` told a new operator to fill in only
`DISCORD_TOKEN`, `GUILD_ID` and `OPENROUTER_API_KEY`. Following those instructions got you
to the dashboard check and a hard failure on a 20-character token you were never told
about. The first-run message now names the token, gives the command to generate one, and
offers `DASHBOARD_ENABLED=false` as the opt-out; the failure message says how to produce a
token too.

**Fixed — the dashboard flag was read two different ways.** Acceptance took
`1|true|yes|on`; the closing log hint compared against the literal `"true"`. So
`DASHBOARD_ENABLED=yes` started the dashboard and then withheld the command for reading
its logs. The flag is normalised once into `DASHBOARD_ON` and both places use it.

**Fixed — the Python containers ran as root.** The Go engine drops to uid 10001; the
Dockerfile behind bot, db-init and dashboard had no `USER` at all, including the
network-facing dashboard. They now run as 10001. The trap worth recording: a bare `USER`
would have killed all three at startup, because `Database.__init__` calls
`path.parent.mkdir()` *unconditionally*, before it checks whether `GAME_ENGINE_URL` is
configured. These services never open SQLite — Go owns it — but that mkdir still runs, so
`/app/data` is created and chowned in the image for a directory the code then never uses.
A test asserts that, and another asserts only the engine mounts a host volume, since a new
bind mount on a Python service would need this revisited.

One open question from the review is worth recording as closed: the code never sets the
`IS_COMPONENTS_V2` message flag (`1 << 15`) itself, and Discord requires it. It does not
need to. `handle_message_parameters` in discord.py's `http.py` - the single payload builder
every send path funnels through - sets it automatically:

```python
if view.has_components_v2():
    if flags is not MISSING:
        flags.components_v2 = True
    else:
        flags = MessageFlags(components_v2=True)
```

`LayoutView.has_components_v2()` returns true for any V2 child, so the flag is set for us.
The live v0.19.6 deployment is the stronger proof: Discord would not render a Container
with Section accessory buttons at all without that flag, and it rendered.

Verification: every hub, page and chunk re-rendered through the real `rebuild()` after the
ordering change (worst case unchanged at 36/40). Each of the five new deployment gates was
confirmed to fail against the v0.19.10 files and pass against these. Full Python suite run
against a pristine extract of v0.19.10 under both interpreters. Not verifiable here: the
container user drop, since Docker cannot run in this sandbox — the reasoning is spelled out
above, and it is the one change in this release I would watch on first start.

## v0.19.12 — main.py decomposition, stage 1: the shared runtime

The split named in `V015_REVIEW_STATUS.md` ("bot/main.py / database decomposition ...
remain after authority migration") and unblocked since v0.18 finally starts. This is stage
one of several, each its own release, because the sandbox cannot import `main.py` at all -
`discord.py` is not installable here - so a refactor of the file every command routes
through cannot be validated by running it. Small stages mean a failure is attributable.

**What moved.** `app/bot/runtime.py`, 323 lines: the singletons (`SETTINGS`, `DB`,
`ENGINE`, `WORLD`, `ROOT`, `log`) and the session helpers every command needs -
`require_character`, `reply_long`, `serialized_user_action`, `current_world_time`,
`character_location_display`, the per-user action lock, the private-location exit table.
`main.py` drops 13,177 -> 12,923 lines and imports them back. No behaviour changes; nothing
was rewritten, only relocated.

**Why this first.** Measuring the dependency footprint of three domain blocks
(`family_group`, the sect block, `aptitude_group`) showed each needs the same small core -
`DB`, `WORLD`, `ENGINE`, `require_character`, `reply_long`, `serialized_user_action`,
`current_world_time`, `GameEngineError` - plus a handful of domain-local helpers that can
travel with their domain. So the shared core is what has to exist first; without it every
command module would have to import `main`, and `main` imports them back. `runtime.py`
importing nothing from `main` is the property that makes the remaining stages possible, and
a test asserts it.

**The structure this revealed.** Nearly every command group is already contiguous in the
file - `family_group` is L10932-11456, `sect_group` L10437-10912, and 40-odd others
likewise. The file is organised by domain already; it is just not separated into files.
That makes the remaining stages mechanical rather than architectural.

**How it was verified without being able to run it.**
`tests/python/unit/test_bot_module_split.py` uses `symtable` - real scope analysis, not
substring matching - to prove every module resolves every name it reads from module scope.
The failure mode of a bad move is a `NameError` at import, which takes the whole bot down
at startup, and that is precisely what this detects. It earned its place immediately:
the first cut left `ROOT`, `_player_property_types` and `WORLD` behind, all three used by
moved code. Each would have been a dead bot on the next deploy. The same check also pins
that `runtime.py` never imports `main`, that the moved names are defined exactly once
(moved, not copied - a leftover would silently shadow), and that `main` still exposes
`XianxiaBot`, `bot`, `register_command_surface` and `run`, which `app/bot/__init__.py`
re-exports.

Two scope subtleties are recorded because they cost time and will recur in later stages.
A hand-rolled AST walk first reported the `/world` command as a dependency of the core - it
was matching the *parameter* named `world` in `_world_is_unlocked(world)`; `symtable`
handles scope properly and the false positive vanished, which is why the test uses it.
And module-level comprehension variables (`defn`, `x`) are reported as globals by
`symtable`, so the checker collects comprehension targets as defined rather than chasing
them.

`test_player_facing_command_hints.py` now scans the whole `app/bot` package rather than
`main.py` alone, since the private-location exit table moved. Anchoring a check to one file
is how a check silently stops covering what it was written for.

Verification: both modules parse; unused imports trimmed out of `runtime.py`; every
existing AST-based test that reads `main.py` (`test_gui_integrity`,
`test_authority_boundary`, `test_command_cleanup`, `test_hub_layout_rollout`,
`test_channel_message_state`) still passes; full Python suite identical to a pristine
extract of v0.19.11 on every pre-existing module under both interpreters; `go vet` clean and
7/7 Go packages. Not verifiable here: that the bot actually starts. Stage 1 moves no command
and rewrites no logic, and `app/bot/__init__.py`'s imports are asserted, but the first start
after this upgrade is the real test.

## v0.19.13 — the split's ordering bug, and the check that was missing

v0.19.12 did not start. The bot container went unhealthy, the updater rolled the NAS back
from 0.19.12 to 0.19.6 on its own, and the database backup was restored. The safety net
worked exactly as designed; the release should not have needed it.

**The bug.** `runtime.py` had this:

```python
DB = Database(ROOT / SETTINGS.database_path, ...)   # line 37
...
ROOT = Path(__file__).resolve().parents[2]          # line 43
```

`NameError: name 'ROOT' is not defined`, at import, every time. The bot could never have
started.

**How it got there.** The extraction ran in three passes. The first moved 22 definitions in
their original order. Two follow-up passes moved `ROOT`, `_player_property_types` and
`WORLD` after the resolution check flagged them as missing - and those passes *inserted at a
marker* rather than restoring original relative position. Module-level statements execute
top-down, so position is semantics, and the follow-ups quietly broke it.

**Why the tests passed anyway.** `test_bot_module_split.py` proved every name the module
reads is defined *somewhere* in it. It never asked whether the name is defined *yet* at the
point of use. That distinction is the whole bug: as a set of names the module was perfectly
consistent, and as a sequence of statements it was broken. A check that answers "does this
resolve?" cannot answer "does this resolve in time?".

**The fix.** `runtime.py` is regenerated from the pre-split `main.py` by extracting its 25
definitions in original line order, so ordering is preserved by construction rather than by
me getting three passes right. The order is now
`log, SETTINGS, ROOT, WORLD, ENGINE, DB, ...` exactly as it was.

`DefinitionOrderTests` is the check that was missing: it walks module-level statements in
order, tracking what is bound so far, and fails on any top-level statement that reads a
top-level name not yet assigned. Reinstating the exact bad ordering makes it report
`runtime.py uses a module-level name before assigning it: [(35, 'ROOT'), (39, 'ROOT')]` -
both use sites, by line. Two details were needed to make it usable: it considers only
genuine top-level bindings (the resolution check deliberately counts comprehension targets
as defined, and reusing that set flagged every module-scope `[... for key in ...]`), and it
computes the name set once per module (calling it per node turned a 13,000-line file into a
multi-minute walk that timed out).

**On attribution.** The NAS went 0.19.6 -> 0.19.12 in one jump, so v0.19.11's non-root
container drop landed in the same failed deploy. It was not the cause - the failure was a
`NameError` in Python, not a permission error, and `xianxia-db-init` (the same image, the
same user) exited cleanly beforehand. The user drop is unchanged here and still wants
watching on the next start, but it is not implicated in this one.

The lesson worth keeping for stages 2 and beyond: when moving module-level code, order is
part of the contract, and "the names all resolve" is not the same claim as "the module
imports".

## v0.19.14 — the second split failure: a decorator moved its annotation namespace

v0.19.13 fixed the ordering bug and still did not start. The real traceback, captured by
running the bot image in the foreground:

```
File "/app/app/bot/main.py", line 4513, in <module>
  @registered_group_command(aptitude_group, name="temper", ...)
File "discord/utils.py", line 1145, in evaluate_annotation
  evaluated = evaluate_annotation(eval(tp, globals, locals), ...)
NameError: name 'app_commands' is not defined
```

`main.py` imports `app_commands` on line 13. The name was missing anyway, and the reason is
worth understanding because it will recur at every later stage.

```python
@registered_group_command(aptitude_group, name="temper", ...)
@app_commands.choices(target=APTITUDE_TARGET_CHOICES)
@serialized_user_action                                   # moved to runtime.py in v0.19.12
async def aptitude_temper(interaction, target: app_commands.Choice[str]) -> None:
```

`serialized_user_action` wraps its target. `functools.wraps` copies `__name__`, `__doc__`
and `__module__`, but it cannot copy `__globals__` - that belongs to the code object's
defining module, and the wrapper was defined in `runtime.py`. So the callback discord.py
finally receives carries **runtime.py's** globals. `main.py` uses
`from __future__ import annotations`, so `app_commands.Choice[str]` reaches discord.py as a
*string*, and `discord.utils.resolve_annotation` evaluates it against
`callback.__globals__` - that is, against runtime.py. 119 command callbacks are wrapped this
way.

The import was there when the module was first written. **A static "unused imports" pass
removed it**, correctly by its own lights: nothing in runtime.py's own code executes
`app_commands`. It is needed only to resolve other modules' annotations. That cleanup is
what shipped the failure.

**The rule this establishes**, and it governs every later stage: a module defining a
wrapping decorator must import every name appearing in the ANNOTATIONS of the functions it
decorates, not merely the names its own code runs. `runtime.py` now imports `app_commands`
under a comment saying exactly that, and `WrappingDecoratorAnnotationTests` enforces it by
collecting the annotation names of every `@serialized_user_action` callback across the
package and checking each resolves in the decorator's own module. Deleting the import again
produces: *"app/bot/runtime.py defines @serialized_user_action, so discord.py resolves its
callbacks' string annotations against runtime.py's globals - but runtime.py does not import
['app_commands']."*

**Why two static passes missed it.** Name resolution proved every name resolves in the
module that reads it; definition order proved every name is assigned before use. Neither
could see that a *third* module would be asked to resolve *main.py's* annotations. The
failure lives in the interaction between three separately-reasonable things - `wraps`,
postponed annotations, and a library that evals them against `__globals__` - and no
single-module analysis can see it.

**So the verification changed.** The build sandbox cannot install `discord.py`, which is why
everything here has been static. It can, however, run a stub good enough to *import the
package for real*: a permissive stand-in for `discord`, `discord.ext.commands` and
`discord.app_commands`, plus a meta-path finder that stubs any other missing third-party
module. Crucially the stub now reproduces `_extract_parameters_from_callback` - it evaluates
each callback's string annotations against `callback.__globals__`, exactly as discord.py
does. Importing the shipped v0.19.13 tree under it reproduces the production `NameError`
verbatim; importing this one succeeds. That is a materially stronger check than anything
used for stages so far, and stage 2 will run against it before shipping.

Also corrected: `runtime.py` no longer has unused imports trimmed automatically. The
trimming pass is what caused this, and "unused" is not a property a static reader can
determine for a module whose job includes hosting other modules' annotation namespace.

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
- **Broken/unused command audit**: cross-referenced all 259 registered
  Discord commands against the Go action dispatcher. Zero broken commands,
  zero stubs. Three Go actions have no Discord caller (`fate.adjust`,
  `cultivation.reward`, `combat.apply_damage`); `fate.adjust` is confirmed
  intentional — Fate is GM-controlled, not player-spendable — the other two
  remain open questions, not bugs.
- **Player command-surface review**: read every root command actually added
  to the Discord command tree (`register_command_surface` in
  `app/bot/main.py`) rather than the raw 259-command inventory, since most
  of that inventory is never registered with Discord at all —
  `ActionRegistry` in `app/bot/registry.py` deliberately keeps hub-routed
  gameplay commands (`/fate`, `/sheet`, `/inventory`, `/battle`, `/market`,
  and ~65 others) as metadata-only objects, reachable solely through hub
  panel buttons, never as typable slash commands. The real player-facing
  surface is 5 standalone commands (`/begin`, `/me`, `/quests`, `/action`,
  `/check`) plus 16 category hub commands (`/character`, `/quest`,
  `/cultivation`, `/items`, `/npc`, `/world`, `/travel`, `/combat`,
  `/economy`, `/craft`, `/beast`, `/sect`, `/family`, `/abode`,
  `/innerworld`, `/realm`), each opening a button-driven panel over a subset
  of the 70 hub-page command roots (`_MIGRATED_ROOTS`); `/admin` is a 17th
  root command but is GM-gated. Found every one of the 70 hub-page roots
  correctly wired to a real page in exactly one hub (enforced at import time
  by the existing `_missing_action_roots` `RuntimeError` guard), so no
  player-facing feature is unreachable. One cosmetic fix made in passing:
  `/me`'s description still said "Open your v0.18 player dashboard" after
  two releases; changed to "Open your player dashboard" so it doesn't go
  stale again.


## v0.19.15 — Administrator AI monitor (narrator health + chat digest)

Two new GM-only actions under `/admin → Server`. Neither is a typable command;
both go through the existing `admin_server_group` metadata surface and re-check
`require_admin` in their own body, so a panel that outlives an administrator's
role cannot be used.

### Why the bot could not already read its own channels

Two independent blockers, both worth writing down because both look like
features that already existed.

**`scene_history` is not a chat log.** `Database.add_history` takes a `keep=60`
argument and, on every single insert, runs a `DELETE ... WHERE id NOT IN (SELECT
id ... ORDER BY id DESC LIMIT 60)` for that channel. It is a rolling narration
context window, deliberately small so the SQLite file stays small. Reading it
back to answer "what has been happening here" gives you at most the last 60
rows of one channel, most of which are the bot's own narration. Any real
analysis has to come from Discord's message history — and there was no
`channel.history()` call anywhere in `app/` before this release.

**The bot could not see message text.** `MESSAGE_CONTENT_INTENT` was `false`, so
`message.content` arrives empty for every message the bot was not mentioned in.
This release ships `.env` and `.env.example` with it set to `true`, but the
environment variable is only half of it: **Message Content Intent must also be
enabled in the Discord Developer Portal** under *Bot → Privileged Gateway
Intents*. Under 100 servers that is a toggle, not a verification process. When
the setting is off the digest says so explicitly, with both steps spelled out,
instead of reporting a channel that merely looks empty.

Enabling the intent is not free of side effects, and this was an accepted
trade rather than an oversight: `on_message` already existed and had been inert
purely because the intent was off. With it on, the bot starts writing RP
messages into `scene_history` and replies "Create your cultivator first with
**/begin**" to anyone who @-mentions it without a character. `AUTO_NARRATE` and
`AUTO_NARRATE_EVENT_THREADS` stay `false`, so it does not begin narrating on its
own.

### `ai_status`

Narrator and route health, from counters only — no prompts, no player text, no
API key (a test asserts the snapshot contains none of them). It reports how many
narrations were served by AI versus how many silently fell back to procedural
prose, per-route attempts/successes/failures, which routes are cooling down and
with how long left, the last error per route, and how often the local 20/min
ceiling refused a request outright.

The counter that matters most is the procedural fallback rate. That failure mode
is invisible to an operator by design: narration is descriptive only, canonical
mechanics are already resolved, so a total OpenRouter outage produces no error
anywhere — play just quietly goes flat. Players notice; logs do not.

A second distinction worth the code it costs: a route skipped because it is
cooling down is counted as `skipped_cooling`, not as an attempt. Without that,
a route that is failing constantly looks healthy in the panel — zero recent
failures — while actually serving nothing.

### `chat_digest`

Reads a channel (optionally its threads) over a configurable window and reports
what players did, where they got stuck, possible bugs, mood, and what deserves
attention. Options: `channel`, `hours`, `include_threads`. Writes a
`server.chat_digest` entry to the admin audit log.

**It costs nothing.** OpenRouter's Fusion server tool (`openrouter:fusion`) was
evaluated for this and deliberately not used. Fusion fans a prompt out to a
panel of 1–8 models in parallel and has an analyst model diff the answers for
consensus, contradictions and blind spots — genuinely well suited to a
once-a-day report — but it bills for every panel model. This build keeps
`OPENROUTER_REQUIRE_FREE=true` and routes the monitor through the same
`AITaskRouter` free chain as narration, so it cannot start spending money. A
contract test asserts `chat_monitor.py` contains no client, no base URL and no
model string of its own, and that `openrouter:fusion` appears nowhere in the
tree. The price paid is a small context window, which is why the transcript is
chunked and analysed map-reduce style (per-chunk notes on the routine tier, one
synthesis pass on the epic tier) rather than in one shot.

Overflow keeps the **newest** chunks, not the first ones, and says so in the
report — an operator asking about a channel is asking about recent play.

### The transcript is untrusted input

It is player-authored text going into a model prompt. The blast radius is small
(administrator-only output, text rather than actions), but it is fenced anyway,
the system prompt states plainly that fenced content is data and never
instructions, and a player who types the closing fence themselves has it
neutralised — otherwise the rest of their message escapes the fenced region and
is read as prompt text.

### The prompt-leak guard is now opt-out, and only the monitor opts out

`_validate_generated_text` rejects model output containing "game engine",
"system prompt", "canonical context block" and similar. That is a *player-facing*
protection and it stays exactly as it was for narration. A bug digest, though,
routinely has to say "the game engine returned an error" when summarising a bug
report — running the player guard over it made the monitor fail on precisely the
reports it exists to surface.

So `generate()` gained `leak_guard: bool = True`. Narration never passes the
flag; `narrator.py` does not contain the string at all. A contract test asserts
`chat_monitor.py` is the only file under `app/` that sets `leak_guard=False`.

### Every AI failure degrades, never raises

If one chunk fails the rest still report. If synthesis fails the part notes are
shown. If every route fails, or the router is not configured at all, the
deterministic counts — messages, humans vs bots, per-author, per-channel, time
span — are still returned. That half of the report never needed a model.

### New settings

All bounded in `config.py`: `MONITOR_MAX_MESSAGES` (400, 20–5000),
`MONITOR_LOOKBACK_HOURS` (24, 1–720), `MONITOR_CHUNK_CHARS` (6000, 1000–20000),
`MONITOR_MAX_CHUNKS` (6, 1–20). The ceilings exist because the monitor shares the
narrator's 20 req/min limiter: an unbounded transcript would starve narration for
a whole minute.

### Verification

59 new tests (47 unit, 12 contract). Eight independent mutations were applied to
a copy of the tree to prove the new tests fail without their fixes — the
leak-guard split, limiter counters, route attempt counters, the cooling-skip
counter, fence neutralisation, newest-chunks-kept, degrade-not-raise, and the
monitor's own `leak_guard=False`. Two pre-existing admin-inventory assertions
that spell out the admin action count moved from 40 to 42; the literal is
deliberate and is what catches an admin action being added or lost by accident.

The full suite was baseline-diffed against a pristine v0.19.14 extract: the same
27 pre-existing sandbox loader errors on both (missing `discord`/`aiosqlite`),
zero new failures. The stub-import harness built for v0.19.14 — which reproduces
discord.py's annotation resolution against `callback.__globals__` — was re-run
against this tree, and both new command callbacks and their annotations
(`discord.TextChannel | None`, `int | None`, `bool`) resolve clean.


## v0.19.16 — openai 2.52.1 → 3.7.0 (dependency bump, shipped alone)

Nothing else is in this build. It is isolated so that a startup failure has
exactly one candidate cause, and so the rollback target (v0.19.15) is otherwise
byte-identical in behaviour.

### Why the bump is a no-op for this codebase

openai 3.0.0's single documented breaking change is that **HTTPX2 replaced httpx
as the HTTP client**, and the project's own `httpx2.md` states the condition
under which nothing else changes:

> If you construct an `OpenAI` or `AsyncOpenAI` client without providing
> `http_client`, your existing API calls, parsed response models, streaming APIs,
> authentication, retries, and numeric timeouts continue to work.

This codebase's entire openai surface is five lines — `AsyncOpenAI(...)` in
`ai_router.py` and `narrator.py`, `chat.completions.create`, `responses.create`,
`output_text` — and none of them passes `http_client`. A contract test now
asserts that, because it is the whole basis for calling this safe rather than an
incidental detail.

### The httpx trap, which we survive by having pinned it already

openai 3.x installs `httpx2` and **no longer installs `httpx` at all**.
`app/database/remote.py` and `app/game_engine.py` import `httpx` directly for the
Go engine transport — the path every piece of canonical game state travels. Had
we been relying on openai's transitive install, this bump would have removed the
HTTP client the entire game depends on.

We aren't: `requirements.txt` has always pinned `httpx>=0.28,<1` in its own
right. `httpx2` is a separate package name rather than httpx 2.x, so the two
coexist without a resolver conflict. A contract test now holds that invariant in
place, with the reason written next to it.

### The one genuine risk: TLS trust store

HTTPX2 verifies against the **operating system trust store** instead of the
certifi bundle httpx 0.x used. On `python:3.12-slim` that store is whatever
`ca-certificates` provides.

If it were absent, every OpenRouter route would fail on a certificate error — and
**nothing would raise**. Narration is descriptive only; canonical mechanics are
resolved before it runs, so a complete AI outage returns procedural prose and
continues. Players see the writing go flat. No log explains it. That is the
worst possible shape for a failure, and it is precisely the shape `ai_status`
was built to expose.

Two defences went in:

**In the image.** The Dockerfile installs `ca-certificates`, runs
`update-ca-certificates`, asserts the bundle is non-empty with `test -s`, and
sets `SSL_CERT_FILE` to it — all *before* `pip install`. A missing bundle is now
a loud build failure instead of a silent runtime one. `go_core/Dockerfile`
already did this; the Python image never had.

**In the router.** TLS failures are now classified by walking the exception
`__cause__`/`__context__` chain, because the openai client wraps transport errors
and an `SSLError` is never the outermost exception in practice. They are counted,
the offending route is flagged, the first one logs at ERROR level with the
remediation, and `ai_status` prints a banner. The cycle-safe walk is tested, and
so is the negative case — "results were empty" and "rate limited" must not be
mistaken for TLS.

### What this verification does *not* cover

The build sandbox has no PyPI access. openai 3.7.0 could not be installed,
imported or called once here. The stub-import harness *stubs* the `openai`
module, so it passes green regardless and proves nothing about this change.
Everything above is a traced read of the published migration note against five
call sites, plus defences for the one runtime failure mode that reading found.

The version string itself is the operator's. PyPI's JSON API, the GitHub tags
page and the releases Atom feed are all robots-blocked from this environment, and
the releases page returned inconsistent partial views across repeated fetches, so
neither 3.7.0 nor its release date could be independently confirmed here — v3.0.0
was confirmed. If 3.7.0 does not exist, `pip install` fails during `docker build`
and `update.sh` rolls back before anything reaches the running stack.

### After deploying

Open `/admin → Server → ai_status`. A TLS banner, or a procedural-fallback rate
that was not there on v0.19.15, means roll back to v0.19.15.


## v0.19.17 — the "0 stones" bug, and the four others beside it

Two player reports from the test server, both real, both wider than reported.

### "Bought **Spirit-Iron Sword x1** for **0** stones" — while the wallet *was* debited

Not a wallet bug and not a pricing bug. A **key mismatch across the language
boundary**.

`marketTradeAction` in Go returns:

```go
out := map[string]any{"item_id": …, "quantity": …, "unit_price": unit,
                      "total": total, "currency_id": currency, "buy": …, "balance": bal}
```

The Python handler read `result.get('total_price', 0)`. `total_price` is not a
key the engine has ever returned. Go compiled, Python ran, and the **default**
turned a missing key into a specific, confident, wrong number about a player's
money.

It was in **all four** trade handlers — market buy, market sell, black market
buy, black market sell — and had been the whole time. Only buy got reported,
because "you paid 0" reads as free money worth mentioning while "you were paid 0"
reads as a balance bug and gets shrugged off.

Receipts now go through `app/trade_receipt.py`, which exists to hold one rule:

> A price that is not present is reported as **missing**, never as **zero**.

A genuine zero still prints as zero — a free trade is a fact. A *missing* total
prints a warning telling the player to check their balance and report it. That is
the difference between a bug that announces itself and a bug that lies quietly
for weeks.

The receipt also fixes three smaller lies it was sitting on. The engine returns
`currency_id`; the message said "stones" regardless. It returns `balance`; the
message dropped it. And black-market trades return `detected` — whether the
player was caught committing a crime — which the player was simply never told.

### The whole boundary was then audited

One instance of a bug class this invisible is not worth fixing alone. A new
contract test (`tests/python/contracts/test_engine_result_keys.py`) parses the Go
dispatch switches, maps every action to its handler function, transitively
collects every string that function and its helpers can use as a result-map key,
and compares that against every `result.get("…")` in the matching Python handler.

Across roughly 180 actions, the **only** mismatch in the entire codebase was
`total_price`, in those four handlers. The audit is deliberately conservative —
the Go key set is over-collected, so it under-reports rather than crying wolf —
and it now runs in the suite. It also asserts that its own parsers matched
something, because a silently-empty parser is a test that passes forever.

### Equip asked for an "Equipment Id" that was never shown to the player

The hub falls back to a text modal whenever a parameter has no option provider.
`equipment_id` is a plain `int` with no autocomplete, so **Equip, Unequip and
Repair** were all asking players to type a database row id.

All three now offer live dropdowns over the player's own equipment — name, slot,
durability, quality. Equip lists only unequipped items (equipping what is already
equipped is a no-op); Unequip only equipped ones; Repair lists everything, most
damaged first, which is what someone opening Repair is looking for.
Confirmations name the item rather than saying "Equipped item `#7`".

These are registered through the hub's own `register_hub_option_provider` rather
than discord.py autocomplete, on purpose. `/equipment` is not typable — it is
reachable only through the `/items` hub — so the hub is the only consumer, and
this keeps the change entirely inside code the build sandbox can exercise rather
than relying on discord.py behaviour that cannot be verified here.

### Verification

33 new tests (22 unit, 11 contract). Six mutations proved they fail without their
fixes — including **restoring the original bug verbatim**, which fails three of
the new tests, so the boundary audit demonstrably catches the thing that shipped.
Suite baseline-diffed against v0.19.14: 395 tests versus 289, zero new failures,
identical pre-existing sandbox loader errors.


## v0.19.18 — Scene Action: no dropdowns, and it travels with the player

Two halves of one report, both correct.

### The dropdowns were a rollout gap, not a decision

The 17 command hubs moved to the Components V2 action-list layout across
v0.19.5–v0.19.7. **The Scene Action panel is not a hub**, so nothing carried it
along, and it kept both selects — "Choose what you are trying to do" and "Choose
a target" — which is precisely the shape that redesign existed to delete.

Both are buttons now: eight action buttons over two rows with the selected one
highlighted, then target buttons (Environment, Self, and everyone actually
present), then Describe & Resolve and Refresh. Target lists longer than ten page
behind a **More targets** button.

There are **no select menus anywhere in the new panel**, on purpose. A `Select`
nested inside a V2 `Container` has no precedent in this codebase —
`HubLayoutSystemSelect` was deleted rather than kept — and it cannot be exercised
in the build sandbox. Everything here uses only the patterns the 17 hubs already
prove in production.

### The panel didn't travel because it never looked again

The old view snapshotted character, location and the NPC/player target list at
construction. `_resolve_scene_action` *does* re-read the character and rejects a
target who is no longer present — so the failure mode was the worst kind of
almost-working: a player who travelled could still pick a stale NPC off the
panel, write a paragraph into the modal, submit, and only then be told the NPC
isn't there — with the panel still displaying the location they had left.

Every interaction now re-reads first:

- Travel updates the location line and says **"You travelled. The scene is now …"**
- A target who is no longer present is **reset to Environment with a note**, at
  the moment it happens, rather than being left selected to fail later
- **Describe & Resolve re-checks before opening the modal**, so nobody types a
  paragraph about a scene they aren't in
- Panel lifetime went from 5 to 15 minutes — only reasonable now that it stays
  current rather than getting staler

### The panel is its own module now, and that is the point

`app/bot/scene_layout.py` imports `discord` and **nothing else from the
project**; everything game-specific (the action profiles, how to re-read a
scene, how to open the modal) is injected from `main.py`.

That isolation is what lets the entire component tree be constructed and counted
in a sandbox with no discord.py installed. Discord's 40-component limit counts
nested components, so a panel that reads fine in source is rejected at send time
— and until now that limit could only have been discovered by a player hitting
it. Measured worst case: **33 of 40** with thirty people in the scene.

`tests/support.py` gained `install_discord_ui_shim()`, which **no-ops when real
discord.py is present**. The same tests therefore exercise the real library on a
machine that has it and the shim everywhere else, and the shim enforces
Discord's own five-components-per-row rule while building.

### Verification

41 new tests (27 unit, 14 contract). Seven mutations proved they fail without
their fixes: a stale target left selected after travel, `reload` dropped from
refresh, resolve skipping the reload check, the travel notice suppressed, a row
overflowing five buttons, a player id shown instead of a name, and a `Select`
creeping back into the panel.

One pre-existing assertion in `test_gui_integrity` was updated rather than
worked around: it verified that the event thread's Scene Action button
pre-selects its action by grepping for `view.action_key = default_action`. That
value is now passed into the factory as `action_key=default_action` — same
intent, new shape, and the test says so.

Suite baseline-diffed against v0.19.14: 436 tests versus 289, zero new failures,
identical pre-existing sandbox errors.


## v0.19.19 — unauthenticated slow-header DoS in both HTTP listeners (security)

Reported against `app/dashboard.py` and `app/health.py`: both parse HTTP by hand
with a loop of `await asyncio.wait_for(reader.readline(), timeout=N)`, with a
timeout per line and no cap on request-line bytes, header count, cumulative
header bytes, or total time.

All four confirmed. Two details make it worse than that list reads.

**Every byte of it runs before authentication.** On the dashboard the header loop
finishes before `_authorized()` is ever called. No credential is needed to reach
any of this.

**It is two different attacks.** The *slow* one exploits the fact that the
per-line timeout **resets on every line**: one header sent just under the limit —
1.9 s on health, 4.9 s on the dashboard — holds a connection open indefinitely.
The *fast* one ignores connections entirely and simply sends distinct header
names, growing `headers[key] = value` without bound; that is memory exhaustion,
and the only thing standing in its way was asyncio's **default** 64 KiB stream
limit per line, which nobody chose.

Underneath both sat a fifth gap: **neither server capped concurrent
connections**, so everything above multiplied by however many sockets an
attacker opened. And an oversized line raised `ValueError` out of `readline()`
straight into `except Exception: log.exception(...)`, writing a stack trace per
line — the same attack amplified into log flooding.

### Demonstrated rather than assumed

The v0.19.18 health server, as shipped, run on a real socket and fed one header
every 1.5 seconds:

```
VULNERABLE: held open 15.0s, 10 header lines sent, no response, connection still live
```

The same script against this build:

```
server answered after 10.0s: HTTP/1.1 408 Request Timeout {"error":"header_timeout"}
```

### The fix

A new `app/http_limits.py`, shared by both servers:

| Limit | Default | Answer |
| --- | --- | --- |
| `max_request_line_bytes` | 8192 | 414 URI Too Long |
| `max_header_lines` | 100 | 431 Request Header Fields Too Large |
| `max_header_bytes` | 16384 | 431 |
| `header_deadline_seconds` | 10 | 408 Request Timeout |
| `line_timeout_seconds` | 5 | 408 |
| `max_connections` | 64 | 503 `too_many_connections` |

Each read gets **whichever is smaller, the per-line timeout or the time left on
the whole head**. That is the part a resetting timeout could never do, and it is
what ends the slow attack; the count and byte caps end the fast one. They are
deliberately independent — closing only one leaves the other open, and the tests
exercise each in isolation.

### Two decisions worth writing down

**The stream limit is now stated, and stays above the body cap.** Bounding each
header line directly means lowering asyncio's stream limit — and that
*deadlocks* `readexactly()` for request bodies, because `_maybe_resume_transport`
refuses to resume while the buffer still exceeds the limit, so a 64 KiB body read
behind an 8 KiB limit hangs until its timeout. So the limit is set explicitly to
131072 (above the 65536 body cap both servers enforce), and cumulative header
bytes are checked **after** each line. One line can therefore overshoot the
budget by at most the stream limit. That is bounded, and it is the cheaper
correct answer.

**Rejections answer a real status line.** Before this, a rejected request went
out as `HTTP/1.1 408 OK`, because the reason-phrase map had no entry for 408 and
fell through to its default.

### Exposure, for sizing the urgency

The dashboard publishes to `${DASHBOARD_BIND_ADDRESS:-127.0.0.1}:8090`, so by
default it is loopback-only on the NAS. **If you set
`DASHBOARD_BIND_ADDRESS=0.0.0.0`** to reach it from a laptop, this was reachable
unauthenticated from the whole LAN. The health listener binds `0.0.0.0` inside
its container and is not published, so it was reachable from other containers on
the compose network.

### Verification

48 new tests. The integration tests start the **real** `HealthServer` on a real
port and dribble headers at it over a socket; they run in 2.7 s and were checked
for stability across repeated runs. Eight mutations proved every limit is
load-bearing: the absolute deadline, the header-count cap, the cumulative byte
cap, the request-line cap, the `ValueError` mapping, the connection cap,
releasing the slot in `finally`, and the explicit stream limit.

Suite baseline-diffed against v0.19.14: 473 tests versus 289, zero new failures,
identical pre-existing sandbox errors.


## v0.19.20 — acting on what `ai_status` found: 90.9% procedural fallback

The panel shipped in v0.19.15 did its job on day one. Production numbers:
**11 narration requests, 1 served by AI, 10 procedural fallbacks.**

And `granted 15, refused 0` — our own 20/min ceiling never fired once. Every 429
came from OpenRouter's shared free pool, not from us.

### Two failures, one label

**Both Gemma routes** returned 429 "temporarily rate-limited upstream", four
failures each, and — the expensive part — **`skipped while cooling 6` each**. A
blanket 60-second cooldown turning a transient provider blip into a minute of
procedural prose.

**`openrouter/free` had never once worked.** Six attempts, zero successes, and
its error was not a 429 at all: `ValueError: AI provider returned an empty
response`. The last-resort route in the chain was a delay and a wasted request.

### The arithmetic nobody was tracking

OpenRouter's free tier is **20 requests per minute *and* 50 requests per day**
under $10 of lifetime credits — 1000/day at $10 or more. Only the per-minute half
was ever tracked.

Eleven narrations cost **fifteen upstream attempts**, because every failed route
walks to the next one and each walk spends a daily slot. That is 30% of a 50/day
allowance for eleven narrations, nine of which produced nothing.

### Why the dynamic router returns nothing

`openrouter/free` is a **random router over the free model pool**, and that pool
is now largely *reasoning* models. Routine narration asks for 165–190
`max_tokens`. A reasoning model handed 180 tokens spends all of them reasoning
and returns **empty content**, with the text — if any — in a separate reasoning
field this code never read.

Two fixes, in order of importance:

1. **The dynamic route now gets at least 700 output tokens**, so content has room
   to exist at all. This is the actual fix.
2. **If content is still empty, prose is salvaged from the reasoning field.**
   This is the salvage path, not the plan.

The salvage is **guarded**, and this is a judgement call worth stating plainly:
raw chain-of-thought that opens "Okay, the user wants a marsh description. Let me
set the scene." is *worse* than the deterministic procedural fallback. Reasoning
text that reads as scratchpad is discarded and the chain moves on. Tested both
ways — prose is kept, thinking-out-loud is refused.

### `Retry-After` instead of a guess

OpenRouter sends a `Retry-After` header when a provider offers a retry hint. The
router now uses it (clamped to 300s), falling back to the old multiplier only
when no hint is given. A three-second hint no longer parks a route for sixty.

### Daily budget: track and stop

New `OPENROUTER_MAX_REQUESTS_PER_DAY`, defaulting to **50** — the real free-tier
floor, not an optimistic guess. Once spent, the router refuses locally with a
named error instead of walking three routes to discover the same thing three
times.

It is a **cost saver, not an authority**: the counter lives in memory and resets
when the bot restarts, while the real limit does not. Upstream stays the source
of truth; this only stops us paying for requests we already know will be refused.

### What `ai_status` now distinguishes

- Daily budget used and remaining, with a bar and an 80% warning
- **`NEVER SUCCEEDED`** on any route with attempts but no successes — a route
  that has never worked is not a fallback, it is a delay plus a wasted slot
- Empty replies counted **separately from failures**, with how many were
  recovered from reasoning

### The two things no code change can do

1. **Put $10 of credits on the account**: 50/day → 1000/day, then set
   `OPENROUTER_MAX_REQUESTS_PER_DAY=1000`.
2. **Add a provider key** at `openrouter.ai/settings/integrations` — the 429
   message says so itself. A Google AI Studio key routes the Gemma models through
   your own Google quota instead of the shared pool, and it has its own free tier.

### Nothing was ever broken

Canonical mechanics resolved, players received deterministic prose, no error
reached anyone. This is a quality degradation — precisely the failure mode
`ai_status` exists to expose, and it was completely invisible before v0.19.15.

### Verification

23 new tests. Eight mutations proved every fix is load-bearing: the daily cap,
the `Retry-After` read, its clamp, reading the reasoning field, the scratchpad
guard, the token bump, empty-response counting, and the never-succeeded flag.
Suite baseline-diffed against v0.19.14: 496 tests versus 289, zero new failures.


## v0.19.21 — the equipment dead end, and the wrong path a new test found

A player carrying a Spirit-Iron Sword clicked **Equip** and got:

> Equip has no available equipment id options right now.

True, and useless. Equip operates on **bound** equipment; the sword was still a
carried item. The real answer — "run Bind first" — was something the panel had no
way to say.

### The chain was unreachable end to end

v0.19.17 gave Equip, Unequip and Repair live pickers. **Bind did not get one** —
and Bind is the first rung, the action that creates bound equipment at all. It
still opened a text box asking the player to type an internal id like
`spirit_iron_sword`. So:

> Bind asks for a typed id → nothing ever gets bound → Equip is permanently empty
> → the empty message doesn't mention Bind.

Bind now lists the carried items that are **actually equipment** (present in
`EQUIPMENT_DEFINITIONS` — a Spirit Herb is not offered), with quantity, slot,
stat bonuses and durability.

### Empty pickers now name the prerequisite

New `register_hub_option_hint()` in `hubs.py`, so an empty live picker can
explain itself rather than stating a fact the player cannot act on. All four
equipment actions use it: Bind points at Market and Craft, Equip points at Bind,
Unequip says nothing is equipped, Repair points at Bind.

### The new test caught shipped copy pointing at a command that doesn't exist

Writing those hints I got a page label wrong myself — `/economy → Market → Buy`
when the page is called **Local Market**. The existing hint test only catches the
arrow-*less* form (`/family leave`); a wrong label *after* an arrow reads as
correct to a reviewer and is a dead end to a player.

So that test now validates every `/hub → Page → …` reference against the real hub
definitions. It caught my mistake — and one that had already shipped:

> You may use **/sect → Shadow / Special → Accept Initiation**

`/sect` has three pages — Sect, Territory, War — and no action called "Accept
Initiation". The real path is **`/sect → Sect → Shadow`**, then choose "Accept
initiation". That line sits at the hidden-sect initiation gate: precisely where a
player who has finally met a karma requirement goes looking for what to do next.

### One pre-existing test error removed

Both hint tests parse every bot module with `ast`. `main.py` uses a PEP 701
f-string that Python 3.11 cannot parse, so the older test **errored** on 3.11 —
it is one of the 14 baseline errors. Both now skip what the running interpreter
cannot parse, assert that something *was* parsed so the scan can never go
silently empty, and still re-raise on 3.12, the interpreter the bot actually
ships on. Baseline errors on 3.11: **14 → 13**.

### Verification

Six mutations proved each fix load-bearing: the bind provider, bind offering
non-equipment items, each empty hint, and a wrong page label in a hint. Suite
baseline-diffed against v0.19.14: 501 tests versus 289, zero new failures, one
fewer pre-existing error.


## v0.19.22 — the expedition journal opens when you step back into the world

Reported by a player well before the GUI work: leave the birth household, and
there is no personal thread. It only turned up later, after running `/explore`.
The exit worked and the location was correct — the room a player's own scenes get
written into simply did not exist yet.

This was queued and then genuinely forgotten while other reports came in. Not a
regression; it had never worked.

### It was three handlers, not one

`PRIVATE_LOCATION_EXITS` lists four private locations and the command that steps
out of each — birth household, sect residence, own property, personal world. All
of them land the player back in the shared world, so all of them need the
journal. Only the family one was reported, and **none of the three handlers had
it**:

| Exit | Handler |
| --- | --- |
| `/family → Leave` | `birth_family_leave` |
| `/abode → Leave` | `abode_leave` (covers `abode:` and `sect_abode:`) |
| `/innerworld → Leave` | `innerworld_leave` |

One shared helper now runs at the end of each.

### Three deliberate choices in that helper

**The character is re-read.** Every caller fetched its copy *before* the engine
moved them, so reusing it would stamp the journal with the household they just
walked out of — the same stale-snapshot mistake the Scene Action panel had in
v0.19.18.

**If they are still inside a private location, nothing opens.** That exit stepped
into another private place, and the world journal is not the right room yet.

**It runs after the confirmation and never raises.** The exit is already
committed canonically by then; a thread that cannot be created deserves a quiet
log, not a failed action or a stalled interaction.

### The test derives its own scope

Rather than naming the three handlers, the contract test reads
`PRIVATE_LOCATION_EXITS` and checks the leave handler behind each entry. A fifth
private location added without wiring its exit fails there instead of in a
player's face.

One test bug found and fixed while writing it, worth recording because it is a
common shape: the ordering check first measured the **first** reply in each
handler — which is always the `GameEngineError` branch, and therefore always
precedes the helper no matter where the helper sits. It passed whatever the code
did. It now measures the **last** reply, and a mutation that moves the helper
before the confirmation is correctly caught.

### Verification

8 new tests. Seven mutations proved each part load-bearing: each of the three
handlers dropping the call, the stale-character reuse, the private-location
guard, the never-raises guard, and the ordering. Suite baseline-diffed against
v0.19.14: 509 tests versus 289, zero new failures, one fewer pre-existing error.


## v0.19.23 — split stage 2: `/family` leaves main.py

A move, not a change. `main.py` 13,177 → **12,786** lines; `runtime.py` 340;
the new `app/bot/commands/family.py` 600, holding the group, 14 leaf commands
and one helper.

### Stage 1 cost two failed deploys, and both shaped this one

**v0.19.12** — a module-level name used above its assignment. Everything moved
here is a function or a literal in its original order, and `DefinitionOrderTests`
now covers the new module.

**v0.19.13** — `NameError: name 'app_commands' is not defined`. `functools.wraps`
cannot copy `__globals__`, so a callback wrapped by a decorator from another
module carries *that* module's namespace — and discord.py evaluates annotation
*strings* against it.

That is live here. **Eight of the 14 family handlers** are wrapped by
`serialized_user_action`, which lives in runtime.py, and the import check
confirms it:

```
birth_family_child     callback globals = app.bot.runtime
birth_family_leave     callback globals = app.bot.runtime
…
birth_family_view      callback globals = app.bot.commands.family
```

Every annotation they use — `discord.Interaction`, `app_commands.Choice[str]`,
`app_commands.Range[int, 1, 20]`, `int`, `str` — resolves in runtime.py. Checked
explicitly rather than assumed.

### The footprint was measured before anything moved

23 free names in the block: 8 already in `runtime.py`, 7 ordinary package
imports, and 8 defined in `main.py`. Only the last group needed a decision:

| Name | Decision |
| --- | --- |
| `family_group` | moves to family.py; main imports it back |
| `_current_birth_family` | used **only** by `/family` — moves with it |
| `CHILD_CULTIVATION_AWAKENING_AGE` | used **only** by `/family` — moves with it |
| `GENDER_CHOICES` | also used by `/begin` and the admin override → moves to `runtime.py` rather than being duplicated |
| `SIM` (31 other uses), `_get_thread` (5), `ensure_birth_family_household_thread` (3), `open_expedition_thread_after_exit` (3) | stay in main.py; imported **inside** the one function that needs each |

Those last four are function-body imports on purpose: a module-level
`from ..main import` is a genuine cycle, while a call-time import runs long after
main.py has finished importing. There are exactly four, one use each, and a test
asserts both the list and that none sits at module level.

The distinction is real rather than stylistic — promoting one to module level
reproduces, in the stub-import harness:

```
ImportError: cannot import name 'SIM' from partially initialized module
'app.bot.main' (most likely due to a circular import)
```

Two independent guards: the harness and the test.

### A test caught the move, for the wrong reason

`test_private_location_exits` went red. It scanned `main.py` only, so when
`birth_family_leave` moved it concluded `/family` had stopped opening the
expedition journal. The code was correct; the test's scope was stale.

That is precisely the mistake documented at the top of
`test_player_facing_command_hints.py` — *"a check anchored to one file would
quietly stop covering them the moment they move"* — which I wrote, and then
repeated two releases later. Both that test and the engine-result-key boundary
scan now read the whole `app/bot` package, so stage 3 cannot silently drop
coverage the way this one nearly did.

### Verification

9 new tests. Five mutations proved them load-bearing: a module-level main import,
a promoted deferred import, a duplicated `GENDER_CHOICES`, a stale group left in
main, and a handler left behind. Suite baseline-diffed against v0.19.14: 518
tests versus 289, zero new failures, one fewer pre-existing error.


## v0.19.24 — per-route rate limits

Provider caps are **per model**. Google allows Gemma 4 roughly **15 requests
per minute and 1500 per day, per model**. This build had a single account-wide
ceiling of 20/min, which is simultaneously:

- **too high** for one route — 20 > 15, so we call out and get refused; and
- **too low** for the fleet — two Gemma routes are entitled to 30 between them.

Production showed exactly that. Both Gemma routes were cooling down
independently while the account limiter reported `granted 15, refused 0` — it had
never fired once. The thing throttling narration was invisible to the thing meant
to prevent it.

There was a second error in the other direction. v0.19.20's daily budget default
of **50** is right for OpenRouter's shared free pool and **30× too low** for a
provider key: with a Google key each route is entitled to 1500/day, so our own
counter would have cut narration off at 50 — the feature added to *save*
requests becoming the only thing refusing them.

### Two layers, because the topology has two

| | Per minute | Per day | Enforced by |
| --- | --- | --- | --- |
| Account-wide | 20 | 50 | OpenRouter's free tier |
| **Per route** | **15** | **1500** | **the provider, per model** |

Both are real and both are kept.

### A capped route is skipped, not called

Previously the only way to discover a cap was to spend an upstream attempt and be
refused — which also spent a daily slot *and* started a 60-second cooldown.
Skipping costs nothing and moves straight to the next model, which has its own
quota.

A skipped route is **not** recorded as a failure. It hasn't failed; it just isn't
its turn. That keeps the "never succeeded" flag from v0.19.20 honest.

`ai_status` now prints each route's own ceiling, which of the two is closest, the
headroom left, and how many calls were held back before dialling out.

### Defaults right in both regimes

15/min and 1500/day are the documented Gemma free-tier figures. On the shared
pool the account daily cap of 50 binds first and these never fire; with your own
provider key they *are* the ceiling. One set of numbers works either way, because
the account layer covers the first case and the route layer the second.

### What no code change can do

None of this raises a provider cap. A key at
`openrouter.ai/settings/integrations` moves these models off OpenRouter's shared
pool and onto your own Google quota — which is what the 429s were asking for. If
you add one, raise `OPENROUTER_MAX_REQUESTS_PER_DAY` as well: at 1500 per route,
the account default of 50 becomes the binding constraint.

### Verification

9 new tests. Seven mutations proved them load-bearing: the route check removed,
one shared window instead of per-route, a capped route counted as a failure, each
ceiling ignored in turn, drifted defaults, and a mis-reported binding ceiling.
Suite baseline-diffed against v0.19.14: 527 tests versus 289, zero new failures.

## v0.19.25 — ack-before-mutate, `/explore` road frontier, and live travel countdowns

A production-review pass covering five things: a real Discord interaction-ack
architecture bug across 84 handlers, three smaller findings from the same
review, a silently-broken contract test discovered while verifying that
review, `/explore`'s location-discovery logic picking from anywhere in the
world instead of the road network, and converting `/travel`'s displayed time
from raw game-minutes to a live, self-updating Discord timestamp plus a new
`/travel status` command.

### 84 interaction handlers could commit a mutation and never tell the player

Discord invalidates an interaction token if it receives no initial response
(`response.send_message`/`defer`/`send_modal`) within roughly three seconds.
79 command handlers across `main.py` and `family.py` called
`ENGINE.authoritative_action(...)` — a real, committed Go mutation — *before*
acknowledging the interaction at all. Ordinary latency (a slow narration call,
a busy event loop) could push that first response past the window: the
mutation lands, but the reply that would have told the player never goes out,
because the token is already dead. The player sees nothing, assumes the
command failed, and retries — and the retry is a second legitimate mutation
from what looked like a single click.

Fixed by making `await interaction.response.defer(ephemeral=False)` the
literal first statement in all 79 handlers, and converting each handler's own
`interaction.response.send_message(...)` calls to `interaction.followup.send(...)`
(163 call-site rewrites total) — deferring twice or calling `send_message`
after a defer both raise `discord.InteractionResponded`, so both had to move
together. Two now-redundant later `defer()` calls (`breakthrough`, `check`)
were removed.

A fifth, related case not in the original review: `require_character()`, the
shared guard-clause helper nearly every handler calls first, can itself
trigger a slow authoritative call (`lifecycle.true_death`, for a character who
aged past their lifespan) before returning — with no ack of its own. It now
defensively defers before that call if the caller hasn't already, and its own
replies route through a new `respond()` helper in `app/bot/runtime.py` that
checks `interaction.response.is_done()` rather than assuming either state, so
it stays safe to call from both already-deferred and not-yet-deferred
handlers.

**Verification:** a new permanent static guard,
`tests/python/contracts/test_ack_before_mutation.py`, AST-scans every
function under `app/bot` for one that calls `authoritative_action(` before its
own first ack call, by source line (excluding nested function bodies, so a
helper's own internal ack doesn't falsely clear a caller that never acks
itself). Mutation-tested: manually removing a single handler's `defer()`
correctly fails the guard, confirming it actually detects the shape rather
than passing regardless.

### A pre-existing contract test had been silently broken since the `/family` split

While verifying the above, `tests/python/contracts/test_authority_boundary.py`
turned out to have been asserting nothing since `/family`'s handlers moved
into `app/bot/commands/family.py`: it scanned `main.py` only, so
`birth_family_enter` and its siblings were no longer in scope, and the file's
own assertions on them silently stopped running. Worse, this session's
existing custom test runner never caught it either — `unittest.TestLoader`
silently discovers zero tests from a file that defines bare `def test_*():`
functions with no `TestCase` class, which is this file's own style, so the
runner reported a clean suite while actually never executing it at all.

Both are now fixed: the test's scan widened from `main.py` alone to every
`.py` file under `app/bot` (matching the same file-scope mistake and fix
already documented in "A test caught the move, for the wrong reason" above,
now repeated a second time in a different test), and a second file-discovery
script now runs bare pytest-style test modules that the primary runner drops.

### Three smaller findings from the same review

- **`EventSceneView`'s timeout cap was shorter than some events it serves.**
  Its remaining-time clamp was `max(300, min(21600, ...))` — a 6-hour ceiling
  — while secret-realm events can run up to 12 hours. Raised to 48 hours
  (`min(172800, ...)`), comfortably covering the longest events without
  removing the floor that protects against a garbage `expires_at`.
- **`operational_health_worker`'s exception handling killed monitoring
  permanently after one failure.** The `try`/`except` wrapped the entire
  `while` loop from outside, so any single iteration's exception exited the
  loop for the rest of the process's life. Restructured so the exception
  boundary sits inside each iteration instead — one bad iteration logs and the
  loop continues on its normal `asyncio.sleep(30)` cadence, while
  `asyncio.CancelledError` still propagates cleanly for shutdown.
- **`spawn_event_thread` could return a thread the database never registered.**
  If `DB.register_event_thread(...)` failed, the function still handed back
  the live, unregistered Discord thread — orphaned from the systems that
  track it. It now retries registration once, and on a second failure warns
  in the thread and archives/locks it instead of returning an orphan.
- **`on_app_command_error`'s generic message overclaimed.** It told players
  "no game-state change was intentionally applied" on any unhandled error —
  false whenever the exception fired *after* a successful
  `authoritative_action` call, during later narration or presentation work.
  Reworded to tell the player to check their actual state before retrying,
  rather than assert a guarantee the code can't back up.

### `/explore` discovered locations from anywhere in the world, not the road network

`discoverNextLocationTx` (Go) picked a random unknown location from the
*entire current world* on a successful exploration roll — so exploring from a
starting town with no roads of its own (Greenriver Town) could just as easily
surface a city several road-hops away as one actually connected to anything
the character knows. The intended design — explore to progressively chart the
road network outward from what you already know — wasn't what the code did.

Fixed with a new `roadFrontierTx` helper: a discovery candidate must now be a
road-neighbor of a location the character already knows (or a realm-hub city
their realm qualifies for), not merely unknown. Discovery is still
progressive — each successful find expands next time's frontier by that
location's own road neighbors, so repeated exploring genuinely charts the map
outward rather than teleporting discovery anywhere at once.

**Verification:** two new Go tests. The first proved the very first
exploration from a fresh character stays within the starting road frontier
(and specifically that a distant, unconnected city never comes back on turn
one); the second proved the frontier legitimately expands once a first-ring
city is known. Mutation-tested against the old whole-catalog-scan behavior —
both fail correctly when reverted.

### `/travel` now shows a live Discord timestamp instead of a bare game-minute

Requested directly: travel time was displayed as a raw game-minute figure
("Arrival **game minute 4830**"), which means nothing to a player without
mentally running the world-clock conversion themselves. The Go engine already
tracks the canonical mapping between game-minutes and real Unix time (the
`world_clock` row in `world_state`); the fix exposes it end to end.

Two new Go helpers convert a game-minute to a real timestamp:
`readCanonicalWorldClock` (a read-only variant of the existing canonical-clock
reader, used by queries) and `realTimestampForGameMinute` (the anchor-inverse
of the existing game-minute-from-real-time formula). `exploration.travel`'s
result now carries `departure_unix_ts`/`arrival_unix_ts` alongside the
existing game-minute fields whenever the world clock can be read — best
effort, so a frozen clock (`WORLD_TIME_SCALE=0`) or an unreadable clock row
still returns the game-minute fields with no timestamp, rather than failing
the whole travel action.

`/travel`'s Discord reply now renders `<t:UNIX:R> (<t:UNIX:t>)` — Discord's
own timestamp markup, which renders live and auto-updating, in each viewer's
own local timezone, entirely client-side — falling back to the old
"game minute N" text only when no timestamp could be resolved.

### New `/travel status`

A new query, `exploration.travel_status`, reads the same in-transit state
`/travel`'s road-journey gate already tracks (`road_transit:{userID}` in
`world_state`) without mutating anything, and reports whether the character
is currently traveling, their destination, and a real arrival timestamp built
the same way. `/travel` is now a command group (`/travel go`, `/travel
status`) rather than a single root command, matching the same
group-with-multiple-leaf-actions pattern already used throughout the hub
system (e.g. `/sect`'s four distinct `status` subcommands) — the in-game
"🗺 Travel Hub" surfaces both as separate actions on its existing Destinations
page, so this is additive rather than a change to how players already reach
travel.

**Verification:** two new Go unit tests for the timestamp-conversion math
(linear conversion, and the frozen-clock edge case) plus one end-to-end Go
test covering an idle check, a hub fast-travel (no transit expected), a real
road journey, a mid-journey `/travel status` check, and the transit state
correctly clearing itself once the arrival minute passes. Mutation-tested:
reverting the real-seconds scaling in `realTimestampForGameMinute` fails both
the unit test and the end-to-end test's delta assertion, confirming the
conversion is actually exercised. On the Python side, the new
`test_ack_before_mutation.py` guard (see above) also covers `/travel status`
by construction — it's a read-only query, calls no `authoritative_action`,
and the guard confirms it isn't mistakenly flagged.

### Verification

Go: `go build ./...`, `go vet ./...` and `go test ./... -count=1` all clean,
including 6 new Go tests across two new files (`road_frontier_discovery_test.go`,
`travel_status_test.go`), all mutation-tested against a reverted version of
their respective fix.

Python: this sandbox's default interpreter (3.11) can't parse `main.py`'s one
PEP 701 f-string, so the full suite was run in two parts and cross-checked —
527 tests clean under python3.11 (one pre-existing failure: the release
manifest, expected before this release's own manifest regeneration; zero
newly-introduced failures), plus the 13 tests and 2 modules that only import
cleanly under python3.12 (including the newly-fixed `test_authority_boundary.py`
and the new `test_ack_before_mutation.py`, both bare pytest-style files this
sandbox's `unittest`-based runner silently drops) independently confirmed
passing directly under python3.12, which is also the interpreter this
codebase's own `requirements.txt`/CI actually target.

## v0.19.26 — a better GM admin dashboard: 9 new Admin Console controls

Requested directly ("better admin dashboard"), narrowed through follow-up to: the web
GM dashboard's Admin Console tab specifically needed more controls — GM actions that
previously required touching the database by hand. All four requested categories are
covered: character-sheet editing, NPC/world-state editing, player moderation, and
bulk/server-wide actions.

Before this release the Admin Console had 9 single-purpose actions (teleport, grant
currency, karma, fate, revive, clear-battle, automation toggles, world-time advance,
sim-interval) plus 4 DB-maintenance actions — all routed through one established
pattern: a Go `admin.*` operation (a handler that mutates inside a transaction and
calls `auditAdmin()` before committing) exposed via `AdminDashboardController.ACTION_MAP`
in `app/dashboard.py`, with a matching form section in `dashboard/app.js`'s
`loadAdmin()`. Every control below follows that same pattern — no new plumbing, just
more instances of the existing one.

### Character-sheet editing — 3 new operations

- **`admin.player.set_realm`** — directly sets a character's `realm_index` and `phase`
  (a story correction), bypassing normal breakthrough gating. Validated against the
  content pack's real bounds (0-31 for realm, 1-9 for phase).
- **`admin.player.set_resource_caps`** — directly sets `vitality_max` and/or `qi_max`.
  Either field can be omitted (only the supplied one changes); lowering a cap clamps
  the character's current value down to match so it never reads above its own maximum,
  while raising a cap deliberately does not also refill the resource.
- **`admin.player.adjust_item`** — grants or removes an inventory item by a signed
  quantity delta in one op (mirroring how karma/fate already take signed deltas),
  floored at 0 on removal rather than erroring.

### NPC / world-state editing — 2 new operations

- **`admin.npc.relocate`** — force-moves an NPC's `current_location`. NPCs were
  previously entirely simulation-owned with zero admin write path at all.
- **`admin.world_event.end`** — ends an active world event early, rather than waiting
  for its natural expiry or throttling the whole automatic-event cadence.

Deliberately not built this pass: creating a new NPC from scratch (it would need to
replicate a meaningful slice of the simulation's own bootstrap logic — name/location/
profession assignment plus life-state and civilization-state rows together — rather
than a simple single-table mutation like everything else here), and editing sect/
family/faction political state directly. Both are real gaps, flagged for a future pass
rather than shipped half-built.

### Player moderation — 3 items, no schema change

- **`admin.player.reset_cooldowns`** — clears all of a player's `cooldowns` rows, or
  just one named action.
- **`admin.player.force_end_scene`** — resets a stuck `player_scene_state` row back to
  `scene_type='world'` **at the player's current location**, without teleporting them —
  for when a player is stuck in a bad scene state but teleporting them away isn't the
  right fix (unlike `admin.player.teleport`, which resets the scene only as a side
  effect of also relocating the character).
- **Fixed and extended `admin.player.clear_battle`.** It previously only abandoned the
  `battles` table, silently leaving a player stuck in group combat
  (`boss_encounters`/`boss_participants`) or PvP (`pvp_matches`/`pvp_challenges`)
  completely untouched. It now also marks any active boss encounter the player
  participates in as abandoned, and any active PvP match or pending PvP challenge
  involving them as abandoned/cancelled, in the same transaction.

Deliberately not built this pass: mute/freeze (needs a new `characters` column, a
schema migration, and a new centralized enforcement checkpoint in Go's dispatch — a
larger, separate change) and "roll back an action" (no safe generic mechanism exists
anywhere in this architecture — only an append-only event ledger, not a reversible
one; the practical equivalent for "a mistake happened" is the existing/new karma, fate,
currency, realm, and resource-cap correction tools above).

### Bulk / server-wide actions — 2 new operations

- **`admin.bulk.grant_currency`** — applies a currency grant to every character in one
  transaction, with a single `admin_audit_log` row (`target="all"`) rather than N
  round trips and N audit rows for one GM decision.
- **`admin.bulk.reset_cooldowns`** — clears cooldowns for every character, same
  rationale.

No bulk primitive existed before this pass — `admin.world.advance_time` only *looks*
bulk because it's one shared clock row, not a per-character loop.

### New `GET /api/player?user_id=` detail endpoint

A GM needs to see what they're about to edit before editing it. This new endpoint
mirrors the existing `GET /api/npc?name=` drawer pattern: the full `characters` row,
current sect membership, inventory, cooldowns, and scene state for one player. The
dashboard's new "Player Detail" control opens it as a drawer, reusing the same
`showNpc()`-style drawer mechanism already used elsewhere. Registered in
`DASHBOARD_GET_API_PATHS` alongside `/api/npc` (same treatment — a detail view, not a
nav-tab view).

**Verification:** 11 new Go tests in `admin_actions_test.go` (one per new operation,
plus the extended `clear_battle` coverage), all passing and all mutation-tested —
each fix/feature was reverted and its test confirmed to fail, then restored and
reconfirmed passing (12 mutations total, including a caught test bug of its own: the
first version of the bulk-currency-grant test wrongly asserted an absolute balance
instead of the correct additive-grant semantics that `admin.player.grant_currency`
already used — caught by the mutation-testing pass itself, not a mutation). `go build
./...`, `go vet ./...` and `go test ./... -count=1` all clean. On the Python/frontend
side: `tests/python/integration/test_dashboard.py::test_static_dashboard_contains_real_admin_console`
extended with a presence assertion for every new action name (also mutation-tested —
temporarily renaming one action in `app.js` correctly fails the test), the dashboard
implementation contract gate stays clean, and the full suite was re-run and
baseline-diffed against the pre-change tree with zero new failures (same pre-existing
python3.11/3.12 PEP-701 split documented above, plus the expected release-manifest
mismatch ahead of this release's own manifest regeneration).

## v0.19.27 — sect membership/rank and progression-detail admin controls

Requested directly, narrowed through follow-up to two of the gaps flagged by a
system-wide audit after v0.19.26: **player sect membership & rank** (sect political
stats, inter-sect relations, and NPC faction reassignment stay out of scope), and
**all four** progression-detail items — realm/body-realm perfection progress,
spiritual root (grade/purity/mutation), bloodline & physique, and tribulation state.

Same established pattern as v0.19.26: one Go `admin.*` operation per control (mutate
inside a transaction, call `auditAdmin()` before committing), exposed via
`AdminDashboardController.ACTION_MAP` in `app/dashboard.py`, with a matching form
section in `dashboard/app.js`'s `loadAdmin()`. All six data points already had
full-column tables with no `CHECK` constraints, so this pass needed no schema
migration — six new operations on the existing shape.

### 6 new operations

- **`admin.player.set_sect`** — assigns, re-ranks, or removes a player's
  `sect_membership` row (a `remove:true` payload flag picks the remove branch, the
  same mode-flag precedent `admin.player.reset_cooldowns` established). The "set"
  branch auto-vivifies the `sects` row (`INSERT ... ON CONFLICT DO NOTHING`) so it
  never fails on a sect not yet recorded there, mirroring what Python's own
  `set_sect_membership()` already does. `sect_name`/`rank_name` are free text — there
  is no canonical rank ladder anywhere in the codebase to validate against, only ad
  hoc values on recruitment — and `rank_level` is clamped 0-100. A rank change
  preserves the original `joined_at`; only a brand-new membership stamps it fresh.
- **`admin.player.set_realm_perfection`** — sets `progress` (0-100, clamped) on
  `realm_perfection` or `body_realm_perfection` for one `(user_id, realm_index)`,
  picked via a `track: "cultivation"|"body"` field. Upserts, since the row may not
  exist yet if the player never started that realm's perfection quests.
- **`admin.player.set_spiritual_root`** — sets `grade` (validated against the exact
  enum from `content/world.json`'s `spiritual_root_system.grades[]`: Mortal, Common,
  Refined, Earth, Heaven, Immortal), `purity` (0-100 clamped), and `mutation` (free
  text, capped at 200 chars). Deliberately narrow: `elements_json`, `stability`,
  `refinement_progress`, and `compatibility` are left untouched on both the update and
  the upsert's first-time-creation path. Upserts, for legacy characters predating the
  table.
- **`admin.player.set_bloodline`** — sets `purity`/`evolution_stage`/`progress` on one
  existing `character_bloodlines` row, identified by `(user_id, bloodline_id)` since a
  character can hold several. Purity and progress clamp 0-100; `evolution_stage` only
  floors at 0 — there's no fixed upper bound anywhere in Go, it's content-pack-
  dependent per `bloodline_id`. **Requires the row to already exist** (errors rather
  than upserting) since `name`/`affinity`/`state` need real content-pack values an
  admin op has no safe way to invent.
- **`admin.player.set_physique`** — the same purity-style fields
  (`evolution_stage` floored at 0, `progress`/`stability` clamped 0-100) on the
  character's single `character_physiques` row (1:1, always present). Also requires
  the row to exist rather than upserting.
- **`admin.player.set_tribulation`** — `mode: "clear"|"reset"` on `tribulation_state`
  for `(user_id, gate_realm_index)`, validated against the existing `tribulationGates`
  map (realms 7/15/23). `clear` sets `cleared=1, preparation=0, last_result='cleared'`
  and deliberately leaves `attempts` alone — a pure historical counter — and is the
  single highest-value fix in this pass, since `cleared=1` is what unlocks the
  breakthrough past that gate for a permanently-stuck player. `reset` wipes
  `preparation=0, attempts=0, cleared=0, last_result=''` for a clean re-attempt.
  `updated_game_minute` comes from `canonicalWorldGameMinute(conn)`, the same helper
  every other gameplay write in the package already uses for "now" in game-minute
  terms.

Deliberately not built this pass, per the scoping decision: sect political stats
(influence/cohesion/resources/treasury), inter-sect relations, and NPC sect rank/
faction reassignment. `sect_membership.contribution_points`/`influence` and
`character_spiritual_roots.elements_json`/`stability`/`refinement_progress`/
`compatibility` stay untouched by the new ops — narrower scope than the full gameplay
write path, same philosophy `admin.player.revive` already uses by not replicating
every gameplay side effect.

### Dashboard

Six new `<section>` controls in the Admin Console: Sect Membership (with a `<datalist>`
of known sect names, since sect names aren't DB-enforced and a GM might reference a
custom one), Realm Perfection Progress, Spiritual Root, Bloodline, Physique, and
Tribulation Gate (with the three real gate names — Mortal Ascension, Transcendence,
and Celestial Ascension Tribulation — rather than bare realm numbers). `snapshot()`
gained a `sects` list, read from `sect_politics_state` with a `content/world.json`
fallback, mirroring the existing `locations` fallback exactly.

**Verification:** all 6 new Go tests in `admin_actions_test.go` pass and are
individually mutation-tested — each op's core guard (clamp, floor, upsert-vs-require-
existing, `joined_at` preservation, `attempts` preservation on clear) was reverted,
confirmed to fail its test, then restored and reconfirmed passing. One of those
mutation passes caught a real test gap of its own: the first version of the physique
test never exercised a negative `evolution_stage`, so its floor-at-0 guard had zero
coverage — the mutation ran clean even with the floor removed. Fixed by mirroring the
bloodline test's negative-input case; the mutation now correctly fails. `go build
./...`, `go vet ./...`, and `go test ./... -count=1` all clean. On the Python/frontend
side: `test_static_dashboard_contains_real_admin_console` extended with a presence
assertion for every new action name, also mutation-tested (the first mutation attempt
— appending characters to the action string — was itself a false pass, since the
original substring still matched; corrected to mutate the substring itself, which then
correctly failed). The dashboard implementation contract gate stays clean, and the
full suite was re-run and baseline-diffed against the pre-change tree with zero new
failures (same pre-existing python3.11/3.12 PEP-701 split documented above, plus the
expected release-manifest mismatch ahead of this release's own manifest regeneration).

## v0.19.28 — database restore and a debuff/condition clear control

Requested directly, picking two items off a system-wide gap audit: a working restore
path to go with the Admin Console's existing backup button (previously backup-only,
with no automated way back), and a way to clear a stuck debuff/condition without
touching the database by hand.

### `POST /v1/db/restore` — the Go engine's first online restore

Every earlier release in this line only ever *created* backups
(`Conn.BackupTo`/`POST /v1/db/backups`) — nothing ever read one back in. The new
`Conn.RestoreFrom` (`go_core/internal/storage/sqlite.go`) is the online-backup API run
in reverse: source and destination swap roles from `BackupTo`, with the backup archive
opened strictly read-only so a restore can never mutate the archive's actual page
content. Two design decisions carry the actual safety of this feature:

- **A restore always takes its own "just in case" backup of the CURRENT state first**,
  before touching anything. A restore that turns out to be the wrong call is then
  itself just one more restore away from being undone — this is deliberately not an
  optional step.
- **Every open db-session is force-closed before restoring**
  (`s.sessions.CloseAll()`). A session holds its own long-lived connection and can be
  sitting mid-transaction; without this, the restore's backup step would have to fight
  that connection for the write lock, or — worse — complete successfully only for the
  session to immediately overwrite the just-restored state with stale in-flight data
  on its next write.

The endpoint only ever accepts a bare filename matching the exact convention
`dbBackups`' own listing already enforces (`xianxia-*.sqlite3`); `filepath.Base`
collapses any path-traversal attempt before that check even runs, so a restore can
only ever target a file that endpoint would itself have offered.

One real bug turned up during development, not shipped: `RestoreFrom`'s read-only
open of a backup archive was leaving `-wal`/`-shm` sidecar files behind in the backups
directory. The cause is a quirk of the already-shipped backup feature, not something
new — `BackupTo` copies the source's page-1 header verbatim, and the live database is
WAL-mode, so every backup archive silently carries the "this database wants WAL"
header flag even though nothing ever writes to it. Opening it — even read-only — makes
SQLite provision the WAL index it needs to read consistently, leaving the sidecar
files behind. `RestoreFrom` now cleans those up itself so a backup archive stays the
single self-contained file a GM expects to find in the backups list.

The dashboard's Admin Console gained a **Restore** button on every row of the existing
backups table, gated behind a confirmation that names the target backup and states
plainly that a safety backup is taken automatically first. `AdminDashboardController`
routes `backup.restore` straight to the new Go transport method
(`GoDatabaseTransport.restore_backup` in `app/database/remote.py`), the same way
`backup.create`/`database.optimize`/`database.vacuum` already bypass `ACTION_MAP` to
call the transport directly — restore isn't a versioned game action, so it doesn't go
through `admin.*`. No Discord slash-command equivalent was added: typing an exact
backup filename blind in a Discord command is exactly the kind of error this feature
exists to protect against, whereas the dashboard shows the real, current list of
backups to click from.

### `admin.player.clear_condition`

Mirrors the exact resolve semantics ordinary gameplay already uses when a condition
clears on its own (`progression_actions.go`'s "cleared" branch):
`state='resolved', severity=0, resolved_game_minute=<canonical world clock>`. A
`condition_id` targets one specific active condition (a character can hold several at
once, each with a different `condition_key`, so the dashboard lists real row IDs to
make this unambiguous); `clear_all:true` instead resolves every currently-active
condition for that player in one transaction and one audit entry, for "the GM wants
this character's slate wiped" rather than clearing debuffs one at a time. Both paths
error rather than silently no-op if there is nothing to clear. The player detail
drawer gained a Conditions section listing each active condition with severity and a
Clear button.

**Verification:** `Conn.RestoreFrom` has a dedicated round-trip unit test in
`internal/storage/sqlite_test.go`; the `/v1/db/restore` endpoint has three HTTP-level
tests in the new `internal/server/server_restore_test.go` (revert-to-named-backup,
reject unsafe/unknown names, force-close open sessions first), and
`admin.player.clear_condition` has two tests in `admin_actions_test.go`. All were
mutation-tested — every guard reverted, confirmed to fail its test, restored, and
reconfirmed passing. One of those mutation passes caught a real gap in the test itself,
not the implementation: the first version of the restore-endpoint test only checked
that a file existed at the safety-backup's path, but `reserveBackupPath` always creates
a zero-byte placeholder as part of claiming a name — so skipping the actual backup
entirely ran the test clean. Fixed by reopening the safety backup and querying its
actual row count, which now correctly fails when the backup step is skipped. `go build
./...`, `go vet ./...`, `go test ./... -count=1` all clean.
`test_static_dashboard_contains_real_admin_console` extended with presence assertions
for both new action names, mutation-tested the same way (mutating the substring itself
this time, per a lesson from the previous release's own mutation-testing mistake). Full
suite re-run and baseline-diffed against the pre-change tree with zero new failures
(same pre-existing python3.11/3.12 PEP-701 split documented above, plus the expected
release-manifest mismatch ahead of this release's own manifest regeneration).

## v0.19.29 — dashboard attribution, world-time scale, dynasty/crafting write paths, mute/freeze, undo

Six items picked off the same running gap audit as the previous two releases: a
dashboard write-attribution bug, wiring the dashboard up to an already-shipped Go
feature (world-time scale), a narrow write path for the previously fully-read-only
dynasty/samsara system, four narrow write paths for previously fully-read-only
crafting-adjacent tables, a mute/freeze moderation system, and an "undo the most
recent admin action" control. Two of these (moderation, undo) were flagged up front as
architecturally nontrivial, and both turned out to be — see their sections below for
the real complications and how they were resolved. A seventh, unplanned fix rode
along: a real privacy bug in `#xianxia-info`'s guide dropdown, found and fixed in the
same session.

### Dashboard write attribution

Every dashboard-originated write and audit entry was logged with `ActorID=0`
("unattributed") no matter who actually clicked the button — `AdminDashboardController`
hardcoded `0` at both its `ACTION_MAP` dispatch call and its `_audit()` helper. The
bot's own slash-command path never had this problem; it always passed the real Discord
snowflake as `ActorID`. Since this server has exactly one GM and the dashboard's
existing HTTP Basic Auth already gates who can reach it at all, the fix doesn't add a
login system — that would be solving a problem this server doesn't have. Instead, a new
`DASHBOARD_ACTOR_ID` setting (default `1`, documented in `.env.example` with the option
to set it to the GM's real Discord ID so dashboard-originated and bot-originated audit
rows attribute to the same identity) replaces the hardcoded `0` everywhere
`AdminDashboardController` writes. Authentication still answers "can this request
proceed"; attribution now just stops lying about who did it.

### World-time scale, wired up

`admin.world.advance_time` already accepted an optional `scale` parameter in Go
(game-minutes elapsed per real-world minute going forward; `scale=0` freezes game-time
drift entirely) — the dashboard's World Time card just never sent it. Added a "New time
scale" field (blank = unchanged, preserving today's "just jump time" behavior) and a
live display of the current scale next to it.

### `admin.player.force_reincarnation_ready` — unstick a soul waiting on real-world time

The dynasty/samsara system was fully read-only before this release. Auditing every
handler for a genuine "stuck player" case (rather than adding CRUD across all seven
dynasty/samsara tables for its own sake) turned up exactly one:
`reincarnation_state.reincarnation_ready_at` is a real-world Unix-seconds wall-clock
gate with no bypass anywhere in the codebase — a player who reincarnates at an
inconvenient real-world hour has no way to proceed until that deadline passes on its
own. The new op clears it to `0` for one player's active cycle only, leaving the other
six dynasty/samsara tables (`soul_legacy`, `samsara_dynasty_history`,
`samsara_ancestral_leads`, `samsara_investigation_quests`, `samsara_dynasty_claims`,
`samsara_dynasty_conflicts`) untouched and still read-only — they're procedurally
generated narrative/history records with no identified "stuck" bug class.

### Four crafting-adjacent admin controls

Also previously read-only: `alchemy_state` (pill toxicity — the one state that
persists across every other reincarnation wipe, since it's conspicuously absent from
the wipe-list that clears `equipment_instances`/`spirit_beasts`/etc. on death, with no
prior fix path at all), `spirit_beasts`, `equipment_instances`, and
`cave_abodes`/`cave_abode_access`. Four narrow ops, not full column-level editors:
`admin.player.set_pill_toxicity` (direct-set, clamped 0-1000), `admin.player.set_beast_stats`
(targeted loyalty/evolution_stage on one beast by `(user_id, beast_id)`, only touching
fields actually supplied), `admin.player.remove_equipment` (force-delete one equipment
row, for the partial-unique-index "stuck unable to re-equip a slot" case), and
`admin.player.set_abode_access` (grant or revoke one `(owner, guest)` access row).

### Mute/freeze a player

The one item where "just add a check" wasn't the whole story. Every earlier admin
write path in this line goes through Go's `applyAuthoritative` dispatch — but Python's
`GoDatabaseTransport`/`RemoteSQLiteConnection` also writes `characters` rows directly
via the engine's raw-SQL `/v1/db/session`/`/v1/db/batch` endpoints, and Go's own
`simulation.Runner` writes `characters` directly too, both entirely bypassing
`applyAuthoritative`. A mute/freeze check can only ever live inside that one dispatch
path — it structurally cannot intercept the other two. This is stated plainly rather
than glossed over: **this is a moderation nudge for a five-player table, not an
anti-cheat mechanism.** Everything reachable through `applyAuthoritative` is the player
acting; everything that bypasses it is another system, or the GM, acting *on* the
player — which mute/freeze was never meant to block anyway.

Schema 27 adds three `characters` columns (`is_muted`, `is_frozen`,
`moderation_reason`, all defaulted so no existing row changes meaning). A new
`checkPlayerModerationTx` (`go_core/internal/game/moderation.go`) runs inside
`applyAuthoritative` immediately after the existing `checkPlayerOldAgeDeathTx` check —
and only when that check did *not* already preempt the call with a death mutation, so a
character genuinely dying of old age still dies regardless of a GM's freeze flag.
Freeze blocks every authoritative player-initiated mutation; mute blocks only
`scene.action` (free-form roleplay) and leaves combat, cultivation, shopping and
`check.resolve` open. `admin.player.set_moderation` toggles both flags and a
GM-visible reason (capped at 300 characters), changing only the fields actually
supplied. The dashboard's players table and its new Moderation card expose all three
fields, pre-filling the controls from whichever player is currently selected.

Because the new check runs unconditionally for every authoritative operation, adding
it broke roughly a dozen unrelated tests across other test files that share the
package's wide `characters` fixture (`batch4_authority_test.go`,
`character_creation_authority_test.go`) with `sqlite error: no such column: is_frozen`
— fixed by widening those two fixtures with the three new columns (all
default-valued, so no existing `INSERT` in either file needed to change).

### `admin.audit.undo_last` — undo the most recent admin action

`admin_audit_log`'s before/after snapshots are not uniformly reversible, so this is a
deliberately narrow allowlist, not "undo anything." An action qualifies only when its
handler captures a complete before/after snapshot of exactly the row(s) it touches —
single-table, or `admin.player.grant_currency`'s specific dual-table shape
(`currency_wallets.balance` always, plus `characters.spirit_stones` specifically when
the currency is `low_spirit_stone`, mirroring the forward op's own `MAX(0,...)` floor).
Fourteen ops qualify: `karma`, `set_realm`, `teleport`, `grant_currency`,
`set_realm_perfection`, `set_spiritual_root` (deletes the row on undo if it didn't
exist before the forward action, restores it otherwise), `set_bloodline`,
`set_physique`, `set_tribulation`, `adjust_item` (deletes the inventory row on undo if
the quantity was zero beforehand), `force_end_scene`, `npc.relocate`,
`clear_condition` (only the single-`condition_id` variant — `clear_all`'s
before-snapshot is an array, one entry per row it resolved, which doesn't fit this
release's one-row-shape-per-action rule, and is rejected by name at undo time rather
than mis-parsed), and `set_moderation`.

Explicitly **not** reversible, each for a reason read directly out of its handler, not
guessed: `admin.player.fate` (also writes `fate_ledger`, a second table this doesn't
reverse), `admin.player.set_sect` (its `ON CONFLICT DO NOTHING` doesn't record whether
it created a row), `admin.player.set_resource_caps` (lowering a cap also clamps a
resource down, and that clamp isn't captured in the snapshot), `admin.player.reset_cooldowns`
and both `admin.bulk.*` actions (no per-row snapshot exists to restore from at all),
`admin.player.revive` and `admin.player.clear_battle` (wide multi-table side effects),
`admin.world.advance_time` (its before/after record a derived `game_minute`, not the
actual `world_clock` state, and reversing time risks desyncing anything the simulation
runner already ran against the timeline in between), and `admin.audit` itself
(log-only). Calling undo when the log is empty, or on any of the above, fails with a
plain "no admin action to undo" / "this action cannot be safely undone" error rather
than guessing.

Undo-of-undo is real *redo*, not just a second log entry: undoing an
`admin.audit.undo_last` row re-fetches the action it undid and re-invokes that same
action's own reversal logic against its `after_json` instead of its `before_json`,
landing back on the exact state the original action produced — verified with a
dedicated test that grants karma, undoes it, undoes the undo, and confirms the score
is back at the *post-grant* value, not stuck at the pre-grant one. There is
deliberately no "my actions only" filter on undo — this server has exactly one GM
account, so scoping by actor would add complexity with nothing to filter against; undo
always targets the single most recent row in the log, full stop. The dashboard gained
one "Undo most recent action" button near the Recent Admin Audit table, with a confirm
dialog stating the allowlist boundary plainly.

### `#xianxia-info` guide dropdown was posting publicly, and leaking admin instructions

Found during this session while explaining the plan above, not part of the original
audit: `XianxiaInfoSelect.callback` (`app/bot/main.py`) sent every guide-topic reply
with `ephemeral=False` — clicking *any* topic in the read-only info channel's dropdown
posted that topic's text into the channel for every player to see, not just the
clicker. Worse, the dropdown's own "🧰 Server Administration" topic (a page
documenting that `/admin` exists and its command groups) was one of those public
options, so any player who clicked it broadcast GM command documentation to the whole
server. Every reply from this dropdown is now `ephemeral=True` — private to whoever
opened it — which on its own fixes both the noise and the exposure. The admin topic
additionally checks `guild_permissions.administrator` before showing its content,
replying with a private "not for you" notice to anyone else who selects it. Discord
gives no way to make one persistent, shared message's dropdown show different options
to different viewers — this is one message with one `timeout=None` `View`, so every
player who opens it sees the identical option list — so the admin topic stays listed
for everyone rather than disappearing for non-admins; only what happens after clicking
it differs. This project has a deliberate, test-enforced rule
(`test_only_begin_flow_can_send_ephemeral_responses`) that only the `/begin`
character-creation flow may send ephemeral responses; `XianxiaInfoSelect` was added to
that test's narrow allowlist as an intentional, documented exception, not a
workaround.

**Verification:** `go build ./...`, `go vet ./...`, `go test ./... -count=1` clean
across the whole `go_core` module (including the four items above with no new tests of
their own past the ones described here). Every new/changed Go behavior has dedicated
tests — `admin_actions_test.go` for `force_reincarnation_ready`, the four
crafting-adjacent ops, `set_moderation`, and `admin.audit.undo_last`'s own
narrow-schema unit tests; a new `moderation_authority_test.go` exercising real
`applyAuthoritative` dispatch for frozen/muted/unflagged/old-age-death-still-fires
cases; a new `admin_undo_test.go` with one test per reversible action, a dedicated
dual-table `grant_currency` test, an exclusion test covering every explicitly-excluded
action, and the undo-of-undo redo test — and every one of those tests was
mutation-tested (the specific guard or branch it exists to catch was temporarily
broken, confirmed to make the test fail, then restored and reconfirmed passing).
`python3.11 -m unittest tests.python.integration.test_dashboard
tests.python.contracts.test_dashboard_implementation` clean. Full suite re-run and
baseline-diffed against the pre-change tree with zero new failures (same pre-existing
python3.11/3.12 PEP-701 split documented above — confirmed by re-running the affected
tests directly under python3.12, where they pass — plus the expected release-manifest
mismatch ahead of this release's own manifest regeneration).


## v0.19.30 — split stage 3: `/sect` leaves main.py

A move, not a change. `main.py` 12,980 → **12,174** lines; the new
`app/bot/commands/sect.py` 895, holding the base `sect` group plus its
`manor`, `discipleship` and `recruitment` subgroups — 25 leaf commands in
total, roughly double stage 2's 14. Also in this release: `README.md` itself
split the same way — a leaner `README.md` (architecture, setup, operations)
plus a new `VERSIONS.md` holding the full v0.18/v0.19.x changelog, release
status and schema history that used to live at the bottom of the README.

### The footprint was measured before anything moved, same as stage 2

29 free names read by the block. Most resolved the way stage 2's did:

| Name(s) | Decision |
| --- | --- |
| `sect_group`, `sect_manor_group`, `sect_disciple_group`, `sect_recruitment_group` | move to sect.py; main imports `sect_group` back for the `_MIGRATED_ROOTS`-adjacent `"sect": sect_group` mapping |
| `ADDRESS_STYLE_CHOICES` | used only by `/sect → Form` — moves with it, same as `CHILD_CULTIVATION_AWAKENING_AGE` in stage 2 |
| `MAX_MANOR_FACILITY_LEVEL`, `SECT_MANOR_ESTABLISHMENT_COST`, `SECT_MANOR_FACILITIES`, `manor_benefit_lines`, `manor_upgrade_cost`, `trial_profile`, `trial_modifier`, `recommendation_modifier` | pure imports from `sect_manor`/`sect_recruitment` used nowhere else in main.py — dropped from main.py's import list, added to sect.py's |
| `recruitment_definition` | used by the moving block **and** by `ensure_sect_abode_record`, which stays in main.py — imported in both files, not moved |
| `SIM`, `_known_locations`, `current_npc_location`, `ensure_sect_abode_record`, `ensure_sect_abode_thread_for` | used far more elsewhere in main.py — deferred `from ..main import` inside the one function that needs each, same pattern as `_get_thread` in stage 2 |
| `_sect_recruitment_at_location` | the mirror image: **defined** in the moving block but used once elsewhere in main.py (`/explore`'s road-discovery flow) — main.py imports it back at module level, same as `sect_group` |

### One name stage 2 never had to deal with

`carried_item_autocomplete` is referenced as a bare
`@app_commands.autocomplete(item=carried_item_autocomplete)` decorator
argument in both `/storage` (main.py) and `/sect → Contribute`/`Redeem`
(sect.py). A decorator argument evaluates at **module-import time**, not call
time — unlike every other shared name above, a deferred `from ..main import`
inside a function cannot reach it, because sect.py's module body (where the
decorator line lives) finishes executing long before any function in it is
called. Its only dependencies are `DB`, `WORLD` and `app_commands`, all
already in runtime.py, so it moved there instead of being duplicated —
exactly the reasoning stage 2 used for `GENDER_CHOICES`, applied to a new
failure mode this stage introduced.

### Verification

`ModuleResolutionTests`, `DefinitionOrderTests`, `WrappingDecoratorAnnotationTests`
and `ImportDirectionTests` in `test_bot_module_split.py` cover `commands/sect.py`
automatically via the shared `MODULES` list — no separate test file was needed for
the generic checks. A new `SectSplitTests` class (mirroring stage 2's
`FamilySplitTests`) covers the shape specific to this move: both groups fully
moved with the right per-group command counts (11/3/5/6), the `carried_item_autocomplete`
promotion, the `_sect_recruitment_at_location` back-import, and the five
deferred-import names. `test_sect_recruitment.py`'s source-scanning test —
previously anchored to `main.py` only, exactly the stage-2-documented failure
mode — now scans the whole `app/bot` package instead of guessing which file a
string lives in after the move.

Six mutations proved the new checks load-bearing: a module-level cycle import
reintroduced into sect.py, `carried_item_autocomplete` duplicated back into
main.py, `ADDRESS_STYLE_CHOICES` deleted from sect.py, the `sect_group`
back-import dropped from main.py's import line, one function's deferred `SIM`
import removed (caught by the deferred-import test but — as expected, and
confirmatory rather than a gap — *not* by the whole-module resolution test,
since Python resolves a function-local import independently per function), and
one leaf command's group changed to shift the per-group counts. All six were
confirmed to fail the exact test meant to catch them, then reverted.

`go build ./...` and `go vet ./...` clean (this release touches no Go code).
Full Python suite baseline-diffed against v0.19.29: 537 tests versus 528,
zero new failures — the same pre-existing python3.11/3.12 PEP-701 split
documented above (13 errors, 2 import errors, all from tests that `ast.parse`
`main.py` directly), cross-checked passing under python3.12, plus
`test_authority_boundary.py`'s pytest-style bare functions (not collected by
`unittest`, and no `pytest` install available in this environment) run
directly by calling each `test_*` function and asserting no exception, all
five passing including `test_sect_recruitment_does_not_send_caller_computed_rolls`
against its new location.

## v0.19.31 — ephemeral-visibility merge

A community member submitted a patch (`xianxia_ephemeral_patch.diff`, touching
`app/bot/hubs.py`, `app/bot/runtime.py` and
`tests/python/unit/test_hub_layout_rollout.py`) aimed at fixing hub replies
that should be private but were not. Rather than apply it verbatim, it was
reviewed against the running suite, and merged with two parts held back.

### The real bug it fixes

`reply_long()` (`app/bot/runtime.py`) takes an `ephemeral` keyword but never
used it — every chunk of a long reply was sent with the literal
`ephemeral=False`, so anything routed through it (`/world`'s output among
others) could never be made private no matter what the caller asked for.
Fixed to forward the caller's own choice; a new test
(`LongReplyVisibilityTests.test_reply_long_preserves_caller_visibility`)
reads the function body via `ast` and asserts all three call sites use
`ephemeral=ephemeral` and none use the old hardcoded `ephemeral=False`.

### Accepted: routing hub-only feedback privately

The bulk of the patch adds `_response_is_ephemeral()` / `_send_ephemeral_followup()`
helpers to `app/bot/hubs.py` and threads them through
`_HubFollowupProxy.send`, `_HubResponseProxy.send_message` and
`_HubResponseProxy.defer` (which now records the deferred visibility as
`self.owner.deferred_ephemeral`, so a component that deferred ephemerally
stays ephemeral when it later responds). This is sound: a handler that
explicitly asks for `ephemeral=True` now actually gets a private followup
instead of that flag being dropped on the floor, and it never *forces*
anything private that was not already — public output is completely
unaffected. On top of that plumbing, the patch flips several UI-only messages
from `ephemeral=False` to `ephemeral=True`:

- `HubActionModal.on_submit`'s bad-input errors and "continue to finish the
  action" prompts — nobody but the submitting player can see the modal in the
  first place, so a public reply only spams the channel.
- `_present_input_step`'s guided choice/bool/member/channel pickers — the
  same "only the acting player is looking at this" shape as the existing
  `BirthFamily*` picker chain.
- `LayoutHubView.interaction_check` / `CommandHubView.interaction_check`'s
  "This panel belongs to another player" and "This panel requires the
  **Administrator** permission" rejections. This is the sharpest find in the
  whole patch: these were `ephemeral=False`, meaning a wrong-user click or a
  permission re-check failure broadcast that rejection notice to the entire
  channel on every mis-click. That is a real, if minor, information leak
  (confirms who owns a panel, confirms an admin's permissions changed) fixed
  by this release.

`tests/python/unit/test_command_cleanup.py`'s
`test_only_begin_flow_can_send_ephemeral_responses` — an AST visitor that
flags any `ephemeral=` keyword argument not inside `/begin`'s own flow or an
explicitly reviewed scope — was extended with six new allowlist entries
(`_fallback_followup`, `_HubFollowupProxy`, `_HubResponseProxy`,
`HubActionModal`, `_present_input_step`, `LayoutHubView`, `CommandHubView`),
each with a comment explaining why that scope's private replies are
intentional, matching the existing `XianxiaInfoSelect` entry's style. Each
entry was confirmed load-bearing by temporarily removing it and checking the
test fails on exactly the violations that entry was covering.

### Rejected: two changes that would have broken the public hub panel

Running the full suite with the patch applied surfaced one genuine new
failure beyond the expected baseline:
`test_hub_public_replies_use_interaction_webhook_and_chunk_long_text`. Tracing
it back found two changes in the patch that do not fit this project's hub
architecture and were reverted to their pre-patch form before merging:

1. **`_invoke_action`'s exception handler.** When a hub action's handler
   raises, this code is responsible for putting the shared hub card back into
   a usable state for whoever is looking at it — not just the player whose
   click failed. The original implementation edits the public hub message
   (or, for a Components V2 layout hub, sends a panel-preserving followup via
   `_layout_result_send`) back to the "that action could not be completed"
   text with the hub view reattached, falling back to a public followup only
   if the edit itself fails. The patch replaced all of this with a single
   private `_send_ephemeral_followup` call — which means only the player who
   triggered the failure ever finds out, and the public hub card is left
   exactly as it was mid-action (potentially showing a disabled/loading
   state) for everyone else looking at the same panel. Reverted to the
   original public-refresh behavior.
2. **`_HubResponseProxy.edit_message`.** The patch dropped this method's
   `_layout_targets_panel(...)` branch entirely, along with the guard that
   only calls `response.edit_message` directly when the interaction's own
   source message actually *is* the hub message (deferring and using
   `edit_original_response` otherwise). For a Components V2 layout hub this
   branch is not an optimization, it is required correctness: a CV2 message
   can never carry `content`/`embeds`, so routing its output through
   `_layout_result_send` instead of trying to edit the panel directly is the
   only way layout-hub actions render at all. Dropping it would have broken
   every layout-hub panel's `edit_message` path. Reverted to the original
   implementation.

Both reversions are now guarded by a new `HubFailurePublicSurfaceTests` class
in `tests/python/unit/test_hub_layout_rollout.py`
(`test_invoke_action_failure_still_refreshes_the_public_hub_panel`,
`test_response_proxy_edit_message_still_defers_to_the_layout_panel`), each
confirmed to fail when the corresponding patch behavior is reintroduced and
to pass again once reverted — so a future patch touching this code gets
caught by the suite instead of requiring another manual review pass.

### Verification

`go build ./...`/`go vet ./...` are not applicable (no Go code touched).
Full Python suite baseline-diffed against v0.19.30: 543 tests versus 537 (4
added by the merged patch's own `EphemeralRoutingTests`/`LongReplyVisibilityTests`,
2 added by this release's `HubFailurePublicSurfaceTests`), zero new failures
beyond the same pre-existing python3.11/3.12 PEP-701 split
documented above (13 errors, 2 import errors, all from `main.py`'s
backslash-in-f-string, cross-checked passing under python3.12), plus
`test_authority_boundary.py`'s five pytest-style functions run directly and
confirmed passing (no `pytest` install in this environment). All patch-added
tests (`EphemeralRoutingTests`, `LongReplyVisibilityTests`) pass unmodified
against the merged result, since neither references the two rejected
functions. The two new `HubFailurePublicSurfaceTests` and the six new
allowlist entries were each mutation-tested (temporarily reintroducing the
rejected patch behavior, or removing an allowlist entry, confirming the
relevant test fails, then restoring and reconfirming green).
