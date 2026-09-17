# Xianxia RP Discord Bot — Version History

The changelog, one paragraph per minor. The per-release entries as they were written are in
`docs/history/CHANGELOG_0_18_TO_0_40.md`, and the earlier per-release notes (`V018_RELEASE_NOTES.md`
… `V023_RELEASE_NOTES.md`) beside it. `README.md` describes the current release.

## Changelog

**1.0.0** (rc.34) lets the head of the house teach and test the new cultivator.

The beginner path ended at the door: nobody in the house had ever spoken to the child as a teacher,
and a fresh cultivator could craft only in the household's own trade, because the engine refuses any
method they do not know and the other three trades were bought into from slips. The path now ends
with a fifth stage, "The Last Lesson": at home, `/family → Hearth → Lesson` is the head of the house
speaking, in words that are content, one entry per birth family. The test is one demonstration check
on the attribute the family's trade lives on, and a failure costs one world day and nothing else.
Passing qualifies the cultivator at level 0 in all four trades - a profession record in each, never
lowered, and every trade's entry methods - hands over the house's own manual studied once, so its
first technique is usable at once, tells the story of the house into its chronicle, leaves a keepsake,
and raises standing. Once per life, kept in the event log against the soul's incarnation count, so
samsara lets a new life take it again; and grandfathered at the door, so somebody who finished the
road home before this release is handed the stage the moment they ask. `family_lesson` is a new
objective type reported only on a pass; the wait shows on the cooldown card. No schema change.

**1.0.0** (rc.33) runs the half of the playtest a player touches.

`scripts/playtest_discord.py` boots the real bot inside SimCord, an in-memory Discord that runs
discord.py's own machinery, against a scratch engine, and drives it only through what a player sees:
the commands sync and every startup phase completes; `/admin` refuses a player and binds the eight
base channels through a picker and a modal; `/begin` goes family, path, sex, form to a cultivator
standing in a private household thread; every hub and `/menu` answer with a panel; `/family` inside
hides Enter and prints why, hands over an errand, and Leave opens the expedition journal; `$ I
explore` typed in the journal and the `x` shorthand from any channel dispatch; `/cooldowns` shows the
live wait; from the capital the door is locked and the Hearth-Return Talisman carries the player
home with the errand's `return_home` line after the reply; Support, Contribute, Enter from the town,
the road; a panel expires to Reopen and reopens; and the bot raised nothing. Thirty-one steps, one
line each, PASS or FAIL, beside the engine half. SimCord is a dev dependency only - nothing under
`app/` imports it and the hash-locked install never carries it - and the harness stays a script,
because the bot cannot boot without the Go engine that CI's python job does not have. Its first green
run found what no source read had: a modal opened from a panel was acknowledged with a "thinking"
placeholder, and the result was written into that placeholder as a second copy of the panel while the
real panel kept buttons no view owned - dead until reopened, after every Contribute. The panel is
edited directly now and the placeholder deleted. Two settings the harness has to raise are findings
too: typed play in a private thread listens only with `AUTO_NARRATE`, and the per-player action
meter is right for a person and wrong for a harness.

**1.0.0** (rc.32) gives a soul back its hands, and a household its reasons.

The craft echo: samsara used to wipe `profession_progress` before the new household tutored the new
life, so a past life's crafting left no trace while everything else the soul had done survived as an
echo. The trades a life practised now go into its past-life record ahead of the wipe (no schema
change), and a new life's craft and forage rolls carry an echo of the best of them, scaled by how much
of the soul's memory has woken and capped at +3. The rc.31 note that "the dao-family rebirth and the
samsara return keep every point they earned" was wrong on both counts and is corrected.

A house worth coming back to: the household's one handout could be claimed from anywhere and the door
was a free teleport. Now the door opens from the family's town, and support, the coffers
(`family.contribute` — the never-read `treasury_balance` finally does something), a second round of
teaching (`family.tutor`) and the household's errands (`family.errand`, twelve authored quests that end
at the door) are asked for inside. The family hall is a cultivation site and a seclusion site. The
panel now hides a door where it would refuse - the household's, and the game's later ones (laws,
tribulations, Perfection, sect rooms, abode keys, inner world, beasts, the house, the Samsara legacy). A
`return_home` objective, a fourth beginner stage, and two Apprentice-Inscription talismans — Hearth-Return
home from anywhere, Waymark back to where it found you — make the round trip. The hub hides each household
door where it would refuse.

**1.0.0** (rc.28) stops the content path costing what it cost.

Also here: the readiness probe was a sample, and the sample had gone stale. `operational_health`
exists to tell a healthy versioned database from the empty file SQLite will happily create if the
real one is removed or replaced while the bot is running, and it did that by checking a set of
tables - twenty-seven of them, written for v0.20.7 and never touched again. Twenty-nine releases
later it still named `catalog_manuals` and `catalog_techniques`, and did not name
`npc_civilization_state`, which is the table the entire simulation runs on, nor `inventory`,
`character_quests`, `battles`, or anything added since. It would not have noticed the simulation's
own table going missing.

It is exact now - every one of the 169 tables a fresh bootstrap makes - and a test holds it against
a real bootstrap rather than against another list, so adding a table without listing it fails there.
That is the part that matters: a sample cannot be kept honest, because nothing says which tables
belong in it. The cost is one set difference against `sqlite_master`, which the probe already reads.

This is also what the decision not to retire `catalog_manuals` and `catalog_techniques` above rests
on, and it rests on it less than it looked: those two are not load-bearing *because* they are in the
probe - they are in it by accident of when the set was written. What is real is `catalog_counts`
reporting their rows in the CATALOG_READY startup phase, and two rows in eighteen hundred not being
worth giving that up.

Nothing here changes a rule. All three are the same shape of problem: work on the hot path that
looks like a lookup and is not.

Asking who is standing somewhere was five hundred and seventy-four round trips to the engine. Every
surface that draws it - the `/action` target picker, `/scene status`, `/world`, a city's Look - walked
the whole NPC catalogue calling `npc.status` once per name, inside an await, so one after another.
There is now a single `npc.at_location` query, and one resolver, `npcs_present`, that all of them
use. It asks the engine once and then resolves only what the engine cannot answer for: a catalogue
NPC whose daily schedule puts them here and who has no simulation row yet, and a hidden master
walking a circuit, whose whereabouts are a pure function of the canonical clock.

That resolver keeps the same order of precedence the single-NPC lookup does - circuit, then the
simulation, then the schedule - because a picker that offers somebody and a command that then
refuses them is worse than either being wrong on its own. The new query is also deliberately narrow:
`npc.status` carries relationships, disciple bonds and a whole life row, and loading all of that for
everybody in a city in order to decide whether to print their name is what made the old shape slow
twice over.

`worlddata.Load` read and parsed two and a half megabytes of JSON on every call, with no cache, and
fifteen of its seventeen call sites are inside the request path in `authoritative.go` - so every
player action re-parsed the whole world, on the CPU-only hardware this project exists to run on. It
is keyed on the file's modification time and size now rather than on the path, so an operator
editing content on a live NAS still does not need a restart; size is in the key beside the timestamp
because some filesystems keep mtime at one-second resolution.

And boot spent about two thousand HTTP round trips rewriting content that had not changed since the
last boot. On the Go-backed path every `db.execute` is its own POST, and the catalogue sync made one
per row. They go in a single batch request now, through an endpoint that has existed on the
transport since the Go engine landed with nothing on this path using it.

One thing was *not* done, and the reason is worth recording. The plan had `catalog_manuals` and
`catalog_techniques` retired here as unread write amplification. They are unread for their
*content* - no getter exists and `search_catalog` is never called with either kind - but they are
not unread: both are in `OPERATIONAL_REQUIRED_TABLES`, which is the readiness probe that tells a
healthy versioned database from an empty file SQLite created at the same path, and `catalog_counts`
reports their row counts in the `CATALOG_READY` startup phase an operator watches. Two rows of every
eighteen hundred is not worth giving that up.

**1.0.0** (rc.27) lets the world keep the people it makes.

Two things had the same cause, and the cause was a missing table.

`npcChildbirth` has been writing `npc_descendants` rows since the life cycle was built, and every
one of them sets `generated_as_npc` to 0. Nothing has ever set it to 1 and nothing has ever read it
- it appears in the DDL and in two INSERTs that hardcode it. That is not an oversight, it is the
absence of anywhere to promote a child into: `catalog_npcs` is a mirror of `content/world.json` and
is rewritten from the file at every boot, so a runtime NPC written there is deleted by the next
restart, and `npc_civilization_state` says where somebody is and what they are doing and has no room
for who they are. So the world's own children were named, recorded and counted in their parents'
`children_count`, and then nothing: they never aged into anybody, never stood anywhere, could not be
spoken to, could not marry and could not have children of their own. A world producing people who
are permanently four years old is not reproducing, it is keeping a list.

The same gap was why a player could not talk to their own family. A starter household's relatives
live in `birth_family_npcs`, a third population that is neither the catalogue nor the simulation,
and `DB.get_npc_definition` tried the catalogue and then the cast of a running world event and
stopped. So `/family` printed those names under "Close relatives", the creation card sent every new
player straight to that command as one of the first three things to do, and `/talk` answered
"Unknown NPC." to every single one of them - a room with people in it the game would not let you
address. Their `personality` column is the literal "Member of a shared starter household" for every
relative in the game, and there are no speech, want or fear columns at all, so there was nothing to
say to a narrator even if one had been asked.

`npc_registry` (schema 49) is the missing half: authored state, written while the game runs, carried
in backups, and never touched by a rebuild from the content file. Keeping it out of the catalogue
mirror rather than adding an origin flag to it is the whole safety property - a rebuild is an
unconditional DELETE over the derived table, and this one is never named in that statement, so no
wrong predicate can wipe the people the world made for itself. A name the content file already
carries is never taken; the catalogue wins, because two people answering to one name is worse than a
birth refused.

At eighteen - the same age a played character starts at, because a child of this world should not
come of age on a different clock from a child of it who happens to be played - a descendant becomes
a person: prose from `npc_generated_traits`, a registry row so `/talk` can find them, and rows in
the two simulation tables so every tick that reads `WHERE status='alive'` starts offering them. From
that minute they are an ordinary NPC. The courtship can court them, the deeds step can send them
out, and they will eventually be buried. A child whose parents are both gone is left alone rather
than given an invented town to grow up in; a later tick may find a household again.

The prose is content and deliberately small pools, because a hundred bland variations read worse
than a dozen written ones. It is picked by `hash64` of the name, field by field rather than one
index across all five, so the same world makes the same person twice and nobody's fear is welded to
their personality forever.

And `narrator.py` stopped crashing on anybody it did not recognise. Its NPC dialogue prompt opened
with a bare subscript into the parsed content file and then six more for role, realm, personality,
speech, want and fear - while the gate upstream resolves through `DB.get_npc_definition`, which has
fallen back to a running event's cast since schema 43. So an event's militia captain passed the gate
and raised `KeyError`, and `/talk`'s `except Exception` turned it into "the narrator service failed
to answer." `NarratorContextBuilder` had always done this correctly; the two classes had simply
drifted apart. `/sense` had drifted the same way, refusing with "Unknown NPC." anybody its own
picker was offering.

**1.0.0** (rc.26) gives a new cultivator something to do.

