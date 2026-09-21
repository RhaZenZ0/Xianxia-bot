"""One events channel per world (v1.0.0-rc.52), and the global feed underneath it.

`world-events` carried all four worlds: a Demon Invasion in the Celestial World
and a caravan over the bank in a Mortal village landed in one feed, in front of
everybody, whatever they could reach - while the capitals have been split per
world since schema 4 and the auction floors since schema 35.

The data was there the whole time. `world_events.location` is NOT NULL on every
row and `_event_scene_location` has existed since the event scenes did; both
announcement writers already called it, about fifty lines *after* they had
already posted. This holds that they call it first, that the routing is by the
place, and - the expensive half - that a place the catalogue does not carry
falls back to the global feed rather than being filed as Mortal World news.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

CHANNELS = (PROJECT_ROOT / "app" / "bot" / "channels.py").read_text(encoding="utf-8")
EVENT_SCENE = (PROJECT_ROOT / "app" / "bot" / "ui" / "event_scene.py").read_text(encoding="utf-8")
SETUP = (PROJECT_ROOT / "app" / "bot" / "admin" / "server_setup.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
HUBS = (PROJECT_ROOT / "app" / "rules" / "realm_hubs.py").read_text(encoding="utf-8")


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node) or ""


def _code(source: str, name: str) -> str:
    """The function's statements without its docstring.

    `world_of_location`'s docstring quotes the very default it exists to refuse,
    so a whole-body search for `or "Mortal World"` matched the explanation and
    the gate failed on correct code. A gate that cannot tell prose from code is
    the rc.47 finding and the rc.49 one; this is the same shape a third time.
    """
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)
    statements = node.body[1:] if ast.get_docstring(node) is not None else node.body
    return "\n".join(ast.get_source_segment(source, stmt) or "" for stmt in statements)


def _realm_hubs() -> dict:
    for node in ast.parse(HUBS).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "REALM_HUBS":
            return ast.literal_eval(node.value)
    raise AssertionError("REALM_HUBS is no longer a literal the gate can read")


class TheRosterCarriesOneChannelPerWorld(unittest.TestCase):
    def test_every_world_names_its_own_events_channel_and_topic(self):
        hubs = _realm_hubs()
        self.assertEqual(len(hubs), 4, "the four worlds")
        names = set()
        for world, hub in hubs.items():
            with self.subTest(world=world):
                name = str(hub.get("events_channel_name") or "")
                self.assertTrue(name, f"{world} has no events channel name")
                self.assertRegex(name, r"^[a-z0-9-]+$", "a Discord channel name, lowercase and hyphenated")
                self.assertNotEqual(name, str(hub["channel_name"]),
                                    "the events channel must not be the capital")
                self.assertTrue(str(hub.get("events_topic") or "").strip(), f"{world} has no events topic")
                names.add(name)
        self.assertEqual(len(names), 4, f"two worlds share a channel name: {sorted(names)}")

    def test_the_roster_is_the_one_enumeration(self):
        """A fifth world is one entry here, not five edits in five files - which
        is only true while the provisioner reads the roster rather than a list
        of its own."""
        ensure = _body(CHANNELS, "ensure_world_event_channels")
        self.assertIn("for world, hub in REALM_HUBS.items():", ensure)
        self.assertIn('hub["events_channel_name"]', ensure)
        for world in _realm_hubs():
            self.assertNotIn(f'"{world}"', ensure, "the provisioner names a world instead of reading the roster")


class TheRouterPicksTheWorldOrTheGlobalFeed(unittest.TestCase):
    def test_a_place_the_catalogue_does_not_carry_has_no_world(self):
        """The expensive half. Every other site in the tree resolves a world
        with `or "Mortal World"`, and a private residence, an inner world, an
        abode or a literal "Unknown" is not in `WORLD.locations` - so that
        default would file somebody's household news as the Mortal World's
        public news. Here the honest answer is None, which routes to the global
        feed, which is where all of this went before."""
        body = _code(CHANNELS, "world_of_location")
        self.assertNotIn('or "Mortal World"', body, "the router defaults an unknown place into a world")
        self.assertIn("in REALM_HUBS", body, "a world the roster does not carry must not be routed to")
        self.assertIn("return None", body)

    def test_the_router_falls_back_to_the_bound_announcement_channel(self):
        body = _code(CHANNELS, "world_event_channel")
        self.assertIn("world_of_location(location)", body)
        self.assertIn("DB.get_world_event_channels(guild.id)", body)
        self.assertIn('config.get("announcement_channel_id")', body,
                      "with no world, an announcement must still reach the global feed")

    def test_an_unbound_world_falls_back_rather_than_going_nowhere(self):
        """A world whose channel is not provisioned yet (an upgraded server
        before Repair) must not swallow its own news."""
        body = _code(CHANNELS, "world_event_channel")
        self.assertIn("break", body, "an unresolvable bound channel must fall through to the global feed")


class TheWritersKnowWhereBeforeTheyPost(unittest.TestCase):
    """`_event_scene_location` existed and both writers already called it - just
    after the announcement had gone out. Order is the whole fix, so order is
    what is held, the way `test_quest_objective_reporters.py` holds a reporter
    speaking after its command has answered."""

    def _assert_resolves_first(self, func: str) -> None:
        body = _body(EVENT_SCENE, func)
        resolve = body.index("_event_scene_location(")
        route = body.index("world_event_channel(")
        send = body.index("announcement_channel.send(")
        self.assertLess(resolve, route, f"{func} routes before it knows the place")
        self.assertLess(route, send, f"{func} posts before it has chosen a channel")

    def test_the_player_triggered_writer_resolves_first(self):
        self._assert_resolves_first("spawn_event_thread")

    def test_the_autonomous_writer_resolves_first(self):
        self._assert_resolves_first("spawn_system_event_thread")

    def test_neither_writer_posts_to_the_global_feed_directly(self):
        for func in ("spawn_event_thread", "spawn_system_event_thread"):
            with self.subTest(func=func):
                body = _body(EVENT_SCENE, func)
                self.assertNotIn('_resolve_text_channel(guild, config.get("announcement_channel_id"))', body)

    def test_the_close_notice_still_follows_the_channel_it_was_posted_in(self):
        """It needed no change at all: `event_threads.announcement_channel_id`
        is written by `register_event_thread` at announcement time, so the close
        notice lands wherever the announcement did. Half the wire was already
        there."""
        bot_py = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        close = _body(bot_py, "close_event_scene")
        self.assertIn('record.get("announcement_channel_id")', close)
        self.assertNotIn("world_event_channel(", close, "the close notice must not re-route")


class TheWorldLessAnnouncementsStayGlobal(unittest.TestCase):
    """Four writers have no world and never will, so `world-events` is not
    retired - it is the global feed, and the fallback."""

    def test_the_weekend_gift_is_server_wide(self):
        bot_py = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn('get_server_config(guild.id)).get("announcement_channel_id")', bot_py)

    def test_the_world_reset_and_the_test_post_are_server_wide(self):
        self.assertGreaterEqual(SETUP.count('cfg.get("announcement_channel_id")'), 2)

    def test_the_base_channel_is_not_retired(self):
        """`#world-events` is a base channel and stays one.

        v1.0.0-rc.59 retired `#event-scenes` and made retirement a thing this
        codebase can express, so the rule is now checkable rather than quoted:
        the global feed must be provisioned and bound, and must not be in the
        retired set. It used to pin a prefix of the channel's topic string,
        which rc.59 rewrote - a test of the prose, not of the rule.
        """
        messages = (PROJECT_ROOT / "app" / "bot" / "admin" / "channel_messages.py").read_text(encoding="utf-8")
        self.assertIn('"world-events": BaseChannel(', messages)
        self.assertIn('"world-events": cfg.get("announcement_channel_id")', messages)
        self.assertNotIn('"world-events"', messages.split("RETIRED_BASE_CHANNELS = {")[1].split("}")[0])


class TheChannelsAreDashboardOwnedAndTornDown(unittest.TestCase):
    def test_setup_creates_and_the_slash_path_only_binds(self):
        complete = _body(SETUP, "_run_complete_server_setup")
        self.assertIn("ensure_world_event_channels(guild, category_name=SERVER_EVENT_CATEGORY, create_missing=create_missing)", complete)
        realmhubs = _body(SETUP, "admin_realm_hubs")
        self.assertIn("ensure_world_event_channels(guild, category_name=SERVER_EVENT_CATEGORY)", realmhubs)
        self.assertNotIn("create_missing=True", realmhubs)

    def test_an_existing_channel_is_moved_not_merely_rebound(self):
        ensure = _body(CHANNELS, "ensure_world_event_channels")
        self.assertIn("channel.category_id != category.id", ensure)
        self.assertIn("await channel.edit(category=category", ensure)
        self.assertIn("if can_create and category is not None", ensure)

    def test_they_are_gated_by_the_access_role_not_the_presence_role(self):
        """A world's news is for everyone who has reached that world, not only
        whoever is standing in its capital this minute - which is what the
        presence role means and why the capitals use it and this does not."""
        ensure = _body(CHANNELS, "ensure_world_event_channels")
        self.assertIn("_ensure_realm_access_roles(guild)", ensure)
        self.assertNotIn("_ensure_realm_presence_roles", ensure)

    def test_the_rows_are_forgotten_and_the_channels_deleted(self):
        clear = _body(CORE, "clear_discord_bindings")
        self.assertIn("DELETE FROM world_event_channels WHERE guild_id=?", clear)
        self.assertIn('"world_events": world_event_rows', clear)
        teardown = _body(SETUP, "teardown_managed_discord_layout")
        self.assertIn("DB.get_world_event_channels(guild.id)", teardown)
        self.assertIn("SERVER_EVENT_CATEGORY", teardown)

    def test_the_table_is_in_the_readiness_probe(self):
        from app.database import OPERATIONAL_REQUIRED_TABLES
        self.assertTrue("world_event_channels" in OPERATIONAL_REQUIRED_TABLES,
                        "a table a fresh bootstrap makes must be in the exact readiness set")

    def test_the_setter_is_bookkeeping_not_a_gameplay_write(self):
        gate = (PROJECT_ROOT / "tests" / "python" / "contracts" / "test_authority_boundary.py").read_text(encoding="utf-8")
        self.assertIn('"set_world_event_channel"', gate[gate.index("BOOKKEEPING_METHODS = {"):])


class TheBotKeepsItsOwnKeyToTheRoom(unittest.TestCase):
    """Found by the harness the first time it was allowed to run Full Setup.

    `ensure_realm_hub_overwrites` denied @everyone View Channel **before** it
    allowed the bot. A channel overwrite applies to the bot like anyone else
    unless it is Administrator, so that first call took away its own access and
    every `set_permissions` after it came back 403 Missing Access - leaving the
    channel denied to @everyone with no allow for the presence role, which is
    invisible to the very players it exists for, while `realm_hub_visibility`
    reported "VISIBLE TO ALL" because the role allow it looks for was never
    written. Two wrong answers out of one ordering, on every server that had
    not given the bot Administrator.
    """

    def test_the_bot_allows_itself_before_it_denies_everyone(self):
        body = _code(CHANNELS, "ensure_realm_hub_overwrites")
        me = body.index("set_permissions(guild.me")
        everyone = body.index("set_permissions(guild.default_role")
        role = body.index("set_permissions(role,")
        self.assertLess(me, everyone, "the bot locks itself out before it can allow itself")
        self.assertLess(everyone, role, "the deny and the allow must still be in that order")


class TheHarnessReachesTheProvisioner(unittest.TestCase):
    """rc.51 recorded that nothing in either harness could reach a
    `create_category` call, because the only caller passing `create_missing=True`
    is the dashboard's Full Setup. The Discord harness drives it over the bot's
    own control plane now - the same HTTP wire the dashboard posts to."""

    def test_the_discord_harness_drives_full_setup(self):
        """Read as *calls*, by AST, not as text.

        The first version of this asserted the substring `_control("setup")`,
        and its drill - commenting the call out - left the string in place and
        the gate passed. A grep cannot see a disabled call: rc.49 learned that
        about a Go condition and this is the same shape in Python. Only a call
        expression counts now, so commenting it out or deleting it both fail.
        """
        harness = (PROJECT_ROOT / "scripts" / "playtest_discord.py").read_text(encoding="utf-8")
        driven = {
            node.args[0].value
            for node in ast.walk(ast.parse(harness))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_control"
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        }
        self.assertEqual(driven, {"setup", "repair"}, f"the harness drives {sorted(driven)} on the control plane")
        self.assertIn("/control/discord", harness)
        self.assertIn("X-Xianxia-Control", harness)
        self.assertIn("SERVER_EVENT_CATEGORY", harness)
        self.assertIn("BOT_CONTROL_TOKEN", harness,
                      "without a token the control endpoint answers 404 and the step cannot run")

    def test_the_limitation_is_no_longer_recorded_as_open(self):
        known = (PROJECT_ROOT / "docs" / "TODO.md").read_text(encoding="utf-8")
        for line in known.splitlines():
            if "create_category" in line:
                self.assertNotIn("**deferred", line,
                                 "the harness reaches it now; the entry should say fixed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
