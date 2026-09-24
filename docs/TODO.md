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

- **fixed (v1.1.0)** — *There was no road into a sect.* Reported in Discord: at a Major Sect
  Recruitment event a player was told by the Visiting Elder that they were impatient and would not be
  taken, and the next player asked *"What menu?"*. The event named no sect and its elder belonged to
  none, so the refusal was the narrator improvising from a role reading "Decides who is taken"; no
  road reaches any of the twelve sect gates, so `/explore` never finds one; the envoys' hall said the
  routes were on your map and wrote nothing; Recommendation and Trial opened at realm 1 on leaves only
  a hub press can reach, so a realm-0 player could not join at all while `road_to_a_sect` - handed to
  everybody by the beginner path - asked for exactly that and had no reachable reporter; no sect work
  was open to an outsider; and the recommendation's "+N" was never added to a roll. The recommendation
  and the trial also took their gate from the caller, which writes a road-less place onto a travel
  list. All of it is the engine's now. See CLAUDE.md, "The door into a sect".
- **fixed (v1.1.0)** — *A button was drawn where the engine would only refuse it because of where you
  stand.* Reported from play as `/economy → City Shops → Browse` in the birth household. Every leaf
  refused purely by place is a locked line naming why and where it works, asked with the engine's own
  predicate; City Shops' Here is always drawn. See CLAUDE.md, "A button is drawn where it works".
- **fixed (v1.1.0)** — *A caravan could not leave from a city's gate.* `caravan.dispatch` planned from
  the raw location while travel uses `cityOf`, so it refused at every gate and district of the city it
  was standing in - v1.0.9's household-door fault in a second handler.
- **fixed (v1.1.0)** — *Two exits named a door that refused.* A sect residence's way out was printed as
  `/abode → Leave`, and `abode.leave` reads `cave_abodes` while a residence is a `sect_abodes` row; and
  `abode leave`/`abode focus` were hidden from anybody owning no property, which is every invited guest
  standing inside somebody else's.
- **deferred (decision)** — *A sect's trial tuning is authored and not read.* `recruitment.base_tn`,
  `path_bonuses`, `root_affinities` and `family_archetype_bonus` are in the content and in the Python
  notes a trial prints, and `sectTrialActionGo` reads none of them: the TNs are 15 and 14 for every
  sect. Wiring them changes every sect's odds, which is a balance call rather than a wiring.
- **deferred (engine)** — *A catalogue sponsor's position is checked by the bot.* `resolveRecommenderTx`
  checks that a delegation's elder is standing where the player is, and leaves a catalogue sponsor to
  the bot, because schedules and circuits are resolved in Python. A bound that lives in the client is
  not a bound; moving it needs the schedule in Go.
- **deferred (engine)** — *`sect.discover` takes the sects from the caller.* The reconcile a read path
  runs sends the list of sects to mark discovered, and the engine writes what it is told. It writes no
  route, so it cannot put a place on a travel list, but it can satisfy the trial's "discovered" check.
- **deferred (content)** — *The "Impress the Visiting Elder" node rolls an attribute nobody has.* It
  and the Possessed Villager node roll `heart`, which `canonicalAttribute` accepts and no character is
  created with, so both roll at +0.
- **deferred (content)** — *The Nine-Echo Sword Wraith can never be fought.* Its lair in
  `bossTemplatesGo` and `BOSS_TEMPLATES` is "Sword Grave of Nine Echoes", which is the name of a secret
  realm and not a catalogue location, so `boss.start` can never find the whole party standing there.
  The two tables are held equal now; where the lair belongs is a content decision.
- **deferred (engine)** — *Two place rules live only in Python.* `/world → City → Accept` checks the
  city board and `/sect → Territory → Claim` checks for a territory row here, and the engine checks
  neither; the panel hides both by the same Python rule, so the hide is right and the bound is not.
- **deferred (decision)** — *A property may be founded inside a birth household.* `abode.establish`
  refuses a property, a sect residence, a personal world and an auction floor, and not a household, so
  the panel draws Establish there too.
- **deferred (cost)** — *Three place-only refusals are not hidden.* The ghost harvest's ground multiplier
  would need a Python copy of `deathQiGroundMultiplier`; and the black market's post and an array's
  departure would need a database read on every panel refresh. Each is hidden only inside a private
  room, where the answer needs no read.
