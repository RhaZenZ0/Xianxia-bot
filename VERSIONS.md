# Xianxia RP Discord Bot — Version History

This is the release-by-release changelog for the Xianxia RP Discord Bot, split out of `README.md`
so the README can stay focused on architecture, setup, and current operational documentation. See
`README.md` for that; see `docs/V019_RELEASE_NOTES.md`, `docs/V018_RELEASE_NOTES.md` and
`docs/V018_BUILD_HISTORY.md` for full per-release detail beyond the summaries below.

## Changelog

Version **0.18** completed the staged authority cleanup: forage/crafting/companions, canonical time,
unified lifespan, multi-hop road travel, caravan mechanics, dashboard-owned Discord setup, and removal
of obsolete Python mechanical authority paths. **0.19** is a cultivation-depth consistency/coverage pass
on top of that release, plus a further authority-migration pass for 1v1 battle start and mid-battle item
recovery, plus GM-authored per-channel welcome messages and a #bugs forum channel for
player bug reports. The release uses schema **26**.

**0.19.5** is a GUI release on top of that. The interactive hub panel gains a Components V2
"action list" layout: every action on a system is visible as its own row with its own button,
instead of being hidden behind an action dropdown, and running an action no longer overwrites
the panel it was launched from. It is piloted on `/character`; the other 15 player hubs and the
admin panel keep the classic embed panel, and `LAYOUT_HUB_NAMES` in `app/bot/hubs.py` is the
whole rollout switch. 0.19.5 changes no schema and no game rules. It also corrects the release
stamp itself, which had drifted: `VERSION` said 0.19 while `app/version.py`, the `Dockerfile`
and `docker-compose.yml` all still said 0.18.

**0.19.6** is a correctness release answering an external audit. It fixes a silent data-loss
path in the Go engine (`/v1/db/batch` with `transaction:false` returned HTTP 200 and then rolled
the writes back), Discord snowflake ids being rounded to nothing by `JSON.parse` on the way to
the dashboard, a cleared channel message coming back on the next Repair, backups taken in the
same second overwriting each other, `#bugs` forum tags never being applied to an adopted
channel, and a `RELEASE_MANIFEST.sha256` that had drifted out of date and was never verified by
`update.sh`. Every fix carries a regression test that fails without it. No schema change.

**0.19.7** corrects the hub layout against how it actually renders in Discord: no generic
glyph on action rows, one status per line with shorter bars, danger buttons that name
their own verb, and the system dropdown removed as a duplicate of Prev/Next.

**0.19.8** fixes onboarding copy that told players to type commands that do not exist
(`/family leave` is not registered — only the hub `/family` is), rewrites the household and
expedition thread openers to lead with how to step outside, and adds a test that gates the
whole shape.

**0.19.9** fixes the release integrity check, which used GNU-only `sha256sum` flags and so
refused every package on BusyBox-based NAS hardware. See RELEASE.txt — upgrading from
0.19.6–0.19.8 needs a one-line patch to the *installed* `update.sh` first.

**0.19.10** rolls the Components V2 layout out to every hub — all 16 player hubs and
`/admin` — in four staged groups, and ports the administrator re-check into the layout view
so `/admin` keeps the guard the classic panel always had.

**0.19.11** orders hub actions by relevance rather than alphabetically (so `/family`'s
Leave is visible without paging), and answers an external review: the Python containers now
run as uid 10001 instead of root, and first-run setup names `DASHBOARD_TOKEN`.

**0.19.12** starts the `main.py` decomposition: `app/bot/runtime.py` now holds the shared
singletons and session helpers, and `main.py` drops from 13,177 to 12,923 lines. Stage 1 of
several; no behaviour change.

**0.19.13** fixes the ordering bug that stopped 0.19.12 starting (`runtime.py` used `ROOT`
before assigning it) and adds the module-level use-before-assignment check that would have
caught it.

**0.19.14** fixes the second stage-1 failure: `serialized_user_action` moved to
`runtime.py`, and because `functools.wraps` cannot copy `__globals__`, discord.py resolved
119 callbacks' string annotations against `runtime.py` — where an "unused import" pass had
removed `app_commands`.

**0.19.15** adds the administrator AI monitor: `ai_status` (narrator health from
counters only) and `chat_digest` (map-reduce channel summaries on the same free
route chain as narration).

**0.19.16** bumps `openai` from 2.52.1 to 3.7.0. openai 3.x uses HTTPX2, which
verifies TLS against the OS trust store rather than certifi, so the Dockerfile now
installs `ca-certificates`, asserts the bundle is present and sets `SSL_CERT_FILE`.

