"""The quest journal is its owner's, as every hub is (v1.35.0).

`QuestDashboardView` had no `interaction_check`: its accept picker and abandon
button refused another member on their own, and the quest doors
(`path_buttons`) refused nobody, so another member could press a public
journal's door and turn it into their own hub. The view refuses the press now,
in the sentence its own items used.
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


class _Response:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, content: str = "", **_kwargs) -> None:
        self.sent.append(content)


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Interaction:
    def __init__(self, user_id: int) -> None:
        self.user = _User(user_id)
        self.response = _Response()


def _view(owner: int):
    with patch.dict(os.environ, ENV):
        from app.bot.commands.character import QuestDashboardView
    return QuestDashboardView(owner, [], None, "")


class TheJournalRefusesAnotherCultivator(unittest.TestCase):
    def test_another_members_press_is_refused_and_told(self):
        view = _view(7)
        stranger = _Interaction(999)
        self.assertFalse(asyncio.run(view.interaction_check(stranger)))
        self.assertEqual(stranger.response.sent, ["This quest panel belongs to another cultivator."])

    def test_the_owner_is_let_through(self):
        view = _view(7)
        owner = _Interaction(7)
        self.assertTrue(asyncio.run(view.interaction_check(owner)))
        self.assertEqual(owner.response.sent, [])


if __name__ == "__main__":
    unittest.main()
