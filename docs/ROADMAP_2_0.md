# Roadmap to v2.0.0

Written at **v1.39.0** (2026-10-23) from an inventory of the tree, the way `docs/history/ROADMAP_1_0.md`
was written at v0.20.4. That file closed with v1.0.0: its three bars were met, its milestones were
renumbered twice when releases overtook them, and it moved to `docs/history/` with the note that
nothing in it describes the current release. Thirty-nine minors have shipped since, thirty-two of
them between 26 September and 22 October 2026, and none of them was planned anywhere: each closed
what the release before it turned up, or what a project-wide review found. That has been the right
way to work and it leaves nothing that says what 2.0 is. This file says it.

The shape is 1.0's. Three bars, each a statement that is true and guarded by a test; milestones
that each name their gate; a table at the end that is updated when a milestone ships or is
overtaken. The numbers from v1.40 are the intended order, not a promise, and the owner's call on any
of it is recorded here when it is made, the way the engine-credential split was declined on
2026-09-07 and stayed declined.

## What 2.0 means

1.0 meant engine authority finished, the doors hardened, and no dead end a player can reach. All
three held, and the thirty-nine releases after it show what the next fault looks like: never a
broken mechanic but a finished one with one wire missing, found one instance at a time until
v1.36.0 wrote the gate over the class and found seven and twenty-three in one walk. 2.0 is the
release after which these three statements are true:

1. **Every fault class has its gate.** Each standing rule in `CLAUDE.md` is held by a test that
   reads the class the rule names - every payload field, every table that carries a person, every
   decode, every site of a one-door rule - or is recorded, with the reason, as a rule no reader
   can hold. A rule that is prose alone is a rule the next review finds broken; the thirty-seven
   faults of v1.34.0 and the thirteen of v1.33.0 were each an instance of a rule already written.
2. **The live pass is walked.** `docs/playtest/v<version>.md` ends in the table of what only a real
   server can show. It has thirty-nine rows; thirty-four were ticked at v1.0.0 and five have never
   been ticked, and no row has been walked on any of the thirty-nine releases since. The two
   harnesses prove everything else, and the checklist's own rule is once per release. 2.0 is cut
   from a tree whose every live row carries a `v2.0.0` stamp.
3. **A world runs at age.** The game is built to keep going whether players are there or not, on a
   CPU-only NAS, with eight simulation systems on fixed intervals, bounded catch-up after downtime,
   and tables that only grow - history, domain events, the registry of people the world makes for
   itself. Nothing measures what a tick costs on a world that has run for years. 2.0 holds each
   system under its own interval at the shipped time scale, on a world aged through the real tick,
   and has a retention decision for every table the drill shows only growing.

A fourth statement is not a bar and is restated so it is not forgotten: **the AI does one thing only
a model can do.** It closed as a bar at v0.31.0 and nothing since has reopened it; the live rows of
the checklist are the only check that a route still narrates, which is bar 2's business.

## Where we are

| Bar | State at v1.39.0 |
|---|---|
| Authority | Closed at v1.0.0 and held since by gates over classes rather than instances: `payload_fields_test.go` walks every struct the engine unmarshals a request into (v1.36.0), `samsara_tables_test.go` every column that carries a person (v1.36.0), `TestNoPayloadIsDecodedIntoAPlainMap` every decode of a JSON object (v1.39.0), `fixture_foreign_keys_test.go` every foreign key production declares (v1.34.0). The Python side writes only the fifty-one presentation writers `PRESENTATION_WRITES` lists, held by `test_v0_30_gate_the_db_layer_writes_only_presentation_tables`. **Open:** that allowlist is a Python test. The engine's generic SQL door (`/v1/db/session`, `/v1/db/batch`) accepts a write from any holder of the engine token, which is the cheap middle the 1.0 roadmap named when it declined the credential split and never built. |
| Hardened | Hardened I (v0.29.0) and II (v0.32.0) shipped; CodeQL on every pull request; `govulncheck` against the Go version in `go.mod`, raised to 1.26 for its advisories. Nothing open but the SQL door above. |
| Gameplay | Twelve player hubs and `/admin`, 274 actions on the checklist, 139 Go action files. `DEFERRED_OPERATIONS` has been empty since rc.38: the engine playtest drives every operation the engine answers. The Discord sweep presses every leaf but three (lockdown, reset, seclusion), each driven in its own section, and since v1.38.0 decides every refusal it meets against a roster. Content: four worlds, forty-eight cities, 484 locations, 585 NPCs, 402 items, 120 shops, 140 commissions, a realm road of 31 stages, 13 sects, 41 recipes. The punch list is fifteen entries, every one deferred with the owner's decision; 206 are closed in `docs/history/TODO_FIXED.md`. |
| The AI | Closed as a bar at v0.31.0. The procedural pool is 162 variants over the four tiers. The narrator's history recall is filed by world since v1.31.0. The only check that a live route narrates is the checklist's live rows, ticked at v1.0.0. |
| The live pass | Thirty-nine rows; thirty-four ticked `v1.0.0`, five never, none since. |
| At age | Unmeasured. A hub refresh is held to one session and about twenty-five round trips by a counting gate (v1.31.2); the three capped NPC walks rotate their start (v1.34.0); the attention feed flags a system that missed its own interval (v1.39.0). No test ages a world. |

