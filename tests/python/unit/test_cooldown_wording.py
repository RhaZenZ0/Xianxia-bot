"""Engine cooldown errors reach the player as a wait, not raw seconds (v0.21.5).

Reported: "cultivation cooldown remaining: 10520". That is the engine's exact
truth (cooldowns are wall-clock seconds set from *_COOLDOWN_MINUTES) and no
help at all. runtime._explain_engine_error rewrites the shape every engine
cooldown error shares - "<what> cooldown remaining: <seconds>" - and every
`❌ {exc}` reply in the command modules goes through it.

runtime.py imports discord, so the two pure pieces are exec'd out of its
source here rather than importing the module.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT, bot_source_files

RUNTIME = (PROJECT_ROOT / "app" / "bot" / "runtime.py").read_text(encoding="utf-8")


def _pure_namespace():
    tree = ast.parse(RUNTIME)
    keep = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in ("format_wait", "_explain_engine_error"))
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_COOLDOWN_REMAINING_RE" for t in node.targets))
    ]
    ns = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "runtime-extract", "exec"), ns)
    return ns


NS = _pure_namespace()


class FormatWaitTests(unittest.TestCase):
    def test_shapes(self):
        f = NS["format_wait"]
        self.assertEqual(f(10520), "2h 55m")
        self.assertEqual(f(10800), "3h")
        self.assertEqual(f(90), "1m 30s")
        self.assertEqual(f(60), "1m")
        self.assertEqual(f(7), "7s")
        self.assertEqual(f(0), "a moment")
        self.assertEqual(f(-5), "a moment")


class CooldownRewriteTests(unittest.TestCase):
    def test_the_reported_message_becomes_a_wait(self):
        text = NS["_explain_engine_error"](Exception("cultivation cooldown remaining: 10520"))
        self.assertEqual(text, "⏳ Cultivation is still on cooldown — ready in **2h 55m**.")
        self.assertNotIn("10520", text)

    def test_every_engine_cooldown_shape(self):
        for raw, what in (("aptitude cooldown remaining: 61", "Aptitude"), ("manual study cooldown: 5 seconds", "Manual study"),
                          ("cooldown active: 90 seconds", "This action"), ("hunt cooldown remaining: 3600", "Hunt")):
            text = NS["_explain_engine_error"](Exception(raw))
            self.assertTrue(text.startswith(f"⏳ {what} is still on cooldown"), text)
            self.assertNotRegex(text, r"\b\d{3,}\b", "no raw second counts reach the player")

    def test_other_errors_pass_through(self):
        self.assertEqual(NS["_explain_engine_error"](Exception("not enough qi")), "not enough qi")

    def test_every_engine_error_reply_in_the_command_modules_is_explained(self):
        offenders = []
        for path in bot_source_files():
            rel = str(path.relative_to(PROJECT_ROOT / "app" / "bot"))
            if not rel.startswith(("commands/", "ui/", "admin/")):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if 'f"❌ {exc}"' in line:
                    offenders.append(f"{rel}:{lineno}")
        self.assertEqual(offenders, [], "raw engine errors reach players at:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
