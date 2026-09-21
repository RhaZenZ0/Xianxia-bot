# TODO — the punch list

What is open, and what each open thing became. Every entry is either **fixed** in the release named,
or **deferred** with the reason; nothing is left open without one of those two words, and
`tests/python/contracts/test_playtest_gate.py` holds the file to that. A fixed entry is **kept**
rather than deleted: it is the record of what the item turned into, and the release write-ups in
`CLAUDE.md` cite these by name.

Newest first. Add to it as findings come in — from the live pass (`docs/playtest/`), from a review,
or from a sweep of the tree.

It began as the v0.34 playtest's findings (`docs/history/ROADMAP_1_0.md`, Gameplay-complete II),
driven by `scripts/playtest_engine.py --launch` against a scratch engine; those entries are still
here, further down. It was `docs/KNOWN_LIMITATIONS.md` until v1.0.1 — a name that described the
deferred half and not the half that says what was done about it.

## Findings

- **fixed (v1.0.1)** — *A method you knew could not tell you what it needed.* Found by playing. A
  player bought an Inscription slip, read it, and had no way to learn that a Swift-Wind Talisman
  wants one `talisman_paper` and one `spirit_ink`. `character_recipes` had four writers and no
  Python reader; `get_recipe_definition` parsed a recipe's `cost` and nothing printed it; and the
  engine computed the exact shortfall and refused with the bare words "missing materials", which the
  bot replaced with a vaguer line of its own. See CLAUDE.md, "A method that could not say what it
  needed".
- **fixed (v1.0.1)** — *A player could not reset a character without a GM.* `/begin` refuses while a
  `characters` row exists, dying is not a reset (`lifecycle.true_death` has three callers and none is
  voluntary, and Samsara deliberately carries the echoes forward), and the only true wipe was
  `admin.player.erase` — a data-protection lever being used as a restart button. `character.reset`
  is the player's own door, on `/reset` and `/character → Samsara`, bounded by the anonymise
  disposition (no reset once the character is named on a row a shared world keeps) and by an
  allowance of three counted from rows the sweep keeps. See CLAUDE.md, "Starting over without a GM".
