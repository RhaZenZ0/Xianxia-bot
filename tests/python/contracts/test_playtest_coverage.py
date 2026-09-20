"""The playtest touches everything (v1.0.0-rc.35): the two coverage gates.

Two harnesses run before a release: `scripts/playtest_engine.py` drives the
engine's operations over HTTP and `scripts/playtest_discord.py` presses the
bot's panels under a simulated Discord. Until now nothing said which
operations or leaves they had to drive, so a new one was uncovered until
somebody noticed. These gates enumerate the surface from the code - the
engine's own allowlist and dispatch switch, the live hub definitions - and
hold each harness to it, with one explicit deferred set per harness in which
every entry carries its reason. The gate is what makes "everything" a fact:
it fails the day an operation or a leaf appears that neither harness drives
nor the deferred set names, and it fails the day a deferral goes stale.
"""

from __future__ import annotations

import ast
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import PROJECT_ROOT

GAME = PROJECT_ROOT / "go_core" / "internal" / "game"
BOT = PROJECT_ROOT / "app" / "bot"
ENGINE_SCRIPT = PROJECT_ROOT / "scripts" / "playtest_engine.py"
DISCORD_SCRIPT = PROJECT_ROOT / "scripts" / "playtest_discord.py"
OPERATION = re.compile(r"^[a-z_]+(?:\.[a-z_]+)+$")
# The op strings `scripts/playtest_engine.py` hands to the engine go through
# these callables and nothing else: `act` (an authoritative mutation),
# `act_free` (the same with the wait cleared first, v1.0.0-rc.56 - the engine
# owns the cooldowns now, so the harness asks the GM to clear them instead of
# sending `cooldown_seconds: 0`), `gm` (a GM lever), `audited` (a GM lever
# whose audit row is then checked), `query` (an authoritative read) and the
# client's own two.
DRIVERS = {"act", "act_free", "gm", "audited", "query", "action", "authoritative_action"}


#: An operation the engine harness can only drive into a designed refusal,
#: with the reason. See `test_every_operation_is_resolved_or_says_why_not`
#: for why this one is not empty.
REFUSAL_ONLY_OPERATIONS: dict[str, str] = {
    "artifact.awaken": (
        "an artifact spirit wants Bond 3, and bond rises only through `artifact.bond` on a "
        "wall-clock cooldown with no GM lever to set it - so a success is a wait, not a step"
    ),
    "meridian.heal": (
        "mending wants a ruptured meridian, and the only writer of one is a failed forced "
        "breakthrough under the Force stance; no lever ruptures one on demand"
    ),
}


def _map_literal(source: str, name: str) -> set[str]:
    """Every `"op": true,` entry of one `var name = map[string]bool{...}`."""
    head = source.index(f"var {name} = map[string]bool{{")
    body = source[head: source.index("\n}\n", head)]
    return {m.group(1) for m in re.finditer(r'^\s*"([a-z_.]+)":\s*true,', body, re.M)}


def engine_operations() -> set[str]:
    """Every operation the engine answers: the allowlist `isAuthoritativeOperation`
    reads, plus the dispatch switch in `actions.go` (the GM levers and the six
    bridge operations the bot calls directly)."""
    authoritative = (GAME / "authoritative.go").read_text(encoding="utf-8")
    ops = _map_literal(authoritative, "authoritativeMutations") | _map_literal(authoritative, "authoritativeQueries")
    actions = (GAME / "actions.go").read_text(encoding="utf-8")
    switch = actions[actions.index("switch req.Operation {"):]
    switch = switch[: switch.index("\n\tdefault:")]
    ops |= {m.group(1) for m in re.finditer(r'^\tcase "([a-z_.]+)":', switch, re.M)}
    return ops


