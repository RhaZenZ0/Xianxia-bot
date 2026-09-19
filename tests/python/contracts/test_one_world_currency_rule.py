"""Which money a world uses is said once, in the content file (v1.0.0-rc.44).

`content/world.json` declares sixteen currencies, each with the `world` it
belongs to and its `tier`. Which of them an ordinary price is denominated in is
therefore a fact the file already states - and it was restated, in code, five
times:

    go_core/internal/game/progression_actions.go   tribulationCurrency(world)
    go_core/internal/simulation/bootstrap.go       worldCurrency(world)
    go_core/internal/game/lifecycle_actions.go     an inline four-way map
    go_core/internal/simulation/world.go           another inline four-way map
    app/dashboard/server.py                        a four-element tuple

Four switches and a tuple, none of which could be wrong in an interesting way
until a fifth world is added or a currency renamed - at which point four of them
are silently stale and the fifth is a list nobody remembers writing. It is the
same shape as the world clock's "four copies in Go and two in Python" that
v1.0.0-rc.39 collapsed, and it is collapsed the same way: one reader
(`worldBaseCurrency`, exported as `game.WorldBaseCurrency` for the simulation
package, and `_base_currencies()` on the dashboard) and nothing else.

This is the gate. A source file naming three or more of the four base
currencies is almost certainly restating the mapping, and the allowlist is
empty: no production file does. Tests are exempt by construction - asserting the
mapping is exactly what `currency_worlds_test.go` is for.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

SEARCH_ROOTS = ("app", "go_core", "scripts", "dashboard")
SEARCH_SUFFIXES = (".py", ".go", ".js")

# A file that restates the mapping anyway, with the reason it may. Empty, and
# empty the day it was written.
RESTATES_THE_MAPPING: dict[str, str] = {}


def _base_currency_ids() -> list[str]:
    world = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
    return sorted(
        str(key)
        for key, entry in (world.get("currencies") or {}).items()
        if isinstance(entry, dict) and int(entry.get("tier") or 0) == 1
    )


def _is_test(path) -> bool:
    name = path.name
    return name.endswith("_test.go") or name.startswith("test_") or "tests" in path.parts


class TheMappingLivesInTheContentFile(unittest.TestCase):
    def test_the_content_file_declares_one_base_currency_per_world(self):
        """The gate below is only meaningful while this holds."""
        world = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
        worlds: dict[str, list[str]] = {}
        for key, entry in (world.get("currencies") or {}).items():
            if int(entry.get("tier") or 0) == 1:
                worlds.setdefault(str(entry.get("world") or ""), []).append(str(key))
        self.assertGreaterEqual(len(worlds), 4, "the four worlds each need a tier-1 currency")
        for name, ids in sorted(worlds.items()):
            with self.subTest(world=name):
                self.assertTrue(name, "a tier-1 currency belongs to no world")
                self.assertEqual(len(ids), 1, f"{name} has more than one base currency: {ids}")

    def test_no_production_file_restates_which_money_a_world_uses(self):
        base = _base_currency_ids()
        self.assertGreaterEqual(len(base), 4)
        offenders = []
        for root in SEARCH_ROOTS:
            for path in sorted((PROJECT_ROOT / root).rglob("*")):
                if not path.is_file() or path.suffix not in SEARCH_SUFFIXES:
                    continue
                if "__pycache__" in path.parts or _is_test(path):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                named = [currency for currency in base if currency in text]
                if len(named) < 3:
                    continue
                relative = str(path.relative_to(PROJECT_ROOT))
                if relative in RESTATES_THE_MAPPING:
                    continue
                offenders.append(f"{relative} names {len(named)} of the {len(base)}")
        self.assertEqual(offenders, [], (
            "these restate which currency a world uses, which content/world.json already declares. "
            "Read it through worldBaseCurrency (Go) or _base_currencies() (the dashboard), or name "
            f"the file in RESTATES_THE_MAPPING with the reason it may: {offenders}"))

    def test_the_allowlist_names_files_that_exist_and_says_why(self):
        for relative, reason in RESTATES_THE_MAPPING.items():
            self.assertTrue((PROJECT_ROOT / relative).exists(), f"{relative} no longer exists")
            self.assertGreaterEqual(len(reason.strip()), 20, f"{relative} needs a reason worth reading")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