- **fixed (v1.0.2)** — *The reset was briefly uncapped, and the line to Samsara was held by
  accident.* The allowance is three per Discord account, ever — not per character, not per life —
  counted from `event_log` rows the sweep is told to keep. And `TestAResetIsNotASmallSamsara` now
  holds the distinction that matters: **Samsara is what death opens and it remembers** (memory seed,
  talent/law/insight echoes, legacy points, craft echo, a lineage rolled off the dead life's karma);
  **a reset keeps none of it** and the account begins again at incarnation 1. That was already true,
  because `soul_legacy` is swept like any other row — but true by accident, and a keep added to that
  table later would have turned a reset into a cut-price samsara with nothing going red.
- **fixed (v1.0.3)** — *A craft took your materials and told you it failed.* Found by playing:
  *"it doesn't let you craft but also takes your items"*, from a player whose bag held six of the
  pills they had been told they never made. `craftResolveAction` shipped the flattened `d1`/`d2` and
  no `degree`, while the reply's `roll_line` reads `die1`/`die2`/`degree`, so every craft past the
  materials check raised `AttributeError` **after** `applyAuthoritative` had committed. v1.0.1 found
  and fixed exactly this shape for the forage reply forty lines above and did not carry it to the
  craft. Neither harness could see it: the engine half asserts on the result map and renders no
  reply, and the Discord half's generic sweep only ever reached the designed "missing materials"
  refusal. The result carries the whole `roll` map now, and
  `TestAResultThatReportsARollReportsTheWholeRoll` states the general rule once, in Go.
- **fixed (v1.0.3)** — *Ninety-two defeats in a hundred left a cultivator at zero vitality with no
  way off it.* `fatalChance` is `min(75, 8+gap*3)`, so a same-realm loss is fatal eight times in a
  hundred and ended at `vitality=0` the other ninety-two — while the two *fate-rescue* branches, the
  rarer and strictly worse outcome, each wrote `vitality=1`. Four branches, four answers, and nothing
  in the game restored vitality with time. All four go through `survivedDefeatTx` now, and
  `condition.treat` applies the treatment item's own `Use.Instant` restore, so a Recovery Pill heals
  what it says it heals instead of being spent on the roll alone.
- **fixed (v1.0.3)** — *The seventh cultivation path had no manuals, in a game with 160.*
  `app/rules/advanced_catalog.py` named six paths where `content/world.json` offers seven, and the
  generator's modulus is what picks a path per id — so **none** of the 160 manuals named the Ghost
  Cultivator, while `death_qi_system`, a whole authored subsystem plus three hundred lines of Go,
  opens with `"path": "Ghost Cultivator"` and exists to serve it. It has 23 now, the same as its
  siblings. `PATHS` is pinned with the reason: the ids embed the path name, so widening the modulus
  re-points every existing id.
- **fixed (v1.0.4)** — *Nothing in the game restored vitality with time.* Twelve `SET vitality`
  statements in `go_core`, four of them damage, and not one keyed on rest, cultivation, seclusion or
  the scheduled tick — four pills and one technique were the whole of it. `vitality_recovery.go`
  mends a quarter of a cultivator's own maximum per world day, settled lazily on the authoritative
  path rather than as a simulation step, because those batches are daily and sit behind an automation
  flag a GM can switch off and state a player is stuck behind must not depend on one. The leftover
  minutes are carried, an active battle is excluded (the battle row and the sheet are kept in
  lockstep), the rate is content and an unauthored one heals nobody. Schema 59 for the anchor,
  because `updated_at` moves on every write.
- **fixed (v1.0.5)** — *A quest was recorded only after the command had answered, so a failure while
  drawing the reply lost progress the engine had already granted.* Seventeen sites wrote
  `await announce_quest_progress(interaction, await QUESTS.progress(...))` and eight placed that one
  statement after the reply, each citing rc.28 — a rule that is real and is about the
  *announcement*, which falls back to `response.send_message` and would otherwise spend the
  interaction on the quest line. Nesting the two made the record inherit the announcement's position,
  and v1.0.3's craft is what it cost: six pills made and the errand still reading zero, reported as a
  second bug. `record_quest_progress` is the record on its own, never raises, and runs before the
  reply; `announce_quest_progress` still runs after it.
- **fixed (v1.0.5)** — *The craft picker offered twenty-five methods to a cultivator who knew three.*
  `recipe_autocomplete` read `DB.search_catalog("recipe", …)` — the whole 33-recipe catalogue, capped
  at Discord's 25 — while `craft.resolve` refuses any method the player has not learned. That is the
  rc.46 rule (a surface must not offer what the engine will refuse) in a place nothing was holding
  it. It reads `DB.get_known_recipes` now, and an empty picker names the two doors that end it rather
  than being a dead end. The hub renders the same callback as a drop-down, which is how *"why is
  crafting a drop-down menu"* turned out to mean *"a menu of things I cannot make"*.
- **fixed (v1.0.7)** — *There was one era for the whole game, and half of what an era did reached no
  rule.* Every reader asked `WHERE active=1 ORDER BY era_id DESC LIMIT 1`, so one row priced a siege
  among the immortal courts and a cultivation session in a Mortal village alike — while the capitals
  have been per world since schema 4 and a world's news since schema 56. Schema 60 gives an era a
  world; each of the four now walks its own cycle of **six eras of sixty world days**, exactly one
  world year, authored in `content/world.json` rather than in a Go literal (which had a third copy in
  `app/rules/advanced_runtime.py`). And the counting is the finding: of the eight modifier keys the
  old cycle authored, production Go fetched **four** — `secret_realm_frequency`, `market_volatility`,
  `beast_encounter_rate` and `recovery_rate` each occurred exactly once in `go_core`, in their own
  declaration — so the Beast Tide Era did nothing whatever to beasts. Two are wired
  (`beast_encounter_rate` on the hunt margin, `recovery_rate` on v1.0.4's vitality recovery) and two
  are refused. See CLAUDE.md, "An era belongs to one world".
- **deferred (design)** — *`secret_realm_frequency` has nowhere honest to land.* It would weight the
  `kind: "secret_realm"` branch of `eligibleUnexpectedEvents`, whose weights rc.53 deliberately
  balanced so each realm totals 3 and a deep realm is not both harder to reach and half as likely to
  open. Letting an era scale them would undo that balance silently, and doing it properly means
  deciding what an era should do to a realm that already fades once you outgrow its world. A
  mechanic, not a wiring. No era authors it, and `TestNoEraAuthorsAModifierNothingReads` refuses one
  that tries.
- **deferred (design)** — *`market_volatility` has no prices to move.* Nothing in the game varies a
  price at all: a shop price is content times a fixed markup, and an auction settles on bids. An era
  term would need a price mechanic to modify first, and inventing one to justify a modifier is
  backwards. Same refusal as above.
- **deferred (harness)** — *The Discord half drives the reset leaf but cannot guarantee it reaches a
  success.* Section 9b presses `/reset` last of all and accepts either the reset or its designed
  refusal, reporting which, because whether the swept cultivator has left a mark the world keeps
  depends on what the sweep happened to do. The success path is driven end to end by
  `scripts/playtest_engine.py` on an account created for it (`QUITTER`), so both outcomes are covered
  — but by two harnesses rather than one, and the Discord half's success wiring (the "is gone" reply,
  the remaining count) is proven only when the dice go that way.
- **fixed (v1.0.3)** — *A household's manual was never the household's.* `family.lesson` hands the
  family's manual over at the head of the house's own lesson, beside its story and its keepsake, and
  the tier was already right and already enforced. What was wrong was whose text it was: eight of the
  thirteen households handed a child the Azure Cloud Sect's or the Jade Meridian Sect's entry manual
  as the family's own teaching, and five handed out a generated `advanced_orthodox_NNN_<path>` with
  its catalogue index in the title. The catalogue was why — of 160 manuals, 142 are generated-shaped
  and of the 18 authored ones twelve carry a `sect` and the other six are all Demonic, so there was
  **no authored, sect-less, non-forbidden manual anywhere in the game** for a household to teach.
  `scripts/author_household_manuals.py` writes the thirteen, one per house, named out of its own
  authored story, each deriving its element through `manual_element` rather than restating it — and
  all thirteen are **Mortal** grade, because the old five-`Mortal`/four-`Spirit` split was a
  permanent nine-percent cultivation difference decided by birth and stated nowhere.
- **fixed (v1.0.1)** — *No gate held a parsed content field to having a reader, and the class
  kept producing findings.* v1.0.0-rc.55 found `RootGrade.CultivationMult` and
  `RootGrade.BreakthroughBonus` parsed and read by nothing — the grade decided how a cultivator was
  made and nothing about what they were — and it was found by hand. rc.58 then built exactly this
  gate one level down, for modifier *stats* (`modifier_vocabulary_test.go`, which requires each to be
  **fetched** by a rule rather than merely named). Nobody built it for the fields themselves. A sweep
  of `worlddata` finds 439 parsed fields across 63 structs, of which **seven** are never read through
  a selector anywhere in production Go; four of those are legitimately read by Python for display
  (`advantage`, `drawback`, `objective`, `channel_name`), which is the distinction a gate has to
  make and the reason a naive one would be noise. The three that survive are the two entries below.
  `field_readers_test.go` is that gate, in the rc.58 shape: a read is an `*ast.SelectorExpr`, never a
  substring, and a composite-literal key is deliberately not one — writing a field is not reading it,
  which is the whole distinction rc.55 turned on. `fieldsReadByPresentation` names the four and the
  file that prints each; `unreadContentFields` names the three below with the decision each waits on.
  It is a **floor, not a proof**: without `go/types` it cannot tell `PhysiqueDefinition.Name` from the
  forty other structs with a `Name`, so a field sharing a read name passes unexamined. It never calls
  a read field unread, and it catches the uniquely-named orphan — which is what every finding of this
  class has been.
- **deferred (planned)** — *Seven cultivation paths each name a skill, and the name reaches nothing.*
  `worlddata.Path.Skill` is parsed from `paths.<name>.skill` — Sword, Spiritual Arts, Martial Arts,
  Soul Arts, Beastcraft, Formations, Ghost Arts — and is read by no rule, no card and no Python
  reader. Each of the seven strings occurs **exactly once in the whole 2.5 MB content file**: its own
  declaration. So they do not name a manual, a technique, a profession or any roster the game has;
  they are a vocabulary with nothing behind it, which is `/learn`'s shape (rc.43) with no mechanism
  waiting at the other end rather than one. Deciding what a path's skill *is* — a display line on
  `/sheet`, a bonus, or a field to delete — is content design, not a wiring fix, which is why this is
  recorded rather than quietly wired.
- **fixed (v1.0.6)** — *Both of an auction house's door fields were read by nothing, and the rule
  they described was held only by the bot.* `protected_interior` and `door_rule` were set on all 48
  houses and read nowhere; meanwhile `app/ai/narrator_context.py` told the narrator *"PROTECTED;
  violence cannot mechanically begin here"* while `combat_actions.go` named `SafeZone` zero times —
  so `/battle challenge`'s refusal lived entirely in `battle.py` (rc.48's rule, a fourth time) — and
  `advanceHunters` raised pressure, engaged and **captured** a fugitive without reading a location
  anywhere in it. `protected_interior` is wired as the sanctuary those 48 descriptions promise: a
  hunter watches from the doors and cannot reach in, while pressure still rises. It is deliberately
  a second predicate beside `safe_zone`, which is true on 446 of 477 places and so means *not the
  wilds* rather than sanctuary — gating the hunter on it would end the bounty system rather than
  give it a refuge. `door_rule` is retired: it restated the second half of the same sentence, no
  house's prose can differ, and the engine already ends the protection at the door by standing the
  ambush outside. See CLAUDE.md, "The protection only the bot believed in".
