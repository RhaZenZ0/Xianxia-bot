# Xianxia RP Discord Bot — Version History

The changelog, one paragraph per minor. The per-release entries as they were written are in
`docs/history/CHANGELOG_0_18_TO_0_40.md`, and the earlier per-release notes (`V018_RELEASE_NOTES.md`
… `V023_RELEASE_NOTES.md`) beside it. `README.md` describes the current release.

## Changelog

**1.0.0** (rc.57) makes rc.56 installable. It could not bootstrap a fresh database:
`seclusion_sessions.ends_real_ts` was declared both in the base schema script and in migration 57's
`ALTER TABLE ... ADD COLUMN`, and the base script runs first, so a new world created the table with
the column already on it and the migration then died on `duplicate column name`. Upgrading an
existing world worked - there the `CREATE TABLE IF NOT EXISTS` is a no-op and the ALTER has
something to do - which is why nothing in the field would have caught it either. All 59 ADD COLUMN
migrations before it follow the rule it broke: a column a migration adds is the migration's alone.

The suite passed because the migration runner's guard against exactly this named
`sqlite3.OperationalError` only. That is what local aiosqlite raises, and every pytest fixture uses
the local transport; production goes through the Go engine, which wraps the same SQLite message in
its own error type. The guard was live in every test and dead in every deployment. It catches both
now, and a new contract test holds the convention itself, per table, with a self-check that fails if
its reader ever stops finding columns.

The engine playtest found it on the first run rc.56 was ever given, along with the other half: two
loops in the perfection leg still spent the caller-supplied cooldown rc.56 removed, and now ask the
GM lever like every other bounded loop there.

**1.0.0** (rc.56) shuts the doors a retreat has always promised to shut, bounds how long they stay
shut, and takes the numbers that decide both away from the caller.

Four faults, and they are one feature. **The wait between actions was the caller's, in fourteen
places.** `cultivation.train` read `cooldown_seconds` off the request payload with a `<= 0 -> 300`
fallback, and thirteen other actions did the same - seven of them with no floor at all, so a caller
sending `0` served no wait whatsoever. The value lived in `app/ops/config.py` and was mailed to the
engine on every request, which is exactly the fault `rejectCallerGameMinute` exists to refuse: a
bound that lives in the client is not a bound. Both playtest harnesses proved it was reachable -
they sent `cooldown_seconds: 1` to drive their loops - and they ask the GM lever to clear the waits
now. The engine owns them, in one table, with the six `*_COOLDOWN_MINUTES` keys as the `.env`
baseline the way `WORLD_TIME_SCALE` has been since rc.39; compose had to be given them, because the
engine service takes an explicit allowlist and a key it is not given is a key it cannot read. An
action the table forgets waits an hour rather than nothing. `minutes_per_day` was refused with them:
a unit of account is the same kind of number as a wait.

**What a retreat is worth was stated in a constant that did nothing.** `seclusionDailyShare = 0.60`
is named for the rule - "around 60% of an active cultivation day" - and was spent as
`daily * seclusionDailyShare / 0.6`, which is exactly 1.0. The rule really lived in an uncommented
`seclusionSessionsPerDay = 1.2` one file away, and a count of sessions only means a share of active
play at one world time scale: at the shipped 4 it happened to be 60%, at 2 it was 30%, and at 8 it
was 120% - an operator who sped their world up made closed-door cultivation strictly better than
playing, while it asked nothing of the player, and nothing anywhere said so. The share is stated
once now and the rate derived from the cooldown it is a share of, so it holds at every scale. It is
125%: a premium, because the doors are shut and that is the trade.

**There were two settles, paying different rates.** The background sweep carried a second
implementation with a pre-rc.5 flat rate, its own day, its own clamps and none of the multipliers
rc.55 added - so which rate a retreat was paid at depended on whether the sweep reached it before
the player came back. It had no tests at all, which is how the two drifted twenty releases apart.
There is one settle now, in the package that owns the rule.

**And there was no cap, so there could be no lockout.** `duration_game_minutes` was floored at 1 and
bounded by nothing; the only limit in the game was a `1-365` range on a slash command, which any
other caller simply did not have. A retreat is two real hours at most now, refused rather than
clamped when more is asked for, on a real deadline (schema 57). That forced the settlement unit: two
real hours is a third of a game day, so under the old whole-day accounting a retreat run to its own
cap would have paid exactly nothing - it is paid per completed game hour. And with a bounded retreat
the lockout the panel has promised since v0.30.0 is finally safe to enforce. The engine refuses the
authoritative path; the bot holds the four doors a player has, because a read never reaches it. Two
rules make it safe: the gate settles and ends an expired retreat rather than refusing on it, so a
lockout on state only an action can clear is never a deadlock, and `seclusion.settle` is always
exempt, so the way out is never shut.

**1.0.0** (rc.55) makes a spiritual root's grade worth what the content always said it was, and
gives a retreat the multipliers a session has.

`spiritual_root_system.grades` is six rungs and every one has carried two mechanical numbers since
it was written - a `cultivation_mult` from Mortal's 0.88 to Immortal's 1.34, and a
`breakthrough_bonus` from -1 to +3. Both were parsed into `worlddata.RootGrade` and **read by
nothing**: `BreakthroughBonus` appeared exactly once in the whole engine, its own declaration.
`RootGrade` has eight fields, and the six that decide how a root is *made* - the roll band, the
element chances, the mutation chance, the realm a grade may evolve at - were all read. The only two
that decide what having it is *worth* were the two that were not. On a 2d10 breakthrough at TN 13
the content says 28% for a Mortal root and 64% for an Immortal one; every grade was 36%. And this
was not only a creation roll: `aptitude.evolve` lets a player climb that ladder a rung at a time,
paying stability for a failure and risking a forced mutation, and the whole payoff of the climb was
those two numbers.

