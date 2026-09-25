# Xianxia RP Discord Bot — Version History

The changelog, one paragraph per minor. The per-release entries as they were written are in
`docs/history/CHANGELOG_0_18_TO_0_40.md`, and the earlier per-release notes (`V018_RELEASE_NOTES.md`
… `V023_RELEASE_NOTES.md`) beside it. `README.md` describes the current release.

## Changelog

**1.4.1** gives the Player Editor a Quests card, so a GM can finish a quest a player is stuck on.

Reported from play: a cultivator passed the household lesson while "The Last Lesson" was not yet
active, so the one report the lesson makes was lost - and the lesson is once per life, so nothing
could ever make it again. An objective is counted only while its quest is active, and no GM lever
reached a player's quests at all. The card lists what the player holds with each objective's
progress, and carries two audited levers: `admin.player.quest_progress` replays one objective
report, and `admin.player.quest_complete` fills every objective. Both run through
`questProgressTx`, the code `quest.progress` itself now calls, so a quest a GM finishes pays its
reward, resolves its commission, moves a household's standing and hands over its next stage
exactly as a player's last report would. Neither can be undone, because the reward is paid.

**1.4.0** lets a GM update the server from the dashboard: the Admin Console's Server update card asks for the newest release, a watcher on the NAS installs it with every backup and rollback `update.sh` already has, and the card shows how it went.

Nothing inside the stack can update it - the images carry the code and no container holds the
Docker socket or the host checkout - so the button writes an audited request into the engine
(`admin.server.request_update`, a `world_state` row with its own nonce), and **`update_watch.sh`**,
a new script beside `update.sh` on the NAS, does the work: it reads the request the way `update.sh`
already reaches `/v1/db/backups` (`docker compose exec` into the engine container, the token read
from the container's own environment - no port opened, no privilege added), closes the world with
the Maintenance lever, runs `./update.sh --upgrade`, reopens the world whatever happened, and
reports `acked`, `fetching` and `done` or `failed` under the request's nonce
(`admin.server.update_status`, audited as actor 0 so the audit table shows the watcher wrote it).
A closed request takes no more reports, so a watcher that restarted cannot re-report a settled one;
a heartbeat every five minutes is how the card knows the watcher is alive, and the button is
offered only while it is. A release that adds a `.env` key is refused by `update.sh`'s own preflight
before anything stops, and the card says *"needs ./migrate_env.sh"* - the watcher never touches the
file the tokens live in.

And the card reads what is newest from the bot's own release check (`release`, a new read on the
control channel), so `release_channel.py` stays the one comparison and GitHub is asked once per
`UPDATE_CHECK_HOURS`; an unreachable bot shows "unknown", never "up to date". `startup.sh` says when
the watcher is not running. One new key, `UPDATE_WATCH_INTERVAL_SECONDS`. No schema change.

**1.3.5** counts how often each command is used and shows the GM the most used ones - and changes no order for it: the daily five stay first on the menu and every page keeps its authored order, on the owner's call.

The owner asked to collect the most used commands. `command_usage` (schema 64) holds one row per
command path per UTC day, server-wide and with no user id, so the erasure sweep never has to know it
exists. The path is the leaf path the hubs build (`/alchemy forage`, `/travel`), so a slash command, a
hub press and a typed line count as one thing, and three doors record it after their own refusal has
passed - the command tree's `interaction_check` (a command only; an autocomplete request shares that
check and is a keystroke), `hubs._invoke_action` and `typed_play.dispatch`. Recording never raises
and never waits (`app/bot/usage.py`): a counter that could cost a player their reply, or a round
trip on every press of `/explore`, would be a worse bug than the one it measures. The cleanup prunes
past thirty days.

And `/admin server observability` gains **Most used commands (30 days)**: the top ten paths with
their presses, "none recorded yet" on a fresh world, and "unknown" rather than a zero when the
count cannot be read. It is the one reader of the counts; `test_command_use_is_counted.py` holds
that nothing which draws a panel reads them, because the numbers were asked for to be seen and not
to reorder anything.

- **Schema 64** adds `command_usage(path, day, presses)`, a presentation counter of command presses
  per UTC day, pruned by the maintenance cleanup at thirty days.

**1.3.4** clears the last of the punch list: the Stygian Ghost Scripture is the Ghost Cultivator's high art and its inheritance knows whose it is, a path's skill is on the sheet, and two era keys that nothing could scale are gone.

`inheritances.stygian_keeper_legacy` preferred the Soul Cultivator and granted a scripture that was a
priced item doing nothing - written when the Ghost Cultivator had no manuals to prefer (v1.0.3). It
prefers the Ghost Cultivator now, the scripture is an authored Heaven-grade Ghost Cultivator manual
(realm 4, three techniques, every page costing karma), and the tomb's rooms favour both paths.
`Inheritance.PreferredPaths` is read by the grant for the first time: a cultivator of a preferred
path has the inheritance's manual studied at once, so its first technique is usable the moment the
last room is cleared; anybody else is handed the sealed copy to study the ordinary way. `/sheet`
prints each path's `skill` under the path, a field parsed and read by nothing for the life of the
file. And `secret_realm_frequency` and `market_volatility` are deleted from the era vocabulary on
the owner's call: nothing in the game varies a price, and the realm weights are balanced per realm.

And two harness notes are settled without a change: the reset leaf's success path stays proven by
the engine half and its refusal by the Discord half, and the sweep counting a designed refusal as
coverage is recorded as a known limit.

**1.3.3** settles the eight open rule decisions: a Law control technique now weakens the opponent it lands on, a clan treaty that runs out ends and leaves a rivalry, and an auction-door ambush is said to happen on the house's own doorstep.

The owner read the research on the eight `deferred (design)` entries in `docs/TODO.md` and took
three changes and five closures. **A Law control effect reaches its target** (schema 63):
`spatial_lockdown` and `spatial_strangulation` have carried modifiers describing a target since they
were written, and a battle opponent is a name on `battles` rather than a row `active_effects` can
address, so they reached nobody. `battles.opponent_modifiers_json` is the one place they can go; the
technique sums the landed effect's modifiers into it, the opponent's counter-attack loses the
`agility` and `body` taken, and the player's flee gains what the opponent's lost `escape_bonus` can no
longer follow. The battle card and the technique reply name the debuff off the engine's own sum.
Every read and write guards on the column, so a battle fought before the migration runs is fought
without the debuff rather than refused. **A clan relation ends**: after the tick's drift, a treaty at
or below zero goes inactive and a rivalry opens from both sides at its opening score, with a public
history row; a rivalry at or above zero ends and nothing follows; a blood feud never ends by drift.
**The ambush is at the doors**: the leave reply and the narrator are told the fight stands on the
house's own doorstep, where its protection ends, rather than that violence cannot begin there.

And the other five are closed on the owner's call with their reasons written into the entries: the
curriculum stays realm-banded (each named system is refused by its real gate in the engine), an
unlocated NPC cannot arise with shipped content (all 574 carry a location), an event scene stays with
its world (nobody below the floor can cross into it either), the Nine-Yang body scorches only through
the purge, and moderation's statement about itself is accurate because every gameplay write already
goes through an engine action.

- **Schema 63** adds `battles.opponent_modifiers_json`, the Law control debuff on a battle's opponent,
  guarded on by every engine read and write so a battle fought before the migration is fought without it.

**1.3.2** makes the five things a cultivator does every day one step each: `/cultivate`, `/explore`, `/hunt`, `/forage` and `/mine` are slash commands, and the menu carries them as a row of five buttons.

Player feedback, with the `/cooldowns` reading pasted: *"For each command i have to go to 3 steps.
Time consuming ... Hunt, Gather, Mine, Explore, Cultivate. With slash command or interface button.
Instead of a b then c."* Four of the five were root commands already and none was in the command
tree, so a hub page was the only door; forage was a group leaf with no root at all. Each is in the
tree now (`/forage` runs the same handler as Craft → Alchemy → Forage, through the registry, so the
two doors cannot drift). The menu's **Daily** row is one tap each: a press opens the hub in place
and runs the leaf, so the result lands in the same panel a hub press would use, behind the same
maintenance and seclusion gate. `/cooldowns` names the command instead of the three-step path, and
a reply that prints `**/hunt**` still earns its next-step button. Nothing in the engine changed.

**1.3.1** puts six rules the bot was holding into the engine, and draws three more buttons only where they work.

A catalogue sponsor must be standing where the applicant is, and the engine now knows where a
catalogue NPC stands - the road a wandering master walks, the simulation's row, the daily schedule
while they are at home - so the check is a bound rather than a courtesy. Which sects a cultivator has
discovered is what their known gates justify, not what a client asserts. Work on a city's board is
taken in that city, and a territory is claimed standing on it. The forage wait is the cooldown
table's (`FORAGE_COOLDOWN_MINUTES`, twenty minutes as before). And a player who finished the beginner
path before a stage was added is handed it the next time they do anything, along whatever chain a GM
has pointed the stages down.