- **fixed (v1.0.16)** — *A condition could not be cured by the medicine made for it.* Reported
  from play as six Heart-Calming Pills on a severity-3 Qi Deviation at 28%, six failures, and *"I
  can't heal injuries"*. The treatment rolled Insight + Spirit against `10 + 2 × severity` through
  `canonicalAttribute`, which counted the condition's own effect row - and Qi Deviation, Meridian
  Damage and Dantian Damage take their severity off Spirit (Soul Wound off both), so the cure grew
  harder exactly as the ailment grew worse; a failure mended nothing and still spent the pill. Every
  treatment mends now (one level on a failure, two on a success, three on a strong success), the TN is
  `10 + severity`, and the condition being treated is left out of its own roll. See CLAUDE.md, "A
  treatment always mends".
- **fixed (v1.0.17)** — *A keeper paid a Saint what it paid a beggar.* Reported from play as the Qi
  Nourishing Pill selling for 11 and buying back for 4. The third itself is the authored rule and is
  kept: across all 831 things a shop both sells and buys the buy-back is a median of 33% and never
  above 40%, and nothing can be bought in one shop and sold in another for a profit. What the check
  found was on the crafting side - three Mortal recipes (the Qi Nourishing Pill, the Spirit-Iron Sword,
  the Spirit-Iron Lamellar) sold back for less than their own ingredients. On the owner's call a rank
  in the item's trade now adds 2 of the shop's coin per rank above Novice to what the keeper pays,
  never reaching the cheapest shelf price in that coin, and shelf prices are untouched. See CLAUDE.md,
  "A keeper pays a craftsman by rank".
- **deferred (content)** — *The Starfall Talisman is a profit for anybody who can make it.* Found by
  the same measurement, and older than v1.0.17: its inputs cost 102 on the Celestial World's shelves
  and the Celestial Mandate talisman hall pays 120 for one at Novice, so any cultivator who knows the
  method turns a profit on every craft, bounded only by shelf stock and the roll. Whether that is a
  reward or a slip is a content decision; lowering that hall's `buys` line below 102 closes it.
- **fixed (v1.0.15)** — *The Heart Calming Pill could not be made in the only world that sold it.*
  Reported from play, straight after the Apprentice examination in Jadewood: *"missing materials:
  Twin Extremes Ice-Fire Fruit x1"*. The method's slip is shelved only in the Mortal World and the
  examination teaches it anywhere; the fruit is on no shelf (auction-grade stock is refused by the
  content gate) and is foraged only at `worldTier >= 1`. The recipe asks for a `moonveil_herb` now,
  the Mortal World's four town apothecaries shelve it, and
  `test_a_method_can_be_made_where_it_is_sold.py` holds every world that sells a method to offering
  what it needs. The craft refusal names where each short material is sold, from where the player
  stands. See CLAUDE.md, "A method can be made where it is sold".
- **deferred (design)** — *A higher trade examination teaches methods its own world cannot supply.*
  `teachRankRecipesTx` hands over every recipe of the trade at the rank passed, wherever the hall
  stands, so the Journeyman examination sat in the Mortal World teaches the Dawn Lotus Vitality Pill
  and the Golden-Edge Talisman, whose herb and ore are shelved from the Spiritual and Immortal
  Worlds up. v1.0.15 holds only the first examination to its own world, because that is the one a
  Mortal cultivator sits first and the one the report came from; from the second up it is knowledge
  ahead of the road rather than a dead end, and the refusal now says which worlds sell what is
  missing. Whether a hall should teach only what its own world can make is a decision about what an
  examination is for, not a wiring.
- **fixed (v1.0.14)** — *A reset was refused for ever once a character had done almost anything.*
  Reported from play as the refusal itself: *"has already left a mark the world keeps
  (world_history_events.related_user_id)"*. The first place a cultivator discovers writes a history
  row naming them, so the reset stopped working minutes into a life, and the refusal named a column
  and gave no way to tell a rule from a wait. On the owner's call a reset now **releases** every
  shipped anonymise column (`characterResetReleased`): private history goes with the life, public
  history and shared things stay with the name rewritten to an unknown cultivator, and a player
  family goes through `playerFamilyDepartTx`, the rule a founder walking out already had. Building it
  found the same family fault in `admin.player.erase` - the sweep deletes `characters` first and
  `player_families` cascades off it - fixed with the same call.
- **fixed (v1.0.13)** — *A GM could not see how many times a player had started over.* The record
  was never missing: one `event_log` row per `character.reset`, kept out of the reset's own sweep so
  the bound survives the action it bounds, carrying the abandoned life's name, path, root, realm and
  phase. It had no Python reader anywhere in the tree, no dashboard view and no `/admin` panel, and
  the only place `resets_used`/`resets_remaining` had ever appeared was the reply to the reset
  itself. `character.reset_status` is the one door; `/admin player inspect` and the dashboard's
  Player Editor ask it. See CLAUDE.md, "The allowance nobody could look up".
