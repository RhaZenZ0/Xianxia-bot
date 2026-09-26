"""One read-only market-stalls channel per world (v1.7.0), and the wires that
keep a stall's card on it.

The channel is the `world_event_channels` shape, rule for rule, and each rule
is held here for the reason its own release recorded: Setup creates and the
slash path only binds; an existing channel is moved, not merely rebound
(rc.51, rc.59); it is gated by the access role, not the presence role (rc.52);
the bot allows itself before anybody is denied (rc.52); and the read-only
overwrite is **merged**, never replaced (v1.0.11) - which is also why this
does not go through `ensure_realm_hub_overwrites`, which grants the role the
full member set, send included, so a read-only channel would flip back and
forth on every Repair.

The card half is held by where each call sits: every stall command refreshes
the card only after the engine agreed, the tick refreshes all of them after it
has run (that is when the town buys), and a reset or an erasure reads the card
before the engine sweeps the row that names it (v1.0.8).
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT, code_only, install_aiosqlite_shim

install_aiosqlite_shim()

CHANNELS = (PROJECT_ROOT / "app" / "bot" / "channels.py").read_text(encoding="utf-8")
SETUP = (PROJECT_ROOT / "app" / "bot" / "admin" / "server_setup.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
HUBS = (PROJECT_ROOT / "app" / "rules" / "realm_hubs.py").read_text(encoding="utf-8")
ECONOMY = (PROJECT_ROOT / "app" / "bot" / "commands" / "economy.py").read_text(encoding="utf-8")
CHARACTER = (PROJECT_ROOT / "app" / "bot" / "commands" / "character.py").read_text(encoding="utf-8")
WORLD_OPS = (PROJECT_ROOT / "app" / "bot" / "admin" / "world_ops.py").read_text(encoding="utf-8")
BOT = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")


def _node(source: str, name: str):
    return next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)


def _body(source: str, name: str) -> str:
    return ast.get_source_segment(source, _node(source, name)) or ""


def _code(source: str, name: str) -> str:
    return code_only(_body(source, name))


def _calls(source: str, name: str) -> list[ast.Call]:
    """In source order: `ast.walk` is breadth-first, and which call comes first
    is the whole of one assertion below."""
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


class TheRosterCarriesOneMarketPerWorld(unittest.TestCase):
    def test_every_world_names_its_own_stalls_channel(self):
        hubs = _realm_hubs()
        self.assertEqual(len(hubs), 4, "the four worlds")
        names = set()
        for world, hub in hubs.items():
            with self.subTest(world=world):
                name = str(hub.get("stalls_channel_name") or "")
                self.assertRegex(name, r"^[a-z0-9-]+$", f"{world} has no usable stalls channel name")
                self.assertNotIn(name, {hub["channel_name"], hub["events_channel_name"]})
                self.assertTrue(str(hub.get("stalls_topic") or "").strip(), f"{world} has no stalls topic")
                names.add(name)
        self.assertEqual(len(names), 4, f"two worlds share a channel name: {sorted(names)}")

    def test_the_provisioner_reads_the_roster(self):
        self.assertIn('hub["stalls_channel_name"]', _code(CHANNELS, "ensure_stall_channels"))


class TheChannelsAreDashboardOwned(unittest.TestCase):
    def test_setup_creates_and_the_slash_path_only_binds(self):
        complete = _body(SETUP, "_run_complete_server_setup")
        self.assertIn("ensure_stall_channels(guild, category_name=SERVER_STALL_CATEGORY, create_missing=create_missing)", complete)
        realmhubs = _body(SETUP, "admin_realm_hubs")
        self.assertIn("ensure_stall_channels(guild, category_name=SERVER_STALL_CATEGORY)", realmhubs)
        self.assertNotIn("create_missing=True", realmhubs)

    def test_an_existing_channel_is_moved_not_merely_rebound(self):
        ensure = _code(CHANNELS, "ensure_stall_channels")
        self.assertIn("channel.category_id != category.id", ensure)
        self.assertIn("await channel.edit(category=category", ensure)

    def test_they_are_gated_by_the_access_role(self):
        ensure = _code(CHANNELS, "ensure_stall_channels")
        self.assertIn("_ensure_realm_access_roles(guild)", ensure)
        self.assertNotIn("_ensure_realm_presence_roles", ensure)


class TheChannelIsReadOnlyAndStaysSo(unittest.TestCase):
    def test_every_overwrite_is_merged(self):
        """`set_permissions(who, **perms)` replaces an overwrite; a direct call
        here would drop whatever the channel already carried (v1.0.11), and the
        member-set helper would hand the role `send_messages` back on every
        Repair."""
        names = [_call_name(call) for call in _calls(CHANNELS, "ensure_stall_channels")]
        self.assertNotIn("set_permissions", names)
        self.assertNotIn("ensure_realm_hub_overwrites", names)
        self.assertGreaterEqual(names.count("merge_overwrite"), 3, "the bot, the access role and @everyone")

    def test_the_bot_allows_itself_first_and_nobody_else_may_post(self):
        merges = [call for call in _calls(CHANNELS, "ensure_stall_channels") if _call_name(call) == "merge_overwrite"]
        self.assertTrue(merges, "the walk found no merge_overwrite call; the gate is broken, not the tree")
        whos = [ast.unparse(call.args[1]) for call in merges]
        self.assertEqual(whos[0], "guild.me", "the bot must allow itself before anybody is denied (rc.52)")
        sends = {ast.unparse(call.args[1]): next((ast.unparse(k.value) for k in call.keywords if k.arg == "send_messages"), None)
                 for call in merges}
        self.assertEqual(sends.get("guild.me"), "True")
        self.assertEqual(sends.get("role"), "False", "the access role could post in a read-only market")
        self.assertEqual(sends.get("guild.default_role"), "False")


class TheRowsAreForgottenAndTheChannelsDeleted(unittest.TestCase):
    def test_clear_and_teardown(self):
        clear = _body(CORE, "clear_discord_bindings")
        self.assertIn("DELETE FROM stall_channels WHERE guild_id=?", clear)
        self.assertIn("DELETE FROM stall_card_messages WHERE guild_id=?", clear)
        teardown = _body(SETUP, "teardown_managed_discord_layout")
        self.assertIn("DB.get_stall_channels(guild.id)", teardown)
        self.assertIn("SERVER_STALL_CATEGORY", teardown)

    def test_the_tables_are_in_the_readiness_probe(self):
        from app.database import OPERATIONAL_REQUIRED_TABLES
        for table in ("stall_channels", "stall_card_messages"):
            self.assertIn(table, OPERATIONAL_REQUIRED_TABLES)

    def test_the_writers_are_bookkeeping(self):
        gate = (PROJECT_ROOT / "tests" / "python" / "contracts" / "test_authority_boundary.py").read_text(encoding="utf-8")
        bookkeeping = gate[gate.index("BOOKKEEPING_METHODS = {"):]
        for method in ("set_stall_channel", "remember_stall_card", "forget_stall_card"):
            self.assertIn(f'"{method}"', bookkeeping)


class TheCardFollowsTheStall(unittest.TestCase):
    def test_every_stall_command_refreshes_after_the_engine_agreed(self):
        for handler, action in (("stall_open", "stall.open"), ("stall_list", "stall.list"),
                                ("stall_withdraw", "stall.withdraw"), ("stall_buy", "stall.buy"),
                                ("stall_close", "stall.close")):
            with self.subTest(handler=handler):
                body = _code(ECONOMY, handler)
                self.assertIn("refresh_stall(interaction.guild,", body, f"{handler} leaves the card stale")
                self.assertLess(body.index(f'authoritative_action("{action}"'), body.index("refresh_stall("))
        self.assertIn('refresh_stall(interaction.guild,int(result.get("seller_user_id")', _code(ECONOMY, "stall_buy"),
                      "a buy changes the seller's stall, not the buyer's")

    def test_the_tick_refreshes_every_card_after_it_ran(self):
        worker = _body(BOT, "event_expiry_worker")
        self.assertIn("sync_stalls(self.get_guild(SETTINGS.guild_id))", worker)
        self.assertLess(worker.index("SIM.run_due("), worker.index("sync_stalls("))

    def test_a_reset_and_an_erasure_read_the_card_before_the_sweep(self):
        for source, handler, action in ((CHARACTER, "reset", '"character.reset"'),
                                        (WORLD_OPS, "admin_erase", '"admin.player.erase"')):
            with self.subTest(handler=handler):
                try:
                    body = _code(source, handler)
                except StopIteration:
                    self.fail(f"{handler} is no longer where the gate looks")
                read, engine, down = body.index("stall_card_record("), body.index(action), body.index("take_down_card(")
                self.assertLess(read, engine, "the card is read after the sweep took the row that names it")
                self.assertLess(engine, down, "the card came down before the engine agreed")


if __name__ == "__main__":
    unittest.main()