**0.19.17** fixes the "0 stones" bug and four others found alongside it.

**0.19.18** reworks Scene Action so it needs no dropdowns and its state travels
with the player between scenes.

**0.19.19** closes an unauthenticated slow-header denial-of-service affecting both
HTTP listeners (health and dashboard) — the ancestor of the bounded-request-head
work described under "HTTP request limits" in the README.

**0.19.20** acts on what `ai_status` found in production: narration was landing on
procedural fallback 90.9% of the time, and this release fixes the causes.

**0.19.21** gives Bind a live picker to match the Equip/Unequip/Repair pickers
added in 0.19.17, and fixes a wrong-path bug a new test surfaced along the way.

**0.19.22** makes the expedition journal open automatically when a player steps
back into the world.

**0.19.23** is stage 2 of the `main.py` decomposition: `/family` moves out of
`main.py` into its own module.

**0.19.24** gives every OpenRouter route its own per-model rate window (RPM + RPD)
alongside the existing account-wide ceiling, so a route at its own cap is skipped
locally instead of spending an upstream attempt, a daily slot, and a cooldown to
discover it.

**0.19.25** is a production-review pass: ack-before-mutate for slow Discord
interactions, persistent event-scene timeouts, `/explore`'s road-discovery/frontier
logic, and live Discord-timestamp travel countdowns.

**0.19.26** adds nine new GM controls to the web Admin Console: character-sheet
editing, NPC/world-state editing, player moderation groundwork, and bulk/
server-wide actions.

**0.19.27** adds six more Admin Console controls, for sect membership/rank and
character progression detail (realm/body-realm perfection, spiritual root,
bloodline, physique, and tribulation state).

**0.19.28** adds a database restore command — the Go engine's first online-restore
capability, always taking its own safety backup first — and a debuff/condition-clear
admin control.

**0.19.29** fixes dashboard write attribution and a public-channel privacy leak in
the `#xianxia-info` guide, wires up world-time scale from the dashboard, adds a
dynasty/samsara unstick control, four crafting-adjacent admin controls, a
mute/freeze moderation system (schema 27), and an "undo the most recent admin
action" control.

**0.19.30** is split stage 3: `/sect` (25 leaf commands across the base group
plus manor/discipleship/recruitment subgroups) moves out of `main.py` into
`app/bot/commands/sect.py`, the same way `/family` moved in stage 2 (0.19.23).
`main.py` 12,980 → 12,174 lines; the new module is 895. This release also
splits the top-level `README.md` itself into a leaner `README.md` (architecture,
setup, operations) and a new `VERSIONS.md` (this file) holding the full
changelog, release status and schema history.

**0.19.31** fixes a real ephemeral-visibility bug and adds several deliberate
privacy improvements to the hub UI, merged in from a reviewed community patch
rather than applied as-is. `reply_long()` (`app/bot/runtime.py`) previously
hardcoded every chunk of a long reply to `ephemeral=False` regardless of what
the caller asked for - `/world`'s long output and anything else routed through
it could never actually be sent privately. It now forwards the caller's own
`ephemeral` choice. Hub UI feedback that is inherently single-user - bad modal
input, a guided multi-step picker continuing a player's own in-progress
action, and (this was the sharper find) the "this panel belongs to another
player" / "requires Administrator" rejection notices on `interaction_check` -
is now sent privately instead of `ephemeral=False`, which used to broadcast
those notices to the whole channel on every mis-click. Two parts of the
originally-proposed patch were deliberately rejected rather than merged: it
would have rewritten a failed hub action's error handling to reply privately
instead of restoring the public hub panel (players other than the one whose
action failed would be left looking at a stale, unresponsive card), and it
collapsed `_HubResponseProxy.edit_message`'s Components V2 panel-preservation
branch, which would have broken every layout-hub panel's ability to route its
own output beside the panel instead of over it. Both are guarded by new
regression tests (`HubFailurePublicSurfaceTests` in
`tests/python/unit/test_hub_layout_rollout.py`) so a future patch touching
this code gets caught the same way. See `docs/V019_RELEASE_NOTES.md` for the
full before/after and the reasoning behind each accepted and rejected change.

See `docs/V019_RELEASE_NOTES.md` for the full detail on every v0.19.x release above, `docs/V018_RELEASE_NOTES.md` and
`docs/V018_BUILD_HISTORY.md` (consolidated validation/audit record) for the prior staged-authority migration.

## Release status — v0.19.31

- Current release: v0.19.31, a hub-UI ephemeral-visibility fix and privacy
  pass merged in (with two parts deliberately rejected) from a reviewed
  community patch.
