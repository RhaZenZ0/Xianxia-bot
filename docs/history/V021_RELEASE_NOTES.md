# Xianxia RP Discord Bot — v0.21 release notes

Shipping as **v0.21.6**. The v0.20 line (v0.20.0 through v0.20.9) is in
`docs/history/V020_RELEASE_NOTES.md`. The release is stamped 0.21.6 in `app/version.py`,
`VERSION`, the `Dockerfile` and `docker-compose.yml`, and carries schema 28,
unchanged since v0.20.6.

v0.21 is the roadmap's **Authority I** milestone (`docs/ROADMAP_1_0.md`):
the last handlers where Python decides a gameplay outcome and writes the
tables itself each get an engine action. The milestone ships as point
releases, one row at a time; its gate is the allowlist in
`tests/python/contracts/test_authority_boundary.py` (`PLAYER_MUTATIONS`),
which lists every remaining Python-side gameplay write and must be empty
before v0.21 is tagged stable.

Release dates: 2026-09-06 (v0.21.0 through v0.21.6).

## v0.21.0 — the gate, and `item.use`

**The gate.** `test_authority_boundary.py` now scans `app/database/` for
every method that runs an INSERT/UPDATE/DELETE (64 of them) and every call
to one of those from `app/bot/` and `app/ops/`. Each call site is either a
`PLAYER_MUTATIONS` row - a gameplay write with the engine action that will
replace it - or one of the `BOOKKEEPING_METHODS` (narration history, RAG
memory, Discord channel and thread ids, ops telemetry, the GM's quest-draft
review), which stay in Python. Anything else fails; a row whose write has
gone fails too until the row is deleted. The scan started at 27 rows across
the seven roadmap items and the test refuses to let it grow. Both directions
are mutation-tested.

**`item.use`.** The non-battle branch of `/use` ran five unguarded writes
in Python: consume the item, restore Qi/vitality, add the permanent life
extension, apply the item's effect, add pill toxicity and then re-derive the
toxicity penalty effect. The battle branch beside it had gone through
`combat.recovery_item` since v0.19; the rest never did. The Go engine now
owns the whole sequence as one transaction, `item.use`
(`go_core/internal/game/item_use_actions.go`), and the Discord handler is
"call, then format" - 22 lines where there were 70.

What moved, and what it preserved:

- The "no implemented active use" refusal happens before anything is
  consumed; storage treasures and array talismans keep their own actions.
- A stack's last item deletes the inventory row instead of leaving a
  zero-quantity row behind (Python's `consume_item` left the row; the
  combat path already deleted it).
- The restore clamps to the maxima and, if a battle is active, moves
  `battles.player_hp` in lockstep - so `/use` mid-battle now behaves like
  the battle panel's recovery option instead of taking a different path.
- Effects are keyed by the content's `effect_key` (default: the item id),
  sourced to the item, normalised to the same shape
  `app/rules/effects.py` produces, and `duration_game_minutes: 0` means
  the effect does not expire (a NULL end, as before).
- The medicinal-residue value is derived exactly as
  `app/rules/alchemy.py` did (24 for a lifespan pill, 18 risky, 12
  cultivation, 10 mental, 7 with an instant restore, 8 otherwise;
  `pill_toxicity` in the content overrides), the existing decay is
  settled *before* the add, and the engine's own toxicity curve
  (`settlePillToxicityEffectTx`, in place since v0.19) writes or clears the
  penalty effect afterwards. The reply carries the band label, so Python
  no longer computes it.
- The Go catalog gained the item `use` fields (`effect`, `effect_key`,
  `name`, `duration_game_minutes`, `lifespan_years`) and the optional
  `pill_toxicity` override; the world file did not change.

Seven Go tests cover the five steps, the penalty-band crossing, decay
order, the lifespan pill, the talisman (no expiry, not a pill), the
refusals, and the mid-battle HP bar; nine mutants (delete-at-zero, each
toxicity constant, decay order, the duration-zero rule, the HP lockstep,
the use gate, the stat-less modifier drop, the post-add settle) are all
killed. `PLAYER_MUTATIONS` shrinks from 27 rows to 22.

