"""Every panel that outlives a process survives a reboot (v1.0.0-rc.22).

Discord keeps the message; the process does not keep the view. Before this,
`EventSceneView` carried a timeout and no custom_id, which is precisely the
pair discord.py refuses to register for persistent listening - so every
restart left the controls of every open event dead. The thread was there, the
realm was open for hours yet, and every button answered "This interaction
failed".

Two halves, and both are needed: the view has to be persistent at all
(asserted here against discord.py's own rule, not a substring), and startup
has to re-register one per scene that is still open.

"All systems" is the registry, not a list kept in bot.py: each family that
owns a long-lived panel registers its own restorer, and startup walks them.
Every panel that does *not* outlive a process stays out - the hubs, pickers
and confirms all time out inside fifteen minutes and are re-opened by running
the command again, and registering those would leave live-looking buttons on
messages whose moment has passed.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

BOT_PY = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
DB_PY = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")


def _event_scene():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.ui.event_scene")


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name
    )
    return ast.get_source_segment(source, node)


class ThePanelIsPersistent(unittest.TestCase):
    def _view(self, event_key: str = "secret_realm_rotation:hollow_throne_vault:44723"):
        scene = _event_scene()
        return scene, scene.EventSceneView(
            title="Hollow Throne Vault", event_type="secret_realm", expires_at=2_000_000_000.0,
            location="Hollow Throne Ruin", event_key=event_key, category="Rotation", severity=4,
        )

    def test_discord_would_accept_it_for_persistent_listening(self):
        # is_persistent() is the exact check Client.add_view makes: no timeout,
        # and every component carrying an explicit custom_id.
        _, view = self._view()
        self.assertTrue(view.is_persistent(), "add_view would reject this panel")
        self.assertIsNone(view.timeout)
        for item in view.children:
            self.assertTrue(getattr(item, "custom_id", None), f"{item!r} has no custom_id")

    def test_ids_are_stable_across_processes_and_unique_per_event(self):
        scene, first = self._view()
        _, again = self._view()
        self.assertEqual([i.custom_id for i in first.children], [i.custom_id for i in again.children])
        _, other = self._view("secret_realm_rotation:other_vault:1")
        self.assertFalse(
            set(i.custom_id for i in first.children) & set(i.custom_id for i in other.children),
            "two live events would fight over the same controls",
        )
        # Discord's ceiling, whatever the event key grows into.
        long_key = "world_event:" + "x" * 400
        for item in scene.EventSceneView(
            title="t", event_type="random_event", expires_at=2_000_000_000.0, event_key=long_key,
        ).children:
            self.assertLessEqual(len(item.custom_id), 100)

    def test_the_site_select_keeps_its_id_when_its_options_change(self):
        # The site menu is rebuilt from live state on every render; rebuilt is
        # not replaced, so the id has to be the event's, not the options'.
        scene, view = self._view()
        nodes = [{"node_key": "beast:1", "name": "Ashen Wolf", "node_type": "beast", "remaining": 2, "total": 3}]
        view._refresh_site_select(nodes)
        first = next(i for i in view.children if isinstance(i, scene.EventSiteSelect)).custom_id
        view._refresh_site_select([{**nodes[0], "remaining": 1}])
        second = next(i for i in view.children if isinstance(i, scene.EventSiteSelect)).custom_id
        self.assertEqual(first, second)
        self.assertTrue(view.is_persistent())


class StartupRestoresTheOpenScenes(unittest.TestCase):
    def test_the_restore_reads_the_live_scenes_and_registers_each(self):
        scene = _event_scene()
        restore = _body(
            (PROJECT_ROOT / "app" / "bot" / "ui" / "event_scene.py").read_text(encoding="utf-8"),
            "restore_event_scene_views",
        )
        self.assertIn("DB.get_live_event_threads()", restore)
        self.assertIn("bot.add_view(", restore)
        self.assertIn("EventSceneView(", restore)
        self.assertTrue(callable(scene.restore_event_scene_views))

    def test_the_query_is_the_mirror_of_the_expiry_sweep(self):
        live = _body(DB_PY, "get_live_event_threads")
        self.assertIn("active=1 AND expires_at>?", live)

    def test_startup_restores_before_it_takes_commands(self):
        hook = _body(BOT_PY, "setup_hook")
        self.assertIn("VIEW_RESTORERS.restore_all(self)", hook)
        # Panels come back before the tick that closes expired ones starts, so
        # a scene is never swept while its controls are still unregistered.
        self.assertLess(hook.index("VIEW_RESTORERS.restore_all("), hook.index("event_expiry_worker()"))



class TheRegistryCoversEveryLongLivedPanel(unittest.TestCase):
    def _registry(self):
        with patch.dict(os.environ, ENV):
            importlib.import_module("app.bot.surface")  # imports every command module
            return importlib.import_module("app.bot.registry").VIEW_RESTORERS

    def test_both_families_register_themselves_by_wiring_the_surface(self):
        # Nothing in bot.py names them: the surface import is what registers
        # them, which is what makes a third family a one-line change.
        self.assertEqual(self._registry().names(), ("event_scene", "exploration_event"))

    def test_startup_walks_the_registry_rather_than_a_hand_written_list(self):
        hook = _body(BOT_PY, "setup_hook")
        self.assertIn("VIEW_RESTORERS.restore_all(self)", hook)
        self.assertNotIn("restore_event_scene_views(", hook)

    def test_one_failing_restorer_never_costs_the_others(self):
        registry = self._registry()

        async def boom(_bot):
            raise RuntimeError("engine is down")

        async def fine(_bot):
            return 3

        registry.register("test_boom", boom)
        registry.register("test_fine", fine)
        try:
            result = asyncio.run(registry.restore_all(object()))
        finally:
            registry._restorers.pop("test_boom", None)
            registry._restorers.pop("test_fine", None)
        self.assertEqual(result["test_boom"], 0)
        self.assertEqual(result["test_fine"], 3)


class TheExplorationPanelIsPersistentToo(unittest.TestCase):
    def _view(self, event_id="evt-1", owner=42):
        with patch.dict(os.environ, ENV):
            exploration = importlib.import_module("app.bot.commands.exploration")
        event = {"event_id": event_id, "title": "A stranger on the road", "active": True,
                 "expires_at": 2_000_000_000.0, "available_actions": [{"key": "observe", "label": "Observe"}]}
        return exploration, exploration.ExplorationEventView(owner, event)

    def test_discord_would_accept_it_for_persistent_listening(self):
        _, view = self._view()
        self.assertTrue(view.is_persistent(), "add_view would reject this panel")
        self.assertIsNone(view.timeout)

    def test_two_players_in_one_encounter_do_not_share_controls(self):
        _, mine = self._view(owner=1)
        _, theirs = self._view(owner=2)
        self.assertFalse(set(i.custom_id for i in mine.children) & set(i.custom_id for i in theirs.children))

    def test_the_buttons_still_know_which_action_they_are(self):
        # _sync_buttons reads the action out of the id; namespacing the id
        # must not stop it disabling what the engine no longer offers.
        _, view = self._view()
        labels = {str(i.label): i.disabled for i in view.children}
        self.assertFalse(labels["Observe"], "an available action must stay enabled")
        self.assertFalse(labels["Refresh"], "refresh is always available")
        self.assertTrue(labels["Approach"], "an action the engine did not offer must be disabled")

    def test_the_restore_reads_live_encounters_and_registers_each(self):
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
        restore = _body(source, "restore_exploration_event_views")
        self.assertIn("DB.get_live_exploration_events()", restore)
        self.assertIn('ENGINE.action("exploration.event.status"', restore)
        self.assertIn("bot.add_view(", restore)
        live = _body(DB_PY, "get_live_exploration_events")
        self.assertIn("p.status='active' AND e.state='active' AND e.expires_at>?", live)