- **deferred (planned)** — *Nothing can grant a physique, not even a GM.* `admin.player.set_physique`
  writes `evolution_stage`, `progress` and `stability` and nothing else (`actions.go:2212`), and the
  only statements that ever write `physique_id` are character creation and samsara —
  `aptitude.awaken` and `aptitude.evolve` both pass the loaded bundle back through `savePhysique`, so
  they move the state and the stage and never the identity. This is **not** dead content: all eight
  non-ordinary physiques are drawable at creation, because `generatePhysique` gives every one weight
  at least 1 and favoured paths and roots only raise it. So it is a missing lever rather than a
  `/learn`-class fault — a GM cannot hand somebody `nine_yang_solar_body`, cannot correct one rolled
  wrong, and cannot stage one for a playtest. Adding it means `physique_id` and `name` on the
  payload, the catalogue check that `aptitude_actions.go:203` already makes, and `physique_id` in
  the undo snapshot, which today carries only the three numbers it can restore.
- **deferred (planned)** — *`admin.player.set_spiritual_root` writes a grade the ladder may not
  carry.* The lever upserts `grade`, `purity` and `mutation` with no check against
  `spiritual_root_system.grades` (`actions.go:2119`), and `gradeIndex` answers 0 for a name it does
  not know — so a typo'd grade is silently worth the bottom rung's `cultivation_mult` and
  `breakthrough_bonus` rather than erroring. v1.0.0-rc.55 found and wrote down exactly this shape
  ("a fallback that looks like a value is not a sentinel") and gated the *fixtures* with
  `TestEveryFixtureRootStandsOnTheLadder`; the lever that a GM actually types into was left
  ungated, so the one writer a human drives is the one nothing holds. The fix is the same check the
  ladder already makes, at the lever.
