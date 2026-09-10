"""v0.34.1: the playtest board in #playtest.

One post per hub page, pre-reacted with the three verdicts; the report
tallies what testers left and names them; the board stores message ids and
nothing else. The channel is a base channel like the others - created by
the dashboard's Setup/Repair, bound by the slash path.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
BOARD = (BOT / "admin" / "playtest_board.py").read_text(encoding="utf-8")
MESSAGES = (BOT / "admin" / "channel_messages.py").read_text(encoding="utf-8")
SETUP = (BOT / "admin" / "server_setup.py").read_text(encoding="utf-8")
HUBS = (BOT / "hubs.py").read_text(encoding="utf-8")
SURFACE = (BOT / "surface.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheChannelIsABaseChannel(unittest.TestCase):
    def test_it_is_specified_bound_and_saved_like_the_others(self):
        self.assertIn('"playtest":', MESSAGES.split("BASE_CHANNEL_SPECS = {")[1].split("}")[0])
        self.assertIn('"playtest": cfg.get("playtest_channel_id")', MESSAGES)
        self.assertIn('playtest_channel_id=_bound_id("playtest")', MESSAGES)
        self.assertIn('"playtest_channel_id": "playtest"', SETUP)
        self.assertIn("playtest_channel_id=COALESCE(excluded.playtest_channel_id,server_config.playtest_channel_id)", CORE)
        app_js = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("bind('bindPlaytest','Playtest board','playtest')", app_js)
        self.assertIn("playtest:bindPlaytest.value", app_js)

    def test_teardown_forgets_it(self):
        clear = _body(CORE, "clear_discord_bindings")
        self.assertIn("playtest_channel_id=NULL", clear)
        self.assertIn("DELETE FROM playtest_items WHERE guild_id=?", clear)


class TheBoardIsOnePostPerPage(unittest.TestCase):
    def test_pages_come_from_the_registered_hubs_not_an_import_of_surface(self):
        self.assertIn("from ..hubs import REGISTERED_HUBS, _leaf_actions", BOARD)
        self.assertNotIn("from ..surface", BOARD)
        self.assertIn("register_hubs(*_HUB_DEFINITIONS, _ADMIN_HUB_DEFINITION)", SURFACE)
        self.assertIn("REGISTERED_HUBS: list[HubDefinition] = []", HUBS)

    def test_every_post_is_pre_reacted_with_the_three_verdicts(self):
        self.assertIn('BOARD_REACTIONS = ("✅", "❌", "💡")', BOARD)
        post = _body(BOARD, "post_board")
        self.assertIn("for emoji in BOARD_REACTIONS:", post)
        self.assertIn("await message.add_reaction(emoji)", post)
        self.assertIn("DB.set_playtest_item(", post)

    def test_posting_is_idempotent(self):
        post = _body(BOARD, "post_board")
        self.assertIn("await channel.fetch_message(int(row[\"message_id\"]))", post)
        self.assertIn("kept += 1", post)

    def test_the_report_names_who_reacted_and_leaves_the_bot_out(self):
        report = _body(BOARD, "board_report")
        self.assertIn("if user.id != me", report)
        self.assertIn('entry["who"][emoji]', report)
        text = _body(BOARD, "format_report")
        self.assertIn("flagged", text)
        self.assertIn("https://discord.com/channels/", text)

    def test_the_command_is_admin_only_and_audited(self):
        command = _body(BOARD, "admin_playtest")
        self.assertIn("require_admin(interaction)", command)
        self.assertEqual(command.count("audit_admin("), 3)
        self.assertIn("interaction.response.defer(", command)


if __name__ == "__main__":
    unittest.main()
