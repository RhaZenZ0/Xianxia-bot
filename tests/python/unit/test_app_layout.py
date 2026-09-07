"""The app/ package layout (v0.20.1).

Until v0.20.0 `app/` was 43 flat modules beside four packages. They are now
grouped by role - rules, ai, ops, dashboard - and the grouping carries a
layering rule. These checks pin both, the same way test_bot_package.py
pins the bot package: a module put in the wrong package, an import that
runs the wrong way, or a flat module quietly re-appearing all fail here.
"""

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"

PACKAGES = {
    "rules": {
        "advanced_catalog", "advanced_runtime", "alchemy", "aptitudes", "battle", "birthfamily",
        "black_market", "commissions", "creation_ui", "effects", "family", "fate", "game",
        "inscription", "npc_memory", "progression_systems", "quests", "realm_hubs", "samsara",
        "seclusion", "sect", "sect_manor", "sect_recruitment", "sense", "trade_receipt", "worldtime",
    },
    "ai": {"ai_router", "chat_monitor", "narrator", "narrator_context", "quest_forge", "rag"},
    "ops": {
        "config", "core_services", "game_engine", "health", "healthcheck", "http_limits", "operations",
        "performance", "release_channel", "user_budget",
    },
    "dashboard": {"server", "contract"},
}
# database_bootstrap went to app/database/bootstrap.py: it imports Database,
# which ops (a layer below database) may not.
DATABASE_EXTRA = {"bootstrap"}

# A package may import only from packages in tiers before its own (and from
# app/version.py). rules and ops share the bottom tier and may not import
# each other: rules is pure content logic, ops is pure plumbing.
TIERS = (("rules", "ops"), ("ai",), ("database",), ("simulation",), ("dashboard",), ("bot",))
LAYERS = tuple(name for tier in TIERS for name in tier)