`first_steps` - "First Steps Beneath Heaven" - has been in this repo since before the Quest Forge,
with exactly the right three objectives, and no player has ever held it. It is seeded into
`quest_definitions` on every boot and it is listed in `/quests`. What never existed is any path that
hands it to somebody: the only two statements in the engine that write a `character_quests` row are
both in `commission_actions.go` and both want a giver, which the static quests deliberately do not
have. So a new player was never short of a quest - they were short of being given one, and `/quests`
is one of sixteen equally-weighted hubs with nothing saying it is theirs today.

`beginner_path` in `content/world.json` is three stages, and the engine hands the first one over in
the same transaction that makes the character, beside the send-off the household puts in their
hands. Each stage hands over the next as it completes, so a player is never between them. There is
deliberately no second quest mechanism: a stage is an ordinary `quest_definitions` row seeded the
way the authored commission pool is, and once handed over it is pinned, progressed, completed and
paid by the code every other quest uses. Only who gives it to you is new.

The stages are shaped by where the player actually is, which is the part that took the most care. A
character is created *inside* their birth-family household, and the engine refuses exploring,
travelling and hunting in a private residence - so a first stage asking for any of those is
unfinishable until the player works out that `/family` and its Leave action exist, which is the
problem the path is meant to solve rather than one it may cause. Stage one is therefore a session
and one scene action, both of which work indoors; stage two is the street, and it is the stage that
names how to get out of the house; stage three is the road, the trade the household taught, and one
fight. The test that holds this reads the refusals out of the Go source rather than trusting a list,
so a new gate in the engine fails a test instead of stranding somebody.

Three refusals are worth stating because they are what stops this going wrong quietly. A definition
that has not seeded yet costs that player their quest and never the character they were making. A
quest with a giver is never handed over, whatever a chain says - a commission takes the
one-at-a-time slot and carries a deadline, and it is offered in person or not at all. And the chain
is read off `quest_definitions.seed_json` rather than off the content file, so a GM who re-points it
in the workbench is obeyed and the shipped file is only ever the starting shape.

A quest advancing also stopped being a dead end. It used to say "Quest progress: <title>" and
nothing else - it confirmed something counted and left the player exactly where they were. It now
names the first objective still outstanding, and because every label is written as a hub path, that
line names a real command; inside a hub panel `suggested_actions` turns it into the button. The
creation card names the stage and its steps too, so the answer to "which door, now" arrives before
the first one is even asked for.

**1.0.0** (rc.25) lets a quest ask for more than five things.

`OBJECTIVE_TYPES` is the ceiling on every quest in this game - the two static ones, every
commission, and anything the Quest Forge drafts - because `quest.progress` only advances an
objective whose type matches an event somebody reported. It held five: explore, talk, scene action,
and the two sect steps. That was honest rather than stale, which is the part worth saying: there
were exactly five `QUESTS.progress(...)` calls in the whole bot and they were those five.

So cultivation, combat, crafting, travel, the shops and the hills were invisible to the quest
system. Nobody could be asked to sit a session, win a fight, make something from a method they knew,
walk to a place, buy something from a keeper or pick a herb out of a hillside - not by a GM's
commission, not by the Forge, not by anything. `cultivate`, `travel`, `combat_win`, `craft`, `trade`
and `gather` close that, and the engine needed no change at all to take them: `progressQuest`
matches an objective's type against no whitelist, so what a new type actually costs is a reporter.
Each is one line at the command that already does the work, written after the authoritative action
has succeeded - the engine decides that something happened and the reporter only says so.

Some of the care is in what does *not* report. A failed refinement spends the ingredients and is a
real part of the trade, but it does not satisfy "craft a Recovery Pill". A replayed combat finalize
is not a second victory, because the engine did not apply its consequence twice. A travel objective
is credited against where you actually ended up rather than where you asked for, since a road
journey that stops at the gate would otherwise never satisfy an objective naming the city. And
buying ten herbs is one visit to one keeper, not ten trades.

`combat_win` is untargeted on purpose: an opponent may be a catalogue NPC, an event manifestation or
a beast off the hunt roster, and only the first of those is in `world.npcs`, so there is no roster a
draft could be held to. The reports carry the opponent's name regardless, which an untargeted
objective accepts and a future targeted one could use.

`craft` and `gather`/`trade` name a recipe and an item, so the Forge's validator and the dashboard's
"points at something the world does not have" audit both learned those two kinds. An item objective
stores the id the reporter will send and prints the name a player would recognise, so nobody reads
"Gather spirit_herb". And `test_quest_objective_reporters.py` holds the whole thing shut in both
directions: a type with no reporter is a quest nobody can finish, and a reporter naming a type the
vocabulary lacks is a report going nowhere.

**1.0.0** (rc.24) lets the world's own people marry each other, bear children, and be lost.

Four weddings a month in a world of five hundred and seventy-four people, and less than one child
anywhere in the first month of it. Both had the same cause. `npcLife` walked one globally sorted
list of singles two at a time - slots (0,1), (2,3), (4,5) - and kept a pair only if both happened to
land on the same `current_location`. Measured against this catalogue that is fifteen usable pairs
out of the two hundred and forty that exist, and six percent of fifteen is 0.9 weddings a tick. The
deeper reason is geography: four hundred and seventy-seven places hold those people and three
hundred and ninety-two are the only person standing where they stand, so under a rule that both must
be on the same tile those three hundred and ninety-two could never marry anybody, ever. Counting a
whole city instead - which `game.WhereAnNPCCanWalk` already models, district to city and back out -
drops that number to fifty-six.

**Courtship.** A pair within reach of each other gain ground each tick and marry at seventy; a pair
the roads have separated cool and part. Realm and age have to agree *proportionally*, because sixty
years is a lifetime to a mortal and a rounding error to a Nascent Soul elder. Nobody courts their
own kin, and nobody courts somebody they hold a grudge against - which only became checkable at all
once rc.23 gave this engine something that raises that column. There is deliberately no gender rule:
not one of the 574 catalogue NPCs carries a gender field, so a rule would be inventing content
rather than reading it. Two sects on speaking terms and short of an alliance also marry their
weightiest unattached members to each other, which is the first thing that has ever *made* a
`marriage_pact` rather than describing one at bootstrap.

**A world with a past.** Bootstrap wrote all 574 of them as single, childless and nowhere near the
end of a life, so a new world was 574 strangers and the first funeral was decades away. It now opens
with 88 households, 176 people married, 104 children and 29 people near the end of the span their
realm allows - all keyed off `hash64` like the rest of bootstrap, so the same content makes the same
world twice.

**Widows.** Nothing had ever set `relationship_status` back from 'married': the only two writes to
that column marked people married. A widow stayed married to a corpse for the rest of her life,
could never be courted again, and went on bearing his children, because `npcChildbirth` read the
column and never checked the spouse's pulse. `ReleaseNPCBondsTx` now runs on all three death paths.
A widow may marry again, and it is harder in both of the ways that matter: a mourning period first,
and afterwards a colder roll.

**Disappearances** (schema 47) and **graves** (schema 48). Somebody away from home can vanish.
`status` carries 'missing' beside 'alive' and 'dead', so every batch that reads `WHERE
status='alive'` stops offering them by construction rather than by a rule written four more times.
They cannot free themselves - if they wandered home the quest would be decoration - but the
surroundings feed them for sixty days before it starts costing them. The point of the feature is the
significance: `forge_quests_from_history` drafts a quest per public history row at or above 80, and
this whole package topped out at 74, so no autonomous event had ever been able to reach the Quest
Forge at all. A disappearance is written at 82 and is the first one that can. At a hundred and
twenty days the town gives up and the marriage is dissolved from both sides - the person is *not*
marked dead, and is still out there and still findable, which is the whole of the tragedy and none
of the bug. If nobody comes, a grave holds where they stopped and what they were carrying: their own
purse, and one thing read off the trade they practised. The first searcher to reach it takes it.

**And somebody else may.** The Tomb-Watch Clan has listed "Grave-robbers" among its troubles since
the birth families were written, and `npcFindChance` has always given the best find rate in the game
to a digging trade - but their finds were abstract, drawn from a catalogue pool, because there was
nothing in the world to dig up. There is now. A grave keeps a grace of twenty-one days, three ticks,
in which it is the searcher's alone; past that a digger within reach may turn it over, so arriving
late stops being the same as arriving. The deed is `hidden` and the goods are not: nobody was out
there to see it done, so the world genuinely does not know, but the keepsake goes under the hammer
at the nearest house and `auctions` is the one fence of the two that records who brought it in. A
player who reaches an emptied grave and later finds the dead herbalist's satchel listed under a
known digger's name has worked it out from the world rather than been told.

Also here: `npcLife`'s bulk heal was `WHERE health>0` with no join to status, so it healed anybody
whose death had been written in `npc_civilization_state` and nowhere else. The city rumour teller
looked for a `lower` district that four of the forty-eight cities have, and inside it a name
beginning "Innkeeper" - which no NPC in the catalogue has; the `inn` district is in all forty-eight
and every one has its landlady standing in it, so 4/48 becomes 48/48. The dead stopped being offered
in every picker at every location. And two f-strings used syntax only legal from Python 3.12, so
`python -m compileall app` - one of this repo's own checks - could not run on 3.11.

**1.0.0** (rc.23) gives the world's own people something to do when nobody has told them to. The
simulation already gave them births, marriages, careers, breakthroughs, masters and feuds, and
`npc_consignments` already had a grave-robber or a herb-gatherer turn something up and put it under
the hammer. Between those the world was law-abiding by omission: `crime_records` is keyed to
`characters` and always has been, so the only crime this world could record was a player's, and the
only way an NPC's trade ever showed was a lot appearing on a floor. A hunter and a bandit lived
exactly the same life.

**A bandit robs somebody.** `npc_deeds.go` runs after the feuds in the `npc_life` batch - after,
because a robbery is what starts the grudge a feud later settles, and one tick should not do both
ends of that. A criminal trade needs no excuse; anyone else needs ambition at 70 and fifteen coins
to their name, which is the difference between a thief and a desperate man. The victim is the
richest person standing in the same place who has more than the thief does, so nobody is ever robbed
of what they do not have. Most of it is theft, some is smuggling, a few turn violent and a fifth of
those end in a death - and the money that moves is the victim's, not new money.

**And the visibility ladder does the work a crime table would have done.** A crime somebody saw is a
`public` row: the town talks about it, the narrator's RAG can surface it, and the victim holds a
grudge against a *name*, which `npcFeuds` will eventually settle. A crime nobody saw is `hidden`,
which by the rule this repo already keeps never reaches narrator RAG at all - so the world genuinely
does not know who did it rather than politely pretending not to. Whether it was seen is the size of
the crowd: two people on an empty road is fifteen percent, a busy capital is most of the time. A
killing is the one thing always found, because a body is; whether it is attached to a name is the
same roll as anything else, and the summary is what says which.

**A hunter goes out.** The world's hunters draw on the same roster `/hunt` does - `RollHuntQuarry`
is that roster exported, because a second list kept in the simulation package would be a second set
of animals and the point is that these are the same woods. What they bring down goes to the nearest
floor, which is where their finds already went; what brings *them* down leaves the injury on
`npc_life_state`, and occasionally the cause of death. Death needs a margin of ten or worse, which
is deliberately out of a competent hunter's reach: at the first draft's one-in-seventy per outing
the trade emptied itself of everyone who practised it inside a season, which is not a living world,
it is a cull.

