"""The reader that tells a player what they have learned must actually run.

`DB.get_known_recipes` (v1.0.1) is the first Python reader `character_recipes`
has ever had. Its first version raised on every call - it did `dict(row)`
without setting `db.row_factory`, so a row came back as a bare tuple, `dict`
walked the first string instead, and the page died with *"dictionary update
sequence element #0 has length 19"*, 19 being the length of "Swift-Wind
Talisman".

**No source read would have caught it and no gate did.**
`test_authority_boundary` requires every state function to have a production
caller, and it had one; `test_a_recipe_tells_you_what_it_needs` requires the
page to call it, and it did. What neither asks is whether the call *works*, and
nothing else executed it - so the Discord playtest found it, on a leaf that had
passed for as long as the leaf had existed.

That is the argument for this file: a new reader gets a test that calls it
against a real database, not only a gate that says it is referenced.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_character
install_aiosqlite_shim()

from app.database import Database

ATTRS = {"body": 4, "agility": 3, "spirit": 5, "insight": 4, "will": 5, "presence": 2}


class KnownRecipesReadBack(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "recipes.sqlite3")
        await self.db.init()
        self.assertTrue(await seed_character(
            self.db, user_id=101, discord_name="scribe", name="Scribe", origin="Greenriver Town",
            path="Formation Adept", spiritual_root="Wind", concept="a talisman scribe",
            location="Greenriver Town", attributes=ATTRS, qi_max=30, vitality_max=30,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_a_cultivator_who_knows_nothing_reads_back_nothing(self):
        self.assertEqual(await self.db.get_known_recipes(101), [])

    async def test_every_learned_method_reads_back_with_its_columns(self):
        async with self.db._connect() as conn:
            await conn.execute(
                "INSERT INTO character_recipes(user_id,recipe,source,learned_game_minute) VALUES(?,?,?,?)",
                (101, "Swift-Wind Talisman", "slip", 120),
            )
            await conn.execute(
                "INSERT INTO character_recipes(user_id,recipe,source,learned_game_minute) VALUES(?,?,?,?)",
                (101, "Stone-Skin Talisman", "family_lesson", 40),
            )
            await conn.commit()

        known = await self.db.get_known_recipes(101)
        # Each row is a mapping with the three columns the status page reads.
        # The old version raised here rather than returning anything at all.
        self.assertEqual([entry["recipe"] for entry in known],
                         ["Swift-Wind Talisman", "Stone-Skin Talisman"],
                         "newest first, by learned_game_minute")
        self.assertEqual(known[0]["source"], "slip")
        self.assertEqual(int(known[1]["learned_game_minute"]), 40)

    async def test_one_cultivators_methods_are_not_anothers(self):
        self.assertTrue(await seed_character(
            self.db, user_id=202, discord_name="smith", name="Smith", origin="Greenriver Town",
            path="Body Refiner", spiritual_root="Earth", concept="a smith",
            location="Greenriver Town", attributes=ATTRS, qi_max=30, vitality_max=30,
        ))
        async with self.db._connect() as conn:
            await conn.execute(
                "INSERT INTO character_recipes(user_id,recipe,source) VALUES(?,?,?)",
                (202, "Recovery Pill", "slip"),
            )
            await conn.commit()
        self.assertEqual(await self.db.get_known_recipes(101), [])
        self.assertEqual([e["recipe"] for e in await self.db.get_known_recipes(202)], ["Recovery Pill"])


if __name__ == "__main__":
    unittest.main()
