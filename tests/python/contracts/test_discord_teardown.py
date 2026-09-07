"""Teardown (v0.21.2): the dashboard action that deletes everything the bot
owns on Discord, and the bookkeeping write that forgets the ids.

What is promised, and held here:

1. The action needs a typed confirmation (`DELETE`), distinct from Fresh
   Start's `CLEAR` and Reset World's `RESET`, and it is audited.
2. It deletes only what a binding names - threads the database tracks, the
   bound base channels, the realm hubs, #bugs - and the two Xianxia
   categories only when nothing else is left in them. It recreates nothing.
3. The ids are cleared last, and clearing them touches no gameplay column:
   GM-authored channel message text survives, thread rows are left for their
   owners to recover from, and nothing outside the guild is affected.
4. The dashboard button cannot fire without the word typed into a box.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT, bot_function_source, install_aiosqlite_shim

install_aiosqlite_shim()

from app.database import Database  # noqa: E402

SETUP = (PROJECT_ROOT / "app" / "bot" / "admin" / "server_setup.py").read_text(encoding="utf-8")
APP_JS = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")


def _action_block(name: str) -> str:
    start = SETUP.index(f'    if action == "{name}":')
    end = SETUP.find("\n    if action == ", start + 1)
    if end == -1:
        end = SETUP.index("    raise ValueError(f\"Unsupported Discord dashboard action", start)
    return SETUP[start:end]


class TeardownActionContractTests(unittest.TestCase):
    def test_teardown_requires_its_own_typed_confirmation(self):
        block = _action_block("teardown")
        self.assertIn('!= "DELETE"', block)
        self.assertIn("confirmation required", block)
        # Each destructive action has a different word, so muscle memory from
        # one cannot fire another.
        self.assertIn('!= "CLEAR"', _action_block("fresh_start"))
        self.assertIn('!= "RESET"', _action_block("reset_world"))

    def test_teardown_is_audited_and_returns_the_new_status(self):
        block = _action_block("teardown")
        self.assertIn("await _audit_dashboard_discord(action, guild, after=result, reason=reason)", block)
        self.assertIn("await teardown_managed_discord_layout(guild)", block)
        self.assertIn("_dashboard_discord_snapshot(client, guild)", block)

    def test_the_helper_deletes_in_order_and_clears_last(self):
        source = bot_function_source("teardown_managed_discord_layout")
        threads = source.index("all_managed_thread_ids()")
        channels = source.index("_base_channel_bindings(cfg)")
        categories = source.index("guild.categories")
        clear = source.index("clear_discord_bindings(guild.id)")
        self.assertLess(threads, channels, "threads before channels (a deleted anchor takes its threads with it)")
        self.assertLess(channels, categories, "channels before categories")
        self.assertLess(categories, clear, "ids are forgotten only after the deletes")

    def test_the_helper_touches_only_what_a_binding_names(self):
        source = bot_function_source("teardown_managed_discord_layout")
        # Base channels come from the bindings, hubs from their table, bugs from config.
        self.assertIn("_base_channel_bindings(cfg).items()", source)
        self.assertIn("DB.get_realm_hub_channels(guild.id)", source)
        self.assertIn('cfg.get("bugs_channel_id")', source)
        # Never by name, never by category membership, never guild.text_channels.
        self.assertNotIn("guild.text_channels", source)
        self.assertNotIn("guild.channels", source)
        self.assertNotIn("BASE_CHANNEL_SPECS", source)

    def test_categories_are_deleted_only_when_empty(self):
        source = bot_function_source("teardown_managed_discord_layout")
        self.assertIn("remaining = [c for c in category.channels if c.id not in seen_ids]", source)
        self.assertIn("if remaining:", source)
        self.assertIn("categories_kept.append", source)

    def test_nothing_is_recreated(self):
        source = bot_function_source("teardown_managed_discord_layout")
        for forbidden in ("_run_complete_server_setup", "ensure_base_xianxia_channels", "ensure_realm_hub_channels",
                          "ensure_bugs_forum_channel", "create_text_channel", "create_category"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_permission_is_checked_before_anything_is_deleted(self):
        source = bot_function_source("teardown_managed_discord_layout")
        self.assertLess(source.index("manage_channels"), source.index("all_managed_thread_ids()"))

    def test_the_bookkeeping_write_is_allowlisted_not_a_gameplay_row(self):
        gate = (PROJECT_ROOT / "tests" / "python" / "contracts" / "test_authority_boundary.py").read_text(encoding="utf-8")
        self.assertIn('"clear_discord_bindings"', gate[gate.index("BOOKKEEPING_METHODS = {"):])
        # PLAYER_MUTATIONS has been empty since v0.23.0, so "not a gameplay row"
        # is now a stronger statement than it was: there are no gameplay rows.
        self.assertIn("PLAYER_MUTATIONS: dict[tuple[str, str, str], str] = {}", gate)
        self.assertNotIn("clear_discord_bindings", gate[gate.index("PLAYER_MUTATIONS"):gate.index("BOOKKEEPING_METHODS = {")])


class DashboardButtonTests(unittest.TestCase):
    def test_button_exists_in_the_danger_zone_with_a_typed_box(self):
        self.assertIn('id="teardownDiscord"', APP_JS)
        self.assertIn('id="teardownConfirm"', APP_JS)
        self.assertIn("Teardown", APP_JS)

    def test_button_refuses_without_the_word(self):
        handler = re.search(r"teardownDiscord\.onclick=\(\)=>\{(.*?)\};\n", APP_JS, re.S)
        self.assertIsNotNone(handler)
        body = handler.group(1)
        self.assertIn("!=='DELETE'", body)
        self.assertLess(body.index("!=='DELETE'"), body.index("run('teardown'"), "the word is checked before the request")
        self.assertIn("confirm:typed", body)

    def test_copy_says_what_is_not_touched(self):
        section = APP_JS[APP_JS.index("Teardown</h3>"):APP_JS.index("Reset World</h3>")]
        for phrase in ("Nothing is recreated", "the database is not reset", "RP_CHANNEL_IDS", "Full Setup"):
            self.assertIn(phrase, section, phrase)


class ClearBindingsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_forgets_ids_keeps_text_and_other_guilds(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "teardown.sqlite3")
            await db.init()
            await db.set_server_channels(
                1, announcement_channel_id=11, event_scene_channel_id=12, home_scene_channel_id=13,
                log_channel_id=14, begin_channel_id=15, info_channel_id=16, exploration_channel_id=17,
            )
            await db.set_info_message_id(1, 160)
            await db.set_bugs_channel_id(1, 18)
            await db.set_realm_hub_channel(guild_id=1, world_name="Mortal Realm", location="Greenriver Town", channel_id=21, category_id=2)
            await db.set_realm_hub_channel(guild_id=1, world_name="Spirit Realm", location="Jade City", channel_id=22, category_id=2)
            await db.set_channel_message(1, "begin-here", content="Welcome, cultivator. (GM wrote this)", message_id=500)
            await db.set_channel_message(1, "bot-logs", content="", message_id=None)
            # Another guild, untouched.
            await db.set_server_channels(2, announcement_channel_id=91, event_scene_channel_id=92)
            await db.set_realm_hub_channel(guild_id=2, world_name="Mortal Realm", location="Greenriver Town", channel_id=93, category_id=None)

            counts = await db.clear_discord_bindings(1)

            self.assertEqual(counts, {"server_config": 1, "realm_hubs": 2, "channel_messages": 1})
            cfg = await db.get_server_config(1)
            for column in ("announcement_channel_id", "event_scene_channel_id", "home_scene_channel_id", "log_channel_id",
                           "begin_channel_id", "info_channel_id", "exploration_channel_id", "info_message_id", "bugs_channel_id"):
                self.assertIsNone(cfg.get(column), column)
            self.assertEqual(await db.get_realm_hub_channels(1), [])
            self.assertIsNone(await db.get_realm_hub_by_channel(1, 21))
            # GM-authored text survives; only the posted message id is forgotten.
            rows = await db.get_channel_messages(1)
            self.assertEqual(rows["begin-here"]["content"], "Welcome, cultivator. (GM wrote this)")
            self.assertIsNone(rows["begin-here"]["message_id"])
            # The other guild is exactly as it was.
            other = await db.get_server_config(2)
            self.assertEqual(other["announcement_channel_id"], 91)
            self.assertEqual(len(await db.get_realm_hub_channels(2)), 1)

    async def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "teardown.sqlite3")
            await db.init()
            self.assertEqual(await db.clear_discord_bindings(7), {"server_config": 0, "realm_hubs": 0, "channel_messages": 0})
            await db.set_server_channels(7, announcement_channel_id=1, event_scene_channel_id=2)
            first = await db.clear_discord_bindings(7)
            second = await db.clear_discord_bindings(7)
            self.assertEqual(first["server_config"], 1)
            self.assertEqual(second, {"server_config": 1, "realm_hubs": 0, "channel_messages": 0})  # the row exists; nothing else to forget


if __name__ == "__main__":
    unittest.main()
