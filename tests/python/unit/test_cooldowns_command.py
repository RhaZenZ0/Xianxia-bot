"""`/cooldowns` says what the engine knows, in words the engine does not have.

The split is the point. Go owns every wait and every clock conversion; this
module owns the labels, the names behind the ids, and the single piece of
filtering that is configuration rather than a rule. So what is worth holding
here is not arithmetic - there is none - but the seam:

1. the command asks the engine and computes no wait of its own;
2. every family the engine can emit has words, and every label it carries is a
   family the engine can still emit - the mirror of
   `TestEveryCooldownKeyTheEngineWritesIsInTheRoster` in
   go_core/internal/game/cooldown_roster_test.go;
3. the one filter Python owns really is the configuration one.
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

COMMAND = (PROJECT_ROOT / "app" / "bot" / "commands" / "cooldowns.py").read_text(encoding="utf-8")
SURFACE = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
ENGINE = (PROJECT_ROOT / "go_core" / "internal" / "game" / "cooldown_status.go").read_text(encoding="utf-8")


def engine_families() -> set[str]:
    """Every family the engine's roster can put on a row, cooldown rows and
    the waits that live outside the table alike."""
    roster = ENGINE[ENGINE.index("var cooldownFamilies"):ENGINE.index("// cooldownExternalFamilies")]
    families = set(re.findall(r'Family:\s*"([a-z0-9_]+)"', roster))
    external = ENGINE[ENGINE.index("var cooldownExternalFamilies"):]
    external = external[:external.index("}")]
    families |= set(re.findall(r'"([a-z0-9_]+)"', external))
    return families


class TheCardIsARootAPlayerCanType(unittest.TestCase):
    def test_it_is_registered_with_discord(self):
        self.assertIn("from .commands import cooldowns as _commands_cooldowns", SURFACE)
        self.assertIn('"vote", "cooldowns"', SURFACE)
        with patch.dict(os.environ, ENV):
            surface = importlib.import_module("app.bot.surface")
            self.assertEqual(surface.ACTIONS.root("cooldowns").name, "cooldowns")


class TheEngineOwnsEveryWait(unittest.TestCase):
    def test_the_command_asks_the_engine_and_computes_no_wait_of_its_own(self):
        operations = {
            node.args[0].value
            for node in ast.walk(ast.parse(COMMAND))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"action", "authoritative_action"}
            and node.args and isinstance(node.args[0], ast.Constant)
        }
        self.assertEqual(operations, {"cooldown.status"})
        # The remaining time is read, never derived: no clock arithmetic here.
        for forbidden in ("time.time()", "datetime.now", "- now", "available_at -"):
            self.assertNotIn(forbidden, COMMAND)

    def test_a_stopped_world_clock_is_said_rather_than_counted_down(self):
        # The engine reports scheduled=false when the GM has frozen the world;
        # printing a <t:0:R> there would put the arrival in 1970.
        self.assertIn('if not row.get("scheduled"):', COMMAND)
        self.assertIn("world clock is stopped", COMMAND)

    def test_engine_errors_are_explained_rather_than_dumped(self):
        self.assertIn("_explain_engine_error(exc)", COMMAND)
        self.assertNotIn('f"❌ {exc}"', COMMAND)


class EveryFamilyHasWords(unittest.TestCase):
    """The mirror of the Go roster scan: neither side may grow alone."""

    def setUp(self):
        with patch.dict(os.environ, ENV):
            self.labels = importlib.import_module("app.bot.commands.cooldowns").FAMILY_LABELS

    def test_every_family_the_engine_can_emit_has_a_label(self):
        missing = sorted(engine_families() - set(self.labels))
        self.assertEqual(missing, [], f"families the card cannot name: {missing}")

    def test_no_label_survives_a_family_the_engine_no_longer_emits(self):
        stale = sorted(set(self.labels) - engine_families())
        self.assertEqual(stale, [], f"labels for waits nothing can produce: {stale}")

    def test_every_label_carries_a_mark_and_words(self):
        for family, (mark, label, _path) in self.labels.items():
            with self.subTest(family=family):
                self.assertTrue(mark.strip(), f"{family} has no mark")
                self.assertTrue(label.strip(), f"{family} has no words")

    def test_only_a_gm_wait_is_left_without_somewhere_to_go(self):
        # Every ordinary wait names where it is done. Being muted or frozen is
        # the exception: there is no action to point a player at.
        pathless = {family for family, (_m, _l, path) in self.labels.items() if not path}
        self.assertEqual(pathless, {"muted", "frozen"})


class PythonOwnsOnlyTheConfiguration(unittest.TestCase):
    def test_the_vote_row_is_dropped_when_no_listing_is_configured(self):
        # Whether a listing site exists is .env, not a rule - so this is the
        # one thing the engine deliberately does not decide.
        self.assertIn("if not SETTINGS.vote_site_url:", COMMAND)
        self.assertIn('row.get("family") != "support_vote"', COMMAND)

    def test_the_beast_lookup_only_runs_when_a_beast_is_on_the_card(self):
        body = COMMAND[COMMAND.index("async def _beast_names"):COMMAND.index("@registered_root_command")]
        self.assertIn("if not any(", body)
        self.assertIn("return {}", body)
        self.assertIn("DB.get_spirit_beasts", body)


if __name__ == "__main__":
    unittest.main()