It also hides the ghost road's harvest where nothing died and its rites where nobody is buried,
the black market where no post is open, and the array where none departs - the three place rules
v1.1.0 left drawn for their cost, at the cost of one read each on a panel refresh.

**1.3.0** takes ten decisions the owner made in one message: a failed craft gives half of its makings back, a hall teaches only what its world can make, the Nine-Echo Sword Wraith can finally be fought, and the reset asks with the count in hand.

A failed craft returns half of each input, rounded down, so one unit of anything is still the stake;
the reply names what was salvaged. A trade hall teaches, of the rank passed, only the methods its
own world can supply the makings of, and says which it withheld and where they are learned instead -
a Mortal apothecary no longer hands a Journeyman the Dawn Lotus Vitality Pill, whose herb grows a
world up. The Nine-Echo Sword Wraith's lair, which named a secret realm rather than a place, is a
secret floor beneath the Sword Grave of Nine Echoes: the raid starts at the realm's entrance once
the party's leader has walked the realm to its last room. `/reset`'s "Are you sure?" names how many
restarts the account has left, read from the engine and never restated. The cultivation card shows
the qi pool and its purity at every realm again, and only the channels wait for the page that opens
them.

It also authors a send-off for every one of the thirty-three upper-world houses a samsara rebirth
can land in - an heirloom flying artifact, a handover line and the family trade each, keyed by the
house's archetype - in place of the reading v1.2.3 made off the kind in each house's id. A property
cannot be founded inside a birth household, and the panel hides Establish there. The two event nodes
and five event-menu rows that rolled `heart`, an attribute no character has, roll presence or will,
and the engine no longer accepts the word. And the Celestial Mandate talisman hall pays 96 for a
Starfall Talisman, beside the other Celestial halls and under the 102 its makings cost, so the one
recipe that turned a profit at the first rank does not.

**1.2.3** settles the review's deferred claims, both tiers: fourteen held and are fixed, two were read and left as they are.

A GM's Vacuum no longer stalls behind a half-written action. A Python write is two requests to the
engine - an execute, which opens the transaction, and a commit - and between them nothing is in
flight, so the maintenance barrier let VACUUM in while that session still held the write lock;
VACUUM waited out the whole ten-second busy timeout and then failed, with the commit queued behind
it. It refuses at once now and says a session is busy, and the commit goes through.

It also makes a qi-gathering array qi-path weather at both doors: a body retreat was priced on the
sect manor's array while a hand-sat body session was not, and a hand-sat body session was priced on
a deployed array while a body retreat was not. Both of a player's combat turns defend against the
counter-attack with one target number, where an ordinary strike had left out the dual-cultivation
resonance a technique counted. A GM can undo an undo again: undo, redo, undo used to refuse. A
keeper's rank price stops short of a travelling merchant's wares as well as the shops' shelves,
which had let a talisman be bought from Madam Wen at 15 and sold back at 16. And `/auction sell`
lists in the house's own coin unless told otherwise, where it defaulted to the Mortal stone on
every floor in the world.

And two claims were read and deliberately not changed: `meridian.open` has no cooldown because a
quarter of the insight pool, rising each channel, is its pace; and an expired panel's Reopen button
takes the same road the Menu button does - the panel opens, and every leaf inside it is checked
again on the press.

It also reads the review's second tier. A commission could be completed by asking: the action
paid whatever outcome the client named, and it refuses "completed" now, since a commission is
completed by doing it. A market counter sold a Wind Gourd for 9 that a provisioner paid 45 for,
and paid 840 for a tomb token a shelf sells at 420; a market now sells for no less than any keeper
pays and pays less than any shelf asks. A lot is appraised only on the floor it stands on. A duel
whose turn holder has died can be ended by the living player, which a refusal in the bot had been
standing in front of. Narrate-it meets the maintenance and seclusion gates. A refused character
creation is told in the engine's words. A sect discovery is stamped with the engine's minute. A
control request whose body never arrives is answered rather than dropped. And a rebirth above the
Mortal World is sent off by its house with an heirloom, a trade and a tutor, where before it got
none of them because no upper-world house carried a send-off entry; its purse mirror is right in
every world.

And a sect's entrance trial reads what the sect authored, on the owner's call. Every public sect
carries a base TN, the paths it favours, the roots it has an affinity with, the households it
keeps a tradition with and which side of the karma ledger it wants, and the bot has printed all of
it in the trial notes since the block was written - while the engine rolled TN 15 for every sect.
The Azure Cloud gate is TN 14 and a Sword Cultivator with a Metal root gets +3 there; the
Celestial Mandate Academy is TN 20; a notorious applicant is refused at an orthodox gate without a
sponsor, as the notes have always said.

**1.2.2** names the trades' ranks the way the genre does: nine tiers, each a title, each trade its own word.

The owner's call on v1.2.0's grades: the Nine-Tier ladder. Unranked before the first examination,
then Tier 1 Apprentice up through Adept, Artisan, Expert, Master, Grandmaster, Sage and Emperor to
Tier 9 Sovereign, and the trade's own word in front - a Pill Apprentice, a Forge Sovereign, a
Talisman Sage, an Array Master, and Herb, Ore, Beast, Artifact and Treasure for the gathering and
appraising trades. A rank is a level and nothing stored changes; what changed is how a level is
read, in `PROFESSION_TIERS` and `PROFESSION_TIER_WORDS`, quoted by the examinations' names (The
Tier 1 Forge Apprentice's Billet) and by every surface that prints a trade. No schema.

**1.2.1** fixes what a deep review of the tree and one report from play found: a beast can always evolve, a homestead can be upgraded in any world, and a dozen smaller wires.

Reported from play: *"I can't evolve my beast it says loyalty should be 110, but I can't go over
100"*. The requirement climbed ten a stage past the cap feed and train clamp loyalty at, so stage
five asked for a number no action could produce, for ever. It is held under the cap now, so every
stage stays reachable.

A homestead may be founded in any world, and its upgrade charged the Mortal stone in all four - so
above the Mortal World, where every reward is paid in that world's crystal, no upgrade could ever be
afforded. It is charged in the money of the world the home stands in. A GM's currency grant now
mirrors onto the sheet by the target's own world currency rather than by the Mortal stone's name;
the household's support wait is the engine's rather than the caller's; a hunter killed by a beast or
a victim killed in a robbery leaves a widow like every other death; a restore's safety backup is
sealed like every other backup; the narrator asks the registry for the world's own people; the
Quest Editor recognises the recipes a craft objective names; a stage the tutorial catches a
graduate up on is told to them; Vacuum and Cleanup write their audit rows; the reset script speaks
to the bot with a tool its image has; `ENGINE_SHUTDOWN_GRACE_SECONDS` reaches the engine; an
Admin Console search treats `_` and `%` as text; and a grave claimed twice in the same instant
hands over one keepsake.

**1.2.0** makes the first hour a short list: cultivate, break through, explore, buy, craft, forge, gather, hunt, mine and quest, and everything else opens as you cultivate.

Player feedback, three reports in a week: *"we need to simplify interface ... Cultivation, Breakthrough,
Explore, Shop, Craft, Forge, Gather, Hunt, Mine, Quest until Foundation Establishment - these things are
enough"*, *"Interface is overwhelming ... I still forget where to go what to do"*, and *"instant travels
... I don't have to wait half hour"*. At Body Tempering a cultivator saw 142 of 248 leaves; they see 120
of 250 now, and every system outside that list waits for Foundation Establishment at the earliest (the
deeper floors at 3, 4 and 5 are untouched). Two things stay open against the list on the owner's call:
a companion, because it is the one system the first hour has to be told about, and the body path, which
is a way of cultivating rather than a system beside it. The main menu leaves off the hubs the curriculum
has opened no lever on yet - Combat, Abode, Inner World and Secret Realms at Body Tempering - and names
them in one line with the realm that opens the nearest, and it says what the tutorial asks for next at
the top: the first objective still short on the active stage. A hub's slash command still works, and
`/locked` still lists every door; hiding is advertising, never a bound.

It also builds the seam. `/world → Act → Mine` (`exploration.mine`) is the ore half of gathering,
forage's twin: the ore of the world you stand in through the same resolver the event sites use, a rare
vein of the next world's ore, the tier-flat makings a seam gives up (`mine_materials`), a few spirit
stones on a rich dig in the money of that world, and Mining as a trade in its own right, with the
Forging houses' tradition riding the roll. Spirit iron - three of which every Forging entry method
wants - came only from a shop counter or an Iron-Horn Boar before this.

And the tutorial does not stop at the household's lesson. Two stages follow it: "Iron from the Seam"
(mine spirit iron, come out of a hunt standing, forge a Spirit-Iron Sword, sell something to a keeper)
and "The First Gate" (the first breakthrough, which is a new objective type), and the sect road follows
them. Somebody who finished the lesson before this release is handed the next stage at their next quest
report, wherever they are, rather than only at the lesson's door.

And a road is walked in the telling. `TRAVEL_TIME_PERCENT` is the share of a road's length a
traveller actually waits, default 0: the toll, the encounter and the discoveries along the road are
untouched, and only the transit - the half hour between neighbouring towns during which every other
command refused - is gone. An operator who wants the old pace sets it to 100.

