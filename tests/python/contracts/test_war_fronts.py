"""One read-only war-front channel per world (v1.24.0), and the wires that keep
a war's card on it.

The channel is `ensure_stall_channels` rule for rule (v1.7.0), held here for
the reason each rule's own release recorded: Setup creates and the slash path
only binds; an existing channel is moved, not merely rebound (rc.51, rc.59);
it is gated by the access role, not the presence role (rc.52); the bot allows
itself before anybody is denied (rc.52); and the read-only overwrite is
merged, never replaced (v1.0.11). It has a category of its own, ⚔️ Sect Wars,
which teardown must be able to empty (rc.51).

The card half: `/war act` and a `/territory claim` that opened a war refresh
the card only after the engine agreed, and the tick refreshes every card after
it ran, because the world's own sieges are fought inside it. And the `/war act`
picker asks the engine which wars the act would take (`war.fronts`) rather
than restating who may fight beside whom.
"""
from __future__ import annotations

import ast
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT, code_only, install_aiosqlite_shim

install_aiosqlite_shim()

CHANNELS = (PROJECT_ROOT / "app" / "bot" / "channels.py").read_text(encoding="utf-8")
SETUP = (PROJECT_ROOT / "app" / "bot" / "admin" / "server_setup.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
HUBS = (PROJECT_ROOT / "app" / "rules" / "realm_hubs.py").read_text(encoding="utf-8")
TERRITORY = (PROJECT_ROOT / "app" / "bot" / "commands" / "territory.py").read_text(encoding="utf-8")
BOT = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _node(source: str, name: str):
    return next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)


def _body(source: str, name: str) -> str:
    return ast.get_source_segment(source, _node(source, name)) or ""


def _code(source: str, name: str) -> str:
    return code_only(_body(source, name))


def _calls(source: str, name: str) -> list[ast.Call]:
    calls = [n for n in ast.walk(_node(source, name)) if isinstance(n, ast.Call)]
    return sorted(calls, key=lambda call: (call.lineno, call.col_offset))


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _realm_hubs() -> dict:
    for node in ast.parse(HUBS).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "REALM_HUBS":
            return ast.literal_eval(node.value)
    raise AssertionError("REALM_HUBS is no longer a literal the gate can read")


class EveryWorldHasAWarFront(unittest.TestCase):
    def test_every_world_names_its_own_war_channel(self):
        hubs = _realm_hubs()
        self.assertEqual(len(hubs), 4, "the four worlds")
        names = set()
        for world, hub in hubs.items():
            with self.subTest(world=world):
                name = str(hub.get("war_channel_name") or "")
                self.assertRegex(name, r"^[a-z0-9-]+$", f"{world} has no usable war channel name")
                self.assertNotIn(name, {hub["channel_name"], hub["events_channel_name"], hub["stalls_channel_name"]})
                self.assertTrue(str(hub.get("war_topic") or "").strip(), f"{world} has no war topic")
                names.add(name)
        self.assertEqual(len(names), 4, f"two worlds share a war channel: {sorted(names)}")

    def test_the_provisioner_reads_the_roster(self):
        self.assertIn('hub["war_channel_name"]', _code(CHANNELS, "ensure_war_channels"))


class TheChannelsAreDashboardOwned(unittest.TestCase):
    def test_setup_creates_and_the_slash_path_only_binds(self):
        self.assertIn("ensure_war_channels(guild, category_name=SERVER_WAR_CATEGORY, create_missing=create_missing)",
                      _body(SETUP, "_run_complete_server_setup"))
        realmhubs = _body(SETUP, "admin_realm_hubs")
        self.assertIn("ensure_war_channels(guild, category_name=SERVER_WAR_CATEGORY)", realmhubs)
        self.assertNotIn("create_missing=True", realmhubs)

    def test_an_existing_channel_is_moved_and_gated_by_the_access_role(self):
        ensure = _code(CHANNELS, "ensure_war_channels")
        self.assertIn("channel.category_id != category.id", ensure)
        self.assertIn("await channel.edit(category=category", ensure)
        self.assertIn("_ensure_realm_access_roles(guild)", ensure)
        self.assertNotIn("_ensure_realm_presence_roles", ensure)

    def test_the_bot_allows_itself_first_and_nobody_else_may_post(self):
        calls = _calls(CHANNELS, "ensure_war_channels")
        names = [_call_name(call) for call in calls]
        self.assertNotIn("set_permissions", names)
        self.assertNotIn("ensure_realm_hub_overwrites", names)
        merges = [call for call in calls if _call_name(call) == "merge_overwrite"]
        self.assertGreaterEqual(len(merges), 3, "the bot, the access role and @everyone")
        whos = [ast.unparse(call.args[1]) for call in merges]
        self.assertEqual(whos[0], "guild.me", "the bot must allow itself before anybody is denied (rc.52)")
        sends = {ast.unparse(call.args[1]): next((ast.unparse(k.value) for k in call.keywords if k.arg == "send_messages"), None)
                 for call in merges}
        self.assertEqual(sends.get("guild.me"), "True")
        self.assertEqual(sends.get("role"), "False", "the access role could post on a read-only war front")
        self.assertEqual(sends.get("guild.default_role"), "False")


