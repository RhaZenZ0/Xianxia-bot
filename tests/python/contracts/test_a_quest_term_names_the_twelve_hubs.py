"""Migration 83 (v1.38.0): the hubs a quest names are the twelve.

Sixteen hubs became twelve and the seven one-action pages folded into their
neighbours, so a printed path a quest objective carries - `**/ascend →
Perfection → Start**`, `**/realm → Secret Realms → Enter**` - names a hub that
no longer exists. The content file says the new paths for new worlds; the
quest rows a running world holds are insert-only (migration 61's rule), so
migration 83 rewrites them, in the definitions and in the terms a player
already holding the quest had pinned.

The claim held here is not the spelling: it is that every path a rewritten
row prints **resolves to a leaf** through the live hubs, the way the hint
resolver reads it - the same question `test_hint_paths.py` asks of the code
and the content. A text that names no path is left exactly as it was.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

# One objective label per rename the fold made, in the shape the content and
# the reporters print them (`app/rules/quests.py` OBJECTIVE_PATHS).
OLD_LABELS = (
    "Begin a Perfect Path at the ninth stage - **/ascend → Perfection → Start**",
    "Survive the tribulation - **/ascend → Tribulation / Ascension → Attempt**",
    "Fill Stage 9 and cross - **/ascend → Main Progression → Breakthrough**",
    "Bring down the boss - **/combat → Boss Raids → Claim**",
    "Enter the realm - **/realm → Secret Realms → Enter**",
    "Speak with Bo Tan - **/npc → People → Talk**",
    "Stabilize a personal world - **/innerworld → Personal World → Create**",
    "Raise a room - **/abode → Property → Upgrade**",
    "Make something in your trade - **/craft → General Crafting → Craft**",
)
# A rename of taste, not of reach: `/economy → Wallet` resolved before the
# fold (the step names an action), so it proves nothing about the rewrite
# and is held apart - rewritten, and still resolving.
WALLET = "Count your stones - **/economy → Wallet**"
UNTOUCHED = "Look around outside - **/world → Act → Explore**"
PATHLESS = "Cultivate once"


def _core():
    from tests.support import install_aiosqlite_shim

    install_aiosqlite_shim()
    from app.database import core

    return core


def _hubs():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.hubs")


class MigrationEightyThreeRenamesTheHubsAQuestNames(unittest.IsolatedAsyncioTestCase):
    def _terms(self, labels: list[str]) -> str:
        # The engine pins terms with Go's json.Marshal, which writes the arrow
        # raw; the definitions below are seeded the Python way (json.dumps),
        # which escapes it to \u2192. One migration has to rewrite both.
        return json.dumps({"objectives": [{"id": f"o{i}", "type": "cultivate", "count": 1, "label": label}
                                          for i, label in enumerate(labels)],
                           "rewards": {"insight_xp": 10}, "pinned_at_game_minute": 5}, ensure_ascii=False)

    async def _world_at_82(self) -> Path:
        core = _core()
        from app.database import Database

        path = Path(tempfile.mkdtemp()) / "hubs.sqlite3"
        migrations = tuple(m for m in core.SCHEMA_MIGRATIONS if int(m[0]) <= 82)
        with patch.object(core, "SCHEMA_VERSION", 82), patch.object(core, "SCHEMA_MIGRATIONS", migrations):
            await Database(path).init()
        labels = [*OLD_LABELS, WALLET, UNTOUCHED, PATHLESS]
        objectives = json.dumps([{"id": f"o{i}", "type": "cultivate", "count": 1, "label": label} for i, label in enumerate(labels)])
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,"
                "rewards_json,status,origin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,0,0)",
                ("old_roads", "Old roads", "Open the way with **/ascend → Perfection → Start**.", "system", "realm_road",
                 objectives, "{}", "approved", "content"))
            conn.execute(
                "INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,created_at,updated_at,terms_json)"
                " VALUES(?,?,?,'{}',0,0,0,?)", (1, "old_roads", "active", self._terms(labels)))
            conn.execute(
                "INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,created_at,updated_at,terms_json)"
                " VALUES(?,?,?,'{}',0,0,0,?)", (2, "old_roads", "active", ""))  # the definition is its terms
            conn.commit()
        await Database(path).init()
        return path

    def _rows(self, path: Path) -> tuple[list[str], str, list[str], str]:
        with sqlite3.connect(path) as conn:
            version = conn.execute("SELECT current_version FROM schema_version WHERE singleton=1").fetchone()[0]
            objectives, description = conn.execute(
                "SELECT objectives_json, description FROM quest_definitions WHERE quest_key='old_roads'").fetchone()
            self.assertIn("\\u2192", objectives, "the fixture lost the escaped arrow the Python seeding writes")
            terms = conn.execute("SELECT terms_json FROM character_quests WHERE quest_key='old_roads' AND user_id=1").fetchone()[0]
            empty = conn.execute("SELECT terms_json FROM character_quests WHERE quest_key='old_roads' AND user_id=2").fetchone()[0]
        self.assertGreaterEqual(version, 83, "the migration did not run; the reader is broken, not the tree")
        definition_labels = [o["label"] for o in json.loads(objectives)]
        term_labels = [o["label"] for o in json.loads(terms)["objectives"]]
        return definition_labels, description, term_labels, empty

    def _resolves(self, hubs, label: str) -> bool:
        match = re.search(r"\*\*/([a-z]+)((?:\s*→\s*[^*]+?)*)\*\*", label)
        self.assertIsNotNone(match, f"{label!r} prints no path; the fixture is broken")
        steps = [s for s in re.split(r"\s*→\s*", match.group(2) or "") if s.strip()]
        return hubs._hint_action(match.group(1), steps) is not None

    async def test_every_old_path_resolves_through_a_live_hub_after_the_migration(self):
        path = await self._world_at_82()
        hubs = _hubs()
        definition_labels, description, term_labels, _ = self._rows(path)
        self.assertEqual(definition_labels, term_labels, "the definition and the pinned terms were rewritten differently")
        for label in definition_labels[:len(OLD_LABELS) + 1]:
            with self.subTest(label=label):
                self.assertNotRegex(label, r"/(ascend|combat|realm|npc|innerworld|abode) → |General Crafting|/economy → Wallet\*\*",
                                    "an old hub is still named")
                self.assertTrue(self._resolves(hubs, label), f"{label!r} resolves to no leaf through the twelve hubs")
        self.assertIn("**/cultivation → Perfection → Start**", description)

    async def test_the_old_paths_did_not_resolve_which_is_why_the_migration_exists(self):
        hubs = _hubs()
        for label in OLD_LABELS:
            with self.subTest(label=label):
                self.assertFalse(self._resolves(hubs, label), "the old path still resolves, so this test proves nothing about the rewrite")

    async def test_a_text_that_names_no_folded_hub_is_left_as_it_was(self):
        path = await self._world_at_82()
        definition_labels, _, term_labels, empty = self._rows(path)
        self.assertEqual(definition_labels[-2:], [UNTOUCHED, PATHLESS])
        self.assertEqual(term_labels[-2:], [UNTOUCHED, PATHLESS])
        self.assertEqual(empty, "", "a quest whose terms are its definition stays that way")


if __name__ == "__main__":
    unittest.main()