And the trades' ranks read as grades. *"Journeyman sounds medieval"*: the ladder is Unranked, Grade 1
to Grade 5 and Saint, stated once in `PROFESSION_RANKS` and quoted by the examinations' titles - The
Grade 1 Billet, The Grade 2 Edge, The Grade 3 Folding. Nothing stored changes; a rank is a level.

And the qi body and the Laws are the Spiritual World's game. The meridians, the purity and the
dantian's refining open at Spirit Body Transformation, where the card until then says so instead of
naming a number whose lever it hides; the Laws wait for the same realm by their own content floor,
`law_system.normal_min_realm_index`, which moved from 6 to 8.

- **Schema 62** re-points the lesson's `follow_on` at "Iron from the Seam" on a running world, only
  where it still names the sect road, so a chain a GM re-pointed is obeyed (migration 55's rule).

**1.1.0** lets a new cultivator join a sect, and tells them how.

Reported in Discord: at a Major Sect Recruitment event a player asked the Visiting Elder to take them,
pressed him, and was told they were impatient and turned away; the answer given in the channel was
"get a recommendation, do quests for a sect elder, then travel to the sect for intake", and the next
question was "what menu?". None of that road existed. The event named no sect and its elder belonged
to none, so the refusal was the narrator improvising. No road reaches any sect gate, so exploring
never finds one, and the envoys' hall said the gates were on your map while writing nothing there.
Recommendation and Trial waited for Qi Refining, so a new cultivator could not join a sect at all -
while "A Road Toward a Sect", which the beginner path hands everybody, asked for exactly that and could
never be finished. No sect work was open to anybody outside it, and a recommendation's bonus was never
added to a trial roll.

Now a recruitment delegation speaks for a real sect of its world and names it on the event's panel;
its elder can sponsor you through **/sect → Recruitment → Recommendation**, and clearing the event's
entrance trial shows you the sect's gate and puts it on your travel list. The envoys' hall in a realm
capital's temple quarter really does put every public gate of its world on your travel list.
Recommendation and Trial open at Body Tempering. A sect's gate keeper has one piece of entry-level
work open to somebody in no sect, and finishing it earns standing with that sect, which lowers its
trial's difficulty and makes a sponsor likelier to agree. A recommendation's bonus is now added to
both trial rolls, as the panel always said. The quest names the menus it wants, no narrator can
grant, promise or refuse membership in conversation, and the trial is always sat at the gate the
sect names.

It also stops drawing a button where the game would only refuse it because of where you stand.
Inside your household, City Shops' Browse, Buy and Sell, exploring, hunting and travelling used to be
drawn and then say no; so did an auction's bid off the floor, a trade offer outside an inn, a
challenge on protected ground, and a dozen more. Each is now shown as a locked line saying why and
where it works, and only where the game itself would refuse; City Shops' Here is always shown, since
it is how you find a city's shops. A caravan can be dispatched from a city's gate or district, not
only from its central street, and a sect residence's lock line names the way out that works.

- **Schema 61** lets a world event speak for a sect. `world_event_npcs.sect_name` and `can_recommend`
  and `world_event_nodes.reveals_sect` are stamped when a site spawns, so a content edit mid-event
  cannot change whom a delegation speaks for; the quest's two labels are rewritten in a running
  world, in the definition and in any copy a player already holds, and the twelve entry-level sect
  commissions are opened to outsiders - leaving alone anything a GM has already edited.

**1.0.17** pays a craftsman more for the goods of their own trade.

A keeper pays about a third of the shelf price for anything handed over the counter, and until now
the same third whoever handed it over, so a Saint alchemist's pill fetched exactly what a beggar's
did, and a Qi Nourishing Pill sold back for less than the herbs it was made from. Now each rank above
Novice in the trade that makes a thing - Alchemy for pills, Forging for blades, Inscription for
talismans, Formation for arrays - adds two of the shop's coin to what the keeper pays for it: a
Journeyman alchemist's Qi Nourishing Pill fetches 8 rather than 4. It never reaches the cheapest shelf
price anywhere, so buying and reselling can never pay, and raw materials, which no trade makes, keep
the ordinary price. Shelf prices are unchanged. The keeper's board shows your own price and the sale
says which rank lifted it.

**1.0.16** makes every treatment mend a condition, so a cultivator can always heal.

Reported from play: six Heart-Calming Pills spent on a Qi Deviation at a 28% chance, six failures,
and *"I can't heal injuries"*. A treatment was a roll that either mended a level or did nothing, and
the pill went either way. Worse, it rolled Insight and Spirit while Qi Deviation, Meridian Damage and
Dantian Damage each lower Spirit by their own severity (a Soul Wound lowers both), so the worse the
condition, the less anybody could cure it: at the top severity a new cultivator's odds were
nearly nil, and every Force deviation raised it a level. Every pill now lowers the severity - by one
on a failed roll, two on a success, three on a strong success - so a condition costs at most as many
pills as its severity. The roll is easier too: against 10 plus the severity rather than twice it,
and the condition being treated no longer counts against its own cure. The reply says where the
severity fell from and to.

**1.0.15** lets an Apprentice alchemist make the Heart Calming Pill the Mortal World teaches them.

Reported from play, by a player who had just passed the Apprentice examination in Jadewood and was
refused with *"missing materials: Twin Extremes Ice-Fire Fruit x1"*. The examination teaches the Heart
Calming Pill wherever it is sat and its slip is sold only in the Mortal World, while the fruit it asked
for sits on no shelf anywhere and is only ever foraged from the Spiritual World up. So the one world
that sold the method was the one world that could never make it, and the price said the recipe had
never been meant: a 1,050-stone fruit went into a pill the shops sell for 13 to 26. It asks for a
Moonveil Herb instead, and the Mortal World's town apothecaries, Jadewood's among them, now keep
Moonveil Herb beside their Spirit Herb, so the hall that sells the slip sells everything it needs.
The fruit is otherwise what it always was: a rare find in the Spiritual World's hills that the
auction houses want.

It also makes a refused craft say where the missing materials are sold. The refusal used to tell
everybody to buy them "at a hall of the trade" whatever the material and wherever they stood. For
each thing you are short of it now names the hall in your own city that sells it, or the cities of
your world that do, or, when your world sells it nowhere, says so and names the worlds that do.
Beast cores, which no forage turns up, point at the hunt.

**1.0.14** lets a cultivator start over whatever they have already done in the world.

Reported from play: a reset refused with *"Xie Kormaq has already left a mark the world keeps
(world_history_events.related_user_id)"*. Since v1.0.1 a reset was refused once a character was named
on anything a shared world keeps, and a history row counted - almost everything a new cultivator does
writes one, starting with the first place they discover - so the way to start over stopped working
minutes into a life, with a refusal that named a database column and said nothing about whether
waiting would help. Nothing the world keeps stops a reset now. A history entry only that player could
see goes with the life; a public one stays, and the name in it becomes *an unknown cultivator* - the
deed is remembered, the doer is not. A gate between worlds, an emptied grave, a written quest and a
sect manor stay where they are with the link to the account cut, and a gate named after its maker is
renamed for an Unknown Cultivator. A player family passes to its most senior other member, the same
rule as a founder walking out, or is dissolved if nobody else was in it; the reply says which, and
what was left behind.

It also fixes the same family fault in a GM's erasure, found while wiring the reset. Erasure deleted
the character row first, and the database's own cascade took a founder's whole family with it, other
players' places in it included, although erasure is meant to let a family outlive its founder. It
hands the house on before anything is deleted now.

And it keeps the GM's view of an account's restarts standing when the engine is down. v1.0.13 gave a GM two places to read how many restarts an account has spent - the dashboard's Player
Editor and `/admin player inspect` - and both promised to answer "unknown" and an em dash rather than
a number when the engine does not reply. Both caught the engine's own error and an `OSError`, and the
one a real outage raises is neither: the engine client calls httpx directly and wraps nothing, so a
refused connection or a timeout escaped - costing the dashboard its whole Player Editor, which asks on
every load, and leaving `/admin player inspect` without a reply. Both catch `httpx.HTTPError` now. The
gate had tried exactly the two errors that were already caught, so it could not see the third; it
tries a refused connection and a timeout too, and restoring the old catch fails it on both surfaces.

**1.0.13** teaches the playtest sweep to tell a question it is being asked from a control the answer
came with.

The Discord harness presses every leaf of every hub and answers whatever the reply puts in front of
it. Once v1.0.12 raised the player past the curriculum's ceiling, `/battle challenge` **resolved**
for the first time in the harness's life - every earlier run pressed that leaf and not one of them
ever began a battle - and it failed at once. A resolved challenge posts a battle panel, whose
technique and recovery pickers are disabled for a cultivator with no Law techniques and nothing to
drink, each carrying a single option that says so; the sweep took the first picker on the reply
without asking whether anybody could use it. A real player could not click it either, which is what
the simulator said and what the harness now believes. The skip lives in the one helper both
answerers reach, so the scripted half gets it too, and a disabled *button* is still a failure -
that one means the panel timed out. What it does not do is count a held-back leaf as covered: a leaf
pressed only into a refusal has had only its refusal proved, which is the engine harness's own rule
from rc.58 arriving on this side, and `docs/TODO.md` carries what closing it would take.

It also finishes counting the fifteen minutes v1.0.12 removed from five files. The harness jumped a
panel's clock **901 seconds** - not the number, an *encoding* of it, one second past a deadline
stated somewhere else - so no search for it could have found it, and raising the default left the
step moving a panel an eighth of the way to its deadline and reporting that it would not expire. It
reads the window the run is configured with now, and the harness pins that window where it pins
every other setting. Both bounds on it were measured: waiting a two-hour window out costs minutes
of woken workers for no extra assurance, and a one-minute window never settles at all, because a
view timer that near counts as runnable. Then an ordinary suite run found the eighth, spelled out
this time as a marker inside a gate, which went red the moment the step was corrected - a check that
pins how a rule is written fails exactly when the rule is fixed.

It also opens the half of the menu a new cultivator could not see. Reported from live play - *"I
can view only like half the menu"*, and at Body Tempering that was literally 109 of 248 leaves.
**Nineteen status reads were held back from a realm-0 player**, while four separate statements in
the tree say they never may be: rc.32's limit, v1.0.9's changelog, the gate's own docstring, and -
most plainly - a comment in the authoring table reading *"Every page's own status stays"*, with
eleven entries setting one to realm 1 or 2 **in the same block, directly beneath it**. Eight more
were never listed and inherited a page floor. What hid it is that the five status reads named as
examples are exactly the five that were written correctly, so the rule was checked against its own
citations and never against the rest. It is a rule in the generator now rather than a list of
zeroes, because a list is what drifted. Three pages also open at Body Tempering on the owner's
call - what you were born with, the qi body the cultivation card already advertises, and a
companion - taking a new cultivator from 109 visible leaves to 142. The deep end is untouched.

It also fixes a quest that named the one command that could not finish it. Reported from live
play: *"multiple successful hunts and it's not getting completed."* The beginner path's fourth stage
asks for a `combat_win` and labels itself **/world -> Act -> Hunt** - and the hunt recorded no quest
progress at all, while the only thing that reported that type was a finished battle. Three gates
already held that every objective type has a reporter, that no reporter invents a type, and that
none speaks before its command answers; all three are about the type, and the label a player reads
was held by nothing. The hunt reports its win now, which unblocks three quests rather than one, and
a new gate resolves every authored label to the live hub leaf it names and requires that command to
report it - which found a second instance on its first run, a trade objective pointing at Browse
when Browse is a read.

And a travel picker dropped the street a shop door opens onto. `/travel` from inside an apothecary
refused by naming the city - the one destination the engine allows from in there - which the picker
did not offer. The engine had it all along; the ordering read `20 + (n or 50)`, where n is the hop
count, the city you stand inside is zero hops away, and zero is falsy, so the only way out sorted
behind every distant city and fell off the end of a 25-option select. The label one operand to the
left already asked the right question.

It also stops a city repeating one rumour five times. Reported from live play: `/city rumours` in
Ashenwall City printed *"Xie Kormaq discovered Ironbanner City"* over and over, about a route
charted from a gate in another city entirely. There was only ever **one** row - the writer is
idempotent on its source key - but the page asked the history query once per place, and that
query's relevance clause is an OR, so every row about the asking player came back for all eight
places a city and its parts make. The page also performed neither of the two checks that query's
own docstring names RAG as the owner of, so a teller could repeat something only the player was
party to, or something an unwitnessed crime left behind that the world is not supposed to know. A
rumour is public news about this city, told once - and the page stops asking about the player at
all, which is where the copies came from. Fixing it turned an existing check red, because that
check pinned the call's exact text including the argument that was the bug, under a name claiming
it held the very viewpoint rule the call does not apply.

And the ninth and tenth were in one line of prose, which is why every gate written for this number
walked past them: the Reopen card itself said *"went quiet for fifteen minutes"* and *"another
fifteen"*. So the release that made the window configurable made that card wrong for everybody - at
the two hours it shipped, a panel waited two hours and told its owner fifteen. The card reads the
configured window now, and the default goes back to **15**, which is what the card, the README and
twenty-six releases of the harness had all been saying. `120` is still a comfortable
page-and-come-back window for any operator who wants it; what the gate holds is that nothing
restates the number, never which number ships.

It also lets a GM see how many times a player has started over. `character.reset` has reported what
an account has spent of its three restarts since v1.0.1, and that reply was the only place either
number had ever appeared: a player learned how many were left by spending one, and a GM could not
look it up anywhere. The record was never missing - one `event_log` row per reset, deliberately kept
out of the sweep so the bound survives the action it bounds, carrying the abandoned life's name,
path, root and realm - it simply had no reader in the whole of Python. It has one door now,
`character.reset_status`, asked by `/admin player inspect` and by the dashboard's Player Editor.
It is an engine query rather than a count in each surface because the row it filters on and the
limit it is measured against are both the engine's, and a card holding its own copy of the limit
reads "one left" on the day the engine refuses. It answers for an account with no character at all,
which is exactly the state a GM asks about, and an engine that cannot be reached says "unknown"
rather than zero - a zero there reads as "never reset". Writing the gate found the fault it forbids
already in the first draft of the fix, which would have printed "2 of 0" from a partial answer.

It also cuts the wait between cultivation sessions to **30 minutes**, from 180 - the knob that sets
the pace of the whole game, since a realm is about a hundred sessions at every realm. Retuning it
turned up that the number is written three times: the engine's table, `.env.example`, and compose's
own `:-` fallback, which exists because the engine service takes an explicit environment allowlist
and which is therefore the copy a deployed stack actually serves - so a pace changed in Go alone
would have reached nobody running the compose stack. All three move together now and a gate holds
them equal for every wait. Three gates also pinned the value rather than the rule and went red on
the retune; one said in its own comment that it existed so ownership moving would not change the
pace, which stopped being a reason three releases ago, and another bounded a day of closed-door
cultivation at five hand-sat sessions - a count, which is a statement about the cooldown, in a
comment that spends a paragraph explaining that its own predecessor was wrong for exactly that
reason. An aptitude evolution and a dao-partnered
session used to be paced with cultivation and share its key; they keep **180 minutes** and take keys
of their own, because each is a costly gated climb - an evolution risks stability and a forced
mutation, and the rung it reaches prices a whole life's cultivation - and ordinary cultivation
getting faster is not a reason for the rare things to. Unsharing the number meant unsharing the key:
three defaults behind one key are fine only while they agree, and once they differ no value of that
key restores what shipped. A closed-door retreat stays the same share of active play by
construction, so its absolute rate follows the new pace. An operator whose `.env`
already sets the old value keeps it: an upgrade never edits `.env`.

**1.0.12** runs the playtest for the first time in four releases, and it went red on a hundred steps.

The Discord harness presses every leaf of every hub and holds one thing about each: it was drawn and
answered, or the panel hid it and printed a lock line saying why. v1.0.9 gave a page a **third**
state - a door the curriculum has not introduced yet, which prints one collapsed line for the whole
page and no line of its own - and the harness knows two. So 97 of 245 leaves were "neither drawn nor
locked", the run's last step went red, and nothing in CI said so for three releases, because a
harness is a script and not CI. The player is raised past the curriculum's own ceiling before the
sweep now, read off the content file rather than written down, so every leaf is pressed again; the
curriculum itself is asserted first, at realm 0, where it is true.

It also fixes the thing the harness found on the way. Raising a realm through the GM's own lever
answered **"character not found"** about a character the panel three lines above had just drawn: an
action's payload was decoded into `map[string]any`, which turns every JSON number into a float, and
a Discord id is about 1.4e18 while a float carries 9.0e15 exactly. Every id a payload named came
back off by a digit or two - so `admin.player.set_realm`, `karma`, `teleport`, `grant`, `erase` and
the rest of the GM console addressed somebody who does not exist. Player actions were never affected,
because the actor's id is a typed field and never went through a float. The decoder keeps numbers
exact now, and **no reader changed**: `ParseInt` has had a case for this since it was written and
nothing could ever produce one.

And a panel stays open as long as it is told. A hub panel went quiet after fifteen minutes and
offered a Reopen button - a bare `timeout=900` written out in five files. `HUB_PANEL_IDLE_MINUTES` is
the setting, the default is **120**, and `0` means a panel never expires. The command tree's own
tuple got the same treatment one level down: it was an inline literal, so four places read this
file's *source* to recover it and one wrote down how many there were and went stale. It is a name now.

**1.0.11** gives a GM the two levers that only creation had, and stops a server being told about one
release when three went by.

Four deferred items, and the first two are one sentence from two sides: **the writer a human drives
is the one nothing held.** `admin.player.set_spiritual_root` held the grade a GM types to a
hand-written copy of the ladder `content/world.json` carries - six names in a map literal, agreeing
with the file the day they were written, and silently wrong the day a rung is renamed or added.
`admin.player.set_physique` moved a physique's stage, progress and stability and **never its
identity**: the only two statements in the whole engine that have ever written `physique_id` are
character creation and samsara, so a GM could not hand somebody a physique, correct one rolled
wrong, or stage one for a playtest, even though all eight are drawable at birth. Both read the
content file now, the physique goes into the undo snapshot so an undo puts back what a grant
replaced, and the dashboard's two aptitude cards are pickers fed from that file - `gradeOpts` was a
third copy of the ladder, in the browser.

It also walks the releases a server missed. `#updates` compared the marker to the running version
for equality and fetched that one entry, so a server upgrading 1.0.5 to 1.0.8 was told about 1.0.8
and never about 1.0.6 or 1.0.7; the marker jumped across and nothing recorded that two releases went
past unmentioned. Every entry in the gap is posted now, oldest first, capped with a line saying what
is not being repeated - and the marker stops at the last release actually posted, so a send that
fails halfway does not make the ones it never reached look announced.

And 🗺️ Cultivation World is for cultivators. `#player-homes` and `#expeditions` were the last
player-facing category open to everybody, so a newcomer's sidebar advertised rooms they cannot use
directly above the `#begin-here` they are meant to go to - while the capitals, the four world feeds
and the admin rooms were each gated. The deeper half was that no "has a character" role existed at
all, because both realm syncs run from `require_character` and so only ever fire for somebody who
already has one. There is one now, granted at creation before the first private thread is opened,
kept in step by `require_character`, taken off at an erasure - the one moment nothing else can
notice - and backfilled onto existing players by the sweep Full Setup already runs.

**1.0.10** makes the panel header name whoever is actually standing there.

Reported from live play, as two lines that disagreed in the same breath. The Family Hub header said
*"Here the East Gate of Cloudblade City, facing Ironbanner City · Gate Captain Yue Dong"*, and
`/talk`, opened at that same gate, offered **Drillmaster Zhai Kang** - who lives at the Blade Yards
and had been walked to the gate by the world's own simulation.

Both were right about different questions. The header listed whoever the content file records as
*living* at a place, with no schedule and no simulation behind it; the picker asks who is *there*.
So the one line that tells you who is in front of you was the one line not asking. It asks now - the
same resolver `/talk`, `/scene status` and `/sense` all use - and where nothing can answer, it
describes the place and names nobody rather than guessing.

**1.0.9** introduces the game a realm at a time, so the first hour is the first hour and not the whole of it.

Reported from live play: *"it's become complex and overwhelming."* A character three minutes old was
shown every system the game has, at once - 249 doors across 67 pages in 16 hubs, sect politics and
territory war and caravan dispatch and the auction floor sitting beside *cultivate* and *talk*, with
nothing saying which of them were meant for them yet. None of it was refused. It simply was not what
the first hour is about.

139 of those doors now wait for a cultivation that can use them, so a new cultivator meets 110
instead of 249, and the rest arrive as they climb: a companion and a rival and a sect worth asking
about at Qi Refining, a sect's rooms and a party and the auction floor at Foundation Establishment,
the underworld and the roads and a home of your own at Core Formation, ground worth holding at
Nascent Soul, and what a life leaves behind after that.

It also fixes a dead end at the end of the opening. Walking home from the road puts you at your
city's gate - that is what the road does - and the household door then refused you, naming the city
you were standing in as somewhere else: *"the Shen Family household stands in Cloudblade City and
you are in Cloudblade City East Gate - travel there first."* 317 of the world's 477 places are parts
of a town in this way, 92 of them gates, and the last stage of the beginner path asks you to come
home. A gate is its city now, on both sides of the door: the engine admits you and the panel stops
hiding the way in.

Nothing is taken away and nothing is hidden. A page holding doors back says so in one line - how
many, and the realm the next one opens at - and **/locked** lists every one of them with what it
needs, because a road you can see is a road you can walk toward. Every status read stays open from
the first minute, so no system is invisible, only the levers inside it wait. The slash commands all
still work if you type them: this decides what the game puts in front of you, never what it allows.
And starting over is never held back - the player most likely to want it is the one who just found
all this too much.

**1.0.8** gives you back the people standing in front of you, and makes starting over leave nothing behind.

Reported from live play: standing at Cloudblade City East Gate, whose scene card names the gate
captain in the room, `/talk` and `/npcinfo` both answered *"nothing to choose from right now"*. The
picker searched the whole 574-name catalogue, took the alphabetically first twenty-five, and only
then asked which of those were in the room - so it could offer somebody only if their name sorted
near the front of the world *and* they happened to be standing there, which for most rooms is
nobody, and a captain called **Y**ue Dong could never appear anywhere at all. It asks who is here
first now, through the same resolver the scene card has used since rc.28, so the card and the picker
can no longer disagree.

That one wire held up more than conversation. The starting quests stall on it at their second stage,
which asks you to speak to somebody; and the commission ladder runs inside `/talk`, so a player
could be neither offered work nor able to finish it - 137 of the 140 authored commissions carry at
least one objective that is reporting a conversation. Scene actions kept working throughout, which
is exactly why the first stage of the path did and the second did not.

It also makes a reset take your private rooms with it. `/reset` deleted a character's rows across
more than a hundred tables, and four of those rows were the only record anywhere of a Discord thread
the bot had made for that player - the expedition journal above all. The rows went and the threads
stayed, holding the abandoned life's whole scene log, with nothing left in the database that could
ever find them again. Both levers that wipe a player now collect the threads before the rows go and
delete them after: for `/reset` that is tidiness, and for a GM's erasure it is the difference between
removing somebody from the database and removing them from the server.

And the dashboard's own status line tells the truth from every page. The two lines in the sidebar
footer were painted only by the Overview, so opening the dashboard anywhere else - a bookmark, a
reload, a deep link - left them reading their placeholders, `SQLite` and `engine —`, for as long as
the tab stayed open. Neither looks broken, which is why it went unreported: a GM could not tell
`engine —` from an engine that had answered and had nothing to say. The shell reports itself now,
from wherever you are standing, and says plainly when it cannot reach the engine at all.

**1.0.7** gives every world its own age, and makes an era mean something in all of them.

There was one era for the whole game. A Demon Invasion in the Celestial World and a quiet century in
a Mortal village shared a single row, and every rule that asked what age it was got the same answer
whether it was pricing a siege among the immortal courts or a cultivation session in a hill village -
while the realm capitals have been split per world since the fourth schema, the auction floors since
the thirty-fifth, and a world's own news since the fifty-sixth. The era was the last thing in the
game still pretending the four worlds were one place.

Each world walks its own cycle now, on its own clock, so the Mortal World can be deep in a Beast Tide
while the Celestial courts are being audited by heaven. Every rule that asks was already about
something standing somewhere - a cultivator, a territory, a war, a caravan, a fugitive - so each one
simply resolves the world it was already talking about.

And a cycle is a year. It ran five hundred and forty world days before, which matched nothing; a
world year is three hundred and sixty, so each world has six eras of sixty days and the year closes
exactly. Twenty-four ages in all, written for the world they belong to, and they live in the content
file rather than in code, so a GM can rewrite an age without rebuilding anything.

The part worth knowing is what the counting found. Of the eight things an era could change, **four
reached no rule at all** - each appeared exactly once in the whole engine, in its own declaration. So
every era carried one live number and one dead one, and the Beast Tide Era, whose entire identity is
beasts, did nothing whatever to beasts: mechanically it was "caravans are fifteen percent riskier".
Authoring twenty-four eras on top of that would have been manufacturing decoration at scale. A Beast
Tide brings beasts down the passes now, and a Quiet Heaven really does close wounds faster - the
promise it had been making since it was written. The two nobody could wire honestly are authored by
no era at all, and a gate refuses any that tries.

- **Schema 60** gave an era a world to belong to. `world_eras.world` splits one global age into four,
  and it defaults to the Mortal World rather than to nothing: every row that existed when it ran was
  written when there was one era, and that era was the Mortal cycle's first. So a live world carries
  on from exactly where it stands, and the three worlds above it open their own cycle on the next
  tick rather than being handed somebody else's history.

**1.0.6** makes the one promise this world repeats everywhere true in the engine as well as in the bot.

`app/ai/narrator_context.py` tells the narrator, in these words, in two places: *"PROTECTED; violence
cannot mechanically begin here"*. A duel was refused in a protected place by the engine, and
`/battle challenge` was refused by `app/bot/commands/battle.py` - and `combat_actions.go` named
`SafeZone` zero times, so the rule a player could feel for an ordinary fight lived entirely in
Discord. A bound that lives in the client is not a bound, which this tree had already written down
three times for the world clock, the action cooldowns and a Law technique; what hid it here is that
the *neighbouring* kind of violence really was engine-held, so the file next door looked like proof.

And a bounty hunter did not care where you were standing. The pursuit sweep raised pressure, closed
in and captured a fugitive without reading a location anywhere in it - so somebody was taken off the
floor of a hall whose own description reads *"Violence inside is forbidden; the protection ends at
the front doors"*, while the field that says exactly that sat on all forty-eight auction houses and
was read by nothing. It is read now: a hunter may watch you from the doors and may not reach in.
Pressure still rises, because standing still is not escaping.

The two protections are deliberately kept apart. A safe zone is on 446 of the world's 477 places -
it means *not the wilds*, and Greenriver Town, where everyone begins, is one of the thirty-one that
are not - so it stops a fight somebody chooses to start and nothing more. What an auction floor
claims is stronger and rarer, and that is what a fugitive can hide behind. Handing all 446 the
auction floor's guarantee would not give the bounty system a sanctuary; it would end it, because
players live in towns.

And nothing refuses a fight that was never the player's idea: a world event that lands in a town is
still fought, and the ambush outside an auction door still happens, because being caught in
something is not the same as starting it.

It also retires the third way the same sentence was said. `door_rule` was set on every auction house,
meant "the protection ends at the doors", and was read by nothing - and the engine already ends it
there, by standing the ambush outside. A switch no content can turn off is not a switch.

**1.0.5** stops a quest being lost to a failure in drawing the reply, and stops the craft menu
offering methods you have not learned.

Seventeen call sites wrote `await announce_quest_progress(interaction, await QUESTS.progress(...))`,
and eight placed that one statement **after** the command's reply, each with a comment citing the
rule from v1.0.0-rc.28. That rule is real and it is about the *announcement*:
`announce_quest_progress` falls back to `interaction.response.send_message` when the interaction has
not been answered, so a reporter ahead of a command's only reply spends it on the quest line. It
says nothing about the record - and nesting the two made the record inherit the announcement's
position. v1.0.3's `/craft` is what that cost: the reply raised after the engine had committed, so
six pills were made and the errand still read zero. The player reported it as two separate bugs.

It also splits the two everywhere, not only where the order was wrong. `record_quest_progress` is
the record on its own and never raises, because it runs before the reply now; a mixed tree where
some sites nest and some do not is what invites the next author to nest.

And the craft picker offers what the cultivator has learned. It was the whole 33-recipe catalogue
capped at Discord's 25, while `craft.resolve` refuses any method the player does not know - so a
fresh character was shown twenty-five and could make about three. The hub renders that same callback
as a drop-down, which is how "why is crafting a drop-down menu" turned out to mean "a menu of things
I cannot make". An empty picker now names the two doors that end it rather than being a dead end.

**1.0.4** gives a body the one thing this game never had: it mends on its own.

Nothing in the tree restored vitality with time - twelve `SET vitality` statements in the engine,
four of them damage, and not one keyed on rest, cultivation, seclusion or the scheduled tick. Four
pills and one technique were the whole of it. So a cultivator who lost a fight, which is nine
defeats in ten, sat on the number the fight left them with until they bought their way off it, and
somebody with no stones and no pill had no way up at all. v1.0.3 closed the loop on a purchase; this
opens the one that costs nothing but time.

A body recovers a quarter of its own maximum a world day, so the same wound costs the same four days
at every realm and what changes with cultivation is what that quarter is worth. The rate is content,
and an unauthored one heals nobody rather than falling back on a number of the engine's invention.

It also carries the leftover minutes. The anchor moves only by the minutes that actually bought a
whole point, so resting in pieces is worth exactly what resting in one span is - and time spent
already whole does not bank into the next wound.

And it settles lazily, on the authoritative path beside the journey and seclusion checks, rather
than as a step of the world simulation: those batches are daily and sit behind an automation flag a
GM can switch off, and state a player is stuck behind must not depend on one. A fight in progress is
not rest, because the battle row and the sheet are kept in lockstep and mending behind the fight's
back would silently desync them.

**1.0.3** stops a craft taking your materials and telling you it failed, leaves a cultivator who
loses a fight with a heartbeat instead of nothing, and gives the seventh cultivation path the
methods it never had.

The craft was reported from live play as *"it doesn't let you craft but also takes your items"* - by
a player whose bag held six of the pills they had been told they never made. `craftResolveAction`
shipped the flattened `d1`/`d2` and no `degree`, while the reply's `roll_line` reads
`die1`/`die2`/`degree`, so every craft that got past the materials check raised `AttributeError`
*after* the engine had committed: materials spent, output granted, profession XP credited, and a
wiring-failure message on screen. v1.0.1 found and fixed exactly this for the forage reply and did
not carry it forty lines up to the craft.

It also gives a defeat one meaning. `fatalChance` is `min(75, 8+gap*3)`, so against a same-realm
opponent eight defeats in a hundred are fatal and the other ninety-two ended with `vitality` at
zero - while the two *fate-rescue* branches, the rarer and strictly worse outcome, each wrote
`vitality=1` outright. Nothing in this game regenerates vitality with time, so zero was not a state
anybody waited their way out of. All four branches go through one door now, and treating a wound
with a Recovery Pill restores what the pill restores: before this it was spent on the roll and
healed nothing, so one pill did one of two jobs and a player needed two to get back where they
started.

And the Ghost Cultivator can be played. `app/rules/advanced_catalog.py` named six cultivation paths
where `content/world.json` offers seven, so **none** of the 160 manuals named the seventh - while
`death_qi_system`, a whole authored subsystem in content and three hundred lines of Go, opens with
`"path": "Ghost Cultivator"` and exists to serve it. It has 23 manuals now, the same as its
siblings, and the hidden sect now says why when it has no forbidden art of an initiate's path
within their reach, instead of passing the line over in silence.

It also gives all thirteen birth households their own tradition to teach. Eight of them handed a
child the Azure Cloud Sect's or the Jade Meridian Sect's entry manual as the family's own teaching,
and five handed out a generated manual with its catalogue index in the title; there was nowhere
correct to point them, because no authored, sect-less, non-forbidden manual existed anywhere in the
game. Every house has one now, named out of its own authored story, and all thirteen are Mortal
grade - the old split was a permanent nine-percent cultivation difference decided by birth and
stated nowhere.

And the Admin Console's Inventory card answers an unknown item with the item. "Qi Nourishment
Pills" used to suggest five demonic cultivation manuals, because the filter took any single token
hit and then sorted the survivors by id, so the 142 generated `advanced_*` ids won on the letter
'a'.

**1.0.2** puts the three-reset limit back and draws the line between `/reset` and Samsara where it
belongs. They are two systems and only one of them remembers: **Samsara is what death opens**, and it
deliberately carries the memory seed, the talent, law and insight echoes, the legacy points, the craft
echo and a family lineage rolled off the dead life's karma into the next life. A reset keeps none of
that - it is for a life you have only just begun and would rather not have begun, and the soul it
leaves behind starts again at incarnation 1 with nothing behind it. That was already true, because
`soul_legacy` is swept like any other row of the account's, but it was true by accident: a keep added
to that table later would have turned a reset into a cut-price samsara with nothing going red.
`TestAResetIsNotASmallSamsara` is the test that goes red. And the allowance is three per account,
ever - not three per character and not three per life - counted from rows the sweep is told to keep,
because a bound the bounded action erases is not a bound.

**1.0.1** makes a craft say what it needs, and lets a player start over without a GM. Both were
found by playing. A cultivator bought an Inscription slip, read it, and had no way to learn that a
Swift-Wind Talisman wants one talisman paper and one spirit ink - both authored, both sold in dozens
of shops, both foragable. Three facts would each have told them and none reached a player:
`character_recipes` had four writers and **no Python reader at all**, so nothing could say what you
had learned; `get_recipe_definition` has always parsed a recipe's `cost` and no command read it; and
the engine computed the exact shortfall per material and refused with the bare words "missing
materials", which the bot then replaced with a vaguer sentence of its own. The refusal names each
short material and how many now, and `/craft → Profession → Profession Status` lists every method you
know per trade with each input's cost beside what you are carrying - a tick if you can make it, a
cross if you are short, a red mark if your rank is too low. That page also no longer returns early
for somebody who has learned something but not yet crafted, which was exactly the state that
prompted this. All four trades share one resolve path, so it is one fix rather than four; the three
other places that spend an item were already naming it.

The other half is `/reset`, also on `/character → Samsara`. Until now the only way out of a character
was to die, and dying is not a reset: `lifecycle.true_death` has three callers and none is
voluntary, and what it opens is Samsara, which deliberately carries the memory seed, the talent, law
and insight echoes, the legacy points and the craft echo into the next life. The one true wipe was
`admin.player.erase` - a data-protection lever, GM-only, and the wrong verb for "I misclicked".
`/begin`'s refusal names the new door, because that refusal is the message somebody who wants to
start over actually reaches. It reuses erasure's own sweep, so a reset removes exactly what an
erasure removes and the two cannot drift. What bounds it is not a clock: a reset is refused the
moment any row with an **anonymise** disposition names the character - a battle the world remembers,
a sect other disciples belong to, a gate still standing over a named town, a grave somebody reached
first - because those rows survive even an erasure and so cannot honestly survive a reset. There is
deliberately no limit on how many times: the world-mark rule is what protects other players, and a
cultivator re-rolling their own first minute takes nothing from anybody. It is still recorded, in
the one row the sweep is told to keep, so a GM can see how often somebody has started over.

The reset's finding is one step further in. It is the first action in this tree whose **actor erases
itself**, and the authoritative framework keeps its own bookkeeping under that actor's id: it reads
the state version before the switch and advances it after, so a sweep that took the version row with
everything else left the engine unable to record the action that had just succeeded, failing with
`stale expected_version: expected 2 current 0` after doing all its work. The engine's record of a
request is kept out of the sweep for the same reason `admin_audit_log` is kept out of an erasure.

It also gives the martial clans somebody real to deal with. `martial_clan_relations.partner_family_id`
is foreign-keyed to `birth_families` and nullable, and the one statement that had ever inserted a row
wrote it `nil` - because the partner it named was invented off a list of surnames: a house with no
members, no town, no wealth and no opinion. So a world held one relation per household, with somebody
who does not exist, from the day it opened, and the only thing that could ever happen to it was the
plus or minus one a tick the `clan_dynamics` batch applies. Four households meant four relations for
ever. `clanDiplomacy` is a step of that batch: two real households in one world, neither already
dealing with the other, sign what their wealth, weight and alignment imply - a trade pact, an
alliance, a marriage pact, or a rivalry where the gap is too wide - written from both sides, at an
opening score both bootstrap and diplomacy now read from one map, with the treaty recorded once in
`world_history_events`. A blood feud is still `combat_aftermath`'s alone, because a feud comes from a
body. `trade_pact` was also the one seeded type the drift CASE did not name, so it had sat at exactly
25 since the world started; it warms like everything else a house signs. No schema.

It also stops the next release spending the live pass. `docs/playtest/v<version>.md` is named after
`RELEASE_VERSION`, so across fifty-nine release candidates the filename never moved and every tick
was carried; 1.0.0 -> 1.0.1 is the first bump that renames it, and there the generator wrote a fresh
checklist with every box blank. The ticks are inherited from the newest older checklist now, and
each carries the release it was walked on - the person writes `[x]`, the generator dates it - so a
row walked on 1.0.0 and not re-walked reads `[x] v1.0.0` rather than claiming a pass that never
happened.

And it holds a class that had been producing findings by hand. rc.55 found two content fields parsed
out of `world.json` and read by nothing; rc.58 built that gate for modifier stats and not for the
fields. `field_readers_test.go` walks all 439 parsed fields across 63 structs and requires each to be
read through a selector - never a substring, and a composite-literal key is not a read, because
writing a field is not reading it. Four are read by Python for display and are named with the file
that prints each; three have no reader at all and are named with the decision each waits on.

And one assertion in the playtest was only ever green by luck. The engine harness's
world-crossing step read a conversion remainder as `int(... or -1)`, and a purse that divides
exactly leaves a remainder of zero - which is falsy, so `or` swapped the right answer for the
missing-value sentinel and the step failed its own range check. It had passed every earlier run
because the run's accumulated wealth had never landed on a round number. Three more of the same
were waiting in that file, one of them on the zero-conversion the currency ladder is documented to
produce. All four go through one helper that asks whether a field is absent rather than whether it
is falsy.

**1.0.0** is the first release with no suffix on its tag, and it is rc.59's tree unchanged: no code,
content or schema moved between the two, so an operator already running rc.59 has nothing to install.

What 1.0 means here is narrower than the number invites, and is worth stating plainly. It means the
game is whole end to end - a cultivator is born into one of thirteen households, taught a trade,
given a path out of the door, and can walk four worlds and thirty-two realms before dying into a
samsara that carries an echo of what the hands learned. It means the authority split holds: Go owns
the rules, the clock and the only connection to SQLite; Python owns Discord and the dashboard; and
the AI narrates outcomes it never decides, falling through to procedural prose when every route
fails. And it means the two playtest harnesses drive **every** operation the engine allows and
**every** leaf the hubs register, held there by a gate that fails the day a new one is added
uncovered - so a mechanism that reaches nobody is a failing test rather than something waiting to be
noticed.

It does not mean the world is finished. `docs/TODO.md` is the list of what is deferred
and why, and it is not short.

Fifty-nine release candidates got here, and the ones worth remembering are not the features. rc.43
found `/learn` unreachable for twenty-three releases - authored, priced, implemented, on no page and
in no command tree. rc.50 found the one item of 287 the world could not produce. rc.56 found a
constant named for a rule that was spent in a way that applied nothing. rc.57 could not bootstrap a
fresh database at all, and the harness found it on the first run anybody gave it. The recurring
fault in this codebase was never a broken mechanic; it was a finished mechanic with one wire
missing, and the reason so many were found is that each release wrote a gate that fails when the
wire is pulled again - drilled against a deliberately broken tree, because a gate that has only ever
passed is decoration.

The fifty-nine release candidates that led here are in
`docs/history/CHANGELOG_1_0_0_RCS.md`, entry by entry as they were written.

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

## Release status — v1.4.1

- Current release: v1.4.1 - a GM can finish or advance a player's quest from the Player Editor's new Quests card, through the same path a player's own report takes (see the changelog). Built on v1.4.0 - a GM updates the server from the dashboard: an audited request, a watcher on the NAS running `update.sh --upgrade`, and the outcome on the card (see the changelog). Built on v1.3.5 - command use is counted (schema 64) and the ten most used commands are on `/admin server observability`; nothing is reordered for it (see the changelog). Built on v1.3.4 - the punch list cleared: the Stygian Ghost Scripture is the Ghost Cultivator's high manual and its inheritance reads `preferred_paths`, a path's skill is on the sheet, two dead era keys deleted (see the changelog). Built on v1.3.3 - the eight open rule decisions settled: a Law control technique weakens its opponent (schema 63), a clan treaty that runs out ends and leaves a rivalry, the auction-door ambush is on the house's doorstep, five entries closed with reasons (see the changelog). Schema 63. Built on v1.3.2 - the daily five are one step each: `/cultivate`, `/explore`, `/hunt`, `/forage` and `/mine` are slash commands and a row of five buttons on the menu (see the changelog). Built on v1.3.1 - six rules the bot held are the engine's (a sponsor's presence, the sects a gate justifies, a city's board, a territory's ground, the forage wait, the quest chain catch-up on any action) and three more buttons are drawn only where they work (see the changelog). Built on v1.3.0 - ten of the owner's decisions: a failed craft returns half its makings, a hall teaches only what its world can make, the Nine-Echo Sword Wraith is a secret floor beneath its realm, `/reset` asks with the count, the Qi Body card shows the pool at every realm, thirty-three upper-world send-offs, no property inside a household, `heart` retired, the Starfall hall's buy line lowered (see the changelog). Built on v1.2.3 - the review's eight deferred claims settled: Vacuum refuses at once behind a half-written action, one array rule at both cultivation doors, one counter-attack TN, an undo undone again, a rank price under a merchant's wares, and a lot listed in its house's coin (see the changelog). Built on v1.2.2 - the trades' ranks are the Nine-Tier ladder: Unranked, then Tier 1
  Apprentice to Tier 9 Sovereign with each trade's own word in front (Pill, Forge, Talisman, Array,
  Herb, Ore, Beast, Artifact, Treasure). No schema.
