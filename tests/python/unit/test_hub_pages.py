"""Every hub page holds what it claims, and no more than a page can show.

v1.0.0-rc.13 regrouped the hub surface: thirty-seven of eighty-five pages held
a single action while seven others held more than the layout can render, so a
page was as likely to waste a tap as to hide seventeen actions behind a Next
button. Pages are now named after what a player is doing, and the large groups
(`/sect` at twenty-five, `/admin player` at fifteen) are split with `only`.

`only` is the part that can go quietly wrong: a page that names the leaves it
takes will silently drop any leaf nobody names, and silently show a leaf twice
if two pages name it. These tests are the thing that makes that loud.
"""
from __future__ import annotations

import importlib
import os
import unittest
from collections import Counter
from unittest.mock import patch

from discord import app_commands

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        return (importlib.import_module("app.bot.surface"),
                importlib.import_module("app.bot.hubs"))


def _definitions(surface, hubs):
    seen = {d.name: d for d in hubs.REGISTERED_HUBS}
    seen.setdefault("admin", surface._ADMIN_HUB_DEFINITION)
    return list(seen.values())


def _reachable(page) -> set[str]:
    """Every leaf under a page's commands, ignoring what `only` selects."""
    out: set[str] = set()
    for command in (page.command, *(tuple(page.extras or ()))):
        if isinstance(command, app_commands.Group):
            out |= {item.qualified_name for item in command.walk_commands()
                    if isinstance(item, app_commands.Command)}
        elif command is not None:
            out.add(command.qualified_name)
    return out


class EveryLeafLandsOnExactlyOnePage(unittest.TestCase):
    def test_no_action_is_dropped_by_a_page_that_names_what_it_takes(self):
        surface, hubs = _modules()
        for definition in _definitions(surface, hubs):
            reachable: set[str] = set()
            for page in definition.pages:
                reachable |= _reachable(page)
            shown = {action.command.qualified_name
                     for page in definition.pages for action in hubs._leaf_actions(page)}
            self.assertEqual(reachable - shown, set(),
                             f"/{definition.name} can reach these leaves but no page shows them")

    def test_no_action_is_shown_on_two_pages_of_one_hub(self):
        surface, hubs = _modules()
        for definition in _definitions(surface, hubs):
            counted = Counter(action.command.qualified_name
                              for page in definition.pages for action in hubs._leaf_actions(page))
            self.assertEqual([name for name, n in counted.items() if n > 1], [],
                             f"/{definition.name} shows an action twice")

    def test_page_keys_are_unique_within_a_hub(self):
        # The select's value and every hint path address a page by its key, so
        # two pages sharing one is not a cosmetic problem: the second is
        # unreachable.
        surface, hubs = _modules()
        for definition in _definitions(surface, hubs):
            keys = [page.key for page in definition.pages]
            self.assertEqual(sorted(keys), sorted(set(keys)), f"/{definition.name} repeats a page key")


class NoPageOverflowsTheLayout(unittest.TestCase):
    def test_every_page_fits_the_rows_a_panel_can_render(self):
        surface, hubs = _modules()
        for definition in _definitions(surface, hubs):
            for page in definition.pages:
                self.assertLessEqual(
                    len(hubs._leaf_actions(page)), hubs._LAYOUT_ACTION_LIMIT,
                    f"/{definition.name} -> {page.label} needs a Next button to show all its actions",
                )

    def test_a_page_is_not_a_single_action_wearing_a_page_costume(self):
        # Eight pages still hold one action, and each is a thing on its own:
        # a deliberate floor, not an accident. If this number climbs, pages are
        # being minted for commands again.
        surface, hubs = _modules()
        singles = [f"/{d.name} -> {p.label}"
                   for d in _definitions(surface, hubs) for p in d.pages
                   if len(hubs._leaf_actions(p)) == 1]
        self.assertLessEqual(len(singles), 8, singles)


class TheMergesThatRemovedDuplication(unittest.TestCase):
    def test_perfection_is_one_page_taking_a_path(self):
        surface, hubs = _modules()
        pages = {p.label: p for p in surface._HUB_BY_NAME["ascend"].pages}
        self.assertIn("Perfection", pages)
        self.assertNotIn("Body Perfection", pages)
        labels = {a.label for a in hubs._leaf_actions(pages["Perfection"])}
        self.assertEqual(labels, {"Start", "Info", "Quest", "Clues", "Trial", "Abandon"})

    def test_the_three_affliction_reads_are_one_page(self):
        surface, hubs = _modules()
        pages = {p.label: p for p in surface._HUB_BY_NAME["character"].pages}
        self.assertIn("Afflictions", pages)
        paths = {a.path for a in hubs._leaf_actions(pages["Afflictions"])}
        for expected in ("/effects", "/specialeffects", "/condition status", "/condition treat"):
            self.assertIn(expected, paths)

    def test_crafting_has_one_door(self):
        surface, hubs = _modules()
        paths = {a.path for d in _definitions(surface, hubs) for p in d.pages
                 for a in hubs._leaf_actions(p)}
        self.assertIn("/craft", paths)
        self.assertNotIn("/alchemy refine", paths)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
