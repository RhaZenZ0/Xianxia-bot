# TODO — what is open

What is still open, each entry **deferred** with the reason it waits and, where it is one, the
decision that would close it. Nothing is left here without that word, and
`tests/python/contracts/test_playtest_gate.py` holds the file to it. An entry that gets fixed moves
to `docs/history/TODO_FIXED.md`, which keeps every closed item as the record of what it turned into
(the release write-ups in `docs/FINDINGS.md` cite those by name); a fixed entry is never deleted,
only moved.

Newest first. Add to it as findings come in — from the live pass (`docs/playtest/`), from a review,
or from a sweep of the tree. When closing an entry, rewrite it as `**fixed (v<version>)**` at the
top of the history file rather than editing it here.

It began as the v0.34 playtest's findings (`docs/history/ROADMAP_1_0.md`, Gameplay-complete II),
driven by `scripts/playtest_engine.py --launch` against a scratch engine. It was
`docs/KNOWN_LIMITATIONS.md` until v1.0.1, and carried its fixed entries beside the open ones until
v1.34.0, when 135 of them outnumbered the 35 still open four to one.

## Open

- **deferred (design)** — *Upper-world keepers have thin boards.* 790 of the 1,154 keeper buy lines pay
  nothing now: a Mortal-made good is worth under a whole coin up there once the ladder is read, so the
  cheap half of an upper economy has no NPC buyer. That is the ceiling doing its job, and it is also a
  content gap: a pass authoring upper-world demand for upper-world goods, or shelves priced on the
  ladder, is the owner's call.
- **deferred (design)** — *Pickers that offer what the economy now refuses.* `/caravan dispatch`'s cargo
  picker is the whole bag, and `/shop sell`'s picker reads the bag rather than the board the engine
  trims, so a thing the counter will not pay for is still offered and then refused. Both want an engine
  question ("what will this pay here").
- **deferred (design)** — *Black-market and fence prices are not read against the ladder.*
  `black_market.trade` sells and fences at its own prices in the post's coin; the arbitrage gate does
  not walk it.
- **deferred (design)** — *Other columns the schema clears on a character's leaving are still
  deleted.* `territory_war_actions.user_id`, `sect_manor_projects.user_id` and
  `deployed_location_arrays.owner_user_id` say `ON DELETE SET NULL` and the sweep still deletes their
  rows. Each is a shared record whose row outlives the person; what a reset does with each is a design
  call per table, and a gate ("every SET NULL column is anonymised or named here") waits on it.
- **deferred (privacy, low)** — *An erased owner's Discord id stays in `caravans.owner_key`.* The row is
  marked lost but keeps the TEXT key, which no subject column covers; anonymising it means a sentinel
  the settlement and the dashboard's caravan list already treat as "no owner".
- **deferred (privacy, low)** — *`docs/PRIVACY.md` does not name the player-keyed `world_state` rows
  the erasure now deletes.*
- **deferred (design)** — *Permanent qi that a settle already took.* Every `qi_max_bonus` starts at 0:
  the qi a settle rebuilt over before v1.34.0 is in no column and cannot be told from the body's own
  figure. The honest repair is a GM `set_resource_caps` per player, or a one-off replay of completed
  `realm_perfection` / `body_realm_perfection` and `inheritances` rows into a bonus, which is an
  estimate because the percentage compounded on the then-current pool.
- **deferred (design)** — *A session on a capped Perfection path gains nothing.* The reply says so and
  points at the path; refusing the session or turning a full stage into a different action is a pacing
  call.
- **deferred (design)** — *An old audit row's undo is still the absolute snapshot.* Grant, adjust-item
  and teleport rows written before v1.34.0 carry no `moved` / `scene` and are undone as they always
  were; they age out of the audit log's useful window, and a migration that rewrites them is not worth
  a schema change.
- **deferred (design)** — *Saving a shipped quest whose reward is above the GM budget is refused.* 35
  shipped commissions and realm-road quests pay more than the default authoring budget, so they cannot
  be opened and saved without lowering the reward first; the refusal names the budget. Whether an
  unchanged reward should pass is a decision.
- **deferred (design)** — *The update card has no "nothing to update" outcome.* An up-to-date
  `--upgrade` is reported as `failed` with "nothing was changed: already the newest", because the
  engine's terminal statuses are `done` and `failed` and the card prints `done` as "updated to vX". A
  third status needs the engine's `updateStatuses`, the card and the watcher together.