- v1.2.1: a beast can always evolve, a homestead can be upgraded in any world, and a dozen smaller wires from a deep review (see the changelog).
- v1.2.0: the first hour is a short list: everything outside cultivate, break
  through, explore, buy, craft, forge, gather, hunt, mine and quest (plus a companion and the body
  path) waits for Foundation Establishment at the earliest; the menu leaves off the hubs with no lever
  open yet and names the tutorial's next step; `/mine` is the ore half of gathering; the beginner path
  gains "Iron from the Seam" and "The First Gate"; a road is walked in the telling
  (`TRAVEL_TIME_PERCENT`, default 0); the trades' ranks are Unranked, Grade 1-5 and Saint; the qi body
  and the Laws open at the Spiritual World. Schema 62.
- v1.1.0: a new cultivator can join a sect: a recruitment delegation speaks for a
  real sect and its elder can sponsor, the envoys' hall and the delegation's trial put a sect's gate
  on the travel list, Recommendation and Trial open at realm 0, a sect's entry-level work is open to
  outsiders and pays standing with it, and a recommendation's bonus rides both trial rolls; and a
  button refused only because of where the player stands is drawn as a locked line saying where it
  works. Schema 61.
- v1.0.17: a rank in the trade that makes an item adds 2 of the shop's coin per rank
  above Novice to what a keeper pays for it, never reaching the cheapest shelf price in that coin;
  shelf prices and raw materials unchanged. No schema.