Test surface: 2,984 Python test functions in 227 files across `unit/`, `integration/` and
`contracts/`; 1,492 Go test functions in 229 files; about 160 of those files read source by AST.
CI runs the Python suite as four shards, the Go suite under `-race`, and boots both images. Schema
83, and `test_migration_drill.py` opens a database from every schema since 1 with the current
release. Go 1.26.

## The release channel

Unchanged. GitHub Releases built by the `release` job of `ci.yml` from a `v*` tag, the zip and its
`.sha256` sidecar, `update.sh --check/--fetch/--upgrade` on the NAS and the Server Update card on the
dashboard (v1.4.0) over `update_watch.sh`. A `2.0.0` tag is three parts, which `ENTRY` in
`app/rules/changelog.py` and the release-version gate read; the one literal in
`tests/python/contracts/test_release_version.py` changes with the stamps as on every bump.

Each milestone below ships as a minor on the way, as the 1.0 milestones did. Betas precede 2.0
itself; nothing else here needs the beta channel.

## Milestones

Each names its **gate**: the test or check that must exist and pass before the milestone is called
shipped. If a release takes a milestone's number for something else, the milestone is renumbered
here in the same release.

### v1.40 — The gate ledger (Classes I)

The thirty standing rules in `CLAUDE.md` each carry the release that found them; about half name
the test that holds them and the rest are prose. The ledger is one table, `docs/GATES.md`: one row
per standing rule, the class it names, the gate that reads that class (a test node id), what its
drill prints when the rule is broken, or the word `prose` with the reason no reader can exist.
Writing it is the inventory; what it finds is the work of v1.41.

Three rows the inventory settles first, because a grep already asked the wrong question of them:

- `moveCharacterTx`, the one way out of a world (rc.44), is named by one Go test file. Whether that
  file walks every site that writes a character's place, or only drives the function, is the row.
- `_history_allowed`, the narrator's viewpoint filter (v1.31.0), is named by no test file. A test
  may reach it through its caller; the ledger says which, or says none does.
- `decodeJSONMapStrict` is named by no test file either, and its class gate is
  `TestNoPayloadIsDecodedIntoAPlainMap`, which reads decodes rather than the name. That is the
  ledger's point: a name count is not a reader (v1.0.0-rc.58), and the row names the test that
  reads the class.

**Gate:** `tests/python/contracts/test_gate_ledger.py`. It parses the Standing rules section of
`CLAUDE.md` (each rule is one bullet with a bold lead-in), holds that every rule has a row in
`docs/GATES.md`, that every gate a row names exists as a collectable test or a Go test function, and
that a `prose` row gives a reason. The parser is asked for a rule it knows before any assertion
runs (rc.57). The ledger joins the files `test_claude_md_stays_small.py` resolves pointers in.

### v1.41 — The doors held by a walk (Classes II)

Every `prose` row of the ledger whose rule has a class gets its reader. The shape exists: the
clock's AST gate (rc.39), the insight XP's (rc.58), the catalogue lookup's (v1.7.0), the hub-refresh
counting gate (v1.31.2), and the field, table and decode walks above. The candidates the inventory
is expected to name, each one walk over its package:

- **The purse.** Every statement that writes a currency column goes through `walletDeltaTx`
  (rc.43), read off the SQL strings in `internal/game` by AST rather than by searching for the
  column name.
- **The way out of a world.** Every statement that writes a character's place goes through
  `moveCharacterTx` (rc.44), the same way.
- **Hidden is hidden for every consumer.** Every Python reader of `world_history_events` and of the
  deed tables passes its rows through the viewpoint filter before anything scores or prints them
  (rc.24, v1.0.13), read by call graph from each consumer.
- **A panel provider never raises.** Every provider registered for a panel line is wrapped by the
  one function that costs the line and not the card (v1.0.10), read off the registrations.
- **Record before the reply, tell after it.** Every command handler that writes a quest report does
  so before its first reply (v1.0.5), read off the statement order.
- **Collect before the engine commits.** Every door that deletes a person's rows calls the thread
  collector first (v1.0.8, v1.7.0), read off the four leaving doors.