Still in Python after this release - the remaining v0.21 rows:
`/alchemy purge` (→ `alchemy.purge`), `/sect shadow` (→ `sect.shadow`),
`/law technique`'s self-applied effect, the toxicity sync in
`character_state.py` (→ engine-owned; `/use` no longer calls it), the eight
admin writes in `world_ops.py`, and the small ones (`set_gender`, abode
`set_location`, `discover_sect`, `accept_quest`).

Python full suite 641 tests, identical failure set; pytest-style 25/25 (the
three new gate tests); Go suite green. No schema change.

## v0.21.1 — typed play

Outside the Authority I row-by-row work, but on the same principle: a model
decides nothing. Design and the rest of the plan it belongs to:
`docs/COMMISSIONS_DESIGN.md`.

**What was wrong.** With `AUTO_NARRATE=true`, `on_message` sent every line in
a hub channel, private scene thread or RP channel to `narrate_action` with no
fixed roll. "I explore my location" produced a paragraph and no exploration -
no encounter roll, no quest progress, no discovery, because the narrator is
forbidden to grant anything. And every line was a call against a shared
allowance of 50 a day; the path had no per-user limit at all.

**The rule.** The narrator listens for a prefix, and the prefix means "this
is an action, resolve it":

- `> I explore the ravine` - the router (`app/bot/typed_play_router.py`)
  turns it into the handler a hub button would run and the engine resolves
  it. Roots with no parameters (`explore`, `hunt`, `cultivate`,
  `breakthrough`), the eight scene actions (the whole line is the detail,
  the named NPC or player is the target), and `/talk` (the named NPC).
- An un-prefixed line that addresses a present NPC (name in the first two
  words, or a question naming them) is `/talk`. An @mention is free
  narration - an explicit "narrator, react".
- Anything else is speech: recorded as history, no reply, no call.

**The router** is three deterministic stages and never calls a model. A verb
table in `content/typed_play.json` (whole-word alias phrases; a longer alias
beats a shorter, one at the start of the line beats one in the middle, after
the leading "I / I'll / let me / I try to" is stripped). Entity resolution
against who is present - the same target list the `/action` panel offers -
matching a full name, a name without titles, or one distinctive token, where a
token two present targets share ("Feng") identifies neither. And a picker
(`TypedPlayPicker`, owner-only) when two readings tie or nothing matches: the
candidates, **Narrate it**, and **Just say it in character**. Naming an NPC
who is not here is a refusal, not a guess.

**No handler of its own.** `typed_play.py` reaches handlers only by registry:
`ACTIONS.root()` / `handler_for()` for roots and `/talk`, and a new
`EVENT_HANDLERS` binding `scene_action_resolve` (bound in `commands/scene.py`
beside the function the `/action` modal submits to) for scene actions. A
`MessageInteraction` adapter gives those handlers the interaction surface
they expect (`response.defer/send_message`, `followup.send`, `user`, `id`,
`edit_original_response`), replying to the typed line - the same idea as
`HubInteractionProxy`. No engine call, no database write, nothing for the
authority gate to allowlist; `PLAYER_MUTATIONS` is unchanged at 22.

**The budget.** `app/ops/user_budget.py` is a per-user token bucket
(`TYPED_PLAY_BURST` at once, refilling at `TYPED_PLAY_PER_MINUTE`; idle users
evicted), instantiated in `runtime.py` as `TYPED_PLAY_BUDGET`. Every path out
of `on_message` that can reach the engine or the narrator spends a token
first; the picker's action and narrate buttons spend one on click; speech
and "Just say it" are free. A refused line is answered with the wait and
never queued. This is the v0.23 Hardened I "per-user command budget" item,
pulled forward.

**Settings.** `TYPED_PLAY_PREFIX` (default `>`; exactly one character, never
a letter, digit or space - `_typed_play_prefix` in `config.py` refuses
anything that would swallow speech), `TYPED_PLAY_BURST`, `TYPED_PLAY_PER_MINUTE`,
`TYPED_PLAY_HINT` (once a day, when a player's un-prefixed line looked like an
action, a self-deleting reply says how to make it one).

