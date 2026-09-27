"""Every card is drawn in the Components V2 layout (v1.9.0).

The hubs and the Scene Action panel moved to Components V2 long ago, and every
card - the sheet, a battle, a raid, a lot, a stall, an event scene, a discovery
- stayed a classic embed, so one bot drew two looks side by side.
``app/bot/cards.py`` draws a card the way an embed was written, as a
``Container``, and lays a view's buttons out under it.

Two halves are held here. The behaviour of the card and its view, driven
directly (``cards.py`` imports only discord, so it is loaded from its file
without booting the bot). And a gate: no production module builds a
``discord.Embed`` or sends one, except the two classic fallbacks, which exist
only for a discord.py without Components V2 - the pinned one always has it.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import unittest
from pathlib import Path

import discord
import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
BOT = ROOT / "app" / "bot"

# (file, enclosing scope) of the only embeds left, and why each stays.
CLASSIC_FALLBACKS = {
    # The classic hub: drawn only when discord.py has no LayoutView, and every
    # registered hub is in LAYOUT_HUB_NAMES.
    ("hubs.py", "CommandHubView.build_embed"),
    ("hubs.py", "HubPageSelect.callback"),
    ("hubs.py", "HubPageButton.callback"),
    ("hubs.py", "HubRefreshButton.callback"),
    ("hubs.py", "_send_classic_hub"),
    # The classic Scene Action panel: scene_action_panel builds it only when
    # scene_layout.LAYOUT_COMPONENTS_AVAILABLE is false.
    ("commands/scene.py", "SceneActionView.embed"),
    ("commands/scene.py", "SceneActionTypeSelect.callback"),
    ("commands/scene.py", "SceneActionTargetSelect.callback"),
}


def _cards():
    spec = importlib.util.spec_from_file_location("cards_under_test", BOT / "cards.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _embed_uses() -> list[tuple[str, str, int]]:
    """Every ``discord.Embed`` and every ``embed=``/``embeds=`` that sends
    something, with the scope it sits in. ``embed=None`` clears an embed and is
    how an old message becomes a card, so it is not a use."""
    found = []
    for path in sorted(BOT.rglob("*.py")):
        name = str(path.relative_to(BOT))
        stack: list[str] = []

        class Walker(ast.NodeVisitor):
            def generic_visit(self, node):
                scope = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                if scope:
                    stack.append(node.name)
                if (isinstance(node, ast.Attribute) and node.attr == "Embed"
                        and isinstance(node.value, ast.Name) and node.value.id == "discord"):
                    found.append((name, ".".join(stack), node.lineno))
                if (isinstance(node, ast.keyword) and node.arg in ("embed", "embeds")
                        and not (isinstance(node.value, ast.Constant) and node.value.value is None)):
                    found.append((name, ".".join(stack), node.value.lineno))
                super().generic_visit(node)
                if scope:
                    stack.pop()

        Walker().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


class NoCardIsAnEmbed(unittest.TestCase):
    def test_the_reader_finds_the_fallbacks_it_allows(self):
        # A walk that silently found nothing would make the gate below vacuous.
        scopes = {(name, scope) for name, scope, _ in _embed_uses()}
        self.assertIn(("hubs.py", "CommandHubView.build_embed"), scopes, "the gate is broken, not the tree")

    def test_no_card_is_built_or_sent_as_an_embed(self):
        offenders = sorted(
            f"{name}:{line} ({scope or 'module'})"
            for name, scope, line in _embed_uses()
            if (name, scope) not in CLASSIC_FALLBACKS
        )
        self.assertEqual(offenders, [], "draw it with app/bot/cards.py - a Card sent as view=card_view(card)")

    def test_every_allowed_fallback_is_still_there(self):
        # An entry nothing matches is a door left open for the next embed.
        scopes = {(name, scope) for name, scope, _ in _embed_uses()}
        self.assertEqual(sorted(CLASSIC_FALLBACKS - scopes), [])


class ACardThatNamesPeopleDoesNotPingThem(unittest.TestCase):
    def test_the_raid_card_is_sent_without_pings(self):
        """The raid card names every raider by mention. An embed never pinged
        one; a text display does, so without this every /boss status called
        the whole party."""
        tree = ast.parse((BOT / "commands" / "boss.py").read_text(encoding="utf-8"))
        send = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "_send_raid_card")
        calls = [call for call in ast.walk(send) if isinstance(call, ast.Call)
                 and getattr(call.func, "attr", "") == "send"]
        self.assertTrue(calls, "the reader found no send in _send_raid_card; the gate is broken, not the tree")
        for call in calls:
            quiet = {kw.arg: ast.unparse(kw.value) for kw in call.keywords}.get("allowed_mentions", "")
            self.assertIn("AllowedMentions.none()", quiet, ast.unparse(call))


class ACardIsDrawnAsAContainer(unittest.TestCase):
    def setUp(self):
        self.cards = _cards()

    def _tree(self, view):
        return view.to_components()

    def test_the_embed_surface_becomes_one_container(self):
        card = self.cards.Card(title="Battle", description="Choose.", color=0xFF0000)
        card.add_field(name="HP", value="10/20")
        card.add_field(name="​", value="an unnamed field")
        card.set_footer(text="Owner locked")
        tree = self._tree(self.cards.card_view(card))
        self.assertEqual([c["type"] for c in tree], [17], "a card is one container")
        container = tree[0]
        self.assertEqual(container["accent_color"], 0xFF0000)
        texts = [c["content"] for c in container["components"] if c["type"] == 10]
        self.assertEqual(texts[0], "## Battle\nChoose.")
        self.assertIn("**HP**\n10/20", texts[1])
        self.assertIn("an unnamed field", texts[1])
        self.assertNotIn("​", texts[1], "a blank field name is not a heading")
        self.assertEqual(texts[-1], "-# Owner locked")

    def test_a_thumbnail_is_a_section_and_an_image_is_a_gallery(self):
        card = self.cards.Card(title="First Sight")
        card.set_thumbnail(url="https://example.com/t.png")
        card.set_image(url="attachment://city.png")
        types = [c["type"] for c in self._tree(self.cards.card_view(card))[0]["components"]]
        self.assertIn(9, types, "the thumbnail sits beside the title in a section")
        self.assertIn(12, types, "the image is a media gallery")

    def test_a_long_card_fits_discords_four_thousand_characters(self):
        card = self.cards.Card(title="Sheet", description="d" * 3000)
        for i in range(20):
            card.add_field(name=f"Field {i}", value="v" * 900)
        card.set_footer(text="Origin: somewhere")
        view = self.cards.card_view(card)
        self.assertLessEqual(view.content_length(), 4000)
        self.assertIn("-# Origin: somewhere", card.text(), "the footer says what the card is, and survives the cut")
        self.assertTrue(card.text().startswith("## Sheet"))

    def test_text_sent_beside_a_card_goes_into_it(self):
        view = self.cards.card_view(self.cards.Card(title="Draft"))
        self.assertTrue(self.cards.fold_content(view, "Draft ready."))
        self.assertEqual(self._tree(view)[0]["components"][0]["content"], "Draft ready.")
        self.cards.fold_content(view, "Draft ready.")
        self.assertEqual(view.card.lead, "Draft ready.", "folding the same text twice doubled it")

    def test_a_layout_that_is_not_a_card_cannot_take_text(self):
        layout = discord.ui.LayoutView()
        layout.add_item(discord.ui.TextDisplay("panel"))
        self.assertTrue(self.cards.is_layout(layout))
        self.assertFalse(self.cards.fold_content(layout, "text"))
        self.assertFalse(self.cards.is_layout(discord.ui.View()))


class AClassicViewMovesByChangingItsBase(unittest.TestCase):
    def setUp(self):
        cards = self.cards = _cards()

        class Panel(cards.CardView):
            def __init__(self):
                super().__init__(timeout=None)
                self.pressed = []
                self.add_item(discord.ui.Select(
                    placeholder="Use a technique", options=[discord.SelectOption(label="Palm")], row=1,
                ))
                self.set_card(cards.Card(title="Battle"))

            @discord.ui.button(label="Attack", row=0, custom_id="panel:attack")
            async def attack(self, interaction, button):
                self.pressed.append((self, button.label))

            @discord.ui.button(label="Flee", row=0, custom_id="panel:flee")
            async def flee(self, interaction, button):
                self.pressed.append((self, button.label))

        self.view = Panel()

    def test_the_controls_sit_in_rows_under_the_card(self):
        container = self.view.to_components()[0]
        rows = [c for c in container["components"] if c["type"] == 1]
        self.assertEqual([[b.get("label") or b.get("placeholder") for b in row["components"]] for row in rows],
                         [["Attack", "Flee"], ["Use a technique"]])
        self.assertTrue(self.view.has_components_v2())

    def test_a_decorated_button_is_still_bound_to_its_view(self):
        button = next(item for item in self.view.controls if getattr(item, "label", "") == "Attack")
        self.assertIs(button.view, self.view)
        asyncio.run(button.callback(None))
        self.assertEqual(self.view.pressed, [(self.view, "Attack")])

    def test_clear_items_keeps_the_card_and_disabling_reaches_every_control(self):
        self.view.disable_controls()
        self.assertTrue(all(item.disabled for item in self.view.controls))
        self.view.clear_items()
        self.assertEqual(self.view.controls, [])
        container = self.view.to_components()[0]
        self.assertEqual(container["components"][0]["content"], "## Battle")

    def test_a_persistent_card_is_still_persistent(self):
        # add_view refuses a view whose items lack a custom_id; the event and
        # exploration panels are restored at startup through it.
        for item in self.view.controls:
            if isinstance(item, discord.ui.Select):
                self.view.remove_item(item)
        self.assertTrue(self.view.is_persistent())


if __name__ == "__main__":
    unittest.main()
