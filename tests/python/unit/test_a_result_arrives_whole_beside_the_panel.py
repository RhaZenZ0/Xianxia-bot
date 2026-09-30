"""A result pressed from a hub panel arrives whole (v1.12.3).

Two faults, each found by driving the delivery path with a fake source and
reading the keywords that reached Discord rather than the source text:

* ``_layout_result_send`` sent a card's own view with ``view=`` and ``wait=``
  and nothing else, so the First Sight image - a ``discord.File`` the card
  points at as ``attachment://...`` - was lost whenever the discovery was
  pressed from a panel.
* ``_HubResponseProxy.edit_message`` handed the edit's kwargs, ``content``
  included, to ``_fallback_followup`` beside the same text as an argument: a
  TypeError for a classic result, and a card sent next to text, which Discord
  refuses with a 400.
"""
import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "123456789012345678",
    "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
    "DATABASE_PATH": "data/test.sqlite3",
}


def _hubs():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.hubs")


def _source(*, panel_id=11, response_done=True, edit_raises=None):
    calls = {"followup": [], "edit_original": [], "response_edit": []}

    async def followup_send(content=None, **kwargs):
        calls["followup"].append((content, kwargs))
        return "followup"

    async def edit_original(**kwargs):
        calls["edit_original"].append(kwargs)
        if edit_raises is not None:
            raise edit_raises
        return "edited"

    async def response_edit(**kwargs):
        calls["response_edit"].append(kwargs)
        if edit_raises is not None:
            raise edit_raises
        return "edited"

    async def defer(**kwargs):
        return None

    source = SimpleNamespace(
        message=SimpleNamespace(id=panel_id),
        response=SimpleNamespace(is_done=lambda: response_done, edit_message=response_edit, defer=defer),
        edit_original_response=edit_original,
        followup=SimpleNamespace(send=followup_send),
    )
    return source, calls


async def _refresh(_interaction):
    return None


def _panel():
    return SimpleNamespace(
        message=SimpleNamespace(id=11),
        last_result="",
        refresh_status=_refresh,
        rebuild=lambda: None,
        is_layout_hub=True,
    )


class ACardKeepsItsFileBesideThePanel(unittest.TestCase):
    def test_every_keyword_a_v2_message_can_carry_reaches_the_followup(self):
        hubs = _hubs()
        from app.bot.cards import Card, card_view

        source, calls = _source()
        card = card_view(Card(title="First Sight"))
        attachment, mentions = object(), object()
        asyncio.run(
            hubs._layout_result_send(
                source,
                "You arrive.",
                {"view": card, "file": attachment, "allowed_mentions": mentions, "wait": True, "embed": object()},
                _panel(),
            )
        )
        self.assertEqual(len(calls["followup"]), 1)
        content, kwargs = calls["followup"][0]
        self.assertIsNone(content)
        self.assertIs(kwargs.get("file"), attachment, "the card's attached image was dropped: %s" % sorted(kwargs))
        self.assertIs(kwargs.get("allowed_mentions"), mentions)
        self.assertIs(kwargs.get("view"), card)
        self.assertTrue(kwargs.get("wait"))
        self.assertNotIn("embed", kwargs, "a V2 message may not carry an embed")
        self.assertNotIn("content", kwargs)

    def test_a_list_of_files_is_carried_too(self):
        hubs = _hubs()
        from app.bot.cards import Card, card_view

        source, calls = _source()
        files = [object(), object()]
        asyncio.run(hubs._layout_result_send(source, None, {"view": card_view(Card(title="x")), "files": files}, _panel()))
        self.assertEqual(calls["followup"][0][1].get("files"), files)


class AFailedEditNeverPassesTheTextTwice(unittest.TestCase):
    def _proxy_edit(self, hubs, source, **kwargs):
        owner = SimpleNamespace(source=source, hub_view=SimpleNamespace(message=source.message), output_written=False)
        proxy = hubs._HubResponseProxy(owner)
        return asyncio.run(proxy.edit_message(**kwargs))

    def _gone(self):
        hubs = _hubs()
        import discord

        return hubs, discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "unknown message")

    def test_a_classic_result_falls_back_without_a_second_content(self):
        hubs, gone = self._gone()
        source, calls = _source(response_done=False, edit_raises=gone)
        # raises TypeError ("multiple values for argument 'content'") on the old code
        self._proxy_edit(hubs, source, content="You bought a pill.")
        self.assertEqual(len(calls["followup"]), 1)
        content, kwargs = calls["followup"][0]
        self.assertEqual(content, "You bought a pill.")
        self.assertNotIn("content", kwargs)

    def test_a_card_falls_back_beside_no_text_at_all(self):
        hubs, gone = self._gone()
        from app.bot.cards import Card, card_view

        source, calls = _source(response_done=False, edit_raises=gone)
        card = card_view(Card(title="Sheet"))
        self._proxy_edit(hubs, source, content="Your sheet:", view=card)
        self.assertEqual(len(calls["followup"]), 1)
        content, kwargs = calls["followup"][0]
        self.assertIsNone(content, "a card sent beside text is refused by Discord")
        self.assertNotIn("content", kwargs)
        self.assertIs(kwargs.get("view"), card)
        self.assertIn("Your sheet:", card.card.lead, "the text was lost instead of folded into the card")

    def test_an_embed_only_result_still_says_the_panel_is_gone(self):
        hubs, gone = self._gone()
        source, calls = _source(response_done=False, edit_raises=gone)
        self._proxy_edit(hubs, source, embed=object())
        content, _kwargs = calls["followup"][0]
        self.assertIn("no longer available", content)


if __name__ == "__main__":
    unittest.main()
