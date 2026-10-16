"""The automation toggles are one list, read by four places.

`admin.automation.set` refuses a system its own table does not name. The Go table
(`adminAutomationSet`'s `defaults`) lacked `npc_consignments` and `secret_realms`
while the Discord command offered both and the dashboard listed them, so a GM
who pressed either got "unknown automation system" - and the bot's own reader
(`get_automation_settings`) did not know `secret_realms` existed. Four copies of
one vocabulary, three of them edited when the simulation grew a switch.

So the Go table is the authority and the other three are held equal to it, by
key and by default: the Python reader's defaults, the Discord choices the GM
picks from and the dashboard's toggle list. The Go is read by regex, since
Python cannot call it; each reader asserts it found something before the
comparison is trusted.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

ACTIONS_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "actions.go").read_text(encoding="utf-8")


def go_defaults() -> dict[str, bool]:
    start = ACTIONS_GO.index("func adminAutomationSet(")
    body = ACTIONS_GO[start : ACTIONS_GO.index("\n}\n", start)]
    block = re.search(r"defaults := map\[string\]bool\{(.*?)\n\t\}", body, re.S)
    if block is None:
        return {}
    # Comments name keys in prose; read the key: value lines only.
    return {m.group(1): m.group(2) == "true" for m in re.finditer(r'^\s*"(\w+)":\s*(true|false),', block.group(1), re.M)}


def _dict_of_bools(node: ast.Dict) -> dict[str, bool]:
    return {
        k.value: v.value
        for k, v in zip(node.keys, node.values)
        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant) and isinstance(v.value, bool)
    }


def _function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _first_dict_assigned(fn: ast.AST, target: str) -> dict[str, bool]:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if any(isinstance(t, ast.Name) and t.id == target for t in node.targets):
                return _dict_of_bools(node.value)
    return {}


def core_defaults() -> dict[str, bool]:
    tree = ast.parse((PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8"))
    return _first_dict_assigned(_function(tree, "get_automation_settings"), "defaults")


def dashboard_defaults() -> dict[str, bool]:
    tree = ast.parse((PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8"))
    found: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            got = _first_dict_assigned(node, "automation")
            if got:
                found = got
    return found


def discord_choices() -> set[str]:
    tree = ast.parse((PROJECT_ROOT / "app" / "bot" / "admin" / "inspect_sim.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ADMIN_AUTOMATION_CHOICES" for t in node.targets):
            out = set()
            for call in node.value.elts:
                for kw in call.keywords:
                    if kw.arg == "value" and isinstance(kw.value, ast.Constant):
                        out.add(kw.value.value)
            return out
    return set()


class AutomationTogglesAgree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A reader that finds nothing makes every comparison below vacuous.
        cls.go = go_defaults()
        cls.core = core_defaults()
        cls.dashboard = dashboard_defaults()
        cls.discord = discord_choices()
        for name, got in (("the Go table", cls.go), ("core.py", cls.core), ("the dashboard list", cls.dashboard), ("the Discord choices", cls.discord)):
            assert len(got) >= 10, f"{name} was read as {sorted(got)}; the reader is broken, not the tree"
        assert "ai_routine_narration" in cls.go and "merchants" in cls.go, "the Go reader missed known keys"

    def _same(self, label, got):
        missing = sorted(set(self.go) - set(got))
        extra = sorted(set(got) - set(self.go))
        self.assertEqual(
            (missing, extra),
            ([], []),
            f"{label} and the engine's adminAutomationSet disagree: the engine's table has {missing} that {label} lacks, "
            f"and {label} has {extra} the engine would refuse as an unknown automation system",
        )

    def test_core_reads_every_system_the_engine_accepts(self):
        self._same("get_automation_settings", self.core)

    def test_the_dashboard_lists_every_system_the_engine_accepts(self):
        self._same("the dashboard's automation list", self.dashboard)

    def test_discord_offers_exactly_the_systems_the_engine_accepts(self):
        self._same("ADMIN_AUTOMATION_CHOICES", self.discord)

    def test_the_defaults_agree(self):
        for label, got in (("core.py", self.core), ("the dashboard", self.dashboard)):
            self.assertEqual(
                {k: v for k, v in got.items() if k in self.go},
                self.go,
                f"{label} states a different default than the engine's table",
            )


if __name__ == "__main__":
    unittest.main()
