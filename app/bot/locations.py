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

from typing import Any

import discord
from discord import app_commands

from ..rules.realm_hubs import REALM_HUBS
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


async def current_npc_location(npc_name: str, period: str | None = None) -> str | None:
    """Resolve the mechanical NPC location from initialized simulation state.

    Daily world.json schedules still shape an NPC's routine while they remain in
    their home region, but there is no legacy no-simulation fallback anymore.
    """
    # A hidden master who walks the road is wherever their circuit puts them
    # this month, and that answer outranks both the daily schedule and the
    # civilization simulation: these are recluses crossing the world on their
    # own business, not townsfolk on a routine. It is a pure function of the
    # canonical clock, so nothing has to tick to move them.
    walking = WORLD.npcs.get(npc_name, {}).get("circuit")
    if walking:
        wt = await current_world_time()
        stop = circuit_stop(
            walking, int(getattr(wt, "total_minutes", 0)),
            months=int(WORLD.npcs[npc_name].get("circuit_months", 2) or 2),
            offset=int(WORLD.npcs[npc_name].get("circuit_offset", 0) or 0),
        )
        if stop:
            return stop

    sim_state = await SIM.npc_status(npc_name)
    if period is None:
        period = (await current_world_time()).period
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
            return WORLD.npc_location_at(npc_name, period) or current
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
    return None


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


def access_realm_index(character: dict[str, Any]) -> int:
    """The cultivation a *place* is measured against.

    The world-crossing tribulation is gated on either ladder - a body
    cultivator clears the Mortal Body Ascension and breaks into the Spiritual
    World's body realm exactly as a qi cultivator clears theirs - but every
    location check read `realm_index` alone, so a body cultivator could
    ascend into a world and then be locked out of it. Mirrors the engine's
    `mechanicsCharacter.accessRealmIndex`.
    """
    return max(int(character.get("realm_index", 0) or 0), int(character.get("body_realm_index", 0) or 0))


def _world_is_unlocked(character: dict[str, Any], world_name: str) -> bool:
    return access_realm_index(character) >= _world_min_realm_index(str(world_name))


async def _known_locations(user_id: int, character: dict[str, Any]) -> set[str]:
    rows = await DB.get_discovered_locations(int(user_id))
    known = {str(row.get("location")) for row in rows if row.get("location")}
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
        for name, data in WORLD.locations.items():
            if data.get("district") and str(data.get("outside_location")) == city:
                known.add(name)
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


async def _location_is_visible(user_id: int, character: dict[str, Any], location: str) -> bool:
    data = WORLD.locations.get(str(location)) or await DB.get_location_definition(str(location))
    if not data:
        return False
    if not _world_is_unlocked(character, str(data.get("world") or WORLD.realm_world(access_realm_index(character)))):
        return False
    return str(location) in await _known_locations(user_id, character)


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
    for name in await DB.search_catalog("npc", current, 25):
        if name in names:
            continue
        npc_location = await current_npc_location(name, wt.period)
        if npc_location == DEAD:
            continue
        if location and npc_location and npc_location != location:
            continue
        if not needle or needle in name.casefold():
            names.append(name)
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


def here_summary(location: str, limit: int = 180) -> str:
    """One line on what the place you stand in is and offers, for the panel
    header (v0.40.0). Pure: reads the catalogue only. Private places
    (abodes, personal worlds, households) answer nothing - their own hubs
    describe them."""
    name = str(location or "")
    data = WORLD.locations.get(name)
    if not data:
        return ""
    people = sorted(n for n, npc in WORLD.npcs.items() if str(npc.get("location")) == name)
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
    elif data.get("district"):
        what = f"the {str(data['district']).replace('_', ' ')} of {city}" if data["district"] != "inn" else f"the inn of {city}"
    elif any(str((sect.get("recruitment") or {}).get("location")) == name for sect in WORLD.sects.values()):
        sect_name = next(s for s, sect in WORLD.sects.items() if str((sect.get("recruitment") or {}).get("location")) == name)
        what = f"the gate of the {sect_name} · trials before its examiner"
    else:
        parts = sorted(n for n, d in WORLD.locations.items() if d.get("district") and str(d.get("outside_location")) == name)
        gates = [p for p in parts if WORLD.locations[p].get("gate")]
        districts = [p for p in parts if not WORLD.locations[p].get("gate")]
        roads = [str(r) for r in list(data.get("roads") or [])]
        if parts:
            what = f"a {'capital' if data.get('realm_hub') else str(data.get('settlement_type') or 'city')} · {len(gates)} gate{'s' if len(gates) != 1 else ''}, {len(districts)} district{'s' if len(districts) != 1 else ''}"
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
        if not data or int(data.get("min_realm_index", 0)) > int(realm_index) or data.get("auction_house"):
            continue
        if site_leg and name in site_leg:
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
            n = hops.get(name)
            rows.append((name, "🌀", f"realm capital · {n} road hop{'s' if n != 1 else ''}" if n is not None else "realm capital", 20 + (n or 50)))
        elif name in hops:
            n = hops[name]
            rows.append((name, "🛣️", f"{n} road hop{'s' if n != 1 else ''} from here", 20 + n))
        else:
            rows.append((name, "📍", str(data.get("world") or "known place"), 90))
    rows.sort(key=lambda r: (r[3], r[0]))
    return rows