**Smaller changes on the way.** Someone without a cultivator who merely
speaks in a hub channel is no longer told to `/begin` on every line - only an
attempt to act, or a mention, earns that. The same for the "this channel
represents X, you are at Y" nudge. `narrate_action` moved from `on_message`
into `XianxiaBot.narrate_freeform`, reachable only by mention or by the
picker. A line that dispatches to a handler is not also recorded as speech,
so the narrator's context never sees the same line twice.

**Tests.** `tests/python/unit/test_typed_play_router.py` - a table of lines
and what each becomes, the verb table validated against the registered roots
(every root it names exists and takes no required parameter) and the scene
action keys, no alias claimed twice. `tests/python/unit/test_user_budget.py` -
the bucket, behaviourally, with a fake clock. `tests/python/contracts/
test_typed_play_surface.py` - by reading the source: the un-prefixed branch
of `on_message` reaches narrator or dispatch only behind `if mentioned` and
`if npc is not None`, both budgeted; the prefixed branch spends before it
routes; `typed_play.py` dispatches only through the registries and contains
no engine call or write; both picker buttons that can act spend first;
the prefix setting rejects letters, digits and spaces.
`test_bot_package.py` gains the two modules in its tier table and the new
registry binding. Eight mutants (speech narrates; mention door unbudgeted;
budget always grants; ambiguity guessed; absent NPC not refused; picker acts
before spending; prefix accepts a letter; any mention counts as addressing)
are all killed.

**Known limits, deliberately.** Root commands with parameters (`/travel`,
`/use`, the group commands) are not in the verb table yet - a typed line
carries no arguments, and the picker is the right place to grow that.
Ephemeral replies cannot be sent from a message, so the dispatched handlers'
"ephemeral" tidiness replies land publicly. The hint state is in memory and
resets on restart. No schema change.

## v0.21.2 — Teardown, and the realm capitals actually hidden

