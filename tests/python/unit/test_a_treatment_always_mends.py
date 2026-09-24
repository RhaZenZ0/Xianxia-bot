"""A treatment always mends (v1.0.16).

Reported from play as six Heart-Calming Pills, six failures and "I can't heal
injuries". The engine now mends at least one level of severity on every
treatment - the roll decides how much, never whether - and the Go tests in
`condition_treat_test.go` hold that. This holds the reply: it must read the
severity the engine wrote rather than decide the outcome off `success`, because
a failed roll that still mended must never be told "the treatment fails".
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import pytest

from tests.support import code_only

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
LAW = ROOT / "app" / "bot" / "commands" / "law.py"


def _treat_body() -> str:
    source = LAW.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "condition_treat":
            return code_only(ast.get_source_segment(source, node) or "")
    return ""


class ATreatmentAlwaysMends(unittest.TestCase):
    def test_the_reader_finds_the_handler(self) -> None:
        body = _treat_body()
        self.assertIn('"condition.treat"', body, "the reader did not find condition_treat; the gate is broken, not the tree")

    def test_the_reply_never_says_the_treatment_failed(self) -> None:
        body = _treat_body()
        self.assertNotIn("does not worsen", body, "a treatment that mended a level was told it failed")
        self.assertNotIn("treatment fails", body.lower())

    def test_the_outcome_is_read_off_the_severity_not_the_roll(self) -> None:
        body = _treat_body()
        tree = ast.parse(body.strip() or "pass")
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # The string constant, not its spelling: `ast.unparse` quotes
                # with ' and the source with ", and the first version of this
                # check matched neither and passed against the broken reply.
                keys = {c.value for c in ast.walk(node.test) if isinstance(c, ast.Constant)}
                self.assertNotIn(
                    "success", keys,
                    f"the reply branches on the roll ({ast.unparse(node.test)}); "
                    "a failed roll still mends, so read severity_after/resolved",
                )
        self.assertIn("severity_before", body, "the reply should say where the severity fell from")
        self.assertIn("severity_after", body)


if __name__ == "__main__":
    unittest.main()
