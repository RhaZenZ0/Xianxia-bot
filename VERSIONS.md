# Xianxia RP Discord Bot — Version History

The changelog, one paragraph per minor. The per-release entries as they were written are in
`docs/history/CHANGELOG_0_18_TO_0_40.md`, and the earlier per-release notes (`V018_RELEASE_NOTES.md`
… `V023_RELEASE_NOTES.md`) beside it. `README.md` describes the current release.

## Changelog

**1.0.0** (rc.5) fixes the curve the cultivation system sat on. A session used to be worth a flat
`8 + d7 + attribute`, about fourteen essence, at every realm - while a realm cost about three fifths
more than the last and a cultivator's attributes were written exactly once, at creation, and never
again. Body Tempering took 88 sessions and Divine Transformation 3,626: the game ended at the second
realm. A session is now a share of the stage it fills (the stage's cost over twelve), worked by the
cultivator's attribute and every multiplier already in the chain, so a realm takes about the same
hundred sessions wherever it sits and the operator's cooldown sets the calendar. Closed-door
seclusion is paced the same way. Crossing a realm now raises the cultivator: one point of will, or
body on the body path, and one of their path's own attribute. The worlds above the mortal one are
thick with qi - a new `world_qi_density` in the content, 1.6 in the Spiritual World, 2.6 in the
Immortal, 4.0 in the Celestial - which is the reward for crossing into one, and the sheet and the
session both name it. Four balance fixes ride along: an untreated qi deviation deepens each time it
is taken (Force was strictly the best stance while it was pinned at severity 1), a session at a full
stage banks no Insight XP and risks nothing (it was an endless farm), a deployed array is read once
for both meditation and seclusion instead of by two copies of the same query, and the ground is
priced for the body path too. No schema change.

**1.0.0** (rc.4) finishes the cultivation system: the place, the spending and the pages. Where a
cultivator sits is worth something the engine prices - a road-side shrine, a city's temple quarter,
a sect gate, a residence chamber, a deployed array - and the Here line, the session's result and the
sheet all name the ground and its rate. Insight XP, which accrued almost everywhere and was spent
almost nowhere, now buys two things besides the realm gate: a seized moment, one more roll after a
failed breakthrough at the same stage, once a stage, without asking for the stage's essence again;
and a bonus on a Law comprehension (`/law comprehend` with `spend_insight`). The cultivation hub's
ten pages named after commands become four named after the work - Cultivate, Body, Path, Arts - with
every action still one tap in; a hub page may now gather several roots, and a gathered page names
its rows in full (`Law Status` beside `Aptitude Status`). And every 2d10 roll in the game prints the
chance it had beside what fell, so a failure reads as unlucky or hopeless rather than arbitrary. No
schema change.

**1.0.0** (rc.3) is a better cultivation system and menu. The main menu is four rows of four -
You, World, Doing, Home - under a header of live facts (where you stand, your realm and stage with
the essence, the trade offers waiting), with Begin when there is no character and Back to the hub
you left; NPCs and Inner World are labelled as such. The cultivation hub opens on a sheet the engine
computes in one query (`cultivation.status`): the essence bar, the stance and when the next session
is ready, the odds of the next breakthrough with what moves them, today's multipliers, the body
path and the Insight XP. Meditation has a stance, kept by the engine and applied to every session:
Circulate (the full gain), Refine (a fifth slower, banks two Insight XP a session and deepens
stage-9 refinement) or Force (a third faster, and fifteen times in a hundred a qi deviation - a
real condition, treated like any other). A breakthrough shows its odds before and after the roll,
from the one modifier the roll uses. And stage 9 into a new realm is a gate: it needs an insight
banked from Insight XP (five at the mortal gate, five more a realm, `/cultivation → Insight`) or a
completed Realm Perfection, and the insight is spent on the crossing. The stance and the banked
insight live in `world_state` under the player's id; no schema change.

**1.0.0** (rc.2) is the GM's view of v0.39 and the content it left thin. The realm rotation the
tick keeps rides on `secret_realm.status`, shows on the dashboard's events page, in `/realm → Secret
Realms → Status` and in the inn's rumours (an open realm anywhere in the world is public news; the
next on the rotation is what the tellers bet on). The trades between cultivators are on the
dashboard's economy page with one audited action, `admin.trade.void`, that closes an open offer -
nothing moved, so nothing is refunded. Exploring a waystation, a hunting ground, a ruin or a shrine
reads from a pool of that place's own (four new scene kinds, three lines a tier each). And the
forty-three local auction floors each have a paragraph of their own - its river, crater, terrace or
court, its broker by name, and where its protection ends - closing the last content item on the
punch list. No schema change.

**1.0.0** (rc.1) is the release candidate: the roadmap's rc bars a machine can meet. The migration
drill bootstraps a database at every shipped schema, seeds a row in every table that will take one
and opens it with the current release, holding every table, column and row (`test_migration_drill`);
the README title, the changelog's release status line, the README's schema number and the Go version
across `go.mod`, the README and the engine's image are held to the stamps by tests; this file is
trimmed to a paragraph per minor; `docs/COMMISSIONS_DESIGN.md` is marked shipped. What stays for the
operator: the backup → restore drill on the NAS, a timed clean install, and two weeks at rc without a
P1. `VERSION` is 1.0.0; the tag carries the `-rc.N` suffix, as the release channel has always worked.
No schema change.

**0.40** is GUI II - one message is the whole GUI: a Menu button on every panel and a menu that opens
hubs in place, next-step buttons under a result, long results paged in the panel, a Here line in every
header, Reopen on an expired panel, a grouped travel picker, pickers that replace each other, and a
confirm before a red button.

**0.39** (schema 39) is the roads, the higher worlds, the trade: a place on every road (waystations
with stalls, hunting grounds, ruins, shrines), two sects and a tier of goods in every higher world,
eight secret realms opened in turn by the tick, typed play with two arguments, and trade between
cultivators at the inn.

**0.38** is city life - a quest pavilion in every capital and a notice board at every gate, a sect
envoys' hall, rumours through the viewpoint gate, an inn in every city with a common room, and
prosperity that trade moves and the shelves show; 0.38.1 shows a short result inside the panel.

**0.37** (schema 38) makes the merchants bid - on the tick, at the next minimum, within a valuation
the market sets and a purse that pays as escrow.

**0.36** is gates and districts - a road journey ends at the gate facing the road you came by, the
capitals have four compass districts and every city one, each with its own people, and the
capitals' shops are a tier better and dearer; 0.36.1 joins Greenriver Town to the roads and walks
the city in the engine playtest.

**0.35** (schema 37) is the city shops - a hundred and four across the forty-eight cities, found by
exploring, entered by travelling, with a keeper to buy from and sell to.

**0.34** (schema 36) is Gameplay-complete II - the engine half of the playtest as a script, the
checklist for the live half, the punch list, the `#playtest` board where testers react, and the
travelling merchants who buy what a floor could not sell and walk the roads; 0.34.2 gives each
merchant a shop of their own.

**0.33** (schema 35) is Gameplay-complete I - every id parameter has a picker, typed play fills one
argument from the line, the callerless `fate.adjust` is gone; 0.33.1 puts an auction house in every
city with live channels and `/menu`.

**0.32** (schema 34) is Hardened II - mute, freeze and ban from Discord with a duration the engine
expires, force-end-scene, every moderation audited so undo covers it, and backups that are pruned,
capped, sealed with an operator key and copied off the box.

**0.31** is the narrator budget - explore and hunt read from a procedural pool unless a player
presses Narrate it, one per-player bucket meters every door, and the ten-dollar switch picks the 50
or 1000 a day allowance from the dashboard.

**0.30** (schema 33) is Authority II - the engine derives the seclusion environment, market pricing
and the world-status reads are engine queries, the DB layer writes only presentation tables, eighty-one
dead rule functions are gone; 0.30.1 builds the sect residence up facility by facility.

**0.29** is Hardened I - the doors fail closed: the engine refuses to run or answer without a token,
the dashboard locks a guessing address and refuses cross-origin mutations, both listeners default to
loopback outside Docker, dependencies are hash-locked and images digest-pinned, CI runs the Go suite
under `-race`; 0.29.1 runs the release job behind the same CI checks.

**0.28** corrects what a `400` is allowed to prove and picks narration routes from OpenRouter's live
free catalogue on the dashboard. **0.27** adds the daily one-token liveness check that retires dead
routes. **0.26** adds the optional direct Google AI Studio route outside the shared budget. **0.25**
(schema 32 in 0.24) remakes the dashboard into five grouped tabs and reorganises `.env.example`;
0.25.1 makes `$` the typed-play prefix. **0.24** is the Quests workbench with pinned terms.

**0.23** closes Authority I - the last 21 Python writer methods are engine actions - and answers a
second external review; 0.23.2 fixes the BusyBox updater. **0.22** (schemas 29-31) is Commissions -
work offered by a named NPC with terms, a deadline the tick enforces and a cooldown for abandoning -
and the P0 review fixes: an atomic tick, idempotent duplicates, a drained shutdown, a restore that
cannot lose an acknowledged write. **0.21** opens Authority I, adds typed play, the Teardown control,
manuals as items with a genuine tier-0 entry manual per sect, and the first AI changes.

**0.20** completes the `main.py` split into `app/bot/`, groups the modules into `rules`, `ops`, `ai`
and `dashboard`, and adds the release channel with `update.sh`. **0.19** (schemas 26-28) is a
cultivation-depth pass, the Components V2 hub layout rolled out to every hub, the Admin Console
controls, and forty-eight point releases of production fixes. **0.18** (schemas 18-25) completed the
staged authority cleanup: forage, crafting and companions, canonical time, unified lifespan, multi-hop
road travel, caravans, dashboard-owned Discord setup, and the removal of the obsolete Python
mechanical authority paths.

## Release status — v1.0.0

- Current release: v1.0.0 (rc.5): a session is a share of the stage it fills, so every realm takes
  about the same work; crossing a realm raises the cultivator; the higher worlds are thick with qi;
  four balance fixes. Tagged `v1.0.0-rc.5` on the beta channel; the NAS drills and two quiet weeks
  make it `v1.0.0`.
- v1.0.0 (rc.4): the ground a cultivator sits on priced and named; Insight XP spent on a seized
  moment and on Laws; the cultivation hub as four pages; every roll printing its chance.
- v1.0.0 (rc.3): the menu as four rows of four under live facts; the cultivation sheet; meditation
  stances; the odds of a breakthrough shown; the realm gate with its banked insight.
- v1.0.0 (rc.2): the realm rotation visible on the dashboard, in `/realm` and in the rumours; the
  trades on the dashboard with an audited void; narration of the road-side sites' own; the
  forty-three local floors with prose of their own.
- v1.0.0 (rc.1): the migration drill across every shipped schema, the stamps held to the README,
  the changelog and the Go version by tests, the changelog trimmed, the commissions design marked
  shipped.
- v0.40.0: GUI II - one message is the whole GUI: a Menu button on every panel
  and a menu that opens hubs in place, next-step buttons under a result, long results paged in the
  panel, a Here line in every header, Reopen on an expired panel, a grouped travel picker, pickers
  that replace each other, and a confirm before a red button.
- v0.39.0 (schema 39): the roads, the higher worlds, the trade - a place on every
  road (waystations with stalls, hunting grounds, ruins, shrines), two sects and a tier of goods in
  every higher world, eight secret realms opened in turn by the tick, typed play with two arguments,
  and trade between cultivators at the inn.
- v0.38.1: results in the panel - a plain, short hub result edits the panel in
  place instead of spawning a message; embeds, results with buttons, files and long text still land
  beside it.
- v0.38.0: city life - a quest pavilion in every capital and a notice board at every
  gate, a sect envoys' hall, rumours through the viewpoint gate, an inn in every city with a common
  room, and prosperity that trade moves and the shelves show.
- v0.37.0 (schema 38): merchants bid - on the tick, at the next minimum, within a
  valuation the market sets and a purse that pays as escrow; outbid, they are refunded; holding the
  high bid at the close, they win.
- v0.36.1: Greenriver Town joins the roads (gates east to the capital and north to
  Riverguard), and the engine playtest walks the city - the gate, a district, a shop found, bought
  from and sold to, and a merchant taking an unsold lot and reselling it - ninety-six steps clean.
- v0.36.0: gates and districts - a road journey ends at the gate facing the road
  you came by, the capitals have four compass districts and every city one, each with its own
  people, and the capitals' shops are a tier better and dearer.
- v0.35.0 (schema 37): the city shops - a hundred and four across the forty-eight
  cities, differing by city in kind, tier and shelf, found by exploring the city, entered by
  travelling to them, with a keeper to buy from and sell to inside.
- v0.34.2: the merchant's own shop - three to five lines of ordinary goods per
  merchant in content, restocked every time it comes home, listed beside the floor finds.
- v0.34.1 (schema 36): the `#playtest` board - one message per hub page,
  testers react ✅ ❌ 💡 and the GM reads the tally - and the travelling merchants, who buy what an
  auction floor could not sell, walk fixed loops of cities and resell it to whoever meets them in a
  city or on the road.
- v0.34.0: Gameplay-complete II - the engine half of the playtest as a script,
  the checklist for the live half on file, the punch list, and the two defects the run found
  fixed (a commission could never be turned in; Reset Cooldowns missed the trial retry).
- v0.33.1 (schema 35): an auction house in every city - grand in the capitals,
  local and smaller elsewhere - live channels where lots are posted, bid on and struck as it
  happens, and `/menu`, one panel that opens any hub.
- v0.33.0: Gameplay-complete I - every id parameter has a picker, typed play
  fills one argument for `/travel` and `/use` from the line, and the callerless `fate.adjust` is gone.
- v0.32.0 (schema 34): Hardened II - mute, freeze and ban from Discord with a
  duration the engine expires, force-end-scene beside them, every moderation audited so undo covers
  it, and backups that are pruned, capped, sealed with an operator key and copied off the box.
- v0.31.0: the narrator budget - explore and hunt read from a procedural pool
  unless a player presses Narrate it, one per-player bucket meters every door, and the ten-dollar
  switch picks the 50 or 1000 a day allowance from the dashboard.
- v0.30.1 (schema 33): one home built up facility by facility - the sect
  residence grows with contribution points under rank and stage gates, and a homestead of one's own
  is founded at Deacon or higher.
- v0.30.0: Authority II - the engine derives the seclusion environment,
  market pricing and the world-status reads are engine queries, the DB layer writes only
  presentation tables, and eighty-one dead rule functions are gone.
- v0.29.1: one GitHub workflow - the release job runs behind the same CI
  checks every pull request gets, on the commit they just proved.
- v0.29.0: the doors fail closed - the engine refuses to run or answer without
  a token, the dashboard locks an address that keeps guessing and refuses cross-origin mutations,
  both listeners default to loopback outside Docker, dependencies are hash-locked and images
  digest-pinned, and CI runs the Go suite under `-race`.
- v0.28.0: a `400` no longer retires a route unless removing the reasoning
  parameter is what fixes it, and narration routes are chosen from the dashboard against
  OpenRouter's live free catalogue.
- v0.27.0: a daily one-token liveness check retires routes that answer
  401/403/404, so a withdrawn slug stops costing a free-tier slot per narration.
- v0.26.1: the withdrawn `z-ai/glm-5.2:free` hop is out of both chains, and a
  narration timeout is no longer misreported as a TLS/certificate failure.
- v0.26.0: an optional direct Google AI Studio route leads both chains when a key is
  set, outside OpenRouter's daily free budget.
- v0.25.4: the AI Studio key instructions say where to get the key, and the routine
  chain summary in `.env.example` matches the defaults under it.
- v0.25.3: MiniMax M3 is out of the routine chain; GLM 5.2 is the routine fallback
  as well as the epic one.
- v0.25.2: `.env.example` reorganised so the five required values are the first
  thing in it, and the first-run message names all five.
- v0.25.1: the typed-play prefix default is `$` rather than `>`.
- v0.25.0: the dashboard remade - 23 flat tabs become five grouped ones, and the
  worst page goes from eleven stacked tables to eleven tabs. No schema change.
- v0.24.0: the Quests workbench - one page for every definition a player can be
  given, an editor where there was none, and pinned terms so an edit cannot rewrite a deal a player
  already accepted. Schema 32.
- v0.23.2: the updater could not install v0.23.0 or v0.23.1 on a QNAP; the post-install manifest
  check used GNU-only checksum flags that BusyBox refuses.
- v0.23.1: eight logic errors from a second external review, each with a regression test verified
  against the defect.
- v0.23.0: Authority I closes - the last 21 Python writer methods are engine actions.
- v0.22.5: the engine drains in-flight requests on shutdown instead of cutting them,
  and closes storage only once the drain has finished.
- v0.22.4: a duel's preconditions are re-checked at accept and on every action, and
  a duel that stops being legitimate ends rather than erroring forever. Schema 31.
- v0.22.3: a restore can no longer lose a write it already acknowledged - a
  maintenance barrier quiesces traffic before the safety backup is taken.
- v0.22.2: the P0 review findings - an atomic simulation tick, idempotent duplicate
  authoritative requests, and quest rewards paid by the transaction that completes the quest.
- v0.22.1: the rest of the giver roster - two old men whose terms are undisclosed
  (one pays far above what he implies, one far below) and a disciples-only board for every public
  sect. Schema 30.
- v0.22.0: commissions - a giver NPC offers work in character, one at a time,
  with terms fixed at accept and four engine-owned outcomes in which failing and abandoning cost
  the same. Closes the last v0.21 authority row (`accept_quest`). Schema 29.
- v0.21.6: realm capitals are visible only while you stand in them - a presence
  role per capital the bot adds and removes by player location, with the channel permissions to match.
- v0.21.5: the narrator's input fence, the v0.25 content batch (every location
  has hints and encounters, every NPC has narrator fields), and cooldown errors as a wait.