**Teardown.** The dashboard's Discord tab had Fresh Start (delete and
recreate the message-safe channels) and Reset World (delete tracked threads
and announce), but no way to take the whole layout down. `teardown`
(`dashboard_discord_control`, `app/bot/admin/server_setup.py`) is the
inverse of Full Setup. In order: every thread the database tracks (the same
set Reset World deletes); every *bound* channel - the seven base channels
including `#player-homes`, `#expeditions` and `#event-scenes`, every
realm-capital hub, the `#bugs` forum; the two Xianxia categories, only if
nothing else is left inside them; then the ids the bot held, through a new
bookkeeping write `Database.clear_discord_bindings` (server_config channel
and message ids, `realm_hub_channels` rows, the posted `message_id` on
channel messages - GM-authored *text* is kept so a later Setup reposts the
GM's words). What a binding names is what is deleted: not by channel name,
not by category membership, and never `RP_CHANNEL_IDS`, the realm roles, or
anything the GM made that Setup did not bind. Nothing is recreated; the
database is not reset - characters, sects and history survive and every
thread owner recovers from a missing thread on next use, as after Reset
World. Confirmation is the typed word `DELETE` (Fresh Start is `CLEAR`,
Reset World is `RESET`; three different words on purpose), the action is
audited, and the dashboard button refuses until the word is in its box.

**Realm capitals were not hidden.** Setup created the `Xianxia • <world>`
roles and `require_character` assigned them as a cultivator's realm
unlocked - but no hub channel ever received a permission overwrite, so the
"visibility gate" gated nothing and every capital was visible to everyone.
`ensure_realm_hub_channels` now gates each hub it resolves on the
dashboard-owned Setup/Repair path (`ensure_realm_hub_overwrites`,
`app/bot/channels.py`): `@everyone` denied View Channel, the world's role
allowed it, and an explicit allow for the bot itself. It runs for existing
hubs too, not only on creation, because Fresh Start recreates hubs bare;
it makes no API call when a hub is already gated; and it never denies
`@everyone` when the role does not exist (that would be a lockout, not a
gate - the roles need Manage Roles, which the permission diagnostics
already flag). `realm_hub_visibility` (`app/rules/realm_hubs.py`, pure)
says whether a hub is actually gated; the dashboard's Realm Capitals table
gains a Visibility column, and a visible hub is no longer counted "ready",
so Setup State reads ATTENTION until it is fixed. The `/admin` slash path
stays validate-only.

**Tests.** `tests/python/contracts/test_discord_teardown.py`: the typed
word, the audit, delete order (threads, channels, categories, then ids),
only-what-a-binding-names, categories only when empty, nothing recreated,
permission checked first, the JS button refusing without the word, and a
real-database test of `clear_discord_bindings` (forgets ids, keeps GM
text, leaves other guilds alone, idempotent).
`tests/python/contracts/test_realm_hub_visibility.py`: the hidden rule on
every overwrite combination, the gate applied for existing hubs and only on
the provisioning path, deny+allow+bot, idempotence, and the dashboard
column. `clear_discord_bindings` joins `BOOKKEEPING_METHODS` in the
authority gate; `PLAYER_MUTATIONS` is unchanged. Nine mutants (gate only on
creation; hidden = deny only; deny with no role; confirm word CLEAR;
bindings cleared before deletes; non-empty categories deleted; GM text
wiped; every guild cleared; button fires without the word) are all killed.

With `httpx` available the Python suite now collects in full: 738 passed,
no failures. No schema change.

## v0.21.3 — one manual on joining a sect, and a catalog both sides can read

**How does a player get a manual?** Mostly, they could not. A manual is an
item (`<manual_id>_manual`) that must be in the inventory before
`/cultivation → Manuals & Techniques → Study` learns it through the engine's
`manual.study`. The only sources were `/sect shadow` (one demonic manual on
hidden-sect initiation) and the black market's forbidden stock - the six
seed manuals. The other 142 - every righteous "Jade Manual" inheritance -
were generated by `augment_advanced_catalog` in Python's memory when `World`
loaded the file. The Go engine reads `content/world.json` raw, so it had
never heard of them: it could not stock them, `manual.study` refused them as
"unknown cultivation manual", and they appeared only in autocompletes.

**The catalog on disk is the catalog.** `scripts/materialize_world_catalog.py`
runs the same deterministic, idempotent expansion once and writes it back:
148 manuals, 528 techniques, 187 items, plus the `world_rules` block and the
hidden Heaven-Devouring Demon Sect the expansion also adds. `World` still
calls the expansion at load; on a materialised file it is a no-op, and
`tests/python/unit/test_world_catalog_materialised.py` fails the moment the
file drifts from it (`--check` does the same from the shell). Two things had
to change for that to be safe: the generated manual items are now
`market_excluded` (an inheritance is given or found, never listed at
`base_price` in every town market - which is what the file on disk would
have done to the Go bootstrap), and the simulation's `Sect` gained `hidden`,
which bootstrap honours so the hidden lineage gets no public politics row,
no relations and no NPC faction (`TestBootstrapSkipsHiddenSectsAndKeepsManualsOffTheMarket`).
`rewardable_items` in Quest Forge already excludes market-excluded items, so
forged quests still cannot reward a manual - a deliberate follow-up, not an
accident (commissions, `docs/COMMISSIONS_DESIGN.md`, is where that decision
belongs).

**One manual on joining.** Passing a sect's entrance trial (`pass` or
`conditional_pass`) bestows the sect's entry inheritance, chosen and written
by the engine inside the same transaction as the membership
(`sectEntryManual`, `go_core/internal/game/sect_actions.go`) - so a new
Outer Disciple never exists without it and a rolled-back trial never grants
one. The rules, all on canon: alignment follows the sect (Orthodox →
Orthodox; Neutral → Neutral, else Orthodox; Demonic → Demonic - a righteous
sect never hands out a forbidden art); the character's own path first, any
path of the right alignment only if theirs has nothing; the lowest tier
within reach, or - for every fresh Mortal-realm disciple, since the catalog
has no Orthodox manual at tier 0 - the lowest tier there is, to grow into
(`manual.study` still enforces `min_realm_index` at study time); never one
already learned or carried; ties on the manual id so the choice never
depends on map order. The item lands in the inventory with an
`item_provenance` row (`sect_entry`, clean or forbidden by the manual's
alignment), and the result carries `granted_manual` so the Discord reply
can say what was given and when it can be studied. Python grants nothing:
`PLAYER_MUTATIONS` is unchanged, and `/sect shadow`'s older Python-side
grant stays on the gate as the `sect.shadow` row it already was.

