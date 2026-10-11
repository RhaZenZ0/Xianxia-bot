"""A private gate is shown only to who knows it (v1.33.0).

The two demonic sects' gates are `private` districts of their seats
(Blood River Gorge in Riverguard City, Corpse Lantern Necropolis in Ashenwall
City, v1.19.0): the street does not show them, and a sponsor's word - a
discovery row - or standing in one does. v1.26.0 made the travel menu obey
that, and every other door of the city page went on telling it:

- **the road in** printed *"Inside the walls: Blood River Gorge, ..."* to
  everyone walking into Riverguard City, because the travel result listed the
  whole city while the map the same travel is checked against did not (the
  engine half is `cityPartsInPlainSight`, held in Go);
- **City -> Look's seat line** named the gate (`, at **Blood River Gorge**`);
- **the panel header** counted it ("3 districts" over a Look that lists two);
- **City -> Enter** returned nothing from inside a private gate, an early
  return written when these gates were wilderness places - so the way back to
  the street was never drawn - and never offered a sponsor-revealed gate,
  although the sponsor's reply sends the player to **/world -> City -> Enter**.

`_city_parts(city, known)` is the one Python statement of which parts of a city
a cultivator sees, and every door reads it. Held here: the twin against a third
computation off the raw content (v1.0.9: two wrong halves agreeing is exactly
what a test against the twin alone would pass), and the doors by behaviour, not
by the spelling of their calls (v1.0.8).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import unittest
from unittest.mock import patch

import pytest

from tests.support import PROJECT_ROOT

pytestmark = pytest.mark.unit

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS: dict[str, dict] = CONTENT["locations"]
SECTS: dict[str, dict] = CONTENT["sects"]


def _private_gates() -> list[tuple[str, str, str]]:
    """(sect, gate, seat) for every non-hidden sect whose gate is a private
    `sect_gate` district, read off the raw content."""
    out = []
    for sect, data in sorted(SECTS.items()):
        gate = str((data.get("recruitment") or {}).get("location") or "")
        place = LOCATIONS.get(gate) or {}
        if not data.get("hidden") and gate and place.get("private") and place.get("district") == "sect_gate":
            out.append((sect, gate, str(place.get("outside_location") or "")))
    return out


def _parts_by_content(city: str, known=()) -> list[str]:
    """A third statement of the rule, off the raw file: a city's gates and
    districts, less a private one nobody has told this cultivator of."""
    return sorted(name for name, data in LOCATIONS.items()
                  if data.get("district") and str(data.get("outside_location") or "") == city
                  and (not data.get("private") or name in known))


def _modules():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.exploration"), importlib.import_module("app.bot.locations")


class _Db:
    """The one read `_known_private_parts` makes, counted."""

    def __init__(self, discovered=(), boom=False):
        self.discovered = list(discovered)
        self.boom = boom
        self.reads = 0

    async def get_discovered_locations(self, _uid):
        self.reads += 1
        if self.boom:
            raise RuntimeError("engine away")
        return [{"location": name} for name in self.discovered]


class TheReaderFindsThePrivateGates(unittest.TestCase):
    def test_the_content_has_private_gates_to_hide(self):
        """Asserted before anything is trusted (rc.57): a reader that finds no
        private gate would make every assertion below vacuous."""
        gates = _private_gates()
        self.assertGreaterEqual(len(gates), 2, gates)
        for sect, gate, seat in gates:
            self.assertTrue(seat, f"{gate} stands in no city")
            self.assertEqual(SECTS[sect].get("recruitment", {}).get("public_route"), False)
        ex, loc = _modules()
        self.assertEqual(sorted(g for _s, g, _c in gates),
                         sorted(n for n, d in loc.WORLD.locations.items() if d.get("private") and d.get("district")),
                         "the world the bot loaded and the file disagree about which gates are private")


class ThePythonTwinAgreesWithTheContent(unittest.TestCase):
    def test_every_city_with_and_without_a_sponsors_word(self):
        ex, loc = _modules()
        cities = {str(d.get("outside_location")) for d in LOCATIONS.values() if d.get("district") and d.get("outside_location")}
        self.assertGreater(len(cities), 40, "the city reader is broken, not the tree")
        privates = _private_gates()
        checked = 0
        for city in sorted(cities):
            for known in [(), *[(gate,) for _s, gate, _c in privates]]:
                with self.subTest(city=city, known=known):
                    self.assertEqual(loc._city_parts(city, known), _parts_by_content(city, known))
                    checked += 1
        for sect, gate, seat in privates:
            self.assertNotIn(gate, loc._city_parts(seat), f"{seat} shows {gate}, a private gate, to somebody who was not told it")
            self.assertIn(gate, loc._city_parts(seat, (gate,)), f"{seat} hides {gate} from somebody who was told it")
            for other in cities - {seat}:
                self.assertNotIn(gate, loc._city_parts(other, (gate,)), f"{other} lists {gate}, which stands in {seat}")
        self.assertGreater(checked, 100)

    def test_the_exploration_module_uses_the_one_definition(self):
        ex, loc = _modules()
        self.assertIs(ex._city_parts, loc._city_parts)


class _Seats(unittest.TestCase):
    def _lines(self, seat, known=()):
        ex, _loc = _modules()

        async def none(*_a, **_k):
            return None

        async def empty(*_a, **_k):
            return []

        with patch.object(ex.DB, "get_territory", none), patch.object(ex.DB, "get_sect_relations", empty), \
                patch.object(ex.DB, "get_territory_wars", empty):
            return asyncio.run(ex._seat_lines(seat, known))


class TheStreetNamesNoPrivateGate(_Seats):
    def test_a_private_sect_is_named_on_its_seat_and_its_gate_is_not(self):
        for sect, gate, seat in _private_gates():
            with self.subTest(sect=sect):
                text = "\n".join(self._lines(seat))
                self.assertIn(f"**Seat:** the **{sect}**", text, "the sect is still named on its seat")
                self.assertNotIn(gate, text, f"City -> Look told the street the name of {gate}")

    def test_a_public_sect_names_its_gate(self):
        ex, loc = _modules()
        named = 0
        for sect, data in sorted(SECTS.items()):
            gate = str((data.get("recruitment") or {}).get("location") or "")
            place = LOCATIONS.get(gate) or {}
            if data.get("hidden") or not gate or place.get("private"):
                continue
            seat = str(place.get("outside_location") or "")
            with self.subTest(sect=sect):
                self.assertIn(f", at **{gate}**", "\n".join(self._lines(seat)))
                named += 1
        self.assertGreaterEqual(named, 10, "the reader found no public sect; the test is vacuous")


class ASponsoredCultivatorSeesTheirGate(_Seats):
    def test_the_seat_line_names_it(self):
        for sect, gate, seat in _private_gates():
            with self.subTest(sect=sect):
                self.assertIn(f", at **{gate}**", "\n".join(self._lines(seat, (gate,))))

    def test_enter_offers_it_as_the_gate_of_its_sect(self):
        ex, _loc = _modules()
        for sect, gate, seat in _private_gates():
            with self.subTest(sect=sect):
                self.assertNotIn(gate, [n for n, _e, _w in ex._places_to_enter(seat)])
                offered = {n: w for n, _e, w in ex._places_to_enter(seat, (gate,))}
                self.assertEqual(offered.get(gate), f"the gate of the {sect}")


class TheWayOutOfAPrivateGate(unittest.TestCase):
    def test_the_street_and_the_citys_other_parts_are_offered(self):
        ex, loc = _modules()
        for sect, gate, seat in _private_gates():
            for known in ((), (gate,)):
                with self.subTest(sect=sect, known=known):
                    rows = ex._places_to_enter(gate, known)
                    self.assertTrue(rows, f"standing in {gate} there is no way out drawn")
                    self.assertEqual(rows[0][0], seat, "the streets come first")
                    self.assertNotIn(gate, [r[0] for r in rows], "the place you stand in is not offered")
                    for name, _emoji, _what in rows:
                        self.assertTrue(loc.door_allows(gate, name), f"{gate} offers {name}, which travel refuses")
                        self.assertEqual(ex._city_of(name), seat)
                    self.assertGreater(len(rows), 3, "a private gate offers the street and nothing else")

    def test_a_private_room_still_offers_nothing(self):
        ex, _loc = _modules()
        for room in ("birth_family:tomb_watch_clan", "abode:5", "personal_world:7"):
            self.assertEqual(ex._places_to_enter(room), [], room)


class TheHeaderCountsWhatTheLookLists(unittest.TestCase):
    def test_every_city_header_counts_the_parts_in_plain_sight(self):
        ex, loc = _modules()
        seen = 0
        for city in sorted({str(d.get("outside_location")) for d in LOCATIONS.values() if d.get("district") and d.get("outside_location")}):
            parts = _parts_by_content(city)
            if not parts:
                continue
            gates = [p for p in parts if LOCATIONS[p].get("gate")]
            districts = [p for p in parts if not LOCATIONS[p].get("gate")]
            header = loc.here_summary(city, limit=400)
            match = re.search(r"(\d+) gates?, (\d+) districts?", header)
            with self.subTest(city=city):
                self.assertIsNotNone(match, header)
                self.assertEqual((int(match.group(1)), int(match.group(2))), (len(gates), len(districts)), header)
                seen += 1
        self.assertGreater(seen, 40)
        for _sect, gate, seat in _private_gates():
            plain = len(_parts_by_content(seat)) + 1
            self.assertEqual(len(_parts_by_content(seat, (gate,))), plain, "the fixture is broken: the private gate is not a part of its seat")


class TheKnowledgeIsReadOnlyWhereItCanMatter(unittest.TestCase):
    def _private(self, city, db, location="Greenriver Town"):
        _ex, loc = _modules()
        with patch.object(loc, "DB", db):
            return asyncio.run(loc._known_private_parts(7, {"location": location, "realm_index": 0, "phase": 1}, city))

    def test_a_city_with_no_private_part_costs_no_read(self):
        db = _Db()
        self.assertEqual(self._private("Cloudblade City", db), frozenset())
        self.assertEqual(self._private("Azure Crown Imperial City", db), frozenset())
        self.assertEqual(db.reads, 0, "a city with nothing to hide paid for a discovery read")

    def test_a_city_with_one_pays_for_one_read(self):
        for _sect, gate, seat in _private_gates():
            db = _Db()
            self.assertEqual(self._private(seat, db), frozenset())
            self.assertEqual(db.reads, 1)
            self.assertEqual(self._private(seat, _Db(discovered=[gate])), frozenset({gate}), "a sponsor's word is not enough")
            self.assertEqual(self._private(seat, _Db(), location=gate), frozenset({gate}), "standing in the gate is knowing it")

    def test_another_citys_gate_is_not_this_citys(self):
        (_s1, gate1, seat1), (_s2, gate2, seat2) = _private_gates()[:2]
        self.assertEqual(self._private(seat1, _Db(discovered=[gate2])), frozenset())

    def test_it_never_raises(self):
        for _sect, _gate, seat in _private_gates():
            self.assertEqual(self._private(seat, _Db(boom=True)), frozenset(),
                             "a failed read must leave the street's answer, not cost the city page")


class TheJournalNamesTheGateOnlyToWhoKnowsIt(unittest.TestCase):
    """`npc_whereabouts` asks `_known_locations`, which asks `_city_parts`: an
    NPC standing at a private gate is "somewhere you have not been" to a
    stranger and is named to somebody a sponsor told."""

    def _says(self, sponsored):
        _ex, loc = _modules()
        _sect, gate, seat = _private_gates()[0]

        class Sim:
            async def npc_status(self, _name):
                return {"status": "alive"}

        async def where(_name, period=None):
            return gate

        db = _Db(discovered=[gate] if sponsored else [])
        with patch.object(loc, "SIM", Sim()), patch.object(loc, "current_npc_location", where), patch.object(loc, "DB", db):
            return gate, asyncio.run(loc.npc_whereabouts(7, {"location": seat, "realm_index": 0, "phase": 1}, "Anyone"))

    def test_a_stranger_is_not_told_where_the_gate_is(self):
        gate, said = self._says(False)
        self.assertNotIn(gate, said)
        self.assertIn("somewhere you have not been", said)

    def test_a_sponsored_cultivator_is(self):
        gate, said = self._says(True)
        self.assertIn(gate, said)


class _Interaction:
    def __init__(self):
        self.user = type("User", (), {"id": 7})()
        self.sent: list[dict] = []
        outer = self

        class Response:
            async def send_message(self, content=None, **kwargs):
                outer.sent.append({"content": content, **kwargs})

        self.response = Response()


class TheDoorsAskWhatThePlayerKnows(unittest.TestCase):
    """City -> Look and both Enter pickers, driven at Riverguard City with the
    character and the discoveries patched on the shared `DB`. Not a read of the
    calls' spelling (v1.0.8): a door that drops what the player knows fails
    here, whatever it is written like."""

    def _door(self, door, *, sponsored, location=None):
        ex, loc = _modules()
        sect, gate, seat = _private_gates()[0]
        character = {"user_id": 7, "location": location or seat, "realm_index": 0, "phase": 1, "name": "Tester"}

        async def get_character(_uid):
            return character

        async def discovered(_uid):
            return [{"location": gate}] if sponsored else []

        async def require(_interaction):
            return character

        async def nobody(*_a, **_k):
            return []

        async def none(*_a, **_k):
            return None

        class Sim:
            async def civilization_status(self, _city):
                return {}

        shown = {}

        class Seen:
            def __init__(self, card, places):
                shown["text"] = card.description
                shown["places"] = [name for name, _e, _w in places]

        with patch.object(ex.DB, "get_character", get_character), patch.object(ex.DB, "get_discovered_locations", discovered), \
                patch.object(ex.DB, "get_territory", none), patch.object(ex.DB, "get_sect_relations", nobody), \
                patch.object(ex.DB, "get_territory_wars", nobody), patch.object(ex, "require_character", require), \
                patch.object(ex, "npcs_present", nobody), patch.object(ex, "SIM", Sim()), patch.object(ex, "CityLookView", Seen):
            interaction = _Interaction()
            result = asyncio.run(door(ex, interaction))
        return gate, shown, result, interaction

    def test_look_names_the_gate_only_to_a_sponsored_cultivator(self):
        async def look(ex, interaction):
            await ex.city_look.callback(interaction)

        for sponsored in (False, True):
            gate, shown, _result, interaction = self._door(look, sponsored=sponsored)
            text = shown.get("text") or "\n".join(str(m.get("content")) for m in interaction.sent)
            districts = next((line for line in text.splitlines() if line.startswith("**Districts:**")), "")
            seat = next((line for line in text.splitlines() if line.startswith("**Seat:**")), "")
            with self.subTest(sponsored=sponsored):
                self.assertTrue(text, "City -> Look said nothing")
                self.assertTrue(districts and seat, f"the Districts or the Seat line is missing: {text}")
                # Each line is its own door: a Look that told one and not the
                # other is half a fix.
                self.assertEqual(gate in districts, sponsored, f"the Districts line {'hides' if sponsored else 'names'} {gate}: {districts}")
                self.assertEqual(gate in seat, sponsored, f"the Seat line {'hides' if sponsored else 'names'} {gate}: {seat}")
                self.assertEqual(gate in shown.get("places", []), sponsored, f"the Look buttons {'lack' if sponsored else 'offer'} {gate}: {shown}")

    def test_both_enter_pickers_offer_the_gate_only_to_a_sponsored_cultivator(self):
        async def autocomplete(ex, interaction):
            return [choice.value for choice in await ex.city_enter_autocomplete(interaction, "")]

        async def hub_options(ex, interaction):
            return [option.value for option in await ex.city_enter_hub_options(interaction, "")]

        for door in (autocomplete, hub_options):
            for sponsored in (False, True):
                gate, _shown, offered, _i = self._door(door, sponsored=sponsored)
                with self.subTest(door=door.__name__, sponsored=sponsored):
                    self.assertTrue(offered, "the picker offered nothing from the city's own streets")
                    self.assertEqual(gate in offered, sponsored,
                                     f"{'a sponsored cultivator was not offered' if sponsored else 'a stranger was offered'} {gate}: {offered}")

    def test_from_inside_the_gate_both_pickers_draw_the_way_out(self):
        async def autocomplete(ex, interaction):
            return [choice.value for choice in await ex.city_enter_autocomplete(interaction, "")]

        async def hub_options(ex, interaction):
            return [option.value for option in await ex.city_enter_hub_options(interaction, "")]

        _sect, gate, seat = _private_gates()[0]
        for door in (autocomplete, hub_options):
            _g, _shown, offered, _i = self._door(door, sponsored=False, location=gate)
            with self.subTest(door=door.__name__):
                self.assertEqual(offered[:1], [seat], "from inside a private gate the street is not offered first")
                self.assertGreater(len(offered), 3)


if __name__ == "__main__":
    unittest.main()
