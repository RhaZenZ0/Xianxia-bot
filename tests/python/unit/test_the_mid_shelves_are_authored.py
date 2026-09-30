"""The capitals' Mid-grade shelf lines are what the generator would write (v1.12.3).

`scripts/author_mid_shelves.py` adds, beside every crafted item a capital shop
sells or buys, a `@mid` line at the grade's price. It is idempotent, and it
was run once (v1.7.0). Then v1.8.0 gave the four capital apothecaries a buy
line for the Marrow-Tempering Pill and nobody ran it again: a keeper bought the
pill at Low for 40 and refused a Mid one, because the shelf carried no `@mid`
line for it.

A generator that has to be remembered is a rule nobody holds. So the content is
held to the generator's own answer: running it on the shipped file must change
nothing, and the day a crafted item is half-shelved this names the lines.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest

from tests.support import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "scripts" / "author_mid_shelves.py"
CONTENT = PROJECT_ROOT / "content" / "world.json"


def _generator():
    spec = importlib.util.spec_from_file_location("author_mid_shelves", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines_added(module, world: dict) -> list[str]:
    before = copy.deepcopy(world)
    module.author(world)
    found = []
    for key, shop in world["shops"].items():
        old = before["shops"][key]
        for line in shop["sells"]:
            if line not in old["sells"]:
                found.append(f"{key} sells {line['item_id']}")
        for item in shop["buys"]:
            if item not in old["buys"]:
                found.append(f"{key} buys {item}")
    return found


class TheGeneratorHasNothingLeftToDo(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _generator()
        self.world = json.loads(CONTENT.read_text(encoding="utf-8"))

    def test_the_reader_finds_capitals_and_crafted_items(self):
        # A reader is asserted before it is trusted (rc.57): a generator that
        # saw no capital and no recipe would be a no-op on anything.
        self.assertGreaterEqual(len(self.module.crafted_items(self.world)), 30)
        capitals = [s for s in self.world["shops"].values() if self.world["locations"].get(s["city"], {}).get("realm_hub")]
        self.assertGreaterEqual(len(capitals), 4)
        self.assertGreaterEqual(
            sum(1 for s in capitals for item in s["buys"] if item.endswith("@mid")), 4,
            "no capital carries a Mid line at all; the gate is broken, not the tree",
        )

    def test_running_it_on_the_shipped_content_changes_nothing(self):
        added = _lines_added(self.module, self.world)
        self.assertEqual(
            added, [],
            "a crafted item is half-shelved at a capital - run scripts/author_mid_shelves.py: " + ", ".join(added),
        )

    def test_the_gate_names_the_line_that_went_missing(self):
        """The drill, kept inside the gate: the four apothecaries' marrow buy
        lines are taken out and the check must name them."""
        stale = copy.deepcopy(self.world)
        removed = []
        for key, shop in stale["shops"].items():
            if "marrow_tempering_pill@mid" in shop["buys"]:
                del shop["buys"]["marrow_tempering_pill@mid"]
                removed.append(key)
        self.assertEqual(len(removed), 4, "the four capital apothecaries no longer buy a Mid marrow pill")
        found = _lines_added(self.module, stale)
        self.assertEqual(sorted(found), sorted(f"{key} buys marrow_tempering_pill@mid" for key in removed))


if __name__ == "__main__":
    unittest.main()
