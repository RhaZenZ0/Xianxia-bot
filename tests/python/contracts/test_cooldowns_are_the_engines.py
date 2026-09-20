"""How long a player waits was never the caller's to say (v1.0.0-rc.56).

`cultivation.train` read `cooldown_seconds` off the request payload and only
defaulted it when the key was absent; thirteen other actions did the same, and
**seven of them had no floor at all** - a caller sending `0` served no wait
whatsoever. The real value lived in `app/ops/config.py` and was mailed to the
engine on every request.

That is the fault `rejectCallerGameMinute` already refuses, in a second place:
a bound that lives in the client is not a bound. rc.48 put it this way for the
clock - *"a scheduled tick must not be able to tell the world what time it
is"* - and a wait is the same kind of number. The playtest harness proved it
was reachable: it sent `cooldown_seconds: 1` to drive its loops, a legitimate
use of an illegitimate door, and it asks the GM to clear the waits now.

The engine owns them: `actionCooldowns` in `go_core/internal/game/cooldown_rules.go`
is the one statement, read through `cooldownSecondsFor`, with `.env` as the
baseline exactly as `WORLD_TIME_SCALE` is since rc.39. This is the gate that
keeps it that way - the behaviour is Go's to prove, and what Python can
honestly check is that nothing here sends a wait and nothing here keeps a
second copy of one.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

GO = PROJECT_ROOT / "go_core" / "internal" / "game"
RULES = (GO / "cooldown_rules.go").read_text(encoding="utf-8")
AUTHORITATIVE = (GO / "authoritative.go").read_text(encoding="utf-8")

# The fields the engine derives and a caller may not state.
FORBIDDEN_FIELDS = ("cooldown_seconds", "quest_cooldown_seconds", "trial_cooldown_seconds")

# Two files keep a wait of their own, and it is genuinely somebody else's: the
# router's per-route failure backoff and the monitor's alert throttle meter
# OpenRouter, not a player. The exemption is two named files rather than the
# package, because `app/ai/` is not innocent as a whole - `narrator_context.py`
# takes the engine, the way `WorldSimulator` does - and it is proved rather
# than asserted below, so it cannot become a hole an engine payload hides in.
NOT_THE_ENGINES_WAIT = ("app/ai/ai_router.py", "app/ai/chat_monitor.py")

# The client's own module and the two methods that send a payload. Read as
# imports and attribute names rather than as substrings: `ENGINE.` alone
# matched the word "ENGINE." inside a narrator prompt.
ENGINE_MODULE = "game_engine"
ENGINE_CALLS = {"action", "authoritative_action"}


class NothingInPythonSendsAWait(unittest.TestCase):
    def test_no_bot_call_carries_a_cooldown(self):
        offenders = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            relative = str(path.relative_to(PROJECT_ROOT))
            if relative in NOT_THE_ENGINES_WAIT:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for field in FORBIDDEN_FIELDS:
                for line in text.splitlines():
                    if f'"{field}"' not in line and f"'{field}'" not in line:
                        continue
                    offenders.append(f"{relative}: {line.strip()[:90]}")
        self.assertEqual(offenders, [], "the engine owns the waits; nothing under app/ may send one")

    def test_the_exempt_files_cannot_reach_the_engine(self):
        """What makes the two exemptions safe: neither imports the engine
        client nor calls it, so a wait written there cannot become a wait
        sent. The day one gains a door, this fails and the exemption must go
        rather than the sweep quietly widening."""
        reaching = []
        for relative in NOT_THE_ENGINES_WAIT:
            tree = ast.parse((PROJECT_ROOT / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                imported = ""
                if isinstance(node, ast.Import):
                    imported = " ".join(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported = f"{node.module or ''} " + " ".join(a.name for a in node.names)
                if ENGINE_MODULE in imported:
                    reaching.append(f"{relative}: imports {imported.strip()}")
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ENGINE_CALLS):
                    reaching.append(f"{relative}: calls .{node.func.attr}()")
        self.assertEqual(reaching, [], "an exempt file reaches the engine; its cooldowns are no longer its own")

    def test_the_exemption_is_not_the_whole_package(self):
        """`app/ai/` holds a file that does call the engine, which is why the
        exemption above names two files and not the directory they sit in."""
        callers = {
            str(path.relative_to(PROJECT_ROOT))
            for path in (PROJECT_ROOT / "app" / "ai").rglob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ENGINE_CALLS
        }
        self.assertTrue(callers - set(NOT_THE_ENGINES_WAIT),
                        "app/ai no longer reaches the engine: exempt the package and say so")

    def test_the_retired_settings_are_gone(self):
        config = (PROJECT_ROOT / "app" / "ops" / "config.py").read_text(encoding="utf-8")
        for setting in ("cultivate_cooldown_minutes", "explore_cooldown_minutes",
                        "hunt_cooldown_minutes", "secret_realm_cooldown_minutes",
                        "perfect_quest_cooldown_minutes", "perfect_trial_cooldown_minutes"):
            self.assertNotIn(setting, config,
                             f"{setting} is the engine's now; a second copy here is free to drift")

    def test_the_harness_asks_the_gm_instead(self):
        # Read the call by AST, not by substring: commenting the helper out
        # would leave the name in place and pass a grep (the rc.49 lesson).
        source = (PROJECT_ROOT / "scripts" / "playtest_engine.py").read_text(encoding="utf-8")
        self.assertNotIn('"cooldown_seconds":', source)
        tree = ast.parse(source)
        resets = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "admin.player.reset_cooldowns"
        ]
        self.assertTrue(resets, "the harness must clear waits through the GM lever")


class TheEngineHoldsTheOneStatement(unittest.TestCase):
    def test_the_central_gate_refuses_every_wait(self):
        # One refusal for every action, beside the caller-supplied game_minute
        # it is the twin of - not fourteen per-handler checks.
        self.assertIn("var callerOwnedNothing = []string{", AUTHORITATIVE)
        block = AUTHORITATIVE.split("var callerOwnedNothing = []string{", 1)[1].split("}", 1)[0]
        for field in ("game_minute",) + FORBIDDEN_FIELDS:
            self.assertIn(f'"{field}"', block, field)

    def test_no_handler_reads_a_wait_off_the_payload(self):
        offenders = []
        for path in sorted(GO.glob("*.go")):
            if path.name.endswith("_test.go"):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\bp\.(Cooldown|QuestCooldown|TrialCooldown)Seconds\b", line):
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "a wait read off the payload is a wait the caller chose")

    def test_every_wait_has_one_entry_and_a_sane_default(self):
        body = RULES.split("var actionCooldowns = map[string]cooldownRule{", 1)[1].split("\n}", 1)[0]
        entries = re.findall(r"\{(\d+),\s*\"([A-Z_]*)\"\}", body)
        self.assertGreaterEqual(len(entries), 12, "every action that had a caller-supplied wait")
        for minutes, _ in entries:
            self.assertGreater(int(minutes), 0, "a wait of zero is no wait")
            self.assertLessEqual(int(minutes), 24 * 60, "a wait longer than a day wants a reason")

    def test_the_defaults_are_what_python_used_to_send(self):
        # A live world must not change pace because ownership moved.
        for key, minutes in (("CULTIVATE_COOLDOWN_MINUTES", 180), ("EXPLORE_COOLDOWN_MINUTES", 20),
                             ("HUNT_COOLDOWN_MINUTES", 30), ("SECRET_REALM_COOLDOWN_MINUTES", 15),
                             ("PERFECT_QUEST_COOLDOWN_MINUTES", 60),
                             ("PERFECT_TRIAL_COOLDOWN_MINUTES", 360)):
            self.assertRegex(RULES, rf"\{{{minutes}, \"{key}\"\}}",
                             f"{key} must still default to {minutes} minutes")

    def test_the_engine_is_given_the_keys_it_now_reads(self):
        # The engine's compose service takes an explicit allowlist and no
        # env_file, so a key it is not given is a key it cannot read - the
        # rc.39 finding that made WORLD_TIME_SCALE dead on arrival.
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        engine = compose.split("\n  xianxia-engine:", 1)[1].split("\n  xianxia-", 1)[0]
        self.assertIn("environment:", engine, "the engine service was not found in compose")
        for key in re.findall(r'"([A-Z_]+_COOLDOWN_MINUTES)"', RULES):
            self.assertIn(key, engine, f"the engine reads {key} but compose never passes it")


if __name__ == "__main__":
    unittest.main()
