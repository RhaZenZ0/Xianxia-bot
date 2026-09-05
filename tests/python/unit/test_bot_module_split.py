"""Static integrity checks for the app/bot package decomposition.

app/bot/main.py cannot be imported in the build sandbox (no discord.py), and it
is the file every Discord command routes through, so a refactor that moves code
out of it cannot be validated by running it here. These checks stand in for
that: they use symtable, which does real scope analysis, to prove that every
module still resolves every name it reads - the specific failure a move like
this produces is a NameError at import, which takes the whole bot down at start.
"""
from tests.support import PROJECT_ROOT
import ast
import collections
import builtins
import re
import symtable
import unittest

BOT = PROJECT_ROOT / "app" / "bot"
# Phase 10 (v0.20.0): the command-surface wiring left main.py for surface.py.
# Guards written as "main.py imports X back" from phases 2-9 read the wiring
# file, whichever file that is.
WIRING = BOT / "surface.py"
MODULES = [
    "main.py",
    "runtime.py",
    "hubs.py",
    "registry.py",
    "commands/family.py",
    "commands/sect.py",
    # Split stage 4 (v0.19.32 tree) - added in phase 1 of the main.py split plan;
    # these had no guard at all between their creation and that release.
    "commands/artifact.py",
    "commands/beast.py",
    "commands/boss.py",
    "commands/duel.py",
    "commands/equipment.py",
    "commands/formation.py",
    # Split phase 2 (v0.19.34): the shared service singletons and formatters.
    "services.py",
    "formatting.py",
    # Split phase 3 (v0.19.35): location knowledge, world gating, NPC whereabouts.
    "locations.py",
    # Split phase 4 (v0.19.39): the rest of the plumbing.
    "discovery.py",
    "character_state.py",
    "channels.py",
    "threads.py",
    # Split phase 5 (v0.19.40): the admin core.
    "admin/core.py",
    # Split phase 6 (v0.19.41): the admin server layer.
    "admin/channel_messages.py",
    "admin/bugs_forum.py",
    "admin/server_setup.py",
    # Split phase 7 (v0.19.42): the admin operations and the shared pickers.
    "pickers.py",
    "admin/world_ops.py",
    "admin/inspect_sim.py",
    # Split phase 8 (v0.19.43): the shared UI and the bot class.
    "ui/event_scene.py",
    "ui/creation.py",
    "bot.py",
    # Split phase 9a (v0.19.44): the first player-command domains.
    "commands/aptitude.py",
    "commands/territory.py",
    "commands/secretrealm.py",
    "commands/cultivation.py",
    # Split phase 9b (v0.19.45).
    "commands/character.py",
    "commands/economy.py",
    "commands/abode.py",
    "commands/exploration.py",
    "commands/battle.py",
    "commands/law.py",
    "commands/scene.py",
    "commands/sense.py",
    # Phase 10 (v0.20.0): the wiring.
    "surface.py",
]
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}