A candidate the walk shows already held is a row updated, not a gate written. A rule that has no
class - "fix every site of a rule, not the one reported" is advice to the person, not a property
of the tree - stays `prose` with that reason.

**Gate:** no `prose` row in `docs/GATES.md` names a class, held by the ledger test; each new gate
drilled, with the drill's sentence recorded in its row.

### v1.42 — Hardened III: the engine's SQL door

The 1.0 roadmap declined a second engine credential and named the cheap middle: refuse *writable*
generic SQL for the bot once bootstrap has finished. It was never built, and since v0.30.0 the
allowlist that keeps Python to its presentation writers has lived in a Python test, which is a
bound that lives in the client (rc.48). The honest end is the one `PLAYER_MUTATIONS` reached in
v0.23.0: the fifty-one presentation writers become named engine operations - the channel message,
the thread and hub channel ids, the RAG and FTS rows, the slow-query log, the operational alerts,
the server config, the schema bookkeeping - and `PRESENTATION_WRITES` is empty with a test that
asserts it stays so. Then the engine refuses a write statement on `/v1/db/session` and
`/v1/db/batch` outright, and `app.database.bootstrap` runs its migrations through the engine's own
migration door, which it already has: the engine is what opens the database and runs the chain.

Two things decided before the work starts, so they are not re-decided inside it. The dashboard
reads through Go query sessions and keeps doing so; this milestone touches writes only. And the
generic SQL *read* door stays, because the RAG assembles its scene packet from it and a read cannot
corrupt the file; a later milestone may count its round trips, as v1.31.2 counted the panel's.

**Gate:** `TestAWriteThroughTheGenericDoorIsRefused` in `internal/server`, driving a real `INSERT`
through `/v1/db/batch` and `/v1/db/session` and reading the refusal; `PRESENTATION_WRITES == {}` in
`test_authority_boundary.py`, held the way `PLAYER_MUTATIONS` is; and the engine playtest, which
already drives every operation, drives the new ones because `test_playtest_coverage.py` makes it.

### v1.43 — A world at age

A drill, not CI, in the shape of the two playtests: `scripts/age_world.py --launch --years N` builds
a scratch engine on the real content, runs the eight systems through `simulation/run-due` for N
world-years at the shipped time scale, and prints for every tick the wall time of each system
against its interval from `SystemIntervals` and the row count of every table, so the growing ones
name themselves. The bound is the engine's own number, never restated in the script (rc.55): a
system that takes longer than its interval at the shipped scale has fallen behind the clock, which
is what the attention feed flags on a live server (v1.39.0) and what this drill shows before one
does.

What it finds is two kinds of work. A system whose cost grows with the world is batched or capped
the way the NPC walks were (`rosterTurn`, v1.34.0), with a test that asks who never got a turn. A
table the drill shows only growing - the candidates are the world history, the domain events, the
channel messages, the slow-query log, the audit log and the registry of people born to the
simulation - gets a retention decision per table, the owner's, recorded in `docs/CONFIGURATION.md`
beside the backup retention that already exists (v0.32.0), and a `maintenance` step that enforces
it. A table whose rows are a promise to a player (the audit log, provenance) is kept, and says so.

**Gate:** the drill is in the tree, `test_playtest_gate.py` holds that it exists and names every
system in `orderedSystems`, and the release checklist carries one live row: the drill was run at
the stated years on this release and no system fell behind its interval. The retention decisions
are each a Go test on the maintenance step, in the shape `TestBackupRetention` set.

### v1.44 — The content track's remainder

The 1.0 roadmap's content track was started early and most of it landed reshaped: grades came as
the crafted grade (v1.7.0) rather than a catalogue field, craft quality feeds the grade, the
vocabulary is in the pool, and the family's tier reaches the start through its lesson and send-off
(v1.0.3). Three items never landed and are listed so they are not lost:

- **Grade-weighted rewards.** `applyCanonicalRewardTx` reads no grade; a hunt or a forage in the
  Celestial World pays the same catalogue draw as one in the Mortal. Grade enters the draw by the
  tier of the world the reward is paid in, which is already how the patron's gift tiers its
  material (v1.0.0).
- **Salvage.** `item.salvage` was the one genuinely new piece and the material sink the economy
  lacks; no production Go names it. One action, fixed yields per grade in the coin of the item's
  world, a provenance row, an audit row above a grade threshold - as designed, with the price walk
  (v1.0.17, v1.8.0) run over its yields so a salvage never pays more than the shelf.
- **Seeded invention** (Commissions step 5): a giver invents a personal commission from RAG reads.
  Held back in 1.0 until the giver pool read well on a live server, which is bar 2 again; it waits
  for the live pass and is the first content item after it.

Everything else content is a content pass, which needs no milestone: v1.32.0 and v1.38.0 were
content passes and shipped as minors.

