"""The dashboard's "Wants your attention" feed and its one lag rule (v1.39.0).

The header read "1 system behind" on a healthy world at ×4 time, and cleared a
minute later: a system's lag passes its interval a moment before the bot's
worker runs the due systems, and the feed flagged the moment. The rule lives in
`_simulation_behind`, once: behind means further past the interval than two
worker passes at the clock's scale, and a system the GM switched off is never
behind, because its clock stands still on purpose.
"""
from __future__ import annotations

import math
import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3",
       "DASHBOARD_TOKEN": "dashboard-test-token-1234567890"}


def _server():
    import importlib

    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.dashboard.server")


def _row(system: str, interval: int, lag: int) -> dict:
    return {"system": system, "interval_game_minutes": interval, "lag_game_minutes": lag}


class TheLagRule(unittest.TestCase):
    def setUp(self):
        self.server = _server()
        self.on = dict(self.server.AUTOMATION_DEFAULTS)

    def grace(self, scale: float) -> int:
        from app.ops.config import SIMULATION_PASS_SECONDS

        return math.ceil(2 * SIMULATION_PASS_SECONDS / 60.0 * max(1.0, scale))

    def test_a_system_between_two_passes_is_not_behind(self):
        # ×4 time: the worker's thirty-second pass is two game minutes, so a
        # system up to four minutes past its interval is waiting for the
        # next pass, not behind.
        behind = self.server._simulation_behind
        self.assertEqual(self.grace(4), 4)
        self.assertFalse(behind(_row("dynamic_economy", 1440, 1440), self.on, 4))
        self.assertFalse(behind(_row("dynamic_economy", 1440, 1441), self.on, 4))
        self.assertFalse(behind(_row("dynamic_economy", 1440, 1444), self.on, 4))
        self.assertTrue(behind(_row("dynamic_economy", 1440, 1445), self.on, 4), "a fifth minute past is a missed pass")

    def test_the_grace_follows_the_clocks_scale(self):
        behind = self.server._simulation_behind
        # At ×1 a pass is half a game minute; two passes round up to one.
        self.assertFalse(behind(_row("npc_life", 10080, 10081), self.on, 1))
        self.assertTrue(behind(_row("npc_life", 10080, 10082), self.on, 1))
        # At ×60 a pass is thirty game minutes.
        self.assertFalse(behind(_row("npc_life", 10080, 10080 + 60), self.on, 60))
        self.assertTrue(behind(_row("npc_life", 10080, 10080 + 61), self.on, 60))
        # An unreadable or missing scale is read as ×1, never as no grace.
        self.assertFalse(behind(_row("npc_life", 10080, 10081), self.on, None))
        self.assertFalse(behind(_row("npc_life", 10080, 10081), self.on, "x"))

    def test_a_system_further_behind_than_its_interval_is_early_not_late(self):
        # The rule the feed always had (v1.34.0): a 4,320-minute system 500
        # minutes behind is waiting its turn.
        self.assertFalse(self.server._simulation_behind(_row("clan_dynamics", 4320, 500), self.on, 4))

    def test_a_system_the_gm_switched_off_is_never_behind(self):
        off = dict(self.on, black_markets=False)
        row = _row("black_markets", 180, 999_999)
        self.assertTrue(self.server._simulation_behind(row, self.on, 4), "the fixture is not behind when on; the test proves nothing")
        self.assertFalse(self.server._simulation_behind(row, off, 4))

    def test_a_system_the_toggles_do_not_name_is_judged_on_its_lag(self):
        # A system the engine added that the dashboard's table has not caught
        # up with is judged as if on: a toggle this dict does not carry cannot
        # have been switched off.
        self.assertTrue(self.server._simulation_behind(_row("a_new_system", 10, 100), self.on, 4))

    def test_a_system_with_no_interval_is_never_behind(self):
        self.assertFalse(self.server._simulation_behind(_row("dynamic_economy", 0, 100), self.on, 4))


class TheWorkerAndTheFeedShareOneCadence(unittest.TestCase):
    def test_the_bot_sleeps_on_the_constant_the_feed_reads(self):
        """The pass length used to be a literal 30 in the worker's sleep. The
        feed's grace reads the same name, so the two cannot drift apart."""
        import ast

        from tests.support import PROJECT_ROOT

        tree = ast.parse((PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8"))
        worker = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.AsyncFunctionDef) and node.name == "event_expiry_worker")
        sleeps = [ast.unparse(node) for node in ast.walk(worker)
                  if isinstance(node, ast.Await) and "sleep" in ast.unparse(node)]
        self.assertEqual(sleeps, ["await asyncio.sleep(SIMULATION_PASS_SECONDS)"], "the worker's pass is not the shared constant")
        server = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.assertIn("SIMULATION_PASS_SECONDS", server)


if __name__ == "__main__":
    unittest.main()