- **deferred (design)** — *A player still cannot see how many restarts they have left before spending one.*
  `/reset`'s confirm step is the generic red "Are you sure?" built from `_DANGER_ACTION_WORDS` and
  the command's description, so the count reaches them only in the reply to a reset they have
  already made. The engine read added in v1.0.13 would serve it (it is actor-agnostic and the
  player is entitled to their own number), but what a destructive confirmation should say is a
  decision about the warning rather than a wiring, and widening one unasked is what this file exists
  to refuse.
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
- **fixed (v1.0.9)** — *Standing at your own city's gate, the household refused you and named that
  city as somewhere else.* `familyHouseholdEnterAction` compared the character's location to the
  household's town by bare string equality while `cityOf` — the engine's one statement of which city
  a place is part of — was already read by travel and by `WhereAnNPCCanWalk`. 317 of the catalogue's
  477 locations are parts of a household town, 92 of them gates, and `beginner_home` reports
  `return_home` from this action, so the beginner path's last stage could not be finished by walking.
  Both halves fixed: the engine's check and the panel's anticipation of it.
- **fixed (v1.0.9)** — *A character three minutes old was shown every system the game has.* 249
  leaves across 67 pages in 16 hubs, none of them refused — sect politics, territory war, caravan
  dispatch and the auction floor beside `cultivate` and `talk`. `feature_unlocks` in
  `content/world.json` holds 139 of them for a realm that can use them, so a new cultivator meets
  110. It is a different rule from rc.32's (which hides only what the engine would refuse), and it
  earns that limit back three ways: a gated page prints one collapsed line and `/locked` lists every
  door with the realm it needs; the slash command still works, because gating is advertising and
  never a bound; and the roster is content a GM can retune.
- **fixed (v1.0.10)** — *The panel header named whoever content says lives here, while the picker
  named whoever is here.* Live: the header said *Gate Captain Yue Dong* and `/talk` offered
  *Drillmaster Zhai Kang*, whom the simulation had walked to that gate. `here_summary` read
  `WORLD.npcs[...]["location"]` — residents, with no schedule and no simulation — and it is
  synchronous, so it cannot know: who is standing somewhere is an engine round trip away. It takes
  `present` now and names people only when a caller hands them over; both production callers were
  already async and pass `npcs_present`'s answer, the same resolver rc.28 wrote and v1.0.8 pointed
  the picker at. This was the third reader of one question; rc.28 fixed the cards and v1.0.8 the
  picker.
- **deferred (design)** — *The curriculum is realm-banded, and some systems are not about realm.*
  The Ghost Cultivator's `ghost` page is path-specific, the hidden sect's door is a karma gate, and
  a profession's rank is its own ladder — each is left open at realm 0 rather than given a realm
  floor that would be wrong for the player it is actually for. Gating on something other than realm
  is a second axis, and a roster with two axes needs a rule about which wins before it is worth
  having.
- **fixed (v1.0.11)** — *The `#updates` channel no longer skips versions.* `releases_between`
  walks every changelog entry after the marker and up to the running version, oldest first, and the
  marker is written once at the end - **at the last release actually posted**, so a send that fails
  halfway does not make the ones it never reached look announced. Versions sort as integer tuples
  with a candidate below the release it is a candidate for (`1.0.0-rc.59 < 1.0.0 < 1.0.10`), and a
  gap longer than `MAX_ANNOUNCED_RELEASES` posts the newest of them under one line saying what is
  not being repeated. The three neighbouring behaviours this entry named as working are untouched.
  The gate this entry asked for exists, and writing it found that rc.59's own fixture stubbed
  `release_notes_for` with a lambda - so the parse was never driven from `announce_release_if_new`
  at all; it pins a temporary `VERSIONS.md` now. See CLAUDE.md, "A server is told what it missed".
- **fixed (v1.0.8)** — *The NPC picker offered whoever sorted first in the world, not whoever was
  standing here.* `local_npc_autocomplete` searched all 574 catalogue NPCs, took the alphabetically
  first twenty-five, and only then filtered by location — so `/talk`, `/npcinfo` and `/sense`
  answered *"nothing to choose from right now"* in a room whose own scene card named the gate
  captain in it. It asks `npcs_present` now, rc.28's one resolver, which is what `/scene status` and
  `/sense` ask. (**This entry first said "which is what the card has always used", and that was
  wrong about one card**: the *panel header* is `here_summary`, a pure catalogue read that asks
  neither the schedule nor the engine — see the v1.0.10 entry above.) It blocked more than conversation: the beginner path stalls at its second stage, which asks
  for a `talk`, and the commission ladder runs inside `/talk`, so 137 of the 140 authored
  commissions could be neither offered nor finished.
