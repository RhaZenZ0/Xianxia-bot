"""An engine refusal reaches a player through `_explain_engine_error` (v1.27.0).

The explainer turns a raw wait into hours, and since v1.27.0 it says where a
refusal points - "travel there first" names the travel menu, "not inside a
shop" names the city's shops - so a panel can draw the place as a button.
Forty-eight command handlers sent `str(exc)` past it, so a beast refused for
loyalty, a duel, a secret realm and every sense sweep printed the engine's
bare words. This holds every `except GameEngineError` under the player-facing
commands to pass its message through the explainer, or to be named below with
the reason it reads the error rather than printing it.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SCANNED = [*sorted((ROOT / "app" / "bot" / "commands").glob("*.py")), ROOT / "app" / "bot" / "ui" / "event_scene.py"]

# file, the first line of the handler's body: why its message is not explained.
ALLOWED = {
    # The craft reply names each short material and the hall that sells it
    # (v1.0.15); it reads the engine's shortfall and builds its own lines.
    ("exploration.py", "# The engine names exactly which materials are short and by how much"),
    # The purge's scorch refusal is reworded by its own helper.
    ("exploration.py", "await interaction.followup.send(alchemy_purge_refusal(str(exc)), ephemeral=False)"),
}


def _unexplained() -> list[str]:
    out: list[str] = []
    for path in SCANNED:
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.ExceptHandler) and node.name and node.type is not None):
                continue
            if "GameEngineError" not in ast.unparse(node.type):
                continue
            uses = [n for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == node.name]
            explained = [n for n in ast.walk(node) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("_explain_engine_error")]
            if not uses or explained:
                continue
            first = text.splitlines()[node.body[0].lineno - 1].strip() if node.body else ""
            comment = text.splitlines()[node.lineno].strip()
            if (path.name, first) in ALLOWED or (path.name, comment) in ALLOWED:
                continue
            out.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return out


class EveryRefusalIsExplained(unittest.TestCase):
    def test_the_scan_sees_the_handlers(self) -> None:
        # A reader asserted before it is trusted (rc.57).
        count = sum(
            1 for path in SCANNED for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ExceptHandler) and node.type is not None and "GameEngineError" in ast.unparse(node.type)
        )
        self.assertGreater(count, 100)

    def test_no_command_prints_the_engines_bare_words(self) -> None:
        self.assertEqual(_unexplained(), [], "these handlers send a GameEngineError without _explain_engine_error")