- **deferred (planned)** — *🗺️ Cultivation World is open to somebody who has never played, and no
  role says otherwise.* `#player-homes` and `#expeditions` sit in that category, read-only since
  v1.0.0-rc.59 but visible to everyone, so a newcomer's sidebar advertises rooms they cannot use
  directly above the `#begin-here` they are meant to go to — while every other player-facing category
  is gated (Realm Capitals by the presence role, the four World Events feeds by the realm-access
  role, Admin by administrator). The deeper half is that **no "has a character" role exists at all**:
  `_sync_realm_access_roles` and `_sync_realm_presence_roles` both run from `require_character` and so
  only ever fire for somebody who already has one, which means nothing in the server can be gated on
  having played. The plan is one generated role (`Xianxia • Cultivator`), granted from the same
  `require_character` sync block and from `/begin`, revoked at `admin_erase` (which cannot ride
  `require_character` — after erasure that call never fires again), and backfilled through
  `_sync_all_realm_access_roles`, the sweep Full Setup and `/admin server sync_roles` already call.
  Four traps are known and none is optional: the bot must allow **itself** before denying `@everyone`
  or it 403s itself out of the channel (rc.52); the overwrite has to reach channels that already
  exist, not only ones the run creates (rc.59, same file); both anchors carry `private_thread`s whose
  members still need to see the parent, so the grant must precede thread creation; and
  `realm_presence_role_name` already falls back to `world_name`, so a fifth hub with no `display_name`
  would collide with `_realm_access_role_name` and silently merge two gates — adding a third name to
  that family is the moment to gate it.
