"""A tutorial stage the engine catches a graduate up on is told to them
(v1.2.1).

v1.2.0's catch-up handed added beginner-path stages over at the end of every
ordinary quest report and wrote them into the result as `caught_up`, and no
Python read the key - so the stage arrived in the journal and nobody was told.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

with patch.dict(os.environ, ENV):
    announce_quest_progress = importlib.import_module("app.bot.character_state").announce_quest_progress


class Response:
    def __init__(self):
        self.sent = []

    def is_done(self):
        return False

    async def send_message(self, text, **_):
        self.sent.append(text)


class ACaughtUpStageIsTold(unittest.TestCase):
    def test_the_stage_is_announced_as_a_new_quest(self):
        response = Response()
        interaction = SimpleNamespace(response=response, followup=SimpleNamespace())
        asyncio.run(announce_quest_progress(interaction, [{
            "quest_key": "beginner_iron", "title": "Iron from the Seam", "caught_up": True,
            "objectives": [{"type": "gather", "target": "spirit_iron", "count": 3, "label": "Mine spirit iron"}],
        }]))
        self.assertEqual(len(response.sent), 1)
        self.assertIn("New quest: Iron from the Seam", response.sent[0])
        self.assertNotIn("Quest progress", response.sent[0], "a handed-over stage was told as progress on it")


if __name__ == "__main__":
    unittest.main()
