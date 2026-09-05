"""Boundary contracts for the administrator chat monitor.

The monitor reads real player conversation and sends it to a cloud model. Three
properties have to stay true for that to be acceptable, and none of them are
visible from a unit test of the analysis logic itself:

1. It is administrator-only, and is not a typable Discord command.
2. It never leaves the free OpenRouter routes, so it cannot start spending money.
3. Turning off the player-facing prompt-leak guard for the report does not turn it
   off for narration.
"""

import re
import unittest

from tests.support import PROJECT_ROOT, bot_package_source, bot_source_files

BOT_DIR = PROJECT_ROOT / "app" / "bot"  # phase 1 of the main.py split: read the package
MONITOR = PROJECT_ROOT / "app" / "ai" / "chat_monitor.py"
ROUTER = PROJECT_ROOT / "app" / "ai" / "ai_router.py"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


class MonitorSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.bot = bot_package_source()

    def test_both_monitor_actions_are_registered_under_the_admin_server_group(self):
        for name in ("ai_status", "chat_digest"):
            self.assertRegex(
                self.bot,
                rf'@registered_group_command\(admin_server_group,\s*name="{name}"',
                name,
            )

    def test_monitor_actions_are_not_typable_root_commands(self):
        # Only the 21 registered surfaces are typable. A monitor that showed up as
        # /chat_digest would be a new public command nobody gated.
        roots = re.search(
            r'for name in \(("(?:[a-z_]+)"(?:,\s*"[a-z_]+")*)\):', self.bot
        )
        self.assertIsNotNone(roots, "could not find the root command registration tuple")
        registered = set(re.findall(r'"([a-z_]+)"', roots.group(1)))
        self.assertNotIn("chat_digest", registered)
        self.assertNotIn("ai_status", registered)
        self.assertIn("admin", registered)

    def test_every_monitor_handler_rechecks_administrator(self):
        for handler in ("admin_ai_status", "admin_chat_digest"):
            start = self.bot.index(f"async def {handler}(")
            body = self.bot[start : start + 1200]
            self.assertIn("await require_admin(interaction)", body, handler)

    def test_the_digest_is_written_to_the_admin_audit_log(self):
        self.assertIn('"server.chat_digest"', self.bot)

    def test_missing_message_content_intent_is_explained_not_silently_empty(self):
        self.assertIn("MONITOR_INTENT_HINT", self.bot)
        hint = self.bot[self.bot.index("MONITOR_INTENT_HINT = (") :][:700]
        self.assertIn("MESSAGE_CONTENT_INTENT=true", hint)
        self.assertIn("Developer Portal", hint)


class MonitorCostTests(unittest.TestCase):
    def test_the_monitor_only_reaches_models_through_the_shared_free_router(self):
        source = MONITOR.read_text(encoding="utf-8")
        # No direct client, no base URL, no model string of its own: every call goes
        # through AITaskRouter, which enforces OPENROUTER_REQUIRE_FREE.
        for forbidden in ("AsyncOpenAI", "openrouter.ai", "chat.completions", "model="):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIn("router.generate(", source)

    def test_no_paid_server_tool_is_wired_in(self):
        # Fusion (openrouter:fusion) fans out to a panel of models and bills for all
        # of them. It is deliberately not used here.
        for path in (MONITOR, ROUTER, *bot_source_files()):
            self.assertNotIn("openrouter:fusion", path.read_text(encoding="utf-8"), str(path))

    def test_monitor_budgets_are_bounded_by_configuration(self):
        bot = bot_package_source()
        self.assertIn("SETTINGS.monitor_max_messages", bot)
        self.assertIn("SETTINGS.monitor_chunk_chars", bot)
        self.assertIn("SETTINGS.monitor_max_chunks", bot)

    def test_env_example_documents_the_monitor_and_the_intent(self):
        env = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("MESSAGE_CONTENT_INTENT=true", env)
        for key in (
            "MONITOR_MAX_MESSAGES",
            "MONITOR_LOOKBACK_HOURS",
            "MONITOR_CHUNK_CHARS",
            "MONITOR_MAX_CHUNKS",
        ):
            self.assertIn(key, env, key)


class LeakGuardContractTests(unittest.TestCase):
    def test_the_guard_defaults_to_on(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn("leak_guard: bool = True", source)

    def test_narrator_never_passes_leak_guard(self):
        # If narration ever opted out, players could be shown implementation detail.
        narrator = (PROJECT_ROOT / "app" / "ai" / "narrator.py").read_text(encoding="utf-8")
        self.assertNotIn("leak_guard", narrator)

    def test_only_the_monitor_opts_out(self):
        opt_outs = []
        for path in sorted(PROJECT_ROOT.joinpath("app").rglob("*.py")):
            if "leak_guard=False" in path.read_text(encoding="utf-8"):
                opt_outs.append(path.name)
        self.assertEqual(opt_outs, ["chat_monitor.py"])


if __name__ == "__main__":
    unittest.main()
