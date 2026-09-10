"""v0.32.0 Hardened II gate: moderation from Discord, audited; backups bounded.

The roadmap's three checks for the milestone, as tests:

- every "GM needs it live" dashboard op has a Discord registration, so an
  outage of the dashboard container never leaves a GM unable to hold a
  player, end a stuck scene or move someone out of a broken location;
- every moderation command in Discord audits, and the engine action behind
  it writes actor, target, reason and expiry, so `admin.audit.undo_last`
  covers a mute from Discord exactly as it covers one from the dashboard;
- the Go engine owns expiry (the tick clears it, the check reads it) and
  the backup policy (retention, cap, sealing) - asserted here by naming the
  Go tests and the wiring, not by re-implementing either in Python.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

BOT_ADMIN = PROJECT_ROOT / "app" / "bot" / "admin"
DASHBOARD_SERVER = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
GO_GAME = PROJECT_ROOT / "go_core" / "internal" / "game"
GO_SERVER = PROJECT_ROOT / "go_core" / "internal" / "server"
GO_SIM = PROJECT_ROOT / "go_core" / "internal" / "simulation"

# The dashboard ops a GM needs from Discord while the dashboard is down.
# Everything else on the dashboard is either an edit that can wait or a
# bulk/world change that deserves the console's confirm dialog.
LIVE_GM_OPS = (
    "admin.player.teleport",
    "admin.player.revive",
    "admin.player.clear_battle",
    "admin.player.force_end_scene",
    "admin.player.set_moderation",
)

MODERATION_VERBS = ("mute", "unmute", "freeze", "unfreeze", "ban", "unban")


def _admin_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(BOT_ADMIN.glob("*.py"))}


def _handler_bodies(source: str) -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(source)
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}


def _calls(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            fn = child.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


class LiveGMOpsHaveDiscordRegistrations(unittest.TestCase):
    def test_every_live_op_is_a_dashboard_op(self):
        for op in LIVE_GM_OPS:
            self.assertIn(f'"{op}"', DASHBOARD_SERVER, f"{op} is no longer a dashboard op; drop it from LIVE_GM_OPS or restore it")

    def test_every_live_op_is_reachable_from_a_discord_command(self):
        sources = "\n".join(_admin_sources().values())
        for op in LIVE_GM_OPS:
            self.assertIn(f'"{op}"', sources, f"{op} has no Discord registration under app/bot/admin")

    def test_force_end_scene_and_the_moderation_verbs_are_player_group_commands(self):
        source = _admin_sources()["inspect_sim.py"]
        names = set(re.findall(r'registered_group_command\(admin_player_group, name="([a-z_]+)"', source))
        for expected in ("forceendscene", *MODERATION_VERBS):
            self.assertIn(expected, names)


class ModerationCommandsAudit(unittest.TestCase):
    def test_each_moderation_verb_reaches_the_one_engine_action_and_audits(self):
        source = _admin_sources()["inspect_sim.py"]
        handlers = _handler_bodies(source)
        shared = handlers["_moderate"]
        self.assertIn("audit_admin", _calls(shared))
        self.assertIn('"admin.player.set_moderation"', ast.get_source_segment(source, shared))
        for verb in MODERATION_VERBS:
            handler = handlers[f"admin_{verb}"]
            self.assertIn("_moderate", _calls(handler), f"admin_{verb} does not go through _moderate")
            self.assertIn("require_admin", _calls(shared))

    def test_force_end_scene_audits(self):
        source = _admin_sources()["inspect_sim.py"]
        handler = _handler_bodies(source)["admin_forceendscene"]
        self.assertIn("audit_admin", _calls(handler))
        self.assertIn("require_admin", _calls(handler))

    def test_a_duration_is_parsed_strictly(self):
        from app.rules.moderation import parse_duration_seconds

        self.assertEqual(parse_duration_seconds(""), 0)
        self.assertEqual(parse_duration_seconds("forever"), 0)
        self.assertEqual(parse_duration_seconds("30m"), 1800)
        self.assertEqual(parse_duration_seconds("2h"), 7200)
        self.assertEqual(parse_duration_seconds("1h30m"), 5400)
        self.assertEqual(parse_duration_seconds("1d"), 86400)
        self.assertEqual(parse_duration_seconds("1w"), 7 * 86400)
        for bad in ("soon", "2", "2 hours", "1y", "400d"):
            with self.assertRaises(ValueError, msg=bad):
                parse_duration_seconds(bad)

    def test_the_summary_reads_a_lapsed_flag_as_over(self):
        from app.rules.moderation import moderation_summary

        self.assertEqual(moderation_summary({}), "none")
        self.assertIn("banned", moderation_summary({"is_banned": 1}))
        self.assertIn("until lifted", moderation_summary({"is_muted": 1, "muted_until": 0}))
        self.assertEqual(moderation_summary({"is_muted": 1, "muted_until": 1.0}), "none")
        self.assertIn("<t:", moderation_summary({"is_frozen": 1, "frozen_until": 4102444800.0, "moderation_reason": "hold"}))


class TheEngineOwnsExpiryAndAudit(unittest.TestCase):
    def test_set_moderation_audits_actor_target_reason_and_expiry(self):
        source = (GO_GAME / "moderation.go").read_text(encoding="utf-8")
        self.assertIn('auditAdmin(conn, adminUserID, "admin.player.set_moderation", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"]))', source)
        for key in ("is_muted", "muted_until", "is_frozen", "frozen_until", "is_banned", "moderation_reason"):
            self.assertIn(f'"{key}":', source, f"the audit snapshot lacks {key}")

    def test_undo_restores_the_expiry_and_the_ban(self):
        source = (GO_GAME / "admin_undo.go").read_text(encoding="utf-8")
        start = source.index('"admin.player.set_moderation": func(')
        body = source[start:start + 900]
        self.assertIn("muted_until=?", body)
        self.assertIn("frozen_until=?", body)
        self.assertIn("is_banned=?", body)

    def test_the_tick_expires_moderations_and_the_check_reads_expiry(self):
        maintenance = (GO_SIM / "advanced_maintenance.go").read_text(encoding="utf-8")
        self.assertIn("game.ExpireDueModerations(conn, nowFloat())", maintenance)
        self.assertIn('counts["moderations_expired"]', maintenance)
        moderation = (GO_GAME / "moderation.go").read_text(encoding="utf-8")
        self.assertIn("func moderationActive(", moderation)
        self.assertIn('moderationActive(row["is_frozen"], row["frozen_until"], now)', moderation)

    def test_the_go_tests_for_expiry_and_retention_exist(self):
        game_tests = "\n".join(p.read_text(encoding="utf-8") for p in GO_GAME.glob("*_test.go"))
        for name in ("TestExpireDueModerationsClearsOnlyLapsedFlags", "TestModerationLapsedMuteNoLongerBlocks",
                     "TestModerationBannedPlayerBlockedFromEverything", "TestAdminUndoLastRestoresModerationExpiryAndBan"):
            self.assertIn(f"func {name}(", game_tests)
        server_tests = "\n".join(p.read_text(encoding="utf-8") for p in GO_SERVER.glob("*_test.go"))
        for name in ("TestRetainBackupsKeepsDailyWholeThenOnePerWeek", "TestSizeCapShedsOldestButNeverTheNewest",
                     "TestEncryptedBackupIsSealedListedAndRestorable", "TestDbBackupsPrunesAfterEachCreate"):
            self.assertIn(f"func {name}(", server_tests)

    def test_schema_34_carries_the_columns(self):
        from app.database.core import SCHEMA_MIGRATIONS, SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, 34)
        version, name, statements = SCHEMA_MIGRATIONS[-1]
        self.assertEqual((version, name), (34, "player_moderation_expiry"))
        joined = "\n".join(statements)
        for column in ("muted_until", "frozen_until", "is_banned"):
            self.assertIn(column, joined)


class BackupsAreBoundedAndConfigured(unittest.TestCase):
    def test_the_engine_reads_the_four_keys_and_compose_passes_them(self):
        backups = (GO_SERVER / "backups.go").read_text(encoding="utf-8")
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        docs = (PROJECT_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
        for key in ("XIANXIA_BACKUP_KEEP_DAILY", "XIANXIA_BACKUP_KEEP_WEEKLY", "XIANXIA_BACKUP_MAX_MB", "XIANXIA_BACKUP_KEY"):
            self.assertIn(f'"{key}"', backups)
            self.assertIn(f"{key}:", compose)
            self.assertIn(key, env)
            self.assertIn(f"`{key}`", docs)
        self.assertIn("XIANXIA_OFFBOX_BACKUP_DIR", env)
        self.assertIn("`XIANXIA_OFFBOX_BACKUP_DIR`", docs)

    def test_pruning_runs_after_every_backup_and_restore_opens_sealed_files(self):
        server = (GO_SERVER / "server.go").read_text(encoding="utf-8")
        self.assertIn("pruneBackups(backupDir, s.backups)", server)
        self.assertIn("backupcrypt.EncryptFile(destination, sealed, s.backups.Key)", server)
        self.assertIn("backupcrypt.DecryptFile(sourcePath, opened, s.backups.Key)", server)
        self.assertIn("backup_key_required", server)

    def test_update_sh_copies_off_box_and_can_roll_back_from_a_sealed_backup(self):
        update = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        self.assertIn("copy_backup_offbox()", update)
        self.assertIn("XIANXIA_OFFBOX_BACKUP_DIR", update)
        self.assertLess(update.index("create_database_backup\n"), update.index("copy_backup_offbox\n"))
        restore = update[update.index("restore_database() {"):update.index("rollback_install() {")]
        self.assertIn("*.enc)", restore)
        self.assertIn("decrypt-backup", restore)
        main = (PROJECT_ROOT / "go_core" / "cmd" / "xianxia-core" / "main.go").read_text(encoding="utf-8")
        self.assertIn('os.Args[1] == "decrypt-backup"', main)


if __name__ == "__main__":
    unittest.main()
