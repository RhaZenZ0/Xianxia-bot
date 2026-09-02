import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
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
        self.assertIn("Realm-role creation and synchronization will not work.", source)
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

    def test_dashboard_can_reuse_the_same_discord_setup_implementation(self):
        source = BOT.read_text(encoding="utf-8")
        self.assertIn("async def dashboard_discord_control", source)
        self.assertIn("await _run_complete_server_setup(guild, create_missing=True)", source)
        self.assertIn('action == "sync_commands"', source)
        self.assertIn('action == "bind_channels"', source)
        self.assertIn('action == "test_announcement"', source)
        self.assertIn("dashboard.discord.", source)

    def test_only_the_dashboard_path_can_provision_missing_discord_channels(self):
        """Channel/category creation is dashboard-owned: only the web GM dashboard's
        Full Setup/Repair action (dashboard_discord_control's "setup"/"repair" branch)
        opts into create_missing=True. The /admin Discord slash command's own setup
        action reuses the same helper but stays validate-and-bind-only, matching
        "Discord channel creation is dashboard-owned" - it must never pass
        create_missing=True itself.
        """
        source = BOT.read_text(encoding="utf-8")
        self.assertIn("guild.create_text_channel", source)
        self.assertIn("guild.create_category", source)
        self.assertIn("create_missing: bool = False", source)
        self.assertIn("Discord channel creation is dashboard-owned", source)
        self.assertIn("the bot will not provision channels", source)
        # The /admin slash command's call site must stay on the create_missing=False
        # default - i.e. call the helper with no create_missing kwarg at all.
        slash_command_marker = "Discord channel creation is dashboard-owned"
        slash_command_call = source.index(
            "await _run_complete_server_setup(guild)", source.index(slash_command_marker)
        )
        self.assertNotIn(
            "create_missing", source[slash_command_call:slash_command_call + len("await _run_complete_server_setup(guild)")]
        )


class AdminServerSetupDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_role_reconciliation_can_list_character_owners(self):
        self.assertEqual(SCHEMA_VERSION, 26)
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "server-setup.sqlite3")
            await db.init()
            for uid in (9303, 9301, 9302):
                created = await seed_character(db,
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