- v1.0.16: every condition treatment lowers the severity (one level on a failed
  roll, two on a success, three on a strong one), the roll is against 10 + severity, and the
  condition being treated no longer counts against its own cure. No schema.
- v1.0.15: the Heart Calming Pill asks for a Moonveil Herb rather than a fruit
  the Mortal World never offers, the Mortal World's town apothecaries shelve that herb, a refused
  craft names where each missing material is sold from where the player stands, and a gate holds
  that every world selling a method offers what the method needs. No schema.
- v1.0.14: a character reset releases what the world keeps instead of refusing
  over it (private history goes, public history and shared things stay naming an unknown
  cultivator, a player family passes to its heir), erasure no longer cascades a founder's family
  away, and the GM's restart-allowance readers answer "unknown" when the engine is unreachable.
  No schema.
- v1.0.12: the Discord playtest can see the curriculum again and presses every
  leaf, a GM lever addresses the player it was given rather than an id a float rounded off, and a
  hub panel stays open for as long as `HUB_PANEL_IDLE_MINUTES` says. No schema.
- v1.0.11: a GM can grant a physique and can only set a root grade the ladder
  carries, `#updates` walks the releases a server missed instead of jumping the marker across them,
  and 🗺️ Cultivation World is gated behind having played. No schema.
