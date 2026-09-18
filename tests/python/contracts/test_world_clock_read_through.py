"""The world clock is read, never computed, on this side (v1.0.0-rc.39).

`Database.get_world_clock` was the last Python-side copy of an engine rule: it
read the anchor out of `world_state`, did the arithmetic here, seeded a default
row when there was none, and **re-anchored the row whenever the stored scale
disagreed with `SETTINGS.world_time_scale`** - so a rate a GM set on the
dashboard was silently undone by the next `/time`, `/cultivate` or narration.
The dashboard kept a third copy of the same arithmetic, and `/admin world
advancetime` sent the env scale on every call whether or not anybody had asked
to change it.

These tests read the source the way `test_cooldowns_command.py` does: an
arithmetic that is gone must stay gone, and a key that has changed owners must
be named on the side that now reads it.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP = PROJECT_ROOT / "app"
SCRIPTS = PROJECT_ROOT / "scripts"


def _python_sources() -> list[Path]:
    return sorted(
        path
        for root in (APP, SCRIPTS)
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _function_source(path: Path, name: str) -> str:
    """One function's code, by name, with its docstring removed.

    The docstring is dropped on purpose: these tests forbid *doing* a thing,
    and a comment explaining what the code used to do must not read as the
    code still doing it.
    """
    text = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]
            return "\n".join(ast.get_source_segment(text, stmt) or "" for stmt in body)
    raise AssertionError(f"{name} not found in {path}")


class TheEngineOwnsTheClock(unittest.TestCase):
    def test_the_database_layer_has_no_clock_left(self):
        core = (APP / "database" / "core.py").read_text(encoding="utf-8")
        self.assertNotIn("get_world_clock", core)
        self.assertNotIn("world_clock", core)

    def test_no_file_under_app_or_scripts_does_the_anchor_arithmetic(self):
        # `anchor_real_ts` is the field the whole rule hinges on. Go is the only
        # place that may touch it; naming it here means somebody is deriving the
        # minute rather than reading it.
        offenders = [
            str(path.relative_to(PROJECT_ROOT))
            for path in _python_sources()
            if "anchor_real_ts" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], f"clock arithmetic outside Go: {offenders}")

    def test_the_two_read_throughs_compute_nothing(self):
        current = _function_source(APP / "bot" / "runtime.py", "current_world_time")
        dashboard = _function_source(APP / "dashboard" / "server.py", "_world_clock")
        for name, source in (("current_world_time", current), ("_world_clock", dashboard)):
            for forbidden in ("time.time()", "datetime.now", "anchor_real_ts", "elapsed"):
                self.assertNotIn(forbidden, source, f"{name} derives the clock: {forbidden}")
        self.assertIn("ENGINE.world_clock()", current)
        self.assertIn("self._engine.world_clock()", dashboard)

    def test_the_client_has_the_one_door_and_the_narrator_uses_it(self):
        client = (APP / "ops" / "game_engine.py").read_text(encoding="utf-8")
        self.assertIn('"world.clock"', client)
        self.assertIn("async def world_clock", client)
        context = (APP / "ai" / "narrator_context.py").read_text(encoding="utf-8")
        self.assertIn("await self.engine.world_clock()", context)
        self.assertNotIn("world_time_scale", context)

    def test_the_engine_registers_the_query_and_never_seeds_from_it(self):
        queries = (PROJECT_ROOT / "go_core" / "internal" / "game" / "world_status_queries.go").read_text(encoding="utf-8")
        allowlist = (PROJECT_ROOT / "go_core" / "internal" / "game" / "authoritative.go").read_text(encoding="utf-8")
        self.assertIn('"world.clock"', queries)
        self.assertIn('"world.clock"', allowlist)
        # The read-only loader, not the one that inserts a default row.
        clock_case = queries[queries.index('case "world.clock":"'[:-1]):]
        clock_case = clock_case[: clock_case.index('case "world.recent_actions":')]
        self.assertIn("loadCanonicalWorldClock", clock_case)
        self.assertNotIn("INSERT", clock_case)


class TheScaleIsTheEnginesKeyNow(unittest.TestCase):
    def test_python_settings_no_longer_read_it(self):
        config = (APP / "ops" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("WORLD_TIME_SCALE", config)
        self.assertNotIn("world_time_scale", config)

    def test_the_engine_reads_it_and_compose_passes_it(self):
        clock = (PROJECT_ROOT / "go_core" / "internal" / "game" / "effect_authority.go").read_text(encoding="utf-8")
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        docs = (PROJECT_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
        self.assertIn('os.Getenv("WORLD_TIME_SCALE")', clock)
        self.assertIn("WORLD_TIME_SCALE:", compose)
        self.assertIn("WORLD_TIME_SCALE=", env)
        self.assertIn("`WORLD_TIME_SCALE`", docs)
        # The engine service takes an allowlist, not env_file - so the key has
        # to be in *its* block, not merely somewhere in the file.
        engine_block = compose[compose.index("xianxia-engine:"): compose.index("xianxia-db-init:")]
        self.assertIn("WORLD_TIME_SCALE:", engine_block)

    def test_advance_time_changes_the_rate_only_when_asked(self):
        source = _function_source(APP / "bot" / "admin" / "world_ops.py", "admin_advance_time")
        self.assertIn("if scale is not None:", source)
        self.assertNotIn("SETTINGS.world_time_scale", source)
        # The scale reaches the payload only under that guard.
        assignments = re.findall(r'payload\["scale"\]\s*=', source)
        self.assertEqual(len(assignments), 1, source)

    def test_the_time_card_prints_the_worlds_rate_not_this_processs(self):
        source = _function_source(APP / "bot" / "commands" / "sense.py", "world_time_command")
        self.assertIn("ENGINE.world_clock()", source)
        self.assertNotIn("SETTINGS.world_time_scale", source)
        self.assertIn("stopped", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