What the grade was worth instead was one flatter restatement in another system - a per-rung term of
0.02 kept under `elemental_qi_system`, folded into the **element** multiplier. So the authored 1.52x
spread was live as 1.10x under another system's name, and two things followed that no reading of the
content file would show. It was hidden: `/cultivate` prints the element line only when the relation
is not indifferent, so for most cultivators the one thing their root did was applied and never
shown. And it switched off when no method was practised, because `absorptionFor` returns early on an
empty element, before the root term - no method, no root bonus at all. Elemental qi is the relation
between a root's *elements* and a *method's*, which is exactly why what a root is worth on its own
never belonged in it. `rootWorthMultiplier` is the one statement now, purity moved to the root
system with it, and `element_mult` is the relation and nothing else - so an indifferent element
really is x1.00 and the surface hides nothing.

A grade the ladder does not carry is worth 1, never the bottom rung. `gradeIndex` answers 0 for a
name it does not know, and `admin.player.set_spiritual_root` writes that column with no check
against the ladder, so the careless reading hands an unknown grade Mortal's 0.88 and -1 - the
`seller_user_id=0` lesson, that a fallback which looks like a value is not a sentinel. The real case
was in this repo's own fixtures, where the canonical character was seeded `'Heavenly'`, a name no
rung has, for releases - harmless only because nothing read a grade for anything.

Python stopped inventing. `aptitude_effects` multiplied the grade by invented purity, mixed-element,
compatibility and stability factors and published the product - and every one of its eight callers
discards the aggregate and the rows never reach `active_effects`, so that arithmetic was the only
statement of the rule in the tree and it reached no mechanic. The honest bar is not "stated once" but
**one authored number, two readers that agree**, and the gate holds them equal at every rung and
purity. Its `path` argument, unread the moment the invented compatibility factor went, is gone with
it - a release about fields nothing reads does not get to leave one behind.

Seclusion is the same question answered twice. `seclusionDailyGainGo` calls itself "the one copy of
the background-cultivation rate" and applied six of the ten terms a hand-sat session applies,
ignoring the effect multiplier, the era, the method practised and what the root makes of its qi - so
a cultivator gathered at one rate sitting down and another behind a closed door. It carries all five
now, through a loader that never fails, on one parameter so the start projection and the settle
payment cannot drift. What is still left out is left out on a rule: a retreat carries what holds for
its whole length, so the hour of the day averages away, a qi storm is momentary, and the manor array
stays out because `environment_mult` already says where the cultivator sat. Its duration and its
lockout are deliberately not here: `duration_game_minutes` is floored at 1 and bounded by nothing,
the 1-365 day picker is presentation-only, and the start message already promises the player that
"any state-changing command will remain locked" while the engine blocks exactly one thing behind a
closed door. A lockout, a cap and the finer settlement a cap needs are one feature, and a different
finding from what a day is worth.

`spiritual_root_worth_test.go` drives real sessions and a real breakthrough rather than reading the
source, because a grep cannot see a disabled condition, and
`test_root_grade_is_worth_something.py` holds the content and the display twin. Four drills against
broken trees - the term removed from the session, the bonus removed from the roll, the root's worth
put back inside the element multiplier so it is paid twice, and the off-ladder grade returned to the
fixture - each failing with the message it exists to print, among them *"an Immortal root and a
Mortal root both gathered 85 - the grade on the sheet does nothing"*. The gate caught its own author
twice, refusing a comment that named the retired key and one that named the deleted function; both
now forbid the declaration rather than the word. No schema change.

**1.0.0** (rc.54) gives the upper worlds somewhere to go, and something to bring back.

Eight secret realms covered thirty-two realms of cultivation, and they were not spread evenly: the
Mortal World had four, the Spiritual two, and the Immortal and Celestial Worlds **one each**. One
place served all eight realms of Immortal cultivation. Five new realms make it four, three, three,
three - and **not one new location was written**, because the world already had the ground: every
world carries three `road_site: "ruin"` legs and only one or two had anything under them. Last
Lantern Ruin, Ash Gate Ruin, Cracked Altar Ruin, Buried Court Ruin and Nine Pillar Ruin were
authored places with nothing in them, which is the shape `/learn` (rc.43), the quest journal
(rc.46), the event bands (rc.49) and the peach (rc.50) all had.

Each realm is built from what its ruin already says. The TN and reward ladders are read off the
realms each world already had (Spiritual 16-22, Immortal 19-25, Celestial 22-28, +2 a room), the
room drops are existing tier items, and the floors mirror the Mortal spread so the rc.53 ceiling
leaves a wide window. One inheritance and one opening event apiece, at the flat weight rc.53 set.

**And what you bring back is kept.** `ItemUse.DurationGameMinutes` has meant "0 does not expire"
since v0.21.0 - the writer stores NULL and every reader is `ends_game_minute IS NULL OR
ends_game_minute > ?` - and **no item in the catalogue had ever set it**: every effect the game
shipped ran 120 to 360 minutes. Each new realm's last room can yield one treasure granting **+1 to
the attribute that room's own trial tested**, permanently, so what the realm asked of you is what it
leaves you better at. One point, not three, because it never wears off, and it feeds
`canonicalAttribute`, which is the basis of every scene check, craft roll and trial in the game.

They are rare finds in rc.50's sense, in the last room, ordered against the two already placed - the
peach at 12,000 for 6% and the ring at 40,000 for 4% - so the dearer find is the rarer one, which
rc.53's gate holds. All five are `auction_interest: legendary` with a door risk, so a player who
would rather sell one than drink it feeds the system rc.50 first lit.

`permanent_treasure_test.go` drives a use and reads the row back a world-year later; its drill is the
trap the field invites - a writer that stores `0` instead of NULL reads as expired the instant it is
drunk, and the test fails with `ends_game_minute is 1000, not NULL`. The content gate holds the
shape: permanent, one modifier, +1, an attribute the engine actually rolls, and matching the trial.
**The gate caught its own author** - the realm script wrote `max_realm_index: null` on the two
Celestial events instead of omitting the key, and rc.53's ceiling test failed on it.