def module_globals(path):
    """(defined names, names read from module scope) for one module."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        defined.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    defined.add(sub.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.comprehension):
            # Comprehension targets at module scope are reported as globals by
            # symtable; they are bound by the comprehension, not read from the
            # module, so collect them as defined rather than chase them as bugs.
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    defined.add(sub.id)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for sub in ast.walk(item.optional_vars):
                        if isinstance(sub, ast.Name):
                            defined.add(sub.id)

    read: set[str] = set()
    stack = [symtable.symtable(source, path.name, "exec")]
    while stack:
        table = stack.pop()
        for symbol in table.get_symbols():
            if symbol.is_global():
                read.add(symbol.get_name())
        stack.extend(table.get_children())
    return defined, read


class ModuleResolutionTests(unittest.TestCase):
    def test_every_relative_import_points_at_a_file_that_exists(self):
        """symtable proves a name is *bound*; it cannot see that
        `from .runtime import DB` in commands/exploration.py binds it from a
        module that does not exist at that depth (phase 9c wrote exactly that
        and every name-resolution guard passed). Resolve each relative import
        against the package on disk and require a module, package or, for
        `from . import x`, a member file to be there. Each imported name is
        checked against the target file's definitions when it is a module."""
        problems = []
        for name in MODULES:
            path = BOT / name
            package_dir = path.parent
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if not isinstance(node, ast.ImportFrom) or node.level == 0:
                    continue
                base = package_dir
                for _ in range(node.level - 1):
                    base = base.parent
                target = base.joinpath(*node.module.split(".")) if node.module else base
                if target.with_suffix(".py").is_file():
                    source = target.with_suffix(".py").read_text(encoding="utf-8")
                    defined = {
                        n.name for n in ast.parse(source).body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    }
                    for n in ast.parse(source).body:
                        if isinstance(n, ast.Assign):
                            defined |= {ast.unparse(x) for x in n.targets}
                        elif isinstance(n, ast.AnnAssign):
                            defined.add(ast.unparse(n.target))
                        elif isinstance(n, (ast.Import, ast.ImportFrom)):
                            defined |= {(a.asname or a.name).split(".")[0] for a in n.names}
                    for alias in node.names:
                        if alias.name != "*" and alias.name not in defined:
                            problems.append(f"app/bot/{name}:{node.lineno} imports {alias.name} which {target.with_suffix('.py').relative_to(PROJECT_ROOT)} does not define")
                elif target.is_dir() and (target / "__init__.py").is_file():
                    for alias in node.names:
                        if alias.name != "*" and not ((target / alias.name).with_suffix(".py").is_file() or (target / alias.name).is_dir()):
                            init = (target / "__init__.py").read_text(encoding="utf-8")
                            if not re.search(rf"(?m)^(?:def |class |async def )?{re.escape(alias.name)}\b|\b{re.escape(alias.name)}\b", init):
                                problems.append(f"app/bot/{name}:{node.lineno} imports {alias.name} from package {target.relative_to(PROJECT_ROOT)} which has no such member")
                else:
                    problems.append(f"app/bot/{name}:{node.lineno}: {'.' * node.level}{node.module or ''} does not exist relative to {package_dir.relative_to(PROJECT_ROOT)}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_every_bot_module_resolves_every_global_it_reads(self):
        """The failure mode of a bad move is a NameError at import time."""
        for name in MODULES:
            with self.subTest(module=name):
                defined, read = module_globals(BOT / name)
                missing = sorted(read - defined - BUILTINS)
                self.assertEqual(
                    missing,
                    [],
                    f"app/bot/{name} reads names it does not define or import: {missing}",
                )


def _top_level_bindings(tree):
    """Names bound by module-level statements, ignoring nested scopes."""
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


class DefinitionOrderTests(unittest.TestCase):
    """Module-level code runs top-down, so a moved name must land in order.

    This is the check that was missing, and it cost a failed deploy. The name
    resolution test above proves a name EXISTS somewhere in the module; it says
    nothing about whether it exists *yet* at the point it is used. The first cut
    of runtime.py had `DB = Database(ROOT / ...)` above `ROOT = Path(...)`, which
    resolves fine as a set of names and raises NameError the instant the module
    is imported - the bot died on start and the updater rolled back.
    """

    def _module_level_order_violations(self, path):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Only genuine TOP-LEVEL bindings can have an ordering problem. Names
        # bound inside a comprehension or lambda are irrelevant here - and
        # module_globals() deliberately counts comprehension targets as defined
        # for the resolution check above, so reusing it would flag every
        # `[... for key in ...]` at module scope as a use-before-assignment.
        module_names = _top_level_bindings(tree)
        bound: set[str] = set()
        violations = []
        for node in tree.body:
            # Names this statement READS before anything it binds takes effect.
            value_nodes = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                if node.value is not None:
                    value_nodes = [node.value]
            elif isinstance(node, ast.Expr):
                value_nodes = [node.value]
            for value in value_nodes:
                for sub in ast.walk(value):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                        if sub.id in module_names and sub.id not in bound:
                            violations.append((node.lineno, sub.id))
            # Now record what this statement binds.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    for sub in ast.walk(target):
                        if isinstance(sub, ast.Name):
                            bound.add(sub.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                bound.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
        return violations

    def module_defined(self, path):
        defined, _ = module_globals(path)
        return defined

    def test_no_module_level_name_is_used_before_it_is_assigned(self):
        for name in MODULES:
            with self.subTest(module=name):
                violations = self._module_level_order_violations(BOT / name)
                self.assertEqual(
                    violations,
                    [],
                    f"app/bot/{name} uses a module-level name before assigning it "
                    f"(NameError at import): {violations}",
                )


class WrappingDecoratorAnnotationTests(unittest.TestCase):
    """A wrapping decorator drags annotation resolution into its OWN module.

    This one cost a deploy, and it is not obvious. `serialized_user_action` lives
    in runtime.py and wraps ~119 command callbacks defined in main.py.
    functools.wraps copies __name__ and __doc__ but CANNOT copy __globals__ -
    that belongs to the code object's defining module - so the callback discord.py
    finally receives carries runtime.py's globals, not main.py's.

    main.py uses `from __future__ import annotations`, so every annotation is a
    string, and discord.utils.resolve_annotation evals those strings against
    callback.__globals__. A signature reading
        target: app_commands.Choice[str]
    therefore needs `app_commands` importable *in runtime.py*. It was trimmed
    there as an unused import, and the bot died at startup with
    `NameError: name 'app_commands' is not defined`.

    So: a module defining a wrapping decorator must import every name used in the
    ANNOTATIONS of the functions it decorates - not just the names its own code
    runs. That is what this checks.
    """

    WRAPPING_DECORATORS = {"serialized_user_action": "runtime.py"}

    def _annotation_names(self, tree):
        """Per decorator: the module-level names its wrapped callbacks annotate with."""
        needed = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                name = getattr(decorator, "id", None)
                if name not in self.WRAPPING_DECORATORS:
                    continue
                annotations = [a.annotation for a in node.args.args if a.annotation]
                if node.returns is not None:
                    annotations.append(node.returns)
                for annotation in annotations:
                    for sub in ast.walk(annotation):
                        if isinstance(sub, ast.Name):
                            needed.setdefault(name, set()).add(sub.id)
                        elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                            needed.setdefault(name, set()).add(sub.value.id)
        return needed

    def _imports_of_module(self, filename):
        names = set()
        for node in ast.parse((BOT / filename).read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
        return names

    def test_decorator_modules_import_what_their_callbacks_annotate_with(self):
        needed = {}
        for filename in MODULES:
            for decorator, names in self._annotation_names(
                ast.parse((BOT / filename).read_text(encoding="utf-8"))
            ).items():
                needed.setdefault(decorator, set()).update(names)
        self.assertTrue(needed, "no wrapped callbacks found - has the decorator been renamed?")
        for decorator, names in needed.items():
            home = self.WRAPPING_DECORATORS[decorator]
            available = self._imports_of_module(home) | BUILTINS
            missing = sorted(names - available)
            self.assertEqual(
                missing,
                [],
                f"app/bot/{home} defines @{decorator}, so discord.py resolves its "
                f"callbacks' string annotations against {home}'s globals - but "
                f"{home} does not import {missing}. The bot will raise NameError at "
                f"startup. Import them there even if they look unused.",
            )

    def test_the_decorator_still_lives_where_we_think(self):
        source = (BOT / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("def serialized_user_action", source)
        self.assertIn("@wraps(", source, "the __globals__ hazard only exists for a wrapping decorator")


class ImportDirectionTests(unittest.TestCase):
    """runtime.py sits below main.py and must never import back up into it.

    That one rule is what makes the rest of the decomposition possible: command
    modules can import the shared core without dragging main.py in, so there is
    no cycle to unpick later.
    """

    def _imports_of(self, name):
        tree = ast.parse((BOT / name).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
        return found

    def test_runtime_does_not_import_main(self):
        for module in self._imports_of("runtime.py"):
            self.assertNotIn("main", module.split("."), f"runtime.py imports {module}")

    def test_registry_and_hubs_stay_independent_of_main(self):
        for name in ("registry.py", "hubs.py"):
            for module in self._imports_of(name):
                self.assertNotIn("main", module.split("."), f"{name} imports {module}")

    def test_main_takes_the_shared_core_from_runtime(self):
        main = (BOT / "main.py").read_text(encoding="utf-8")
        wiring = WIRING.read_text(encoding="utf-8")
        # Phase 9e (v0.19.48) took the last command out of main.py and phase
        # 10 (v0.20.0) moved the wiring to surface.py. The composition root
        # reads SETTINGS; the wiring reads DB. Neither reads the handler
        # helpers, and they must NOT be imported - an orphaned import is how
        # main.py used to hide a name nothing resolved.
        self.assertIn("from .runtime import SETTINGS", main)
        self.assertRegex(wiring, r"from \.runtime import [^\n]*\bDB\b")
        for source in (main, wiring):
            for name in ("ENGINE", "require_character", "reply_long", "serialized_user_action"):
                self.assertNotRegex(source, rf"(?m)^\s+{name},$|import [^\n]*\b{name}\b", name)

    def test_no_module_below_main_imports_it_at_module_level(self):
        """The package-wide form of the per-split checks below.

        main.py imports every module under app/bot at its own module level, so a
        module-level `from ..main import` anywhere else is a genuine cycle. The
        family/sect classes assert this for one file each; this covers every
        file the package has now and every file the split adds later, without
        needing a new class per move. Call-time imports inside functions are
        allowed (and counted down to zero by the split plan's phase 4).
        """
        for path in sorted(BOT.rglob("*.py")):
            if path.name in ("main.py", "__init__.py", "__main__.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        "main", (node.module or "").split("."),
                        f"{path.relative_to(BOT)} imports main at module level (line {node.lineno})",
                    )


class PublicSurfaceTests(unittest.TestCase):
    def test_main_still_exposes_what_the_package_reexports(self):
        """app/bot/__init__.py imports these; losing one breaks `python -m app.bot`."""
        tree = ast.parse((BOT / "main.py").read_text(encoding="utf-8"))
        top_level = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                top_level.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        top_level.add(target.id)
            elif isinstance(node, ast.ImportFrom):
                # Since phase 8 (v0.19.43) XianxiaBot and bot are defined in
                # bot.py and re-exported through main.py; a name imported at
                # module level is exposed exactly as a definition is.
                top_level.update(alias.asname or alias.name for alias in node.names)
        for name in ("XianxiaBot", "bot", "register_command_surface", "run"):
            self.assertIn(name, top_level, f"app/bot/__init__.py imports main.{name}")

    def test_the_shared_core_is_defined_exactly_once(self):
        """Moved, not copied - a leftover duplicate would silently shadow."""
        main_defined, _ = module_globals(BOT / "main.py")
        runtime_tree = ast.parse((BOT / "runtime.py").read_text(encoding="utf-8"))
        runtime_top = set()
        for node in runtime_tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                runtime_top.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        runtime_top.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                runtime_top.add(node.target.id)

        main_tree = ast.parse((BOT / "main.py").read_text(encoding="utf-8"))
        redefined = []
        for node in main_tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            redefined += [n for n in names if n in runtime_top]
        self.assertEqual(redefined, [], f"main.py redefines names that moved to runtime.py: {redefined}")


if __name__ == "__main__":
    unittest.main()


FAMILY = BOT / "commands" / "family.py"


class FamilySplitTests(unittest.TestCase):
    """Split stage 2: /family moved to app/bot/commands/family.py.

    Stage 1 cost two failed deploys - a name used above its assignment, then
    annotations resolving in the wrong module because functools.wraps cannot copy
    __globals__. The generic checks above cover both for every module in MODULES,
    including this one. What is left is the shape specific to this move.
    """

    def setUp(self):
        self.family = FAMILY.read_text(encoding="utf-8")
        self.main = (BOT / "main.py").read_text(encoding="utf-8")

    def test_the_module_exists_and_owns_the_group(self):
        self.assertTrue(FAMILY.exists())
        self.assertIn("family_group=app_commands.Group(", self.family)

    def test_all_fourteen_leaf_commands_moved(self):
        self.assertEqual(self.family.count("@registered_group_command(family_group,"), 14)

    def test_main_no_longer_defines_any_of_them(self):
        # Moved, not copied: a leftover duplicate would register twice.
        self.assertNotIn("@registered_group_command(family_group,", self.main)
        self.assertNotIn("family_group=app_commands.Group(", self.main)

    def test_main_takes_the_group_from_the_new_module(self):
        wiring = WIRING.read_text(encoding="utf-8")
        self.assertIn("from .commands.family import family_group", wiring)
        self.assertIn('"family": family_group,', wiring)

    def test_family_never_imports_main_at_module_level(self):
        """A module-level `from ..main import` here is a real import cycle.

        The four names still owned by main.py are imported inside the single
        function that uses each, which runs long after main.py finished
        importing. This asserts the distinction rather than trusting it.
        """
        tree = ast.parse(self.family)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    "main", (node.module or ""),
                    f"module-level import of main at line {node.lineno}",
                )

    def test_the_deferred_imports_are_inside_functions_and_few(self):
        tree = ast.parse(self.family)
        deferred = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "main" in (node.module or ""):
                deferred.extend(alias.name for alias in node.names)
        self.assertEqual(
            sorted(deferred),
            [
                # Empty since split phase 4 (v0.19.39): SIM (phase 2), then
                # _get_thread and the two thread helpers (phase 4) all moved
                # below main.py and are module-level imports now.
            ],
        )

    def test_shared_constants_moved_to_runtime_rather_than_being_copied(self):
        runtime = (BOT / "runtime.py").read_text(encoding="utf-8")
        # GENDER_CHOICES is used by /begin, /family and the admin override, so it
        # belongs to the shared core; duplicating it would let the two drift.
        self.assertIn("GENDER_CHOICES = [", runtime)
        self.assertNotIn("GENDER_CHOICES = [", self.main)
        self.assertNotIn("GENDER_CHOICES = [", self.family)

    def test_a_family_only_constant_moved_with_the_family(self):
        self.assertIn("CHILD_CULTIVATION_AWAKENING_AGE = 12", self.family)
        self.assertNotIn("CHILD_CULTIVATION_AWAKENING_AGE = 12", self.main)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        # A "move" that leaves the original behind is the failure this catches.
        self.assertLess(len(self.main.splitlines()), 13000)
        self.assertGreater(len(self.family.splitlines()), 400)


SECT = BOT / "commands" / "sect.py"


class SectSplitTests(unittest.TestCase):
    """Split stage 3: /sect moved to app/bot/commands/sect.py.

    Same generic coverage as stage 2 (definition order, name resolution,
    annotation resolution, import direction) applies automatically via
    MODULES above. What is left here is the shape specific to this move,
    including the two wrinkles stage 2 did not have:

    * ``carried_item_autocomplete`` is shared with main.py's own /storage
      commands AND referenced as a bare ``@app_commands.autocomplete(...)``
      argument here - which evaluates at module-IMPORT time, not call time -
      so it was promoted to runtime.py rather than duplicated or deferred.
    * ``_sect_recruitment_at_location`` is the mirror image of the four
      main.py-owned deferred names: it is DEFINED in this module but used
      once elsewhere in main.py (the /explore road-discovery flow), so
      main.py imports it back at module level, same as it does ``sect_group``.
    """

    def setUp(self):
        self.sect = SECT.read_text(encoding="utf-8")
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.runtime = (BOT / "runtime.py").read_text(encoding="utf-8")

    def test_the_module_exists_and_owns_the_groups(self):
        self.assertTrue(SECT.exists())
        for group in ("sect_group", "sect_manor_group", "sect_disciple_group", "sect_recruitment_group"):
            self.assertIn(f"{group} = app_commands.Group(", self.sect)

    def test_all_twenty_five_leaf_commands_moved(self):
        self.assertEqual(self.sect.count("@registered_group_command(sect_group,"), 11)
        self.assertEqual(self.sect.count("@registered_group_command(sect_manor_group,"), 3)
        self.assertEqual(self.sect.count("@registered_group_command(sect_disciple_group,"), 5)
        self.assertEqual(self.sect.count("@registered_group_command(sect_recruitment_group,"), 6)

    def test_main_no_longer_defines_any_of_them(self):
        # Moved, not copied: a leftover duplicate would register twice.
        # Regex with a boundary, not a plain substring check: "sect_group = "
        # is also a substring of "admin_sect_group = ", which legitimately
        # stays in main.py (a different, unmoved group).
        for group in ("sect_group", "sect_manor_group", "sect_disciple_group", "sect_recruitment_group"):
            self.assertNotIn(f"@registered_group_command({group},", self.main)
            self.assertIsNone(
                re.search(rf"(?<!\w){re.escape(group)} = app_commands\.Group\(", self.main),
                f"{group} still defined in main.py",
            )

    def test_main_takes_the_group_and_the_helper_from_the_new_module(self):
        wiring = WIRING.read_text(encoding="utf-8")
        self.assertIn("from .commands.sect import sect_group", wiring)
        self.assertIn('"sect": sect_group,', wiring)
        # The helper's one caller (/explore) left main.py in split phase 9c
        # (v0.19.46); it is taken from sect.py by commands/exploration.py now.
        self.assertNotIn("_sect_recruitment_at_location", self.main)
        exploration = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")
        self.assertIn("from .sect import _sect_recruitment_at_location", exploration)

    def test_sect_never_imports_main_at_module_level(self):
        """A module-level `from ..main import` here is a real import cycle.

        main.py imports this module (for sect_group and
        _sect_recruitment_at_location) at ITS module level, so this module
        cannot import main.py back at module level without a cycle. The five
        names still owned by main.py are imported inside the one function
        that uses each, which runs long after both modules finished importing.
        """
        tree = ast.parse(self.sect)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    "main", (node.module or ""),
                    f"module-level import of main at line {node.lineno}",
                )

    def test_the_deferred_imports_are_inside_functions_and_few(self):
        tree = ast.parse(self.sect)
        deferred = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "main" in (node.module or ""):
                deferred.extend(alias.name for alias in node.names)
        self.assertEqual(
            sorted(deferred),
            [
                # Empty since split phase 4 (v0.19.39): SIM (phase 2), the
                # location helpers (phase 3) and the sect-abode thread helpers
                # (phase 4) all moved below main.py.
            ],
        )

    def test_carried_item_autocomplete_moved_to_runtime_rather_than_duplicated(self):
        # Referenced as `@app_commands.autocomplete(item=carried_item_autocomplete)`
        # in both main.py and here - that evaluates at module-import time, so a
        # deferred `from ..main import` (the pattern above) cannot reach it.
        self.assertIn("async def carried_item_autocomplete(", self.runtime)
        self.assertNotIn("async def carried_item_autocomplete(", self.main)
        self.assertNotIn("async def carried_item_autocomplete(", self.sect)
        # The other module-level user moved from main.py to commands/economy.py
        # in split phase 9b (v0.19.45); the point - one definition, imported
        # at module level wherever it is decorated with - is unchanged.
        economy = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
        self.assertIn("carried_item_autocomplete", economy)
        self.assertIn("carried_item_autocomplete,", self.sect)

    def test_a_sect_only_constant_moved_with_the_sect(self):
        self.assertIn('ADDRESS_STYLE_CHOICES = [', self.sect)
        self.assertNotIn('ADDRESS_STYLE_CHOICES = [', self.main)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 12300)
        self.assertGreater(len(self.sect.splitlines()), 800)


SERVICES = BOT / "services.py"
FORMATTING = BOT / "formatting.py"


class Phase2SplitTests(unittest.TestCase):
    """Split phase 2 (v0.19.34): the service singletons and shared formatters.

    The generic checks in MODULES cover resolution, order and annotations for
    both new files. What is left here is the shape specific to this move: the
    names really left main.py, they are imported back by name, the layering
    (runtime <- services <- formatting <- main) holds, and the two hooks and
    two copies that only existed because these lived in main.py are gone.
    """

    SINGLETONS = (
        "GUILD", "SCENES", "NPC_RELATIONSHIPS", "QUESTS", "EXPLORATION", "COMBAT",
        "NARRATOR_QUEUE", "SIM", "AI_ROUTER", "NARRATOR", "NARRATOR_CONTEXT", "ALERTS",
        "PLAYER_PROPERTY_TYPE_CHOICES", "PLAYER_PROPERTY_FACILITY_KEYS",
        "PLAYER_PROPERTY_FACILITY_LABELS",
    )
    FORMATTERS = (
        "player_property_emoji", "player_property_facility_lines", "human_duration",
        "roll_line", "effective_attribute",
    )

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.services = SERVICES.read_text(encoding="utf-8")
        self.formatting = FORMATTING.read_text(encoding="utf-8")

    def test_the_singletons_are_defined_in_services_and_nowhere_else(self):
        for name in self.SINGLETONS:
            with self.subTest(name=name):
                self.assertIn(name, _top_level_bindings(ast.parse(self.services)))
                for other in bot_modules_except("services.py"):
                    self.assertNotIn(
                        name, _assigned_at_top_level(other),
                        f"{name} is still assigned in {other.name}",
                    )

    def test_the_formatters_are_defined_in_formatting_and_nowhere_else(self):
        for name in self.FORMATTERS:
            with self.subTest(name=name):
                self.assertIn(f"def {name}(", self.formatting)
                for other in bot_modules_except("formatting.py"):
                    self.assertNotIn(
                        f"def {name}(", other.read_text(encoding="utf-8"),
                        f"{name} is still defined in {other.name}",
                    )

    def test_main_imports_every_moved_name_back(self):
        # The wiring reads GUILD and SIM as bare globals (surface.py since
        # phase 10; main.py before).
        imported = {
            alias.asname or alias.name
            for node in ast.parse(WIRING.read_text(encoding="utf-8")).body
            if isinstance(node, ast.ImportFrom) and node.module in ("services", "formatting")
            for alias in node.names
        }
        # Names leave this import-back as the code that read them leaves main.py
        # (AI_ROUTER in phase 6, ALERTS in phase 8, the property constants and
        # formatters in phase 9b); the resolution guard is what proves nothing
        # is missing. This pins that whatever main.py does import from the two
        # modules is a name they define.
        self.assertEqual(sorted(imported - set(self.SINGLETONS + self.FORMATTERS)), [])
        self.assertIn("SIM", imported)
        self.assertIn("GUILD", imported)

    def test_layering_runtime_services_formatting(self):
        def imports_of(source):
            return {
                node.module for node in ast.parse(source).body
                if isinstance(node, ast.ImportFrom) and node.module
            }
        self.assertNotIn("services", imports_of((BOT / "runtime.py").read_text(encoding="utf-8")))
        self.assertNotIn("formatting", imports_of((BOT / "runtime.py").read_text(encoding="utf-8")))
        self.assertNotIn("formatting", imports_of(self.services))
        self.assertNotIn("main", imports_of(self.services))
        self.assertNotIn("main", imports_of(self.formatting))
        self.assertIn("runtime", imports_of(self.services))
        self.assertIn("services", imports_of(self.formatting))

    def test_no_command_module_reaches_into_main_for_sim_any_more(self):
        for path in sorted((BOT / "commands").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from ..main import SIM", source, path.name)
            if "SIM." in source:
                imported = {
                    alias.name for node in ast.parse(source).body
                    if isinstance(node, ast.ImportFrom) and node.module == "services"
                    for alias in node.names
                }
                self.assertIn("SIM", imported, path.name)

    def test_roll_line_is_no_longer_duplicated(self):
        # Split stage 4 copied roll_line into beast.py and duel.py rather than
        # import main.py. With formatting.py below them there is one copy.
        definitions = [
            path for path in BOT.rglob("*.py")
            if "def roll_line(" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([p.name for p in definitions], ["formatting.py"])
        for name in ("beast.py", "duel.py"):
            self.assertIn("from ..formatting import roll_line", (BOT / "commands" / name).read_text(encoding="utf-8"))

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 11600)
        self.assertGreater(len(self.services.splitlines()), 90)
        self.assertGreater(len(self.formatting.splitlines()), 35)


def bot_modules_except(filename):
    return [p for p in BOT.rglob("*.py") if p.name != filename]


def _assigned_at_top_level(path):
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


LOCATIONS = BOT / "locations.py"


class Phase3SplitTests(unittest.TestCase):
    """Split phase 3 (v0.19.35): locations.py.

    Same shape as Phase2SplitTests. The extra point specific to this move is
    the two autocompletes: they are named as bare decorator arguments in the
    modules that use them, so they must be importable at module level from
    below main.py - a deferred import could never have served them.
    """

    HELPERS = (
        "current_npc_location", "_world_min_realm_index", "_world_is_unlocked",
        "_known_locations", "_location_is_visible", "location_autocomplete",
        "local_npc_autocomplete",
    )

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.locations = LOCATIONS.read_text(encoding="utf-8")

    def test_the_helpers_are_defined_in_locations_and_nowhere_else(self):
        for name in self.HELPERS:
            with self.subTest(name=name):
                self.assertRegex(self.locations, rf"(?m)^(?:async )?def {name}\(")
                for other in bot_modules_except("locations.py"):
                    self.assertNotRegex(
                        other.read_text(encoding="utf-8"), rf"(?m)^(?:async )?def {name}\(",
                        f"{name} is still defined in {other.name}",
                    )

    def test_main_imports_every_moved_name_back(self):
        imported = {
            alias.asname or alias.name
            for node in ast.parse(self.main).body
            if isinstance(node, ast.ImportFrom) and node.module == "locations"
            for alias in node.names
        }
        # The helpers left main.py with their readers: location_autocomplete
        # in 9c (v0.19.46), the rest in 9e (v0.19.48); `_world_min_realm_index`
        # turned out to have no reader in the wiring at all and was dropped in
        # the phase-10 sweep (v0.20.0). Neither the composition root nor the
        # wiring imports anything from locations now; every helper is imported
        # at module level by the module that decorates with it (test below).
        self.assertEqual(imported, set())
        wiring_imports = {
            n.module for n in ast.parse(WIRING.read_text(encoding="utf-8")).body if isinstance(n, ast.ImportFrom)
        }
        self.assertNotIn("locations", wiring_imports)

    def test_locations_sits_below_main_and_above_services(self):
        modules = {
            node.module for node in ast.parse(self.locations).body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("main", modules)
        self.assertNotIn("formatting", modules)
        self.assertIn("runtime", modules)
        self.assertIn("services", modules)
        for lower in ("runtime.py", "services.py", "formatting.py"):
            self.assertNotIn(
                "locations",
                {n.module for n in ast.parse((BOT / lower).read_text(encoding="utf-8")).body
                 if isinstance(n, ast.ImportFrom) and n.module},
                f"{lower} must not import locations",
            )

    def test_the_autocompletes_are_module_level_imports_where_used(self):
        # Any module that decorates with these must import them at module level.
        for path in bot_modules_except("locations.py"):
            source = path.read_text(encoding="utf-8")
            for name in ("location_autocomplete", "local_npc_autocomplete"):
                if f"autocomplete({name}" in source or f"={name})" in source or f"={name}," in source:
                    tree = ast.parse(source)
                    top = {
                        alias.name for node in tree.body
                        if isinstance(node, ast.ImportFrom) for alias in node.names
                    }
                    self.assertIn(name, top, f"{path.name} uses {name} without a module-level import")

    def test_sect_no_longer_reaches_into_main_for_these(self):
        sect = (BOT / "commands" / "sect.py").read_text(encoding="utf-8")
        self.assertNotIn("from ..main import _known_locations", sect)
        self.assertNotIn("from ..main import current_npc_location", sect)
        self.assertIn("from ..locations import _known_locations, current_npc_location", sect)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 11500)
        self.assertGreater(len(self.locations.splitlines()), 100)


class Phase4SplitTests(unittest.TestCase):
    """Split phase 4 (v0.19.39): discovery, character_state, channels, threads.

    The last of the shared plumbing leaves main.py, and with it the last
    call-time `from ..main import` anywhere in the package. From here on a
    command module that needs main.py is a bug, not a workaround.
    """

    MOVED = {
        "discovery.py": (
            "LOCATION_DISCOVERY_IMAGES", "location_discovery_image_path", "location_discovery_embed",
            "send_location_discovery_image", "travel_first_discovers_location",
        ),
        "character_state.py": (
            "settle_all_seclusions", "current_effect_modifiers", "sync_pill_toxicity_effect",
            "_npc_name_mentioned", "_remember_freeform_npc_scene",
        ),
        "channels.py": (
            "_event_archive_minutes", "_resolve_text_channel", "_ensure_realm_access_roles",
            "ensure_realm_hub_channels", "event_channels", "home_scene_channel",
            "exploration_scene_channel", "configured_info_channel", "_get_thread",
            "send_long_to_thread", "configured_log_channel", "configured_begin_channel",
            "post_server_log",
            "_report_game_ui_error",  # joined channels.py in phase 8 (v0.19.43)
        ),
        "threads.py": (
            "_expedition_thread_intro", "ensure_expedition_thread", "open_expedition_thread_after_exit",
            "ensure_birth_family_household_thread", "_sect_abode_name", "ensure_sect_abode_record",
            "ensure_sect_abode_thread_for", "_private_scene_for_thread",
            "active_private_location_thread", "ensure_abode_thread",
        ),
    }

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")

    def test_each_name_is_defined_in_its_module_and_nowhere_else(self):
        for module, names in self.MOVED.items():
            bound = _top_level_bindings(ast.parse((BOT / module).read_text(encoding="utf-8")))
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertIn(name, bound)
                    for other in bot_modules_except(module):
                        other_bound = _top_level_bindings(ast.parse(other.read_text(encoding="utf-8")))
                        # Other modules may IMPORT the name; they may not define it.
                        if name in other_bound:
                            src = other.read_text(encoding="utf-8")
                            self.assertNotRegex(
                                src, rf"(?m)^(?:async )?def {name}\(|^{name}\s*[:=]",
                                f"{name} is still defined in {other.name}",
                            )

    def test_main_imports_every_moved_name_it_still_reads(self):
        # Phase 6 (v0.19.41) moved the server-setup code that read three of
        # these, so main.py no longer needs them all; the resolution guard
        # above is what proves nothing is missing. This pins that what IS
        # imported comes from the right modules.
        for node in ast.parse(self.main).body:
            if isinstance(node, ast.ImportFrom) and node.module in self.MOVED_MODULES:
                for alias in node.names:
                    self.assertIn(alias.name, self.MOVED[node.module + ".py"], alias.name)

    MOVED_MODULES = ("discovery", "character_state", "channels", "threads")

    def test_layering(self):
        def imports_of(name):
            return {
                node.module for node in ast.parse((BOT / name).read_text(encoding="utf-8")).body
                if isinstance(node, ast.ImportFrom) and node.module
            }
        for name in self.MOVED:
            self.assertNotIn("main", imports_of(name), name)
        self.assertIn("channels", imports_of("threads.py"))
        self.assertNotIn("threads", imports_of("channels.py"))
        for lower in ("runtime.py", "services.py", "formatting.py", "locations.py"):
            for upper in self.MOVED:
                self.assertNotIn(upper[:-3], imports_of(lower), f"{lower} must not import {upper}")

    def test_no_call_time_import_of_main_remains_anywhere(self):
        for path in bot_source_files_under_bot():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and "main" in (node.module or "").split("."):
                    self.fail(f"{path.relative_to(BOT)} line {node.lineno} still imports main")

    def test_family_and_sect_import_the_helpers_normally(self):
        family = (BOT / "commands" / "family.py").read_text(encoding="utf-8")
        sect = (BOT / "commands" / "sect.py").read_text(encoding="utf-8")
        self.assertIn("from ..threads import ensure_birth_family_household_thread, open_expedition_thread_after_exit", family)
        self.assertIn("from ..channels import _get_thread", family)
        self.assertIn("from ..threads import ensure_sect_abode_record, ensure_sect_abode_thread_for", sect)

    def test_the_hub_error_reporter_still_comes_from_channels(self):
        # The bot instance moved to bot.py in phase 8 (v0.19.43); the wiring
        # line moved with it and still points at channels.post_server_log.
        bot_module = (BOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("bot.hub_error_reporter = post_server_log", bot_module)
        self.assertIn("from .channels import post_server_log", bot_module)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 10950)
        for module, floor in (("discovery.py", 60), ("character_state.py", 100), ("channels.py", 180), ("threads.py", 300)):
            self.assertGreater(len((BOT / module).read_text(encoding="utf-8").splitlines()), floor, module)


def bot_source_files_under_bot():
    return [p for p in sorted(BOT.rglob("*.py")) if p.name not in ("main.py", "__init__.py", "__main__.py")]


ADMIN_CORE = BOT / "admin" / "core.py"


class Phase5SplitTests(unittest.TestCase):
    """Split phase 5 (v0.19.40): the admin core moves into a new admin package."""

    NAMES = (
        "_admin_command_option_summary", "log_admin_command_invocation", "require_admin",
        "audit_admin", "admin_group", "admin_server_group", "admin_world_group",
        "admin_player_group", "admin_sect_group", "admin_family_group", "admin_npc_group",
        "admin_sim_group",
    )

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.core = ADMIN_CORE.read_text(encoding="utf-8")

    def test_the_package_exists_and_owns_the_core(self):
        self.assertTrue((BOT / "admin" / "__init__.py").is_file())
        bound = _top_level_bindings(ast.parse(self.core))
        for name in self.NAMES:
            self.assertIn(name, bound, name)

    def test_nothing_else_defines_them(self):
        for other in bot_modules_except("core.py"):
            src = other.read_text(encoding="utf-8")
            for name in self.NAMES:
                self.assertNotRegex(src, rf"(?m)^(?:async )?def {name}\(|^{name}\s*=", f"{name} in {other.name}")

    def test_main_imports_every_name_back(self):
        imported = {
            alias.asname or alias.name
            for node in ast.parse(WIRING.read_text(encoding="utf-8")).body
            if isinstance(node, ast.ImportFrom) and node.module == "admin.core"
            for alias in node.names
        }
        # audit_admin left the import-back in phase 8 (v0.19.43): the last
        # admin handler in main.py that called it moved to admin/world_ops.
        # The phase-10 sweep (v0.20.0) found three more with no reader left
        # in the wiring (they were orphaned imports in main.py) and dropped
        # them; the seven page groups and require_admin are what /admin reads.
        unread = {"audit_admin", "admin_group", "_admin_command_option_summary", "log_admin_command_invocation"}
        self.assertEqual(sorted(set(self.NAMES) - imported - unread), [])
        self.assertEqual(sorted(imported & unread), [])

    def test_the_core_reads_only_from_below(self):
        modules = {
            node.module for node in ast.parse(self.core).body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertEqual(modules, {"channels", "runtime", "discord", "__future__"})

    def test_the_admin_root_keeps_its_permission_and_guild_lock(self):
        # The only /admin root Discord ever sees; losing either flag on the
        # move would expose GM commands to every member.
        block = self.core[self.core.index("admin_group = app_commands.Group("):]
        block = block[: block.index(")") + 1]
        self.assertIn("guild_only=True", block)
        self.assertIn("default_permissions=discord.Permissions(administrator=True)", block)
        for sub in ("server", "world", "player", "sect", "family", "npc", "simulation"):
            self.assertRegex(self.core, rf'name="{sub}",\s*\n\s*description=[^\n]+\n\s*parent=admin_group,')

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 10800)
        self.assertGreater(len(self.core.splitlines()), 140)


class Phase6SplitTests(unittest.TestCase):
    """Split phase 6 (v0.19.41): the admin server layer.

    Three modules, one ordering rule: channel_messages and bugs_forum are
    leaves, server_setup imports both. clear_managed_channel_messages sits
    with the channel-message code in main.py but re-runs the complete server
    setup, so it lives in server_setup - the other placement is a cycle.
    """

    SERVER_COMMANDS = ("bind_channels", "status", "basechannels", "setup", "realmhubs",
                       "observability", "ai_status", "chat_digest")

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.cm = (BOT / "admin" / "channel_messages.py").read_text(encoding="utf-8")
        self.bf = (BOT / "admin" / "bugs_forum.py").read_text(encoding="utf-8")
        self.ss = (BOT / "admin" / "server_setup.py").read_text(encoding="utf-8")

    def _imports(self, source):
        return {n.module for n in ast.parse(source).body if isinstance(n, ast.ImportFrom) and n.module}

    def test_layering(self):
        for leaf in (self.cm, self.bf):
            self.assertFalse({"server_setup", "main", "core"} & self._imports(leaf))
        self.assertNotIn("bugs_forum", self._imports(self.cm))
        self.assertNotIn("channel_messages", self._imports(self.bf))
        self.assertTrue({"channel_messages", "bugs_forum", "core"} <= self._imports(self.ss))
        self.assertNotIn("main", self._imports(self.ss))

    def test_clear_managed_channel_messages_lives_with_the_setup_it_reruns(self):
        self.assertIn("async def clear_managed_channel_messages", self.ss)
        self.assertNotIn("async def clear_managed_channel_messages", self.cm)
        self.assertIn("_run_complete_server_setup(guild, create_missing=True)", self.ss)

    def test_the_eight_server_commands_moved(self):
        for name in self.SERVER_COMMANDS:
            with self.subTest(command=name):
                self.assertRegex(self.ss, rf'registered_group_command\(\s*admin_server_group,\s*name="{name}"')
                self.assertNotRegex(self.main, rf'registered_group_command\(\s*admin_server_group,\s*name="{name}"')

    def test_main_still_imports_server_setup_so_the_commands_register(self):
        # Registration is a side effect of import. Dropping this line makes
        # the eight commands vanish from /admin with no error anywhere.
        # bot.py has imported both since phase 8 (v0.19.43); main.py's copies
        # were orphans and went in the phase-10 sweep (v0.20.0).
        bot_src = (BOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("from .admin.server_setup import dashboard_discord_control", bot_src)
        self.assertIn("from .admin.channel_messages import XianxiaInfoView", bot_src)

    def test_the_dashboard_bridge_no_longer_names_the_bot_class(self):
        code = ast.parse(self.ss)
        names = {n.id for n in ast.walk(code) if isinstance(n, ast.Name)}
        self.assertNotIn("XianxiaBot", names)
        self.assertIn("client: commands.Bot", self.ss)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 9300)
        self.assertGreater(len(self.ss.splitlines()), 900)
        self.assertGreater(len(self.cm.splitlines()), 300)
        self.assertGreater(len(self.bf.splitlines()), 130)


class Phase7SplitTests(unittest.TestCase):
    """Split phase 7 (v0.19.42): the admin operations and the shared pickers."""

    WORLD_OPS = ("karma", "setsect", "removesect", "setmaster", "clearmaster", "sectrank",
                 "masterattention", "grantstorage", "grantcurrency", "grant", "advancetime", "maintenance")
    INSPECT_SIM = ("inspect", "teleport", "revive", "clearbattle", "familyinspect", "npcinspect",
                   "toggle", "automation", "status", "run", "interval", "region", "npc", "sect",
                   "market", "clan", "actions", "world", "backup", "audit")
    # events/spawnrealm/closeevent waited for phase 8 (spawn_event_thread and
    # the bot instance had to be importable from below); they are in world_ops now.
    STILL_IN_MAIN = ()

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.wo = (BOT / "admin" / "world_ops.py").read_text(encoding="utf-8")
        self.isim = (BOT / "admin" / "inspect_sim.py").read_text(encoding="utf-8")
        self.pickers = (BOT / "pickers.py").read_text(encoding="utf-8")

    def _imports(self, source):
        return {n.module for n in ast.parse(source).body if isinstance(n, ast.ImportFrom) and n.module}

    def test_the_commands_moved_where_the_plan_says(self):
        for name in self.WORLD_OPS:
            self.assertRegex(self.wo, rf'registered_group_command\(\s*admin_\w+_group,\s*name="{name}"', name)
        for name in self.INSPECT_SIM:
            self.assertRegex(self.isim, rf'registered_group_command\(\s*admin_\w+_group,\s*name="{name}"', name)
        for name in self.WORLD_OPS + self.INSPECT_SIM:
            self.assertNotRegex(self.main, rf'registered_group_command\(\s*admin_\w+_group,\s*name="{name}"', name)
        for name in ("events", "spawnrealm", "closeevent"):
            self.assertRegex(self.wo, rf'registered_group_command\(\s*admin_world_group,\s*name="{name}"', name)
            self.assertNotRegex(self.main, rf'registered_group_command\(\s*admin_world_group,\s*name="{name}"', name)

    def test_all_admin_commands_are_still_registered_exactly_once(self):
        # Losing or duplicating one in the move is silent at import time
        # (ACTIONS.bind raises on a duplicate, but only when the bot starts).
        pattern = re.compile(r'registered_group_command\(\s*(admin_\w+_group),\s*name="([^"]+)"')
        seen = collections.Counter()
        for path in bot_modules_except("__init__.py"):
            for group, name in pattern.findall(path.read_text(encoding="utf-8")):
                seen[(group, name)] += 1
        self.assertEqual(sum(seen.values()), 43)
        self.assertEqual([k for k, v in seen.items() if v != 1], [])

    def test_pickers_sits_between_services_and_the_command_modules(self):
        self.assertEqual(self._imports(self.pickers) - {"__future__", "discord"}, {"runtime", "services"})
        self.assertIn("async def auction_currency_autocomplete", self.pickers)
        self.assertIn("def _market_item_matches", self.pickers)
        self.assertNotIn("async def auction_currency_autocomplete", self.main)
        self.assertNotIn("def _market_item_matches", self.main)
        # Named as a decorator argument in two modules: both import it at module
        # level. (The auction side moved from main.py to commands/economy.py in
        # phase 9b, v0.19.45.)
        economy = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
        for src in (economy, self.wo):
            self.assertIn("auction_currency_autocomplete", src)
            self.assertIn("from ..pickers import", src)

    def test_main_imports_both_modules_for_their_registration_side_effect(self):
        wiring = WIRING.read_text(encoding="utf-8")
        self.assertIn("from .admin import inspect_sim as _admin_inspect_sim", wiring)
        self.assertIn("from .admin import world_ops as _admin_world_ops", wiring)

    def test_layering(self):
        for src in (self.wo, self.isim, self.pickers):
            self.assertNotIn("main", self._imports(src))
        self.assertIn("world_ops", self._imports(self.isim))       # admin_sect_name_hub_options
        self.assertNotIn("inspect_sim", self._imports(self.wo))

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 8500)
        self.assertGreater(len(self.wo.splitlines()), 320)
        self.assertGreater(len(self.isim.splitlines()), 480)


class Phase8SplitTests(unittest.TestCase):
    """Split phase 8 (v0.19.43): the shared UI, the bot class, and the last
    three /admin commands. After this, main.py defines no classes."""

    EVENT_SCENE = ("EVENT_ACTION_RULES", "_event_action_keys", "EventActionSelect", "EventNpcTalkModal",
                   "EventNpcSelect", "EventNpcSelectView", "EventSystemsSelect", "EventSystemsView",
                   "EventSceneView", "_event_scene_location", "_event_scene_profile",
                   "spawn_event_thread", "spawn_system_event_thread")
    CREATION = ("CharacterModal", "_birth_family_preview_embed", "BirthFamilyPreviousButton",
                "BirthFamilyChooseButton", "BirthFamilyNextButton", "CultivationStyleSelect",
                "BirthSexSelect", "BirthFamilyBackButton", "BirthFamilyConfirmButton", "BirthFamilyView")

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.es = (BOT / "ui" / "event_scene.py").read_text(encoding="utf-8")
        self.cr = (BOT / "ui" / "creation.py").read_text(encoding="utf-8")
        self.bot = (BOT / "bot.py").read_text(encoding="utf-8")

    def _imports(self, source):
        return {n.module for n in ast.parse(source).body if isinstance(n, ast.ImportFrom) and n.module}

    def test_main_defines_no_classes_any_more(self):
        classes = [n.name for n in ast.parse(self.main).body if isinstance(n, ast.ClassDef)]
        # The battle / scene-action / dashboard / exploration views move with
        # their commands in phase 9; this pins that the phase-8 set is gone.
        for name in self.EVENT_SCENE + self.CREATION + ("XianxiaBot",):
            self.assertNotIn(name, classes)
            self.assertNotRegex(self.main, rf"(?m)^(?:async )?def {name}\(|^class {name}\(|^{name}\s*[:=]")

    def test_each_name_lives_in_its_module(self):
        for name in self.EVENT_SCENE:
            self.assertIn(name, _top_level_bindings(ast.parse(self.es)), name)
        for name in self.CREATION:
            self.assertIn(name, _top_level_bindings(ast.parse(self.cr)), name)
        bound = _top_level_bindings(ast.parse(self.bot))
        self.assertIn("XianxiaBot", bound)
        self.assertIn("bot", bound)

    def test_the_event_view_reaches_main_owned_panels_through_the_registry(self):
        # The plan's three back-edges. Until phase 9 moves the panels with
        # their commands, the view dispatches by name; main.py binds the names.
        for direct in ("_scene_action_targets(", "scene_action_panel(", "_battle_panel("):
            code = "\n".join(l for l in self.es.splitlines() if not l.strip().startswith("#") and '"""' not in l)
            self.assertNotIn(direct, code, f"{direct} is called directly in ui/event_scene.py")
        # Each binding lives wherever its panel now lives: "battle_panel"
        # moved to commands/battle.py in phase 9d (v0.19.47) ...
        package = {p: p.read_text(encoding="utf-8") for p in BOT.rglob("*.py")}
        # ... and the two scene bindings moved to commands/scene.py in 9e (v0.19.48).
        for key, owner in (("scene_action_targets", "commands/scene.py"), ("scene_action_panel", "commands/scene.py"), ("battle_panel", "commands/battle.py")):
            self.assertRegex(self.es, rf'EVENT_HANDLERS\.invoke\(\s*"{key}"')
            binders = sorted(str(p.relative_to(BOT)) for p, src in package.items() if f'EVENT_HANDLERS.register("{key}"' in src)
            self.assertEqual(binders, [owner], key)

    def test_layering(self):
        for src in (self.es, self.cr, self.bot):
            self.assertNotIn("main", self._imports(src))
        self.assertNotIn("bot", self._imports(self.es))
        self.assertNotIn("bot", self._imports(self.cr))
        self.assertIn("ui.event_scene", self._imports(self.bot))
        self.assertIn("admin.server_setup", self._imports(self.bot))
        wo = (BOT / "admin" / "world_ops.py").read_text(encoding="utf-8")
        self.assertIn("bot", self._imports(wo))
        self.assertIn("ui.event_scene", self._imports(wo))

    def test_main_reexports_the_bot_for_the_package_init(self):
        self.assertIn("from .bot import XianxiaBot, bot", self.main)
        init = (BOT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .main import XianxiaBot, bot, register_command_surface, run", init)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 7000)
        self.assertGreater(len(self.es.splitlines()), 480)
        self.assertGreater(len(self.cr.splitlines()), 470)
        self.assertGreater(len(self.bot.splitlines()), 480)


class Phase9aSplitTests(unittest.TestCase):
    """Split phase 9a (v0.19.44): the first four player-command domains.

    Picked because the scope-accurate scan showed nothing else still in
    main.py reads them (only the hub wiring, which reads every group).
    """

    DOMAINS = {
        "aptitude": ("aptitude_group", ("status", "root", "bloodline", "physique", "temper", "awaken", "evolve", "harmonize")),
        "territory": ("territory_group", ("status", "claim")),
        "secretrealm": ("secret_group", ("status", "enter", "explore", "leave")),
        "cultivation": ("seclusion_group", ("start", "status", "end")),
    }
    ROOTS_IN_CULTIVATION = ("cultivate", "breakthrough")

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.wiring = WIRING.read_text(encoding="utf-8")
        self.src = {d: (BOT / "commands" / f"{d}.py").read_text(encoding="utf-8") for d in self.DOMAINS}

    def test_each_domain_owns_its_group_and_leaves(self):
        for domain, (group, leaves) in self.DOMAINS.items():
            with self.subTest(domain=domain):
                self.assertIn(f"{group} = app_commands.Group(", self.src[domain])
                self.assertNotIn(f"{group} = app_commands.Group(", self.main)
                for leaf in leaves:
                    self.assertRegex(self.src[domain], rf'registered_group_command\({group},\s*name="{leaf}"')
                    self.assertNotRegex(self.main, rf'registered_group_command\({group},\s*name="{leaf}"')
        for root in self.ROOTS_IN_CULTIVATION:
            self.assertRegex(self.src["cultivation"], rf'registered_root_command\(\s*name="{root}"')
            self.assertNotRegex(self.main, rf'registered_root_command\(\s*name="{root}"')

    def test_all_fourteen_groups_moved_with_their_commands(self):
        groups = ("aptitude_group", "territory_group", "war_group", "caravan_group", "party_group",
                  "secret_group", "seclusion_group", "body_group", "bodyperfect_group",
                  "perfect_group", "tribulation_group")
        for g in groups:
            self.assertNotRegex(self.main, rf"(?m)^{g}\s*=", g)

    def test_main_imports_what_the_wiring_reads(self):
        for name in ("aptitude_group", "body_group", "bodyperfect_group", "perfect_group", "seclusion_group",
                     "tribulation_group", "secret_group", "secret_status", "caravan_group", "party_group",
                     "party_status", "territory_group", "war_group", "war_status"):
            self.assertRegex(self.wiring, rf"from \.commands\.\w+ import [^\n]*\b{name}\b", name)

    def test_no_new_module_imports_main(self):
        for domain, src in self.src.items():
            mods = {n.module for n in ast.parse(src).body if isinstance(n, ast.ImportFrom) and n.module}
            self.assertNotIn("main", mods, domain)

    def test_all_command_registrations_are_unique_across_the_package(self):
        pattern = re.compile(r'registered_(?:group|root)_command\(\s*(?:(\w+),\s*)?name="([^"]+)"')
        seen = collections.Counter()
        for path in bot_modules_except("__init__.py"):
            for group, name in pattern.findall(path.read_text(encoding="utf-8")):
                seen[(group or "<root>", name)] += 1
        self.assertEqual([k for k, v in seen.items() if v != 1], [])
        self.assertGreaterEqual(sum(seen.values()), 190)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 5700)
        for domain, floor in (("aptitude", 200), ("territory", 130), ("secretrealm", 130), ("cultivation", 650)):
            self.assertGreater(len(self.src[domain].splitlines()), floor, domain)


class Phase9bSplitTests(unittest.TestCase):
    """Split phase 9b (v0.19.45): character, economy, abode; the carried-item
    picker they share goes to pickers.py."""

    DOMAINS = {
        "character": (("fate_group", "bond_group"), ("begin", "sheet", "inventory", "lifespan", "karma", "soul", "reincarnate")),
        "economy": (("storage_group", "auction_group", "market_group", "blackmarket_group", "civilization_group"), ("wallet", "use")),
        "abode": (("abode_group", "array_group", "innerworld_group"), ("spatialkey",)),
    }

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.wiring = WIRING.read_text(encoding="utf-8")
        self.src = {d: (BOT / "commands" / f"{d}.py").read_text(encoding="utf-8") for d in self.DOMAINS}
        self.pickers = (BOT / "pickers.py").read_text(encoding="utf-8")

    def test_each_domain_owns_its_groups_and_roots(self):
        for domain, (groups, roots) in self.DOMAINS.items():
            with self.subTest(domain=domain):
                for g in groups:
                    self.assertRegex(self.src[domain], rf"(?m)^{g}\s*=\s*app_commands\.Group\(")
                    self.assertNotRegex(self.main, rf"(?m)^{g}\s*=")
                for r in roots:
                    self.assertRegex(self.src[domain], rf'registered_root_command\(\s*name="{r}"')
                    self.assertNotRegex(self.main, rf'registered_root_command\(\s*name="{r}"')

    def test_the_shared_picker_moved_to_pickers_and_both_users_import_it(self):
        self.assertIn("async def usable_item_autocomplete(", self.pickers)
        self.assertNotIn("async def usable_item_autocomplete(", self.main)
        for d in ("economy", "abode"):
            self.assertNotIn("async def usable_item_autocomplete(", self.src[d])
            top = {a.name for n in ast.parse(self.src[d]).body if isinstance(n, ast.ImportFrom) and n.module == "pickers" for a in n.names}
            self.assertIn("usable_item_autocomplete", top, d)

    def test_main_imports_what_the_wiring_reads(self):
        # `inventory` was in this list until phase 10 (v0.20.0): the wiring
        # reaches the /inventory root by name through ACTIONS, and the
        # import-back of the handler itself had no reader. The sweep dropped it.
        for name in ("abode_group", "array_group", "innerworld_group", "bond_group", "fate_group",
                     "auction_browse", "auction_group", "blackmarket_group", "blackmarket_status", "civilization_group",
                     "civilization_status_command", "market_group", "storage_group"):
            self.assertRegex(self.wiring, rf"(?m)from \.commands\.\w+ import [^\n]*\b{name}\b|^\s+{name},$", name)

    def test_no_new_module_imports_main(self):
        for d, src in self.src.items():
            mods = {n.module for n in ast.parse(src).body if isinstance(n, ast.ImportFrom) and n.module}
            self.assertNotIn("main", mods, d)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 4150)
        for d, floor in (("character", 750), ("economy", 550), ("abode", 220)):
            self.assertGreater(len(self.src[d].splitlines()), floor, d)


class Phase9cSplitTests(unittest.TestCase):
    """Split phase 9c (v0.19.46): exploration, craft and alchemy - one
    contiguous block of main.py, so one module. `_run_crafting` (shared by
    `craft` and `/alchemy refine`) stays inside it."""

    GROUPS = ("alchemy_group", "realmhub_group", "travel_group")
    ROOTS = ("explore", "hunt", "craft")

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.wiring = WIRING.read_text(encoding="utf-8")
        self.src = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")

    def test_exploration_owns_its_groups_and_roots(self):
        for g in self.GROUPS:
            self.assertRegex(self.src, rf"(?m)^{g}\s*=\s*app_commands\.Group\(")
            self.assertNotRegex(self.main, rf"(?m)^{g}\s*=")
        for r in self.ROOTS:
            self.assertRegex(self.src, rf'registered_root_command\(\s*name="{r}"')
            self.assertNotRegex(self.main, rf'registered_root_command\(\s*name="{r}"')

    def test_run_crafting_moved_whole_and_is_called_from_both_recipes(self):
        self.assertIn("async def _run_crafting(", self.src)
        self.assertNotIn("_run_crafting", self.main)
        self.assertGreaterEqual(self.src.count("await _run_crafting("), 2)

    def test_main_imports_the_three_groups_the_wiring_reads(self):
        for name in self.GROUPS:
            self.assertRegex(self.wiring, rf"(?m)from \.commands\.exploration import [^\n]*\b{name}\b|^\s+{name},$", name)

    def test_exploration_reads_only_modules_below_main(self):
        mods = {n.module for n in ast.parse(self.src).body if isinstance(n, ast.ImportFrom) and n.module}
        self.assertNotIn("main", mods)
        # The sect-recruitment hint at a location is sect.py's; explore must
        # take it from there rather than carry a copy.
        self.assertIn("sect", mods)
        self.assertNotIn("def _sect_recruitment_at_location(", self.src)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 3300)
        self.assertGreater(len(self.src.splitlines()), 850)