Everything here writes a column or a table that already existed - wealth and activity on
`npc_civilization_state`, health and injury on `npc_life_state`, grudges in `npc_social_relations`,
contraband in `black_market_stock`, lots in `auctions`, the record in `world_history_events`. **No
NPC gets a crime record**, because that table is the player's: a row in it would mean a bounty
nobody can collect and a capture nothing can perform. Making NPC crime *prosecutable* is a schema
change and a separate decision; making it real is not, and this is real - the money moves, the
grudge is held, the contraband is on the night market, and the hunter does not always come home.

**The dice can be borrowed by a test now.** Two tests in the simulation package asserted that a
low-probability thing eventually happened - a sect declaring war on a 12% roll, a grave-robber
turning something up on a 22% one - against `crypto/rand`, and so failed for no reason about one run
in two thousand and one in fifty respectively. More iterations only make that number small; it never
reaches zero, and a test that fails for no reason is worse than no test because it teaches the next
person to re-run CI instead of reading it. `gamerng.UseRoller` lends the dice to one test and hands
back the restore; the roller is given the bound it was called with, so a test can answer a 1-in-100
chance differently from a pick out of a list, and its answer is clamped into the die so no test can
roll something impossible. Production is untouched - there is still no seed, and a test in `gamerng`
walks every non-test file in the engine to prove nothing outside a `_test.go` ever calls it.

Both tests are better for it rather than merely quieter: the grave-robber one now pins that thirty
finders produce *exactly* `findCap` lots instead of "not zero", and the sect-war one runs a single
week instead of rolling sixty and hoping.

Also in this release: the shorthand's abbreviation rule ate one word too few. A group with a single
leaf abbreviates to that leaf, and the leaf's own word was left sitting in the line - `x prof status`
ran `/profession status` and then offered "status" to its first parameter. Both such groups take no
parameters today so the word was dropped harmlessly; it is consumed properly now, before the next
one is less lucky. And the rc.16 entry above claimed a shorthand line in a chat channel "returns
before the first database read", which was never true of `on_message` - it is corrected in place
rather than quietly left. No schema change.

**1.0.0** (rc.22) fixes a door that opened onto nothing. Since v0.39.0 the world tick has rotated
the secret realms - one every three game days, at its own entrance, so a realm at a ruin nobody walks
to still comes round. It wrote the `world_events` row, it wrote the public history row the rumours
carry, and it told the caller *how many realms it had opened*. A count is not something a bot can
announce. Every other path that opens a realm - `/explore` rolling one, `/admin world spawnrealm` -
spawns the Discord scene thread beside it; the rotation had no Python caller at all, so
`/admin world events` listed a live realm reading `Thread: none` and there was no way in. The
entrance was open for eight hours in a world where nobody could be told it existed.

`RotateSecretRealms` now hands back the realm it opened - key, name, description, entrance, closing
time - and the maintenance pass carries it out on the run as a spawned event, which is the channel
the autonomous world events have always used to reach Discord. The bot already spawns one scene per
event of every run, so the realm arrives with a thread, an announcement and the event panel.

`SpawnedWorldEvent` gains an `event_type`, and that is the second half of the fix: the bot named
every event it spawned a `random_event`, which is the type the expiry worker reads to decide whether
a closing scene says "Secret realm closed" or "World event closed", and which the scene panel reads
to decide what it draws. A realm flattened to a random event closed under the wrong words. The type
the engine gave the row now rides through to the thread.

Fixed beside it: `/spatialkey` had the same hole from the other end - the key consumed itself, the
engine opened the realm, and the player was told an entrance existed with no scene to enter it
through. It spawns its thread now, like every other opener.

**And the panels come back after a reboot.** A scene thread is a Discord message that outlives the
process that sent it; a `discord.ui.View` is not. `EventSceneView` carried a timeout and no
`custom_id`, which is exactly the pair discord.py refuses to register for persistent listening, so
every restart left every open event with dead controls - the thread still there, the realm open for
six more hours, and every button answering "This interaction failed" until it expired. The same was
true of `ExplorationEventView`, where it was worse: the encounter *pauses* exploration, so a player
whose panel died had no way to resolve it and no command that would redraw it. They could only wait
the event out.

Both are persistent now - no timeout, and a `custom_id` on every component that is stable across
processes and unique per event (a digest of the event key, which keeps it inside Discord's
hundred-character ceiling however long a key gets; the exploration ids carry the owner too, so two
cultivators in one encounter do not share controls). The closing time still governs play: the panel
refuses an event that has expired and the tick still archives the thread, so nothing is lost by
letting the view outlive the process.

What makes it "every system" rather than "the two somebody remembered" is `VIEW_RESTORERS`, a
registry beside `ACTIONS` and `EVENT_HANDLERS`. Each family registers its own restorer, startup
walks them all in one step (`PANELS_RESTORED`), and one that fails never costs the others or the
boot. Deliberately *not* in it: the hubs, pickers and confirms. Those time out inside fifteen
minutes and are re-opened by running the command again - registering them would leave live-looking
buttons on messages whose moment has passed, which is the opposite of the fix.

Two new reads on the Python side (`get_live_event_threads`, `get_live_exploration_events`), both
mirrors of sweeps that already existed. No schema change.

**1.0.0** (rc.21) closes the last line of the profession audit, the one filed under "worth deciding
separately" and left through two releases. `array_disk_blank`, `spirit_ink` and `talisman_paper`
were named by items, by twelve recipes, by shops and by merchants, and by no gathering path at all -
no event-site node, no secret-realm treasure, no forage table. So a player could forage their way
into an alchemist's career and had to buy their way into an inscriber's, and rc.20 sharpened it:
Formation now asked forty-four stones for the entry slip before the first gram of material.

**Foraging finds them.** `forage_materials` is the roster and it is content, the way `event_sites` is
- `talisman_paper` at 22% above 35 regional spirit resources, `spirit_ink` at 18% above 45,
`array_disk_blank` at 8% above 60, the gradient following what each thing actually is: bark and fibre
is commoner than a prepared substrate. Richness and the forager's own level both improve the odds,
under the same 65% cap the rare pool already used, so no single find becomes reliable and the
profession gains a reason to be levelled by somebody who is not an alchemist. The roster is walked in
sorted key order rather than by ranging the map, because a Go map range is randomised and an unsorted
loop would spend the RNG differently every call and be unpinnable by a test.

**Which also fixes Foraging.** It was a herb feeder wearing a general name: filed under `/craft →
Alchemy → Forage`, described as "Gather medicinal herbs", reporting a "Medicinal Forage", and
training a profession called Foraging that fed one craft out of four. The wording is honest now and
the finds are named in their own line, because a forager who does not know that ink and paper come
out of the hills has no reason to look. The command path and the `alchemy_forage` cooldown key are
deliberately unchanged - renaming the door is a breaking surface change that buys nothing.

**And two gates, because the block could so easily have shipped dead.**
`EveryCraftCanBeGatheredIntoTests` asks of each craft whether it has one entry method every material
of which has a source that is not a shop counter, and names the missing materials when it does not -
dropping the roster makes it say, in as many words, that Formation can only be bought into.
`ForageMaterialsAreReadTests` asserts the engine iterates the roster and reports what it finds; a
roster in content that no action reads would be the `authenticity` and `tracking_strength` shape a
fifth time. Both mutation-verified. Four Go tests cover the behaviour, including the one that had to
zero the fixture's attributes to see a failed forage at all - a cultivator with 100 in everything
never misses, and a test that can only pass by never running is the same fault in miniature. No
schema change.

**1.0.0** (rc.20) is the one finding rc.19 wrote down and deliberately left: the learning step.
`/craft`'s own description promised a dish made "from a known recipe" and nothing in the game knew
anything - every one of the thirty-one methods was workable by every cultivator from the minute they
were born, materials permitting, and a profession row sprang into existence the first time its verb
was used. That is the whole of what a craft was: do you have the herbs.

**A method is two things now, and they are asked separately.** Whether you were ever taught it, and
whether your hands are good enough for it - and the refusals are worded differently on purpose,
because a player told the wrong one goes looking in the wrong place. "You do not know the method for
X; it is carried on a jade slip" sends them to a shop. "X asks for Alchemy 3 and you are 1" sends
them back to the bench. Every recipe carries a `min_level` derived from the target number it always
had (`(TN-14)/4 + 1`, clamped to 0-4), so the ladders that were already spaced are now gated at the
spacing they were already written with: seven entry methods anyone can work, and four at the top that
want a master.

**Knowledge is a table, not a flag.** `character_recipes` is the third of its family after
`character_manuals` and `character_item_appraisals` - the same shape, the same per-character
composite key, and the same incarnation wipe, because what you learned is not what you are. A method
reaches it by two roads. The first is a **jade slip**: thirty-one items, one per recipe, priced at
twice the base price of the thing they teach, sold by the shop kind that already trades that
profession's goods in that world, and **spent by the reading**: the jade holds one impression of a
method and goes blank as it is taken, so a method reaches a second cultivator only by a second slip.
That is what keeps a shop's stock worth buying and a rare method worth guarding. The one exception is
the one that would make `/learn` a trap - reading a method you already carry teaches nothing and so
costs nothing, and the slip stays in the bags for somebody who needs it. `/learn` reads one, reports
whether the hands are ready, and says so without refusing the lesson.

**The second road is the family you were born to.** A household that has a trade teaches it at the
send-off, which is where the deadlock was: crafting is the only thing that raises a crafting
profession, so a family that taught only its own high method would hand a child a craft they could
never reach the bottom of. So what is taught is every entry method of the family's trade *plus* the
lowest method of that trade in the household's own world - which is what makes it right under
samsara. Reincarnate into a Celestial forging house and you are taught the Mortal fundamentals you
can actually work and the starsteel method you cannot yet, rather than a Mortal smith's education in
a world that stopped using it. Thirteen households, each mapped to one craft by what they already
are: five forging, three inscription, three formation, two alchemy. The teaching happens ahead of
both of `grantBirthFamilySendoffTx`'s early returns, so a second life in the same household is
taught again rather than silently skipped.

**And nobody loses anything.** Every character alive when this lands knew nothing at all, so the
gate would have arrived as a takeaway on all of them at once. Migration 46 credits each of them with
the entry methods and with everything their profession level already reached - an Alchemy 3 keeps
every pill an Alchemy 3 could work the day before and gains nothing they could not - and two drills
in `test_migration_drill.py` hold that, one for a mid-career crafter and one for a cultivator who
never crafted at all. The live database dates from v0.29, so this is every cultivator who has ever
existed. Schema 46.

**1.0.0** (rc.19) makes the eight professions good, which four of them were not. `VERSIONS.md` set
the bar when rc.15 repaired Appraisal and Inscription - a live profession has a recipe, a call that
grants it and a check that rolls it - and this is that audit run across all eight, plus the question
rc.15 did not ask: given a profession is alive, is it worth practising. The findings are kept in
`docs/PROFESSIONS_AUDIT.md`, because four of the seven were invisible to a gate that existed
specifically to catch them.

**Appraisal had no content at all.** It was the only profession missing from
`patron_gift.by_profession`, so a cultivator who practised nothing else fell through to the path
table and silently got a different gift - and the gate written to catch exactly that hardcoded a set
of three whose comment miscounted both halves, "the three that recipes name" (four do) and "the three
the engine hardcodes" (four are, since rc.15 made Appraisal the fourth). It has a gift now, a beast
core, the one material every tier trades, which suits the profession whose trade is value itself. The
set is *derived*: two scanners read the grant sites and the level reads out of `go_core` and resolve
both bare literals and named constants, with a companion test asserting they still resolve both
shapes, because a scanner that matches nothing passes forever. A reading that misses now teaches 3
against a hit's 12 rather than 5 - finding the right information is what moves the profession.