**Gate:** each item is one engine action with its Go test, driven by the engine playtest (the
coverage gate makes that automatic), and a Python boundary test that the surface offers it only
where the engine will not refuse (rc.46).

### The documents at 2.0

Housekeeping, and it is a milestone because the 1.0 rc did it and the tree has grown since.

- `VERSIONS.md`'s "Current release" bullet is one line of 22,334 characters that recites every
  release since v1.2.2; `test_release_version.py` reads its stamp and `#updates` posts the opening
  sentence of each entry. At 2.0 the line is one sentence again and the 1.x paragraphs move to
  `docs/history/CHANGELOG_1_X.md`, the way 0.18 to 0.40 moved at rc.1, with the headline test
  following them.
- `docs/FINDINGS.md` is 748 KB. It is never loaded automatically, which is the point of v1.33.0,
  but `test_claude_md_stays_small.py` resolves every pointer into it by heading, so a split by
  major (`docs/history/FINDINGS_1_X.md`) is a change to the resolver first and the file second.
- This file closes with v2.0.0 and moves beside `ROADMAP_1_0.md`, and `README.md`'s pointer moves
  with it.

**Gate:** the release-version, headline and pointer tests, unchanged in what they hold and changed
in where they look.

### v2.0.0-rc.N → v2.0.0

Release candidates go to the **beta channel** only, as 1.0's did.

- The migration drill, from every shipped schema to current, is already in CI and stays.
- **The live pass, every row, stamped `v2.0.0`.** *(operator)* This is bar 2 and it is the one thing
  no machine here can do. A row that cannot be walked is deleted from the table with its reason,
  never carried unticked.
- Backup → restore drill on the NAS, documented. *(operator; carried from 1.0, where it was the
  operator's too)*
- Clean install from the README on a machine that has never seen the project, timed. *(operator)*
- Two weeks on the NAS at rc without a P1. *(operator)*
- The stamps: the seven places `test_release_version.py` holds equal, the schema in its three, the
  checklist regenerated with the previous ticks dated, the manifest rewritten.

`v2.0.0` is the rc that survived, re-tagged.

## Order and dependencies

```
v1.40 Gate ledger ──► v1.41 Doors by a walk ──► v1.42 Hardened III (the SQL door)
                                                          │
v1.43 A world at age ─────────────────────────────────────┤
                                                          │
                                        v1.44 Content remainder (after the live pass)
                                                          │
                                        the documents ──► v2.0.0-rc.1 … ► v2.0.0
```

The ledger goes first because every later milestone adds gates and the ledger is where a gate is
recorded; writing it is a week of reading and no code. The walks follow because they are what the
ledger finds. Hardened III is last of the three because it reshapes the DB layer, and a walk over
`internal/game`'s SQL is cheaper to write once the fifty-one Python writers are gone from the other
side of the door. The age drill is independent and can interleave with anything; the retention
decisions it forces are the owner's and wait for its first run. The content remainder waits for the
live pass on purpose, for the reason 1.0 gave: every item in it adds player-reachable surface, and
the pass is the check that the existing surface is sound. The documents are done at the rc, when
the 1.x history is complete.

## Not 2.0

Named so they are not re-raised as bars. Each is the owner's call and none has been made.

- **A second guild.** `GUILD_ID` is one value and the 2026-09-07 reasoning - one guild, one
  operator, one NAS behind one trust boundary - still holds every design decision after it. A
  second guild is a 3.0-sized question about whose world a world is, not a hardening item.
- **A paid or local narrator.** The free chain with a procedural floor is the design, and an
  operator with a key already has the AI Studio route past the ceiling (v0.26.0).
- **A fourth version part.** `1.0.13.1` would break the changelog parse; a small fix after 2.0 is
  `2.0.1`, as `CLAUDE.md` says.
- **New hubs.** Sixteen became twelve on the Most-used card's own numbers (v1.38.0); a new page
  earns its place by the same card, not by a roadmap.

## Keeping this file honest

Each milestone's gate is a test or a named drill. When a milestone ships, its section gains a
one-line "shipped in vX.Y.Z — gate: `…`" and the table below is updated in the same release. When a
release takes a milestone's number for something else, the milestone is renumbered here in that
release rather than left to drift, which is the lesson the 1.0 file recorded about v0.21.6 to
v0.28.0.

| Milestone | Release | Status |
|---|---|---|
| v1.40 Gate ledger | — | open |
| v1.41 Doors by a walk | — | open |
| v1.42 Hardened III | — | open |
| v1.43 A world at age | — | open |
| v1.44 Content remainder | — | open; waits for the live pass |
| The documents | — | open; done at the rc |
| v2.0.0-rc | — | open; the live pass and the NAS drills are the operator's |
| v2.0.0 | — | open |
