import asyncio
import unittest
from types import SimpleNamespace

from tests.support import install_discord_ui_shim, load_module_by_path

install_discord_ui_shim()

import discord  # noqa: E402  (shim or the real library, whichever is present)

scene_layout = load_module_by_path("scene_layout_under_test", "app/bot/scene_layout.py")

PROFILES = {
    "observe": {"label": "Observe", "emoji": "👁️", "attribute": "insight", "tn": 11, "description": "Read the scene."},
    "investigate": {"label": "Investigate", "emoji": "🔎", "attribute": "insight", "tn": 14, "description": "Search."},
    "influence": {"label": "Influence", "emoji": "🗣️", "attribute": "presence", "tn": 14, "description": "Persuade."},
    "stealth": {"label": "Stealth", "emoji": "🌫️", "attribute": "agility", "tn": 14, "description": "Hide."},
    "physical": {"label": "Physical Feat", "emoji": "💪", "attribute": "body", "tn": 14, "description": "Climb."},
    "qi": {"label": "Qi Control", "emoji": "✨", "attribute": "spirit", "tn": 14, "description": "Shape qi."},
    "resolve": {"label": "Resolve", "emoji": "🧘", "attribute": "will", "tn": 14, "description": "Endure."},
    "aid": {"label": "Aid", "emoji": "🤝", "attribute": "presence", "tn": 11, "description": "Support."},
}


class _TestInteraction:
    """Minimal interaction double for Scene Action callback tests."""

    def __init__(self, user_id: int):
        self.user = SimpleNamespace(id=user_id)
        self.edited_with = None
        self.sent = []
        self.response = self

    def is_done(self) -> bool:
        return False

    async def edit_message(self, **kwargs):
        self.edited_with = kwargs

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))


def _interaction(user_id: int) -> _TestInteraction:
    return _TestInteraction(user_id)


def _view(*, npcs=(), location="Moonfen Marsh", reload_state=None, open_modal=None, character=None):
    async def _no_reload(_owner_id):
        return None

    async def _no_modal(_interaction, _action, _target):
        return None

    return scene_layout.SceneActionLayoutView(
        owner_id=7,
        profiles=PROFILES,
        character=character if character is not None else {"location": "moonfen_marsh", "name": "Arceus"},
        npcs=list(npcs),
        location_display=location,
        reload_state=reload_state or _no_reload,
        open_modal=open_modal or _no_modal,
    )


def _walk(view):
    stack = list(view.children)
    while stack:
        item = stack.pop()
        yield item
        children = list(getattr(item, "children", []) or [])
        accessory = getattr(item, "accessory", None)
        if accessory is not None:
            children.append(accessory)
        stack.extend(children)


class NoDropdownTests(unittest.TestCase):
    """The reported bug: "Scene Action still have a drop-down menu"."""

    def test_the_panel_contains_no_select_menu_at_all(self):
        view = _view(npcs=[f"NPC {i}" for i in range(20)])
        selects = [item for item in _walk(view) if isinstance(item, discord.ui.Select)]
        self.assertEqual(selects, [])

    def test_actions_are_rendered_as_buttons(self):
        view = _view()
        labels = {
            getattr(item, "label", "")
            for item in _walk(view)
            if isinstance(item, discord.ui.Button)
        }
        for profile in PROFILES.values():
            self.assertIn(profile["label"], labels)

    def test_environment_and_self_are_always_offered(self):
        view = _view()
        labels = {getattr(item, "label", "") for item in _walk(view) if isinstance(item, discord.ui.Button)}
        self.assertIn("Environment", labels)
        self.assertIn("Self", labels)

    def test_the_panel_is_built_inside_one_container(self):
        view = _view()
        self.assertEqual(len(view.children), 1)
        self.assertIsInstance(view.children[0], discord.ui.Container)


