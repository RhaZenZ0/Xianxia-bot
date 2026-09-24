"""`/admin server maintenance` writes an audit row for every branch that
changes state (design rule 6).

Vacuum and Cleanup were the two that did not (v1.2.1): the content resync
was audited by the engine, and the other two ran and told nobody.
"""

from __future__ import annotations

import ast
import unittest

from tests.support import bot_function_source


class MaintenanceIsAudited(unittest.TestCase):
    def test_vacuum_and_cleanup_each_write_their_row(self):
        tree = ast.parse(bot_function_source("admin_maintenance"))
        audited = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "audit_admin":
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    audited.add(str(node.args[1].value))
        self.assertTrue(audited, "the handler calls audit_admin nowhere; the reader is broken, not the tree")
        for action in ("admin.server.vacuum", "admin.server.cleanup"):
            self.assertIn(action, audited, f"{action} changes the database and writes no admin_audit_log row")


if __name__ == "__main__":
    unittest.main()