**Two professions were written by six call sites and read by no rule.** `Beast Taming` and
`Artifact Refining` accumulated levels that changed nothing: every read of the row went into the
response payload, so the number was printed on the card and consulted by nobody, and a Grandmaster
tamed no better than a first-timer. That is the `authenticity` and `tracking_strength` shape a third
time. Taming had a roll already so the level joins it, and the training gain with it; Artifact
Refining had no roll at all, so the level moves the one number the action does produce - the
resonance each bonding gains, which is what awakening gates on, so a refiner who knows the work
reaches an awakened artifact sooner rather than a different one. An unpractised cultivator is
affected not at all, which is pinned.

**Formation was hollowed out by rc.15's own fix.** Moving six talismans to Inscription was right;
nothing refilled what it emptied, leaving two recipes at the same TN and nothing above the Mortal
World, while eight array workshops stood in all four worlds with only Mortal goods to trade. It has
six recipes spanning TN 12 to 26 now, one tier material per world, mirroring the Inscription ladder
it was measured against - and each new disk carries a deployment definition, because an item with
`array_deploy` and no entry in that map is refused at deploy time, which would have been the same
fault one layer down. A gate holds both directions of that.

**And the smaller ones.** `formation_bonus` was a stat nothing could grant, because `"formation"` was
missing from the `abode.focus` map while being a perfectly valid facility - so building and focusing a
formation workshop succeeded and applied nothing, and Formation and Inscription crafts sat
structurally at effect_bonus 0 while their peers got +2. `manor_craft_bonus` omitted the
`inscription` branch the Go authority had carried since rc.15, under a Go comment citing that file by
line number. Thirteen crafted goods had no buyer anywhere - nine of the eleven pills, one talisman,
and every armour above tier 1 while every blade sold - though all thirteen were already on sale
somewhere; they are bought back now at the quarter of base price the median of all 748 existing entries
already paid. Foraging yielded `spirit_herb` in all four worlds and now resolves through
`EventSites.Material`, so a Celestial forager stops gathering Mortal weeds.

The roster gate is the part that matters longest. `PROFESSIONS` is imported by no production module,
and the three checks around it could not have caught rc.15's fault: one iterated a four-name tuple
written inline under a docstring promising "every declared profession", one asserted three strings
are members of a tuple, and one passes vacuously if every talisman is deleted. There is now one that
iterates the roster itself and asks whether each profession is granted, whether its level changes
anything, and whether the content knows it exists. Mutation-verified against all three faults. No
schema change.

**1.0.0** (rc.18) is the three faults rc.15's sweep reported and did not fix, and the one its own
manifest shipped. Each is the same shape: a column, a branch or a table that the code was written
around and nothing ever reached.

**The manifest could not verify itself.** rc.16 shipped `RELEASE_MANIFEST.sha256` with a `.git` line
in it, because the tree it was generated from was a git worktree, where `.git` is a one-line file
rather than a directory. Nothing was wrong with the release - every other hash was correct - but
`--verify` walks the tree it is checking, and in an ordinary clone `.git` is a directory and so is
skipped, leaving an entry for a path the walker never yields. That reads as `MISSING .git` and fails
the gate on every normal checkout, CI's included, which is where it was found: the one check whose
whole job is to tell a corrupt package from a sound one, refusing a sound one. The exclusions are
matched against every part of a path now rather than only its parents, so an excluded name is
excluded whichever it is.

**The world sends its own caravans.** `caravans.owner_type` has defaulted to `'npc'` since the table
was built and the resolver has always had a branch reading `if owner_type == "player"` - the shape of
code that expects a second kind of owner. There was never a second kind: the one production writer
hardcodes `'player'`, so the default was unreachable, the guard guarded nothing, and every road in
the four worlds was empty of trade unless a player put something on it. The travelling merchants are
the natural senders - they already have a home, a route, a purse and a shelf of wares - so a merchant
sitting out its dwell at a stop now sends a load ahead to the next one, planned by the same road
planner a player's caravan uses, written as the same three rows, and settled by the same resolver.
An arrival pays the sender, which meant the empty half of that branch had to be filled: a merchant's
takings go to its trading purse and to the wealth the world reads, the way `payAuctionSeller` already
paid an NPC consignor. One load per merchant at a time, four a tick, and a numeric `owner_key` is
never written, so nothing a merchant sends can surface in a player's `/caravans`.

