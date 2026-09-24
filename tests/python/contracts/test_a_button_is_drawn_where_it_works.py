"""A button the engine would refuse where you stand is not drawn there (v1.1.0).

Reported from play: in the birth household, `/economy → City Shops → Browse`
answered *"not inside a shop; find one by exploring a city"*. The panel had
drawn a button whose only possible answer, in that room, was no. Asked to do
the same for every such button, the owner chose all of them, and to keep
City Shops **Here** - it never refuses, and it is the door that says where a
city's shops are.

`LOCATION_GATES` in `surface.py` is the table, and `_location_hidden_actions`
asks each gate with the engine's own question. Three things are held here.

- **The twins answer what the engine answers, everywhere.** Each predicate
  that is a copy of a Go helper - `shopAt`, the auction door's `cityOf`-plus-
  shop override, `sectGate` - is computed a third time off the raw content
  file, from the Go rule rather than from the Python one, over all 477
  locations. Two wrong halves agreeing with each other is exactly what a
  behavioural test against the Python alone would pass.
- **The rooms a player stands in get the right panel.** The provider is
  driven over a household, a city street, a shop, an auction floor, an inn and
  a sect gate, and asked what it hides - including that it never hides
  `shop here` or a status read anywhere.
- **The copies that exist stay copies.** Boss lairs are said in Go and in
  Python and nothing held them equal; the private-room prefixes are said in
  Go at every handler and in `PRIVATE_LOCATION_EXITS`.

The engine stays the refusal. Hiding is advertising, never a bound (v1.0.9):
a leaf typed directly still reaches the engine, which still says no.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS: dict = CONTENT["locations"]
GO = PROJECT_ROOT / "go_core" / "internal" / "game"


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


def go_city_of(location: str) -> str:
    """`cityOf`, exploration_actions.go, written from the Go."""
    loc = LOCATIONS.get(location)
    if loc and loc.get("outside_location") and (loc.get("district") or loc.get("shop") or loc.get("auction_house")):
        return str(loc["outside_location"])
    return location


def go_shop_at(location: str) -> bool:
    """`shopAt`, shop_actions.go: the location names a shop the catalogue has."""
    loc = LOCATIONS.get(location)
    return bool(loc and loc.get("shop") and loc["shop"] in CONTENT["shops"])


def go_auction_door(location: str) -> bool:
    """`auctionEnterAction`, economy_actions.go: `cityOf`, blanked in a shop,
    against every house's `entrance_location`."""
    here = go_city_of(location)
    if (LOCATIONS.get(location) or {}).get("shop"):
        here = ""
    return any(h.get("entrance_location") == here for h in CONTENT["auction_houses"].values()) and here != ""


def go_sect_gate(location: str) -> bool:
    """`sectGate`, sect_doors.go: not hidden, and a real place."""
    return any(not s.get("hidden") and str((s.get("recruitment") or {}).get("location") or "").strip() == location
               and location in LOCATIONS for s in CONTENT["sects"].values())


def hides(location: str, *, uid: int = 42, manor: dict | None = None) -> dict[str, str]:
    surface = _surface()

    async def member_manor(_uid):
        return manor

    interaction = SimpleNamespace(user=SimpleNamespace(id=uid))
    with patch.object(surface.DB, "get_member_sect_manor", member_manor):
        return asyncio.run(surface._location_hidden_actions(interaction, {"location": location}))


class TheTwinsAnswerWhatTheEngineAnswers(unittest.TestCase):
    def test_the_reader_sees_the_world(self):
        """Asserted before it is trusted (rc.57)."""
        self.assertEqual(len(LOCATIONS), 477, "the content reader found a different world; re-check the counts")
        self.assertTrue(go_shop_at("Jadewood Apothecary"))
        self.assertTrue(go_auction_door("Greenriver Town"))
        self.assertTrue(go_sect_gate("Azure Cloud Mountain Gate"))

    def test_every_location_agrees_with_the_go_rule(self):
        surface = _surface()
        for name in sorted(LOCATIONS):
            with self.subTest(location=name):
                self.assertEqual(bool(surface._shop_at(name)), go_shop_at(name), "shopAt")
                self.assertEqual(surface._auction_entrance_here(name), go_auction_door(name), "the auction door")
                self.assertEqual(surface._public_sect_gate_here(name), go_sect_gate(name), "sectGate")
                self.assertEqual(surface._city_of(name), go_city_of(name), "cityOf")

    def test_a_hidden_sect_sits_no_public_trial(self):
        """The shipped hidden sect carries no recruitment at all, so the sweep
        above cannot see `sectGate`'s hidden check: dropping it from the twin
        left every location agreeing (the drill said so). A hidden sect with a
        gate on a real street is planted here instead."""
        surface = _surface()
        planted = {"Planted Hidden Sect": {"hidden": True, "recruitment": {"location": "Greenriver Town"}}}
        with patch.dict(surface.WORLD.data["sects"], planted):
            self.assertFalse(surface._public_sect_gate_here("Greenriver Town"))

    def test_a_private_room_is_no_shop_no_door_and_no_gate(self):
        surface = _surface()
        for place in ("birth_family:3", "abode:7", "sect_abode:42", "personal_world:42"):
            with self.subTest(place=place):
                self.assertFalse(surface._shop_at(place))
                self.assertFalse(surface._auction_entrance_here(place))
                self.assertFalse(surface._public_sect_gate_here(place))


