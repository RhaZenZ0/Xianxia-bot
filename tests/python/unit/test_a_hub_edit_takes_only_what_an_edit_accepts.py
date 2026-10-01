"""A hub turns a send into an edit, and an edit takes fewer keywords (v1.14.1).

A leaf answered from a picker is answered by editing the picker's message: the
hub proxy turns the handler's `followup.send(...)` into
`edit_original_response(...)`. A send takes keywords an edit refuses, and
`_safe_edit_kwargs` stripped two of them. The raid card is sent with
`wait=True`, so `/boss start` pressed from the hub raised
`TypeError: edit_original_response() got an unexpected keyword argument 'wait'`
- after the engine had already started the raid. The first parallel playtest
found it, because one part's player happened to be standing at a lair when the
sweep pressed Start; a whole run never had been.

Held against discord.py's own signatures, so the next send-only keyword cannot
reach an edit either.
"""
from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def keywords(function) -> set[str]:
    return {name for name, p in inspect.signature(function).parameters.items()
            if name != "self" and p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)}


class AHubEditTakesOnlyWhatAnEditAccepts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, ENV):
            import discord

            from app.bot import hubs
        cls.discord = discord
        cls.hubs = hubs

    def test_every_send_keyword_is_either_kept_or_dropped(self):
        discord = self.discord
        sent = keywords(discord.Webhook.send) | keywords(discord.InteractionResponse.send_message)
        self.assertIn("wait", sent, "discord.py's send no longer names wait; the reader is broken, not the tree")
        accepted = keywords(discord.Interaction.edit_original_response)
        given = {name: None for name in sent}
        given["content"] = "a result"
        clean = self.hubs._safe_edit_kwargs(given, fallback_view=None)
        self.assertEqual(set(clean) - accepted, set(),
                         "an edit is handed keywords edit_original_response refuses")

    def test_the_raid_cards_send_survives_the_edit(self):
        discord = self.discord
        clean = self.hubs._safe_edit_kwargs(
            {"view": None, "ephemeral": False, "wait": True, "allowed_mentions": discord.AllowedMentions.none()},
            fallback_view=None)
        self.assertNotIn("wait", clean)
        self.assertIn("allowed_mentions", clean, "the card's mentions must still not ping")

    def test_a_file_becomes_an_attachment(self):
        marker = object()
        clean = self.hubs._safe_edit_kwargs({"content": "x", "file": marker}, fallback_view=None)
        self.assertEqual(clean.get("attachments"), [marker])
        self.assertNotIn("file", clean)


if __name__ == "__main__":
    unittest.main()