def module_constant(path: Path, name: str):
    """A module-level literal assignment, read without importing the script
    (the Discord harness may only import the bot after it has set the
    environment, and the engine harness imports nothing at module level)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name} has no module-level {name}")


def driven_operations() -> set[str]:
    """The first string argument of every driver call in the engine harness.
    Not a substring scan: an operation named in a comment, a step title or an
    `expect_error` is not driven."""
    tree = ast.parse(ENGINE_SCRIPT.read_text(encoding="utf-8"))
    driven: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in DRIVERS and OPERATION.match(first.value):
            driven.add(first.value)
    return driven


def resolved_operations() -> set[str]:
    """The operations the harness drives to something other than a refusal
    (v1.0.0-rc.58).

    rc.35 defined "driven" as "named as the first string argument of a driver
    call", which is exactly right for catching an operation nothing calls. It
    is not enough for catching one that is only ever called into a designed
    refusal: `law.technique` read as covered off a single
    `expect_error="required"` step while the engine hard-errored on the one
    technique that could reach a success, for twenty-five releases.

    A driver call is *resolved* unless it sits inside a call carrying
    `expect_error` - which is where the keyword lives, since the harness
    writes `step(report, title, act(...), expect_error=...)`. The first
    version of this walked every call node and marked the inner `act(...)`
    resolved on its own account, so every operation looked resolved and the
    gate passed on anything. It marks the enclosing call's driver arguments
    now, which is the only way round that is not a guess.
    """
    tree = ast.parse(ENGINE_SCRIPT.read_text(encoding="utf-8"))

    def operation_of(call: ast.Call) -> str:
        if not call.args:
            return ""
        first = call.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return ""
        func = call.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        return first.value if name in DRIVERS and OPERATION.match(first.value) else ""

    refused: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(kw.arg == "expect_error" for kw in node.keywords):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and child is not node and operation_of(child):
                refused.add(id(child))

    resolved: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and id(node) not in refused:
            if op := operation_of(node):
                resolved.add(op)
    return resolved


def live_leaves() -> set[str]:
    """Every action path a hub can reach, admin included: the walk the
    checklist gate uses (`test_playtest_gate`), which leaves admin off the
    board because a tester cannot open it. The sweep can."""
    env = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
           "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
    with patch.dict(os.environ, env):
        import importlib

        hubs = importlib.import_module("app.bot.hubs")
    return {action.path for definition in hubs.REGISTERED_HUBS
            for page in definition.pages for action in hubs._leaf_actions(page)}


class TheEngineHalfDrivesEveryOperation(unittest.TestCase):
    def test_the_operations_are_read_off_the_engine(self):
        ops = engine_operations()
        self.assertGreaterEqual(len(ops), 240, "the allowlist and the switch hold 246 operations at rc.34")
        for op in ops:
            self.assertRegex(op, OPERATION)
        for known in ("family.lesson", "cooldown.status", "admin.world.advance_time", "sect.discover", "npc.found"):
            self.assertIn(known, ops)

    def test_every_operation_is_driven_or_deferred_with_a_reason(self):
        deferred = module_constant(ENGINE_SCRIPT, "DEFERRED_OPERATIONS")
        uncovered = engine_operations() - driven_operations() - set(deferred)
        self.assertEqual(sorted(uncovered), [], "the engine playtest drives none of these, and DEFERRED_OPERATIONS does not say why")

    def test_every_operation_is_resolved_or_says_why_not(self):
        """Driven means resolved, not merely called.

        `REFUSAL_ONLY_OPERATIONS` is **not** empty on the day it is written,
        unlike `SOURCELESS_ITEMS`, `UNGRANTABLE_QUESTS`, `ALLOWED_UNREACHABLE`
        and `DEFERRED_OPERATIONS`. Tightening rc.35's rule reveals a backlog
        rc.35's own wording created, and pretending otherwise would mean
        leaving the rule loose. Each entry says why the harness cannot reach a
        success.
        """
        deferred = module_constant(ENGINE_SCRIPT, "DEFERRED_OPERATIONS")
        unresolved = (engine_operations() - resolved_operations()
                      - set(deferred) - set(REFUSAL_ONLY_OPERATIONS))
        self.assertEqual(sorted(unresolved), [],
                         "these are driven only into designed refusals, and nothing says why")

    def test_no_refusal_only_entry_is_stale(self):
        resolved = resolved_operations()
        ops = engine_operations()
        self.assertEqual(sorted(set(REFUSAL_ONLY_OPERATIONS) - ops), [],
                         "listed as refusal-only but not an operation the engine has")
        self.assertEqual(sorted(set(REFUSAL_ONLY_OPERATIONS) & resolved), [],
                         "listed as refusal-only and driven to a success: the entry is stale")
        for op, reason in REFUSAL_ONLY_OPERATIONS.items():
            with self.subTest(op=op):
                self.assertGreaterEqual(len(str(reason).strip()), 20, "an entry carries its reason")

    def test_the_resolved_read_still_sees_the_harness(self):
        """A reader that silently finds nothing makes every assertion above it
        vacuous, and the refusal set is the half that would go quiet."""
        resolved = resolved_operations()
        self.assertGreaterEqual(len(resolved), 150, "the resolved read has stopped seeing the harness")
        for known in ("cultivation.train", "law.technique", "alchemy.purge"):
            self.assertIn(known, resolved, f"{known} is driven to a success and the read missed it")

    def test_no_deferral_is_stale(self):
        deferred = module_constant(ENGINE_SCRIPT, "DEFERRED_OPERATIONS")
        self.assertIsInstance(deferred, dict)
        ops, driven = engine_operations(), driven_operations()
        self.assertEqual(sorted(set(deferred) - ops), [], "deferred but not an operation the engine has")
        self.assertEqual(sorted(set(deferred) & driven), [], "deferred and driven: the deferral is stale")
        for op, reason in deferred.items():
            with self.subTest(op=op):
                self.assertGreaterEqual(len(str(reason).strip()), 12, "a deferral carries its reason")


class TheDiscordHalfPressesEveryLeaf(unittest.TestCase):
    def test_every_deferred_leaf_is_live_and_has_a_reason(self):
        deferred = module_constant(DISCORD_SCRIPT, "DEFERRED_LEAVES")
        self.assertIsInstance(deferred, dict)
        live = live_leaves()
        self.assertGreaterEqual(len(live), 290, "245 player leaves and 54 admin leaves at rc.34")
        self.assertEqual(sorted(set(deferred) - live), [], "deferred but no hub reaches it: the deferral is stale")
        for path, reason in deferred.items():
            with self.subTest(path=path):
                self.assertGreaterEqual(len(str(reason).strip()), 12, "a deferral carries its reason")

    def test_the_sweep_walks_the_live_definitions_and_holds_the_wiring(self):
        """The sweep is generic: it presses whatever the hubs register, so a
        new leaf is covered the day it is registered. The run itself holds
        that every leaf was pressed or printed its lock line."""
        text = DISCORD_SCRIPT.read_text(encoding="utf-8")
        for marker in ("REGISTERED_HUBS", "._leaf_actions(", "._inputs_for(", "DEFERRED_LEAVES",
                       "WIRING_FAILURE_TEXTS", "More actions", "every reachable leaf was pressed"):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_the_failure_texts_it_holds_against_are_the_bots_own(self):
        """A designed refusal names what is missing; the hub's failure text
        never varies by leaf. The sweep fails a leaf on exactly the strings the
        bot prints when a handler raised, read here off the source so a
        reworded string cannot leave the sweep holding against stale text."""
        held = set(module_constant(DISCORD_SCRIPT, "WIRING_FAILURE_TEXTS"))
        hubs = (BOT / "hubs.py").read_text(encoding="utf-8")
        surface = (BOT / "surface.py").read_text(encoding="utf-8")
        printed = set(re.findall(r'text = "(❌ [^"]+)"', hubs)) | set(re.findall(r'message = "(Something went wrong[^"]+)"', surface))
        self.assertEqual(len(printed), 3, printed)
        self.assertEqual(held, printed)


if __name__ == "__main__":
    unittest.main()
