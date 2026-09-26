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
import asyncio
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
from app.ops.release_channel import (  # noqa: E402
    RELEASE_CARD_MAX_AGE_SECONDS,
    read_release_check,
    release_check_is_stale,
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

    def test_the_card_read_goes_through_the_refresh(self):
        """v1.7.3: the control action hands the bot's own check to
        read_release_check, so a stale stored answer is refreshed rather than
        shown - and honours UPDATE_CHECK_ENABLED."""
        source = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_dashboard_discord_control")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and ast.unparse(n.func) == "read_release_check"]
        self.assertEqual(len(calls), 1, "the release action no longer refreshes a stale check")
        args = [ast.unparse(a) for a in calls[0].args]
        self.assertIn("self.check_for_release", args)
        self.assertIn("self.release_check_lock", args)
        self.assertEqual({k.arg: ast.unparse(k.value) for k in calls[0].keywords}.get("enabled"), "SETTINGS.update_check_enabled")

    def test_the_one_comparison_stays_in_release_channel(self):
        """Neither the dashboard nor the card re-derives "newer": the bot's
        check_for_release is the only caller of newer_than_installed."""
        callers = []
        for path in (PROJECT_ROOT / "app").rglob("*.py"):
            if "newer_than_installed(" in path.read_text(encoding="utf-8") and path.name != "release_channel.py":
                callers.append(path.relative_to(PROJECT_ROOT).as_posix())
        self.assertEqual(callers, ["app/bot/bot.py"])


class TheCardAsksAgainWhenItsAnswerIsOld(unittest.TestCase):
    """Reported from the dashboard: v1.7.2 was live and the card still said
    v1.7.1 was the newest, because it showed the bot's daily check and the
    last one had run that morning."""

    def setUp(self):
        self.now = NOW
        self.store: dict = {}
        self.checks = 0

    def read(self):
        return self.store.get("release_channel")

    async def check(self):
        self.checks += 1
        await asyncio.sleep(0)
        self.store["release_channel"] = {"ok": True, "newest": "1.7.2", "checked_at": self.now}

    def run_read(self, *, enabled=True, concurrent=1):
        lock = asyncio.Lock()

        async def go():
            return await asyncio.gather(*(
                read_release_check(self.read, self.check, lock, lambda: self.now, enabled=enabled)
                for _ in range(concurrent)
            ))
        return asyncio.run(go())

    def test_a_stale_answer_is_refreshed_before_it_is_shown(self):
        self.store["release_channel"] = {"ok": True, "newest": "1.7.1", "checked_at": NOW - RELEASE_CARD_MAX_AGE_SECONDS - 1}
        (result,) = self.run_read()
        self.assertEqual(result["newest"], "1.7.2", "the card showed the morning's answer after a release was published")
        self.assertEqual(self.checks, 1)

    def test_a_fresh_answer_is_shown_without_asking(self):
        self.store["release_channel"] = {"ok": True, "newest": "1.7.1", "checked_at": NOW - 60}
        (result,) = self.run_read()
        self.assertEqual(result["newest"], "1.7.1")
        self.assertEqual(self.checks, 0)

    def test_many_loads_at_once_cost_one_check(self):
        results = self.run_read(concurrent=6)
        self.assertEqual(self.checks, 1, "concurrent card loads each asked GitHub")
        self.assertTrue(all(r["newest"] == "1.7.2" for r in results))

    def test_a_switched_off_check_is_never_asked(self):
        results = self.run_read(enabled=False)
        self.assertEqual(self.checks, 0)
        self.assertEqual(results, [{}])

    def test_staleness_reads_the_timestamp_honestly(self):
        self.assertTrue(release_check_is_stale(None, NOW))
        self.assertTrue(release_check_is_stale({"ok": True}, NOW), "an absent timestamp is stale, not checked at 0")
        self.assertTrue(release_check_is_stale({"checked_at": "soon"}, NOW))
        self.assertFalse(release_check_is_stale({"ok": False, "checked_at": NOW - 5}, NOW),
                         "a failed check is still an answer; GitHub is not asked again on every load")
        self.assertTrue(release_check_is_stale({"checked_at": NOW - RELEASE_CARD_MAX_AGE_SECONDS}, NOW))

    def test_the_card_says_how_old_its_answer_is(self):
        state = update_card_state({}, {**RELEASE_OK, "checked_at": NOW - 125}, NOW)
        self.assertEqual(state["checked_seconds_ago"], 125)
        self.assertIsNone(update_card_state({}, RELEASE_OK, NOW)["checked_seconds_ago"],
                          "an answer with no timestamp must not read as checked just now")
        js = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("upd.checked_seconds_ago", js)


if __name__ == "__main__":
    unittest.main()
