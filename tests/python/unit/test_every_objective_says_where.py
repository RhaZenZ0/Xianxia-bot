"""Every quest objective says where it is done (v1.27.0).

Labels written for the beginner path and the realm road name their door
(`**/world → City → Envoys**`), and a panel turns the door into a button.
Every commission label - 375 of the content file's 524 - and every label the
Forge builds from `OBJECTIVE_TYPES` named none. `OBJECTIVE_PATHS` is the one
place each type's door is written, and `labelled_objective` adds it to a label
that carries no path of its own.
"""
from __future__ import annotations

import importlib
import os
import re
import unittest
from unittest.mock import patch

import pytest

from app.rules.quests import OBJECTIVE_PATHS, OBJECTIVE_TYPES, labelled_objective, next_objective_label

pytestmark = pytest.mark.unit

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _hubs():
    with patch.dict(os.environ, ENV):
        importlib.import_module("app.bot.surface")
        return importlib.import_module("app.bot.hubs")


class EveryObjectiveSaysWhere(unittest.TestCase):
    def test_every_type_has_a_door(self) -> None:
        self.assertEqual(sorted(set(OBJECTIVE_TYPES) - set(OBJECTIVE_PATHS)), [])
        self.assertEqual(sorted(set(OBJECTIVE_PATHS) - set(OBJECTIVE_TYPES)), [])

    def test_every_door_is_a_button(self) -> None:
        hubs = _hubs()
        for kind, path in OBJECTIVE_PATHS.items():
            with self.subTest(kind=kind):
                actions = hubs.suggested_actions(path)
                self.assertEqual(len(actions), 1, f"{kind}: {path} resolves to no action")

    def test_a_label_without_a_path_is_given_one(self) -> None:
        label = labelled_objective({"id": "a", "type": "talk", "label": "Speak with Bo Tan"})
        self.assertEqual(label, "Speak with Bo Tan - **/npc → People → Talk**")
        self.assertIn("**/npc → People → Talk**", next_objective_label([{"id": "a", "type": "talk", "label": "Speak with Bo Tan"}], {}))

    def test_a_label_that_names_its_door_is_left_alone(self) -> None:
        written = "Learn where a sect takes applicants - **/world → City → Envoys**"
        self.assertEqual(labelled_objective({"id": "a", "type": "sect_discovery", "label": written}), written)

    def test_the_readers_ask_the_one_helper(self) -> None:
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        for path in ("app/bot/ui/commissions.py", "app/bot/commands/character.py"):
            text = (root / path).read_text(encoding="utf-8")
            self.assertTrue(re.search(r"labelled_objective\(", text), path)