**A sect with no players stocks its own storehouse.** `sect_treasury` is the shop a disciple spends
contribution points at, and its only writer was `sect.contribute` - a player walking in with
something in their bags. So the treasury of a sect no player had joined was empty when the world was
made and empty a century later, and `sect_system.resource_policy` ("contribution points can be
exchanged for stocked sect resources") described nothing that happened. The sects have their own
people, and since rc.15 those rolls change every tick; they are the ones who hand things in now, at a
rate their own numbers set, capped so a storehouse stays one. What they bring is content
(`sect_system.tribute`), resolved against the tier of the world the sect's gate stands in through the
same `EventSites.Material` the world events and the birth-family send-off use, so one three-line list
is right for an Azure Cloud outer disciple and a Celestial Mandate Academy elder alike. A hidden sect
takes none: its disciples have no counter to hand things in at. The cap refuses the next delivery and
never takes anything back, so nothing a player contributed can be lost to a tick.

**And what you carry can be followed.** `item_provenance.tracking_strength` is the sibling of
`authenticity`, and had the same fault in the other direction: five writers set it with care - an
underworld broker's goods at the post's own heat, a hidden sect's grant at seventy, a caravan's cargo
at five or twenty depending on whether it is being smuggled - and nothing read it, so `/provenance`
printed a number that decided nothing and a cultivator wearing a branded relic out of a night market
was no easier to find than one carrying nothing. `bounty_hunter_pursuits` was sitting right there. The
trail is the strongest mark on anything still carried *or worn* - worn matters, because
`equipment.bind` takes an item out of the inventory and a trail read off the bags alone would go cold
the moment its owner put the thing on - and it does two things and no more: a hunter closes faster,
and shaking one costs more. Never all of an escape, because a fugitive who cannot run is a cutscene.
An honest cultivator is pursued at exactly the rate this repo already had, since every honest writer
passes zero. The lever is the obvious one and it is real: the trail is read off what is held, not off
the provenance rows, which are never deleted - so selling the relic, or leaving it in a storehouse,
cools it. `/hunter act` says in words what is giving you away, because a rule the player cannot see is
the fault this one was written to fix. No schema change.

**1.0.0** (rc.17) gives a small server a way to be found, and a cultivator a way to see what they are
waiting on. `/vote` prints the server's page on whichever listing site the operator set
(`VOTE_SITE_URL` / `VOTE_SITE_NAME` - Top.gg, DISBOARD, whichever) and offers one claim every twelve
hours, the cadence every listing site resets a vote on. The gift is sized to the cultivator rather
than flat: fifteen of the local world's low-grade currency and five more for every realm climbed
inside that world - fifteen to fifty, and never less than the flat fifteen everybody used to get -
and with it one material the character actually uses, a herb for an alchemist, ore for a smith or a
sword cultivator, a beast core for a tamer, profession before path and resolved against the tier of
the world they stand in through the same `EventSites.Material` the world events use. The mapping is
content (`patron_gift` in `content/world.json`), so a new path needs no engine change. `@herb` and
`@ore` tier by name; `@core` deliberately does not, because beast_core is the same item in all four
worlds and nineteen recipes and nineteen shops across every tier - the Celestial tier-4 and tier-5
floors included - still trade in it, so forking it into tiered cores would hand a Celestial tamer an
item no recipe accepts and no shop buys. It tiers by number instead: one core in the Mortal World,
four in the Celestial, the herb's own 2-to-20 ladder in another shape. Which refs work that way is
content too (`patron_gift.untiered`), and the content gate checks it against the tier table in both
directions.

Nothing verifies the vote, and that is a decision rather than an omission. Verification means an
inbound webhook, which means publishing an endpoint from a box that publishes nothing - a door opened
for a thank-you, one release after Hardened I closed the last ones. So the claim is taken on trust
and the *cadence* is the engine's: someone who claims without voting is thanked no more often than
someone who votes, and the gift is small against what playing pays. From Friday 00:00 to Sunday 23:59
the whole of it doubles, in a named zone (Europe/Amsterdam) rather than a fixed offset so the window
opens at local midnight in December as well as June - one pure function over an injected time, pinned
at eight instants either side of the daylight change, with the game package blank-importing
`time/tzdata` because neither the engine image nor CI carries a timezone database. A worker announces
the window once in the server's existing announcement channel when it opens and once when it closes,
marked by the window's own key in `channel_messages`, so a restart mid-weekend cannot post twice and
a first tick on a quiet Tuesday cannot announce the end of a weekend nobody heard about.

`/cooldowns` is the aggregate view this repo never had. The engine meters two dozen waits plus half a
dozen that are not cooldown rows at all - a road journey, closed-door seclusion, the sect trial's
retry, a realm seal, the Samsara wait, a GM mute - and the only way to find one was to try the action
and read the refusal. One query (`cooldown.status`) puts three clocks on one axis: wall-clock rows,
game-minute waits converted through the world clock, and each wall-clock column, emitted as
structured rows that Python turns into words. The case the conversion cannot cover is reported rather
than faked - a world the GM has stopped has no real moment of arrival, so the card says so instead of
counting down to 1970. Under the waits is everything *ready*, each naming the hub page that runs it
and filtered so it is not noise: no ghost road unless the cultivator walks it, no perfection quest
without a perfection under way. The roster of cooldown keys is the part that never existed -
`admin.player.reset_cooldowns` deletes rows wholesale precisely because no such list did - and a Go
test now walks the package AST for every `setCooldown` call and both raw inserts, resolving five
expression shapes, with a second test pinning that the scanner still resolves all five because a
scanner that matches nothing passes forever. A Python test mirrors it from the other side: every
family the engine can emit has a label, and no label outlives its family. Fixed along the way:
`support.vote_claim` wrote the wallet with a raw upsert instead of `walletDeltaTx`, skipping that
helper's int64 overflow guard and its `low_spirit_stone` mirror onto the character sheet, so every
Mortal-World claim left the sheet stale against the wallet. No schema change.

**1.0.0** (rc.16) adds the shorthand: `x explore` runs `/explore`. Typed play has had one door since
v0.21.1, the prefix, and it reaches the world through the verb table in `content/typed_play.json` -
which is eight of the forty-three roots. There has never been a way to type a command by its own
name, and there could not be: `TYPED_PLAY_PREFIX` is one character and refuses letters outright, for
the reason its docstring gives, that "i explore" must never become an action because somebody set the
prefix to "i". So the shorthand is a word rather than a symbol, and it earns that by resolving
against the *registered command table* - `ACTIONS`, the same one the hubs and typed play dispatch
through - rather than against a second list kept in this repo. `x travel go Greenriver Town` reaches
the group leaf; `x inv` reaches `/inventory` when exactly one command begins that way; `x world
events` reaches `/worldevents` rather than running `/world` and dropping the rest.

The reason it can be heard in every channel of the guild, which the prefix is not, is that it does
nothing at all until a line names a real command. `x marks the spot` in a chat channel costs one
dictionary lookup and no database read of its own - `on_message` already reads the hub row and the
character for every line in the guild, and the shorthand adds nothing to that - so the gate that
already decided which channels typed play listens in carries the rule, and there is one place for it
rather than a channel test in the branch as well. (This paragraph used to say the line "returns
before the first database read", which was never true of `on_message` and is corrected here rather
than quietly left.) Inside the channels it always listened to, a shorthand line that names no
command still falls through to the verb table, so `x search the ravine` is unchanged. Arguments are
filled two ways and neither guesses: a command the verb table already describes gets the entity
resolution the prefix gets, so `x use a healing pill` reaches a real inventory id; anything else fills
its required parameters positionally, and only while they are plain words with a boundary between
them. Two free-text parameters have no such boundary - splitting `merchant buy zhao jade talisman` is
a coin flip - so that, and a value Discord picks from a list, and the whole `/admin` tree, are
answered with the slash command instead. The new door spends the same per-player bucket under its own
name, so the AI Routing page says which of the four a refusal came through. `TYPED_PLAY_SHORTHAND` is
`x`, empty disables it, and the words that start ordinary sentences are refused as tokens. No schema
change.

**1.0.0** (rc.15) sets the world's own people moving, and makes a spiritual sense worth casting at
them. `npc_civilization_state` has carried `home_location`, `current_location` and `faction` since
the simulation was built, and the daily tick moved wealth, influence, ambition, activity and phase -
never any of those three. Nothing autonomous had ever written `current_location` at all: it was set
at bootstrap and afterwards only touched by a merchant relocating, one game action and an admin
undo. So every NPC in the world stood exactly where they were born, for the life of the world, and
`current_npc_location`'s branch for "autonomous civilization travel has moved them away from their
home region" was unreachable code guarding a thing that could not happen. `faction` was the same,
chosen at bootstrap and frozen, so no sect ever gained or lost a member.

They walk now. Each daily tick some of them set out along the road, step to a neighbouring place, or
turn for home - weighted by trade, because a peddler is almost always travelling and a gate guard
almost never is, and capped so a world does not relocate overnight. No journey is stored: a tick is
one pass with nothing half-finished to reconcile if it is missed or replayed. The road they walk is
the players' own map rule rather than a second copy of it, and that mattered more than it sounds -
the first version here matched only `roads` and `gates`, which joins the forty-eight cities and
leaves 429 of the world's 477 places (every district, waystation, shrine and shop) with no
neighbour at all, so almost every NPC alive still could not have moved. `game.WhereAnNPCCanWalk`
composes all four ways the map joins up - roads, a city's own districts, the sites on a leg, and
either end of a site's leg - and 429 stranded places became 17. A test holds the real content to it,
because a fixture of two towns on a road passes that bug happily.

Players can found a house. `player_families`, `player_family_members`, `player_family_invites` and
`family_children` have carried one since the schema was written - with foreign keys, a cascade, a
unique seniority index and a GM dashboard panel joining founders to members to children - and nothing
ever wrote a single row into any of them. The panel could only ever be empty, and character deletion
carefully cleaned up rows that could not exist. The design was finished; only the doors were missing.

`/family house` is the doors: found one and take its first seat, invite another cultivator at a
seniority, accept or decline the offer waiting for you, leave, and record a child born into the line.
The house is distinct from the birth family beside it - that is the NPC household a character is born
into, this is a line they start - which is why it is its own page rather than more actions on the old
one. Nothing here invents state, and the schema had already decided most of the rules: a name is
UNIQUE so two houses cannot share one, a member is UNIQUE so nobody belongs to two, an invitee is
UNIQUE so nobody holds two offers, and (house, seniority) is UNIQUE so two people cannot hold the
same seat. Each of those is now a refusal a player can read rather than a constraint error, and the
seat is checked when the invitation is written rather than when it is answered, which is the worst
possible moment to discover it. A child's spiritual root and talent are the world's to roll, not the
parent's to choose; the last member out dissolves the house; and a founder who leaves hands it to the
most senior who stays rather than leaving `founder_user_id` pointing at somebody gone.

`/gender` is gone. A cultivator's sex is chosen at creation - `/begin` will not make a character
without it - so a second setter afterwards was a door onto a room the player had already furnished,
and the one thing it could do was undo a choice the creation screen had already taken. The engine
operation went with the command the way `alchemy.refine` went with `/alchemy refine`, along with its
regression tests; the sheet still prints the gendered realm titles, because reading what was chosen
was never the duplicated part. 235 actions to 234, no mechanic removed.

The rest of an NPC's life follows, and every piece of it writes a column or a table that was already
there. **Children are born.** `npc_descendants` had no writer at all and `children_count` was read
only by the query hunting for singles to marry, so married couples never had a child and the world
was demographically terminal: everyone died of old age and nobody was ever born. A healthy marriage
now produces children up to four, named from the family name they are born to and a given name from
the world's own pool, checked against every name already in use. **Realms are crossed.**
`realm_index` was fixed at bootstrap, so `phase` crept to nine and stopped there for ever; a
cultivator at stage nine can now break through, and it is paid for in wealth - the first thing wealth
has ever been for in this simulation. **Careers go somewhere.** `career_progress` climbed to its
ceiling and was read by nothing; inside a sect it buys the next rank up a five-rung ladder, and
outside one it buys the reputation an unaffiliated cultivator lives on. **Masters take disciples.**
`npc_disciple_bonds` had no writer while `world_status_queries.go` *queried* it, so "who is whose
disciple" was a question the world could be asked and always answered empty; a cultivator four realms
above another standing in the same place may now take them on, nobody serves two masters, and a bond
ends when either party dies. **And grudges are answered.** `grudge` climbed to a hundred and nothing
ever happened, which made old age the only death in the world. A grudge that has run its course is
now settled, usually with an injury and occasionally with a killing.

Each of those lands in `world_history_events` as public history - a birth, a promotion, a
breakthrough, a new disciple, a duel, a death - because a world that changes silently reads exactly
like one that does not change at all.

And the sects take people and lose them. A sect whose recruitment pressure has climbed past sixty
actually recruits the ambitious and masterless; one whose cohesion has fallen under thirty-five
actually loses members to the road. Both numbers were already being maintained by the politics tick
and read by nothing. Each change is written to the sect's own log and to `world_history_events` as
public history, because a named cultivator changing banner is what a town talks about. A settled
sect - neither desperate nor coming apart - is left entirely alone; churn for its own sake is noise
rather than a living world.

The spiritual sense could not answer
the one question the genre uses it for - is the qi here good enough to sit in - although the engine
has known the answer since rc.4: `placeCultivationMultiplier` prices a road-side shrine, a temple
quarter, a sect gate, a cave abode and its gathering array and a deployed array, and `placeQuality`
is the word the Here line and the cultivation sheet already print for it. `/sense area` read none of
it and returned scenery instead. It reads that same number now, so what a sweep reports and what a
session actually pays cannot disagree, and the detail is what the sweep earned: a weak one gets the
word for the ground, a better one what is gathering it, an overwhelming one the multiplier and the
world's own qi density as numbers. Qualitative early and precise later, which is the progression a
spiritual sense was always described as having.

Concealment cost nothing and did nothing. It was a free toggle with no reason ever to be off, and it
rose 7 a realm against a sense power that rose 10, so it fell behind every realm and stopped
mattering entirely: from Nascent Soul a concealed cultivator was read exactly, every single time. It
rises 10 a realm now, so hiding keeps the worth it had at the bottom of the ladder all the way up it,
and it has a price - a folded aura does not reach, so while concealed your own sense runs at three
quarters of its power, precision and range. Hide or look; not both. The one place concealment was
already a real decision is untouched and is now the reason to pay it: a forbidden technique used
openly is witnessed every time, and concealed only sometimes.

Two of the five readings `/sense` can give were unreachable. Resolving *what* a cultivator is got 2
harder a realm while detecting them at all got 7, so precision was never the binding constraint: by
the time a target was far enough above you to make the detail roll marginal, detection had already
failed and the answer was "none" or "world". Swept over the realm ladder and some 2,900 attribute
builds, `approx` was 0.3% of outcomes and `realm` 0.015%, both only for a minimum-stat realm-0
character - a ladder written and never walked. The slope is 6, chosen against the concealment above
rather than the old one, and it reads the way the fiction does: someone well below you exactly, one
realm below exactly or approximately, a peer approximately, and anyone above you not at all - only
the world they belong to, if that. The area sweep keeps 2, because it reads a place and its target
number already scales with the sensor's own realm, where the same slope would only cancel that
growth.

A sense also reached the entire world. `/sense` on a player checked nothing - not distance, not
location - while sensing an NPC already required standing with them, and `range_m` was computed with
the most elaborate formula in the file, took bonuses and effect modifiers, and was then only ever
printed. It is a rule now: the place you are standing in is always within reach, the places a road or
gate joins it to once your range passes 25km, and nowhere else. A sense that cannot find someone does
not learn where they are either.

And a probe was silent. The engine told the sensor "the target immediately feels your probing sense"
while nothing anywhere told the target, so there was no counter-play to being read at all. A
cultivator feels a sense settle over them when they are at least as perceptive as the one reading
them, or when the reading went all the way to the dantian, and the reply names who did it - the way a
trade offer names who sent it.

The hidden masters they might be reading went from three to twenty. All the interesting machinery
here - the collapsing false aura, the seamless void too perfect to be natural, the glimpse of
something vastly beyond your realm - served three NPCs out of five hundred and sixty-five, all of
them in one town in the Mortal World. There are twenty now, spread evenly over all four worlds,
thirteen genuine and seven frauds: a shrine hermit who is a Dao Comprehension sage, an innkeeper
letting a rumour do her haggling, a Dao Saint kneeling at a roadside stone, a waystation keeper whose
Celestial Emperor pressure is a forgery half a beat out of time with his breathing. The eight added
to existing NPCs keep the lore they already had - the hidden truth is written under it, not over it.

Half of them walk. A recluse who never moves is a landmark rather than a rumour, so ten of the twenty
hold a stop for two or three world-months and are then somewhere else: the barefoot pilgrim crossing
the Spiritual World's shrine road, the lamp-carrier going shrine to shrine in the Immortal World
looking for the one she is supposed to light, the beggar counting Celestial milestones, and the
frauds especially - a manual-seller and a relic appraiser working their cities in rotation, which is
precisely how a fraud survives. `circuit_stop` is a pure function of the canonical clock, so nobody
ticks and nothing is stored: ask at any minute and the road answers the same. A `circuit_offset`
staggers two who share a road, and a contract holds every stop to being a real place, because a
circuit naming somewhere that does not exist would strand a master where no player can stand and
nothing would say so.

And every timed lookup the sense made asked for minute zero. An authoritative payload may not carry
`game_minute`, so the field the action read was always unset - which means no active effect and no
deployed location array has ever modified a sense reading, and every sense event was filed at the
dawn of the world. It takes the clock from the engine now, like every other action. No schema change.

And how a cultivator crosses ground is finally a question of realm. Every journey in the game was a
walk: terrain set the minutes, realm shaved at most a third off them, and that was the whole ladder -
so an Ascension Realm ancestor and a mortal porter crossed the same valley at nearly the same speed,
and the flying sword this genre is built on existed only inside item descriptions. There are three
ways to cross ground now, and the realm you cross it at decides which. Below Core Formation nobody
leaves the ground unaided and the road is a road. From Core Formation a cultivator flies: a third of
the hours and ten off the danger, because what walks the road cannot reach you. From Ascension Realm
distance stops being crossed and starts being folded - an eighth of the hours, twenty-five off the
danger, and a floor that scales with the mode so the fastest travel in the setting is not
indistinguishable from the slowest on a short leg.

Six flying artifacts let a disciple off the road before their own realm would: a paper crane charm
and a wind gourd (Core Formation), an azure flying sword and a cloudskiff boat, a crane-summons token,
and a void-stride talisman that folds space for whoever holds it. What is in the bags sets the realm
you *travel* at, which is the entire point of one - it is how a Qi Refining disciple gets airborne at
all. They are on real shelves, by shop kind and by tier: the crane in the talisman halls from the
Mortal World up, the gourd with the provisioners, the sword at the smiths, the boat at the
waystations, the crane token at the beast halls, the void talisman only in the Celestial World, each
of them the dearest thing on its shelf and each bought back by the keeper who sold it. An artifact no
shop sells is scenery, which is what the six were the day they were written.

The flying sword is also a sword. It is the one artifact that both carries a rider and takes the
weapon slot, in all three stat tables the parity contract holds to each other - which is the genre's
own reason it is the default: you do not choose between going and fighting. That made a second rule
necessary. `equipment.bind` takes an item *out* of the inventory to make it an equipment instance, so
a mount read only off `inventory` would have stopped flying the moment its owner bound it, which is
exactly backwards - binding it is what makes it theirs. The lookup reads both, and a sword broken to
nothing carries nobody.

Underneath it, the roads got their ground back. Twenty locations had no `terrain` at all, among them
all three higher-world capitals, so every journey to or from Spirit Jade Capital, Nine-Heavens
Immortal Court or Celestial Mandate Palace was priced by the fallback branch - a flat seventy-five
minutes, the same for a jade terrace as for a volcanic pass. All 102 travel endpoints in the world
carry terrain now; the remaining 375 locations are districts, gates, shops and auction floors inside
them, which no road ever ends at.

And the hardest journey in the genre had three doors, two of them shut. Ascension - 飞升, the
tribulation-gated jump to the next plane - had every piece built: the three waves of lightning, heart
and void, the gate on the breakthrough that refuses an uncleared tribulation, the `world_history_events`
row at significance 98 reading "ascended to the Spiritual World". And then the cultivator was still
standing in Greenriver Town, with a free `/realmhub go` as the only way to actually be in the world
they had just crossed into. The heavens took nobody anywhere. They do now: the crossing sets you down
in the new world's capital, puts it on your map, and says so. An ordinary breakthrough inside a world
still moves nobody, and an uncleared tribulation still refuses.

The teleportation arrays were dead content wired end to end. Three of the four charged the
*destination* world's tier-1 currency - which no reward path grants, which no exchange converts, and
which can only be earned by selling in the world you are trying to reach. The chain was circular: the
Ascendant Jade Gate wanted spirit crystals from someone who had never been to the Spiritual World.
They charge the world you are standing in now, the way the tribulation's own preparation does, and a
crossing costs what a crossing should. Each of the four also got its return leg, because a one-way
gate is a trap. The `/array` picker no longer offers a gate your realm cannot withstand, and the
arrival message names where you came down instead of "your destination" - it had been reading two
keys the engine never returned.

One more thing the crossing exposed: the world-crossing tribulation is gated on either ladder - a
body cultivator clears the Mortal Body Ascension exactly as a qi cultivator clears theirs - but every
location check in both languages read `realm_index` alone. A body cultivator could therefore ascend
into a world and be locked out of it, arriving in a capital that admits realm 8 with a qi ladder
still at zero. Whichever ladder carried them is the one that answers now, in the engine and in the
pickers alike.

And a capital is a city. `death_qi.go` gives a city its gathering penalty only where `settlement_type`
is set, and the three higher-world capitals had none - so a death-qi cultivator gathering in Spirit
Jade Capital, Nine-Heavens Immortal Court or Celestial Mandate Palace quietly escaped a penalty every
mortal-world city pays. A content gate holds every realm hub to being a city.

Two more things the world had written down and nobody could reach. `climate` was parsed into
`LocationDefinition.Climate` and read by **nothing at all**, in either language - ninety-eight
locations' worth of weather that existed only as a key in a file. It reaches the narrator now, beside
the description and the protection, and a sense sweep that can name what gathers the qi also reads
the land it gathers over. The twenty places that had neither terrain nor climate have both, and a
content gate holds every travel endpoint to both; an interior has no weather, so the 375 districts,
shops and auction floors are correctly left alone.

And the three spatial keys opened nothing. Each carried a `secret_realm_id` the engine looks up
exactly - `sword_grave`, `verdant_grotto`, `stygian_tomb` - against a catalogue holding
`sword_grave_nine_echoes`, `verdant_immortal_grotto` and `stygian_lantern_tomb`, so `spatial_key.use`
could only ever answer "the token's coordinates no longer correspond to a known realm". Nobody had
found out, because nothing sold them either: a working action, a working item, and no way to hold
one. They name their realms correctly, the Mortal World's array workshops keep them (an array master
trading in spatial coordinates is exactly who would), and a key spent away from its own entrance is
now refused rather than silently wasted - `secret_realm.enter` only ever steps through at the
realm's own mouth.

