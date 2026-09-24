"""A refused VACUUM is told as a wait, not a wiring failure (v1.2.3).

The engine refuses a VACUUM at once when a database session is between its
execute and its commit, rather than sitting out busy_timeout and failing. The
Discord handler must pass that refusal on in words - the generic wrapper would
print one of the three wiring-failure strings over a condition that is neither
wiring nor failure - and must write no audit row for it, since nothing changed.
"""

from __future__ import annotations

import ast
import unittest

from tests.support import bot_function_source


class ARefusedVacuumIsAWait(unittest.TestCase):
    def _vacuum_branch(self) -> ast.If:
        tree = ast.parse(bot_function_source("admin_maintenance"))
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and 'action.value == "vacuum"' in ast.unparse(node.test).replace("'", '"'):
                return node
        self.fail("the vacuum branch could not be found; the reader is broken, not the tree")

    def test_the_busy_refusal_is_caught_and_said(self):
        branch = self._vacuum_branch()
        handlers = [h for n in ast.walk(branch) if isinstance(n, ast.Try) for h in n.handlers]
        names = {ast.unparse(h.type) for h in handlers if h.type is not None}
        self.assertIn("RemoteDatabaseError", names, "DB.vacuum()'s refusal is not caught, so a busy session prints a wiring failure")
        text = "\n".join(ast.unparse(h) for h in handlers)
        self.assertIn("sessions_busy", text, "the handler does not tell the busy refusal from a real failure")
        self.assertNotIn("audit_admin", text, "a refused vacuum changed nothing and must not be audited")


if __name__ == "__main__":
    unittest.main()
