from tests.support import (
    PROJECT_ROOT,
    bot_function_source,
    bot_module_defining,
    bot_package_source,
    bot_source_files,
)
import ast
import re
import unittest

from app.rules.birthfamily import FAMILY_ARCHETYPES
from app.rules.creation_ui import CULTIVATION_STYLE_PROFILES

ROOT = PROJECT_ROOT
HUBS = ROOT / "app" / "bot" / "hubs.py"
# Phase 1 of the main.py split (v0.19.33): bot-side checks read the whole
# package or locate one function, never app/bot/main.py by path - see
# tests/support.py's bot_* helpers for why.


class GUIIntegrityTests(unittest.TestCase):
    def test_character_sheet_never_declares_more_than_discord_field_limit(self):
        sheet = bot_function_source("sheet")
        # This is an upper bound because conditional fields are also counted.
        self.assertLessEqual(sheet.count("embed.add_field"), 25)
        self.assertIn('name="Resources"', sheet)
        self.assertIn('name="Identity & Legacy"', sheet)

    def test_creation_selects_fit_discord_twenty_five_option_limit(self):
        self.assertLessEqual(len(FAMILY_ARCHETYPES), 25)
        self.assertLessEqual(len(CULTIVATION_STYLE_PROFILES), 25)
        self.assertGreaterEqual(len(FAMILY_ARCHETYPES), 11)

    def test_battle_selects_are_explicitly_bounded(self):
        source = bot_package_source()
        self.assertIn("return techniques[:25],usable[:25]", source)

    def test_hub_definitions_fit_page_and_action_select_limits(self):
        source = bot_package_source()
        hub_nodes = [
            node
            for path in bot_source_files()
            if path.name != "hubs.py"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "HubDefinition"
        ]
        self.assertTrue(hub_nodes)
        for hub in hub_nodes:
            name = "unknown"
            pages_node = None
            for kw in hub.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name = str(kw.value.value)
                elif kw.arg == "pages":
                    pages_node = kw.value
            if isinstance(pages_node, (ast.Tuple, ast.List)):
                self.assertLessEqual(len(pages_node.elts), 25, name)

        # Every migrated command group currently fits one Discord action selector.
        group_names = re.findall(r'^(\w+)_group\s*=\s*app_commands\.Group\(name="([^"]+)"', source, re.M)
        for variable, slash_name in group_names:
            commands = re.findall(rf'^@registered_group_command\({re.escape(variable)}_group,\s*name="([^"]+)"', source, re.M)
            self.assertLessEqual(len(commands), 25, slash_name)

    def test_admin_panel_has_seven_sections_and_all_actions_fit_selects(self):
        source = bot_package_source()
        wiring = bot_module_defining("_ADMIN_HUB_DEFINITION").read_text(encoding="utf-8")
        start = wiring.index("_ADMIN_HUB_DEFINITION = HubDefinition(")
        end = wiring.index("@registered_root_command(", start)
        panel = wiring[start:end]
        expected_groups = (
            "admin_server_group", "admin_world_group", "admin_player_group",
            "admin_sect_group", "admin_family_group", "admin_npc_group", "admin_sim_group",
        )
        self.assertEqual(panel.count("HubPage("), 7)
        for group in expected_groups:
            self.assertEqual(panel.count(f"command={group}"), 1, group)
        counts = [
            len(re.findall(rf"@registered_group_command\({re.escape(group)},\s*name=\"([^\"]+)\"", source))
            for group in expected_groups
        ]
        # See test_command_cleanup for why this count is spelled out: 40 through
        # v0.19.14, +2 in v0.19.15 for the administrator chat monitor.
        self.assertEqual(sum(counts), 42)
        self.assertLessEqual(max(counts), 25)

    def test_hub_router_reapplies_range_constraints(self):
        source = HUBS.read_text(encoding="utf-8")
        self.assertIn("_RANGE_ANNOTATION", source)
        self.assertIn("enter a value from", source)
        self.assertIn("converted < minimum or converted > maximum", source)

    def test_hub_has_typed_boolean_member_and_channel_controls(self):
        source = HUBS.read_text(encoding="utf-8")
        for class_name in (
            "HubBoolSelect", "HubBoolView", "HubMemberSelect", "HubMemberView",
            "HubChannelSelect", "HubChannelView", "HubDefaultInputButton",
            "HubDynamicSelect", "HubDynamicView",
        ):
            self.assertIn(f"class {class_name}", source)
        self.assertIn("Use self / default", source)

    def test_hub_uses_rich_cards_and_direct_action_buttons(self):
        source = HUBS.read_text(encoding="utf-8")
        for class_name in (
            "HubQuickActionButton", "HubPageButton", "HubRefreshButton", "CommandHubView",
        ):
            self.assertIn(f"class {class_name}", source)
        self.assertIn("_QUICK_ACTION_LIMIT = 3", source)
        self.assertIn('name="⚡ Quick Actions"', source)
        self.assertIn('name="📖 What can I do here?"', source)
        self.assertIn("_action_button_style(action, index)", source)
        self.assertIn("await _start_hub_action(interaction, self.hub_view, self.action)", source)
        self.assertIn("super().__init__(timeout=900)", source)

    def test_hub_cards_refresh_live_character_and_admin_state(self):
        hub_source = HUBS.read_text(encoding="utf-8")
        bot_source = bot_package_source()
        self.assertIn("class HubStatusField", hub_source)
        self.assertIn("async def refresh_status", hub_source)
        self.assertIn("await self.hub_view.refresh_status(interaction)", hub_source)
        self.assertIn("await view.refresh_status(interaction)", hub_source)
        self.assertIn("async def _player_hub_status", bot_source)
        self.assertIn("async def _admin_hub_status", bot_source)
        for label in ("☯️ Realm", "❤️ Vitality", "💠 Qi", "🎒 Items", "📍 Location"):
            self.assertIn(label, bot_source)
        self.assertIn("status_provider=_player_hub_status", bot_source)
        self.assertIn("status_provider=_admin_hub_status", bot_source)

    def test_hub_component_rows_stay_within_discord_limit(self):
        source = HUBS.read_text(encoding="utf-8")
        self.assertIn("action_row = 1", source)
        self.assertIn("action_row = 0", source)
        self.assertIn("quick_row = action_row + 1", source)
        self.assertIn("controls_row = quick_row + 1", source)
        self.assertNotRegex(source, r"row\s*=\s*[5-9]")

    def test_hub_reuses_live_autocomplete_as_dropdowns(self):
        source = HUBS.read_text(encoding="utf-8")
        bot_source = bot_package_source()
        self.assertIn("def _autocomplete_provider", source)
        self.assertIn('getattr(action.command, "_params", None)', source)
        self.assertIn("async def _live_options", source)
        self.assertIn("async def _present_input_step", source)
        self.assertIn("current live options", source)
        self.assertIn("register_hub_option_provider(admin_closeevent", bot_source)
        self.assertIn("admin_closeevent_hub_options", bot_source)
        self.assertIn("closes in", bot_source)
        self.assertIn('register_hub_option_provider(admin_setsect, "sect_name"', bot_source)
        self.assertIn('register_hub_option_provider(admin_simulation_sect, "sect"', bot_source)

    def test_guided_hub_inputs_can_chain_before_free_text_modal(self):
        source = HUBS.read_text(encoding="utf-8")
        self.assertIn("async def _continue_guided_value", source)
        self.assertIn("remaining_inputs[1:]", source)
        self.assertIn("stopping\n    # before the next parameter", source)
        self.assertIn("remaining=remaining_inputs[index:]", source)

    def test_all_custom_ui_surfaces_have_error_reporting(self):
        bot_source = bot_package_source()
        hub_source = HUBS.read_text(encoding="utf-8")
        self.assertIn("_report_game_ui_error", bot_source)
        self.assertIn("character-creation-modal", bot_source)
        self.assertIn('where=f"battle:', bot_source)
        self.assertIn("_report_hub_ui_error", hub_source)
        self.assertIn("Hub UI callback failed", hub_source)

    def test_event_threads_open_with_playable_gui_controls(self):
        source = bot_package_source()
        self.assertIn("class EventSceneView", source)
        for label in ("Investigate", "Scene Action", "Battle", "Refresh"):
            self.assertIn(f'label="{label}"', source)
        self.assertIn("await _scene_action_targets(character)", source)
        # v0.19.18 moved Scene Action behind scene_action_panel(); the default
        # action is now passed into the factory instead of assigned after
        # construction. Same intent: the event button pre-selects its action.
        self.assertIn("action_key=default_action", source)
        self.assertIn("Travel to **{self.location}** before acting in this event", source)
        self.assertIn("embed=event_view.embed()", source)
        self.assertIn("view=event_view", source)

    def test_hub_resources_render_as_visual_bars(self):
        source = bot_package_source()
        self.assertIn('bar = "▰" * filled + "▱" * (10 - filled)', source)
        self.assertIn('`{bar}` **{percentage}%**', source)

    def test_known_invalid_refresh_glyph_is_absent(self):
        combined = bot_package_source()
        self.assertNotIn('emoji="↻"', combined)
        self.assertNotIn("emoji='↻'", combined)


    def test_hub_actions_are_acknowledged_before_registered_handlers(self):
        source = HUBS.read_text(encoding="utf-8")
        self.assertIn("async def _acknowledge_hub_action", source)
        invoke_start = source.index("async def _invoke_action(")
        invoke_end = source.index("async def _report_hub_ui_error", invoke_start)
        block = source[invoke_start:invoke_end]
        self.assertLess(block.index("await _acknowledge_hub_action(interaction)"), block.index("await action.handler"))
        self.assertIn("InteractionType.modal_submit", source)

    def test_modal_component_counts_stay_below_discord_limit(self):
        bot_source = bot_package_source()
        self.assertIn("for field in (self.name_input, self.concept_input)", bot_source)
        self.assertNotIn("self.style_input = discord.ui.TextInput", bot_source)
        self.assertNotIn("self.root_input = discord.ui.TextInput", bot_source)
        hub_source = HUBS.read_text(encoding="utf-8")
        self.assertIn("self.inputs = list(inputs[:5])", hub_source)
        self.assertIn("remaining if remaining is not None else inputs[5:]", hub_source)
        self.assertIn("if len(modal_inputs) >= 5", hub_source)


if __name__ == "__main__":
    unittest.main()