And the household you were born into finally puts something in your hands. Thirteen birth families
carry a hand-tuned wealth from 26 for a ruined clan to 82 for an imperial one, a written boon and a
written risk each - and every one of them handed a new cultivator the same two spirit herbs and one
spirit iron, so what a family was worth bought their child exactly nothing on the way out of the
door. Each sends its own flying artifact now, and no two households give the same object, because
which artifact a family owns *is* the family: a tomb-watch clan folds a burnt offering that will
carry the living too, a weapon-smith's child leaves on the blade they proved on the anvil, a body
cultivator is given weighted sandals and told not to ride anything, and a fallen clan has only the
cracked ancestral sword nobody would buy. The roster is content, the same way the event sites and the
narration pool are.

Power tracks the purse already written beside each house: flight 3 below wealth 60, which carries a
disciple until their own realm reaches Core Formation and then goes quiet, and flight 5 for the
Alchemy Family and the Noble Martial Clan, which keeps carrying them to Ascension. The four heirloom
swords are deliberately under the shop ladder - the cracked blade and the two training swords below
the spirit-iron sword anyone can buy, the clan sword under the spirit-crystal one - because the gift
is the flight, not the edge. And they are heirlooms rather than stock: no shop sells one, so the
sixty-stone paper crane on the shelf is still the road for anyone whose family could not do better.

There are two doors and one guard. A character made today leaves home carrying it. A character made
before this comes home and asks - `/family support` hands it over the first time and never again -
which is also where two households stopped being ignored: neither ghost house had a case in that
switch at all, so the two families that trade in funeral goods and grave-lore handed over one
ordinary recovery pill like everybody else. The guard is an `item_provenance` row rather than a new
column, keyed on the *family* rather than the character, and that is what makes samsara work: a new
life is a new household, so it earns that household's heirloom, while asking the same household twice
earns nothing. Creation also writes provenance for what it grants now, which it never did.

And the forty-eight auction houses stopped being empty rooms with a steward standing in them. Every
lot on every floor since the auctions shipped had to be listed by a player, and merchants were wired
to them in the buy direction only - they bid, they take the unsold - so the world could consume
treasure and never produce a single piece of it. The floors of a server nobody had played on were
furniture.

The world's own people find things now. A grave-robber, a tomb digger, a beast hunter or a
herb-gatherer turns something up on their own time - weighted by trade, the way travel already is,
because a scavenger is looking and a gate guard is not - and what happens next is decided by two
questions. Is it legal? Contraband goes to the night market rather than a floor that would have the
finder arrested for it. And can they read it? Because a realm-0 scavenger who digs a Nine-Echo Sword
Tablet out of a barrow does not know what a Nine-Echo Sword Tablet is.

That second question is the whole of it. A find the finder understands goes up with a reserve. A find
they *don't* goes up blind: the house grades it by eye - "legendary or near it" - says no more than
that, and opens at a quarter of the price it would otherwise ask, because a house cannot vouch for
what it cannot name. Nothing here needed an NPC inventory table, and that is deliberate: a find is
resolved and consigned in one pass, so there is no half-owned item to reconcile if a tick is missed
or replayed. A full floor takes nothing more, exactly as it refuses a player's seventh lot, and the
whole thing is capped per tick for the same reason travel is - thirty treasures surfacing overnight
is a fire sale, not a living world. Each find lands in `world_history_events` as public history,
because a treasure coming up out of the ground is what a town talks about.

Settlement had to learn that a seller is not always a character. `seller_user_id` is foreign-keyed to
`characters`, so a consignment's is 0, and the payout was a single `walletDeltaSim` on that column -
which would have written a wallet for a character who does not exist. A finder is paid into the only
purse they have, their own `wealth`, and an unsold consignment is simply taken home rather than
pushed into user 0's bag. The GM dashboard's auction panel joined `characters` on that same column
with an inner join, so every consignment would have been invisible there; it is a left join now.

**Appraisal** is the other half, and it was a word in a list. "Appraisal" has been one of the eight
professions since the progression system was written and nothing in either language ever granted a
point of it; `item_provenance.authenticity` has been a column every writer sets to 100 and no rule
ever read. Both are load-bearing now. `/economy → Auction House → Appraise` reads something in your
bag or a lot standing open in front of you, two ways: your own eyes for nothing - Insight, the
attribute every knowledge-flavoured verb in the game already rolls, plus your Appraisal level, against
a target number that rises with the grade - or the floor's own keeper for a quarter of the thing's
worth, which is certain. Practising trains the profession whether the reading lands or not; buying an
answer teaches you nothing, because it is not practice.

Knowing is per person and permanent, shaped like `character_location_discoveries`: the second
Nine-Echo Sword Tablet you meet, you read at a glance. Which means a blind lot is blind only to the
people who have not done the work - an appraiser walks the same floor as everybody else and sees what
is actually on it. A badly botched reading is confidently wrong rather than merely unhelpful, and the
mistake lives in the words rather than in any table, so a second look can still find the truth.

Nine of the nineteen auction-grade items made all of that meaningless until they were fixed: they
carried `sect_value` 8 - the same as a recovery pill - and no `base_price` at all, and every valuation
in the game derives from those two numbers. A legendary sword tablet would have gone under the hammer
for eight stones. They are priced against the goods that already had considered numbers now, and a
content gate holds every auction-grade item to being worth more than ordinary stock.

And then a sweep for the same four faults everywhere else, which found five more and one of its own.

**A crash, shipped the day before.** The smuggling path wrote `black_market_stock(world_name,
location, item_id, quantity, price, ...)`. That table has no `location` and no `price`, and its
`currency_id` and `unit_price` are NOT NULL - so every contraband find raised a bare SQL error,
which aborted the consignment tick and, because the runner wraps a system in `BEGIN IMMEDIATE`,
rolled back every legal lot in the same pass. Two of the nineteen findable items are contraband, so
roughly one find in nine took it down. It passed CI because the test fixture beside it had invented
a matching schema of its own. The insert names the real columns now, the fixture is the production
one, and a contract test compares the two column lists directly - the fixture is what actually
failed here, so the fixture is what is now held to the table.

**Sects go to war on their own.** `territory_wars` had exactly one writer, `territory.claim`, and it
always made the acting player's sect the attacker. Thirteen sects, a full siege resolver with scores,
morale, occupations and an era war-pressure modifier - and the map could only ever be contested by
somebody at a keyboard. A sect with standing and means and a weakly-held rival border now moves on
it, rarely, never onto ground already contested and never at a wall it cannot breach. It opens the
same operation row a player's war does, taken from the engine's own statement rather than written
afresh, because a siege with no operation row is a war the tick cannot fight.

