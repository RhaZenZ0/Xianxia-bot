# Xianxia RP Discord Bot — Version History

The changelog, one paragraph per minor. The per-release entries as they were written are in
`docs/history/CHANGELOG_0_18_TO_0_40.md`, and the earlier per-release notes (`V018_RELEASE_NOTES.md`
… `V023_RELEASE_NOTES.md`) beside it. `README.md` describes the current release.

## Changelog

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

## Release status — v1.0.4

- Current release: v1.0.4 - vitality recovers with time, which nothing in this game had ever done.
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
