"""A cleared channel message must stay cleared.

The dashboard offers "clear a box and save to remove that channel's message".
That produces a stored row with content="", which is falsy - so the obvious
`row.get("content") or DEFAULT` treats it as "nothing configured" and hands back
the default. The message was deleted from Discord, the dashboard redisplayed the
default as if it were live, and the next Full Setup/Repair posted it again.

There are three states, and these tests pin all three:

    no stored row     -> default
    stored row, text  -> custom
    stored row, ""    -> disabled

These tests deliberately exercise the resolver by source-loading it rather than
importing app.bot.main, which needs discord.py at import time.
"""
from tests.support import PROJECT_ROOT
import ast
import unittest

MAIN = PROJECT_ROOT / "app" / "bot" / "main.py"


def _load_channel_message_helpers():
    """Execute just the state helpers and their default table, with no discord import.

    app/bot/main.py cannot be imported without discord.py, but these helpers are
    pure functions over a mapping, so lifting them out of the AST keeps the test
    runnable anywhere while still exercising the shipped source.
    """
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    wanted_functions = {"channel_message_state", "resolve_channel_message_content"}
    wanted_assignments = {
        "DEFAULT_CHANNEL_MESSAGES",
        "CHANNEL_MESSAGE_DEFAULT",
        "CHANNEL_MESSAGE_CUSTOM",
        "CHANNEL_MESSAGE_DISABLED",
    }
    picked: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            picked.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if names & wanted_assignments:
                picked.append(node)
    namespace: dict = {"Mapping": __import__("typing").Mapping, "Any": __import__("typing").Any}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(MAIN), "exec"), namespace)
    missing = (wanted_functions | wanted_assignments) - set(namespace)
    if missing:
        raise AssertionError(f"could not lift {sorted(missing)} out of main.py")
    return namespace


HELPERS = _load_channel_message_helpers()
state = HELPERS["channel_message_state"]
resolve = HELPERS["resolve_channel_message_content"]
DEFAULTS = HELPERS["DEFAULT_CHANNEL_MESSAGES"]
KEY = "world-events"


class ChannelMessageStateTests(unittest.TestCase):
    def setUp(self):
        self.assertIn(KEY, DEFAULTS, "fixture key must have a built-in default")
        self.assertTrue(DEFAULTS[KEY].strip())

    def test_no_stored_row_uses_the_default(self):
        self.assertEqual(state({}, KEY), "default")
        self.assertEqual(resolve({}, KEY), DEFAULTS[KEY])

    def test_stored_text_is_the_custom_message(self):
        stored = {KEY: {"content": "Welcome, cultivator.", "message_id": 5}}
        self.assertEqual(state(stored, KEY), "custom")
        self.assertEqual(resolve(stored, KEY), "Welcome, cultivator.")

    def test_explicitly_cleared_row_is_disabled_not_default(self):
        """The regression: "" must not be read as "nothing configured"."""
        stored = {KEY: {"content": "", "message_id": None}}
        self.assertEqual(state(stored, KEY), "disabled")
        self.assertEqual(resolve(stored, KEY), "")
        self.assertNotEqual(resolve(stored, KEY), DEFAULTS[KEY])

    def test_whitespace_only_content_counts_as_cleared(self):
        stored = {KEY: {"content": "   \n ", "message_id": None}}
        self.assertEqual(state(stored, KEY), "disabled")
        self.assertEqual(resolve(stored, KEY), "")

    def test_row_with_null_content_falls_back_to_default(self):
        """A row that never carried content is 'unset', not 'disabled'."""
        stored = {KEY: {"content": None, "message_id": None}}
        self.assertEqual(state(stored, KEY), "default")
        self.assertEqual(resolve(stored, KEY), DEFAULTS[KEY])

    def test_unknown_key_without_a_default_resolves_empty(self):
        self.assertEqual(resolve({}, "not-a-real-channel"), "")

    def test_clearing_then_repairing_does_not_resurrect_the_message(self):
        """End-to-end of the reported failure, at the resolver level."""
        stored = {KEY: {"content": DEFAULTS[KEY], "message_id": 99}}
        self.assertEqual(state(stored, KEY), "custom")
        # GM clears the box and saves; ensure_channel_message stores "".
        stored[KEY] = {"content": "", "message_id": None}
        # Full Setup/Repair now runs ensure_all_channel_messages.
        self.assertEqual(resolve(stored, KEY), "", "Repair would repost the default")


class ChannelMessagePersistenceTests(unittest.TestCase):
    """Source-level checks on the two call sites that used the falsy fallback."""

    def setUp(self):
        self.source = MAIN.read_text(encoding="utf-8")

    def test_no_call_site_still_uses_the_falsy_default_fallback(self):
        """Look for the actual expression, not the text.

        Matching on a substring would also hit the docstring that explains why
        the pattern is wrong, so this walks the AST for a real `<something> or
        DEFAULT_CHANNEL_MESSAGES[...]`/`.get(...)` expression instead.
        """
        offenders = []
        for node in ast.walk(ast.parse(self.source)):
            if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                continue
            for operand in node.values:
                target = operand.func.value if isinstance(operand, ast.Call) and isinstance(operand.func, ast.Attribute) else operand
                if isinstance(target, ast.Subscript):
                    target = target.value
                if isinstance(target, ast.Name) and target.id == "DEFAULT_CHANNEL_MESSAGES":
                    offenders.append(getattr(node, "lineno", "?"))
        self.assertEqual(
            offenders,
            [],
            f"main.py line(s) {offenders} still collapse an explicitly cleared "
            f'message into the default with `... or DEFAULT_CHANNEL_MESSAGES`',
        )

    def test_ensure_all_channel_messages_uses_the_resolver(self):
        start = self.source.index("async def ensure_all_channel_messages")
        body = self.source[start : start + 1200]
        self.assertIn("resolve_channel_message_content(stored, key)", body)

    def test_snapshot_reports_all_three_states(self):
        self.assertIn('"state": state', self.source)
        self.assertIn('"is_disabled": state == CHANNEL_MESSAGE_DISABLED', self.source)
        self.assertIn('"default_content"', self.source)

    def test_clearing_persists_even_when_the_channel_is_unbound(self):
        """ensure_channel_message used to return before saving if the channel was gone."""
        start = self.source.index("async def ensure_channel_message")
        body = self.source[start : start + 2000]
        early_return = body.index("if channel is None:")
        persisted = body.index("await DB.set_channel_message")
        self.assertLess(
            persisted - early_return,
            600,
            "the channel-is-None branch must persist the edit before returning",
        )


if __name__ == "__main__":
    unittest.main()
