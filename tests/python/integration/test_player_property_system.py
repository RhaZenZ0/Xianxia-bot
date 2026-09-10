import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.database import Database
from app.rules.game import World

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4}


class PlayerPropertySystemTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "property.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        for uid, name in ((1901, "Owner"), (1902, "Guest")):
            self.assertTrue(await seed_character(self.db,
                user_id=uid, discord_name=name.lower(), name=name,
                origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
                concept="property-system test", location="Greenriver Town", attributes=ATTRS,
                qi_max=20, vitality_max=20, created_game_minute=10,
            ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_the_property_columns_and_info_message_exist(self):
        with sqlite3.connect(self.path) as conn:
            abode_cols = {row[1] for row in conn.execute("PRAGMA table_info(cave_abodes)")}
            server_cols = {row[1] for row in conn.execute("PRAGMA table_info(server_config)")}
        self.assertTrue({
            "property_type", "storage_level", "herb_garden_level", "beast_pen_level", "merchant_level"
        }.issubset(abode_cols))
        self.assertIn("info_message_id", server_cols)


    async def test_info_guide_message_id_is_persistent(self):
        await self.db.set_server_channels(
            88, announcement_channel_id=1, event_scene_channel_id=2,
            home_scene_channel_id=3, log_channel_id=4, begin_channel_id=5,
            info_channel_id=6, exploration_channel_id=7,
        )
        await self.db.set_info_message_id(88, 123456)
        cfg = await self.db.get_server_config(88)
        self.assertEqual(cfg["info_message_id"], 123456)

    def test_one_home_is_founded_and_the_archetypes_stay_for_their_rows(self):
        # v0.30.1: a home is one place built up facility by facility. The
        # homestead is the only type founded; the cave abode (what a sect
        # assigns) and the five estate archetypes stay defined so rows that
        # carry them keep their label, and are marked not buildable.
        world = World(ROOT / "content" / "world.json")
        types = world.abode_system.get("property_types", {})
        self.assertEqual(set(types), {
            "homestead", "cave_abode", "alchemy_estate", "spirit_herb_estate",
            "spirit_beast_ranch", "merchant_pavilion", "clan_estate",
        })
        buildable = {key for key, defn in types.items() if defn.get("buildable", True)}
        self.assertEqual(buildable, {"homestead"})
        self.assertEqual(types["homestead"]["defaults"], {"cultivation": 1, "storage": 1})
        # Every other facility is built with an upgrade, so none may be a default.
        facilities = set(world.abode_system["facilities"])
        self.assertEqual(facilities - set(types["homestead"]["defaults"]), {
            "alchemy", "forge", "formation", "defense", "herb_garden", "beast_pen", "merchant",
        })

    def test_founding_asks_for_a_name_and_nothing_else(self):
        # services.py imports discord at module level, so the rule is held in
        # source: /abode establish sends only the name, the home-type list is
        # the content's buildable set, and the engine founds that one type and
        # refuses the rest (property_types_test.go).
        abode = (ROOT / "app" / "bot" / "commands" / "abode.py").read_text(encoding="utf-8")
        establish = abode[abode.index("async def abode_establish("):abode.index("async def abode_status(")]
        self.assertIn("async def abode_establish(interaction:discord.Interaction,name:str)->None:", establish)
        self.assertIn('{"name":name}', establish)
        self.assertNotIn("property_type", establish)
        self.assertNotIn("PLAYER_PROPERTY_TYPE_CHOICES", abode)
        services = (ROOT / "app" / "bot" / "services.py").read_text(encoding="utf-8")
        self.assertIn('if defn.get("buildable", True)', services[services.index("PLAYER_PROPERTY_HOME_TYPES"):])
        engine = (ROOT / "go_core" / "internal" / "game" / "property_storage_actions.go").read_text(encoding="utf-8")
        self.assertIn("propertyTypeBuildable(catalog, p.PropertyType)", engine)
        self.assertNotIn('p.PropertyType = "cave_abode"', engine)


if __name__ == "__main__":
    unittest.main()