- **deferred (design)** — *An NPC nothing knows the location of is no longer offered anywhere.*
  `current_npc_location` answering `None` means "do not filter by location", and `/talk` honours
  that, so such an NPC used to appear in every room's picker and now appears in none. That is the
  safe side of rc.28's rule — never offering somebody `/talk` would allow costs discoverability,
  while offering somebody it refuses is the fault the rule exists for — and it is what makes the
  picker agree with the scene card exactly. Whether those NPCs should be reachable at all is a
  question about the registry, not about the picker.
- **fixed (v1.0.8)** — *A reset deleted the rows that name a player's private threads and left the
  threads standing.* Four tables hold the only record of a Discord thread the bot made for one
  player, and `character.reset` wiped all four — so an abandoned life's expedition journal stayed in
  Discord with its whole scene log and nothing anywhere able to find it again.
  `DB.all_managed_thread_ids` had stated the ordering since the world-wide reset was written and the
  per-player lever never applied it. Both levers collect the ids before the engine call and delete
  the threads after it succeeds; `admin.player.erase` had the same hole, where it was the difference
  between removing somebody from the database and removing them from the server.
- **fixed (v1.0.8)** — *The GM dashboard's status footer was painted only by the Overview.* Opening
  the dashboard on any other view left both lines on their literal placeholders, `SQLite` and
  `engine —`, for as long as the tab stayed open. Half the wire was already there: the boot path
  fetched `/api/overview` and used it to set the world clock and nothing else, three lines above the
  two spans made from the same response. Neither placeholder looks broken, which is why it went
  unreported — a GM could not tell `engine —` from an engine that had answered and had nothing to
  say. The shell paints itself from anywhere now and says when it cannot reach the engine.
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
  refusal, reporting which. Since v1.0.14 nothing the sweep leaves behind refuses a reset, so the
  success path is the one it should now see; the refusal is still accepted for an anonymise column a
  reset has not been told how to release. The success path is driven end to end by
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
- **fixed (v1.0.12)** — *The Discord sweep could not see the curriculum, and had not been run since
  v1.0.8.* v1.0.9 gave a page a third state - a door the curriculum has not introduced yet, which
  prints one collapsed line per page and no per-leaf lock line - and `press_leaf` knows two, so 97 of
  245 leaves were "neither drawn nor locked" and **100 of 345 steps went red**. The harness raises
  the player past the roster's own ceiling (read off `feature_unlocks`, never written down) before
  the sweep, so every leaf is pressed again, and asserts the curriculum first at realm 0 on the page
  the roster says holds the most back. `test_playtest_coverage.py` was green throughout because it
  only asked about the deferral set; it holds all three of those now. See CLAUDE.md, "The curriculum
  the sweep could not see".
- **fixed (v1.0.12)** — *Every GM lever addressed an id a float had rounded off.* `decodeMap` is
  `json.Unmarshal` into `map[string]any`, so a JSON number became a float64 - and a Discord snowflake
  (~1.4e18) exceeds what a float carries exactly (2^53), so `admin.player.set_realm` was handed
  1456074443989188610 and looked up ...608: "character not found", about a character the panel had
  just drawn. Player actions were never affected, because `ActionRequest.ActorID` is a typed field.
  `UseNumber()` is the fix and **no reader changed** - `storage.ParseInt` has had a `case
  json.Number` since it was written and nothing could ever produce one. Found by the playtest, not by
  reading. See CLAUDE.md, "An id too big for a float".
- **fixed (v1.0.12)** — *A hub panel went quiet after fifteen minutes, and the number was written
  out five times.* Asked for in play. `HUB_PANEL_IDLE_MINUTES` (`0` = never) replaces a bare
  `timeout=900` in five files; injected into `hubs.py` because the layering refuses a `runtime`
  import there. The default shipped at 120 and was put back to **15** in v1.0.13 on the owner's
  call: the Reopen card says fifteen, so a default that disagreed with it made the card a lie. The
  module default is the same fifteen, so a missed registration preserves behaviour rather than
  leaking a view per panel.
