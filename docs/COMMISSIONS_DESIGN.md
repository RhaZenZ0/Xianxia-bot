# Commissions and typed play — design (shipped)

Written at v0.21.0 (2026-09-06), against the tree as it is. Intended path:
`docs/COMMISSIONS_DESIGN.md`, beside `docs/ROADMAP_1_0.md`. Every path and
line below was checked against the v0.21.0 archive; if the file moves, the
claim about it may not.

> **Status.** Typed play shipped in v0.21.1. Commissions shipped in
> **v0.22.0** — schema 29, `commission.accept` / `commission.resolve`, the
> tick expiry, the pool, the ladder, the voice block, the player surface and
> the GM tab — with one deliberate exception: **seeded invention is not
> built**. Its columns exist and its visibility rule is enforced and tested,
> but nothing writes a personal commission yet. See
> `docs/history/V022_RELEASE_NOTES.md`. Sections below describing invention are
> therefore still design, not code; everything else describes what is in the
> tree. **Marked shipped at v1.0.0-rc.1:** commissions have been in production
> since v0.22.0 and a board in every city and sect since v0.38.0/v0.39.0;
> seeded invention stays deferred past 1.0 (`docs/KNOWN_LIMITATIONS.md`). Typed
> play took a second argument in v0.39.0.

## What a commission is

A player talks to a Steward NPC in character. The Steward offers a piece of
work. The player accepts it; it becomes a real quest with real rewards. The
player can hold **one commission at a time**; it ends as completed, failed
or abandoned, and how it ended shapes what the Steward offers next.

The AI's role is exactly the one the README already gives it: it voices the
Steward. It does not decide what a commission is worth, whether the player
may have one, or whether the last one counted. Those are engine facts the
Steward performs.

Why this is the best use of the narrator budget: at 50 free requests a day,
the calls that matter are the ones only a model can do — a person with an
agenda answering arbitrary player text. Commissions put every call on that
path and keep the rest deterministic.

Two features, one document, because they share the seed builder, the
input fence, and the rule that a model never decides mechanics:

- **Commissions** — the Steward offers work; one at a time; four outcomes.
- **Typed play** — a `>` line in a scene channel is an action the engine
  resolves; an un-prefixed line is speech that costs nothing.

## Design summary — commissions

One pipeline, two producers, one gate difference.

- **One table, one validator, one accept path.** Both producers write
  `quest_definitions` rows in the same shape, pass through
  `validate_quest_definition` (`app/rules/quests.py:85`), and are accepted
  through one engine action. Nothing downstream cares which producer made
  a row.
- **The pool.** The batch forge (`forge_quests_from_history`,
  `app/bot/bot.py:272`) drafts offline from world history, tagged with a
  giver NPC and a realm band. The GM approves it into a pool with the
  existing `/admin world quests` flow. `owner_user_id` is NULL.