class ComponentBudgetTests(unittest.TestCase):
    """Discord counts nested components against a limit of 40.

    A layout that looks fine in source is rejected at send time when it goes
    over, which a build sandbox with no discord.py would never discover.
    """

    def test_an_empty_scene_fits(self):
        self.assertLessEqual(scene_layout.component_budget(_view()), scene_layout.COMPONENT_LIMIT)

    def test_a_full_target_page_fits_with_headroom(self):
        view = _view(npcs=[f"Cultivator {i}" for i in range(30)])
        budget = scene_layout.component_budget(view)
        self.assertLessEqual(budget, scene_layout.COMPONENT_LIMIT)
        # Headroom in case Discord ever counts a nested component differently.
        self.assertLessEqual(budget, 36)

    def test_no_action_row_exceeds_five_components(self):
        # The shim asserts this on add_item; building the worst case is the test.
        view = _view(npcs=[f"Cultivator {i}" for i in range(30)])
        rows = [item for item in _walk(view) if isinstance(item, discord.ui.ActionRow)]
        self.assertTrue(rows)
        for row in rows:
            self.assertLessEqual(len(row.children), 5)


class TravelTests(unittest.TestCase):
    """The other half of the report: "scene action need to travel with the player"."""

    def test_travel_updates_the_location_and_announces_it(self):
        async def reload(_owner_id):
            return {
                "character": {"location": "verdant_gate", "name": "Arceus"},
                "npcs": ["Elder Shen"],
                "location_display": "Verdant Gate",
            }

        view = _view(npcs=["Marsh Hermit"], reload_state=reload)
        self.assertTrue(asyncio.run(view.reload()))
        self.assertEqual(view.location_display, "Verdant Gate")
        self.assertIn("Verdant Gate", view.header_text())
        self.assertIn("travelled", view.header_text())

    def test_a_target_left_behind_is_reset_rather_than_failing_at_resolve(self):
        # _resolve_scene_action rejects an absent target. Without this the player
        # only finds out after writing their attempt into the modal.
        async def reload(_owner_id):
            return {
                "character": {"location": "verdant_gate"},
                "npcs": ["Elder Shen"],
                "location_display": "Verdant Gate",
            }

        view = _view(npcs=["Marsh Hermit"], reload_state=reload)
        view.target = "Marsh Hermit"
        asyncio.run(view.reload())
        self.assertEqual(view.target, "Environment")
        self.assertIn("no longer in this scene", view.notice)

    def test_a_target_still_present_survives_a_refresh(self):
        async def reload(_owner_id):
            return {
                "character": {"location": "moonfen_marsh"},
                "npcs": ["Marsh Hermit"],
                "location_display": "Moonfen Marsh",
            }

        view = _view(npcs=["Marsh Hermit"], reload_state=reload)
        view.target = "Marsh Hermit"
        self.assertFalse(asyncio.run(view.reload()))
        self.assertEqual(view.target, "Marsh Hermit")
        self.assertEqual(view.notice, "")

    def test_staying_put_clears_a_stale_notice(self):
        async def reload(_owner_id):
            return {
                "character": {"location": "moonfen_marsh"},
                "npcs": [],
                "location_display": "Moonfen Marsh",
            }

        view = _view(reload_state=reload)
        view.notice = "📍 You travelled."
        asyncio.run(view.reload())
        self.assertEqual(view.notice, "")

    def test_a_failed_reload_leaves_the_panel_usable(self):
        async def reload(_owner_id):
            return None

        view = _view(npcs=["Marsh Hermit"], reload_state=reload)
        view.target = "Marsh Hermit"
        self.assertFalse(asyncio.run(view.reload()))
        self.assertEqual(view.target, "Marsh Hermit")
        self.assertEqual(view.location_display, "Moonfen Marsh")

    def test_travel_resets_the_target_page(self):
        async def reload(_owner_id):
            return {
                "character": {"location": "verdant_gate"},
                "npcs": ["Elder Shen"],
                "location_display": "Verdant Gate",
            }

        view = _view(npcs=[f"NPC {i}" for i in range(30)], reload_state=reload)
        view.target_offset = 20
        asyncio.run(view.reload())
        self.assertEqual(view.target_offset, 0)


