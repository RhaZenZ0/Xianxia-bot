"""A cultivation path plays differently (v1.13.0).

The engine owns each path's ability (``go_core/internal/game/path_traits.go``,
held by ``path_traits_test.go``). What is held here is the presentation's half:
the sheet's line is filled from the numbers the engine reads, every surface
offers a path's door only to that path (rc.46), and a door another path owns is
left off the panel altogether - *"Hide command/buttons you can't use if you
play the wrong type of cultivator."*
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

from app.rules.path_traits import (
    QI_REFINER, REFINED_STANCE_KEY, SWORD_CULTIVATOR, stances_for, sword_intent_cap, trait_line, trait_summary,
)
from tests.support import PROJECT_ROOT

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
PATHS = CONTENT["paths"]
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
GO_TRAITS = (GO / "path_traits.go").read_text(encoding="utf-8")
GO_DEATH_QI = (GO / "death_qi.go").read_text(encoding="utf-8")
BOT = PROJECT_ROOT / "app" / "bot"
ATTRIBUTES = ("body", "agility", "spirit", "insight", "will", "presence")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _go_const(name: str) -> str:
    match = re.search(rf'\b{name}\s*=\s*"([^"]+)"', GO_TRAITS)
    if not match:
        raise AssertionError(f"{name} is not a string constant in path_traits.go; the reader is broken, not the tree")
    return match.group(1)


class EveryPathSaysWhatItDoes(unittest.TestCase):
    def test_every_path_has_a_trait_whose_summary_fills(self):
        self.assertEqual(len(PATHS), 7, "the content carries a different number of paths; the reader is broken")
        for name in PATHS:
            with self.subTest(path=name):
                line = trait_line(PATHS, name)
                self.assertTrue(line.startswith("**"), f"{name} has no trait name")
                summary = trait_summary(PATHS, name)
                self.assertTrue(summary, f"{name} has no trait summary")
                self.assertNotIn("?", summary, f"{name}'s summary names a number its trait does not carry: {summary}")
                self.assertNotIn("{", summary, summary)

    def test_the_summary_is_filled_from_the_engines_numbers(self):
        trait = PATHS[SWORD_CULTIVATOR]["trait"]
        summary = trait_summary(PATHS, SWORD_CULTIVATOR)
        self.assertIn(str(trait["intent_cap"]), summary)
        self.assertIn(f"+{trait['intent_strike_bonus']}", summary)

    def test_the_names_are_the_engines(self):
        self.assertEqual(_go_const("pathSword"), SWORD_CULTIVATOR)
        self.assertEqual(_go_const("pathQi"), QI_REFINER)
        self.assertEqual(_go_const("qiRefinerStanceKey"), REFINED_STANCE_KEY)

    def test_every_creation_focus_names_what_the_path_grows(self):
        """The creation screen's Focus names two attributes, and since v1.13.0
        a path grows every attribute tied for its highest - so the two must be
        that tie, or the screen promises what the realm crossing does not do."""
        from app.rules.creation_ui import cultivation_style_profile
        for name, definition in PATHS.items():
            with self.subTest(path=name):
                top = max(int(definition[a]) for a in ATTRIBUTES)
                grows = {a for a in ATTRIBUTES if int(definition[a]) == top}
                focus = cultivation_style_profile(name)["focus"]
                named = {a for a in ATTRIBUTES if a.title() in focus}
                self.assertEqual(named, grows, f"{name}'s Focus reads {focus!r} and the path grows {sorted(grows)}")


class EveryLawHasAPath(unittest.TestCase):
    def test_every_affinity_names_a_real_path_and_every_path_has_one(self):
        laws = CONTENT["law_system"]["laws"]
        reached = set()
        for law_id, law in laws.items():
            for path in law.get("affinity_paths") or ():
                self.assertIn(path, PATHS, f"{law_id} has an affinity for {path!r}, which is no path")
                reached.add(path)
        self.assertEqual(reached, set(PATHS), f"a path with no Law affinity: {sorted(set(PATHS) - reached)}")


class APathsDoorIsOfferedOnlyToThatPath(unittest.TestCase):
    def test_the_refined_stance_is_offered_only_to_a_qi_refiner(self):
        for name in PATHS:
            keys = [key for key, _label in stances_for(PATHS, name)]
            with self.subTest(path=name):
                self.assertEqual(REFINED_STANCE_KEY in keys, name == QI_REFINER, keys)
                self.assertEqual(keys[:3], ["circulate", "refine", "force"])

    def test_a_stance_multiplier_on_another_path_does_not_open_the_stance(self):
        """Only the Qi Refiner's trait carries a stance multiplier today, so the
        path check is belt-and-braces against the shipped content (rc.53's
        `!ok`); this plants one on another path to hold it."""
        planted = {**PATHS, SWORD_CULTIVATOR: {**PATHS[SWORD_CULTIVATOR], "trait": {**PATHS[SWORD_CULTIVATOR]["trait"], "stance_gain_mult": 1.5}}}
        self.assertNotIn(REFINED_STANCE_KEY, [key for key, _label in stances_for(planted, SWORD_CULTIVATOR)])

    def test_sword_intent_belongs_to_the_sword_cultivator(self):
        for name in PATHS:
            with self.subTest(path=name):
                self.assertEqual(sword_intent_cap(PATHS, name) > 0, name == SWORD_CULTIVATOR)

    def test_the_intent_strike_button_is_drawn_only_with_intent(self):
        with patch.dict(os.environ, ENV):
            battle = importlib.import_module("app.bot.commands.battle")
        sword = {"path": SWORD_CULTIVATOR, "path_resource": 2}
        self.assertEqual(battle.battle_intent(sword), 2)
        self.assertEqual(battle.battle_intent({**sword, "path_resource": 0}), 0)
        self.assertEqual(battle.battle_intent({"path": QI_REFINER, "path_resource": 3}), 0,
                         "a Qi Refiner holding a stray path_resource must not be offered an Intent Strike")

        def labels(view):
            return [getattr(item, "label", "") for item in view.controls]
        drawn = battle.BattleView(1, 1, [], [], intent=2)
        self.assertIn("Intent Strike (2)", labels(drawn))
        self.assertFalse(any("Intent" in label for label in labels(battle.BattleView(1, 1, [], [], intent=0))))

    def test_the_engine_reports_what_the_bot_prints(self):
        law = (GO / "law_actions.go").read_text(encoding="utf-8")
        realm = (GO / "secret_realm_actions.go").read_text(encoding="utf-8")
        train = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
        self.assertIn('"affinity_bonus": affinity', law)
        self.assertIn('"path_bonus": pathBonus', realm)
        self.assertIn('"manual_own_path": manualOwnPath', train)
        self.assertIn("affinity_bonus", (BOT / "commands" / "law.py").read_text(encoding="utf-8"))
        self.assertIn("path_bonus", (BOT / "commands" / "secretrealm.py").read_text(encoding="utf-8"))
        self.assertIn("manual_own_path", (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8"))
        self.assertIn("trait_line(WORLD.paths", (BOT / "commands" / "character.py").read_text(encoding="utf-8"))


class AnotherPathsDoorIsLeftOff(unittest.TestCase):
    """The panel's path gate. Drills: drop `_path_hidden_actions` from
    `_hidden_actions` and a Sword Cultivator is shown the Ghost page; print the
    padlock for `NOT_YOUR_PATH` and the page reads a column of other paths'
    doors; let `visible_pages` keep every page and the Ghost page stays in the
    list."""

    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, ENV):
            cls.surface = importlib.import_module("app.bot.surface")
            cls.hubs = importlib.import_module("app.bot.hubs")

    def _ghost_page(self):
        for definition in self.hubs.REGISTERED_HUBS:
            for page in definition.pages:
                if {action.path for action in self.hubs._leaf_actions(page)} >= {"/ghost harvest", "/ghost appease"}:
                    return definition, page
        raise AssertionError("no hub page carries the ghost road; the reader is broken, not the tree")

    def test_the_ghost_gate_is_the_engines(self):
        self.assertEqual(self.surface.GHOST_PATH, CONTENT["death_qi_system"]["path"])
        self.assertIn("requireGhostCultivator(conn, catalog, userID)", GO_DEATH_QI.split("func ghostHarvestAction(")[1].split("\nfunc ")[0])
        self.assertIn("requireGhostCultivator(conn, catalog, userID)", GO_DEATH_QI.split("func ghostAppeaseAction(")[1].split("\nfunc ")[0])
        checklist = (PROJECT_ROOT / "docs" / "playtest" / f"v{(PROJECT_ROOT / 'VERSION').read_text().strip()}.md").read_text(encoding="utf-8")
        for leaves in self.surface.PATH_GATES.values():
            for leaf in leaves:
                self.assertIn(f"| `/{leaf}`", checklist, f"/{leaf} is hidden by a path but no hub reaches it")

    def test_another_path_is_not_shown_the_ghost_road(self):
        sword = asyncio.run(self.surface._path_hidden_actions(None, {"path": SWORD_CULTIVATOR}))
        ghost = asyncio.run(self.surface._path_hidden_actions(None, {"path": self.surface.GHOST_PATH}))
        self.assertEqual(sword.get("/ghost harvest"), self.hubs.NOT_YOUR_PATH)
        self.assertNotIn("/ghost harvest", ghost)
        self.assertIn("_path_hidden_actions", self.surface.__dict__)
        body = (BOT / "surface.py").read_text(encoding="utf-8").split("async def _hidden_actions(")[1].split("\nregister_hidden_actions")[0]
        self.assertIn("_path_hidden_actions", body)

    def test_the_page_leaves_the_list_and_prints_no_padlock(self):
        definition, page = self._ghost_page()
        hidden = asyncio.run(self.surface._path_hidden_actions(None, {"path": SWORD_CULTIVATOR}))
        shown = self.hubs.visible_pages(definition, hidden)
        self.assertNotIn(page.key, [p.key for p in shown], "a page of nothing but another path's doors is still in the list")
        self.assertIn(page.key, [p.key for p in self.hubs.visible_pages(definition, {})])
        view = SimpleNamespace(hidden_paths=hidden, unlock_line=lambda _page: "")
        text = self.hubs.CommandHubView.locked_lines(view, page)
        self.assertNotIn("🔒", text, f"another path's doors printed padlocks:\n{text}")


if __name__ == "__main__":
    unittest.main()
