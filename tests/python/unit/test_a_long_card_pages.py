"""A card too long for one message pages instead of being cut (v1.12.1).

Asked as *"if the discord layout is too long can we do page 2?"*. The hub
panels already paged: actions behind "More actions", a plain result up to five
pages with Prev/Next. A **card** did not. v1.9.0 drew every card as a
Components V2 container, which Discord caps at 4,000 characters where an embed
allowed 6,000, and `_fitted` cut the fields and then the description with an
ellipsis - so the end of a long sheet, a profession card or a long reply was
simply lost. `Card._pages` splits it instead, and `CardView` puts
◀ Page n/N ▶ under it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path

import discord
import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
BOT = ROOT / "app" / "bot"


def _cards():
    spec = importlib.util.spec_from_file_location("cards_paging_under_test", BOT / "cards.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _long_card(cards):
    card = cards.Card(title="Sheet", description="opening words")
    for i in range(12):
        card.add_field(name=f"Field {i}", value=f"value-{i} " + "v" * 800)
    card.set_footer(text="Origin: somewhere")
    return card


def _texts(view) -> list[str]:
    out = []

    def walk(component):
        if component.get("type") == 10:
            out.append(component["content"])
        for child in component.get("components", []) or []:
            walk(child)

    for top in view.to_components():
        walk(top)
    return out


def _buttons(view) -> list[dict]:
    out = []

    def walk(component):
        if component.get("type") == 2:
            out.append(component)
        for child in component.get("components", []) or []:
            walk(child)

    for top in view.to_components():
        walk(top)
    return out


class ALongCardPages(unittest.TestCase):
    def setUp(self):
        self.cards = _cards()

    def test_a_card_that_fits_is_one_page_and_has_no_buttons(self):
        card = self.cards.Card(title="Short", description="fits")
        view = self.cards.card_view(card)
        self.assertEqual(card.page_count(), 1)
        self.assertEqual(_buttons(view), [])
        self.assertIsNone(view.timeout, "a card with nothing to press must not start a timer")

    def test_nothing_is_lost_across_the_pages(self):
        card = _long_card(self.cards)
        self.assertGreater(card.page_count(), 1, "the reader built a card that fits; the test is broken")
        seen = "\n".join(
            "\n".join(part for part in page if part) for page in card._pages())
        for i in range(12):
            self.assertIn(f"value-{i} ", seen, f"field {i} was cut instead of paged")
        self.assertNotIn("…", seen, "a card under ten pages is paged whole, never cut")

    def test_every_page_fits_and_says_what_the_card_is(self):
        card = _long_card(self.cards)
        view = self.cards.card_view(card)
        for page in range(card.page_count()):
            view.page = page
            view._lay_out()
            self.assertLessEqual(view.content_length(), 4000, f"page {page + 1} is over Discord's limit")
            texts = _texts(view)
            self.assertTrue(any(t.startswith("## Sheet") for t in texts), f"page {page + 1} lost its title")
            self.assertEqual(texts[-1], "-# Origin: somewhere", f"page {page + 1} lost its footer")

    def test_the_buttons_turn_the_page_and_wrap(self):
        card = _long_card(self.cards)
        view = self.cards.card_view(card)
        count = card.page_count()
        labels = [b.get("label") for b in _buttons(view)]
        self.assertEqual(labels, ["◀", f"Page 1/{count}", "▶"])
        self.assertIsNotNone(view.timeout, "a paged card with no timeout holds a view for ever")
        nxt = next(item for item in view.walk_children() if isinstance(item, self.cards.CardPageButton) and item.step == 1)
        prev = next(item for item in view.walk_children() if isinstance(item, self.cards.CardPageButton) and item.step == -1)

        edits = []

        class Response:
            async def edit_message(self, **kwargs):
                edits.append(kwargs)

        class Interaction:
            response = Response()

        asyncio.run(nxt.callback(Interaction()))
        self.assertEqual(view.page, 1)
        self.assertIs(edits[-1]["view"], view)
        self.assertIn(f"Page 2/{count}", [b.get("label") for b in _buttons(view)])
        prev = next(item for item in view.walk_children() if isinstance(item, self.cards.CardPageButton) and item.step == -1)
        asyncio.run(prev.callback(Interaction()))
        asyncio.run(next(item for item in view.walk_children()
                         if isinstance(item, self.cards.CardPageButton) and item.step == -1).callback(Interaction()))
        self.assertEqual(view.page, count - 1, "◀ on the first page wraps to the last")

    def test_the_page_timeout_is_the_registered_one(self):
        self.cards.register_page_timeout(lambda: 1234.0)
        view = self.cards.card_view(_long_card(self.cards))
        self.assertEqual(view.timeout, 1234.0)

    def test_a_view_that_must_stay_reregistrable_keeps_the_cut(self):
        """A view with its own controls and no timeout may be re-registered at
        boot, which needs every control to carry a fixed id; page buttons carry
        none, so it shows the first page and says more was cut."""
        cards = self.cards

        class Panel(cards.CardView):
            @discord.ui.button(label="Act", custom_id="panel:act")
            async def act(self, interaction, button):
                pass

        view = Panel(card=_long_card(cards), timeout=None)
        self.assertEqual([b.get("label") for b in _buttons(view)], ["Act"])
        self.assertIsNone(view.timeout)
        self.assertTrue(view.is_persistent())
        self.assertTrue(any(t.endswith("…") for t in _texts(view)), "the cut no longer says it was cut")

    def test_a_long_notice_pages_too(self):
        body = "\n\n".join(f"Paragraph {i}: " + "w " * 300 for i in range(12))
        view = self.cards.card_view(self.cards.notice_card(body))
        self.assertGreater(view.card.page_count(), 1)
        self.assertTrue(any(b.get("label", "").startswith("Page 1/") for b in _buttons(view)))


class TheSurfaceRegistersThePanelsWindow(unittest.TestCase):
    def test_register_page_timeout_is_handed_panel_timeout(self):
        source = (BOT / "surface.py").read_text(encoding="utf-8")
        self.assertTrue("register_page_timeout(panel_timeout)" in source,
                        "a paged card's buttons do not wait as long as the panel they came from")


if __name__ == "__main__":
    unittest.main()