class Phase9dSplitTests(unittest.TestCase):
    """Split phase 9d (v0.19.47): battle and law. Law reads battle (the panel
    and the technique executor); battle reads nothing in law, so the
    executor - called from BattleView AND /law technique - lives in battle."""

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.wiring = WIRING.read_text(encoding="utf-8")
        self.battle = (BOT / "commands" / "battle.py").read_text(encoding="utf-8")
        self.law = (BOT / "commands" / "law.py").read_text(encoding="utf-8")

    def test_each_domain_owns_its_groups_and_roots(self):
        for src, groups, roots in (
            (self.battle, ("battle_group",), ("bounty",)),
            (self.law, ("law_group", "manual_group", "condition_group", "profession_group", "crime_group"), ()),
        ):
            for g in groups:
                self.assertRegex(src, rf"(?m)^{g}\s*=\s*app_commands\.Group\(")
                self.assertNotRegex(self.main, rf"(?m)^{g}\s*=")
            for r in roots:
                self.assertRegex(src, rf'registered_root_command\(\s*name="{r}"')
                self.assertNotRegex(self.main, rf'registered_root_command\(\s*name="{r}"')
        # worldrules sat between the manual and condition blocks; it is
        # sense's (commands/sense.py since 9e).
        self.assertRegex((BOT / "commands" / "sense.py").read_text(encoding="utf-8"), r'registered_root_command\(\s*name="worldrules"')
        self.assertNotIn("def world_rules_command(", self.law)

    LEAVES = {
        "battle_group": {"status", "finish", "challenge", "act"},
        "law_group": {"status", "comprehend", "technique"},
        "manual_group": {"list", "study", "technique"},
        "condition_group": {"status", "treat"},
        "profession_group": {"status"},
        "crime_group": {"status", "atone"},
    }

    def test_every_leaf_arrived_under_its_own_name(self):
        # Counted from the v0.19.46 main.py before the cut. A leaf lost in the
        # move is silent until a player looks for it; so is a renamed one.
        both = self.battle + self.law
        for group, leaves in self.LEAVES.items():
            found = set(re.findall(rf'@registered_group_command\({group},\s*name="([a-z_]+)"', both))
            self.assertEqual(found, leaves, group)

    def test_the_edge_runs_law_to_battle_only(self):
        law_mods = {n.module for n in ast.parse(self.law).body if isinstance(n, ast.ImportFrom) and n.module}
        battle_mods = {n.module for n in ast.parse(self.battle).body if isinstance(n, ast.ImportFrom) and n.module}
        self.assertIn("battle", law_mods)
        self.assertNotIn("law", battle_mods)
        self.assertNotIn("main", law_mods | battle_mods)
        self.assertIn("async def _execute_battle_law_technique(", self.battle)
        self.assertNotIn("def _execute_battle_law_technique(", self.law)
        self.assertIn("from .battle import _battle_panel, _execute_battle_law_technique", self.law)

    def test_the_battle_panel_binding_moved_with_the_panel(self):
        # EventSceneView (ui/event_scene.py) invokes "battle_panel" through
        # the registry. The binding must run at import of the module that
        # defines the panel, or the first ambush after startup KeyErrors.
        self.assertNotIn('EVENT_HANDLERS.register("battle_panel"', self.main)
        self.assertNotIn("_battle_panel", self.main)
        self.assertRegex(self.battle, r'(?m)^EVENT_HANDLERS\.register\("battle_panel", _battle_panel\)$')
        self.assertIn("EVENT_HANDLERS", {a.name for n in ast.parse(self.battle).body if isinstance(n, ast.ImportFrom) and n.module == "registry" for a in n.names})

    def test_main_imports_what_the_wiring_reads(self):
        for name in ("battle_group", "battle_status", "condition_group", "crime_group", "law_group", "manual_group", "profession_group"):
            self.assertRegex(self.wiring, rf"(?m)from \.commands\.(?:battle|law) import [^\n]*\b{name}\b|^\s+{name},$", name)
        self.assertIn('"battle": battle_status,', self.wiring)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 2450)
        self.assertGreater(len(self.battle.splitlines()), 480)
        self.assertGreater(len(self.law.splitlines()), 370)