class TheRowsAreForgottenAndTheChannelsDeleted(unittest.TestCase):
    def test_clear_and_teardown(self):
        clear = _body(CORE, "clear_discord_bindings")
        self.assertIn("DELETE FROM war_channels WHERE guild_id=?", clear)
        self.assertIn("DELETE FROM war_card_messages WHERE guild_id=?", clear)
        teardown = _body(SETUP, "teardown_managed_discord_layout")
        self.assertIn("DB.get_war_channels(guild.id)", teardown)
        self.assertIn("SERVER_WAR_CATEGORY", teardown)

    def test_the_tables_are_in_the_readiness_probe(self):
        from app.database import OPERATIONAL_REQUIRED_TABLES
        for table in ("war_channels", "war_card_messages"):
            self.assertIn(table, OPERATIONAL_REQUIRED_TABLES)


class TheCardFollowsTheWar(unittest.TestCase):
    def test_an_act_and_a_declaration_refresh_after_the_engine_agreed(self):
        for handler, action in (("war_act", "war.act"), ("war_peace", "war.peace"), ("territory_claim", "territory.claim")):
            with self.subTest(handler=handler):
                body = _code(TERRITORY, handler)
                self.assertIn("refresh_war(interaction.guild,", body, f"{handler} leaves the card stale")
                self.assertLess(body.index(f'authoritative_action("{action}"'), body.index("refresh_war("))

    def test_the_tick_refreshes_every_card_after_it_ran(self):
        worker = _body(BOT, "event_expiry_worker")
        self.assertIn("sync_wars(self.get_guild(SETTINGS.guild_id)", worker)
        self.assertLess(worker.index("SIM.run_due("), worker.index("sync_wars("))


class ThePickerAsksTheEngine(unittest.TestCase):
    def test_the_picker_reads_the_fronts_and_never_the_relations(self):
        self.assertIn('"war.fronts"', _code(TERRITORY, "_war_fronts"))
        for picker in ("war_front_hub_options", "war_peace_hub_options"):
            self.assertIn("_war_fronts(interaction)", _code(TERRITORY, picker), f"{picker} does not ask the engine")
        self.assertNotIn("sect_relations", TERRITORY, "the panel is restating who may fight beside whom")

    def test_the_picker_offers_what_the_engine_answered(self):
        with patch.dict(os.environ, ENV):
            import importlib
            territory = importlib.import_module("app.bot.commands.territory")

        async def action(op, uid, payload):
            self.assertEqual(op, "war.fronts")
            return {"wars": [{"war_id": 7, "territory_name": "The Ford", "fights_for": "Holding Sect", "ally": True,
                              "attacker_key": "A", "defender_key": "Holding Sect", "siege_progress": 40,
                              "territory_defense": 52, "points_left": 16}]}

        with patch.object(territory.ENGINE, "action", action):
            options = asyncio.run(territory.war_front_hub_options(SimpleNamespace(user=SimpleNamespace(id=1)), ""))
        self.assertEqual([o.value for o in options], [7])
        self.assertIn("(ally)", options[0].label)
        self.assertIn("16 pts left", options[0].description)


class PeaceIsOfferedOnlyToABelligerent(unittest.TestCase):
    def test_the_peace_picker_leaves_out_the_wars_you_fight_as_an_ally(self):
        with patch.dict(os.environ, ENV):
            import importlib
            territory = importlib.import_module("app.bot.commands.territory")

        async def action(op, uid, payload):
            return {"wars": [
                {"war_id": 7, "territory_name": "The Ford", "fights_for": "Mine", "ally": False,
                 "attacker_key": "Mine", "defender_key": "Theirs"},
                {"war_id": 8, "territory_name": "The Pass", "fights_for": "Friend", "ally": True,
                 "attacker_key": "Friend", "defender_key": "Theirs"}]}

        with patch.object(territory.ENGINE, "action", action):
            peace = asyncio.run(territory.war_peace_hub_options(SimpleNamespace(user=SimpleNamespace(id=1)), ""))
            fight = asyncio.run(territory.war_front_hub_options(SimpleNamespace(user=SimpleNamespace(id=1)), ""))
        self.assertEqual([o.value for o in fight], [7, 8])
        self.assertEqual([o.value for o in peace], [7], "an ally has no standing at the table")

    def test_the_terms_are_printed_as_the_engine_made_them(self):
        with patch.dict(os.environ, ENV):
            import importlib
            territory = importlib.import_module("app.bot.commands.territory")
        text = territory.war_peace_text({"war_id": 3, "resolution": "ceded", "attacker_key": "A", "defender_key": "D",
                                         "territory_name": "The Ford", "siege_progress": 64, "cost": 100, "sued_by": "D"})
        for needle in ("**D** cedes **The Ford** to **A**", "**64%**", "**100**"):
            self.assertIn(needle, text)


class TheReplySaysWhatTheEngineDid(unittest.TestCase):
    def test_points_victory_and_promotion_are_printed(self):
        with patch.dict(os.environ, ENV):
            import importlib
            territory = importlib.import_module("app.bot.commands.territory")
        text = territory.war_act_text(3, "Repel", {
            "fights_for": "Holding Sect", "ally": True, "ally_joined": True, "points": 8, "territory_defense": 52,
            "status": "resolved", "victory_points": 120, "victors_paid": 2, "promoted": "Inner Disciple",
            "operations": {"siege_progress": 0, "winner_key": "Holding Sect", "resolution": "defender_holds"}})
        for needle in ("as an ally", "+**8**", "+**120**", "Inner Disciple", "Holding Sect", "walls **52**"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
