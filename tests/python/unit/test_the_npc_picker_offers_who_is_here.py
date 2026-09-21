"""The NPC picker offers who is standing here (v1.0.8).

**Found by playing.** A cultivator stood at Cloudblade City East Gate, whose
scene card named the person in the room - *"Here the East Gate of Cloudblade
City, facing Ironbanner City · Gate Captain Yue Dong"* - and both `/talk` and
`/npcinfo` answered *"nothing to choose from right now."*

`local_npc_autocomplete` asked `DB.search_catalog("npc", current, 25)` and
filtered the answer by location. That query is::

    SELECT name FROM content_npcs WHERE name LIKE ? COLLATE NOCASE
    ORDER BY name LIMIT 25

and an empty box makes the needle `%%`, so it returns **the alphabetically
first twenty-five of all 574 catalogue NPCs** and the location filter then runs
on those. The picker could therefore only ever offer somebody who sorts near
the front of the world *and* is in the room; for most rooms that is nobody, and
a gate captain called **Y**ue Dong could never appear anywhere.

v1.0.0-rc.28 wrote `npcs_present(location, period)` as the one resolver for
this question and stated the rule the split broke: *"a picker that offers
somebody /talk then refuses them is worse than either being wrong alone."* The
inverse is what shipped - the card named people the picker would not offer -
because the card asks that resolver and this did not.

It is also v1.0.5's craft-picker finding one command over: that release pointed
`recipe_autocomplete` at what the player has learned and did not look for
siblings. This is the sibling.

**The two admin pickers are deliberately left alone.** `inspect_sim.py`'s two
call `search_catalog` because a GM inspecting the simulation is searching the
whole world by name, not asking who is in the room - which is the distinction
that stops this being a blind sweep over every `search_catalog` caller.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
LOCATIONS = APP / "bot" / "locations.py"


def _function(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """By AST, because a multi-line `def` defeats an indentation slice (rc.59)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


def _called(fn: ast.AST) -> set[str]:
    """Every function this one calls, by name - `DB.x(...)` counted as `x`."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            out.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            out.add(node.func.attr)
    return out


class ThePickerOffersWhoIsHere(unittest.TestCase):
    def setUp(self) -> None:
        self.source = LOCATIONS.read_text(encoding="utf-8")
        self.picker = _function(self.source, "local_npc_autocomplete")
        # A reader is asserted before it is trusted (rc.57): a walk that found
        # no calls at all would make every assertion below vacuous.
        self.calls = _called(self.picker)
        self.assertIn(
            "get_character", self.calls,
            "the AST walk found no known call in local_npc_autocomplete; the gate is broken, not the tree",
        )

    def test_the_picker_asks_who_is_standing_here(self):
        self.assertIn(
            "npcs_present", self.calls,
            "local_npc_autocomplete no longer asks npcs_present. Without it the picker cannot know "
            "who is in the room, and /talk and /npcinfo answer 'nothing to choose from right now' "
            "while the scene card one line above names the person standing there.",
        )

    def test_the_picker_does_not_search_the_whole_world_and_filter_after(self):
        """The shape of the fault, not its spelling.

        `search_catalog("npc", …)` is `ORDER BY name LIMIT 25` over every NPC
        in the game, so filtering its answer by location asks "is anyone in the
        alphabetical first twenty-five also here", which is a different and
        much smaller question than "who is here".
        """
        for node in ast.walk(self.picker):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "search_catalog":
                continue
            kind = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else "?"
            self.fail(
                f"local_npc_autocomplete calls search_catalog({kind!r}) again. That is a name search "
                "over the whole catalogue capped at 25 before anything looks at where the player is, "
                "so a name late in the alphabet can never be offered. Ask npcs_present instead.",
            )

    def test_the_surfaces_that_agree_with_the_card_still_do(self):
        """rc.28's rule is that the picker and the card cannot disagree, so the
        card's own resolver has to still be the one these use. If `npcs_present`
        were renamed or retired under them, the rule above would be guarding
        nothing while continuing to pass."""
        self.assertIn(
            "async def npcs_present(", self.source,
            "npcs_present is gone from locations.py; this gate is guarding a resolver that moved",
        )
        for module, function in (
            ("commands/scene.py", "scene_status"),
            ("commands/sense.py", "sense"),
        ):
            source = (APP / "bot" / module).read_text(encoding="utf-8")
            self.assertIn(
                "npcs_present(", source,
                f"{module} stopped asking npcs_present, so the card and the picker can disagree again",
            )

    def test_a_gm_may_still_search_the_whole_world_by_name(self):
        """The distinction that keeps this from being a blind sweep.

        A GM inspecting the simulation is looking somebody up by name, not
        asking who is in the room, so those two pickers are *correct* to search
        the catalogue. A fix that converted every `search_catalog("npc", …)`
        caller would have broken them.
        """
        source = (APP / "bot" / "admin" / "inspect_sim.py").read_text(encoding="utf-8")
        self.assertIn(
            'search_catalog("npc"', source,
            "the admin simulation pickers stopped searching the catalogue by name; a GM looking an "
            "NPC up is not asking who is standing next to them",
        )


if __name__ == "__main__":
    unittest.main()
