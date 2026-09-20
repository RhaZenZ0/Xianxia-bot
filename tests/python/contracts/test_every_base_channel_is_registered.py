"""A base channel is wired in nine places or it is half-wired (v1.0.0-rc.59).

Base channels are column-per-channel, not generic: `world_event_channels` is a
row-per-world table, so rc.52 added a prefixed message key and nothing else,
while a ninth base channel needs a `server_config` column, a migration, four
edits inside one `set_server_channels`, a teardown NULL, a bindings entry, a
dashboard mapping and a form field.

`#playtest`, the eighth, got a test for exactly this (`test_playtest_board.py`)
and it named `playtest` nine times. That is the right check written the wrong
way round: it proves *that* channel is registered and says nothing about the
next one. `#updates` is the ninth, and this walks `BASE_CHANNEL_SPECS` instead,
so the tenth cannot be half-wired either.
"""
from __future__ import annotations

import ast
import re
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

MESSAGES = (PROJECT_ROOT / "app" / "bot" / "admin" / "channel_messages.py").read_text(encoding="utf-8")
SETUP = (PROJECT_ROOT / "app" / "bot" / "admin" / "server_setup.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
APP_JS = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _channel_messages():
    with patch.dict("os.environ", ENV):
        import importlib

        return importlib.import_module("app.bot.admin.channel_messages")


def _bindings_block() -> str:
    return MESSAGES.split("def _base_channel_bindings(")[1].split("\n\n")[0]


def _func(text: str, name: str) -> str:
    """A named function's source, by AST.

    The first version of this sliced on indentation from a header string, and
    a multi-line `def` defeated it: the closing `) -> None:` sits at the
    function's own indent, so every block ended one line in and every check
    below passed vacuously. `test_the_sweep_still_sees_the_channels` is what
    caught it - the rc.57 rule that a reader is asserted before it is trusted.
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"no function named {name!r}")


def _bind_channels_branch() -> str:
    """The dashboard's `bind_channels` action, which is a branch rather than a
    function - sliced from its `if` to the next one at the same indent."""
    lines = SETUP.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if 'if action == "bind_channels":' in line:
            break
    else:
        raise AssertionError("the bind_channels branch is gone")
    indent = len(lines[index]) - len(lines[index].lstrip())
    out = [lines[index]]
    for line in lines[index + 1:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    return "".join(out)

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _channel_messages():
    with patch.dict("os.environ", ENV):
        import importlib

        return importlib.import_module("app.bot.admin.channel_messages")


def _bindings_block() -> str:
    return MESSAGES.split("def _base_channel_bindings(")[1].split("\n\n")[0]


def _block(text: str, header: str) -> str:
    """The source from the line containing `header` to the next line at the
    same indent or less.

    The indent is read off the *source line*, not off the `header` string:
    taking it from the argument meant every method looked top-level and the
    block ran to the end of the file, which would have made every check below
    pass. `test_the_sweep_still_sees_the_channels` asserts that a known block
    stops where it should before any of this is trusted - the rc.57 rule, and
    it caught this on its first run.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if header in line:
            break
    else:
        raise AssertionError(f"block header not found: {header!r}")
    indent = len(lines[index]) - len(lines[index].lstrip())
    out = [lines[index]]
    for line in lines[index + 1:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    return "".join(out)

class EveryBaseChannelIsRegisteredEverywhere(unittest.TestCase):
    def setUp(self):
        self.module = _channel_messages()
        self.specs = self.module.BASE_CHANNEL_SPECS
        self.bindings_block = _bindings_block()

    def test_the_sweep_still_sees_the_channels(self):
        """Self-check first: a reader that finds nothing passes everything."""
        self.assertGreaterEqual(len(self.specs), 8, "the base-channel table all but disappeared")
        for expected in ("world-events", "begin-here", "xianxia-info", "updates"):
            self.assertIn(expected, self.specs)
        self.assertIn('cfg.get("announcement_channel_id")', self.bindings_block)
        setter = _func(CORE, "set_server_channels")
        self.assertIn("announcement_channel_id", setter)
        self.assertNotIn("set_playtest_item", setter, "the block reader ran past its function")
        # `_bound_id(`, not a column name: `_base_channel_bindings` names every
        # column too, so a reader pointed at the wrong function still passed
        # this check on its first drill. Pick a string only the right function
        # can contain.
        self.assertIn("_bound_id(", _func(MESSAGES, "ensure_base_xianxia_channels"),
                      "the persist block reader is not reading the persist block")
        self.assertIn("playtest", _bind_channels_branch(), "the bind_channels slice found nothing")

    def test_every_spec_names_a_topic_and_a_category(self):
        for name, spec in self.specs.items():
            with self.subTest(channel=name):
                self.assertGreaterEqual(len(spec.topic), 20, f"#{name} has no topic worth showing")
                self.assertIn(spec.category, self.module.__dict__.values(),
                              f"#{name} names a category bucket that does not exist")

    def test_every_channel_has_a_binding_a_column_and_a_teardown(self):
        missing: list[str] = []
        for name in self.specs:
            column = re.search(rf'"{re.escape(name)}": cfg\.get\("(\w+)"\)', self.bindings_block)
            if column is None:
                missing.append(f"{name}: no entry in _base_channel_bindings")
                continue
            key = column.group(1)
            # Presence in the right block, not an exact spelling: three of the
            # columns are required kwargs assigned straight from `excluded.`
            # while the rest are optional and COALESCE'd, and the gate is about
            # being registered everywhere rather than registered identically.
            for where, block in (
                ("set_server_channels", _func(CORE, "set_server_channels")),
                ("clear_discord_bindings", _func(CORE, "clear_discord_bindings")),
                ("the persist block", _func(MESSAGES, "ensure_base_xianxia_channels")),
                ("the dashboard bind mapping", _bind_channels_branch()),
            ):
                if key not in block:
                    missing.append(f"{name} ({key}): {where} does not name it")
        self.assertEqual(missing, [], "a base channel wired in some places and not others")

    def test_every_channel_has_a_dashboard_form_field(self):
        missing = [name for name in self.specs if f"'{name}')}}" not in APP_JS]
        self.assertEqual(missing, [], "a base channel a GM cannot bind from the dashboard")

    def test_every_channel_says_what_it_is(self):
        """A base channel with no blurb is created blank, silently.

        `resolve_channel_message_content` answers `""` for a key
        `DEFAULT_CHANNEL_MESSAGES` does not carry - which is correct, because
        that is also how a GM turns a message off - so a new channel with no
        entry is indistinguishable from one a GM deliberately cleared. Nothing
        errors and nothing is posted. `#updates` shipped that way in the first
        draft of this release: created, locked, bound, and empty until the
        first release landed in it.

        Two channels have no entry on purpose, because something else fills
        them, and each says which.
        """
        filled_by_something_else = {
            # The guide view writes every page of it at Setup and at Repair.
            "xianxia-info": "XianxiaInfoView writes it",
            # The playtest board posts one item per hub page.
            "playtest": "the playtest board posts its own items",
        }
        missing = [
            name for name in self.specs
            if name not in self.module.DEFAULT_CHANNEL_MESSAGES and name not in filled_by_something_else
        ]
        self.assertEqual(missing, [], "a base channel the bot creates and never says anything in")
        for name, reason in filled_by_something_else.items():
            with self.subTest(channel=name):
                self.assertIn(name, self.specs, f"{name} is exempted from having a blurb and is not a base channel")
                self.assertNotIn(name, self.module.DEFAULT_CHANNEL_MESSAGES,
                                 f"{name} has a blurb now, so remove it from the exemption ({reason})")

    def test_a_retired_channel_keeps_its_binding(self):
        """`#event-scenes` is retired and deliberately still bound.

        Teardown builds its targets from `_base_channel_bindings`, so dropping
        the key would leave the channel standing on every server that has one -
        which is the rc.51 fault, and the reason the category constant it used
        to live in is still in the teardown tuple too.
        """
        for name in self.module.RETIRED_BASE_CHANNELS:
            with self.subTest(channel=name):
                self.assertNotIn(name, self.specs, f"{name} is retired but still provisioned")
                self.assertIn(f'"{name}": cfg.get(', self.bindings_block,
                              f"{name} is retired and its binding is gone: teardown can no longer reach it")


if __name__ == "__main__":
    unittest.main()