- v1.0.10: the panel header names who is actually standing there, instead of
  whoever the content file says lives there. No schema.
- v1.0.9: the game introduces itself a realm at a time: 139 of its 249 doors wait
  for a cultivation that can use them, a gated page says how many and when, and `/locked` lists
  every one. No schema.
- v1.0.8: the NPC picker offers whoever is standing in the room rather than
  whoever sorts first in the world, a reset takes the player's private threads with it, and the GM
  dashboard's status footer reports from every page. No schema.
- v1.0.7: every world keeps its own age, a cycle is exactly one world year, and
  four era modifiers that reached no rule are wired or refused. Schema 60.
- v1.0.6: "violence cannot mechanically begin here" is held by the engine and not
  only by the bot, and an auction floor is a sanctuary a bounty hunter cannot reach into. No schema.
- v1.0.5: a quest is recorded before it is told, and the craft menu offers only
  methods you know. No schema.
- v1.0.4: vitality recovers with time, which nothing in this game had ever done.
  Schema 59.
- v1.0.3: a craft no longer eats the materials and reports a failure, a lost
  fight leaves a heartbeat, the seventh cultivation path has methods, and every birth household
  teaches its own. No schema.
- v1.0.2: `/reset` is bounded at three per account again, and the line between it
  and Samsara is now held by a test rather than left to hold by accident. No schema.
