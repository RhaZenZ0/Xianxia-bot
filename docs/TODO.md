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
- **deferred (design)** — *Households for people the catalogue adds later.* Seeding runs once per
  world, so an NPC a content update adds afterwards is never offered a seeded marriage; the courtship
  tick is how they marry. A per-NPC marker would be the shape if that matters. Decided (v1.36.0): the
  courtship tick is how they marry; no marker.
- **deferred (design)** — *A scene sweep for events other than the tick's.* The sweep covers the two
  key shapes the tick mints (`auto:`, `secret_realm_rotation:`); a player's or a GM's event gets its
  scene from the command that made it, and telling "no thread yet" from "never had one" for those keys
  needs a marker in the payload. Decided (v1.36.0): waits for a live
  case; no marker until one shows.
- **deferred (design)** — *Event manifestations have no per-day reward cap.* A `combat.finalize`
  against an `event:` battle has no `target_key`, so insight and tempering are not limited per day;
  the site's finite beasts bound it. Decided (v1.35.0): left; the roster is the bound.
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
  path would be a second matching rule. Revisit only if a quest is authored with both. Decided again (v1.36.0): no change until
  a quest carries the pair.
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
- **deferred (design)** — *One engine query for the panel's gate state.* After v1.31.2 a refresh is one
  session, about sixteen SELECTs and seven engine actions in production. A single `panel.gates` query in
  Go, or the sixteen SELECTs through `/v1/db/batch`, would make it about ten round trips; not built,
  because each `get_x` is its own repository method and a batched read changes that shape for a saving
  that no longer dominates a press. Decided (v1.35.0): not built.
- **deferred (design)** — *A focus's end moves if the world's rate changes mid-focus.* `active_effects`
  keeps only a game-minute deadline, so the four real hours are converted at the scale the focus was
  taken at; a GM changing the rate during one stretches or shortens it, and at a stopped clock it lasts
  until the clock moves. A real-time column would fix it for this one effect at a schema change; it is
  left until a second effect wants a real-time end. Decided (v1.36.0): left until a second
  effect wants a real-time end.
- **deferred (design)** — *The merchant picker offers a merchant out of reach.* `/merchant buy`'s
  picker lists every travelling merchant and the first it offers can be in another world, so the press
  is refused with "is not within reach" - a surface offering what the engine will refuse (rc.46). The
  sweep's refusal roster found it (v1.38.0) and accepts the refusal, because the reach rule is the
  engine's and holds. The fix is one engine question, "who is within reach of me", read by the picker;
  whether an out-of-reach merchant is left off or drawn disabled with the world named is the owner's
  call. Decided (v1.38.0): recorded, not built with this release.