class ResolveTests(unittest.TestCase):
    def test_resolve_opens_the_modal_when_the_scene_is_unchanged(self):
        opened = []

        async def reload(_owner_id):
            return None

        async def open_modal(_interaction, action_key, target):
            opened.append((action_key, target))

        view = _view(reload_state=reload, open_modal=open_modal)
        button = next(
            item for item in _walk(view) if isinstance(item, scene_layout.SceneLayoutResolveButton)
        )
        asyncio.run(button.callback(_interaction(7)))
        self.assertEqual(opened, [("observe", "Environment")])

    def test_resolve_refreshes_instead_of_opening_the_modal_after_travel(self):
        opened = []

        async def reload(_owner_id):
            return {
                "character": {"location": "verdant_gate"},
                "npcs": [],
                "location_display": "Verdant Gate",
            }

        async def open_modal(_interaction, action_key, target):
            opened.append((action_key, target))

        view = _view(reload_state=reload, open_modal=open_modal)
        button = next(
            item for item in _walk(view) if isinstance(item, scene_layout.SceneLayoutResolveButton)
        )
        interaction = _interaction(7)
        asyncio.run(button.callback(interaction))
        self.assertEqual(opened, [], "a player should not type into a modal about a scene they left")
        self.assertIsNotNone(interaction.edited_with)


class TargetLabelTests(unittest.TestCase):
    def test_player_targets_show_the_name_not_the_id(self):
        # The same mistake Equip made: a database key is not a thing a person
        # recognises.
        self.assertEqual(scene_layout.target_label("Player 4213: Windborne Ke"), "Windborne Ke")

    def test_plain_targets_pass_through(self):
        self.assertEqual(scene_layout.target_label("Marsh Hermit"), "Marsh Hermit")

    def test_an_empty_target_falls_back_to_environment(self):
        self.assertEqual(scene_layout.target_label(""), "Environment")

    def test_a_malformed_player_target_does_not_crash(self):
        self.assertEqual(scene_layout.target_label("Player 4213"), "Player 4213")

    def test_labels_stay_inside_the_button_limit(self):
        self.assertLessEqual(len(scene_layout.target_label("N" * 300)), 80)


class PagingTests(unittest.TestCase):
    def test_targets_beyond_a_page_are_reachable(self):
        view = _view(npcs=[f"NPC {i:02d}" for i in range(25)])
        page = next(item for item in _walk(view) if isinstance(item, scene_layout.SceneLayoutTargetPageButton))
        self.assertIsNotNone(page)

    def test_no_pager_appears_when_everything_fits(self):
        view = _view(npcs=["A", "B"])
        pagers = [item for item in _walk(view) if isinstance(item, scene_layout.SceneLayoutTargetPageButton)]
        self.assertEqual(pagers, [])

    def test_paging_wraps_back_to_the_first_page(self):
        view = _view(npcs=[f"NPC {i:02d}" for i in range(25)])
        view.target_offset = 20
        page = next(item for item in _walk(view) if isinstance(item, scene_layout.SceneLayoutTargetPageButton))
        asyncio.run(page.callback(_interaction(7)))
        self.assertEqual(view.target_offset, 0)


class OwnershipTests(unittest.TestCase):
    def test_another_players_click_is_refused(self):
        view = _view()
        allowed = asyncio.run(view.interaction_check(_interaction(999)))
        self.assertFalse(allowed)

    def test_the_owner_is_allowed(self):
        view = _view()
        self.assertTrue(asyncio.run(view.interaction_check(_interaction(7))))


class GuardTests(unittest.TestCase):
    def test_an_empty_profile_table_is_rejected_loudly(self):
        with self.assertRaises(ValueError):
            scene_layout.SceneActionLayoutView(
                owner_id=1, profiles={}, character={}, npcs=[], location_display="x",
                reload_state=None, open_modal=None,
            )

    def test_an_unknown_action_key_falls_back_to_the_first_profile(self):
        async def noop(*_a):
            return None

        view = scene_layout.SceneActionLayoutView(
            owner_id=1, profiles=PROFILES, character={}, npcs=[], location_display="x",
            reload_state=noop, open_modal=noop, action_key="nonsense",
        )
        self.assertEqual(view.action_key, "observe")


if __name__ == "__main__":
    unittest.main()
