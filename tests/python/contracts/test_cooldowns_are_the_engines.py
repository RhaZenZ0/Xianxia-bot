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



def cooldown_defaults() -> list[tuple[str, int]]:
    """Every wait in `actionCooldowns`, in table order, as (env key, minutes).

    Pairs rather than a dict, because three entries share
    `CULTIVATE_COOLDOWN_MINUTES` - an aptitude evolution and a dao-partnered
    session are paced with cultivation - so keying by the env key silently
    loses two of the twelve. An empty key is a wait the operator cannot set.

    It resolves a named constant as well as a literal. `cultivateWaitMinutes`
    became a name in v1.0.13, precisely so those three defaults cannot
    disagree with the one key that moves them, and the older reader here
    matched only digits - so naming it made the sanity check below see nine
    entries where there are twelve. A reader that silently finds less than it
    should is the shape rc.57 named; there is one of them now and both tests
    use it.
    """
    consts = {name: int(value) for name, value in
              re.findall(r"^const\s+(\w+)\s*=\s*(\d+)$", RULES, re.M)}
    body = RULES.split("var actionCooldowns = map[string]cooldownRule{", 1)[1].split("\n}", 1)[0]
    return [(key, int(minutes) if minutes.isdigit() else consts[minutes])
            for minutes, key in re.findall(r"\{(\w+),\s*\"([A-Z_]*)\"\}", body)]


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
        entries = cooldown_defaults()
        self.assertGreaterEqual(len(entries), 12, "every action that had a caller-supplied wait")
        for key, minutes in entries:
            self.assertGreater(minutes, 0, f"{key or 'an engine-only wait'}: a wait of zero is no wait")
            self.assertLessEqual(minutes, 24 * 60,
                                 f"{key or 'an engine-only wait'}: a wait longer than a day wants a reason")

    def test_the_three_statements_of_a_default_agree(self):
        """Every shipped wait is written three times; they must say the same thing.

        This used to pin each default to the number Python sent before rc.56
        moved ownership, with the reason *"a live world must not change pace
        because ownership moved"*. That reason was spent the release it was
        written: ownership moved, the numbers have been the engine's since, and
        what was left was a gate that went red the moment the owner retuned the
        pace - which v1.0.13 is what found, changing the cultivate wait to 30.
        A gate that pins how a rule is written rather than that it holds fails
        exactly when the decision behind it is taken again (v1.0.8).

        The rule worth holding is the one that could actually cost something.
        The engine's compose service takes an explicit `environment:` allowlist
        with its own `:-` fallback, so a default changed in Go and not in
        compose **reaches nobody running the stack** - a fresh install and no
        server anybody has, which is the shape rc.43, rc.46, rc.49, rc.50,
        rc.51 and rc.59 each wore. `.env.example` is the third statement, and
        `migrate_env.sh` copies it into a new `.env`.
        """
        defaults: dict[str, int] = {}
        shared = []
        for key, minutes in cooldown_defaults():
            if not key:
                continue
            if defaults.setdefault(key, minutes) != minutes:
                # Three entries share the cultivate key and an operator moves
                # all three with one value, so shipped defaults that differ
                # would be a pace nobody can restore by setting it.
                shared.append(f"{key} is shipped as both {defaults[key]} and {minutes} minutes")
        self.assertFalse(shared, "entries sharing one environment key ship different defaults: " + str(shared))
        # Asserted before it is trusted (rc.57): a reader that resolved nothing
        # would make the comparison below vacuous.
        self.assertIn("CULTIVATE_COOLDOWN_MINUTES", defaults,
                      "the cooldown table could not be read off cooldown_rules.go; the gate is broken, not the tree")

        example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        offenders = []
        for key, minutes in sorted(defaults.items()):
            shipped = re.search(rf"^{key}=(\d+)$", example, re.M)
            if shipped is None or int(shipped.group(1)) != minutes:
                offenders.append(f".env.example says {key}={shipped.group(1) if shipped else 'nothing'}, the engine defaults to {minutes}")
            fallback = re.search(rf"\$\{{{key}:-(\d+)\}}", compose)
            if fallback is None or int(fallback.group(1)) != minutes:
                offenders.append(f"compose falls back to {key}={fallback.group(1) if fallback else 'nothing'}, the engine defaults to {minutes}")
        self.assertFalse(offenders, (
            "a wait is written down three times and they disagree. The compose fallback is the one "
            "that decides what a deployed stack actually serves, so a default changed only in Go "
            f"reaches nobody running it:\n  " + "\n  ".join(offenders)))

    def test_the_engine_is_given_the_keys_it_now_reads(self):
        # The engine's compose service takes an explicit allowlist and no
        # env_file, so a key it is not given is a key it cannot read - the
        # rc.39 finding that made WORLD_TIME_SCALE dead on arrival.
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        engine = compose.split("\n  xianxia-engine:", 1)[1].split("\n  xianxia-", 1)[0]
        self.assertIn("environment:", engine, "the engine service was not found in compose")
        # assertTrue over a search, not assertIn: the haystack is the whole
        # engine service and a message that has to be scrolled past is one
        # nobody reads (v1.0.8).
        missing = [key for key in re.findall(r'"([A-Z_]+_COOLDOWN_MINUTES)"', RULES) if key not in engine]
        self.assertFalse(missing, (
            "the engine reads these keys and compose never passes them, so a value set in .env "
            f"reaches nothing: {sorted(set(missing))}"))


if __name__ == "__main__":
    unittest.main()