- **deferred (design)** — *The other capped walks over the roster.* `npcCrimes` (cap 5),
  `npcBeastHunts` (cap 5) and `npcChildbirth` (cap 6) read their people in name order to a cap; the
  first two rarely bind and childbirth consumes the state it walks. If a content change makes one bind,
  call `rosterTurn` on its rows.
- **deferred (design)** — *Households for people the catalogue adds later.* Seeding runs once per
  world, so an NPC a content update adds afterwards is never offered a seeded marriage; the courtship
  tick is how they marry. A per-NPC marker would be the shape if that matters.
- **deferred (design)** — *A scene sweep for events other than the tick's.* The sweep covers the two
  key shapes the tick mints (`auto:`, `secret_realm_rotation:`); a player's or a GM's event gets its
  scene from the command that made it, and telling "no thread yet" from "never had one" for those keys
  needs a marker in the payload.
- **deferred (design)** — *The batch-5 fixture still lacks production's foreign keys.*
  `setupBatch5AuthorityDB` (and `setupShopDB` on top of it) declares `equipment_instances`,
  `character_item_appraisals`, `spirit_beasts`, `wild_beast_encounters`, `event_log`, `event_claims`
  and the exploration-event tables without their `REFERENCES`; extend `fixtureKeyedTables` and that
  DDL the same way and seed what the tests then miss.
- **deferred (design)** — *A raid with an absent raider settles only on the next press.* The absence
  gate is asked after every raid action and at a kick; no clock rings it, so a raid whose every
  present raider has acted stays at "to act" until somebody presses. A raid ticker in the simulation
  would settle it with nobody present.
- **deferred (design)** — *The raid card does not mark an absent raider.* It still reads "to act" for
  a raider the round will skip; an "away" tag wants the card to read the absence from the engine
  rather than restating it.
- **deferred (design)** — *Event manifestations have no per-day reward cap.* A `combat.finalize`
  against an `event:` battle has no `target_key`, so insight and tempering are not limited per day;
  the site's finite beasts bound it.
- **deferred (design)** — *A GM's set_sect leaves the NPC master bond.* `admin.player.set_sect`
  (remove or change) leaves `sect_lineage` and `npc_mentorships` in place, and `livingNPCMasterTx`
  checks only that the master is alive, not that they are in the disciple's sect, so a player a GM
  moves out of a sect keeps the master's terms until they press Leave. Either the lever ends the
  bonds, as `sect.ascend` does since v1.33.0, or the readers require membership; the owner has not
  chosen.
- **deferred (design)** — *A table-level samsara gate.* v1.33.0's gate holds every column of
  `characters` to being reset or kept by a rebirth. The rows of other tables have no such gate: about
  fifty tables carry a `user_id` and are in neither wipe list (`soul_legacy`, `reincarnation_state` and
  `event_log` are meant to survive; `character_qi_body`, `alchemy_state` and `character_fate` are
  per-body and are neither wiped nor rewritten). `npc_mentorships` is keyed `disciple_user_id`, so a
  gate that looked for `user_id` would have missed it; the key columns need naming first.
- **deferred (design)** — *Which strength floors should read the higher ladder.* v1.33.0 moved the
  floors of a *place* (the sect ascent's gate, the transit arrays, the caravan's road, and the panels
  that anticipate them) onto `accessRealmIndex`, the higher of the qi and body ladders. The floors of
  a cultivator's *strength* stay on the qi ladder: a secret realm's entry and its rooms, the realm draw
  and the unexpected-event bands, flames, Laws and their techniques, manuals, a raid member's realm,
  the residence facility floor, the personal world, and the curriculum and commission bands. A body
  cultivator ahead of their qi stage can be walked into a world they cannot yet fight in, which is
  the point of the split; whether any of these should follow the higher ladder is a decision for each.
- **deferred (design)** — *Which world a cultivator's strength puts them in is read off the qi ladder.*
  `realmWorldGo(realm_index)` chooses the hidden sect's branch, `currentWorld` falls back to the qi
  realm for a character at a place the catalogue does not carry, and `/sense` reads
  `WORLD.realm_world(realm_index)`; a body cultivator in the Spiritual World at qi 3 reads as Mortal
  there. Left because each is "which world am I from" rather than "may I enter".