- **Seeded invention.** When the pool has nothing for this player, the live
  forge drafts a personal commission — but the drafter is fed a **seed**
  the code assembles from canonical state (realm, standing with the giver,
  open feuds, the last commission's outcome), never the player's text. The
  invariant in `app/ai/quest_forge.py` ("Player-supplied text never
  reaches this module") holds word for word. `owner_user_id` is the
  player. Auto-approved under a smaller budget; reviewed by the GM after
  the fact; nothing pays until completion.
- **The voice.** The player's dialogue goes where it already goes:
  `Narrator.talk_to_npc` (`app/ai/narrator.py:513`), labelled untrusted. It
  influences *whether* the Steward offers, never *what the quest says*.
- **One at a time.** An active commission blocks a second. That rule, not a
  cooldown, is the primary throttle: invention rate is capped by
  completion rate.
- **Four outcomes, engine-owned.** `active → completed | failed |
  abandoned`. `failed` and `abandoned` cost the same. Standing with the
  giver is the one number that ties outcome to next offer.

## The player's view

1. `/scene` talk to a Steward (today: `app/bot/commands/scene.py:106`).
2. The Steward either offers a commission, refuses (with the reason in his
   own terms), or checks on the one you hold. The offer carries the title,
   the objectives, the reward, and — if the commission has variants — the
   terms he is proposing.
3. Accept in the dialogue view. The quest appears in `/quests` like any
   other; progress advances through `quest.progress` as it does today.
4. It resolves one of three ways. Completed pays out and raises standing.
   Failed (deadline passed) or abandoned (the player chose to) costs
   standing, starts a game-time cooldown, and lowers the tier of the next
   offer. Abandon is a danger button in the quest hub that states its cost
   before confirming.

The player never learns whether a commission came from the pool or was
invented for them. It is one Steward.

## Selection at offer time

Runs in Python before any model call, entirely on engine facts, in this
order:

1. **Held commission?** Any `character_quests` row for this player with
   `commission = 1` and `status = 'active'` → no offer. The dialogue becomes
   a progress check; the seed passes the quest and its progress to the
   voice call.
2. **On cooldown?** `commission_cooldown_until_game_minute` on the
   relationship row is in the future → refusal, and the seed says why
   (failed vs abandoned — see "the one asymmetry").
3. **Pool match.** `quest_definitions` where `status = 'approved'`,
   `owner_user_id IS NULL`, `giver_npc = <steward>`, `realm_band` covers
   the player's realm, tier ≤ the standing-derived ceiling, and this player
   has no `character_quests` row for it. Pick deterministically (oldest
   unoffered first, or seeded by `user_id` so two players don't see the
   same one on the same day).
4. **Invent.** No pool match, the invention budget has room today, and the
   player's `last_commission_resolved_game_minute` is older than the
   invention interval → run the live forge with a seed. On any failure
   (model, validation, budget) → step 5, never a procedural quest. A
   generic commission is worse than none.
5. **Nothing.** "He has nothing for you today." A fine thing for a Steward
   to say.

## Standing

`npc_relationships` (`app/database/core.py:535`) already holds `trust`,
`respect`, `fear`, `affection`, `debt`, `grudge`. Standing for commission
purposes is derived, not stored:

```
standing = trust + respect - grudge          (clamped, presentation-banded)
```

Outcomes move it through the existing `relationship.update` engine action:

| Outcome | trust | respect | grudge | cooldown | next tier |
|---|---|---|---|---|---|
| completed | + | + | — | none | may rise |
| failed | − | — | + | yes (game time) | drops |
| abandoned | − | — | + | yes, same length | drops, same |

Exact deltas are operator decisions (below). The design only requires that
failed and abandoned are indistinguishable in consequence and
distinguishable in record.

The voice call does not get told to "act cold". It gets the standing band
and the last outcome in the seed, and the SPEECH STYLE it already has. Cold
follows.

## Reward variants (bounded negotiation)

An approved pooled commission may carry two or three reward variants —
standard, harder terms for more, rushed for less — each within the GM
budget and each having passed the validator. Invented commissions get
variants from the budget ladder deterministically. The Steward picks
*among* variants based on the exchange; he never names a number the
validator has not seen. The chosen variant is fixed at accept and stored on
the `character_quests` row, so a later change to the definition cannot
change what a held commission pays.

## Schema — migration 29

`SCHEMA_VERSION` is 28 (`app/database/core.py:38`). This is 29.

`quest_definitions` gains:

| column | type | meaning |
|---|---|---|
| `giver_npc` | TEXT NOT NULL DEFAULT '' | the Steward (or other giver) who offers it; '' = not a commission |
| `realm_band` | TEXT NOT NULL DEFAULT '' | e.g. `qi_refining:1-9`; '' = any |
| `tier` | INTEGER NOT NULL DEFAULT 1 | standing ceiling this needs |
| `owner_user_id` | INTEGER NULL | NULL = pooled; set = invented for that player |
| `deadline_game_minutes` | INTEGER NOT NULL DEFAULT 0 | 0 = no deadline (non-commission quests keep 0) |
| `variants_json` | TEXT NOT NULL DEFAULT '[]' | reward variants; empty = `rewards_json` is the only terms |
| `seed_json` | TEXT NOT NULL DEFAULT '{}' | the canonical seed an invented one was drafted from (GM review, replay) |

`character_quests` (`app/database/core.py:552`) gains:

| column | type | meaning |
|---|---|---|
| `commission` | INTEGER NOT NULL DEFAULT 0 | the one-at-a-time check keys on this, not on the definition |
| `deadline_game_minute` | INTEGER NULL | absolute; set at accept from `accepted_game_minute + deadline_game_minutes` |
| `variant_index` | INTEGER NOT NULL DEFAULT 0 | which terms were accepted |
| `resolved_game_minute` | INTEGER NULL | when it left `active` |

`status` values on `character_quests` become `active`, `completed`,
`failed`, `abandoned`. Today only the first two exist
(`go_core/internal/game/actions.go:336`).

`npc_relationships` gains:

| column | type | meaning |
|---|---|---|
| `commission_cooldown_until_game_minute` | INTEGER NOT NULL DEFAULT 0 | refusal until this world minute |
| `commissions_completed` / `commissions_failed` / `commissions_abandoned` | INTEGER NOT NULL DEFAULT 0 | counters; the seed and the GM view read them |

A visibility rule, not a column: `/quests` and the dashboard Quests tab
list approved definitions where `owner_user_id IS NULL OR owner_user_id =
<viewer>`. Personal commissions are not public content.

Retention: invented definitions that were never accepted are discarded
after N game-days by the engine tick; those that were accepted follow the
`character_quests` row.

## Engine actions

All new gameplay writes go through the Go engine. None of this may add a
row to `PLAYER_MUTATIONS` in
`tests/python/contracts/test_authority_boundary.py`.

| Action | What it does, in one transaction |
|---|---|
| `commission.accept` *(shipped v0.22.0)* | Refuses if the player holds an active commission or is on cooldown; inserts the `character_quests` row with `commission=1`, the deadline, the variant; returns the row. **This replaces `DB.accept_quest`** (`app/database/core.py:3323`, called from `app/ops/core_services.py:250`), which is one of the v0.21 "small ones" still writing from Python. Building on it shrinks the gate; building beside it grows the gate. |
| `commission.resolve` *(shipped v0.22.0)* | Payload `outcome ∈ {completed, failed, abandoned}`. Sets status and `resolved_game_minute`; applies the standing deltas via the same code path as `relationship.update`; sets the cooldown for failed/abandoned; bumps the counter; for completed, grants the chosen variant through `cultivationReward` (`actions.go:58`) — so nothing pays at accept, and a GM can retire a bad invented commission before it completes with zero economic effect. Claws back any upfront component if the commission had one. |
| `commission.expire` (tick) *(shipped v0.22.0 as `ExpireDueCommissions`, called from `advancedMaintenance`)* | Called from the simulation tick: every active commission whose `deadline_game_minute` has passed resolves as `failed`. This is the only path that makes `failed` exist without a GM. It runs in the engine so it survives restarts — the same reasoning the roadmap gives for moderation expiry in v0.24. |

`quest.progress` is unchanged: when it flips a commission to `completed` it
calls the resolve path instead of setting the status directly, so
completion consequences are in one place.

## Producers

### The pool (batch forge)

*Shipped v0.22.0, with one change from the design: the starting pool is
**authored content** (`content/world.json` → `commissions`, seeded insert-only
into `quest_definitions` at startup) rather than a forge run. The forge path
producing commission drafts for the same table is unchanged in principle and
is what the GM tab's Approve button is for.*

`forge_quests_from_history` gains a giver: a small table in content or
config mapping giver NPCs to the locations and event types they commission
for. The system prompt in `quest_forge.system_prompt` gains the giver's
name, role, and the realm band, and asks for `variants` alongside
`rewards`. Everything else is as today: drafts land as `status='draft'`,
the GM approves in `/admin world quests`, and the approval view shows
giver, band, tier and variants.

### Seeded invention (live forge)

*Not built as of v0.22.0 — the pool ships first, by the ordering at the end of
this document. The schema and the visibility rule are in place.*

New: `app/ai/commission_seed.py` (or a function in `quest_forge.py`).
Builds a seed from reads that already exist:

- character: realm, path, sect, location — the same summary
  `Narrator._character_summary` uses
- relationship row with the giver: standing band, counters, last outcome
- `npc_player_memories` / `rag_memories` for this player, salience-ranked,
  **filtered to public visibility** — the same rule the narrator context
  already applies ("Hidden history is never supplied to narrator RAG",
  README)
- open threads the engine knows about: a family feud, a sect trial pending,
  a lost item — whatever the world-history and relationship tables expose
  as public facts
- the giver's public role and location

The seed is rendered as fenced data (`<<<SEED>>> … <<<END SEED>>>`), the
way `quest_forge._ask` already fences the story, and is stored in
`seed_json` for GM review. The player's dialogue text is not in it. The
draft is validated against the **invention budget** (smaller than the GM
budget), retried once with errors, and on any failure the offer is
"nothing today" — never the procedural drafter.

The invention budget is a per-purpose limiter in `ai_router.py` beside
`OpenRouterRequestLimiter`: N invention calls per day reserved out of the
daily allowance, so voice calls are never starved by invention and vice
versa.

## The voice call

`talk_to_npc` gains a `commission_context` block, rendered into the prompt
the same way `scene_context` is today, containing exactly one of:

- **offer**: title, objectives, the proposed variant's terms, and whether
  the Steward may name the alternatives
- **refusal**: reason ∈ {holding one (with progress), on cooldown (with
  outcome), standing too low, nothing available}
- **progress check**: the held commission and its progress

plus the standing band and the last outcome. The model narrates; the UI
attaches Accept / Decline / (variant) buttons based on the block, not on
anything the model wrote. The output-side leak guard stays on.

**The one asymmetry.** A `failed` commission gets a `steward_initiates:
true` flag in the seed — he came looking for you, so he may raise it
unprompted. An `abandoned` one does not — you told him yourself, he has
already said his piece. Small, cheap, and the kind of thing that makes him
feel like he remembers.

## What this does not do

- No procedural fallback for an invented commission. Generic is worse than
  none.
- No model call decides eligibility, tier, variant, or outcome.
- No player text reaches the drafter, in either producer.
- No reward moves at accept.
- No second commission while one is active — there is no override, not even
  for the GM; a GM who wants to clear one retires it, which resolves it as
  abandoned at no cost to the player (an explicit admin outcome flag on
  `commission.resolve`).

## Typed play

*Shipped in v0.21.1 — gate: `tests/python/contracts/test_typed_play_surface.py`,
`tests/python/unit/test_typed_play_router.py`, `tests/python/unit/test_user_budget.py`.
The prefix is configurable (`TYPED_PLAY_PREFIX`, default `$` since v0.25.1). Roots with
parameters and group commands are not in the verb table yet.*

### What happened before v0.21.1

With `AUTO_NARRATE=true`, every message in a hub channel, private scene
thread or RP channel (`app/bot/bot.py:474-560`) gets one canonical shortcut
check (`is_current_location_question`) and then goes to
`Narrator.narrate_action` with no fixed roll. One model call, routine tier,
per line. Two consequences: the line is **mechanically inert** — "I explore
my location" produces a paragraph and no encounter roll, no quest progress,
no discovery, because the model is (rightly) forbidden to grant anything —
and it is the **most expensive path in the bot**, spending the day's
allowance on the least distinguishable output.

### The rule

The narrator listens for a **prefix**, and the prefix means "this is an
action, resolve it" — not "narrator, react".

| Line | What happens | Model calls |
|---|---|---|
| `> I explore the ravine` | The router. Resolves to an engine action or a picker. Narration is whatever that action already produces. | 0 for routine actions (procedural-first); the action's own tier otherwise |
| `Qiao, what is the caravan carrying?` — names an NPC who is present, or @mentions the bot | `talk_to_npc`, as today | 1 (the call worth spending) |
| any other line | Added to history as the player's speech. No reply. The narrator sees it later as RECENT RP CONTEXT. | 0 |
| `> ...` that matches nothing | Ephemeral picker: top candidates plus "Just say it in character". Never a guess. | 0 |

`narrate_action` stays as the last resort for a prefixed line that is
clearly an action but resolves to nothing, and it becomes the exception
rather than the default.

Discoverability: the scene/thread opener's mode line
(`app/bot/ui/event_scene.py:153`) states the prefix; the first un-prefixed
line from a player that *looks* like an action (matches the verb table)
gets one ephemeral hint, once per player per day, and is otherwise treated
as speech.

### The router — three deterministic stages, no model call

Inserted in `on_message` after `DB.add_history` and before any narrator
call.

1. **Verb table.** Aliases → action, in a content file, not code:
   explore / look around / search the area → `/explore`; talk to / ask /
   tell X → talk; meditate / cultivate / sit in seclusion → cultivate;
   sneak / hide → scene action `stealth`; and so on across the sixteen
   hubs and the eight `SCENE_ACTION_KEYS` (`app/rules/quests.py:53`).
   A few dozen aliases per action is enough to start; the picker catches
   the rest and its choices are the data for growing the table.
2. **Entity resolution against what is present.** NPCs at this location
   this period (`current_npc_location`), exits from the road table,
   inventory item names, known techniques, other cultivators in the
   scene (`get_characters_at_location`). "I ask Qiao about the caravan"
   becomes talk → Steward Qiao only if he is here; if he is not, that is a
   canonical refusal in the reply, not a guess and not a model call.
3. **Ambiguity → picker.** Two or more candidates, or none: an ephemeral
   view with the top two or three plus "Just say it in character". The
   chosen candidate dispatches exactly as a slash command would, with the
   same `action_id` idempotency key shape.

A model may join stage 3 later, choosing **among the listed candidates**
as a structured pick — never inventing an action. Not built until the
table demonstrably fails.

### What it dispatches to

The real handlers, unchanged: the same functions the slash commands and
hub buttons call, so engine rolls, quest progress (`quest.progress`), RAG
memory rows and cooldowns all fire as they do today. Typed play adds no
handler and no write. It is an input method.

### Budget and throttle

- Routine actions reached by typing follow the reallocation this design
  assumes: procedural (or pooled) prose first, live narration as the
  upgrade for the epic tier and NPC dialogue.
- **Per-user token bucket on `on_message`** — burst plus sustained rate,
  configurable — before the router runs. `serialized_user_action`
  (`app/bot/runtime.py:193-217`) serialises but never throttles, and
  `on_message` has less; today one player pasting paragraphs can drain the
  shared daily allowance for everyone. This is the v0.23 "per-user command
  budget" item, pulled forward, and it should ship before the router does.
- The input fence (v0.23): every prefixed line and every NPC-addressed line
  is delimited and length-capped the way `chat_monitor.py` already does,
  before it reaches any prompt. Typed play multiplies the free text that
  reaches prompts; the fence is a prerequisite, not a follow-up.

### Interaction with commissions

- `> I ask Qiao for work` resolves to talk → Steward Qiao with the
  commission selection run first; the offer/refusal/progress block rides
  the same voice call. Nothing in commissions is reachable only by typing,
  and nothing only by button.
- `> I give up the commission` resolves to the quest hub's abandon action,
  which still shows its cost and asks for confirmation. Typing never skips
  a danger confirmation.

### What typed play does not do

- No un-prefixed line triggers a model call unless it addresses a present
  NPC or mentions the bot.
- No model decides which action a line is.
- No new gameplay write; every dispatch is an existing handler.
- No parsing outside scene channels, private scene threads and configured
  RP channels — the same set `auto_channel` computes today.

## Content prerequisites

- The verb/alias table, as content (`content/typed_play.json` or a section
  of `world.json`): aliases per action, per scene action key, per hub
  action that takes no id. Start small; the picker's choices grow it.
- Steward Qiao is one of the five NPCs missing `personality` and `secret`
  (`content/world.json`; roadmap v0.25). He is the natural first giver and
  needs both fields before the voice call reads well.
- Any giver's location must have `sense_hints` and non-empty `encounters`
  if commissions send players there. Seven locations lack the first, ten
  the second (`docs/ROADMAP_1_0.md`, v0.25).
- A giver table: NPC → locations, event types, realm bands, tier ceiling.
  Three givers is plenty to start.

## Decisions left to the operator

Marked as decisions, not defaults. The design does not depend on the
values; the feel does.

**What v0.22.0 shipped these as** (engine constants in
`go_core/internal/game/commission_actions.go`, and per-commission content in
`content/world.json`): cooldown after failed/abandoned **2 world-days**;
standing on completed **trust +6, respect +4**, on failed/abandoned **trust
−5, respect −3, grudge +4** (identical, by construction); deadlines per tier
**3 / 5 / 7 world-days**, carried per set of terms so a rushed variant is
genuinely shorter; tier ceiling by standing band **hostile 0, cold 1, neutral
1, warm 2, trusted 3**. The invention rows are still open, because invention
is not built. Changing the first three means editing the constants and the
test that pins them; changing the deadlines is content.

| Decision | The trade |
|---|---|
| Invention calls per day (out of 50) | Small makes invented commissions feel like events; generous makes the Steward a vending machine and the pool is what players mostly see anyway |
| Invention interval per player (game time) | Even with one-at-a-time, a fast completer can chain inventions; this is the second throttle |
| Cooldown length after failed/abandoned (game time) | Long enough to sting, short enough that a player comes back |
| Standing deltas per outcome | Whatever `trust`/`respect`/`grudge` scale the rest of the game already uses |
| Deadline per tier (game time) | Deadlines are what make `failed` real; too generous and nothing ever fails |
| Invention budget (xp / stones / items) | Must be strictly below the GM budget — the GM saw pooled rewards, nobody saw these |
| Never-accepted invented definitions: retain how long | GM review value vs table growth |

## Gate

The milestone ships when these exist and pass:

- `tests/python/contracts/test_authority_boundary.py`: `PLAYER_MUTATIONS`
  has lost the `accept_quest` row and gained none.
- Go tests for `commission.accept` (refuses on held / on cooldown; inserts
  with deadline and variant), `commission.resolve` (each outcome; failed and
  abandoned produce identical standing and cooldown; completed pays the
  chosen variant and nothing else; admin-retire costs nothing), and
  `commission.expire` (past-deadline → failed, on the tick, once). Mutation
  tests on the outcome table.
- A Python test that the seed builder's output contains no player-authored
  text and no non-public memory row, mutation-tested by injecting one of
  each.
- A visibility test: `/quests` and the dashboard never list another
  player's `owner_user_id`.
- A source check, `test_scene_action_surface`-style, that the Accept
  button's payload comes from the `commission_context` block and not from
  narration text.
- Content tests from v0.25 pass for every giver's locations and for every
  giver NPC's narrator fields.
- Typed play: a table-driven router test — a fixture of lines → expected
  (action, target) or picker, including "NPC named but not present" →
  refusal and "un-prefixed action-shaped line" → speech only; a test that
  no un-prefixed, non-addressing line reaches any narrator method
  (mutation-tested by removing the prefix check); the token bucket
  exercised behaviourally (N lines in a window → the N+1th is refused
  without a model call); and a source check that every router dispatch
  target is an existing registered handler, so the router cannot grow a
  handler of its own.

## Where it sits in the roadmap

Commissions are independent of v0.22 Authority II and close one v0.21 row
(`accept_quest`) on the way. Typed play depends on two v0.23 Hardened I
items — the input fence and the per-user budget — which should ship first,
as a point release, whatever else lands. Both want v0.25's content tests
for the givers' locations, and together they are the natural first subject
for the v0.26 playtest: one loop exercises the router, the voice call, the
quest hub, the engine tick and the GM review surface.

Suggested order: throttle + fence → typed play (router, speech-only
default) → commissions (schema 29, engine actions, pool) → seeded
invention last, once the pool and the voice call are seen to read well.