- v0.21.4: every public sect has an authored tier-0 entry manual, and it is the
  one bestowed on joining.
- v0.21.3: one manual on joining a sect (engine-owned), and the 148-manual
  catalog materialised into `content/world.json` so Go and Python read the same content.
- v0.21.2: Teardown (delete everything the bot owns on Discord, from the
  dashboard, typed confirmation) and the realm-capital visibility gate actually gating.
- v0.21.1: typed play - `> action` lines route to existing handlers, speech is free,
  per-player budget on every line that can reach the engine or the narrator.
- v0.21.0: Authority I begins - the v0.21 gate (DB-write allowlist) and `item.use`.
- v0.20.9: the updater authenticates its backup call to the engine, aborts on a
  failed copy, and verifies the installed tree against the manifest before starting.
- v0.20.8: fixes `update.sh` dropping the `.sha256` sidecar asset when it
  wasn't the first asset on the release.
- v0.20.7: the two v0.20.6 builds merged - Quest Forge (schema 28) and the
  dashboard item-grant fix.
- v0.20.6: shipped twice from two sessions (build A: Quest Forge; build B: dashboard item grants).
- v0.20.5: the updater preflights `.env` before stopping anything.
- v0.20.4: release channel (GitHub Releases, bot announcement,
  `update.sh --fetch/--upgrade`) and the roadmap to v1.0.0.