- **deferred (design)** — *A sweep of every payload field an engine action reads.* The samsara
  wait was rc.56's class found one action at a time (v1.35.0 closed it); a gate that walks every
  payload struct the engine unmarshals and holds each field against a list of what a caller may
  state would find the next one before a release does. Not built yet: the list is the decision.
- **deferred (design)** — *`admin.player.force_end_scene` writes more than its undo restores.* The
  forward action writes `scene_key`, `channel_id` and `metadata_json` as well as the type and label,
  and snapshots only the two its undo puts back, so an undone force-end leaves a scene with its old
  type and label and none of its key, channel or metadata - the opposite shape to the undo fault
  v1.33.0 closed, and putting it right widens what the audit row records.
- **deferred (design)** — *A realm an old undo reopened stays open.* Undos run under v1.23.1 to
  v1.32.0 could put `active=1` back on a `completed=1` perfection row, which the quest and trial
  actions read as an open path. No migration writes it back, because nothing records whether a GM
  meant a realm to be reopened; `SELECT user_id, realm_index FROM realm_perfection WHERE active=1
  AND completed=1` (and the same on `body_realm_perfection`) finds any, and the Player Editor sets
  one right.
- **deferred (design)** — *Python's map skips a city part's own roads; the engine's does not.*
  `_known_locations` walks only the city's `roads`, while `knownLocationsTx` adds the road neighbours
  of both the part and the city. On the shipped content the two agree (none of the 375 city parts (484 locations in all) carries
  `roads`), but a district authored with its own roads would put a neighbour on the engine's map and
  not the picker's. A content gate that no part carries `roads`, or the twin walking both, is the
  fix, and which is the owner's call.
- **deferred (design)** — *A Discord GM quest-report lever.* The Player Editor is the only door to
  `admin.player.quest_progress`; there is no `/admin player` counterpart, so a GM away from the
  dashboard cannot unstick a quest. Decided no for v1.33.0: the lever stays a replay of a player's
  report and the dashboard is where a GM works one character's quests. If a Discord door is wanted it
  should be the same picker over `quest_journal`'s objectives, not a typed type-and-target pair.
- **deferred (design)** — *The report lever matches like a player's report.* `progressQuest`
  accepts any event of its type for an objective with no target, so a GM reporting `talk -> Elder
  Pine` moves an untargeted `talk` objective of the same quest as well. No shipped quest has such a
  pair (`TestEveryShippedObjectiveIsMetByItsOwnTarget` would name it), and an engine `objective_id`
  path would be a second matching rule. Revisit only if a quest is authored with both.
- **deferred (design)** — *A rival that held the gate when migration 74 ran was given the seat city.*
  The migration's first statement copies the gate's banner onto a neutral seat whoever held the gate,
  so a sect whose gate had been taken in a v1.12-v1.18 war lost its home city to the rival. The repair
  cannot tell that from a city honestly held, and migration 74 is frozen. Only worlds that ran NPC wars
  across those releases are affected, and it cannot be found from here; an audited
  `admin.territory.set_controller` lever (a new operation, with playtest coverage and a dashboard
  card) is how a GM would hand the city back, and is not built until a world shows the state.
- **deferred (harness)** — *The playtests claim from a whole place only.* `playtest_engine.py` claims
  the hills and `playtest_discord.py` presses `/territory claim` wherever the sweep's player stands;
  neither claims from a gate or a street, so the redirect is held by the Go tests and the Python
  commands test and not by a real server. A leg that stands at a city's gate, claims, and holds that
  the city's row (not the gate's) carries the banner would drive it end to end.
- **deferred (design)** — *A repaired street forgets what it paid.* Releasing a held part ends the
  tribute it paid (a lot a week, v1.29.0) and folds a sole holder into the whole city; a sect that
  held several streets of a city loses the extra lots. The city pays its own tribute once folded,
  which is the rule; whether a fold should carry the sum is a balance decision nobody has asked for.