**1.0.0** (rc.53) settles where a secret realm's band belongs, and stops the shallow ones crowding
out the deep.

rc.52 deferred an item that read *"the nine `secret_realm` unexpected events still carry no realm
band"*. **The premise was wrong on two counts.** There are twelve of them, not nine - three
(`ancient_ruin_appears`, `spatial_rift`, `forbidden_zone_opens`) do not start with `secret_`. And
they need no band: `eligibleUnexpectedEvents` has a second branch for `kind: "secret_realm"` that
reads the **realm's** own `min_realm_index` and `location`, so a realm-0 cultivator has never been
able to draw the Hollow Throne Vault. Putting `min_realm_index` on the events would have been a
second statement of a rule the realm already owns, free to drift from it - the fault rc.39 removed
for the world clock and rc.44 for the world currencies. `test_beginner_world_events.py` now holds
that no secret-realm event carries one.

**What was actually open was that nothing drove that branch.** rc.49's `beginner_events_test.go`
fixture is all `world_event`, so deleting any of the three conditions failed no test - and the whole
point of rc.49 was that a grep cannot see a disabled condition. Three behavioural tests drive it
now, each drilled by disabling its condition. One of those drills is worth knowing: **`!ok` is
belt-and-braces rather than the guard.** A missing realm yields the zero value, whose `Location` is
`""`, and no character stands at `""` - so the entrance check already excludes a typo'd
`secret_realm_id`, and disabling `!ok` alone leaves the test passing. Both have to go. The test says
so rather than implying otherwise.

**Every realm now opens as often as every other.** The draw only offers a realm to somebody standing
at its entrance, so the weights compete per realm rather than globally - and they were lopsided:
seven of the twelve events opened the three Mortal-floor realms, which are also the only three that
sell a key, at 4/5/4 weight, while every realm from floor 2 up had one event at 2. **Deep realms were
harder to reach and opened half as often.** Each realm totals 3 now; a realm with several fictions
(three open the Stygian tomb) keeps them and splits the weight, because that is variety rather than
three times the chance. Total draw weight moves 124 -> 125, so the surprise rate is unchanged.

**And a realm fades once you have outgrown the world it stands in** - Mortal realms at 7, Spiritual
at 15, Immortal at 23, and the Celestial one never, because nothing is above it. rc.50's own
reasoning is why this matters now: a realm's rooms are walked again on every run, and `rare_items`
put a 12,000 peach in a floor-2 realm and a 40,000 ring in a floor-16 one, so without a ceiling a Dao
Saint farms the Salt King's Barrow for peaches for ever - the "guaranteed, repeatable payout" rc.50
exists to avoid. It gates the **draw** only: a key still opens its realm and a GM can still spawn
one, the same asymmetry rc.49 drew between being handed something and walking into it. One
consequence worth stating plainly: the Salt King's Barrow sells no key, so above realm 7 the peach
is reachable only by a GM - which is consistent with rc.50 calling fifty years "enormous low down and
worthless high up", but it is a narrowing.

The ceiling is computed from the realm's floor in the gate rather than read off the events, so the
content cannot quietly disagree with the rule it is supposed to follow.

**1.0.0** (rc.52) gives each world its own news, and says so in every channel.

`world-events` carried all four worlds. A Demon Invasion in the Celestial World and a caravan over
the bank in a Mortal village landed in one feed, in front of everybody, whatever they could reach -
while the capitals have been split per world since schema 4 and the auction floors since schema 35.
**🌠 World Events** is the fourth category, holding `#mortal-world-events`,
`#spiritual-world-events`, `#immortal-world-events` and `#celestial-world-events`, each gated by the
realm **access** role (you have reached that world) rather than the presence role (you are standing
in its capital this minute), which is the same rule the auction floors use and deliberately not the
capitals'.

**The data was there the whole time, and half the wire with it.** `world_events.location` is
`NOT NULL` on every row, and `_event_scene_location` - the resolver that turns an event key into a
place - already existed and was **already called by both announcement writers**, about fifty lines
*after* each had posted. Moving that call above the send is the entire routing change. And
`event_threads.announcement_channel_id` is written at announcement time, so the "event closed"
notice lands wherever the announcement went and **needed no change at all**.

**The global channel stays, and that is the design.** Four writers have no world and never will -
the weekend gift, a GM's world-reset notice, the dashboard's test post and the channel's own blurb -
and `world-events`' own spec string already said *"Global cultivation-world announcements"*. It is
also the fallback, which is what makes the change incapable of breaking a writer: the worst case is
the channel an announcement already used. **The fallback is "no world", never "Mortal World"** - a
private residence, an inner world, an abode or a literal `Unknown` is not in the location catalogue,
and the `or "Mortal World"` default every other site in the tree uses would have filed somebody's
household news as that world's public news.

**Every channel's text was rewritten, and the `#xianxia-info` guide with it.** The blurbs still
described the v0.19 server; the guide said *"main realm-capital channels remain shared social
spaces"*, which stopped being true in v0.21.6. The guide is eleven topics now, three of them new -
**The Server** (what the four categories are and which are gated by what), **World Events** (a scene
has a finite site, so arriving first is worth something) and **Crafts & Professions** (slips,
examinations, and that your household teaches one trade and the head of the house can qualify you in
all four). The four new per-world channels get their own GM-editable message slots, resolved through
`world_event_channels` exactly as the `realm:` slots resolve through `realm_hub_channels`.

**The GM dashboard** gains a World Events card and table beside Realm Capitals and Auction Houses -
channel, access role, gated or visible, ready - and they **count toward `setup_ready`**, unlike the
auction halls, which are reported but excluded: a missing auction channel costs a lot card, a
missing events channel loses a world's news outright.