def _app_imports(path):
    """Top-level app subpackage names a module imports (relative or absolute)."""
    out = set()
    package_dir = path.parent
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_dir
                for _ in range(node.level - 1):
                    base = base.parent
                rel = base.relative_to(APP).parts
                target = rel + (tuple(node.module.split(".")) if node.module else ())
                if not node.module:
                    for alias in node.names:
                        out.add((target + (alias.name,))[0] if target else alias.name)
                elif target:
                    out.add(target[0])
            elif node.module and node.module.split(".")[0] == "app":
                parts = node.module.split(".")
                out.add(parts[1] if len(parts) > 1 else node.names[0].name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    out.add(alias.name.split(".")[1])
    return {name for name in out if (APP / name).is_dir()}


class AppLayoutTests(unittest.TestCase):
    def test_no_flat_module_is_left_beside_the_packages(self):
        flat = sorted(p.stem for p in APP.glob("*.py"))
        self.assertEqual(flat, ["__init__", "version"])

    def test_every_package_holds_exactly_its_modules(self):
        for package, expected in PACKAGES.items():
            with self.subTest(package=package):
                found = {p.stem for p in (APP / package).glob("*.py")} - {"__init__", "__main__"}
                self.assertEqual(found, expected)
                self.assertTrue((APP / package / "__init__.py").is_file())
        self.assertTrue(DATABASE_EXTRA <= {p.stem for p in (APP / "database").glob("*.py")})

    def test_the_dashboard_still_starts_as_a_module(self):
        # docker-compose runs `python -m app.dashboard`; the package needs a
        # __main__ for that to keep meaning the server.
        main = (APP / "dashboard" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("from .server import _main", main)
        self.assertIn("asyncio.run(_main())", main)

    def test_the_entrypoints_the_containers_run_exist(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        for module in ("app.bot", "app.ops.healthcheck"):
            self.assertIn(f'"-m", "{module}"', dockerfile, module)
        for module in ("app.ops.healthcheck", "app.database.bootstrap", "app.dashboard"):
            self.assertIn(f'"-m", "{module}"', compose, module)
        for module in ("app.ops.healthcheck", "app.database.bootstrap"):
            self.assertTrue(APP.joinpath(*module.split(".")[1:]).with_suffix(".py").is_file(), module)
        self.assertTrue((APP / "database" / "bootstrap.py").is_file())

    def test_imports_run_down_the_layers_only(self):
        rank = {name: i for i, tier in enumerate(TIERS) for name in tier}
        problems = []
        for package in LAYERS:
            for path in sorted((APP / package).rglob("*.py")):
                for target in sorted(_app_imports(path)):
                    if target == package:
                        continue
                    if target not in rank or rank[target] >= rank[package]:
                        problems.append(f"{path.relative_to(PROJECT_ROOT)} imports app.{target}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_rules_is_pure(self):
        # No Discord, no HTTP client, no database: rules are functions over
        # content. (`json`, `secrets`, `dataclasses` and friends are fine.)
        banned = {"discord", "httpx", "aiosqlite", "openai", "aiohttp"}
        for path in sorted((APP / "rules").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = set()
                if isinstance(node, ast.Import):
                    names = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = {node.module.split(".")[0]}
                self.assertEqual(names & banned, set(), f"{path.name} imports {names & banned}")

    def test_the_two_file_relative_roots_still_point_at_the_repository(self):
        # Both modules moved one directory deeper; ROOT is what finds
        # dashboard/ (static files), content/world.json and data/.
        for rel in (("dashboard", "server.py"), ("database", "bootstrap.py")):
            path = APP.joinpath(*rel)
            self.assertIn("ROOT = Path(__file__).resolve().parents[2]", path.read_text(encoding="utf-8"), rel)
            self.assertEqual(path.resolve().parents[2], PROJECT_ROOT.resolve(), rel)
        self.assertTrue((PROJECT_ROOT / "dashboard" / "index.html").is_file())

    def test_the_deployment_scripts_stay_at_the_root(self):
        # v0.20.2 considered moving these under scripts/ and did not: the
        # update.sh already installed on a NAS refuses a release that lacks
        # startup.sh/stop.sh at the root, replaces only a root update.sh at
        # its commit point, and reset_database.sh calls ./stop.sh and
        # ./startup.sh by root path. They are the deployment's entry points.
        for name in ("startup.sh", "stop.sh", "update.sh", "reset_database.sh"):
            self.assertTrue((PROJECT_ROOT / name).is_file(), name)
        update = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        self.assertIn('for required in VERSION startup.sh stop.sh docker-compose.yml go_core app content;', update)
        # The stale release marker (it described v0.19.24) is history now.
        self.assertFalse((PROJECT_ROOT / "RELEASE.txt").exists())
        self.assertTrue((PROJECT_ROOT / "docs" / "migration_history" / "V019_24_RELEASE.txt").is_file())

    def test_nothing_in_the_repository_names_an_old_path(self):
        # Sources, tests, scripts and the container files. Docs are history.
        old = {f"app.{m}" for mods in PACKAGES.values() for m in mods if m not in ("server", "contract")}
        old |= {"app.dashboard_contract", "app.database_bootstrap", "app.ops.database_bootstrap"}
        old_strings = re.compile(r"""["'](%s)(?:\.|["'])""" % "|".join(re.escape(o) for o in old))
        # ... and the pathlib form: `root / "app" / "dashboard.py"` (v0.20.1
        # shipped one of these in app/dashboard/contract.py; the dashboard
        # implementation gate reported a missing file until v0.20.3).
        old_joins = re.compile(r"""/\s*["']app["']\s*/\s*["'](%s)\.py["']""" % "|".join(
            [re.escape(o.split(".", 1)[1]) for o in old if o.count(".") == 1] + ["dashboard"]
        ))
        old_paths = {o.replace(".", "/") + ".py" for o in old}
        roots = [APP, PROJECT_ROOT / "tests", PROJECT_ROOT / "scripts"]
        files = [p for r in roots for p in r.rglob("*.py") if "__pycache__" not in p.parts]
        files += [PROJECT_ROOT / n for n in ("Dockerfile", "docker-compose.yml", "Makefile", "startup.sh", "update.sh", "reset_database.sh")]
        problems = []
        for path in files:
            if path == PROJECT_ROOT / "tests" / "python" / "unit" / "test_app_layout.py":
                continue
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)) if path.suffix == ".py" else ():
                for name in (
                    [a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
                ):
                    if name in old or any(name.startswith(o + ".") for o in old):
                        problems.append(f"{path.relative_to(PROJECT_ROOT)} imports {name}")
            for o in old_paths:
                if o in text:
                    problems.append(f"{path.relative_to(PROJECT_ROOT)} mentions {o}")
            for m in old_strings.finditer(text):
                problems.append(f"{path.relative_to(PROJECT_ROOT)} mentions {m.group(1)} in a string")
            for m in old_joins.finditer(text):
                problems.append(f"{path.relative_to(PROJECT_ROOT)} joins the old path app/{m.group(1)}.py")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