- v1.0.1: a craft that says what it needs, a player who can start over without a GM, and martial
  clans with somebody real to deal with. The first patch release: no schema. What 1.0 does not mean
  is still written down: `docs/TODO.md` is the deferred list, and it is not short.
- v1.0.0: the first release with no suffix on its tag, and rc.59's tree unchanged. Fifty-nine release
  candidates, schema 58, and two harnesses that between them drive every operation the engine allows
  and every leaf the hubs register.
- v1.0.0 (rc.59): eight ordered categories a player can read top to bottom, the re-parent that makes
  a layout change reach a server that already exists, and `#updates`, where the bot announces its own
  release notes. Merged to `main` and never tagged on its own - it is the tree v1.0.0 is cut from.
  (rc.58 was the same. Both went green and merged while the newest tag stood at rc.57, so neither was
  ever built into an archive. A release that is never tagged is a release nobody can install, which
  is worth knowing before reading a merged rc as a shipped one.)
- v1.0.0 (rc.58): the modifier vocabulary nothing was holding - seven authored stats
  that reached no rule, the Law of Space capstone that named an effect nobody wrote, and the purge's
  flame its own content had always described.
  (This line stood at rc.16 for forty-two releases. `test_release_version.py` only ever asserted the
  `v1.0.0` prefix, so nothing caught it, and it is the line an operator reads.)
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
- **Schema 59** gave a body an anchor to mend from. `characters.vitality_recovered_game_minute`
  is the world minute a cultivator's vitality was last settled at; `updated_at` could not serve,
  because it moves on every write, so a player who did anything at all would have reset their own
  healing. NULL is "never settled" and banks nothing - there is no honest way to say how long
  somebody has already been hurt - so an upgraded world starts the clock on each character's next
  action rather than paying out for the time before the column existed.