- **fixed (v1.0.1)** — *The release after a live pass would have deleted it.* The checklist is named
  after `RELEASE_VERSION`, so the filename held still across all fifty-nine release candidates of
  1.0.0 and `merge_ticks` carried every tick; on the first bump that renames it the target does not
  exist, `old` is empty, and a fresh checklist is written with every box blank. The ticks are
  inherited from the newest older checklist now, dated with the release each was walked on so
  carrying one is not a claim that the new release was walked, and the superseded file is removed
  because `docs/playtest/` is one checklist.
- **fixed (v1.0.1)** — *No clan alliance had ever been formed at runtime, in any world.*
  `martial_clan_relations` was seeded once per household behind a `COUNT(*)==0` guard, with an
  **invented** partner — a surname off a list, `partner_family_id` left NULL although the column is
  foreign-keyed to `birth_families` — and the only statement that could insert another was
  `combat_aftermath.go`, which writes `blood_feud` alone. The `clan_dynamics` batch nudged existing
  scores ±1 and created nothing. So four households meant four relations on the day the world opened
  and four for ever after. `clanDiplomacy` is the step that forms one between two real households,
  written from both sides; `npcPoliticalMarriages` had done the same for `sect_relations` since
  rc.24, in the file whose own comment names the clan fault it did not fix.