**Tests.** Go: `TestSectTrialPassGrantsTheEntryManual` (pass → inventory,
provenance, membership, and the gift is then studyable through the same
catalog), `TestSectEntryManualSelectionRules` (alignment per sect, path
first, reach beats out-of-reach, lowest tier, no duplicates, fallback path,
deterministic across twenty runs), `TestSectTrialFailGrantsNothing`; seven
mutants killed (orthodox may give demonic; grant on fail; duplicates; path
ignored; highest tier; no inventory write; reach ignored). Python:
`test_world_catalog_materialised.py` and `test_sect_entry_manual.py` (the
handler reads the gift and makes none; the grant sits between membership
and result in the engine; the selector's alignment table). Go suite green;
Python 748 passed. No schema change.

## v0.21.4 — every sect has a genuine tier-0 entry manual

v0.21.3 exposed a content gap: the generated catalog has no Orthodox manual
at tier 0, so a fresh Mortal-realm disciple - exactly who joins a sect -
was handed a tier-2 manual to grow into. The fix is content, not a formula
tweak: six **authored** entry inheritances in `content/world.json`, one per
public sect, each marked with its `sect`:

| Sect | Entry manual | Alignment |
|---|---|---|
| Azure Cloud Sect | Azure Cloud Foundation Sword Canon | Orthodox |
| Crimson Furnace Sect | Crimson Furnace Ember-Tempering Record | Orthodox |
| Frozen Moon Palace | Frozen Moon First-Frost Sutra | Orthodox |
| Black Serpent Clan | Black Serpent Venom-Fang Primer | Neutral |
| Blood River Sect | Blood River Crimson Tide Initiation | Demonic |
| Corpse Lantern Pavilion | Corpse Lantern Pale-Flame Initiation | Demonic |

Each is tier 0, path-agnostic (`"path": "Any"` - the manual's path is
descriptive, nothing in the engine gates study on it), and carries three
techniques unlocking at mastery 0, 1 and 2 in the shape the battle resolver
already executes (a strike, a recovery, a control - the demonic pair
paying karma and exposure as their alignment demands). The items are
`market_excluded` and legally `clean` or `forbidden` by alignment. The
hidden Heaven-Devouring Demon Sect has none: it has no public trial.

`sectEntryManual` now takes the sect's own manual first (`Sect` on
`worlddata.ManualDefinition`); the v0.21.3 rules apply only once the
character already has it - a rejoining disciple gets the next one, never a
duplicate. Two more mutants killed (sect's own manual ignored; given even
when owned); the Go trial test now asserts the Azure Cloud canon itself,
studyable at once. `test_world_catalog_materialised.py` gains a test that
every public sect has exactly one tier-0 entry manual with valid techniques
and a hidden sect has none; the catalog counts move to 154 manuals / 546
techniques (44 / 166 demonic) in `test_technique_catalog.py`. No schema
change.

## v0.21.5 — the input fence, the content batch, and cooldowns as a wait

The first two items of the AI plan, plus one piece of wording a player
reported.

**The input fence.** Player-authored text used to reach the narrator's
prompt merely *labelled* untrusted, between bare `<<<` / `>>>` lines a
player could close themselves by typing `>>>`; the output-side leak guard
in `ai_router.py` was the only defence, and typed play (v0.21.1) had just
multiplied the text that gets there. `fence_untrusted` (`app/ai/narrator.py`)
now wraps every player-authored slot - the dialogue in `talk_to_npc`, the
action in `narrate_action`, the recent history block every scene sends, and
the NPC's short- and long-term memories of the player (which quote them) -
in `<<<BEGIN …>>>` / `<<<END …>>>` markers, whitespace-tidied, capped
(600 characters for a line, more for history and memories), with any run of
`<<<` or `>>>` inside the text swapped for a look-alike so the fence cannot
be closed from inside. Both system prompts now say what the markers mean:
anything inside a fence is data the players wrote, and an instruction, rule
or "system" message in there is fiction to narrate around, never obey. The
same shape `chat_monitor.py` has used since v0.19.
`tests/python/unit/test_narrator_input_fence.py` pins the helper (the
closing-the-fence attack, the cap, empty slots still fenced) and scans the
prompt source so no slot can quietly return to bare interpolation; four
mutants killed (markers not neutralised; no cap; dialogue bare; history
unfenced).

