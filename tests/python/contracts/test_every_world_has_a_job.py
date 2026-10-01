"""Each world above the Mortal has a job (v1.17.0).

The world-flow study called the three upper worlds "the Mortal World again
with less in it": the same eleven cities, the same shops and halls, and nothing
a rule read that the Mortal World did not also have. Each carries four things
of its own now - a district kind that stands in that world and nowhere else and
is read by one engine rule, a raid boss in the wilds of one of its cities, a
secret realm at that wild place, and a key to it on the shelves of the world's
array workshops.

This file holds the content to that shape and the Python side to the engine's
answers: the boss twin, the key on a shelf in the realm's world, the reply
lines that name the place and its bonus rather than restating either. The
rules themselves - a Law read more clearly in the archive, a craft made better
in the court, a breakthrough heard sooner on the altar, a raid paid in the
money of its world - are `world_jobs_test.go`, driven against the shipped
catalogue.
"""
from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from app.rules.advanced_runtime import BOSS_TEMPLATES, boss_lair
from tests.support import PROJECT_ROOT, code_only

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS = CONTENT["locations"]
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
BOT = PROJECT_ROOT / "app" / "bot" / "commands"
MORTAL = "Mortal World"


def upper_worlds() -> list[str]:
    return sorted({str(loc.get("world")) for loc in LOCATIONS.values() if loc.get("world") and loc.get("world") != MORTAL})


def job_district(world: str) -> tuple[str, str]:
    """The one district kind that stands in `world` and nowhere else, and the
    district carrying it - read off the content rather than a list."""
    worlds_by_kind: dict[str, set[str]] = {}
    name_by_kind: dict[str, str] = {}
    for place, loc in LOCATIONS.items():
        kind = str(loc.get("district") or "")
        if kind in ("", "gate", "inn"):
            continue
        worlds_by_kind.setdefault(kind, set()).add(str(loc.get("world")))
        if loc.get("world") == world:
            name_by_kind[kind] = place
    own = [k for k, worlds in worlds_by_kind.items() if worlds == {world}]
    return (own[0], name_by_kind[own[0]]) if len(own) == 1 else ("", "")


def go_readers() -> dict[str, str]:
    """`district kind -> Go file` for every `District == "<kind>"` the engine
    reads inside `jobDistrictAt`'s callers, off the source rather than a copy."""
    source = code_only_go((GO / "world_jobs.go").read_text(encoding="utf-8"))
    kinds = dict(re.findall(r'(district\w+)\s*=\s*"(\w+)"', source))
    readers: dict[str, str] = {}
    for const, kind in kinds.items():
        for fn in ("lawPlaceBonus", "craftPlaceBonus", "breakthroughPlaceBonus"):
            body = source.split(f"func {fn}(", 1)[1].split("\nfunc ", 1)[0] if f"func {fn}(" in source else ""
            if f"jobDistrictAt(catalog, location, {const})" in body:
                readers[kind] = fn
    return readers