- v0.20.3: test suite audited (70 → 59 files); dashboard gate path fixed.
- v0.20.2: top-level tidy-up; `RELEASE.txt` archived, deployment
  scripts deliberately kept at the root.
- v0.20.1: `app/` grouped into `rules/`, `ops/`, `ai/`, `dashboard/`
  packages with a layering guard.
- v0.20.0: the `main.py` split complete: the wiring moves to
  `surface.py`, `main.py` is the composition root.
- v0.19.48: phase 9e of the `main.py` split: scene and sense
  commands leave `main.py`; no player command is defined there any more.
- v0.19.47: phase 9d of the `main.py` split: battle and law
  commands leave `main.py`.
- v0.19.46: phase 9c of the `main.py` split: exploration, craft
  and alchemy commands leave `main.py`.
- v0.19.45: phase 9b of the `main.py` split: character, economy
  and abode commands leave `main.py`.
- v0.19.44: phase 9a of the `main.py` split: aptitude, territory,
  secret-realm and cultivation commands leave `main.py`.
- v0.19.43: phase 8 of the `main.py` split: the shared UI, the bot
  class and the last `/admin` commands leave `main.py`.
- v0.19.42: phase 7 of the `main.py` split: the admin operations
  (`world_ops.py`, `inspect_sim.py`) and shared `pickers.py` leave `main.py`.
