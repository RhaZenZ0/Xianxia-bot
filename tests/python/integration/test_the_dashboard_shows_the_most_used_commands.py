"""The GM dashboard shows the most-used commands (v1.22.0).

The bot has counted every press since v1.3.5 (`command_usage`), and the only
place a GM could read the counts was Discord's `/admin server observability`.
Player Activity carries the card now. What is held:

- it reads the same table over the same window the bot prunes to, most used
  first, with the totals the card prints beside it;
- an unreadable count is `None` - "unknown" on the page - never an empty list
  a GM would read as "nobody plays", the `engine —` footer lesson (v1.0.8);
- the table is registered to the view that reads it, and the page draws it.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT, install_aiosqlite_shim
install_aiosqlite_shim()

from app.dashboard.contract import DASHBOARD_SYSTEM_TABLES  # noqa: E402
from app.dashboard.server import ReadOnlyDashboardStore  # noqa: E402
from app.database import Database  # noqa: E402
from app.database.core import COMMAND_USAGE_DAYS, _usage_day  # noqa: E402

APP_JS = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")


class TheCardReadsTheCounts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dashboard.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.store = ReadOnlyDashboardStore(self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _seed(self, path: str, day: str, presses: int) -> None:
        async with self.db._connect() as db:
            await db.execute("INSERT INTO command_usage(path, day, presses) VALUES(?,?,?)", (path, day, presses))
            await db.commit()

    async def test_most_used_first_inside_the_window_with_totals(self):
        today = _usage_day()
        yesterday = _usage_day(time.time() - 86400)
        long_ago = _usage_day(time.time() - (COMMAND_USAGE_DAYS + 5) * 86400)
        await self._seed("/explore", today, 7)
        await self._seed("/explore", yesterday, 5)
        await self._seed("/cultivate", today, 9)
        await self._seed("/hunt", long_ago, 500)  # pruned window: not counted
        data = await self.store.players()
        self.assertEqual(data["command_usage_days"], COMMAND_USAGE_DAYS)
        rows = [(r["path"], r["presses"], r["days_used"]) for r in data["command_usage"]]
        self.assertEqual(rows, [("/explore", 12, 2), ("/cultivate", 9, 1)])
        self.assertEqual(data["command_usage"][0]["last_day"], today)
        self.assertEqual(data["command_usage_total"], 21)
        self.assertEqual(data["command_usage_paths"], 2)

    async def test_nothing_recorded_is_an_empty_list(self):
        data = await self.store.players()
        self.assertEqual(data["command_usage"], [])
        self.assertEqual(data["command_usage_total"], 0)

    async def test_an_unreadable_count_is_unknown_not_empty(self):
        async with self.db._connect() as db:
            await db.execute("DROP TABLE command_usage")
            await db.commit()
        data = await self.store.players()
        self.assertIsNone(data["command_usage"], "an unreadable count was drawn as nobody pressing anything")
        self.assertIn("command_usage", data["command_usage_error"])
        # The rest of the page still answers.
        self.assertIn("players", data)


class ThePageDrawsIt(unittest.TestCase):
    def test_the_table_belongs_to_player_activity_and_the_page_draws_the_card(self):
        self.assertIn("command_usage", DASHBOARD_SYSTEM_TABLES["players"])
        self.assertIn("${commandUsageCard(d)}", APP_JS)
        card = APP_JS.split("function commandUsageCard(d){", 1)[1].split("\n", 1)[0]
        self.assertIn("d.command_usage==null", card, "an unreadable count is no longer told apart from an empty one")
        self.assertIn("unknown", card)


if __name__ == "__main__":
    unittest.main()
