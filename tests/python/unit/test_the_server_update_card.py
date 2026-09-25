"""The Server Update card says what it knows and nothing it does not (v1.4.0).

The card is drawn from two sources that can each be missing: the engine's
rows (a request, the last result, the watcher's heartbeat) and the bot's
release check (what is newest on the channel). Two answers would mislead a
GM and each is held here: an unreachable bot must never read as "up to
date", and a watcher nobody has heard from must never read as running -
because the button that asks for an update is only offered while one is.
"""

from __future__ import annotations

import ast
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

from app.dashboard.server import (  # noqa: E402
    UPDATE_WATCHER_STALE_SECONDS,
    AdminDashboardController,
    update_card_state,
)
from app.version import INSTALLED_VERSION  # noqa: E402

NOW = 1_800_000_000.0
RELEASE_OK = {"ok": True, "channel": "stable", "newest": "1.5.0", "update_available": True, "installed": INSTALLED_VERSION}


class WhatTheCardShows(unittest.TestCase):
    def test_an_unreachable_bot_is_unknown_never_up_to_date(self):
        state = update_card_state({}, {"ok": False, "error": "bot down"}, NOW)
        self.assertIsNone(state["newest_on_channel"])
        self.assertIsNone(state["update_available"])
        self.assertIn("bot down", state["release_error"])
        state = update_card_state({}, None, NOW)
        self.assertIsNone(state["update_available"], "no answer at all must not read as up to date")

    def test_a_reachable_bot_names_the_newest_release(self):
        state = update_card_state({}, RELEASE_OK, NOW)
        self.assertEqual(state["newest_on_channel"], "1.5.0")
        self.assertTrue(state["update_available"])
        self.assertEqual(state["installed_version"], INSTALLED_VERSION)
        self.assertIsNone(state["release_error"])

    def test_a_watcher_nobody_has_heard_from_is_not_running(self):
        self.assertFalse(update_card_state({}, RELEASE_OK, NOW)["watcher_running"])
        stale = {"update_watcher_heartbeat": {"at": NOW - UPDATE_WATCHER_STALE_SECONDS - 1}}
        self.assertFalse(update_card_state(stale, RELEASE_OK, NOW)["watcher_running"])
        fresh = {"update_watcher_heartbeat": {"at": NOW - 40}}
        state = update_card_state(fresh, RELEASE_OK, NOW)
        self.assertTrue(state["watcher_running"])
        self.assertEqual(state["watcher_seen_seconds_ago"], 40)

    def test_an_open_request_is_in_progress_and_a_closed_one_is_not(self):
        rows = {"update_request": {"status": "fetching", "nonce": "n", "requested_at": NOW}}
        self.assertTrue(update_card_state(rows, RELEASE_OK, NOW)["in_progress"])
        rows = {"update_request": {"status": "done", "nonce": "n"}, "update_result": {"status": "done", "installed_version": "1.5.0"}}
        state = update_card_state(rows, RELEASE_OK, NOW)
        self.assertFalse(state["in_progress"])
        self.assertEqual(state["result"]["installed_version"], "1.5.0")

    def test_a_broken_row_reads_as_absent(self):
        rows = {"update_request": "not a dict", "update_watcher_heartbeat": {"at": "soon"}}
        state = update_card_state(rows, RELEASE_OK, NOW)
        self.assertIsNone(state["request"])
        self.assertFalse(state["watcher_running"])


class TheButtonAndItsWire(unittest.TestCase):
    def test_the_request_forwards_to_the_engines_own_action(self):
        self.assertEqual(AdminDashboardController.ACTION_MAP["server.request_update"], "admin.server.request_update")

    def test_the_card_offers_the_button_only_while_the_watcher_runs(self):
        js = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("server.request_update", js)
        self.assertIn("updCanAsk=!upd.in_progress&&upd.watcher_running", js,
                      "the button must be gated on a running watcher and no open request")

    def test_the_dashboard_installs_nothing_itself(self):
        """The dashboard writes a request; the NAS runs it. No shell, no
        Docker, no updater is reached from this process."""
        source = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse({"subprocess", "shlex"} & imported, "the dashboard must not shell out")
        self.assertNotIn("update.sh", source)

    def test_the_api_composes_rows_and_release_into_one_block(self):
        """`/api/admin` hands the card both halves; the reader must be told
        when the bot could not be asked rather than shown a stale answer."""
        source = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        handler = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_handle")
        # ast.unparse quotes with ', so the check reads the call's arguments
        # rather than a spelling (rc.52's lesson, v1.0.16's drill).
        calls = {ast.unparse(n.func): [ast.unparse(a) for a in n.args] for n in ast.walk(handler) if isinstance(n, ast.Call)}
        self.assertIn("update_card_state", calls)
        release_reads = [args for func, args in calls.items() if func.endswith("run_readonly") and args and args[0] == "'release'"]
        self.assertTrue(release_reads, "the GET handler never asks the bot for its release check")


class TheBotAnswersItsReleaseCheck(unittest.TestCase):
    def test_the_control_action_is_a_read_of_the_health_entry(self):
        source = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_dashboard_discord_control")
        compares = {ast.unparse(n.comparators[0]) for n in ast.walk(fn) if isinstance(n, ast.Compare) and ast.unparse(n.left) == "action"}
        self.assertIn("'release'", compares)
        reads = [ast.unparse(n.args[0]) for n in ast.walk(fn) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("checks.get")]
        self.assertIn("'release_channel'", reads)

    def test_the_one_comparison_stays_in_release_channel(self):
        """Neither the dashboard nor the card re-derives "newer": the bot's
        check_for_release is the only caller of newer_than_installed."""
        callers = []
        for path in (PROJECT_ROOT / "app").rglob("*.py"):
            if "newer_than_installed(" in path.read_text(encoding="utf-8") and path.name != "release_channel.py":
                callers.append(path.relative_to(PROJECT_ROOT).as_posix())
        self.assertEqual(callers, ["app/bot/bot.py"])


if __name__ == "__main__":
    unittest.main()