- v0.19.30: split stage 3 - `/sect` leaves `main.py` for
  `app/bot/commands/sect.py`, and this `README.md`/`VERSIONS.md` split.
- v0.19.29: fixed dashboard write attribution and a public-channel privacy
  leak in the `#xianxia-info` guide, wired up world-time scale from the dashboard, added a
  dynasty/samsara unstick control, four crafting-adjacent admin controls, a mute/freeze moderation
  system (schema 27), and an "undo the most recent admin action" control.
- v0.19.28: a database restore command (the Go engine's first online-restore capability) and a
  debuff/condition-clear admin control.
- v0.19.27: six more Admin Console controls for sect membership/rank and character progression
  detail.
- v0.19.26: nine new Admin Console controls (character-sheet editing, NPC/world-state editing,
  player moderation groundwork, bulk/server-wide actions).
- v0.19.25: a production-review pass — ack-before-mutate, persistent event-scene timeouts,
  `/explore` road-discovery/frontier logic, and live Discord-timestamp travel countdowns.
- v0.19.24: per-route OpenRouter rate limits (RPM + RPD per model) alongside the account-wide pair.
- v0.19.23: split stage 2 - `/family` leaves `main.py`.
- v0.19.22: the expedition journal opens automatically on returning to the world.
- v0.19.21: a live picker for Bind, matching Equip/Unequip/Repair, plus a wrong-path fix.
- v0.19.20: fixes for the 90.9% procedural-fallback rate `ai_status` surfaced in production.
- v0.19.19: closed an unauthenticated slow-header DoS in both HTTP listeners.
- v0.19.18: Scene Action reworked to need no dropdowns; state travels with the player.
- v0.19.17: the "0 stones" bug and four others found alongside it.
- v0.19.16: `openai` dependency bump (2.52.1 → 3.7.0) and the OS-trust-store TLS fix it required.
- v0.19.15: the administrator AI monitor (`ai_status`, `chat_digest`).
- v0.19.14: split stage 1 corrected again (annotation-namespace fix + import harness).
- v0.19.13: definition-order fix - still did not start.
- v0.19.12: stage 1 of the main.py decomposition (shared runtime extracted) - did not start.
- v0.19.11: relevance-ordered actions and deployment hardening.
- v0.19.10: the Components V2 layout on all 17 hubs.
- v0.19.9: portable release-integrity check (BusyBox-safe).
- v0.19.8: onboarding copy pointing at reachable commands, gated by a test.
- v0.19.7: hub-layout corrections made after seeing the panel render live.
- v0.19.6: a correctness release fixing the findings of an external audit
  (engine batch durability, dashboard snowflake precision, cleared-message persistence, backup
  naming, `#bugs` tag sync, release-manifest integrity). No schema change.
- v0.19.5: a GUI release adding the Components V2 hub layout, piloted on `/character`.
- v0.19: a cultivation-depth consistency/coverage pass plus a further authority-migration pass for
  1v1 battle start and mid-battle item recovery, built on top of the v0.18 staged-authority migration.
- The v0.18 release gate added loopback-by-default standalone engine binding plus strict bounded JSON
  request handling; v0.19 added three schema migrations (schema 25 for GM-authored per-channel messages,
  schema 26 for the #bugs forum channel, schema 27 for v0.19.29's mute/freeze moderation columns),
  otherwise only mechanics/logic and documentation fixes.

## Schema history

- **Schema 14** introduced player memory and narrator-safe canon FTS.
- **Schema 15** added structured permanent world history and `world_history_fts`.
- **Schema 16** added persistent NPC life/social/descendant systems.
- **Schema 17** adds the current event participation/GUI persistence layer and associated current schema updates.
- **Schemas 18-24** carried the v0.18 staged-authority migration (forage/crafting/companions, canonical time,
  unified lifespan, multi-hop road travel, caravan mechanics, dashboard-owned Discord setup) through to its
  final state.
- **Schema 25** added GM-authored per-channel welcome messages (`channel_messages`).
- **Schema 26** added the `#bugs` forum channel (`bugs_channel_id`).
- **Schema 27** added the v0.19.29 mute/freeze moderation columns on `characters`
  (`is_muted`, `is_frozen`, `moderation_reason`).

See `docs/V018_RELEASE_NOTES.md` and `docs/V019_RELEASE_NOTES.md` for the per-release detail.

## Release notes

See `docs/V019_RELEASE_NOTES.md` for the current release's cultivation-depth audit, dashboard coverage gaps, and
combat authority-migration fixes. See `docs/V018_RELEASE_NOTES.md` for the complete staged-authority, road/caravan,
setup, cleanup, migration, security, and upgrade summary that v0.19 builds on.