- **deferred (design)** — *A safe zone refuses a fight somebody chose to start, and nothing else.*
  v1.0.6 put that rule in the engine, and it deliberately lets two involuntary fights through: a
  world event that lands in a town (rc.49's asymmetry) and the auction door ambush, which
  `auctionLeaveAction` stands at `EntranceLocation` — a safe zone for 47 of the 48 houses, since
  Greenriver Town is the one rough entrance. Refusing those would delete the event battle in 446 of
  477 places and the door risk in 47 of 48, so the asymmetry is the design. What is genuinely open
  is whether a town being ambush-able *reads* right to a player who was just told the place is
  protected; moving the ambush to the first unprotected ground the fugitive reaches is a mechanic
  rather than a wiring, and wants its own change.
- **deferred (design)** — *Nothing ends a clan relation.* `martial_clan_relations.active` is written
  1 by every INSERT, read by every SELECT, and set to 0 by nothing in the tree. So an alliance warms
  toward 100 and a rivalry cools toward −100 and neither can ever become the other, because no rule
  re-types a row either. What a broken alliance leaves behind — a rivalry, or simply nothing — is a
  decision about the fiction rather than a default, and adding a dissolution without making it would
  be picking one silently.
- **fixed (v1.0.0-rc.52)** — *Nothing in either harness reached a `create_category` call.*
  Every provisioning helper defaults to `create_missing=False` and the one caller that passes True
  is the GM dashboard's Full Setup, so no slash command and no hub button could reach it — the
  categories, the four realm capitals, the nine auction channels and (now) the four per-world
  events channels were provisioned by code no test had ever run. rc.51 recorded it as deferred
  because the Discord sweep's own rule is that a loop goes through a surface rather than a handler.
  The resolution is that the bot's **control plane is a surface** — it is just not a Discord one:
  section 2b of `scripts/playtest_discord.py` posts `{"action": "setup"}` to
  `POST /control/discord` with `X-Xianxia-Control`, exactly as the dashboard does, then asserts that
  every category exists and stands in its stated order, that each capital and each world feed sits in
  the right one, that nine auction channels were made, and that a second Repair over the same layout
  creates nothing new.
- **fixed (v1.0.0-rc.51)** — *Teardown never deleted an auction channel, so it never deleted the
  capitals' category either.* `clear_discord_bindings` has `DELETE`d from `auction_house_channels`
  since v0.33.1, while `teardown_managed_discord_layout` built its targets from the base bindings,
  the realm hubs and `#bugs` and named no auction channel — so the nine of them were left standing
  with their bindings forgotten, and because they sat inside it, `🌌 Realm Capitals` always had
  "9 other channel(s) inside" and was never once deleted. Found while giving the auctions a
  category of their own, which would have inherited the same fault.

- **fixed (v0.36.1)** — *Greenriver Town had no roads.* The starting town sat outside the road
  graph, so it had no gates (v0.36.0), could only be reached by direct travel, and a fresh
  character's first road journey was the capital's rather than their own town's. It now has roads
  to Azure Crown Imperial City and Riverguard City, with the gates to match. Found by asking why
  the gate test skipped it; the engine playtest's city section (v0.36.1) now starts a road journey
  from a real gate.
- **fixed (v0.34.0)** — *A commission's last objective could never be turned in.* `quest.progress`
  wrote the row `completed` and then asked `resolveCommissionTx` for an `active` row, which failed
  with "no active commission by that name" and rolled the whole progress back. Every commission
  since v0.24.0 could be taken and worked but not finished; only the dashboard's manual close
  completed one. The row now stays `active` until the resolution flips it, and
  `TestACommissionCompletesThroughProgressAlone` drives one through progress alone.
- **fixed (v0.34.0)** — *Reset Cooldowns did not reset the sect trial's retry wait.* The wait is
  read off the last failed `sect_recruitment_attempts` row, not the `cooldowns` table, so a GM
  resetting a disciple's cooldowns left them waiting a day anyway. A full reset ages the failed
  attempts out (the history stays) and reports `trial_retries_cleared`;
  `TestResetCooldownsClearsTheTrialRetryWait` pins it.
- **fixed (v0.33.1)** — *The Quest Forge's prompt listed forty-eight auction floors before the
  town a story is set in.* Floors and their stewards are off the capped lists; validation accepts
  them regardless.
- **fixed (v1.0.0-rc.47)** — *The three live columns of the checklist asked a person for what a
  machine now does.* Reachable from the hub, error text actionable, narration or fallback fired:
  248 actions times three, seven hundred and forty-four boxes, written in v0.34.0 when nothing in
  the tree could press a button. `scripts/playtest_discord.py` has pressed every leaf since rc.33
  and `test_playtest_coverage.py` holds it to the live definitions, so the first column was asking
  for work already done and the second for the wiring half of work already done. Not one box was
  ever ticked, across twelve regenerations - and `merge_ticks` was carefully preserving them while
  silently dropping the live-table ticks, the only ones that were ever a person's. The per-action
  columns are one machine-filled `Swept` column now, read off the harness's own `DEFERRED_LEAVES`
  so it cannot claim more than the sweep drives.
- **fixed (v1.0.0)** — *The live pass on the NAS is unticked.* It is walked and ticked: every row
  of the four live tables in `docs/playtest/v1.0.0.md`, against a real server running the release.
  What it covers is what the sweep structurally cannot reach — real Discord, a live AI route (it
  runs `NARRATOR_PROVIDER=procedural`), the auction channels, `/vote`, a mute expiring on its own,
  `update.sh --fetch` against the published archive and its `.sha256` sidecar — plus the one
  judgement neither harness can make, whether a refusal reads helpfully to a human, made against
  the sweep's log rather than against 248 empty boxes. The ticks then survived their first
  regeneration, which is rc.47's `merge_ticks` fix doing on real ticks what could only be argued
  about while there were none.
- **fixed (v1.0.0)** — *A release could ship a thing only a person can check with nothing asking a
  person to check it.* The live tables are prose a release has to remember to extend, and rc.59 did
  not: the category split, the re-parent of channels that already existed, the read-only lock on
  ones the bot did not create and the retired `#event-scenes` had **no rows at all**, and were
  walked off a hand-written page instead. The upgrade table was stale in the other direction — it
  named `--channel beta`, where the release candidates went out, while a tag with no `-` in it is
  published as GitHub's latest, so 1.0.0 is on `stable` and the row would have sent the next walker
  to a channel with nothing newer on it. Both are in `scripts/playtest_checklist.py` now, the
  layout as its own five-row table saying to walk it on a guild with history. There is no gate
  behind this and deliberately so: which features need a person cannot be derived from the code,
  which is exactly why the rule is written here instead.
- **fixed (v1.0.0-rc.39)** — *`get_world_clock` in Python re-anchored the clock the engine owns
  when the configured scale changed.* Named on the roadmap's remaining-authority list since v0.30.0.
  The method is gone: `world.clock` is a read-only engine query and every surface reads through it,
  `WORLD_TIME_SCALE` is the engine's key (compose passes it) and seeds a new world only, and
  `/admin world advancetime` changes the rate only when asked to. It had one live consequence: a
  rate a GM set on the dashboard was undone by the next command that asked the time.
- **fixed (v1.0.0-rc.48)** — *The simulation's force and bootstrap requests trusted a
  caller-supplied `game_minute`.* `RunDue` ignored one by design since the v0.22.2 review, with the
  reason written on the field — *"a scheduled tick must not be able to tell the world what time it
  is"* — while `ForceRequest` and `BootstrapRequest` carried the same field and used it, for
  twenty-six more releases. `runSystem`'s own comment said it stamped the anchor "at a caller-chosen
  minute", two hundred lines under the field that said the opposite. Both derive
  `game.CanonicalWorldGameMinute` now, the one door `RunDue` already read. The wire still accepts the
  field so an older bot mid-upgrade keeps working; the three Python client methods no longer take one,
  and nine call sites stopped computing a minute the engine throws away. `caller_minute_test.go` sends
  a wild minute and asserts the canonical one landed (it fails with `Force stamped 9999999` against
  the old code), and `test_simulation_minute.py` holds the Python side — where the assertion that
  encoded the fault lived, two tests below one refusing a forged minute on `/v1/game/action`.
- **fixed (v1.0.0-rc.58)** — *An authored modifier stat could reach no rule, and nothing said so.*
  `active_effects` modifiers were a vocabulary with no gate: content authored twenty stats and
  production Go wrote twelve, and seven reached nothing. Two were the `characters` column name
  written into a modifier slot — `sense_power_bonus` on `space_domain`, `sense_precision_bonus` in
  the engine's own Soul Wound — which is why the gate has to read an argument position and a map
  key rather than a name: that identifier occurs three times in production Go, twice as a column
  inside a SQL string. `modifier_vocabulary_test.go` holds the vocabulary from both sides with an
  empty allowlist, and its own drill found the last fault in it — pointing the content path at
  nothing made the whole test SKIP, green and useless, so a read that fails is a `t.Fatalf` now.
- **deferred (design)** — *A Law control effect's modifiers are never applied to anybody.*
  `special_effects.spatial_lockdown` and `.spatial_strangulation` carry modifiers describing a
  *target*, and `combat.technique` resolves both mechanically (suppression turns, damage) while
  writing no `active_effects` row — a battle opponent is a name on `battles`, not a row anything
  can modify, and there are no PvP techniques. So the modifiers describe something the engine has
  nowhere to put. `combat.technique` names the effect that landed now, so the content reaches a
  player as prose; applying it is a mechanic rather than a wiring, and wants its own change.
- **deferred (design)** — *`nine_yang_solar_body` scorches only through the purge.* Its drawback
  prose says "excess yang scorches the meridians" and v1.0.0-rc.58 gave it `fire_resistance -5`, so
  it bites on `alchemy.purge` and nowhere else. A physique that scorches on its own schedule needs a
  simulation pass over a body nothing currently ticks, which is a different change.
- **fixed (v1.0.0-rc.59)** — *A category or a read-only lock reached a fresh guild and no other.*
  Three of the five helpers that place a channel in a category computed it only inside
  `if channel is None and can_create:`, so a channel that already existed — pre-existing,
  name-matched, or bound by a GM — was bound where it lay and stayed there. The same line held
  `READ_ONLY_BASE_CHANNELS`, so `#xianxia-info`, `#expeditions` and `#player-homes` were read-only
  only where the bot had created them, and `ensure_base_xianxia_channels` returned `"repaired": []`
  as a hardcoded empty list into an audit row that had therefore never carried anything. rc.51 had
  already found and fixed this for the auction floors and rc.52 for the world feeds; the other
  three never got it. `test_the_layout_reaches_an_existing_server.py` now holds all five, with an
  empty allowlist.
- **fixed (v1.0.4)** — *Nothing in the game restored vitality with time.* Twelve `SET vitality`
  statements in `go_core`, four of them damage, and not one keyed on rest, cultivation, seclusion or
  the scheduled tick. `vitality_recovery.go` mends a quarter of a cultivator's own maximum per world
  day, settled lazily on the authoritative path (not as a simulation step, which is flag-gated), with
  the leftover minutes carried and an active battle excluded. The rate is content and an unauthored
  one heals nobody. Schema 59 for the anchor, because `updated_at` moves on every write.
- **deferred (content, v1.0.3)** — *The ghost inheritance prefers the wrong path.*
  `inheritances.stygian_keeper_legacy` is "a forbidden soul inheritance dealing with ghosts, corpse
  echoes and the boundary between life and death", grants `stygian_ghost_scripture`, and lists
  `preferred_paths: ["Soul Cultivator"]` - written when the Ghost Cultivator had no manuals to
  prefer. The scripture is also an item with `sect_value: 450` and no `type`, so it is worth 3,650
  and does nothing; making it the path's high manual is the rc.50 shape (an authored, priced,
  granted thing with no mechanism behind it) and is a content decision, not a wiring.