**Inscription was Appraisal all over again.** Eight professions, and the eighth had no recipe, no
call that granted it and no check that rolled it - the exact shape "Appraisal" was in before this
release. Six of the eight recipes filed under Formation were talismans; only the two array disks
were formation work. The talismans belong to the inscribers now, and because a talisman bench and an
array table are the same room here - the sect manor's own description says its grand defensive array
doubles as an inscription workshop - inscription shares the formation workshop, manor hall and effect
bonus. Splitting them without that would have quietly stripped every talisman recipe of its bonuses,
which is the fault this sweep was looking for, committed in the act of fixing another one.

**And `item_provenance.authenticity` finally means something.** Five writers, every one of them
passing the literal 100, and no rule anywhere reading it: the column recorded precisely that nothing
in the world was ever fake. Some of what the underworld sells is fake. A broker's goods now enter at
a rolled authenticity, a keeper pays a forgery what a forgery is worth - so passing one off at full
price depends on the buyer not having had it read - and an appraisal is where a holder finds out
which they are carrying. The worst provenance is the one that counts: if one of the three seals in
your bag is a copy you cannot know which, and neither can the keeper pricing them. Nothing here
touches what an item *does*; authenticity is information and money, not power.

The placeholders got the rest of it. 43 of the 48 auction houses were byte-identical - local, six
lots, 360 minutes - which mattered more than it looks, because the consignment tick reads the lot cap
to decide whether the world's finders can put anything on a floor at all. A floor's size is the trade
that passes through it now: roads met, world, and how many shops the city keeps, giving nine distinct
floors instead of two. And the repricing from earlier in this release was half a job - 37 items still
had no `base_price` and a dozen still carried the exact placeholder 8, so a Spirit-Iron Sword, a
Recovery Pill and a set of Formation Flags were worth the same through the `max(8, sect_value*8)`
fallback that five separate valuation sites use. Every item has a decided price now, anchored on
what the shops already charge, and content gates hold all of it: every item priced, a sword dearer
than a pill, the floors not one floor copied, and every declared crafting profession making
something.

**1.0.0** (rc.14) repairs a broken call in `/sense` and closes the class of fault it belongs to.
Sensing another cultivator called `WORLD.approximate_realm(...)`, and there is no such method on
`World`: the function lives in `app/rules/sense.py` and takes its two realm lookups as arguments, so
the call was wrong in both its name and its signature and would raise `AttributeError`. The three
branches beside it read `realm_world`, `realm_name` and `body_realm_name`, all real methods, which is
exactly why the fourth was written as one.

Why nobody ever hit it is the more interesting half, and it is a balance fault rather than a lucky
escape: the branch is reached only when detection succeeds *and* precision lands on "success" or
"strong", and those two conditions are close to mutually exclusive. Precision's target number rises
2 a realm while a concealed target's detection target rises 7, so by the time a target is far enough
above you to make precision marginal, detection has already failed. Swept over the realm ladder and
some 2,900 attribute builds, `approx` is 0.3% of outcomes and `realm` 0.015%, both confined to a
minimum-stat realm-0 character; everyone else gets `exact` (66%), `none` (25%) or `world` (9%). Two
of the five readings `/sense` can give are effectively dead, which is a tuning question for the sense
system and not something this release changes. The call is simply correct now.

Nothing was looking, and that is the half worth keeping. `F821`, selected in rc.13 after the `/craft`
crash, finds a name nothing defines - not an attribute nothing defines - and no suite can walk every
branch of every command. So the check that found this is now a test: every `DB.`, `WORLD.`,
`SETTINGS.` and `ENGINE.` read in `app/` is resolved against the real class, its dataclass
annotations and whatever `__init__` assigns to self. That is 1,176 of the 1,287 singleton reads in
the tree; the rest hang off `app.bot.services`, which cannot be imported without a Discord token, and
join when that changes. Clean today, so it ratchets like the pyflakes rules rather than starting a
backlog. `OPENROUTER_EPIC_MODEL` and `OPENROUTER_EPIC_FALLBACK_MODEL` were also the only two of
`.env.example`'s ninety-two keys explained nowhere in `docs/CONFIGURATION.md` - the Epic chain was
described in prose that never named the variables setting it - and the contract already holding that
file to keys-and-separators now holds the other half of the rule too. No schema change.

**1.0.0** (rc.13) makes the hub surface navigable and the beta channel installable. rc.10 stamped
`RELEASE_TAG` so the channel could be ordered; the two checks that compare a package against what it
claims to be kept reading plain `VERSION`, so every stamped release failed "Extracted VERSION
mismatch" and rc.10, rc.11 and rc.12 could not be installed at all - the change that made the channel
readable made it unwalkable in the same stroke. Both checks now read the stamp the same way the
comparison does, believing it only when its numbers agree with `VERSION`.

The surface itself was thirty-seven pages holding one action each and seven holding more than the
eight rows a panel renders: `/character` spent eighteen pages on twenty-five actions while `/sect`
put twenty-five on one, four screens deep in a Next button with nothing to say they were there.
Pages are named after what a player is doing now - `/character` 18 to 6, `/world` 11 to 4, `/npc` 3
to 1 - and `HubPage.only` lets one group be several pages, so `/sect` is four and `/admin -> Players`
is three. Three reads of "what is wrong with me" became one Afflictions page; curses were named by
two of the three, so a hurt player had to open all of them. `/perfect` and `/bodyperfect` were the
same six verbs twelve times and are one group taking a path. `/alchemy refine` was `/craft` with a
profession check `/craft` walked around, so it is gone and no recipe is unreachable. `/quest` held no
quests - it is `/ascend`, and `/quests` is the other thing. 85 pages to 71, 297 actions to 288, no
mechanic removed and the 23 registered slash commands unchanged.

The playtest checklist was describing a different game. Its group reader was a single-line regex and
a subgroup is declared over several lines with `parent=`, so every nested command was invisible - all
of `/sect recruitment`, `/sect discipleship`, `/sect manor`, and the whole `/admin` section, which
was a heading with nothing under it while the gate passed on the heading alone. Both reads are ast
now and a contract holds the file to the live surface: a command reachable from a hub and missing
from the checklist fails the build. `/admin` is off the board deliberately rather than by accident.

Two faults were caught in the same tree before it was cut. Removing `/alchemy refine` took the
profession gate with it and the local `profession` went along with the gate, leaving three uses of
the name in `_run_crafting` - so the first craft to come back with a quality label raised
`NameError`, in a core loop. Ruff was selecting only `E9` and `S`; pyflakes was off, so `F821`, the
rule whose whole job is that, was never asked. It is selected now along with the neighbours that find
a bug rather than untidiness (`F811`, `F822`, `F823`, `F632`, `F901` and the format-string ones), all
clean, so they ratchet; `F401`/`F841` stay out, being a backlog that cannot crash anything. And of
the 154 hub paths a reply prints in bold to earn a tappable button, 30 resolved to nothing and the
button was simply never drawn - no error, nothing in a log. 24 were one cause: the resolver required
`hub → page → action` and gave up when the first step named an action directly, as `**/family →
Leave**` does. It falls through to the hub's actions now, only where it previously returned None, so
a path that does name a page resolves exactly as before; the other six named an admin label that does
not exist or were written as literal command paths. 154 of 154, held by a test with a floor on the
count. No schema change.

**1.0.0** (rc.10) makes the beta channel walkable. `VERSION` holds the numbers and never the
`-rc.N` suffix - that is deliberate, so the updater compares plain numbers - but it means an installed
tree cannot tell one candidate of a version from another: every 1.0.0 rc says `1.0.0`. The updater
compared those numbers, found `1.0.0` was not newer than `1.0.0`, and answered "already the newest on
the beta channel" for every rc after the first. The channel could be read and never walked; rc.7, rc.8
and rc.9 could only reach a NAS by hand. The release job now stamps the tag it built into `RELEASE_TAG`
beside `VERSION`, and both readers of the channel - `update.sh` and the bot's update-check worker -
order releases by semver precedence, so `1.0.0-rc.6` < `1.0.0-rc.9` < `1.0.0` and a finished release is
never pulled back to one of its own candidates. The shell comparator is BusyBox awk, because a QNAP has
nothing else, and it is driven against the Python one from the same table of cases so the two cannot
drift. A tree with no `RELEASE_TAG` reads as the plain release, which is the one-time cost of the
change: `docs/CONFIGURATION.md` says how to stamp an installed rc by hand, once. The tag is believed
only when its numbers agree with `VERSION`, and an absent, empty or malformed one is ignored rather
than fatal. No schema change.

**1.0.0** (rc.9) makes qi more than one substance. Every method in the 160-manual catalogue now draws
one kind of it - Fire, Water, Wood, Metal, Earth, and the seven beyond the five phases - and a
cultivator's spiritual root decides how much of that kind actually goes in. The old cycle does the
deciding: a root that stands with the method's phase is **resonant** and gathers a quarter more, a
root the cycle feeds or is fed by is **generative** and gathers an eighth more, a root that overcomes
the method's qi finds it **draining** and loses a little, and a root the method's qi overcomes is
**clashing** - a quarter less, and an eight-in-a-hundred chance each session that the qi turns going
in and leaves a qi deviation, whatever stance was held. Void and Chaos stand with no phase: they help
and hinder nobody. The root itself counts on top of the relation, two percent a grade and a tenth at
full purity, so a Heaven-grade root absorbs more of whatever it touches. A cultivator with several
root elements is answered by the kindest of them, which is what a multi-element root is for. The
elements are content, assigned by each method's own name - a Vermilion Crane canon is a fire method,
a Frost Moon sutra an ice one - with the same rule in the catalogue generator, so regenerating the
catalogue reproduces them exactly rather than reshuffling every manual. Every element has at least a
dozen methods, so a root of any kind has something to go and find. The cultivation sheet names the
kind beside the method and what the root makes of it, the session says so when it matters, and
`/cultivation → Arts → Practise` and the manual list both name what a method draws. The body path
tempers flesh and answers to no element. No schema change.

**1.0.0** (rc.8) opens a road that has to be born into. Two households join the eleven — the
Nether-Market Household of the fog-bound yin wetlands and the Tomb-Watch Clan of the necropolis above
the old battlefield — and a child of either, and of nobody else, may take a seventh cultivation path:
the **Ghost Cultivator**. The engine enforces that at creation and the picker never offers the option
to anyone it would refuse. A ghost cultivator fills the same three dantian with death qi instead of
spirit qi, and reads the world the other way round: a road-side ruin is rich ground where a wayside
shrine is hostile, the streets of a living city are thin, and night is their noon while the afternoon
sun costs them. `ghost.harvest` takes what a place has been holding — a third of the dantian at a
stroke, priced by the ground — and `ghost.appease` burns incense at the one kind of place their
gathering fails, which is the only thing that lifts what the road leaves behind. What it leaves is
corruption (schema 41, three columns on `character_qi_body`): a point a session, six a harvest,
eating three percent of the middle dantian's purity ceiling for every ten it reaches, and tearing a
channel at a one-in-seven chance once it passes sixty. It also remakes the body, step by step, up a
six-rung ladder of ghost forms from Living Flesh to Revenant Sovereign — each widening the dantian
and deepening what daylight costs, each needing a realm to carry it, and none of them ever given
back: incense lifts the residue, not what the residue has made of you. The cultivation hub gains a
sixth **Ghost** page, the sheet names death qi and its corruption where it used to say qi, and the GM
dashboard's Cultivation page shows which qi each body holds and how far gone it is. The ground, the
hours, the corruption costs and the whole ghost-form ladder are content, in a new `death_qi_system`
block.

