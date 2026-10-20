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
  ladder, is the owner's call. Decided (v1.35.0): a content pass, later, not with this release.
- **deferred (design)** — *Black-market and fence prices are not read against the ladder.*
  `black_market.trade` sells and fences at its own prices in the post's coin; the arbitrage gate does
  not walk it.
- **deferred (design)** — *Permanent qi that a settle already took.* Every `qi_max_bonus` starts at 0:
  the qi a settle rebuilt over before v1.34.0 is in no column and cannot be told from the body's own
  figure. The honest repair is a GM `set_resource_caps` per player, or a one-off replay of completed
  `realm_perfection` / `body_realm_perfection` and `inheritances` rows into a bonus, which is an
  estimate because the percentage compounded on the then-current pool. Decided (v1.35.0): no replay;
  a GM sets the cap per player.
- **deferred (design)** — *An old audit row's undo is still the absolute snapshot.* Grant, adjust-item
  and teleport rows written before v1.34.0 carry no `moved` / `scene` and are undone as they always
  were; they age out of the audit log's useful window, and a migration that rewrites them is not worth
  a schema change. Decided (v1.35.0): no migration.
- **deferred (design)** — *Saving a shipped quest whose reward is above the GM budget is refused.* 35
  shipped commissions and realm-road quests pay more than the default authoring budget, so they cannot
  be opened and saved without lowering the reward first; the refusal names the budget. Whether an
  unchanged reward should pass is a decision.
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
- **deferred (design)** — *Event manifestations have no per-day reward cap.* A `combat.finalize`
  against an `event:` battle has no `target_key`, so insight and tempering are not limited per day;
  the site's finite beasts bound it. Decided (v1.35.0): left; the roster is the bound.
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
  Decided (v1.35.0): the strength floors stay on the qi ladder.
- **deferred (design)** — *Which world a cultivator's strength puts them in is read off the qi ladder.*
  `realmWorldGo(realm_index)` chooses the hidden sect's branch, `currentWorld` falls back to the qi
  realm for a character at a place the catalogue does not carry, and `/sense` reads
  `WORLD.realm_world(realm_index)`; a body cultivator in the Spiritual World at qi 3 reads as Mortal
  there. Left because each is "which world am I from" rather than "may I enter". Decided (v1.35.0):
  stays on the qi ladder with the strength floors.
- **deferred (design)** — *A sweep of every payload field an engine action reads.* The samsara
  wait was rc.56's class found one action at a time (v1.35.0 closed it); a gate that walks every
  payload struct the engine unmarshals and holds each field against a list of what a caller may
  state would find the next one before a release does. Not built yet: the list is the decision.
- **deferred (design)** — *A realm an old undo reopened stays open.* Undos run under v1.23.1 to
  v1.32.0 could put `active=1` back on a `completed=1` perfection row, which the quest and trial
  actions read as an open path. No migration writes it back, because nothing records whether a GM
  meant a realm to be reopened; `SELECT user_id, realm_index FROM realm_perfection WHERE active=1
  AND completed=1` (and the same on `body_realm_perfection`) finds any, and the Player Editor sets
  one right. Decided (v1.35.0): no migration; the query above is the remedy.
- **deferred (design)** — *A Discord GM quest-report lever.* The Player Editor is the only door to
  `admin.player.quest_progress`; there is no `/admin player` counterpart, so a GM away from the
  dashboard cannot unstick a quest. Decided no for v1.33.0: the lever stays a replay of a player's
  report and the dashboard is where a GM works one character's quests. If a Discord door is wanted it
  should be the same picker over `quest_journal`'s objectives, not a typed type-and-target pair.
  Decided again (v1.35.0): dashboard only.
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
  Decided (v1.35.0): not built until one does.
- **deferred (harness)** — *The playtests claim from a whole place only.* `playtest_engine.py` claims
  the hills and `playtest_discord.py` presses `/territory claim` wherever the sweep's player stands;
  neither claims from a gate or a street, so the redirect is held by the Go tests and the Python
  commands test and not by a real server. A leg that stands at a city's gate, claims, and holds that
  the city's row (not the gate's) carries the banner would drive it end to end.
- **deferred (design)** — *A repaired street forgets what it paid.* Releasing a held part ends the
  tribute it paid (a lot a week, v1.29.0) and folds a sole holder into the whole city; a sect that
  held several streets of a city loses the extra lots. The city pays its own tribute once folded,
  which is the rule; whether a fold should carry the sum is a balance decision nobody has asked for.
  Decided (v1.35.0): the rule stands.
- **deferred (rare)** — *A truly dead install holds the Request button for up to two hours.* The
  lease is the exit for a watcher that died mid-install (it was fifteen minutes). Shorter would free
  the button sooner and let a GM cancel an install that is merely slow on a cold NAS build; the
  length is `updateInstallLeaseSeconds` (and the card's twin) if the owner wants it moved. Decided
  (v1.35.0): two hours stays.
- **deferred (design)** — *The classic journal's door edit.* `open_hub_in_place` edits the classic
  (non-layout) journal message without `content=None, embed=None`; if Discord refuses that edit the
  door opens a new panel and never presses the leaf. Not verified live.
- **deferred (design)** — *One engine query for the panel's gate state.* After v1.31.2 a refresh is one
  session, about sixteen SELECTs and seven engine actions in production. A single `panel.gates` query in
  Go, or the sixteen SELECTs through `/v1/db/batch`, would make it about ten round trips; not built,
  because each `get_x` is its own repository method and a batched read changes that shape for a saving
  that no longer dominates a press. Decided (v1.35.0): not built.
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
  players already hold, the way migration 61 did, and a re-authored curriculum roster. Decided
  (v1.35.0): waits on the Most-used commands card's data.
- **deferred (design)** — *The other hub-only groups.* `_GROUP_ACTION_ROOTS` holds some fifty groups
  and only `stall`, `boss` and `party` are slash commands; each more is one more entry in Discord's
  command list, so which of them earn a typed name is the owner's call, not a wiring. Decided
  (v1.35.0): none more until the Most-used commands card shows demand.
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