**And both harnesses can now reach a `create_category` call**, which rc.51 recorded as deferred.
Every provisioning helper defaults to `create_missing=False` and the one caller that passes True is
the GM dashboard's Full Setup, so no slash command and no hub button could reach it - the
categories, the four capitals, the nine auction channels and these four feeds were provisioned by
code no test had ever run. The resolution is that the bot's **control plane is a surface**, it is
just not a Discord one: section 2b of `scripts/playtest_discord.py` posts `{"action": "setup"}` to
`POST /control/discord` with `X-Xianxia-Control`, exactly as the dashboard does, then holds that the
four categories exist, that each capital and each world feed sits in the right one, that nine
auction channels were made, and that a second Repair over the same layout creates nothing new.

**A fourth gate this session passed its own drill**, and it is worth knowing which kind. The first
version of the harness check asserted the substring `_control("setup")`; commenting the call out
left the string in place and the gate passed. It reads *call expressions* by AST now, so a commented
or deleted call both fail. The router's gate had the same shape from the other side - its docstring
quotes the `or "Mortal World"` default it exists to refuse, so a whole-body search matched the
explanation and failed on correct code; it reads the function's statements without its docstring.

**1.0.0** (rc.51) gives the ring a home and the auctions a room of their own.

`living_world_ring` was the second orphan rc.50's sweep found, the moment it stopped counting test
files as sources: the top of the storage ladder - Immortal grade, 500 slots, the only container
carrying `living_space` - worth 40,000, with a `door_event_chance` of 75 that had never fired for
want of a ring to auction, and named in exactly one place in the tree, `support_storage_test.go`.
It went into `SOURCELESS_ITEMS` because which realm it belonged in is a content decision. It is the
**Weeping Wall Sanctum** now: the Immortal World's keyless realm (`min_realm_index: 16`, opening on
one weight-2 event, 1.6% of a draw), in `The Array's Heart`, TN 25, the last room, which granted
nothing before - and the item's own `storage_upgrade.grade` is `"Immortal"`, so the catalogue named
the world it belonged to all along. A `rare_items` entry at **chance 4**, rarer than the peach's 6
because the peach is consumed and the ring is permanent and tradeable. No Go change: rc.50's
mechanism was built, tested and shipped. `SOURCELESS_ITEMS` is empty again, and emptied by placing
the entry rather than deleting it.

**The auction channels have a category of their own.** Content authors 48 auction houses collapsing
onto **nine** channels (five grand houses, four shared per-world local floors), and all nine were
created in `🌌 Realm Capitals` - a category named for four channels and holding thirteen.
They are `🏮 Auction Houses` now, and two halves had to come with it or the split would have
been cosmetic.

**An existing server is moved, not merely rebound.** `category=` is read only on creation, so a
channel `ensure_auction_house_channels` resolved by id or by name kept whatever parent it already
had - without a re-parent step the change would reach a fresh guild and no other. It is behind
`can_create` (Discord layout is dashboard-owned; the `/admin` slash path still only binds) and
issued once per channel, because forty-eight houses share nine of them and `category_id` is read
from a cache the edit updates by gateway event.

**And teardown deletes them, which it never has.** `clear_discord_bindings` has always
`DELETE`d from `auction_house_channels`, while `teardown_managed_discord_layout` built its targets
from the base bindings, the realm hubs and `#bugs` - so Teardown forgot the bindings and left the
nine channels standing, and because they sat inside it, `🌌 Realm Capitals` could never be
emptied and **was never once deleted by the action whose whole job is to delete it**. Half the wire
had been there since v0.33.1. The gate that could not see this was the teardown contract test, which
asked only that the three sources it already knew about were named; `test_every_category_setup_makes_is_a_category_teardown_can_empty`
counts instead - every `SERVER_*CATEGORY` constant must be one teardown walks, and every provisioning
table must be one it deletes from.

**1.0.0** (rc.50) grows the one item in the catalogue that nothing could produce.

`hundred_year_peach` was one of 287 items and the only one no shop sold, no recipe made, no realm
room held, no event granted and no production file named. Fifty years of lifespan, 12,000 base
price, `auction_interest: legendary`, `door_event_chance: 65` - and both halves already worked, which
is the part worth keeping. `item_use_actions.go` grants the fifty years, and `advanced_maintenance.go`
reads `door_event_chance` to write an `auction_door_risks` row when a legendary lot is struck, so
that system had never fired either: you cannot auction a fruit that does not exist. One missing wire
kept two authored systems dark, the shape `/learn`, the quest journal and the event bands all had.

It needed a chance rather than just a placement. A realm's rooms are walked again on every run -
`secret_realm_runs` keeps one row per user and entering resets `room_index` to 0 - and three of the
eight realms have a key on sale at 448 to 672 stones, which are also the three low-floor realms. So a
room's `items` is a guaranteed repeatable payout. `rare_items` is what a room *might* hold, in
`forage_materials`' own shape, merged into the room's single reward so a find cannot be paid twice.

It grows in the Salt King's Throne, the last room of a keyless barrow that opens when the marsh
floods - floor 2, because fifty years is enormous low down and nothing high up, and salt preserves.

The sweep that proves no item is sourceless found a second orphan the moment it stopped counting
test files: its first version had no `--exclude=*_test.go`, so the peach looked sourced by the very
test written to prove it had none. With tests excluded, `living_world_ring` surfaced - the top of the
storage ladder, 40,000, named only in a Go test. It is recorded in `SOURCELESS_ITEMS` with that
reason rather than quietly placed.

**1.0.0** (rc.49) gives the first hour world events it can actually take part in.

`UnexpectedEvent` has carried `min_realm_index` and `max_realm_index` since the roster was written,
and `eligibleUnexpectedEvents` has filtered on both - so the filter ran on every draw and excluded
nobody, because all forty-four events left both unset. A cultivator three minutes old, with
attributes of 1 to 3, drew from the same pool as a Nascent Soul elder: fifteen of the eighteen world
events are severity 4 or higher, and `A Dragon Appears` was as drawable at Body Tempering as the
village festival. The mechanism was built, complete and correct, and no content used it - the same
fault as `/learn` and the quest journal, three releases running, and the fix is content because the
code was never the problem.

