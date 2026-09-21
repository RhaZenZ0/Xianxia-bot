"""How long a hub panel sits idle is one number, and it is a setting (v1.0.12).

Asked for in play: *"can we skip the 15min wait time for reopen"*. A panel went
quiet after fifteen minutes and swapped its controls for a **Reopen** button -
which is right in principle, because discord.py holds a live view in memory for
as long as it has not timed out, and wrong at fifteen minutes, which is a long
time to hold one open and a short time to read a page, go and do something, and
come back to it.

The number was a bare `timeout=900` written out in **five** files
(`hubs.py` twice, `surface.py`, `admin/world_ops.py`, `commands/support.py`), so
changing it meant finding all five - the same shape as the command tree's own
tuple, one level down, and the reason this release made that a name too.

`HUB_PANEL_IDLE_MINUTES` is the setting, **120** is the default, and `0` means a
panel never expires. Zero is deliberately not the default: it costs one view
held for the life of the process per panel ever opened, which is fine on a small
server and is the operator's call rather than this file's.

**Injected, not read.** `hubs.py` and `runtime.py` are the same tier and
`test_bot_package` refuses an import between them, so `surface.py` registers the
number the way it already registers the four gates - the shape
`app/rules/feature_unlocks.py` and `describe_era` use for their content. The
module default is the old fifteen minutes, so a missed registration is the
behaviour this release started from rather than a panel that never expires: a
presentation default that failed towards *never* would leak.
"""
from __future__ import annotations

import ast
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

# Every file that builds a panel view. Each used to spell the timeout out.
PANEL_FILES = ("hubs.py", "surface.py", "admin/world_ops.py", "commands/support.py")


def _hubs():
    with patch.dict(os.environ, ENV):
        import importlib

        return importlib.import_module("app.bot.hubs")


class TheIdleWindowIsOneNumber(unittest.TestCase):
    def test_no_panel_spells_its_own_timeout_out(self):
        offenders = []
        for name in PANEL_FILES:
            source = (BOT / name).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "timeout" or not isinstance(keyword.value, ast.Constant):
                        continue
                    if isinstance(keyword.value.value, (int, float)) and keyword.value.value >= 600:
                        offenders.append(f"{name}:{node.lineno} timeout={keyword.value.value}")
        self.assertEqual(offenders, [], (
            "a panel view carries its own idle window rather than asking panel_timeout(). Five "
            f"copies of one number are five places to forget one: {offenders}"))

    def test_every_panel_view_asks_the_one_helper(self):
        for name in PANEL_FILES:
            source = (BOT / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertTrue("panel_timeout()" in source,
                                f"{name} builds a panel view without asking how long it may stay open")

    def test_the_helper_answers_minutes_and_never_for_zero(self):
        hubs = _hubs()
        try:
            hubs.register_panel_idle(120)
            self.assertEqual(hubs.panel_timeout(), 7200.0)
            hubs.register_panel_idle(1)
            self.assertEqual(hubs.panel_timeout(), 60.0)
            hubs.register_panel_idle(0)
            self.assertIsNone(hubs.panel_timeout(), (
                "0 must mean a panel never expires - that is the whole of what was asked for, and "
                "a timeout of 0 seconds would expire every panel the instant it was drawn"))
            hubs.register_panel_idle(-5)
            self.assertIsNone(hubs.panel_timeout(), "a negative window is never, not a crash")
        finally:
            hubs.register_panel_idle(120)

    def test_an_unregistered_window_is_the_old_fifteen_minutes(self):
        """Failing towards *never* would leak a view per panel, for ever."""
        source = (BOT / "hubs.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        default = next((node.value.value for node in ast.walk(tree)
                        if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "_PANEL_IDLE_MINUTES" for t in node.targets)
                        and isinstance(node.value, ast.Constant)), None)
        self.assertEqual(default, 15, (
            "the module default is not the fifteen minutes this behaved as before the setting, so "
            "a missed registration changes behaviour instead of preserving it"))

    def test_the_surface_registers_it_from_the_setting(self):
        source = (BOT / "surface.py").read_text(encoding="utf-8")
        # assertTrue, not assertIn: the haystack is the whole file, and a gate
        # whose message has to be scrolled past is one nobody reads (v1.0.8).
        self.assertTrue("register_panel_idle(SETTINGS.hub_panel_idle_minutes)" in source, (
            "nothing hands the hubs the configured window, so HUB_PANEL_IDLE_MINUTES reaches "
            "nothing and the setting is decoration"))

    def test_the_setting_is_read_bounded_and_documented(self):
        with patch.dict(os.environ, ENV, clear=False):
            import importlib

            config = importlib.import_module("app.ops.config")
            with patch.dict(os.environ, {**ENV, "HUB_PANEL_IDLE_MINUTES": "45"}):
                self.assertEqual(config.Settings.from_env().hub_panel_idle_minutes, 45)
            with patch.dict(os.environ, {**ENV, "HUB_PANEL_IDLE_MINUTES": "0"}):
                self.assertEqual(config.Settings.from_env().hub_panel_idle_minutes, 0)
            with patch.dict(os.environ, {**ENV, "HUB_PANEL_IDLE_MINUTES": "-1"}):
                with self.assertRaises(ValueError):
                    config.Settings.from_env()
        self.assertIn("HUB_PANEL_IDLE_MINUTES", (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8"))
        self.assertIn("HUB_PANEL_IDLE_MINUTES",
                      (PROJECT_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
