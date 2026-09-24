"""`search_catalog` escapes LIKE's wildcards (v1.2.1).

The needle was `%{query}%` with no ESCAPE clause, so a GM typing `_` into an
Admin Console picker got every name in the table rather than a narrowed one,
and a `%` typed as text was a wildcard. Driven against a real database rather
than read off the source, because the spelling of an escape is exactly the
thing a source read gets wrong.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT, install_aiosqlite_shim, seed_content_tables

install_aiosqlite_shim()

from app.database import Database  # noqa: E402
from app.rules.game import World  # noqa: E402


class ASearchEscapesItsWildcards(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "search.sqlite3")
        await self.db.init()
        await seed_content_tables(self.db, World(PROJECT_ROOT / "content" / "world.json").data)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_a_plain_search_still_finds_its_name(self):
        self.assertIn("Gate Captain Yue Dong", await self.db.search_catalog("npc", "yue dong", 25))

    async def test_an_underscore_matches_nothing_rather_than_everything(self):
        self.assertEqual(await self.db.search_catalog("npc", "_", 25), [])
        self.assertEqual(await self.db.search_catalog("npc", "%", 25), [])

    async def test_a_backslash_is_text_too(self):
        self.assertEqual(await self.db.search_catalog("npc", "\\", 25), [])


if __name__ == "__main__":
    unittest.main()