Two halves, because either alone is worse than nothing. A floor by severity, one rule stated once:
severity 3 and under from the start, 10 only from Soul Formation. And a band worth drawing from,
because gating the old roster alone would have shown a beginner the same three events forever -
`Local Trouble` is five village-scale events (a caravan over the bank, an irrigation break before
harvest, lantern night, something in the granary, a physician's free clinic) whose site is TN 10-12,
which a fresh character clears 55 to 79 percent of the time. They carry a ceiling as well as a floor,
so they fade once a cultivator could end them by standing still.

The floor gates the player-triggered draw only. The autonomous batch still puts a Demon Invasion
wherever the world wants one and a beginner can walk into it: being caught in something is not the
same as being handed it.

One thing worth keeping, and it is the second release running it has come up: the gate's mechanism
check was a source grep, and the drill passed when it should have failed. Asserting the draw's body
contains `c.RealmIndex < e.MinRealmIndex` survives that line becoming `if false && ...`. The
behavioural half is in Go now, where the function can be called.

**1.0.0** (rc.48) stops the engine taking a caller's word for what time it is - the last thing one
could still tell it, and the open Authority item on the punch list.

`RunDueRequest.GameMinute` has been accepted-and-ignored since the v0.22.2 review, with the reason
written on the field: *"a scheduled tick must not be able to tell the world what time it is."*
`ForceRequest` and `BootstrapRequest` carried the same field and used it for twenty-six more
releases, and `runSystem`'s own comment said it stamped the anchor "at a caller-chosen minute" two
hundred lines below the field that said the opposite. One rule, two answers, both written down.

The number is not a label. Every system under it reads it as *now*: it is the age an NPC is measured
against, the birth minute stamped on ~88 households, the anchor each system carries, the founding of
every clan. A caller a year out does not mis-title a run, it buries people. Nothing exploited it -
every caller read the engine's own minute and sent it straight back, which is exactly why it survived
twenty-six releases: the fault is invisible until the first caller that does not.

Both derive `game.CanonicalWorldGameMinute` now, the door `RunDue` already read. The wire keeps the
field on all three so an older bot mid-upgrade is not an outage - it is the value that is ignored,
not the request - while the three Python client methods take no minute and nine call sites stopped
computing one to ship and have discarded. Two gates, because neither half can see the other: the Go
one sends a wild minute and fails on the old code with `Force stamped 9999999`, and the Python one
holds that nothing in the tree sends one, reading the client's signatures by AST.

Worth keeping: the assertion that encoded the fault sat two tests below its opposite.
`test_game_engine.py` has held since v0.22.2 that an authoritative action rejects a client
`game_minute` ("Go owns current world time"), and directly under it
`test_simulation_endpoints_keep_explicit_scheduler_time` asserted `payload["game_minute"] == 12345`.
A file can hold a rule and its contradiction a dozen lines apart and stay green for a very long time.

**1.0.0** (rc.47) makes the playtest checklist say what the Discord sim proved, instead of asking a
person for it.

Every one of the checklist's 248 actions carried three live checkboxes - reachable from the hub,
error text actionable, narration or fallback fired. They were right in v0.34.0, when nothing in the
repo could press a button. rc.33 built `playtest_discord.py`, which presses every leaf the hubs
register; rc.35 built `test_playtest_coverage.py`, which holds it to the live definitions so a new
leaf is covered the day it is registered. The checklist went on asking for the first column outright
and the wiring half of the second for fourteen more releases: seven hundred and forty-four boxes,
and **not one ever ticked** across twelve regenerations.

They are one machine-filled `Swept` column now, read off the harness's own `DEFERRED_LEAVES` by AST
- the same read the coverage gate makes - so it can never claim more than the sweep drives, and a
deferral added to the harness and not regenerated into the file fails the gate. What is left for a
person is twenty-seven rows a real server is needed for, including the two the sweep *structurally*
cannot do: it runs `NARRATOR_PROVIDER=procedural`, so it can never reach a live AI route, and it
cannot judge whether a refusal *reads* helpfully to a human - one row against the sweep's log, which
is an artifact that exists, rather than 248 empty boxes nobody walks.

`merge_ticks` turned out to be preserving the wrong half: it looked only at rows of six cells or
more, so it carried the per-action boxes across every regeneration and silently dropped the
live-table ticks, the only ones in the file that were ever a person's. Nobody had noticed, because
nobody had ticked one - the same fact that retired the columns. And the new gate had the same blind
spot the code did: its first version selected action rows by their shape, so restoring the three old
checkboxes made a row it could not see, and the drill passed when it should have failed. Running a
gate against the broken tree is what says whether it is a gate or decoration.

**1.0.0** (rc.46) stops the quest journal offering the quests that exist to be handed to you.

rc.45 gave five rosters the power to hand a quest over - the beginner path stage by stage, a
household errand one at a time at home, an ascension quest off a cleared tribulation, a trade's
examination off the craft that reached the rank, and the sect road as the last beginner stage's
`follow_on` - and left `QuestService.available` offering every one of their quests from minute one.
So the first Discord sweep after the merge printed a journal to a character seconds old that listed
ten examinations, `The Expert's Toxicity` among them, at Novice, holding no trade, with an accept
select built from the same list.

Accepting one is not cosmetic. `grantOrdinaryQuestTx` reads an already-held quest as "no" and returns
without error, so a quest taken from the journal is the thing that stops its roster ever offering it:
the hall never says the examination is open, `family.errand` skips an errand it believes is already
out, and the beginner chain hands over a stage the player has been sitting on since creation. The
journal offers what nothing hands over - for most players the Quest Forge's approved drafts and
nothing else - and where it used to print a list it now says where the rest come from, so the page
still points somewhere.

The rule is one frozenset of `source_key` families beside `visible_to`, and the gate holds it equal
to the seeders in `app/rules/quests.py`, so a sixth roster fails
`tests/python/unit/test_quests_reach_a_player.py` rather than quietly putting its quests back on the
list. Nothing but presentation changed - no engine action, no schema, no content. The Discord
harness is what found it, and it had a stale assertion of its own: it looked for "First Steps" in
the journal, which was never a held quest and only ever sat under "Available", so rc.45's retirement
of that orphan broke the one step that reads the page.

**1.0.0** (rc.45) gives two quests a door, and makes a trade's rank worth sitting an examination for.

`road_to_a_sect` was the second `first_steps`. rc.26 found that fault - a quest seeded on every boot,
listed in `/quests`, handed to nobody, because every writer of a `character_quests` row wanted a
`giver_npc` the static quests deliberately do not have - and built the beginner path to fix it, for
the beginner path. The other static quest went on reaching nobody for nineteen more releases, and
`first_steps` itself was left seeded beside the stage that replaced it. So the fault was found,
named, and half fixed. The sect road is `beginner_lesson`'s `follow_on` now, which needs no new
mechanism at all, and it lands where the odds are worth taking: the trial rolls `body + realm×2 +
phase/3` against TN 15, so a cultivator who has finished the first hour is a far better candidate
than one who has just been born. `first_steps` is retired, and
`tests/python/unit/test_quests_reach_a_player.py` is the gate for the class - every seeded key must
be reachable by a giver, a roster that grants, or another quest's chain, and its allowlist is empty.

A trade's rank rose on XP alone and nothing marked it: six silent steps from Novice to Saint, a
hundred and twenty hall keepers with no opinion of anybody, and twenty-six of the thirty-three
recipes reachable only by buying a slip. `profession.exam` is what a rank is worth. Crossing one
hands over the examination the content authors for it; it is sat at a hall of that trade - a
`weaponsmith` for Forging, an `apothecary` for Alchemy, a `talisman` hall for Inscription, an `array`
workshop for Formation - and examined by that shop's own `keeper`, who is already a catalogue NPC
standing there, so nobody had to be invented. Passing teaches that rank's methods, pays standing with
the halls and costs a fee in the money of the world it is sat in; failing costs the fee and a world
day. **It never blocks a level**: `advanceProfessionTx` is untouched, so no live crafter loses a rank
they earned and nothing needs grandfathering.

**1.0.0** (rc.44) pays a cultivator in the money of the world they are standing in, converts what
they carry when they leave it, and lets the storm that judged them leave a door behind.

Which money a world uses was stated five times in code - four switches in Go and a tuple in Python -
over a fact `content/world.json` already declares on all sixteen currencies. It is `worldBaseCurrency`
now and nothing else, the same collapse rc.39 did to the world clock, and
`test_one_world_currency_rule.py` is the gate: a production file naming three of the four base
currencies is restating the mapping, and its allowlist is empty. On top of that one reader, every
reward is denominated by where it was earned - until now a cultivator in the Spiritual World was
paid in Mortal stones and charged in spirit crystals, which is the teleport arrays' old fault
("payable only by somebody who had already arrived") spread across the whole upper-world economy.

Nothing converted at the boundary, either. The content carries a `base_ratio` on every tier above
the first - a hundred of the rung below - and a world is that same ladder seen from further up, so a
crossing now divides by it going up and multiplies by it coming down. The remainder is left in the
money it was already in rather than destroyed. `moveCharacterTx` is the one door out of a world and
`TestAWorldIsLeftByOneDoor` holds it: fourteen statements wrote `characters.location` and thirteen of
them could cross a world - the ascension breakthrough, an array, a GM's relocate, a Hearth-Return
Talisman that carries you home from anywhere - so which half of a fortune survived would have
depended on how you travelled.

And clearing a world-crossing tribulation wrote `tribulation_state.cleared`, paid a reputation point
and a fate point, and stopped: the heavens opened over one named place and left nothing there, while
the only anchored way up was one authored array in one capital. `ascension.gate` anchors the seam
where the lightning fell - a permanent crossing at your own location, into the world the gate you
survived opens onto, borrowing the authored crossing's terminus, fare and realm floor so nobody can
tear open a cheaper road than the world already has. It is public ground: `array.use` resolves it
beside the authored eight, and the world's own people walk through it - the ones whose cultivation is
near the cultivator who tore it, because a seam is cut to that measure and `npc_crossing_realm_reach`
is how far either side of it still fits. It opens the gate as well as the road: `npcBreakthroughs`
had been carrying NPCs out of a world at realm 7 on wealth and health alone, which is a standard no
player is held to, and that is refused now until a seam exists - after which the people near its
measure follow, and the rest stay stalled at the gate where the world can see them. Talent is asked
before any of that: wealth and health were the only two questions, and both are things a porter can
have, so the whole world was on one ladder with realm 31 at the top. A band drawn off
`hash64(name, "talent")` and weighted by the work somebody does, added to the realm the catalogue
started them at, is the ceiling most lives never leave - and the ones who leave it are the ones worth
writing about. It is the one road in the game that leaves a world
(`WhereAnNPCCanWalk` refuses another world by construction, and content roads still do). The tribulation hands over the quest authored for that crossing, through the same
`grantOrdinaryQuestTx` the beginner path uses - there is still no second quest mechanism, only a
second thing that hands one over.

**1.0.0** (rc.43) opens two doors nobody could reach, and gives money one door of its own.

`/learn` was the only registered root command in the game that reached no player: forty-five roots,
one orphan. The engine action is allowlisted and writes an event-ledger row, the command exists with
its own autocomplete, and the content file authors thirty-three method slips - one per recipe - sold
in sixty-eight of a hundred and twenty shops. It was in neither `_MIGRATED_ROOTS` nor the tree tuple,
so it sat on no hub page and was never a slash command, and `/use` refuses a slip while the item
picker filters it out of the list. A character created today reached about three of thirty-three
recipes. `TheLearningStepTests` had proved the slip content exhaustively for twenty-three releases
and its own docstring names "two new ways to ship something dead"; it was three, and the third was
invisible from inside a file that only ever asks whether the content is right.

Caravan dispatch charged `mid_spirit_stone` from realm 4 and `high_spirit_stone` from realm 7. Those
two ids appeared at exactly the two lines that spent them - nothing has ever credited a tier above
the base - so the leaf was dead from the fourth realm upward, and below it the charge was a *Mortal
World* currency taken in all four worlds. It is the tier-1 money of the world the road departs from
now, which is the rule the teleport arrays already had and this one missed by living in Go rather
than in content.

And money got one door. Stones live in `currency_wallets` and in `characters.spirit_stones`, which is
a mirror of it; `walletDeltaTx` keeps the two in step, and eleven other places wrote one without the
other. `trade.accept` moved stones between two players with two bare UPDATEs and named
`currency_wallets` nowhere at all, so every trade desynchronised them and the next shop purchase
overwrote the traded stones out of existence. Every writer goes through the one door now, the
simulation package's byte-for-byte copy of it is gone, and schema 53 settles the drift on live worlds
upwards - neither store is the complete record, and of the two ways to be wrong, handing somebody
stones they might not have earned beats taking a fortune off a player who did nothing wrong.

Three gates, each proven to fail: the registered-root sweep names `['learn']` against the tree as it
stood, the caravan test fails on "not enough mid spirit stone" with the old escalation restored, and
the trade test reports the purse still holding 100 while the sheet says 70. No fixture could have
caught the money bug - most seeded the mirror and never made a wallet row, and the shared fixture's
`characters` table did not carry the column at all - so the fixtures carry both stores now. See
CLAUDE.md, "The slip nobody could read, and the road nobody could pay for".

**1.0.0** (rc.42) stops the suite gambling, and gates the shape so it cannot start again.

CLAUDE.md has forbidden "assert that a random thing happened, however many iterations you give it"
since September, when a sect war at 12% and a grave-robber at 22% went red for no reason. That commit
fixed those two and wrote the rule; it did not sweep the rest, and rc.41 paid for it again when a
realm crossing at 0.82^30 - one run in 385 - turned a pull request red and cost a diagnosis before
anybody could read the change it had stopped. Twenty-two tests are converted here: each either lends
the dice (`gamerng.UseRoller`) or is made certain by its scenario, and each assertion is strengthened
while it is open - "not zero" becomes the exact count the cap allows, a hunt that always lands is
asserted to land two hundred times out of two hundred, and a failed forage is now tested with
something to clear rather than with an empty plan.

The guard is `TestATestThatAssertsARollLandedLendsTheDice`, beside `TestOnlyTestsBorrowTheDice` in
`gamerng` and closing the direction that one cannot see. It asks two questions of every test in
`internal/simulation` and `internal/game`: can it reach a draw - a real call graph over the functions
that name `gamerng`, closed over same-package calls and extended through the package's own test
helpers - and does it assert a tally came back zero. A test that does both must lend the dice or be
named in `diceAllowed` with its reason. Run against the tree as it stood before the sweep it names
fifteen of them.

Two things the gate found that reading the code did not. The forage authority test
(`TestForageResolveOwnsRegionalProfileRareLootAndRNG`) asserted that a herb reached the inventory
after a TN 8 check at modifier 4 - a miss on 2d10 of 2 or 3, about **one run in thirty-three**, and by
a wide margin the worst in the tree; it was not in the inventory this sweep was planned from, because
that inventory came from grepping the simulation's own test files. And the test that says the dead
commit no further crimes skipped itself whenever no killing happened, which is the same fault wearing
a quieter coat: most runs it proved nothing. Both are certain now. The measured class was eleven tests
by assertion phrasing and twenty-two by the time the call graph had been asked; the difference is why
the rule is no longer prose alone. See CLAUDE.md, "Testing conventions".

**1.0.0** (rc.41) closes the world while you update it.

`/admin server lockdown` and a Maintenance card on the GM dashboard shut every player door and open
them again. It is not the `maintenanceBarrier` the engine already had: that one makes a restore and
in-flight writes wait for each other and never refuses anybody, which is right for thirty seconds
and wrong for an update. The engine refuses every player operation at `applyAuthoritative`, where no
`admin.*` lever passes - so a GM is never locked out of reopening the world - and the bot refuses at
its own four doors, because a read like `/sheet` never reaches the engine at all and would otherwise
answer out of a half-migrated database. The scheduled world tick stands down too and resumes where
it left off, since every system schedules off the game clock rather than wall-clock. The reason the
operator types is shown to players verbatim. The flag fails open on every unreadable shape, because
a world nobody can enter is also a world nobody can reach to unlock. See CLAUDE.md, "The world
closed for maintenance".

**1.0.0** (rc.40) retires the catalogue mirrors: one table, one path.

Schema 51 gave the engine `content_*`, written from `content/world.json` itself, and kept the five
older Python-written `catalog_*` blobs beside them for one release so a rollback would find them
intact. They are gone. Every catalogue reader - the definition lookups, the autocomplete search, the
boot counts and the GM dashboard - names its `content_*` table directly, and `content_table_for`, the
switch that picked a table by whether an engine was attached, goes with them. Boot stops rewriting
about 1,800 rows of content that had not changed, and `sync_world_catalog` is `seed_world_territories`:
it writes the territory map and the baseline era, which is what it did besides the mirroring, and the
name no longer claims a catalogue it does not touch. The GM's "Sync world catalog" says the same
truth. The one path the mirrors existed to serve - pytest, which has no engine to fill the new tables
- is a fixture now rather than a second set of tables, because the engine is the only thing that may
write `content_*` in production. See CLAUDE.md, "The content file as tables".

**1.0.0** (rc.39) retires the last Python-side clock.

`Database.get_world_clock` was the one copy of an engine rule left in the DB layer, named as open on
the roadmap since v0.30.0. It read the anchor out of `world_state`, did the arithmetic in Python -
and **re-anchored the row whenever the stored scale disagreed with `WORLD_TIME_SCALE`**, which every
caller passed. So a rate a GM set on the dashboard was silently undone by the next `/time`,
`/cultivate` or narration, and `/admin world advancetime` sent the env scale on every call whether
or not anybody had asked to change it. The engine answers `world.clock` now - a read-only query that
deliberately seeds nothing, so a clock somebody merely looked at is not a clock that started - and
`current_world_time`, the bot's startup, the narrator's context builder, the GM dashboard, `/time`
and the engine playtest all read through it. Four copies of the arithmetic in Go became one
(`loadCanonicalWorldClock` + `worldClockGameMinute`), and two in Python became none: the gate holds
that no file under `app/` or `scripts/` so much as names `anchor_real_ts`. `WORLD_TIME_SCALE` is the
engine's key now, passed through by compose, and it seeds a **new** world only - after that the
stored scale is the last word, changed by the audited lever alone, which `/admin world advancetime`
gained an optional `scale` for. `Settings.world_time_scale` is gone. See CLAUDE.md, "The last
Python-side clock".

**1.0.0** (rc.38) drives what only the world makes, and empties the deferred set.

The last of rc.35's deferred blocks is empty and `DEFERRED_OPERATIONS` is an empty dict on purpose:
a beast hunted, tamed, fed, trained, made active and evolved on a bounded loop of free hunts (the
hunt roll is the only door to an encounter, and its cooldown is the one thing on that path a payload
may clear); a bounty earned with certainty - a forbidden palm in a fight, unconcealed, is witnessed
every time - and its hunter fielded by the due tick, then evaded, fought and surrendered to; and an
NPC lost and found. A disappearance is the one thing that gained a lever: `admin.npc.set_missing`
stages one the way the `npc_life` tick does, through one shared helper that writes the row the
Quest Forge reads at 82, and brings somebody home off-screen with a quieter row. It is audited and
undoable, on the dashboard's NPC card as Lose and Bring back and on Discord as
`/admin npc setmissing`. `Database.list_missing_npcs` is the read. And the leg found a bug the day
it could drive `npc.found`: the find answered found and persisted nothing, because the storage
connection's implicit transaction was never committed before the switch path closed it - `/talk`
told the player they had found somebody while the row stayed missing, and a grave claim handed over
a keepsake it never wrote. The handler commits, the switch path now commits a result returned over an
open transaction and rolls back an error, and a test drives it through `Apply` the way the server
does. See CLAUDE.md, "The playtest touches everything".

**1.0.0** (rc.37) drives progression through the engine playtest.

The second of rc.35's deferred blocks is empty: realm perfection on both ladders - the path started,
its seven quests prepared and rolled on a bounded loop, the twenty points of training, the trial
driven when the dice allowed every quest and held locked when they did not - the body breakthrough,
the Mortal Ascension tribulation prepared five times and faced with its waves' conditions treated,
the root refined, steadied and evolved, the bloodline and the physique on whichever path the birth
allows, and a personal world created, ruled, entered and left after Space Law is climbed to
Essence/Origin; seventeen operations. The body ladder had no lever, so `admin.player.set_realm`
takes an optional `body_realm_index`/`body_phase` pair (both or neither), audited and undone with
the qi pair. The GM dashboard gained a **Player Editor**: the sixteen per-player cards the Admin
Console carried, each blind to the character chosen, are one view now, picked once and pre-filled
from the rows each lever writes (`/api/player` returns them beside the sheet), while the console
keeps the levers that act on the world or the server; the write path, the action map and the audit
row are unchanged. The Discord `/admin` panel does the same: its Players, Grants and Moderation
heads are one **Player Edit** head, paged by "More actions", and it gains `/admin player setrealm`,
the realm lever with the same optional body pair. See CLAUDE.md, "The playtest touches everything"
and "Dashboard".

**1.0.0** (rc.36) drives the sect and the homestead through the engine playtest.

The first of rc.35's three deferred blocks is empty: the sect's residence, its contributions and
redemptions, the manor and its first facility, the hidden sect's initiation at karma −200, a
recommendation, a discipleship requested, accepted and left, a territory claimed and a war started by
a second sect's claim, and the homestead founded, shared, furnished, sat in and left - twenty-two
operations and one GM lever, each on a state the GM levers build, with the rolls reported. The one
door the engine never opens itself, the sect's residence row, is staged through the same repository
call `/sect abode` uses. See CLAUDE.md, "The playtest touches everything".

**1.0.0** (rc.35) makes the playtest touch everything, and holds it there.

The engine playtest drove 51 of 193 allowlisted operations and the Discord playtest pressed 9 of
245 leaves, and nothing said which the harnesses had to drive, so a new operation or leaf was
uncovered until somebody noticed. `tests/python/contracts/test_playtest_coverage.py` now enumerates
the surface from the code - the allowlist maps and the dispatch switch in Go, the live hub
definitions in Python - and holds each harness to it with one explicit deferred set per harness,
each entry carrying its reason; a stale deferral fails the gate. The Discord harness gained a
generic sweep that presses every leaf of every hub, admin included, answering each input step as a
player with no plan would and holding that the reply is a result or a designed refusal, never the
hub's failure text. The engine harness gained every family a fresh pair of characters and the GM
levers can reach, from storage to the dynasty a new life inherits, with every remaining lever's
audit row checked; the sect's rooms, progression and what only the world makes are deferred to
three named follow-ups. The sweep's first run found four handlers no source read had: no trade
had ever left Discord (a `game_minute` the client refuses to send), the forage reply raised on
every forage (the engine's roll was flattened without its degree), `/talk` at a grave called a
switch operation through the authoritative client, and a GM's grant to a member with no
character crashed instead of refusing. All four are fixed and held. See CLAUDE.md, "The playtest
touches everything".

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
