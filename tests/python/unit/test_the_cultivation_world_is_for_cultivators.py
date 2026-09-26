"""🗺️ Cultivation World is gated on having played (v1.0.11).

`#player-homes` and `#expeditions` are read-only anchors for private threads,
and they were the last player-facing category on the server open to everybody -
so a newcomer's sidebar advertised rooms they cannot use directly above the
`#begin-here` they are meant to go to, while Realm Capitals sat behind the
presence role, the four World Events feeds behind the realm-access role and
Admin behind administrator.

The deeper half is that **no "has a character" role existed at all**:
`_sync_realm_access_roles` and `_sync_realm_presence_roles` both run from
`require_character` and so only ever fire for somebody who already has one,
which meant nothing in the server could be gated on having played.

Four traps were known before a line was written, and each is a test here.

- **The bot allows itself before it denies anybody** (rc.52). A channel
  overwrite applies to the bot like anyone else unless it is Administrator, so
  denying `@everyone` first takes the bot's own access away and every call
  after it is refused 403 - leaving the channel denied to everyone with no
  allow to put back.
- **The overwrite reaches channels that already exist** (rc.59, found in the
  file that provisions them, where the category and the read-only lock were
  computed only inside `if channel is None and can_create:`). A category
  overwrite is inherited only by a channel synced to it, and both of these
  carry an `@everyone` overwrite of their own.
- **The grant precedes thread creation.** Both anchors carry private threads,
  whose members still need to see the parent.
- **A third generated role name is where the name family has to be gated** -
  that one lives in `test_the_role_names_never_collide.py`.

And one that is not a trap but a rule: `set_permissions(target, **perms)`
*replaces* an overwrite rather than merging into it, and the `@everyone`
overwrite on both channels is `send_messages=False`. Writing a bare
`view_channel=False` over it would have left them hidden and, to everybody
holding the role, writable - the gate quietly undoing the thing the channels
are for.
"""
from __future__ import annotations

import ast
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _module(name: str):
    with patch.dict(os.environ, ENV):
        import importlib

        return importlib.import_module(name)


def _function(path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """One function by AST, never by an indentation slice.

    A multi-line `def` defeats a slice on indentation, because the closing
    `) -> None:` sits at the function's own indent - the fault rc.59's
    base-channel gate shipped and its own drill found.
    """
    tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone from {path}; the gate is guarding a function that moved")


def _code(node) -> str:
    """The function's statements without its docstring.

    This file's own docstring quotes the rules it enforces, and so does the
    helper's: a gate that cannot tell prose from code is decoration (rc.52),
    and this one would find every rule in the paragraph explaining it.
    """
    body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                             and isinstance(node.body[0].value, ast.Constant)) else node.body
    return "\n".join(ast.unparse(statement) for statement in body)


class TheGateAllowsBeforeItDenies(unittest.TestCase):
    def setUp(self):
        self.node = _function("app/bot/channels.py", "ensure_cultivator_gate")
        self.code = _code(self.node)

    def test_the_reader_works(self):
        self.assertTrue("default_role" in self.code, (
            "the AST reader did not return ensure_cultivator_gate's statements; the gate is "
            "broken, not the tree"))

    def test_the_bot_is_allowed_before_everyone_is_denied(self):
        me = self.code.index("guild.me")
        everyone = self.code.index("guild.default_role")
        self.assertLess(me, everyone, (
            "@everyone is denied before the bot allows itself. A channel overwrite applies to the "
            "bot like anyone else unless it is Administrator, so that ordering 403s every call "
            "after it and leaves the channel invisible to the very players it is for (rc.52)"))

    def test_nothing_is_denied_without_a_role_to_allow(self):
        self.assertTrue("if role is None" in self.code, (
            "the gate denies @everyone with no role to put an allow back on - which is how a "
            "channel becomes invisible to everybody"))

    def test_the_overwrite_is_merged_rather_than_replaced(self):
        # `assertIn` prints its haystack, and the haystack is the whole
        # function; a gate whose message has to be scrolled past is one nobody
        # reads (v1.0.8's shell gate).
        #
        # v1.7.0 lifted the merge into `merge_overwrite` so the market-stalls
        # channels share one statement of it; the gate must still go through
        # it, and it must still merge.
        merge = _code(_function("app/bot/channels.py", "merge_overwrite"))
        self.assertTrue("merge_overwrite(" in self.code, (
            "the gate no longer goes through merge_overwrite, so nothing says its overwrites merge"))
        self.assertTrue("PermissionOverwrite" in merge and "overwrite=overwrite" in merge, (
            "set_permissions(target, **perms) replaces the overwrite. Both anchors carry an "
            "@everyone overwrite of send_messages=False, so a bare view_channel=False would "
            "leave them hidden and writable to everybody holding the role"))