- **deferred (known limit, v1.0.3)** — *`Inheritance.PreferredPaths` is read by nothing.* The only
  `.PreferredPaths` reader in production Go is `secret_realm_actions.go:310`, which reads
  `SecretRealmRoom.PreferredPaths`. `field_readers_test.go` cannot tell the two apart - it is
  name-based and says so - so this is the documented blind spot doing exactly what its own docstring
  predicts rather than a gate failing.
- **deferred (design)** — *An event scene is only visible to somebody who has reached that world.*
  v1.0.0-rc.59 anchors an event's scene and its thread in that world's own feed, which is where
  rc.52 already sent the announcement, and retires `#event-scenes`. Those feeds are gated by the
  realm **access** role, so a scene in a world a player has not reached is now invisible to them
  where the shared channel was not. That is the trade the change makes rather than an oversight:
  an event is somewhere, and a cultivator who cannot reach the somewhere could not have joined it.
- **deferred (design)** — *Moderation is a nudge on the engine's dispatch layer, not anti-cheat.*
  A muted or frozen player is blocked from the ~150 authoritative ops; raw `/v1/db` writes the
  bot makes on their behalf and the simulation runner are not intercepted. Stated in
  `moderation.go`; a stronger guarantee would need every presentation write to carry the actor.
- **fixed (v1.0.0-rc.2)** — *The forty-three local auction floors share archetype prose.* Was:
  twelve archetypes, four worlds; a riverside hall read like a riverside hall in every world. Each
  floor now has a paragraph of its own - its river, crater, terrace or court, its broker by name, and
  where its protection ends - and the content gate holds the forty-three apart.
- **fixed (v0.39.0)** — *Typed play fills one argument, not two.* Was: `$ I give the pill to Qiao`
  routed to talk or use. A root may now declare several arguments, each with its own source (a
  known place, a carried item, an NPC present, a player present); `$ I give the pill to Li Feng`
  is a trade offer to Li Feng, and a line that names an NPC instead says what it lacks rather than
  guessing a cultivator.
