import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION

ROOT = PROJECT_ROOT
BOT = ROOT / "app" / "bot" / "main.py"
ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4}


class AdminServerSetupSourceTests(unittest.TestCase):
    def test_setup_action_has_six_installer_and_diagnostic_choices(self):
        source = BOT.read_text(encoding="utf-8")
        expected = (
            ('name="Setup Server", value="setup"',),
            ('name="Repair Server", value="repair"',),
            ('name="Check Permissions", value="permissions"',),
            ('name="Show Configuration", value="configuration"',),
            ('name="Sync Realm Roles", value="sync_roles"',),
            ('name="Rebuild Info Guide", value="rebuild_info"',),
        )
        for (needle,) in expected:
            self.assertIn(needle, source)
        start = source.index("SERVER_SETUP_CHOICES = [")
        end = source.index("]", start)
        self.assertEqual(source[start:end].count("app_commands.Choice("), 6)

    def test_permission_diagnostics_are_actionable(self):
        source = BOT.read_text(encoding="utf-8")
        self.assertIn("def _server_permission_report", source)
        self.assertIn("Manage Channels", source)
        self.assertIn("Manage Threads", source)
        self.assertIn("Create Private Threads", source)
        self.assertIn("Send Messages in Threads", source)
        self.assertIn("Manage Roles", source)
        self.assertIn("Realm-role syncing and automatic private/read-only permission repair will not work.", source)
        self.assertIn("Realm Role Hierarchy", source)
        self.assertIn("Move the bot's highest role above", source)

    def test_setup_supports_config_role_sync_and_info_rebuild(self):
        source = BOT.read_text(encoding="utf-8")
        self.assertIn("async def _server_configuration_report", source)
        self.assertIn("async def _sync_all_realm_access_roles", source)
        self.assertIn("await DB.list_character_user_ids()", source)
        self.assertIn("server.setup.sync_roles", source)
        self.assertIn("server.setup.rebuild_info", source)
        self.assertIn("ensure_xianxia_info_guide", source)
        self.assertIn("No database/world reset was performed", source)

    def test_realm_permissions_cannot_lock_the_bot_out(self):
        source = BOT.read_text(encoding="utf-8")
        self.assertIn("def _realm_channel_overwrites", source)
        self.assertIn("async def _apply_realm_channel_permissions", source)
        self.assertIn('create_kwargs["overwrites"] = _realm_channel_overwrites', source)
        start = source.index("async def _apply_realm_channel_permissions")
        end = source.index("async def ensure_realm_hub_channels", start)
        block = source[start:end]
        self.assertLess(block.index("guild.me,"), block.index("access_role,"))
        self.assertLess(block.index("access_role,"), block.index("guild.default_role,"))
        self.assertIn("Deny @everyone only after both positive overwrites", block)


class AdminServerSetupDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_role_reconciliation_can_list_character_owners(self):
        self.assertEqual(SCHEMA_VERSION, 17)
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "server-setup.sqlite3")
            await db.init()
            for uid in (9303, 9301, 9302):
                created = await db.create_character(
                    user_id=uid,
                    discord_name=f"u{uid}",
                    name=f"Cultivator {uid}",
                    origin="Greenriver Town",
                    path="Qi Refiner",
                    spiritual_root="Wood",
                    concept="setup role sync test",
                    location="Greenriver Town",
                    attributes=ATTRS,
                    qi_max=20,
                    vitality_max=20,
                    created_game_minute=0,
                )
                self.assertTrue(created)
            self.assertEqual(await db.list_character_user_ids(), [9301, 9302, 9303])


if __name__ == "__main__":
    unittest.main()
