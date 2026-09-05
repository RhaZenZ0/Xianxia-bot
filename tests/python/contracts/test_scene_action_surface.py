"""Contracts for how the Scene Action panel is built and sent.

Reported: "Scene Action still have a drop-down menu and scene action need to
travel with the player."

The dropdown half is a rollout gap - the 17 command hubs moved to the action-list
layout in v0.19.5-v0.19.7 and this panel is not a hub, so nothing carried it
along. These tests hold the new wiring in place: one factory, one set of send
kwargs, and no path that sends the dropdown panel while the layout is available.
"""

import re
import unittest

from tests.support import PROJECT_ROOT

BOT = (PROJECT_ROOT / "app" / "bot" / "main.py").read_text(encoding="utf-8")
LAYOUT = (PROJECT_ROOT / "app" / "bot" / "scene_layout.py").read_text(encoding="utf-8")


class PanelFactoryTests(unittest.TestCase):
    def test_the_classic_dropdown_view_is_only_built_inside_the_factory(self):
        # Four call sites used to build it directly. Any new one that does is a
        # path that can still ship dropdowns.
        self.assertEqual(BOT.count("SceneActionView("), 2)  # the class def, and the fallback
        start = BOT.index("def scene_action_panel(")
        fallback = BOT[start : start + 1400]
        self.assertIn("SceneActionView(", fallback)

    def test_no_scene_action_send_site_hand_builds_an_embed(self):
        # A Components V2 message cannot carry an embed; mixing the two paths by
        # hand is how one of them silently regresses. Scoped to the Scene Action
        # senders - ExplorationEventView is a different, still-classic panel.
        for marker, span in (
            ("async def _open_scene_actions(", 900),
            ("async def scene_action_command(", 2200),
        ):
            body = BOT[BOT.index(marker) :][:span]
            self.assertNotIn("embed=", body, marker)

    def test_every_scene_panel_send_uses_the_factory_kwargs(self):
        sends = [line for line in BOT.splitlines() if "panel_kwargs" in line or "**kwargs" in line]
        self.assertGreaterEqual(len(sends), 3)

    def test_the_factory_returns_a_view_and_its_send_kwargs(self):
        start = BOT.index("def scene_action_panel(")
        signature = BOT[start : start + 400]
        self.assertIn("tuple[discord.ui.View, dict[str, Any]]", signature)

    def test_the_layout_is_feature_detected_not_assumed(self):
        # Same discipline as the hubs: on an older discord.py the names are
        # simply missing and the classic panel is used instead of raising.
        self.assertIn("scene_layout.LAYOUT_COMPONENTS_AVAILABLE", BOT)
        self.assertIn("LAYOUT_COMPONENTS_AVAILABLE = all(", LAYOUT)


class NoDropdownTests(unittest.TestCase):
    def test_the_layout_module_never_constructs_a_select(self):
        self.assertNotIn("ui.Select", LAYOUT)
        self.assertNotIn("SelectOption", LAYOUT)

    def test_the_dropdown_classes_survive_only_for_the_classic_fallback(self):
        for name in ("SceneActionTypeSelect", "SceneActionTargetSelect"):
            self.assertIn(name, BOT, name)
        # Constructed only by SceneActionView.refresh_components, nowhere else.
        # (The "class X(" definition line is excluded, not counted.)
        for name in ("SceneActionTypeSelect", "SceneActionTargetSelect"):
            uses = len(re.findall(rf"(?<!class )\b{name}\(", BOT))
            self.assertEqual(uses, 1, f"{name} is constructed {uses} times")


class TravelTests(unittest.TestCase):
    def test_the_panel_is_given_a_way_to_re_read_the_players_scene(self):
        self.assertIn("async def _scene_reload_state(", BOT)
        body = BOT[BOT.index("async def _scene_reload_state(") :][:900]
        self.assertIn("DB.get_character(", body)
        self.assertIn("_scene_action_targets(", body)
        self.assertIn("character_location_display(", body)

    def test_a_failed_reload_returns_none_rather_than_raising(self):
        body = BOT[BOT.index("async def _scene_reload_state(") :][:900]
        self.assertIn("except Exception:", body)
        self.assertIn("return None", body)

    def test_every_interaction_reloads_before_rendering(self):
        body = LAYOUT[LAYOUT.index("async def refresh_and_edit(") :][:400]
        self.assertLess(body.index("await self.reload()"), body.index("self.rebuild()"))

    def test_resolve_reloads_before_opening_the_modal(self):
        body = LAYOUT[LAYOUT.index("class SceneLayoutResolveButton") :][:1200]
        self.assertLess(body.index("await self.scene_view.reload()"), body.index("open_modal("))


class LayoutIsolationTests(unittest.TestCase):
    def test_the_layout_module_imports_nothing_from_the_project(self):
        # This is what lets the panel be built and counted in a sandbox with no
        # discord.py, which is the only place the component budget gets checked.
        for line in LAYOUT.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                self.assertNotIn("from .", stripped)
                self.assertNotIn("from ..", stripped)

    def test_the_component_limit_is_written_down(self):
        self.assertIn("COMPONENT_LIMIT = 40", LAYOUT)

    def test_an_action_row_never_gets_more_than_five_buttons(self):
        self.assertIn("BUTTONS_PER_ROW = 5", LAYOUT)


if __name__ == "__main__":
    unittest.main()