- **Schema 58** gave a server somewhere to be told what changed. `server_config.updates_channel_id`
  is the ninth base channel, `#updates`, and `announced_release` is the release this guild has
  already been told about - so the bot's own announcement is idempotent across restarts by
  construction rather than by a flag somebody has to remember to reset. The row is the memory, which
  is what `(user_id, quest_key)` does for the beginner path. A guild whose marker is NULL is a fresh
  install: it records the running release and says nothing, because a server being set up today does
  not want forty paragraphs of history. Both join the `NULL` list in `clear_discord_bindings`, so a
  torn-down server is a new one and is told nothing either. Neither column is in the base DDL - the
  rule rc.57 exists for.
- **Schema 57** put a retreat's deadline on a real clock. `seclusion_sessions.ends_real_ts` is the
  wall-clock moment the doors open, beside the `ends_game_minute` that was the only end before it.
  A retreat lasts at most two real hours now, and a bound expressed in game minutes could not hold
  it: a GM changing the world's time scale would silently re-size every retreat already under way,
  while what a rate change should move is how many game minutes that same wall-clock covers.
  `ends_game_minute` stays, recomputed from the real deadline on every settle so the index and the
  cards see the same end the payment used. There is no backfill: a retreat in flight at the upgrade
  has a NULL here and keeps the length it was started with, because a new rule must never shorten
  something a player already committed to.
- **Schema 56** gave each world its own news channel. `world_event_channels` is one row per guild
  per world - the channel, its category, and nothing else, because an events channel belongs to a
  world rather than to a place in it. It is `realm_hub_channels`' shape minus `location`, and it
  keeps the `UNIQUE(guild_id,channel_id)` the capitals carry and the auction table deliberately
  drops: there one channel serves forty-eight houses, here one channel is exactly one world. The
  `world-events` base channel is not retired - the weekend gift, a GM's world-reset notice and the
  dashboard's test post have no world and never will, so it stays as the global feed and as the
  fallback for any place the location catalogue does not carry.

- **Schema 55** gave two orphan quests their doors. `road_to_a_sect` had been seeded on every boot
  since v0.23.1 and the string appeared in exactly one place in the tree - its own definition - so
  nothing could hand it over; it is `beginner_lesson`'s `follow_on` now, and because
  `sync_commission_pool` is insert-only on purpose (a GM's edit survives every restart) the content
  change reaches new worlds only, which is what this migration is for. Only a stage whose chain is
  still empty is re-pointed, so a GM who already chained it is obeyed. `first_steps`, which rc.26
  superseded with `beginner_household` without retiring, is dropped from the seeded catalogue and
  the rows already written are marked retired - except one somebody is somehow holding, because
  retiring a definition must never take a quest out of a player's hands.
- **Schema 54** raised `world_crossings`, where a survived world-crossing tribulation leaves its
  mark. One row per gate a cultivator has anchored: where it stands, the two worlds it joins, the
  terminus and fare it borrows from the authored crossing for that pair, who tore it open, and the
  cultivation they tore it at - which is what decides which of the world's own people can follow
  them through.
  Keyed on the location rather than on the opener, for the reason a robbed grave is keyed on its
  claim minute - `opened_by_user_id` anonymises on erasure, so keying meaning to it would let an
  erasure unmake a gate. The CREATE is the whole migration: no gate has ever stood anywhere, so
  there is nothing to back-fill.
- **Schema 53** reconciled the purse with its mirror. A player's stones live in `currency_wallets`
  and in `characters.spirit_stones`, and eleven writers moved one without the other - `trade.accept`
  worst of all, moving stones between two players and naming `currency_wallets` nowhere. Every writer
  goes through `walletDeltaTx` now, so the drift had to be settled before the purse became the one
  thing read: the wallet is set to the greater of the two and the mirror is then set from the wallet.
  Upwards deliberately - neither store is the complete record, and of the two ways to be wrong,
  handing somebody stones they might not have earned beats taking a fortune off a player who did
  nothing wrong. A character with no wallet row is given one, the same shape as the rc.15 back-fill.
- **Schema 52** retired the five Python-written catalogue mirrors. `catalog_locations`,
  `catalog_npcs`, `catalog_recipes`, `catalog_manuals` and `catalog_techniques` were blob tables
  (`name`, `data_json`, `updated_at`) that boot rewrote at roughly 1,800 upserts a time, and schema 51
  kept them one release so a rollback to 50 would find them intact. That window has passed: every
  reader names its `content_*` table directly, the mode switch that picked between the two is gone,
  and nothing references the mirrors by foreign key so the drop fires no cascade. Their historical
  CREATE statements stay in migration 12's neighbourhood the way migration 44 left the unused core
  ledger's - a historical migration is how an old database walks forward - and the baseline no longer
  makes them at all. `sync_world_catalog`, their only writer, is `seed_world_territories` and does
  what it always did besides the mirroring: a territory node per location, and the baseline era. The
  no-engine path that the mirrors served is a test fixture now (`tests/support.seed_content_tables`),
  because in production the engine is the only thing that may write `content_*`.

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
