"""v0.40.0: GUI II - one message is the whole GUI.

A Menu button on every panel swaps it into the menu in place and the menu
opens a hub in place; the hub paths a result prints as hints become
buttons under it; a long result pages inside the panel; a Here line says
what the place is; an expired panel keeps a Reopen button; a step message
replaces the previous step; a destructive action asks once.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
HUBS_SOURCE = (BOT / "hubs.py").read_text(encoding="utf-8")
SURFACE_SOURCE = (BOT / "surface.py").read_text(encoding="utf-8")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        surface = importlib.import_module("app.bot.surface")
        hubs = importlib.import_module("app.bot.hubs")
        locations = importlib.import_module("app.bot.locations")
    return surface, hubs, locations


def _count(item) -> int:
    n = 1
    for child in getattr(item, "children", []) or []:
        n += _count(child)
    if getattr(item, "accessory", None) is not None:
        n += 1
    return n


class ResultPagesAndButtons(unittest.TestCase):
    def test_a_result_is_split_on_paragraphs_then_lines_and_never_over_the_budget(self):
        _, hubs, _ = _modules()
        text = "one.\n\n" + ("word " * 300) + "\n\n" + ("line\n" * 200) + "end"
        pages = hubs._result_pages(text)
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), hubs._LAYOUT_RESULT_LIMIT)
        self.assertEqual("".join(p.replace("\n", "").replace(" ", "") for p in pages), text.replace("\n", "").replace(" ", ""))
        self.assertEqual(hubs._result_pages(""), [""])

    def test_the_panel_holds_up_to_five_pages_and_no_more(self):
        _, hubs, _ = _modules()
        self.assertTrue(hubs._panel_can_hold("x" * (hubs._LAYOUT_RESULT_LIMIT * hubs._LAYOUT_RESULT_PAGES), {}))
        self.assertFalse(hubs._panel_can_hold("x" * (hubs._LAYOUT_RESULT_LIMIT * hubs._LAYOUT_RESULT_PAGES + 1), {}))

    def test_hint_paths_become_the_actions_they_name(self):
        _, hubs, _ = _modules()
        actions = hubs.suggested_actions("Then **/world → City → Look**, **/economy → City Shops → Browse**, **/travel**, **/world → City → Look** again, **/world → Hunt**.")
        self.assertEqual([a.path for a in actions], ["/city look", "/shop browse", "/travel go"])  # three at most, no repeats
        self.assertEqual(hubs.suggested_actions("no hints here"), [])
        self.assertEqual(hubs.suggested_actions("**/nowhere → X**"), [])

    def test_a_shown_result_carries_its_pages_and_buttons(self):
        _, hubs, _ = _modules()
        calls = []

        async def refresh_status(_):
            return None

        async def edit(**kwargs):
            calls.append(kwargs)
            return "edited"

        hub_view = SimpleNamespace(message=SimpleNamespace(id=1, edit=edit), last_result="", refresh_status=refresh_status, rebuild=lambda: None, is_layout_hub=True)
        source = SimpleNamespace(message=SimpleNamespace(id=1), response=SimpleNamespace(is_done=lambda: True, edit_message=edit), edit_original_response=edit)
        text = ("a" * 900 + "\n\n") * 2 + "See **/world → Explore**."
        asyncio.run(hubs._show_result_in_panel(source, hub_view, text))
        self.assertEqual(len(hub_view.result_pages), 2)
        self.assertEqual(hub_view.result_page, 0)
        self.assertEqual([a.path for a in hub_view.result_actions], ["/explore"])


class ThePanelBudget(unittest.TestCase):
    def test_every_page_with_a_paged_result_and_three_buttons_fits_discords_cap(self):
        _, hubs, _ = _modules()
        worst = 0
        for definition in hubs.REGISTERED_HUBS:
            view = hubs.LayoutHubView(1, definition, owner_name="T")
            for page in definition.pages:
                view.page_key = page.key
                view.action_offset = 0
                view.last_result = ("x" * 990 + "\n") * 3
                view.result_pages = hubs._result_pages(view.last_result)
                view.result_actions = hubs.suggested_actions("**/world → City → Look** **/world → Explore** **/world → Hunt**")
                view.rebuild()
                worst = max(worst, sum(_count(c) for c in view.children))
                self.assertLessEqual(view.row_limit, hubs._LAYOUT_ACTION_LIMIT)
        self.assertLessEqual(worst, hubs._LAYOUT_COMPONENT_CAP)

    def test_the_control_row_has_menu_and_at_most_five_buttons(self):
        self.assertIn("controls.add_item(HubLayoutMenuButton(self))", HUBS_SOURCE)
        self.assertNotIn("HubLayoutActionPageButton(self, direction=-1)", HUBS_SOURCE)


class OneMessageIsTheGUI(unittest.TestCase):
    def test_the_menu_is_a_panel_that_opens_hubs_in_place(self):
        surface, hubs, _ = _modules()
        self.assertTrue(callable(hubs._MENU_BUILDER))
        menu = hubs._MENU_BUILDER(owner_id=1, is_admin=True, owner_name="T")
        self.assertIsInstance(menu, surface.MenuView)
        # Four rows of four and an admin row (v1.0.0-rc.3), inside the budget.
        self.assertLessEqual(sum(_count(c) for c in menu.children), hubs._LAYOUT_COMPONENT_CAP)
        self.assertIn("await open_hub_in_place(interaction, definition, provider)", SURFACE_SOURCE)
        self.assertIn("await open_hub_in_place(interaction, _ADMIN_HUB_DEFINITION, _admin_hub_status)", SURFACE_SOURCE)
        self.assertIn("register_menu_builder(", SURFACE_SOURCE)

    def test_an_expired_panel_keeps_reopen(self):
        _, hubs, _ = _modules()
        definition = hubs.REGISTERED_HUBS[0]
        view = hubs.LayoutHubView(7, definition, owner_name="T")
        expired = hubs.ExpiredPanelView(view)
        self.assertEqual(expired.owner_id, 7)
        self.assertIsNone(expired.timeout)
        labels = [getattr(b, "label", "") for row in expired.children[0].children if hasattr(row, "children") for b in row.children]
        self.assertIn("Reopen", labels)
        self.assertIn("await self.message.edit(view=ExpiredPanelView(self))", HUBS_SOURCE)

    def test_a_step_replaces_the_previous_step_and_a_danger_action_asks_first(self):
        self.assertIn("async def _step_reply(", HUBS_SOURCE)
        self.assertIn("await interaction.response.edit_message(content=content, view=view)", HUBS_SOURCE)
        self.assertIn("if _is_danger_action(action) and not confirmed:", HUBS_SOURCE)
        self.assertIn("class HubConfirmView(discord.ui.View):", HUBS_SOURCE)
        self.assertNotIn("interaction.response.send_message(\n            f\"**{action.label}** — select", HUBS_SOURCE)


class TheHereLineAndThePicker(unittest.TestCase):
    def test_here_says_what_the_place_is(self):
        _, _, locations = _modules()
        self.assertIn("a town", locations.here_summary("Greenriver Town"))
        self.assertIn("a capital", locations.here_summary("Azure Crown Imperial City"))
        self.assertIn("wayside shrine on the Greenriver Town – Riverguard City road", locations.here_summary("Shrine of the Patient Ox"))
        self.assertIn("kept by", locations.here_summary("Greenriver Apothecary"))
        self.assertIn("gate of the Azure Cloud Sect", locations.here_summary("Azure Cloud Mountain Gate"))
        self.assertEqual(locations.here_summary("abode:1"), "")
        self.assertLessEqual(len(locations.here_summary("Greenriver Town")), 180)
        self.assertIn('HubStatusField("🧭 Here", summary, inline=False)', SURFACE_SOURCE)

    def test_the_travel_picker_groups_and_counts_hops(self):
        _, _, locations = _modules()
        known = {"Greenriver Town", "Riverguard City", "Azure Crown Imperial City", "Jadewood Medicine City", "Shrine of the Patient Ox", "Greenriver Inn", "Golden Pavilion Auction House"}
        rows = locations.destination_groups("Greenriver Town", known, 0)
        by_name = {name: (emoji, desc) for name, emoji, desc, _ in rows}
        self.assertNotIn("Greenriver Town", by_name)
        self.assertNotIn("Golden Pavilion Auction House", by_name, "an auction floor is entered by its warded door, not the picker")
        self.assertEqual(by_name["Greenriver Inn"][0], "🏙️")
        self.assertIn("half a leg", by_name["Shrine of the Patient Ox"][1])
        self.assertIn("1 road hop", by_name["Riverguard City"][1])
        self.assertIn("2 road hops", by_name["Jadewood Medicine City"][1])
        self.assertEqual(rows[0][1], "🏙️", "this city's parts come first")
        from_site = {name: desc for name, _, desc, _ in locations.destination_groups("Shrine of the Patient Ox", known, 0)}
        self.assertIn("half a leg", from_site["Greenriver Town"])
        self.assertIn('register_hub_option_provider(travel, "destination", travel_destination_hub_options)', (BOT / "commands" / "exploration.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