class Phase9eSplitTests(unittest.TestCase):
    """Split phase 9e (v0.19.48): scene and sense - the last two player
    domains. With them go the two scene registry bindings, which leaves
    main.py holding nothing but hub wiring and the entrypoint."""

    SCENE_ROOTS = ("talk", "action", "npcinfo")
    SENSE_ROOTS = ("sense", "conceal", "check", "world", "worldevents", "time", "rulers", "worldrules")

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.wiring = WIRING.read_text(encoding="utf-8")
        self.scene = (BOT / "commands" / "scene.py").read_text(encoding="utf-8")
        self.sense = (BOT / "commands" / "sense.py").read_text(encoding="utf-8")

    def test_each_domain_owns_its_roots_and_main_defines_no_command_at_all(self):
        for src, roots in ((self.scene, self.SCENE_ROOTS), (self.sense, self.SENSE_ROOTS)):
            for r in roots:
                self.assertRegex(src, rf'registered_root_command\(\s*name="{r}"', r)
        self.assertRegex(self.scene, r"(?m)^scene_group\s*=\s*app_commands\.Group\(")
        self.assertEqual(set(re.findall(r'@registered_group_command\(scene_group,\s*name="([a-z_]+)"', self.scene)), {"status"})
        # The point of phase 9: no player command is defined in main.py any more.
        self.assertNotIn("@registered_group_command(", self.main)
        self.assertNotIn("app_commands.Group(", self.main)
        # The admin hub root is the wiring's (surface.py since phase 10);
        # main.py, the composition root, registers nothing itself.
        self.assertEqual(re.findall(r'registered_root_command\(\s*name="([a-z_]+)"', self.wiring), ["admin"])
        self.assertNotIn("registered_root_command", self.main)

    def test_the_scene_bindings_moved_with_the_panel_helpers(self):
        for key in ("scene_action_targets", "scene_action_panel"):
            self.assertNotIn(f'EVENT_HANDLERS.register("{key}"', self.main)
            self.assertRegex(self.scene, rf'(?m)^EVENT_HANDLERS\.register\("{key}", ')
        self.assertNotIn("_scene_action_targets", self.main)
        self.assertNotIn("scene_action_panel", self.main)
        self.assertIn("EVENT_HANDLERS", {a.name for n in ast.parse(self.scene).body if isinstance(n, ast.ImportFrom) and n.module == "registry" for a in n.names})

    def test_main_imports_what_the_wiring_reads_and_sense_for_its_side_effect(self):
        for name in ("scene_group", "scene_status", "talk"):
            self.assertRegex(self.wiring, rf"(?m)from \.commands\.scene import [^\n]*\b{name}\b|^\s+{name},$", name)
        self.assertIn('"talk": talk,', self.wiring)
        self.assertIn('"scene": scene_status,', self.wiring)
        # sense defines only root commands nothing in the wiring reads; it is
        # imported for the registration that happens when it loads.
        self.assertRegex(self.wiring, r"(?m)^from \.commands import sense as \w+\s+# noqa")

    def test_neither_module_imports_main_or_each_other(self):
        for src, other in ((self.scene, "sense"), (self.sense, "scene")):
            mods = {n.module for n in ast.parse(src).body if isinstance(n, ast.ImportFrom) and n.module}
            self.assertNotIn("main", mods)
            self.assertNotIn(other, mods)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 1350)
        self.assertGreater(len(self.scene.splitlines()), 620)
        self.assertGreater(len(self.sense.splitlines()), 430)


