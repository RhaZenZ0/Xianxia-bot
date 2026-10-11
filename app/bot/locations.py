"""Where things are: known/visible locations, world gating, NPC whereabouts.

Phase 3 of the main.py split (v0.19.35, docs/history/MAIN_SPLIT_PLAN.md). The scope-
accurate dependency scan behind the plan found that 15 of the 19 edges between
the player-command sub-domains still in main.py all pointed at these helpers -
they were buried in the travel section but read by perception, market, scene,
territory, storage and sect code. Pulling them below main.py is what lets those
domains be cut out one at a time without cross-imports.

`current_npc_location` came along too (the plan had it in a later module): it
is the one dependency of `local_npc_autocomplete`, reads only `SIM`/`WORLD`/
`current_world_time`, and is about where an NPC is - the same question as the
rest of this file.

Two of these (`location_autocomplete`, `local_npc_autocomplete`) are referenced
as bare `@app_commands.autocomplete(...)` arguments, which evaluate when the
decorated command's module is imported - so they must be importable at module
level, never behind a deferred import. Nothing here imports main.py, and the
definition order is the order these had in main.py.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import Any, Sequence

import discord
from discord import app_commands

from ..rules.realm_hubs import REALM_HUBS, access_realm_index
from ..rules.sense import circuit_stop
from .runtime import DB, WORLD, current_world_time, log
from .services import SIM

# `current_npc_location` returned None for two different things - "this NPC is
# dead" and "nothing knows where they are" - and every caller read None as the
# second. So a dead NPC was offered in every picker at every location, and
# `/talk` and `/npcinfo` treated a corpse as somebody standing where they fell.
# DEAD separates the two. It is not a location and never matches one, so the
# callers that compare against a real place already do the right thing with it;
# the ones that would show it to a player are the ones that had to change.
DEAD = "\x00dead"


def scheduled_location(npc_name: str, period: str) -> str:
    """What the content file's daily schedule says for this period, and nothing
    else - no fallback to the NPC's `location`.

    This is the engine's own term (`npcWhereaboutsTx`: `npc.Schedule[period]`,
    blank when there is no entry), and v1.12.3 holds the Python twin to it.
    `WORLD.npc_location_at` answers the schedule *or* the content location,
    which is right for asking where content puts somebody and wrong for a
    somebody the simulation has a row for: a period with no schedule entry
    leaves them where the simulation says, and the content location is not a
    whereabouts. Reading it here answered the file's place for an NPC whose
    row - after a content edit moved them - said somewhere else.
    """
    schedule = (WORLD.npcs.get(npc_name) or {}).get("schedule") or {}
    return str(schedule.get(period) or "").strip()


async def current_npc_location(npc_name: str, period: str | None = None, *, world_time: Any = None) -> str | None:
    """Resolve the mechanical NPC location from initialized simulation state.

    Daily world.json schedules still shape an NPC's routine while they remain in
    their home region, but there is no legacy no-simulation fallback anymore.

    `world_time` is the clock a caller has already read (v1.31.2). The circuit
    branch below needs the full game minute, so handing it only `period` did
    not spare it a clock read - and `npcs_present` resolved every circuit walker
    in the catalogue through here, eleven identical `world.clock` round trips
    on every panel header. A caller with the clock in hand passes it; one
    without still reads it here, once.
    """
    # A hidden master who walks the road is wherever their circuit puts them
    # this month, and that answer outranks both the daily schedule and the
    # civilization simulation: these are recluses crossing the world on their
    # own business, not townsfolk on a routine. It is a pure function of the
    # canonical clock, so nothing has to tick to move them.
    walking = WORLD.npcs.get(npc_name, {}).get("circuit")
    if walking:
        wt = world_time if world_time is not None else await current_world_time()
        stop = circuit_stop(
            walking, int(getattr(wt, "total_minutes", 0)),
            months=int(WORLD.npcs[npc_name].get("circuit_months", 2) or 2),
            offset=int(WORLD.npcs[npc_name].get("circuit_offset", 0) or 0),
        )
        if stop:
            return stop

    sim_state = await SIM.npc_status(npc_name)
    if period is None:
        period = (world_time if world_time is not None else await current_world_time()).period
    status = str((sim_state or {}).get("status") or "")
    if sim_state and status not in ("", "alive", "missing"):
        return DEAD
    if sim_state and status in ("alive", "missing") and sim_state.get("current_location"):
        current = str(sim_state["current_location"])
        # A missing person is exactly where they are; the world simply does
        # not know it (schema 47). Answering truthfully here is what makes the
        # picker's own location filter do the work - they are hidden from
        # every place except the one a searcher would actually find them in -
        # and it keeps the daily schedule out of it, because somebody who has
        # vanished is not keeping to one.
        if status == "missing":
            return current
        home = str(sim_state.get("home_location") or current)
        # Normal daily schedules still apply while the NPC remains in their home
        # region. Autonomous civilization travel overrides the schedule only when
        # the NPC has actually moved away from that home region.
        if current == home:
            return scheduled_location(npc_name, period) or current
        return current
    # Somebody the world made for itself who has no simulation row (schema 49).
    # A matured descendant gets one at the moment they come of age, so this is
    # really about a starter household's relatives: they stand in the household
    # and nowhere else, and they are never walked by the civilization tick.
    #
    # It matters that this answers rather than falling through. `None` here
    # means "nothing knows where they are", which every caller reads as "do not
    # filter by location" - so without this a player could talk to somebody
    # else's uncle from the other side of the world.
    try:
        registered = await DB.get_registered_npc(npc_name)
    except Exception:
        log.exception("Could not read the registry location of %s", npc_name)
        registered = None
    if registered and str(registered.get("location") or ""):
        return str(registered["location"])
    # A running world event's cast stands where the event is (v1.1.0). They
    # had no answer here, and `None` reads as "do not filter by location" - so
    # a militia captain or a recruitment delegation's elder could be talked to
    # from the far side of the world, and a sponsor's presence check could
    # never be met. The event's location is the whole truth of where they are.
    try:
        cast = await DB.get_event_npc_definition(npc_name)
    except Exception:
        log.exception("Could not read the event location of %s", npc_name)
        cast = None
    if cast and str(cast.get("location") or ""):
        return str(cast["location"])
    # A catalogue NPC no simulation has touched: the content's own placement,
    # their schedule this period else where the file puts them - the engine's
    # last term (`npcWhereaboutsTx`), held equal in v1.12.3. It used to answer
    # `None` here, which every caller reads as "do not filter by location", so
    # the picker and `/talk` let somebody the engine would place through from
    # anywhere. Somebody content does not carry is still `None`.
    if npc_name in WORLD.npcs:
        return WORLD.npc_location_at(npc_name, period) or None
    return None


async def npcs_present(location: str, period: str | None = None) -> list[str]:
    """Who is standing at one place, resolved in a bounded number of queries.

    This replaces the shape every caller used until v1.0.0-rc.28:

        for npc_name in WORLD.npcs:
            if await current_npc_location(npc_name, period) == location:

    which is 574 iterations with an engine round trip inside each, serially,
    to draw one picker - and `/action` and `/scene status` both did it on every
    open.

    The bound comes from asking content first. One query gets everybody the
    engine has standing here; then, rather than asking the engine about the
    other five hundred and seventy, the in-process catalogue rules out
    everybody it does not put here this period, and only what is left costs a
    round trip. Content can be wrong in exactly one direction - it does not
    know about autonomous travel - and that direction is covered by the first
    query, because somebody the simulation walked here has a row saying so.

    The order of precedence is the same one `current_npc_location` keeps, and
    it has to be: a circuit outranks everything, then the simulation, then the
    content file's schedule. Answering differently here from there would mean a
    picker that offers somebody `/talk` then refuses them.
    """
    where = str(location or "")
    if not where:
        return []
    # The clock is read once here and handed to every resolve below (v1.31.2):
    # the period for the schedules, the minute for the circuit walkers. Before
    # this each walker's resolve read it again, and there are eleven of them.
    wt = await current_world_time()
    if period is None:
        period = wt.period
    present: list[str] = []
    seen: set[str] = set()
    engine_rows: list[dict[str, Any]] = []
    try:
        engine_rows = await SIM.npcs_at_location(where)
    except Exception:
        log.exception("Could not read who is standing at %s", where)
    settled: set[str] = set()
    for row in engine_rows:
        name = str(row.get("npc_name") or "")
        if not name:
            continue
        settled.add(name)
        # A circuit-walker is placed by content against the canonical clock and
        # that answer outranks the simulation, exactly as current_npc_location
        # has it - so they are resolved below rather than trusted from here.
        if (WORLD.npcs.get(name) or {}).get("circuit"):
            continue
        # Somebody standing in their home region is still keeping to a daily
        # routine, and the routine can take them out of the room their row
        # names. That is the one thing the engine's answer cannot know, because
        # the schedule is content - so it is applied here, on the same terms
        # `current_npc_location` applies it: home only, and never to somebody
        # who has vanished, because a missing person keeps no routine
        # (schema 47) and their row is the whole truth about where they are.
        home = str(row.get("home_location") or where)
        if str(row.get("status") or "") != "missing" and str(row.get("current_location") or where) == home:
            if (scheduled_location(name, period) or where) != where:
                continue
        present.append(name)
        seen.add(name)
    for name, definition in WORLD.npcs.items():
        if name in seen:
            continue
        # A circuit stop is a pure function of the canonical clock, so these
        # cost nothing to resolve and are the one group the engine cannot rule
        # on at all.
        if (definition or {}).get("circuit"):
            if await current_npc_location(name, period, world_time=wt) == where:
                present.append(name)
                seen.add(name)
            continue
        # Everybody the engine returned has been ruled on above.
        if name in settled:
            continue
        # And content has ruled out everybody it does not put here this period.
        # Skipping them without asking is the whole point: resolving each in
        # turn is the per-NPC round trip this function exists to remove, and
        # doing it for the five hundred who are demonstrably somewhere else
        # kept the old cost while looking like it had gone.
        if (WORLD.npc_location_at(name, period) or "") != where:
            continue
        # Content says here, so the engine gets the last word - it may have
        # walked them away, or buried them.
        if await current_npc_location(name, period, world_time=wt) == where:
            present.append(name)
            seen.add(name)
    return sorted(present)


def _world_min_realm_index(world_name: str) -> int:
    hub = REALM_HUBS.get(str(world_name))
    if hub is not None:
        return int(hub.get("min_realm_index", 0))
    candidates = [
        int(data.get("min_realm_index", 0))
        for data in WORLD.locations.values()
        if str(data.get("world")) == str(world_name)
    ]
    return min(candidates) if candidates else 0


def _world_is_unlocked(character: dict[str, Any], world_name: str) -> bool:
    return access_realm_index(character) >= _world_min_realm_index(str(world_name))


def _city_parts(city: str, known: Collection[str] = ()) -> list[str]:
    """A city's gates and districts as a cultivator sees them - the twin of the
    engine's `cityPartsInPlainSight`, and the one list every door of the city
    page reads (the header, City -> Look, City -> Enter, the seat line, the
    travel picker's map).

    A part is shown unless it is `private`: a demonic sect's gate is a district
    of its seat (v1.19.0) which the street does not show, so the arrival line,
    the header and Look name it to nobody. What lifts that is the engine's
    other half of the rule - a sponsor's word is a discovery row, and standing
    in the gate is knowing it - so a private part is shown to somebody whose own
    map (`known`, `_known_private_parts`) holds it. With no `known` this is
    plain sight alone."""
    return sorted(name for name, data in WORLD.locations.items()
                  if data.get("district") and str(data.get("outside_location")) == city
                  and (not data.get("private") or name in known))


async def _known_locations(user_id: int, character: dict[str, Any]) -> set[str]:
    rows = await DB.get_discovered_locations(int(user_id))
    known = {str(row.get("location")) for row in rows if row.get("location")}
    # A known part of a city is a known city - the twin of the rule
    # `knownLocationsTx` has kept since v1.19.0 and this side never had, so a
    # gate a sponsor revealed was on the engine's map and the picker's city
    # list was missing the city it stands in (v1.26.0).
    for place in list(known):
        city = _city_of_location(place)
        if city and city != place:
            known.add(city)
    current = str(character.get("location") or "")
    if current and not current.startswith(("abode:", "personal_world:")):
        known.add(current)
        current_data = WORLD.locations.get(current) or {}
        # Inside a city's gate, district, shop or hall (v0.36.0) the city
        # itself is known, its roads, and every gate and district of it.
        city = current
        if current_data.get("outside_location") and (current_data.get("district") or current_data.get("shop") or current_data.get("auction_house")):
            city = str(current_data["outside_location"])
            known.add(city)
            current_data = WORLD.locations.get(city) or {}
        # A private part - a sect's hidden gate - is a sponsor's to reveal,
        # not the street's (`knownLocationsTx` skips it too, v1.19.0); the
        # picker offered it and the engine refused it until v1.26.0. No `known`
        # is passed: this is plain sight, and what is discovered is above.
        known.update(_city_parts(city))
        # At a road-side site (v0.39.0) the road runs both ways: both ends
        # of its leg are known, and every other site on that leg.
        leg = [str(x) for x in list(current_data.get("road_leg") or [])] if current_data.get("road_site") else []
        if len(leg) == 2:
            known.update(leg)
            for name, data in WORLD.locations.items():
                if data.get("road_site") and sorted(str(x) for x in data.get("road_leg") or []) == sorted(leg):
                    known.add(name)
        for neighbor in current_data.get("roads", []):
            neighbor_name = str(neighbor)
            neighbor_data = WORLD.locations.get(neighbor_name) or {}
            if not neighbor_data or bool(neighbor_data.get("private", False)):
                continue
            if access_realm_index(character) < int(neighbor_data.get("min_realm_index", 0)):
                continue
            if str(neighbor_data.get("world") or "") != str(current_data.get("world") or ""):
                continue
            known.add(neighbor_name)
    # Realm capitals become public knowledge only when the character can actually
    # survive in that world. Future worlds remain completely hidden.
    for world_name, hub in REALM_HUBS.items():
        if _world_is_unlocked(character, world_name):
            known.add(str(hub["location"]))
    return known


async def _known_private_parts(user_id: int, character: dict[str, Any], city: str) -> frozenset[str]:
    """The private parts of ``city`` this cultivator's own map holds - a gate a
    sponsor named, or the one they are standing in - for `_city_parts` to lift.

    Nearly every city has none, so it answers at once without a read; the two
    seats of a private gate pay for one. It never raises: it is drawn beside
    everything else on a city page, and a failed read leaves the street's
    answer (plain sight), which under-offers rather than leaks."""
    private = {name for name, data in WORLD.locations.items()
               if data.get("private") and data.get("district") and str(data.get("outside_location")) == city}
    if not private:
        return frozenset()
    try:
        return frozenset(private & await _known_locations(int(user_id), character))
    except Exception:  # noqa: BLE001 - one unavailable read must not cost the city page
        log.exception("Could not read what %s knows of %s", user_id, city)
        return frozenset()


async def _location_is_visible(user_id: int, character: dict[str, Any], location: str) -> bool:
    data = WORLD.locations.get(str(location)) or await DB.get_location_definition(str(location))
    if not data:
        return False
    if not _world_is_unlocked(character, str(data.get("world") or WORLD.realm_world(access_realm_index(character)))):
        return False
    return str(location) in await _known_locations(user_id, character)


async def npc_whereabouts(user_id: int, character: dict[str, Any], npc_name: str) -> str:
    """Where somebody is now, as a player may be told it (v1.8.3, v1.20.2).

    The civilization tick walks ordinary townsfolk off to the next district
    and home again, so a commission's fishmonger is often not on the dock the
    commission was written about - and the `/talk` and `/npcinfo` pickers only
    offer who is in the room, so nothing told a player where to look. This is
    `/npcinfo`'s own answer, under `/npcinfo`'s own rule: a place the player
    has not discovered is not named. A missing person is never placed, because
    finding them is the point of looking. It never raises, because it is drawn
    beside every line of the journal.

    v1.20.2: it is also every reply's answer, not only the journal's. Asked
    "why would a player see npc inspect", reading it found `/npcinfo`, `/talk`
    and the sect recommendation each printing `current_npc_location` straight
    back - which answers a missing person's true position on purpose, so the
    pickers list them only where a searcher stands - so typing a missing
    person's name did a disappearance quest's whole search, and the two
    refusals named places the player had never found. Three readers had
    drifted from the one statement of the rule; they all ask this now. A
    missing person standing where the player stands *is* here: the searcher
    has found them, and `/talk` there is what reports it.
    """
    name = str(npc_name or "").strip()
    if not name:
        return ""
    try:
        state = await SIM.npc_status(name) or {}
        status = str(state.get("status") or "")
        where = await current_npc_location(name)
        if status == "missing":
            if where and str(where) == str(character.get("location") or ""):
                return "is here with you"
            return "is missing; nobody knows where"
        if where == DEAD or (state and status not in ("", "alive")):
            return "has died"
        if not where:
            return ""
        if str(where) == str(character.get("location") or ""):
            return "is here with you"
        if await _location_is_visible(int(user_id), character, str(where)):
            return f"is now at **{where}**"
        return "is somewhere you have not been"
    except Exception:
        log.exception("Could not resolve where %s is for the journal", name)
        return ""


async def objective_line_suffix(user_id: int, character: dict[str, Any] | None, objective: dict[str, Any], done: bool) -> str:
    """The whereabouts note for an unfinished `talk` objective, else nothing."""
    if done or not character or str(objective.get("type") or "") != "talk":
        return ""
    where = await npc_whereabouts(user_id, character, str(objective.get("target") or ""))
    return f" · *{str(objective.get('target'))} {where}*" if where else ""


async def location_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    known = await _known_locations(interaction.user.id, c)
    needle = current.casefold().strip()
    names = [
        name for name in known
        if name in WORLD.locations
        and (not needle or needle in name.casefold())
        and _world_is_unlocked(c, str(WORLD.locations[name].get("world") or "Mortal World"))
    ]
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in sorted(names)[:25]]


async def local_npc_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    location = c.get("location") if c else None
    wt = await current_world_time()
    needle = current.casefold().strip()
    names: list[str] = []
    # A running world event's cast stand here too, and they are not in the
    # permanent catalogue, so search_catalog will never turn them up. They go
    # first: they are the reason there is anyone to talk to at a beast tide.
    if location:
        try:
            for person in await DB.list_active_event_npcs(location):
                name = str(person.get("name") or "")
                if name and (not needle or needle in name.casefold()):
                    names.append(name)
        except Exception:
            log.exception("Could not read the event cast at %s", location)
        # A grave is a name you can address that the catalogue picker filters
        # out, because the dead are filtered out of it (schema 48). It belongs
        # in the list exactly where it stands and nowhere else - the same rule
        # the missing follow, for the same reason.
        try:
            for grave in await DB.list_graves_at(location):
                name = str(grave.get("npc_name") or "")
                if name and name not in names and (not needle or needle in name.casefold()):
                    names.append(name)
        except Exception:
            log.exception("Could not read the graves at %s", location)
        # And the people this world made for itself (schema 49): a child of two
        # NPCs who has grown up, a relative of the household a player was born
        # into. They are in neither the catalogue nor the event cast, and until
        # this the household a new character is standing in printed its own
        # relatives under `/family` while `/talk` refused every one of them.
        try:
            for person in await DB.list_registered_npcs_at(location):
                name = str(person.get("name") or "")
                if name and name not in names and (not needle or needle in name.casefold()):
                    names.append(name)
        except Exception:
            log.exception("Could not read the registered NPCs at %s", location)
    # Who is actually standing here, through the one resolver (v1.0.8).
    #
    # This asked `DB.search_catalog("npc", current, 25)` and filtered the
    # answer by location. With nothing typed the needle is `%%`, so that query
    # is `ORDER BY name LIMIT 25` over all 574 catalogue NPCs - the
    # alphabetically first twenty-five of the whole world - and the location
    # filter then ran on *those*. So the picker could only ever offer somebody
    # whose name sorts near the front AND who happens to be in the room, which
    # for most rooms is nobody: `/talk` and `/npcinfo` both answered "nothing
    # to choose from right now" while the scene card one line above named the
    # gate captain standing there, because the card asks `npcs_present` and
    # this did not.
    #
    # rc.28 wrote `npcs_present` as the one resolver for exactly this question
    # and said why the two must agree: "a picker that offers somebody /talk
    # then refuses them is worse than either being wrong alone." The inverse
    # held instead - the card named people the picker would not offer.
    #
    # The typed needle narrows what is here rather than selecting what to look
    # for, which is the rc.46 rule: a surface must not offer what the engine
    # will refuse, and `/talk` refuses anybody who is somewhere else.
    if location:
        try:
            for name in await npcs_present(location, wt.period):
                if name in names:
                    continue
                if not needle or needle in name.casefold():
                    names.append(name)
        except Exception:
            log.exception("Could not read who is standing at %s", location)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]


# --- the Here line and the travel picker (v0.40.0) ---------------------------

ROAD_SITE_WORDS = {"waystation": "a waystation", "hunting_ground": "a hunting ground", "ruin": "a ruin", "shrine": "a wayside shrine"}


def _city_of_location(name: str) -> str:
    data = WORLD.locations.get(name) or {}
    if data.get("outside_location") and (data.get("district") or data.get("shop") or data.get("auction_house")):
        return str(data["outside_location"])
    return name


# The ground's qi (v1.0.0-rc.4). The engine is authoritative for what a
# session actually gathers (placeCultivationMultiplier); this names the same
# three catalogue facts so the Here line can say a place is worth sitting in.
_QI_GROUND_WORDS = {"shrine": "rich qi", "temple": "good qi"}


def _qi_ground(name: str, data: dict) -> str:
    if str(data.get("road_site") or "") == "shrine":
        return _QI_GROUND_WORDS["shrine"]
    if str(data.get("district") or "") == "temple":
        return _QI_GROUND_WORDS["temple"]
    if any(str((sect.get("recruitment") or {}).get("location")) == name for sect in WORLD.sects.values()):
        return "good qi"
    return ""


def here_summary(location: str, limit: int = 180, *, present: Sequence[str] | None = None) -> str:
    """One line on what the place you stand in is and offers, for the panel
    header (v0.40.0). Pure: reads the catalogue only. Private places
    (abodes, personal worlds, households) answer nothing - their own hubs
    describe them.

    **It names who is here only when the caller tells it (v1.0.10).** This used
    to append the content file's *residents* - every NPC whose `location` field
    is this place - read with no schedule and no simulation. So the header said
    "Gate Captain Yue Dong" while `/talk`, which asks `npcs_present`, offered
    Drillmaster Zhai Kang, whom the tick had walked to that gate. Both were
    right by their own definition and they disagreed, which is the state rc.28
    forbids - in a line rc.28 never covered, because rc.28 fixed the cards and
    v1.0.8 fixed the picker, and this is the header.

    A synchronous function cannot answer it: who is standing somewhere is a
    simulation row, an engine round trip away. So it no longer guesses. Both
    production callers are async and hand over `npcs_present`'s answer; a
    caller with nothing to give gets the place described and nobody named,
    which is the honest half of what this can know.
    """
    name = str(location or "")
    data = WORLD.locations.get(name)
    if not data:
        return ""
    people = sorted(str(n) for n in (present or ()))
    city = _city_of_location(name)
    if data.get("road_site"):
        leg = [str(x) for x in list(data.get("road_leg") or [])]
        what = f"{ROAD_SITE_WORDS.get(str(data['road_site']), 'a place')} on the {' – '.join(leg)} road"
    elif data.get("shop"):
        shop = WORLD.shops.get(str(data["shop"])) or {}
        what = f"inside {shop.get('name') or name}, kept by {shop.get('keeper') or 'the keeper'}, in {city}"
    elif data.get("auction_house"):
        what = f"the auction floor of {city}"
    elif data.get("gate"):
        faces = ", ".join(str(x) for x in (WORLD.locations.get(city, {}).get("gates") or {}).get(str(data["gate"]), []))
        what = f"the {data['gate']} Gate of {city}" + (f", facing {faces}" if faces else "")
    elif any(str((sect.get("recruitment") or {}).get("location")) == name for sect in WORLD.sects.values()):
        # A sect's gate is asked before the district branch: since v1.19.0 a
        # seated gate is a `sect_gate` district of its city, and "the sect
        # gate of Cloudblade City" says less than whose gate it is.
        sect_name = next(s for s, sect in WORLD.sects.items() if str((sect.get("recruitment") or {}).get("location")) == name)
        seat = WORLD.sect_seat(sect_name)
        what = f"the gate of the {sect_name}" + (f", its seat in {seat}" if seat else "") + " · trials before its examiner"
    elif data.get("district"):
        what = f"the {str(data['district']).replace('_', ' ')} of {city}" if data["district"] != "inn" else f"the inn of {city}"
    else:
        # Counted from plain sight (v1.33.0): this header has no viewer, and a
        # private gate counted here made it say three districts over a Look
        # that lists two.
        parts = _city_parts(name)
        gates = [p for p in parts if WORLD.locations[p].get("gate")]
        districts = [p for p in parts if not WORLD.locations[p].get("gate")]
        roads = [str(r) for r in list(data.get("roads") or [])]
        if parts:
            what = f"a {'capital' if data.get('realm_hub') else str(data.get('settlement_type') or 'city')} · {len(gates)} gate{'s' if len(gates) != 1 else ''}, {len(districts)} district{'s' if len(districts) != 1 else ''}"
            # The city a sect sits in says so (v1.19.0): the seat is the
            # city's politics, and the panel header is where a city is read.
            if seated := WORLD.seated_sect(name):
                what += f" · seat of the {seated}"
        elif roads:
            what = f"{str(data.get('terrain') or 'open country')} · roads to {', '.join(roads[:3])}"
        else:
            what = str(data.get("terrain") or "open country")
    qi = _qi_ground(name, data)
    if qi:
        what += f" · {qi}"
    if people:
        shown = ", ".join(people[:3]) + (f" +{len(people) - 3}" if len(people) > 3 else "")
        what += f" · {shown}"
    return what[:limit]


def door_allows(current: str, destination: str) -> bool:
    """Whether `exploration.travel`'s door rules let a walk go from `current`
    to `destination` - the Python twin of the four checks at the top of
    `explorationTravelAction`, so the travel picker never offers a place the
    engine refuses on the doorstep (rc.46; v1.7.1).

    - an auction hall is never walked into, and its warded door leads only
      onto its own street;
    - a shop is entered from anywhere in its city, and nowhere else;
    - a shop's door opens onto its city's street and nowhere else, so from
      inside one the street and the city's other shops are the whole list;
    - a gate or district is walked to from anywhere in its city.

    A waystation's stall is the waystation itself (a road site), so neither
    shop rule applies to one. The engine remains the refusal: this only
    decides what a picker draws. `test_a_shop_door_opens_onto_its_street.py`
    rewrites the rules off the Go source's own content and holds this to them
    over every pair of locations in the catalogue.
    """
    cur = WORLD.locations.get(current) or {}
    dest = WORLD.locations.get(destination) or {}
    if dest.get("auction_house"):
        return False
    if cur.get("auction_house"):
        return destination == str(cur.get("outside_location") or "Greenriver Town")
    origin = _city_of_location(current)
    if dest.get("shop") and not dest.get("road_site"):
        return origin == str(dest.get("outside_location") or "")
    if cur.get("shop") and not cur.get("road_site"):
        return destination == str(cur.get("outside_location") or "")
    if dest.get("district"):
        return origin == str(dest.get("outside_location") or "")
    return True


# The four kinds of place the travel menu picks from (v1.26.0), in the order
# the Destinations page draws them. One picker held every known place in one
# list of 25, and every road site anywhere outranked every city, so a player
# who had walked the roads could not pick most cities at all: from the Azure
# Crown capital even the four cities one road away fell off the end.
DESTINATION_KINDS: tuple[tuple[str, str], ...] = (
    ("city", "Cities"),
    ("here", "This city"),
    ("road", "Road sites"),
    ("wilds", "Wilds and gates"),
)


def destination_kind(emoji: str) -> str:
    """Which kind a `destination_groups` row is, by the group it was drawn in:
    the streets, parts and shops of the city you stand in; a road site; a city
    by road (or a capital, or a road's end); anything else - a wild place, a
    sect gate, a road-less spot."""
    if emoji in ("🚪", "🏙️", "🏪"):
        return "here"
    if emoji == "🛤️":
        return "road"
    if emoji in ("🛣️", "🌀"):
        return "city"
    return "wilds"


def destinations_of_kind(current: str, known: set[str] | list[str], realm_index: int, kind: str) -> list[tuple[str, str, str, int]]:
    """The rows of one kind, in `destination_groups`' own order - so a city is
    nearest first and nothing of another kind can push it off a list of 25."""
    return [row for row in destination_groups(current, known, realm_index) if destination_kind(row[1]) == kind]


def destination_groups(current: str, known: set[str] | list[str], realm_index: int) -> list[tuple[str, str, str, int]]:
    """The travel picker's rows (v0.40.0): (name, group, description, order).

    Groups: the parts of the city you stand in; road-side sites on the
    road you stand on or beside; the cities along the roads, by hops from
    here; the realm capitals. Hops are counted over the catalogue's roads
    for the label only - the engine plans the journey and its time.
    """
    current = str(current or "")
    city = _city_of_location(current)
    here = WORLD.locations.get(current) or {}
    origin = city
    site_leg = [str(x) for x in list(here.get("road_leg") or [])] if here.get("road_site") else []
    hops: dict[str, int] = {}
    starts = site_leg or [origin]
    frontier = [(s, 1 if site_leg else 0) for s in starts]
    for s, d in frontier:
        hops[s] = d
    while frontier:
        node, dist = frontier.pop(0)
        for road in list((WORLD.locations.get(node) or {}).get("roads") or []):
            road = str(road)
            if road not in hops:
                hops[road] = dist + 1
                frontier.append((road, dist + 1))
    rows: list[tuple[str, str, str, int]] = []
    for name in sorted(str(n) for n in known):
        if name == current:
            continue
        data = WORLD.locations.get(name)
        if not data or int(data.get("min_realm_index", 0)) > int(realm_index) or not door_allows(current, name):
            continue
        if name == city and current != city:
            # Standing inside the city - a shop, a gate, a district - the
            # street itself is the first way out, and from a shop it is the
            # only one besides the city's other shops (v1.7.1).
            rows.append((name, "🚪", "out onto the street" if here.get("shop") else "the city's own streets", -1))
        elif site_leg and name in site_leg:
            rows.append((name, "🛣️", "half a leg from here · the road's end", 5))
        elif data.get("district") and str(data.get("outside_location")) == city:
            kind = "inn" if data.get("district") == "inn" else ("gate" if data.get("gate") else "district")
            rows.append((name, "🏙️", f"in this city · {kind}", 0))
        elif data.get("shop") and str(data.get("outside_location")) == city:
            shop = WORLD.shops.get(str(data["shop"])) or {}
            rows.append((name, "🏪", f"in this city · {str(shop.get('kind') or 'shop').replace('_', ' ')}", 1))
        elif data.get("road_site"):
            leg = [str(x) for x in list(data.get("road_leg") or [])]
            near = min((hops.get(x, 99) for x in leg), default=99)
            where = "half a leg from here" if origin in leg or set(leg) == set(site_leg) else f"on the {' – '.join(leg)} road"
            rows.append((name, "🛤️", f"{ROAD_SITE_WORDS.get(str(data['road_site']), 'a place')} · {where}", 10 + near))
        elif data.get("realm_hub"):
            # `20 + (n or 50)` put the city a player is *standing in* at 70
            # (v1.0.13): a capital reached from inside its own shop is 0 hops
            # away, 0 is falsy, and `or` handed it the missing-value number.
            # The label one expression to the left already asks the right
            # question - `if n is not None` - so the rule was known and broken
            # in the same line. Reported from live play as a shop door that
            # named the street it opens onto while the picker did not offer it.
            n = hops.get(name)
            rows.append((name, "🌀", f"realm capital · {n} road hop{'s' if n != 1 else ''}" if n is not None else "realm capital", 20 + (50 if n is None else n)))
        elif name in hops:
            n = hops[name]
            rows.append((name, "🛣️", f"{n} road hop{'s' if n != 1 else ''} from here", 20 + n))
        else:
            rows.append((name, "📍", str(data.get("world") or "known place"), 90))
    rows.sort(key=lambda r: (r[3], r[0]))
    return rows

