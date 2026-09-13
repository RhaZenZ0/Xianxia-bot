# Xianxia RP Discord Bot — Version History

The changelog, one paragraph per minor. The per-release entries as they were written are in
`docs/history/CHANGELOG_0_18_TO_0_40.md`, and the earlier per-release notes (`V018_RELEASE_NOTES.md`
… `V023_RELEASE_NOTES.md`) beside it. `README.md` describes the current release.

## Changelog

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

- Current release: v1.0.0 (rc.15): the spiritual sense reads the ground it is standing on, the two
  middle readings it could give become reachable, and it stops asking the world for minute zero.
  Tagged `v1.0.0-rc.15` on the beta channel; the NAS drills and two quiet weeks make it `v1.0.0`.
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
