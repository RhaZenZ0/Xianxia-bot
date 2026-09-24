"""Doors the panel leaves off (v1.0.0-rc.32).

A hub page is a static list, and until now every leaf on it was drawn for
every player: `/family → Enter` from the far side of the world, `/law →
Comprehend` at Qi Condensation, a sect's treasury to somebody in no sect. The
panel now asks one async provider (`register_hidden_actions` in hubs.py) for
the paths to leave off for this player, whenever it refreshes its status.

Two rules are held here. Every name a provider can hide is a real leaf - a
row on the playtest checklist, which `test_playtest_gate` holds to the live
surface - so a renamed command cannot leave a stale hide behind. And no gate
hides a status read or the door into its own system: a road nobody can see is
a road nobody learns exists, so only what the engine would refuse outright is
hidden.
"""

from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

SURFACE = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
# Named after the stamped release, never a literal: `docs/playtest/v<version>.md`
# is renamed by every version bump, and a hardcoded `v1.0.0.md` here made the
# whole file error with FileNotFoundError the first time one happened (v1.0.1).
# That is the same fault `merge_ticks` had one level up - something that only
# holds while the filename does.
VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
CHECKLIST = (PROJECT_ROOT / "docs" / "playtest" / f"v{VERSION}.md").read_text(encoding="utf-8")
ROWS = {m.group(1) for m in re.finditer(r"^\| `(/[^`]+)`", CHECKLIST, re.M)}


def _literal(name: str):
    tree = ast.parse(SURFACE)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a literal assignment in surface.py")


def hidden_names() -> set[str]:
    names = {_literal("HOUSEHOLD_DOOR"), *_literal("HOUSEHOLD_INDOOR_ACTIONS")}
    for leaves in _literal("PROGRESSION_GATES").values():
        names |= set(leaves)
    # Where you stand (v1.1.0): the third provider's table, held to the same
    # two rules - a real leaf, and never a read.
    for leaves in _literal("LOCATION_GATES").values():
        names |= set(leaves)
    return names


class EveryHiddenNameIsARealDoor(unittest.TestCase):
    def test_every_hidden_leaf_is_a_row_on_the_checklist(self):
        for name in sorted(hidden_names()):
            with self.subTest(action=name):
                self.assertIn(f"/{name}", ROWS, f"/{name} is hidden by the panel but is not a command the hubs reach")

    def test_no_gate_hides_a_status_read_or_the_door_into_its_system(self):
        for name in sorted(hidden_names()):
            with self.subTest(action=name):
                leaf = name.split()[-1]
                self.assertNotIn(leaf, ("status", "view", "info", "history", "encounters", "visit", "respond"),
                                 f"/{name} is a read or an entry, and a road nobody can see is a road nobody learns exists")

    def test_both_providers_are_asked_and_registered_once(self):
        self.assertIn("register_hidden_actions(_hidden_actions)", SURFACE)
        self.assertEqual(SURFACE.count("register_hidden_actions("), 1)
        for provider in ("_household_hidden_actions", "_progression_hidden_actions", "_location_hidden_actions"):
            self.assertIn(f"async def {provider}(", SURFACE)
            self.assertIn(provider, SURFACE.split("async def _hidden_actions(")[1])

    def test_the_gates_read_the_same_state_the_engine_reads(self):
        for reader in ("DB.get_sect_membership(uid)", "DB.get_abode(uid)", "DB.get_personal_world(uid)",
                       "DB.get_spirit_beasts(uid)", "DB.get_player_family_membership(uid)", "DB.get_soul_legacy(uid)"):
            self.assertIn(reader, SURFACE)
        self.assertIn('normal_min_realm_index', SURFACE)
        self.assertIn("realm not in ASCENSION_GATES", SURFACE)