class TheGateReachesWhatIsAlreadyThere(unittest.TestCase):
    def test_setup_gates_the_category_and_each_channel_in_it(self):
        """A category overwrite is inherited only by a channel synced to it."""
        code = _code(_function("app/bot/admin/server_setup.py", "_run_complete_server_setup"))
        self.assertTrue("ensure_cultivator_gate" in code, "Full Setup never applies the gate")
        self.assertTrue("CATEGORY_WORLD" in code, (
            "the gate is applied to something other than the channels that belong to "
            "🗺️ Cultivation World"))

    def test_the_gate_takes_both_the_category_and_a_list_of_channels(self):
        node = _function("app/bot/channels.py", "ensure_cultivator_gate")
        names = [arg.arg for arg in node.args.args]
        self.assertIn("category", names)
        self.assertIn("channels", names, (
            "the gate is category-only, so it reaches no channel that already exists with an "
            "overwrite of its own - rc.59's finding, in the release that fixed it for the "
            "neighbouring family"))


class TheRoleFollowsHavingACharacter(unittest.TestCase):
    def setUp(self):
        self.runtime = _module("app.bot.runtime")

    def test_the_role_is_named_and_is_not_a_world(self):
        self.assertTrue(self.runtime.CULTIVATOR_ROLE_NAME.startswith("Xianxia • "))

    def test_require_character_syncs_it(self):
        """The one place in the bot that fires often enough to keep a role in
        step - and reaching it at all is what "has a character" means."""
        code = _code(_function("app/bot/runtime.py", "require_character"))
        self.assertTrue("_sync_cultivator_role" in code, (
            "nothing keeps the role in step, so a player who joined before Full Setup ran never "
            "gets it"))

    def test_creation_grants_it_before_any_thread_is_opened(self):
        node = _function("app/bot/ui/creation.py", "on_submit")
        code = _code(node)
        grant = code.index("_sync_cultivator_role")
        for thread in ("ensure_birth_family_household_thread", "ensure_expedition_thread"):
            self.assertTrue(thread in code, f"{thread} is gone from creation; the gate moved")
            self.assertLess(grant, code.index(thread), (
                f"{thread} runs before the role is granted. Both anchors are in the gated "
                "category, and a member still needs to see a private thread's parent"))

    def test_an_erasure_takes_it_off(self):
        """The only place it can come off: `_sync_cultivator_role` rides
        `require_character`, which never fires again for an erased account."""
        code = _code(_function("app/bot/admin/world_ops.py", "admin_erase"))
        self.assertTrue("_revoke_cultivator_role" in code, (
            "an erased account keeps the role that says it has played, and nothing will ever "
            "notice - after an erasure require_character never fires for it again"))

    def test_the_backfill_sweep_reaches_it(self):
        """`_sync_all_realm_access_roles` is the one thing that walks every
        character already in the guild, so it is how an upgraded server gets
        the role onto people who made their cultivator before it existed."""
        code = _code(_function("app/bot/admin/server_setup.py", "_sync_all_realm_access_roles"))
        self.assertTrue("_sync_cultivator_role" in code, (
            "the sweep does not grant the role, so an upgraded server never backfills it"))
        self.assertTrue("_ensure_cultivator_role" in code, (
            "the sweep does not check the cultivator role against the bot's own hierarchy, so a "
            "role above the bot fails silently per member instead of once, loudly"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
