"""What time it is was never the caller's to say (v1.0.0-rc.48).

`RunDueRequest.GameMinute` has been accepted-and-ignored in Go since the
v0.22.2 review, with the reason written on the field: *"a scheduled tick must
not be able to tell the world what time it is."* `ForceRequest` and
`BootstrapRequest` carried the same field and **used** it, for twenty-six more
releases - so the rule held on one of three doors, and `docs/KNOWN_LIMITATIONS.md`
carried the asymmetry as a deferred Authority item, sized as "a Go change of
its own".

Nothing ever exploited it. Every caller in this tree read the engine's own
minute and sent it straight back - `wt.total_minutes`, `int(clock["game_minute"])`,
`await clock()` - which is precisely why it survived: the fault is invisible
until somebody writes the first caller that does not, and by then the engine
has aged an NPC on a number a client chose.

The Go half is `internal/simulation/caller_minute_test.go`, which sends a wild
minute and asserts the canonical one landed. This is the Python half: the
engine still *accepts* the field, so an older bot mid-upgrade keeps working,
and nothing here sends it.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

CLIENT = PROJECT_ROOT / "app" / "ops" / "game_engine.py"
# The three client methods that reach a simulation endpoint, and the one
# orchestration class that wraps them.
SIMULATION_CALLS = ("bootstrap_simulation", "run_due_simulation", "force_simulation",
                    "run_due", "force_run", "initialize")


def _python_files():
    for root in ("app", "scripts"):
        for path in sorted((PROJECT_ROOT / root).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


class NothingTellsTheEngineWhatTimeItIs(unittest.TestCase):
    def test_the_three_client_methods_take_no_minute(self):
        """Read off the signatures, so a parameter cannot creep back in."""
        tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in SIMULATION_CALLS:
                found[node.name] = [a.arg for a in node.args.args]
        for name in ("bootstrap_simulation", "run_due_simulation", "force_simulation"):
            with self.subTest(method=name):
                self.assertIn(name, found, f"{name} is gone from the engine client")
                self.assertNotIn("game_minute", found[name],
                                 f"{name} takes a minute again; the engine derives its own")

    def test_no_simulation_payload_in_the_tree_carries_a_minute(self):
        """The call sites, not just the client.

        Before rc.48 there were nine of them, each computing a minute the
        engine either ignored or - worse, on force and bootstrap - believed.
        """
        offenders = []
        pattern = re.compile(
            r"\b(" + "|".join(SIMULATION_CALLS) + r")\(([^)]*)\)", re.S)
        for path in _python_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in pattern.finditer(text):
                name, args = match.group(1), match.group(2)
                # `initialize` still takes a minute, and it is a read: it asks
                # which black markets are open. It must not pass one on, which
                # the client-signature test above is what actually holds.
                if name == "initialize":
                    continue
                if re.search(r"game_minute|total_minutes|await clock\(\)", args):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {name}({args.strip()[:60]})")
        self.assertEqual(offenders, [], (
            "these send the engine a minute for a simulation run. Go derives the canonical one "
            f"for all three endpoints since v1.0.0-rc.48: {offenders}"))

    def test_the_engine_still_accepts_the_field_it_ignores(self):
        """A rolling deploy must not be an outage.

        The wire keeps `game_minute` on all three requests: an older bot that
        still sends one gets a normal run, not a 400. It is the value that is
        refused, not the request - and the Go test named below is what proves
        the value really is thrown away, which this file cannot see.
        """
        for name in ("world.go", "bootstrap.go"):
            source = (PROJECT_ROOT / "go_core" / "internal" / "simulation" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIn('GameMinute int64 `json:"game_minute"`', source,
                              "the wire field is gone; an older bot mid-upgrade now breaks")
                self.assertIn("deliberately ignored", source,
                              "the field is there without the comment that says why")
        gate = PROJECT_ROOT / "go_core" / "internal" / "simulation" / "caller_minute_test.go"
        self.assertTrue(gate.exists(), "the Go half of this rule is gone")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