def _module_level_relative_imports(path):
    """The app/bot files a module imports at module level (relative imports
    only; `from .admin import world_ops` counts the member file)."""
    package = path.parent
    out = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = package
        for _ in range(node.level - 1):
            base = base.parent
        if node.module:
            target = base.joinpath(*node.module.split("."))
            if target.with_suffix(".py").is_file():
                out.add(target.with_suffix(".py"))
            elif target.is_dir():
                out.add(target / "__init__.py")
                for alias in node.names:
                    member = (target / alias.name).with_suffix(".py")
                    if member.is_file():
                        out.add(member)
        else:
            for alias in node.names:
                member = (base / alias.name).with_suffix(".py")
                if member.is_file():
                    out.add(member)
    return out


class Phase10SplitTests(unittest.TestCase):
    """Split phase 10 (v0.20.0): the final sweep. The wiring moved to
    surface.py; main.py is the composition root and nothing else."""

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.wiring = WIRING.read_text(encoding="utf-8")

    def test_main_is_a_composition_root_and_nothing_else(self):
        tree = ast.parse(self.main)
        defined = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.assertEqual(defined, ["run"])
        self.assertEqual(
            sorted(n.module for n in tree.body if isinstance(n, ast.ImportFrom) and n.level),
            ["bot", "runtime", "surface"],
        )
        imported = sorted(a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.level for a in n.names)
        self.assertEqual(imported, ["SETTINGS", "XianxiaBot", "bot", "register_command_surface", "register_event_handlers"])
        calls = [ast.unparse(n.value) for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
        self.assertEqual(calls[-2:], ["register_event_handlers()", "register_command_surface(bot)"])
        self.assertLess(len(self.main.splitlines()), 50)

    def test_the_wiring_owns_the_surface(self):
        bound = _top_level_bindings(ast.parse(self.wiring))
        for name in ("_GROUP_ACTION_ROOTS", "_MIGRATED_ROOTS", "_ROOT_ACTIONS", "_HUB_DEFINITIONS", "_HUB_COMMANDS",
                     "_ADMIN_HUB_DEFINITION", "admin_panel", "on_app_command_error", "register_command_surface",
                     "register_event_handlers"):
            self.assertIn(name, bound, name)
            self.assertNotRegex(self.main, rf"(?m)^(?:async )?def {name}\(|^{name}\s*=", name)
        mods = {n.module for n in ast.parse(self.wiring).body if isinstance(n, ast.ImportFrom) and n.module}
        self.assertNotIn("main", mods)

    def test_every_bot_module_is_loaded_at_startup(self):
        # Registration is a side effect of import, package-wide: a command
        # module nobody imports is a command that silently vanishes. Follow
        # module-level imports from main.py and require every file under
        # app/bot to be reached.
        seen: set = set()
        stack = [BOT / "main.py"]
        while stack:
            path = stack.pop()
            if path in seen:
                continue
            seen.add(path)
            stack.extend(_module_level_relative_imports(path))
        unreached = sorted(
            str(p.relative_to(BOT)) for p in BOT.rglob("*.py")
            if p not in seen and p.name not in ("__init__.py", "__main__.py")
        )
        self.assertEqual(unreached, [], unreached)
        # ... and the walk actually walked: the leaves are in the set.
        for leaf in ("commands/sense.py", "admin/inspect_sim.py", "ui/creation.py", "pickers.py"):
            self.assertIn(BOT / leaf, seen, leaf)

    def test_the_wiring_imports_every_command_module_directly(self):
        # Not merely transitively: the surface is the one place that says
        # what the bot is made of.
        direct = {p.relative_to(BOT) for p in _module_level_relative_imports(WIRING) if BOT in p.parents}
        for path in sorted((BOT / "commands").glob("*.py")):
            if path.name != "__init__.py":
                self.assertIn(path.relative_to(BOT), direct, path.name)

    def test_the_dead_tribulation_helper_is_gone(self):
        for path in BOT.rglob("*.py"):
            self.assertNotIn("_tribulation_currency", path.read_text(encoding="utf-8"), str(path))

    def test_the_package_init_still_gets_its_four_names_from_main(self):
        init = (BOT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .main import XianxiaBot, bot, register_command_surface, run", init)
        for name in ("XianxiaBot", "bot", "register_command_surface", "run"):
            self.assertRegex(self.main, rf"(?m)^from \.\w+ import [^\n]*\b{name}\b|^def {name}\(")