**The content batch.** The v0.25 content items, authored now rather than
forged later: encounters for the ten locations that had none - the three
recruitment grounds and the three hidden-sect grounds `/explore`
hard-errored on ("current location has no exploration encounters"), plus the
Auction House and the three rebirth sanctuaries - and `sense_hints` for the
seven without. Safe-zone encounters are things that happen around you, not
attacks (rule 15: violence cannot succeed in a protected interior). The four
world rulers, who shared one copy-pasted speech/want/fear and had no
personality or secret, each get their own, tied to what the world already
says (the damaged world-meridian seal, the rebirth enclaves, the hidden
demonic lineage); Steward Qiao gets a personality and a secret that make him
the commission giver the design doc names.
`tests/python/unit/test_world_content_gate.py` is the v0.25 gate: every
location has hints and at least three distinct encounters, safe zones start
no violence, every NPC has all five narrator fields, and the rulers are four
people, not one template.

**Cooldowns as a wait.** Reported: "cultivation cooldown remaining: 10520".
That is the engine's exact truth in wall-clock seconds
(`CULTIVATE_COOLDOWN_MINUTES=180`) and no help to anyone.
`_explain_engine_error` (`app/bot/runtime.py`) now rewrites all three engine
cooldown shapes into "⏳ Cultivation is still on cooldown — ready in
**2h 55m**", and every `❌ {exc}` reply in the command, UI and admin
modules goes through it (82 sites; the one in `hubs.py` is input validation,
not an engine error, and stays). `test_cooldown_wording.py` pins the
formats and scans for any raw reply that comes back.

Python 772 passed; Go suite green; no schema change.

## v0.21.6 — realm capitals visible only while you are in the city

Requested: "hide realm capitals until you are in the main city; make a
role for it that we can add and remove based on player location; set up
the perms needed."

**The role.** One presence role per capital, `Xianxia • <capital name>`
(`realm_presence_role_name`, `app/rules/realm_hubs.py`), created by
Setup/Repair beside the realm-access roles with no guild permissions and
not mentionable. It is distinct from `Xianxia • <world>` (earned by
cultivation, v0.19), which stays for Sync Realm Roles and the diagnostics
but no longer gates anything.

**The sync.** `_sync_realm_presence_roles` (`app/bot/runtime.py`) gives a
member exactly the presence role of the capital their character stands in -
`presence_world_for` is an exact match on the hub's location, so a
residence inside the city, a road, or a name that merely shares a prefix is
not the capital - and removes any other. It runs from `require_character`
(every command), from `on_message` (every line in a scene channel, because
a road journey settles in the engine and the next thing a player does may
be to type), and immediately after `/travel → Realm Capitals → Go`, which
is instant. It makes no Discord call when nothing changed, and the bulk
Sync Realm Roles action reconciles presence for every character too.

**The permissions.** `ensure_realm_hub_overwrites` (`app/bot/channels.py`)
now gates each capital on the presence role: `@everyone` denied View
Channel; the presence role granted `REALM_HUB_MEMBER_PERMISSIONS` - view,
send, send in threads, read history, add reactions, embed links, attach
files, use application commands; the bot allowed view/send/manage
messages/history for itself; and the v0.21.2 realm-access role's allow
removed (`stale_roles`), so an unlocked-but-absent cultivator no longer
sees the room. Idempotent - an already-correct hub makes no API call - and
still never denies `@everyone` when the presence role is missing. Both
role sets are checked against the bot's role hierarchy in the permission
diagnostics; the bot needs Manage Roles and Manage Channels as before.
**Run Repair once after upgrading**, then Sync Realm Roles, to apply the
gate and put the presence role on everyone already standing in a capital.

**The dashboard.** The Realm Capitals table's role column is now the
presence role and Visibility reads "in-city only" or "VISIBLE TO ALL"; a
visible hub is still not "ready".

**Tests.** `test_realm_hub_visibility.py` grows presence rules (every
capital maps to its world; nothing else does, prefixes and longer names
included), the role names, the member permission set, the gate applying
the presence role and stripping the old allow, the sync being called from
all four places, and the sync's add/remove shape. Three mutants killed
(presence never removed; prefix match; old allow kept). Python 778 passed;
Go unchanged. No schema change.