- **deferred (design)** — *`world_cross` and `ascension_gate` objectives are untargeted and fire on
  any world change.* The leak the examinations had: an untargeted objective takes every event of
  its type, so a descent through an array reports `world_cross` and completes a quest that asks to
  cross *up*; `abode.py` and `cultivation.py` report them without a target. The right target (the
  world crossed into, or the gate's key) is a content decision.
- **deferred (design)** — *The dashboard quest editor suggests scene actions for a target it has no
  picker for.* `loadQuestEditor` binds a `<datalist>` for `location` and `npc` targets and falls
  back to the scene-action list for every other kind (`realm`, `flame`, `boss`, `secret_realm`,
  `law`, `law_technique`, now `profession_exam`), which suggests wrong values rather than none. The
  validator accepts the right ones, so nothing breaks; a picker per kind off `vocabulary` is the fix.
- **deferred (design)** — *A watcher restarted after a SIGKILL mid-install reports it failed while
  `update.sh` is still working.* `interrupted()` and `reopen_world 1` in `update_watch.sh` do not
  consult `update.sh`'s own lock, so a watcher started again after the first was SIGKILLed (no EXIT
  trap runs) closes the request `failed` and reopens the world over an install still running. The
  v1.33.0 refusal tells a GM to restart the watcher only once `update.sh` has finished, which holds
  the advice honest and not the script; the fix is for `interrupted()` to wait on, or report only
  after, the updater's lock.