- v0.19.41: phase 6 of the `main.py` split: the admin server layer
  (channel messages, bugs forum, server setup, dashboard bridge) leaves `main.py`.
- v0.19.40: phase 5 of the `main.py` split: the admin core moves to
  `app/bot/admin/core.py`.
- v0.19.39: phase 4 of the `main.py` split: the rest of the shared
  plumbing leaves `main.py`; zero deferred `main` imports remain.
- v0.19.38: reasoning off on every narration request and new
  fallback chains (MiniMax M3, GLM 5.2; Nemotron Super dropped).
- v0.19.37: escalating backoff for failing OpenRouter routes and
  provider/BYOK visibility in `ai_status`.
- v0.19.36: narrator no longer posts a reasoning model's scratchpad as
  narration; the guard runs on every player-facing reply and salvages or rejects.
- v0.19.35: phase 3 of the `main.py` split: `locations.py` leaves
  `main.py`; three more deferred hooks gone (five of ten remain, all for phase 4).
- v0.19.34: phase 2 of the `main.py` split: `services.py` and
  `formatting.py` leave `main.py`; three deferred hooks and two `roll_line` copies gone.
- v0.19.33: phase 1 of the `main.py` split: test scaffolding
  (package-wide source scans, stage-4 modules guarded), no `app/` change.