**1.0.0** (rc.7) gives a cultivator a body for the qi to live in. `character_qi_body` (schema 40)
holds what the sheet never had: the purity of the qi in the dantian, the meridians opened out of the
hundred and eight a cultivator can hold, the ones ruptured, and the state of the vessel itself. Qi
is no longer a small pool that barely moved - capacity is half the current realm's phase costs,
scaled by the stage within it, by the meridians open, by the state of the dantian and by the grade of
the method practised, so a Qi Condensation cultivator holds hundreds and a Nascent Soul one holds
tens of thousands. It refills over four game hours rather than by a flat trickle, and every cost in
the game - a forbidden art, a purging pill, a technique in a duel - is now a share of the pool rather
than a flat number, so the old prices still bite at every realm instead of becoming free at the
second. Purity is the price of haste: the Force stance costs a point, a qi deviation costs three, and
a severe one ruptures a meridian; impure qi makes every technique cost more (`2 - purity/100`, doubled
while a meridian is torn). Each stage crossed opens another meridian, the middle dantian's spiritual
sense reaches further as it does, and the upper dantian opens at Nascent Soul. A breakthrough now
spends a quarter of the pool before the roll, so a cultivator who arrives at the gate drained waits.
`/dantian refine` trades game time for purity up to the ceiling the realm allows, `/meridian open`
spends qi to force one open early, and `/meridian heal` mends a rupture; all three sit on the new
**Qi Body** page of the cultivation hub, and the sheet carries the pool, the regeneration, the purity
against its ceiling and the meridians on one line.

**1.0.0** (rc.6) gives a cultivator two things to raise besides the number on their sheet, and tilts
the climb. The `formation` facility of a player's own property is a spirit-gathering array now, not
just a workshop for inscribing formations: six percent a level, nine levels, and it multiplies what
every session gathers at home and in closed-door seclusion alike. The manual a cultivator practises
multiplies the gathering too, by its grade - Mortal, Earth, Spirit, Heaven, Immortal, Dao - deepened
three percent for each level of mastery, so a perfected Dao method is worth half again what a mortal
pamphlet is. `/cultivation → Arts → Practise` chooses it from the methods actually learned, and a
cultivator who never chose gathers by the best method they hold, so every existing character
benefits from the manual already on their shelf. And the pacing tilts: a stage takes eight sessions
at Body Tempering and five quarters of a session more with each realm above it, so the early game is
quick and the ladder steepens, while the qi of a new world (softened to 1.5, 2.0 and 2.5) is the
relief that makes the next ladder climbable. No schema change - both the array column and the
manuals table already existed.

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

- Current release: v1.0.0 (rc.16): the shorthand — `x explore` runs `/explore`, resolved against the
  registered command table and heard in every channel of the guild, silent on a line that names no
  command. Tagged `v1.0.0-rc.16` on the beta channel; the NAS drills and two quiet weeks make it
  `v1.0.0`.
- v1.0.0 (rc.15): the spiritual sense reads the ground it is standing on, the two middle readings it
  could give become reachable, and it stops asking the world for minute zero.
- v1.0.0 (rc.14): a broken call in `/sense` repaired, and every attribute the command surface reads
  off `DB`, `WORLD`, `SETTINGS` and `ENGINE` held to exist by a test. A NAS still on rc.12 needs the
  fixed `update.sh` dropped in by hand before it can install this or anything after it - see rc.13.
- v1.0.0 (rc.13): the hub surface regrouped around what a player is doing, the updater fix that makes
  a stamped release installable at all, and - cut into the same tree before it shipped - the `/craft`
  `NameError` and the thirty printed hub paths that drew no button.
- v1.0.0 (rc.10-rc.12): the beta channel ordered by semver precedence. rc.11 and rc.12 shipped but
  could not be installed - see rc.13 - so a NAS on rc.9 or earlier upgrades straight to rc.13, and
  one already carrying rc.12's `update.sh` needs the fixed script dropped in by hand first.
- v1.0.0 (rc.9): elemental qi - every method draws one of twelve kinds, the five
  phases decide how much of it a given spiritual root can absorb, and a clashing element can turn
  going in.
- v1.0.0 (rc.8, schema 41): the ghost road - two households born to death qi, a seventh path only
  they can take, a ruin for a shrine and night for noon, and a corruption that remakes the body up a
  ladder of ghost forms.
- v1.0.0 (rc.7, schema 40): the qi body - purity, the hundred and eight meridians, the three dantian,
  a pool that scales with the realm and refills over game hours, and every qi cost in the game
  rescaled as a share of it.
- v1.0.0 (rc.6): the spirit-gathering array a player raises at home; the manual they practise speeding
  every session by its grade and mastery; a climb that tightens with each realm and eases when a
  world is crossed.
- v1.0.0 (rc.5): a session is a share of the stage it fills; crossing a realm raises the cultivator;
  the higher worlds are thick with qi; four balance fixes.
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
- **Schema 51** put the content file into tables with columns. Nine derived `content_*` tables
  (`npcs`, `locations`, `items`, `recipes`, `sects`, `shops`, `merchants`, `manuals`, `techniques`) are
  written by the engine alone - `internal/contentsync` - from `content/world.json`, hash-gated, in one
  transaction, and with deletes, which the Python-written `catalog_*` blobs never had: an entry renamed
  in the file lived in `catalog_npcs` forever. Every row carries the entry's exact bytes in `data_json`
  and a typed projection beside it - `content_locations(road_site, district, settlement_type, ...)`,
  `content_npcs(location, district, ...)` - so "every NPC in this district" is an indexed read rather
  than a parse of 2.5 MB. The projection is defined once, in Go, and the migration's DDL is held to it
  by a contract test; a parity test counts every projected column against the real file. The tables
  are filled by the engine but created by this migration, which in the compose stack runs after the
  engine is already healthy, so db-init calls `/v1/content/sync` the moment it has run and the bot
  again at `CATALOG_READY`; engine-backed readers switch to `content_*`, the local-SQLite path stays on
  `catalog_*`, and `catalog_*` is still written so a rollback finds it intact. The GM's "Sync world
  catalog" re-reads the file on both sides (`admin.content.reload`, audited) instead of rewriting the
  bot's in-memory copy and saying it had.

- **Schema 50** let one of the world's own people actually sell what they found.
  `auctions.seller_user_id` lost its `NOT NULL`, because it is foreign-keyed to `characters` and a
  consignment has no character behind it - so there is no integer that can mean "nobody", and the 0
  that `npc_consignments` had written since rc.15 was refused by that foreign key every single time.
  `runSystems` returns on the first error and the consignment batch is fifth of eight, so
  `sect_politics`, `clan_dynamics`, `autonomous_world_events` and the whole advanced-maintenance
  bundle never ran either: commissions did not expire, auctions did not settle, merchants did not
  bid, and the secret realm never rotated. The batch is daily, so this was every day. NULL is the
  sentinel now; `storage.ParseInt(nil)` is 0, so every reader's existing `seller > 0` guard - and
  `payAuctionSeller` paying a finder's own `wealth` - was already correct and is untouched. The
  rebuild parks `auction_bids` in a table carrying no foreign key first, because `auction_bids` is
  `ON DELETE CASCADE` on `auctions` and a DROP under `foreign_keys=ON` fires that cascade; the bids
  are put back once the new parent exists. One reader was not already correct: the merchant
  settlement path paid the seller's wallet unconditionally, so the first NPC lot a merchant won would
  have refilled the same hole one step downstream - `game.PayLotSellerTx` is the single payout both
  settlement paths use now.

- **Schema 49** gave the people this world makes for itself somewhere to live. `npc_registry` holds
  who somebody is - role, manner, what they want, what they are afraid of - for anybody who is not
  in `content/world.json`. `npc_descendants.generated_as_npc` had existed since the life cycle was
  written and was read by nothing, written twice as a hardcoded 0, because there was nowhere to
  promote a child *into*: `catalog_npcs` is a mirror of the content file and is rewritten from it at
  every boot, and `npc_civilization_state` says where a person is and not who they are. It is
  deliberately a second table rather than a flag on the mirror, and that is the whole safety
  property - a rebuild is an unconditional DELETE over the derived table, and this one is never
  named in the statement, so no wrong predicate can wipe the world's own people.

- **Schema 48** gave a search somewhere to arrive. `npc_graves` holds where a missing person
  actually ended up, what they were carrying when they stopped, and whether anybody has been to it.
  Until now a disappearance that ran out of grace produced a history row and nothing else, so the
  searcher who went looking stood in an empty place and learned nothing - `status='dead'` is not a
  thing you can stand in front of. A grave is, and being able to carry something back from one is
  what turns knowing into reporting. Nothing is dropped and no existing column changes meaning.

- **Schema 47** gave a disappearance a length. `npc_civilization_state` gained
  `missing_since_game_minute`, and `status` carries `'missing'` beside `'alive'` and `'dead'`, so
  every batch that reads `WHERE status='alive'` stops offering a missing person by construction
  rather than by a rule written four more times. `npcTravel` deliberately stores no journey -
  "nothing tracks how long they have been away - this is what makes the journey end without storing
  a journey" - which is the right rule for an errand and the wrong one for a disappearance, because
  how long it has lasted is the whole of what makes one. It is the single thing about a journey
  worth keeping: the minute it stopped being one. Nothing is dropped and no existing column changes
  meaning, so an old database upgrades by adding a column with a default of zero.

- **Schema 46** gave a method somewhere to be known. `character_recipes` is the third table in
  the family of `character_manuals` and `character_item_appraisals` - a composite key, the route
  by which it was learned, and the minute it was - because crafting now asks whether a cultivator
  was ever taught the thing they are making. The migration grandfathers every character alive at
  the time: each keeps the entry methods and everything their profession level already reached,
  so the release lands as nothing taken away.
- **Schema 45** let the world's own people put things under the hammer and gave a cultivator
  somewhere to record what they have learned to recognise. `auctions` gained `seller_npc_name`
  (mirroring `merchant_buyer` and `merchant_bidder`, because the seller column is foreign-keyed
  to `characters` and a finder is not one), plus `appraised` and `grade_band` for a lot consigned
  blind; `character_item_appraisals` is shaped like `character_location_discoveries` - a
  composite key, the route by which it became known, and the minute it did.
- **Schema 44** dropped `core_state_versions` and `core_request_log`, which came in with
  migration 12 as the shape of an earlier write ledger and were never written or read by
  anything in any release since. Migration 12 keeps its statements - a historical migration is
  how an old database walks forward - so the removal is its own step rather than a rewrite.
- **Schema 43** added the cast an event brings with it (`world_event_npcs`): the captain to
  report to, the elder to impress, the auctioneer whose floor it is - named at spawn, talkable
  through the ordinary NPC path, and event-scoped so the life simulation never inherits them.
- **Schema 42** added what is actually inside a world event (`world_event_nodes`): the beasts,
  herb and ore nodes, relics and tasks a scene contains, each with a finite `remaining` that
  depletes as players work it, so an event can be cleared out instead of only being announced.
- **Schema 41** added the ghost road to the qi body - which qi it holds, the residue death qi
  leaves and the form the residue has made (`character_qi_body.qi_type`, `.corruption`,
  `.ghost_form`) (v1.0.0-rc.8).
- **Schema 40** added the cultivator's qi body - purity, meridians and the dantian's state
  (`character_qi_body`) (v1.0.0-rc.7).
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