- **deferred (rare)** — *A truly dead install holds the Request button for up to two hours.* The
  lease is the exit for a watcher that died mid-install (it was fifteen minutes). Shorter would free
  the button sooner and let a GM cancel an install that is merely slow on a cold NAS build; the
  length is `updateInstallLeaseSeconds` (and the card's twin) if the owner wants it moved.
- **deferred (design)** — *A picker for `/boss claim`.* It is the one leaf the Realm Road sends a
  player to before any raid exists, and it still asks for a typed encounter id; taking it out of
  `STILL_TYPED` wants a provider that lists the player's open encounters, and the Next label could
  then say where the encounter comes from.
- **deferred (design)** — *The classic journal's door edit.* `open_hub_in_place` edits the classic
  (non-layout) journal message without `content=None, embed=None`; if Discord refuses that edit the
  door opens a new panel and never presses the leaf. Not verified live.
- **deferred (design)** — *The journal has no owner check.* `QuestDashboardView` has no
  `interaction_check` and `MenuNextButton` checks no owner, so another member can press a public
  journal's door and turn it into their own hub. Needs a decision on whether the journal is public.
- **deferred (design)** — *One engine query for the panel's gate state.* After v1.31.2 a refresh is one
  session, about sixteen SELECTs and seven engine actions in production. A single `panel.gates` query in
  Go, or the sixteen SELECTs through `/v1/db/batch`, would make it about ten round trips; not built,
  because each `get_x` is its own repository method and a batched read changes that shape for a saving
  that no longer dominates a press.
- **deferred (design)** — *A focus's end moves if the world's rate changes mid-focus.* `active_effects`
  keeps only a game-minute deadline, so the four real hours are converted at the scale the focus was
  taken at; a GM changing the rate during one stretches or shortens it, and at a stopped clock it lasts
  until the clock moves. A real-time column would fix it for this one effect at a schema change; it is
  left until a second effect wants a real-time end.
- **deferred (design)** — *The rest of the menu layout.* Two steps of the same research wait on the
  owner, and on the dashboard's new Most-used commands card for what players actually press:
  folding the seven one-action pages (Inventory, Use Item and Provenance into one Bag
  page, Wallet into the Economy header, General Crafting into Alchemy); and sixteen hubs to twelve
  (Ascension into Cultivation, NPCs into World, Combat and Secret Realms into Adventure, Abode and
  Inner World into Home). The last renames printed hub paths - about 38 `/ascend`, 19 `/realm`,
  5 `/npc` and 5 `/innerworld` in content and code - so it needs a migration of the quest terms
  players already hold, the way migration 61 did, and a re-authored curriculum roster.
- **deferred (design)** — *`world_rules.faction_attitudes` is read by nothing.* Per-alignment
  numbers (Orthodox -35 for a forbidden art, +15 for mercy; Demonic +20 and -5) that look like
  reputation deltas and move nothing. `forbiddenPolicy` reads `forbidden_arts`, and mercy pays fate.
  Either a rule reads them (a sect's standing moved by a deed, by the deed-doer's alignment) or they
  go; named in `UNREAD_RULES` until decided. Found by the v1.19.3 gate. So is
  `forbidden_arts.concealed_use_reduces_exposure`, a boolean stating a rule the engine already
  hardcodes (an unwitnessed use is a third as severe) - the gate looks at top-level keys only.
- **deferred (design)** — *Grandfathering a beast lowers its rank and leaves its evolution stage.*
  `beast_grandfather.go` settles an old beast to the rank its owner's cores can pay for and keeps
  `evolution_stage` where it was, so a stage can stand above the rank and the stage's combat term
  is still paid in full. Whether a stage should be taken back from a beast that already climbed it
  is the owner's call; the shipped test asserts rank 0 with stage 16 left in place.
- **deferred (rare)** — *A stale watcher lock after a power loss.* `update_watch.sh` refuses to start when
  the lock's pid answers `kill -0`, and a rebooted NAS can hand that pid to another process. Rare,
  and the remedy is `rm -rf` on the lock directory; a lock that also records a boot id would tell
  the two apart.
- **deferred (privacy, low)** — *An erased GM's id in the update rows.* `world_state['update_request']` and
  `update_result` carry `requested_by` inside their JSON, and the erasure sweep walks columns, so an
  erased GM's Discord id stays in those two blobs. A one-line anonymise beside the sweep; low
  privacy weight, since only administrators can request an update.
- **deferred (design)** — *A stranger's way to Underworld Contacts 15.* v1.11.1 let a broker buy
  from a stranger, +1 standing a sale, but a post stocks six rows drawn from ninety restricted,
  special or legendary items none of which any shelf sells, so each +1 costs a rare item and the
  threshold is effectively reachable only through the five underworld households. Whether a
  broker should also buy ordinary contraband is a content decision.
- **deferred (design)** — *The other hub-only groups.* `_GROUP_ACTION_ROOTS` holds some fifty groups
  and only `stall`, `boss` and `party` are slash commands; each more is one more entry in Discord's
  command list, so which of them earn a typed name is the owner's call, not a wiring.
- **deferred (design)** — *The spirit stat on gear reaches no rule.* Every piece in
  `EQUIPMENT_DEFINITIONS` carries `spirit`, the engine sums it in `combatEquipment` and
  `equipmentPowerRows`, and nothing reads the sum. Since v1.7.5 no surface shows it, on the owner's
  call, so the descriptions promise only what a fight does. Whether to wire it (a technique's power, a
  sense check) or remove it is a mechanic, not a wiring.
- **deferred (known limit)** — *The leaf sweep counts a leaf pressed into a designed refusal as covered.*
  Left open as a known limit on the owner's call (v1.3.4).
  This is rc.58's finding on the Discord side, and it is the reason `/battle challenge` was green for
  releases while no run had ever begun a battle: the sweep's contract is *"the reply is a result or a
  designed refusal"*, and a leaf that has only ever been refused has had only its refusal proved.
  rc.58 tightened the engine half from *called* to **resolved** with `REFUSAL_ONLY_OPERATIONS`, and
  the Discord half cannot simply copy it. A refusal is the **correct** answer for most leaves under a
  sweep — no sect, no beast, no lot, no partner — so the equivalent is not one allowlist but a
  per-leaf statement of which ones must actually resolve, and the state each needs built first. That
  is a roster and a decision, not a wiring, and inventing it unasked is what this file exists to
  refuse. What v1.0.12 does instead is raise the player past the curriculum before the sweep, which
  is what reached this one.
- **deferred (content)** — *A deployable array's numbers are not on the craft menu.* The effect of an
  array disk lives in the engine's `deployedArrayDefs` (`property_storage_actions.go`), a Go literal
  Python cannot read, so `item_does` describes an array in the item's own words rather than copy the
  numbers into a second table. Moving those six definitions into `content/world.json` (an
  `array_effect` block on each disk, read by both sides) would let the menu print them; it is a
  content move of its own, so it waits for the owner's word.
- **deferred (decision)** — *Registry NPCs cannot sponsor a sect recommendation.* `resolveRecommenderTx`
  accepts catalogue NPCs and a running event's cast only, so a sect's generated Elders - who now stand
  at its gate - cannot vouch for an applicant. Whether they should is a decision about how open the
  door into a sect is, not a wiring.
