"""The doors stay shut, at every door (v1.0.0-rc.56).

`/cultivation → Seclusion → Start` has promised since v0.30.0 that *"any
state-changing command will remain locked"*. What enforced it was half a gate
in `runtime.py`'s `serialized_user_action`: it covered only the ~141 handlers
wearing that decorator so every read passed, its exemption was the
function-name prefix `seclusion_`, a hub press was already deferred before it
ran, typed play never reached it, and it settled the retreat before it
checked - so the engine action ran ahead of its own refusal.

The engine refuses the authoritative path now (`checkPlayerSeclusionTx`) and
that is the backstop. This holds the other half, in `test_maintenance_mode`'s
shape and for its reason: the wiring is what fails silently, and a door added
later with no gate would pass every behavioural test.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
import time
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

BOT = PROJECT_ROOT / "app" / "bot"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

with patch.dict(os.environ, ENV):
    seclusion = importlib.import_module("app.bot.seclusion")


class _DB:
    def __init__(self, row=None, raises=False):
        self.row, self.raises = row, raises

    async def get_seclusion(self, user_id, *, active_only=True):
        if self.raises:
            raise RuntimeError("database is away")
        return self.row


def _active(minutes_left: float | None = 45.0) -> dict:
    row = {"user_id": 42, "mode": "qi", "status": "active", "accumulated_gain": 12,
           "ends_game_minute": 99_999, "ends_real_ts": None}
    if minutes_left is not None:
        row["ends_real_ts"] = time.time() + minutes_left * 60
    return row


def run(coro):
    return asyncio.run(coro)


class TheRuleItself(unittest.TestCase):
    def test_a_secluded_player_is_refused(self):
        refusal = run(seclusion.refuse(_DB(_active()), 42, command="explore"))
        self.assertIsNotNone(refusal)
        self.assertIn("closed-door", refusal)
        self.assertRegex(refusal, r"\*\*4[45] minutes\*\*")

    def test_nobody_else_is(self):
        self.assertIsNone(run(seclusion.refuse(_DB(None), 42, command="explore")))
        self.assertIsNone(run(seclusion.refuse(_DB({"status": "completed"}), 42, command="explore")))

    def test_the_way_out_and_the_reads_stay_open(self):
        db = _DB(_active())
        for command in ("/seclusion end", "/seclusion status",  # the way out
                        "/sheet", "/inventory", "/wallet", "/effects", "/time",  # read leaves
                        "me", "menu", "quests", "cooldowns", "check",  # slash reads
                        "cultivation", "character", "items"):  # hub panels
            with self.subTest(command=command):
                self.assertIsNone(run(seclusion.refuse(db, 42, command=command)))

    def test_a_hub_panel_opens_but_its_leaves_do_not(self):
        """Opening a panel is a read, and it is where the way out is drawn -
        refusing it would hide the only door. Each leaf inside is checked
        again on the press, which is what `_panel_gate` is for."""
        db = _DB(_active())
        self.assertIsNone(run(seclusion.refuse(db, 42, command="cultivation")))
        for leaf in ("/cultivate", "/breakthrough", "/seclusion start", "/explore", "/craft"):
            with self.subTest(leaf=leaf):
                self.assertIsNotNone(run(seclusion.refuse(db, 42, command=leaf)))

    def test_the_commands_that_act_are_refused_even_though_they_are_short(self):
        db = _DB(_active())
        for command in ("begin", "action", "tribute"):
            with self.subTest(command=command):
                self.assertIsNotNone(run(seclusion.refuse(db, 42, command=command)))

    def test_admin_stays_open_but_the_person_is_not_exempt(self):
        """Maintenance exempts the administrator, because they are how the
        world reopens. A retreat is the player's own state, so what stays open
        here is the `/admin` tree and not the person holding it."""
        db = _DB(_active())
        self.assertIsNone(run(seclusion.refuse(db, 42, command="admin player setrealm")))
        self.assertIsNotNone(run(seclusion.refuse(db, 42, command="/cultivate")))

    def test_an_unnamed_door_is_refused(self):
        """A typed line and a free-narration message name no command. They are
        play, so they are refused - the old gate never reached them at all."""
        self.assertIsNotNone(run(seclusion.refuse(_DB(_active()), 42)))

    def test_a_retreat_whose_time_is_up_is_let_through(self):
        """The engine's own gate settles and completes an expired retreat on
        the next action; refusing here would refuse a retreat that is over,
        and a lockout on state only an action can clear is a deadlock."""
        self.assertIsNone(run(seclusion.refuse(_DB(_active(minutes_left=-1)), 42, command="cultivation")))

    def test_it_fails_towards_play(self):
        """An unreachable database is not a reason to lock a player out: the
        engine refuses anything that matters anyway, and a gate that locks all
        play must fail towards play."""
        self.assertIsNone(run(seclusion.refuse(_DB(raises=True), 42, command="cultivation")))

    def test_a_grandfathered_retreat_says_nothing_it_cannot_know(self):
        """`ends_real_ts` is NULL before schema 57. Converting the world
        minutes left into real ones behind a rate a GM may have changed would
        be a guess printed as a fact."""
        refusal = run(seclusion.refuse(_DB(_active(minutes_left=None)), 42, command="explore"))
        self.assertIsNotNone(refusal)
        self.assertIn("planned retreat completes", refusal)
        self.assertNotIn("minutes**", refusal)


class TheAllowlistIsTheRealSurface(unittest.TestCase):
    """The old gate's exemption was whether a handler wore a decorator, which
    is not a decision anybody made. These hold the explicit list against the
    live command surface, so a new hub or a renamed read fails here rather
    than quietly closing a door or opening one."""

    def _surface(self):
        with patch.dict(os.environ, ENV):
            hubs = importlib.import_module("app.bot.hubs")
            surface = importlib.import_module("app.bot.surface")
        return hubs, surface

    def test_every_hub_is_named_and_no_name_is_stale(self):
        hubs, _ = self._surface()
        live = {definition.name for definition in hubs.REGISTERED_HUBS}
        self.assertGreaterEqual(len(live), 17)
        self.assertEqual(seclusion.HUB_ROOTS, live,
                         "a hub whose panel cannot be opened hides the way out of a retreat")

    def test_every_open_slash_command_is_one_the_tree_registers(self):
        _, surface = self._surface()
        # Imported rather than parsed since v1.0.12: the tuple is
        # `surface.TREE_COMMANDS`, so there is no read that can come back empty.
        registered = set(surface.TREE_COMMANDS)
        self.assertTrue(registered, "the tree tuple is empty; the gate is broken, not the tree")
        self.assertTrue(seclusion.OPEN_COMMANDS <= registered,
                        f"{sorted(seclusion.OPEN_COMMANDS - registered)} is open but not a command")
        # The ones that act are deliberately shut, and this says so: the
        # three that always were, and the daily five (v1.3.2), each a hub
        # leaf the panel gate already refuses on the press.
        self.assertEqual(sorted(registered - seclusion.OPEN_COMMANDS - {"admin"}),
                         sorted(["action", "begin", "tribute", *surface.DAILY_ACTIONS]))

    def test_the_way_out_is_a_leaf_every_hub_walk_can_reach(self):
        hubs, _ = self._surface()
        live = {action.path for definition in hubs.REGISTERED_HUBS
                for page in definition.pages for action in hubs._leaf_actions(page)}
        for name in seclusion.ALWAYS_OPEN:
            with self.subTest(name=name):
                self.assertIn("/" + name, live, "the exit names a leaf no hub reaches")


class EveryDoorIsGated(unittest.TestCase):
    """Read off the source: the wiring is what fails silently."""

    def test_every_slash_command_passes_the_tree_check(self):
        bot = (BOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(bot)
        check = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.AsyncFunctionDef) and n.name == "interaction_check")
        source = ast.get_source_segment(bot, check) or ""
        self.assertIn("seclusion.refuse", source)

    def test_the_hub_panel_checks_on_the_press(self):
        surface = (BOT / "surface.py").read_text(encoding="utf-8")
        tree = ast.parse(surface)
        gate = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_panel_gate")
        source = ast.get_source_segment(surface, gate) or ""
        self.assertIn("seclusion.refuse", source)

    def test_the_typed_line_and_the_shorthand_are_gated(self):
        bot = (BOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("seclusion.refuse(DB, message.author.id)", bot)

    def test_the_picker_click_is_gated(self):
        typed = (BOT / "typed_play.py").read_text(encoding="utf-8")
        dispatch = typed[typed.index("async def dispatch("):]
        dispatch = dispatch[: dispatch.index('\n    if candidate.kind == "root":')]
        self.assertIn("seclusion.refuse", dispatch)

    def test_the_old_half_gate_is_gone(self):
        """It is replaced, not left beside the new one: two gates with
        different semantics is how the first one came to exempt by
        function-name prefix and settle before it checked."""
        runtime = (BOT / "runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(runtime)
        wrapper = next((n for n in ast.walk(tree)
                        if isinstance(n, ast.AsyncFunctionDef) and n.name == "wrapper"), None)
        self.assertIsNotNone(wrapper, "serialized_user_action no longer has a wrapper")
        source = ast.get_source_segment(runtime, wrapper) or ""
        # By call, not by substring - the explanation of what was removed
        # names the old prefix and must not itself fail this.
        calls = {ast.unparse(n.func) for n in ast.walk(ast.parse(source.strip()))
                 if isinstance(n, ast.Call)}
        self.assertNotIn("settle_seclusion_for_user", calls,
                         "the gate still settles before it checks")
        self.assertNotIn("func.__name__.startswith", calls,
                         "the exemption is still a function-name prefix")


class TheEngineHoldsTheBackstop(unittest.TestCase):
    def test_the_authoritative_path_is_gated_and_the_way_out_is_exempt(self):
        authoritative = (PROJECT_ROOT / "go_core" / "internal" / "game" / "authoritative.go").read_text(encoding="utf-8")
        self.assertIn("checkPlayerSeclusionTx(conn, catalog, req.ActorID", authoritative)
        lockout = (PROJECT_ROOT / "go_core" / "internal" / "game" / "seclusion_lockout.go").read_text(encoding="utf-8")
        self.assertIn('"seclusion.settle": true', lockout,
                      "the way out must be exempt or a retreat can never be left")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