- v0.19.32: the Bugslayer Sword reward item and its passive, `/admin player
  grant` hardening, a split-stage-4 startup crash fix, and the tests that
  split had broken repaired.
- v0.19.31: a hub-UI ephemeral-visibility fix and privacy pass merged in (with
  two parts deliberately rejected) from a reviewed community patch.
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
- **Schema 28** added the Quest Forge definition table (`quest_definitions`).
- **Schema 39** added the trade offers between cultivators (`trade_offers`) (v0.39.0).
- **Schema 38** added the merchant holding a lot's high bid (`auctions.merchant_bidder`) (v0.37.0).
- **Schema 37** added the city shops' shelves (`shop_state`, `shop_stock`) (v0.35.0).
- **Schema 36** added the playtest board (`playtest_channel_id`, `playtest_items`) and the travelling
  merchants (`merchant_state`, `merchant_stock`, `auctions.merchant_buyer`) (v0.34.1).
- **Schema 35** added the live-auction channel per house (`auction_house_channels`) and the card per
  open lot (`auction_lot_messages`), Discord ids only (v0.33.1).
- **Schema 34** added the moderation expiry pair (`muted_until`, `frozen_until`) and `is_banned` to
  `characters` (v0.32.0).
- **Schema 33** gave the sect residence (`sect_abodes`) its six facility levels (v0.30.1).
- **Schema 32** added `terms_json` to `character_quests`: the objectives and rewards each player
  accepted, so an edit to a definition cannot rewrite a deal that was already struck.
- **Schema 31** added `location` to `pvp_matches`, so a duel in progress knows where it is fought.
- **Schema 30** added the giver refinements: `requires_sect`, `reward_visibility` and `boast` on
  `quest_definitions`.
- **Schema 29** added commissions: giver, realm band, tier, owner, deadline, variants and seed on
  `quest_definitions`; the commission flag, absolute deadline, accepted variant and resolved minute
  on `character_quests`; and the refusal cooldown, per-outcome counters and last outcome on
  `npc_relationships`.

See `docs/history/CHANGELOG_0_18_TO_0_40.md` for the per-release detail.

## Release notes

See `docs/history/V019_RELEASE_NOTES.md` for the current release's cultivation-depth audit, dashboard coverage gaps, and
combat authority-migration fixes. See `docs/history/V018_RELEASE_NOTES.md` for the complete staged-authority, road/caravan,
setup, cleanup, migration, security, and upgrade summary that v0.19 builds on.