def code_only_go(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def function_source(path: Path, name: str) -> str:
    """One function's statements, without its docstring (rc.52), unparsed."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                     and isinstance(getattr(node.body[0], "value", None), ast.Constant)) else node.body
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError(f"{name} not found in {path.name}; the reader is broken, not the tree")


class TheContentWasRead(unittest.TestCase):
    def test_three_worlds_stand_above_the_mortal(self):
        self.assertEqual(upper_worlds(), ["Celestial World", "Immortal World", "Spiritual World"],
                         "the content reader found a different world; the gate is broken, not the tree")

    def test_the_go_readers_were_found(self):
        self.assertEqual(sorted(go_readers()), ["altar", "archive", "court"],
                         "the reader could not find the three district rules in world_jobs.go; the gate is broken, not the tree")


class EachUpperWorldHasAJob(unittest.TestCase):
    def test_a_district_kind_of_its_own_read_by_a_rule(self):
        readers = go_readers()
        kinds_seen = set()
        for world in upper_worlds():
            kind, district = job_district(world)
            with self.subTest(world=world):
                self.assertTrue(kind, f"{world} has no district kind that stands in it and nowhere else")
                self.assertIn(kind, readers, f"{world}'s own district kind {kind!r} is read by no rule in world_jobs.go")
                self.assertNotIn(kind, kinds_seen, "two worlds share one job")
                kinds_seen.add(kind)
                loc = LOCATIONS[district]
                self.assertTrue(loc.get("safe_zone"), f"{district} is a district of a city and is guarded like one")
                self.assertIn(loc.get("outside_location"), LOCATIONS, f"{district} is a district of nowhere")
        self.assertEqual(set(readers) - kinds_seen, set(), "a rule reads a district kind no world carries")

    def test_a_raid_of_its_own_in_the_wilds_of_one_of_its_cities(self):
        for world in upper_worlds():
            lairs = []
            for key, boss in BOSS_TEMPLATES.items():
                lair, _ = boss_lair(boss, CONTENT["secret_realms"])
                if LOCATIONS.get(lair, {}).get("world") == world:
                    lairs.append((key, lair))
            with self.subTest(world=world):
                self.assertEqual(len(lairs), 1, f"{world} has {len(lairs)} raids: {lairs}")
                key, lair = lairs[0]
                self.assertTrue(LOCATIONS[lair].get("wilds_of"), f"{key}'s lair {lair} is not in the wilds of a city")
                self.assertEqual(LOCATIONS[LOCATIONS[lair]["wilds_of"]].get("world"), world, "the wilds belong to a city of another world")
                floor = int(LOCATIONS[next(n for n, l in LOCATIONS.items() if l.get("realm_hub") and l.get("world") == world)]["min_realm_index"])
                self.assertGreaterEqual(int(BOSS_TEMPLATES[key]["realm_index"]), floor, f"{key} asks a realm below its world's floor")

    def test_a_keyed_realm_of_its_own_with_the_key_on_a_shelf_in_that_world(self):
        for world in upper_worlds():
            keys = []
            for item_id, item in CONTENT["items"].items():
                spatial = item.get("spatial_key") or {}
                realm = CONTENT["secret_realms"].get(str(spatial.get("secret_realm_id") or ""))
                if realm and LOCATIONS.get(realm["location"], {}).get("world") == world:
                    keys.append((item_id, realm))
            with self.subTest(world=world):
                self.assertEqual(len(keys), 1, f"{world} has {len(keys)} keyed realms: {[k for k, _ in keys]}")
                item_id, realm = keys[0]
                self.assertTrue(LOCATIONS[realm["location"]].get("wilds_of"), f"{realm['name']} opens somewhere no explore from a city can find")
                shelves = [sid for sid, shop in CONTENT["shops"].items()
                           if shop.get("world") == world and any(line["item_id"] == item_id for line in shop["sells"])]
                self.assertTrue(shelves, f"{item_id} is on no shelf in {world}")
                # A key makes every room a purchase, so the realm holds no rare find (rc.50's rule).
                self.assertFalse(any(room.get("rare_items") for room in realm["rooms"]), f"{realm['name']} sells a key and holds a rare find")


class TheRepliesNameWhatTheEngineAnswered(unittest.TestCase):
    """Each reply prints the place and the bonus the engine named, and
    neither restates a district kind or a number of its own."""

    def _reply(self, filename: str, function: str) -> str:
        return code_only(function_source(BOT / filename, function))

    def test_the_law_reply_names_the_archive(self):
        source = self._reply("law.py", "law_comprehend")
        self.assertIn("place_bonus", source, "the Law reply no longer reads the engine's place bonus")
        self.assertNotIn("archive", source.lower(), "the Law reply restates the district kind the engine names")

    def test_the_craft_reply_names_the_court(self):
        source = self._reply("exploration.py", "_run_crafting")
        self.assertIn("place_bonus", source, "the craft reply no longer reads the engine's place bonus")
        self.assertNotIn("court", source.lower(), "the craft reply restates the district kind the engine names")

    def test_both_breakthrough_replies_name_the_altar(self):
        for function in ("breakthrough", "body_breakthrough"):
            with self.subTest(function=function):
                source = self._reply("cultivation.py", function)
                self.assertIn("place_bonus", source, f"{function} no longer reads the engine's place bonus")
                self.assertNotIn("altar", source.lower(), f"{function} restates the district kind the engine names")

    def test_the_raid_card_names_the_lairs_worlds_coin(self):
        source = self._reply("boss.py", "raid_card")
        self.assertIn("world_base_currency", source, "the raid card no longer reads the lair's world's coin")
        self.assertNotIn("Low Spirit Stones", source, "the raid card names the Mortal stone for a raid in another world")

    def test_the_claim_line_reads_the_engines_coin(self):
        source = self._reply("boss.py", "_claim_line")
        self.assertIn("result.get('currency')", source, "the claim line no longer reads the coin the engine paid")
        self.assertNotIn("Low Spirit Stones", source, "the claim line names the Mortal stone whatever world the raid was in")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
