# Xianxia RP v0.19 — Cultivation Depth & Consistency Pass

Shipping as **v0.19.6**. This file covers the whole v0.19 line: v0.19 itself, the v0.19.5
GUI release (the Components V2 hub layout), and v0.19.6, which fixes the findings of an
external audit — see "Components V2 hub layout", "Release stamp corrected" and
"v0.19.6 — external audit findings, fixed" near the end. The release is stamped 0.19.6 in
`app/version.py`, `VERSION`, the `Dockerfile`, `docker-compose.yml` and `.env.example`, and
carries schema 26 with no migration of its own.

Release date: 2026-08-31 (v0.19) / 2026-09-01 (v0.19.5, v0.19.6)
Schema: 26 (unchanged from v0.18 through most of this release — every fix was
logic-only — until the "GM-authored per-channel messages" entry below added schema 25's
`channel_messages` table, and the "#bugs forum channel" entry further below added
schema 26's `bugs_channel_id` column)
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