- **fixed (v1.0.12)** — *Four places parsed `surface.py` to recover the command tree's tuple, and a
  fifth wrote down how many there were.* The tuple was inline inside `register_command_surface`, so
  rc.43's "never copy it" could only be obeyed by reading the source - four ways, each with its own
  self-check - while `playtest_discord.py` asserted `9 + len(_HUB_COMMANDS)` and went stale on
  `/locked`. `surface.TREE_COMMANDS` is a name; all four are imports.
- **fixed (v1.0.11)** — *A GM can grant a physique, and can only set a root grade the ladder
  carries.* Two levers, one sentence: **the writer a human drives is the one nothing held.**
  `admin.player.set_physique` takes an optional `physique_id`, held to the catalogue the way
  `aptitude_actions.go:203` already holds one, and writes `name` beside it; the identity goes into
  the undo snapshot whether or not the call changes it, so an undo puts back what a grant replaced,
  and a pre-v1.0.11 snapshot keeps the three-number statement because an old audit row must stay
  undoable on the terms it was written. `admin.player.set_spiritual_root` reads
  `spiritual_root_system.grades` instead of the six-name map literal it kept beside them, and
  `dashboard/app.js` stopped keeping a third copy in `gradeOpts` - both cards are pickers fed from
  the content file by `_aptitude_catalogue`. The finding underneath is the fixture: `Apply` passes
  an **empty world path** and every admin test went through it, so a lever that refused everything
  would have passed all of them. See CLAUDE.md, "The writer a human drives is the one nothing held".
- **fixed (v1.0.11)** — *🗺️ Cultivation World is gated behind having played.* One generated role
  (`Xianxia • Cultivator`), granted at creation ahead of the first private thread, kept in step by
  `require_character`, revoked at `admin.player.erase` - the one moment nothing else can notice -
  and backfilled by `_sync_all_realm_access_roles`. The overwrite covers the category *and* both
  anchors, because a category overwrite reaches only a channel synced to it; it allows the bot
  before it denies `@everyone` (rc.52); and it **merges** rather than replaces, because
  `set_permissions(**perms)` would have taken `send_messages=False` off `@everyone` with it and
  left two read-only anchors writable to everybody holding the role. The name-family trap this
  entry called out is now `test_the_role_names_never_collide.py`. See CLAUDE.md, "A room for people
  who have played".
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
- **fixed (v1.0.13)** — *The sweep could not tell a question from a result.* Once the harness was
  raised past the curriculum's ceiling, `/battle challenge` **resolved** for the first time in its
  life and immediately failed with SimCord's *"That component is disabled - a real user could not
  interact with it"*. A resolved challenge posts a `BattleView` whose technique and recovery selects
  are `disabled=not available`, so a cultivator with no Law techniques and nothing to drink gets two
  dead pickers carrying one explanatory option each - and `answer_generically` walks an action's
  input steps by taking the first select on the **result's** message. The tell was in the payload all
  along, as `disabled`, and the sweep never read it. The skip is in `select_by_placeholder`, the one
  helper both answerers reach; a disabled **leaf button** is deliberately still a failure, because
  that one means the panel timed out. See CLAUDE.md, "A disabled control is not a question".
- **fixed (v1.0.13)** — *Two more copies of the fifteen minutes, and only one of them spelled.*
  v1.0.12 removed a bare `timeout=900` from five production files and its drill then found a sixth
  pinned as a string in `test_gui_integrity.py`; both are in that release. The seventh was not the
  number at all but an
  *encoding* of it - `advance_time(901)`, one second past a deadline stated somewhere else - so no
  search for `900` and no gate reading production for a literal could have found it, and raising the
  default to 120 minutes left the step moving a panel an eighth of the way to its deadline and then
  reporting that it would not expire. The harness pins the window in its own environment block now
  and the step reads it back, and both bounds on that number were measured rather than chosen:
  waiting the shipped two hours out costs minutes of woken periodic workers for no extra assurance,
  and a one-minute window never settles at all — a view timer that near counts as runnable, which is
  rc.35's finding met from the other side. `TheHarnessWaitsTheConfiguredWindowOut` holds it:
  `advance_time` may not take a numeric literal, and the harness must call `panel_timeout()`. An
  ordinary suite run then found the **eighth**: `test_playtest_gate.py` pinned the literal
  `advance_time(901)` as its marker that this loop is driven at all, so correcting the step turned
  that gate red — a check that pins how a rule is *written* failing exactly when the rule is fixed,
  which is v1.0.8's lesson, met for the fourth time in four releases — v1.0.9, v1.0.10 and v1.0.12
  each retired a hand-copy of the command tree's tuple for the same reason.
- **deferred (design)** — *The leaf sweep counts a leaf pressed into a designed refusal as covered.*
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