class TheRoomGetsTheRightPanel(unittest.TestCase):
    def test_the_household_hides_the_shop_counter_and_the_road(self):
        """The report itself."""
        shut = hides("birth_family:3")
        for leaf in ("/shop browse", "/shop buy", "/shop sell", "/explore", "/hunt", "/travel go",
                     "/city accept", "/auction bid", "/trade offer"):
            self.assertIn(leaf, shut, f"{leaf} is refused in a household and was drawn there")
        self.assertIn("no shops here", shut["/shop browse"])
        self.assertIn("**/family → Leave**", shut["/explore"], "a private room names its own way out")
        self.assertNotIn("/seclusion start", shut, "the household is a seclusion site")

    def test_a_city_street_keeps_the_road_and_hides_the_counter(self):
        shut = hides("Greenriver Town")
        for leaf in ("/explore", "/hunt", "/travel go", "/auction enter", "/battle challenge"):
            self.assertNotIn(leaf, shut, f"{leaf} works on Greenriver's street")
        self.assertIn("/shop browse", shut)
        self.assertIn("find Greenriver Town's with Here", shut["/shop browse"])
        self.assertIn("/seclusion start", shut, "Greenriver Town is deliberately rough ground")

    def test_a_shop_draws_its_counter_and_its_examination(self):
        shut = hides("Jadewood Apothecary")
        for leaf in ("/shop browse", "/shop buy", "/shop sell", "/profession exam"):
            self.assertNotIn(leaf, shut, f"{leaf} works inside an apothecary")
        self.assertIn("/auction enter", shut, "the engine blanks the door from inside a shop")

    def test_an_auction_floor_draws_its_floor_and_suppresses_violence(self):
        house = CONTENT["auction_houses"]["golden_pavilion"]["location"]
        shut = hides(house)
        for leaf in ("/auction leave", "/auction bid", "/auction sell", "/auction browse", "/auction enter"):
            self.assertNotIn(leaf, shut, f"{leaf} works on the floor of {house}")
        self.assertIn("/battle challenge", shut)
        self.assertIn("/duel challenge", shut)

    def test_an_inn_draws_the_long_table(self):
        inn = next(k for k, v in LOCATIONS.items() if v.get("district") == "inn")
        self.assertNotIn("/trade offer", hides(inn))

    def test_a_sect_gate_draws_its_trial(self):
        self.assertNotIn("/sect recruitment trial", hides("Azure Cloud Mountain Gate"))
        self.assertIn("/sect recruitment trial", hides("Greenriver Town"))

    def test_your_own_world_and_your_manor(self):
        self.assertNotIn("/innerworld leave", hides("personal_world:42", uid=42))
        self.assertIn("/innerworld leave", hides("personal_world:43", uid=42))
        self.assertNotIn("/seclusion start", hides("Greenriver Town", manor={"base_location": "Greenriver Town"}))

    def test_here_and_the_status_reads_are_never_hidden(self):
        """`shop here` is how a player learns where a city's shops are, and a
        status read is how they learn a system exists (rc.32)."""
        for place in ("birth_family:3", "Greenriver Town", "Jadewood Apothecary", "Azure Cloud Mountain Gate",
                      "Wayside Shrine of the Quiet Pine", "Halfmoon Waystation"):
            shut = hides(place)
            with self.subTest(place=place):
                self.assertNotIn("/shop here", shut)
                self.assertEqual([p for p in shut if p.endswith(" status")], [])


class TheCopiesStayCopies(unittest.TestCase):
    def test_every_boss_lair_is_the_engines(self):
        """`bossTemplatesGo` and `BOSS_TEMPLATES` both say where each great
        beast is fought, and nothing held them equal; the panel reads the
        Python one to decide where Start is drawn."""
        source = (GO / "group_combat_actions.go").read_text(encoding="utf-8")
        go = dict(re.findall(r'"([a-z_]+)":\s*\{"[^"]+",\s*"([^"]+)"', source))
        self.assertGreaterEqual(len(go), 3, "the Go boss table was not read; the reader is broken, not the tree")
        surface = _surface()
        self.assertEqual({k: v["location"] for k, v in surface.BOSS_TEMPLATES.items()}, go)

    def test_the_private_prefixes_are_the_engines(self):
        surface = _surface()
        runtime = importlib.import_module("app.bot.runtime")
        self.assertEqual(set(surface.PRIVATE_PREFIXES), {p for p, _, _ in runtime.PRIVATE_LOCATION_EXITS})
        explore = (GO / "exploration_actions.go").read_text(encoding="utf-8")
        refusal = next(line for line in explore.splitlines()
                       if "world exploration is unavailable inside a private residence" in line
                       or ("HasPrefix(c.Location" in line and "birth_family:" in line))
        for prefix in surface.PRIVATE_PREFIXES:
            self.assertIn(f'"{prefix}"', refusal, f"{prefix} is not a prefix the engine refuses exploring inside")

    def test_a_sect_residence_names_its_own_way_out(self):
        """`/abode → Leave` reads `cave_abodes`, and a sect residence is a
        `sect_abodes` row: the old exit refused the player it was shown to."""
        runtime = importlib.import_module("app.bot.runtime")
        command, _ = runtime.private_location_exit("sect_abode:42")
        self.assertNotIn("/abode → Leave", command)
        self.assertIn("/sect → Holdings → Abode", command)

    def test_a_guests_way_out_is_not_hidden_for_owning_no_home(self):
        """`abode leave` and `abode focus` work for an invited guest; they
        were hidden under "you have no property yet"."""
        surface = _surface()
        self.assertNotIn("abode leave", surface.PROGRESSION_GATES["abode"])
        self.assertNotIn("abode focus", surface.PROGRESSION_GATES["abode"])
        self.assertNotIn("/abode leave", hides("abode:7"))


if __name__ == "__main__":
    unittest.main()
