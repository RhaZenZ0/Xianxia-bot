"""Every event the tick reports gets a scene thread of its own kind (v1.0.0-rc.22).

A world event that opens in SQLite and nowhere else is an entrance nobody can
walk through. The engine's tick is the only thing that knows an autonomous
event happened, so every event it reports has to become a Discord scene here -
and become the kind of scene the engine said it was, because a secret realm
that is spawned as a "random_event" closes under the wrong label and draws the
wrong panel.

The rotation is what made this concrete: `RotateSecretRealms` opened a realm
every three game days, wrote its world_events row and its public history row,
and answered the caller with a count. Nothing carried it out to the bot, so
`/admin world events` listed a live realm with "Thread: none" and there was no
way in.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
BOT_PY = (BOT / "bot.py").read_text(encoding="utf-8")
EVENT_SCENE = (BOT / "ui" / "event_scene.py").read_text(encoding="utf-8")
ROTATION = (PROJECT_ROOT / "go_core" / "internal" / "game" / "secret_realm_rotation.go").read_text(encoding="utf-8")
MAINTENANCE = (PROJECT_ROOT / "go_core" / "internal" / "simulation" / "advanced_maintenance.go").read_text(encoding="utf-8")
SIM_WORLD = (PROJECT_ROOT / "go_core" / "internal" / "simulation" / "world.go").read_text(encoding="utf-8")


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name
    )
    return ast.get_source_segment(source, node)


class TheTickSpawnsAThreadPerEvent(unittest.TestCase):
    def test_every_reported_event_becomes_a_scene(self):
        worker = _body(BOT_PY, "event_expiry_worker")
        self.assertIn("for sim_run in simulation_runs:", worker)
        self.assertIn("for event in sim_run.events:", worker)
        self.assertIn("spawn_system_event_thread(", worker)
        # The loop walks every run, not only the one system that used to
        # report events - the rotation rides out on advanced_world.
        self.assertNotIn('sim_run.system == "autonomous_world_events"', worker)

    def test_the_engines_event_type_is_not_flattened(self):
        worker = _body(BOT_PY, "event_expiry_worker")
        self.assertIn('event_type=str(event.get("event_type") or "random_event")', worker)
        self.assertIn("event_type=event_type", worker)
        # The old line named every autonomous event a random one.
        self.assertNotIn('event_type="random_event"', worker)

    def test_a_realm_is_announced_as_a_realm(self):
        worker = _body(BOT_PY, "event_expiry_worker")
        self.assertIn('event_type=="secret_realm"', worker)
        self.assertIn("SECRET REALM OPENS", worker)
        scene = _body(EVENT_SCENE, "spawn_system_event_thread")
        self.assertIn('str(event_type) == "secret_realm"', scene)
        self.assertIn("SECRET REALM", scene)


class TheEngineReportsWhatItOpened(unittest.TestCase):
    """The Go half, read as source: Python cannot spawn what it is not told."""

    def test_the_rotation_hands_back_the_realm_not_a_count(self):
        self.assertIn("type OpenedSecretRealm struct", ROTATION)
        self.assertIn(
            "func RotateSecretRealms(conn *storage.Conn, catalog worlddata.Catalog, gm int64) (*OpenedSecretRealm, error)",
            ROTATION,
        )
        for field in ("EventKey", "Name", "Description", "Location", "EndsAt"):
            self.assertIn(field, ROTATION, field)

    def test_maintenance_carries_it_out_on_the_run(self):
        self.assertIn("game.RotateSecretRealms(conn, r.World, gm)", MAINTENANCE)
        self.assertIn('EventType:   "secret_realm"', MAINTENANCE)
        self.assertIn("Events: spawned", MAINTENANCE)

    def test_a_spawned_event_carries_its_type(self):
        self.assertIn('EventType       string   `json:"event_type,omitempty"`', SIM_WORLD)
        self.assertIn('EventType: "random_event"', SIM_WORLD)
