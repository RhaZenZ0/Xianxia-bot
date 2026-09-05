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
import builtins
import re
import symtable
import unittest

BOT = PROJECT_ROOT / "app" / "bot"
MODULES = ["main.py", "runtime.py", "hubs.py", "registry.py", "commands/family.py", "commands/sect.py"]
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
        source = (BOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("from .runtime import", source)
        for name in ("DB", "ENGINE", "SETTINGS", "require_character", "reply_long"):
            self.assertIn(name, source)


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
        self.assertIn("from .commands.family import family_group", self.main)
        self.assertIn('"family": family_group,', self.main)

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
                "SIM",
                "_get_thread",
                "ensure_birth_family_household_thread",
                "open_expedition_thread_after_exit",
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
        self.assertIn("from .commands.sect import sect_group, _sect_recruitment_at_location", self.main)
        self.assertIn('"sect": sect_group,', self.main)

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
                "SIM",
                "SIM",
                "_known_locations",
                "current_npc_location",
                "current_npc_location",
                "ensure_sect_abode_record",
                "ensure_sect_abode_thread_for",
            ],
        )

    def test_carried_item_autocomplete_moved_to_runtime_rather_than_duplicated(self):
        # Referenced as `@app_commands.autocomplete(item=carried_item_autocomplete)`
        # in both main.py and here - that evaluates at module-import time, so a
        # deferred `from ..main import` (the pattern above) cannot reach it.
        self.assertIn("async def carried_item_autocomplete(", self.runtime)
        self.assertNotIn("async def carried_item_autocomplete(", self.main)
        self.assertNotIn("async def carried_item_autocomplete(", self.sect)
        self.assertIn("carried_item_autocomplete,", self.main)
        self.assertIn("carried_item_autocomplete,", self.sect)

    def test_a_sect_only_constant_moved_with_the_sect(self):
        self.assertIn('ADDRESS_STYLE_CHOICES = [', self.sect)
        self.assertNotIn('ADDRESS_STYLE_CHOICES = [', self.main)

    def test_main_shrank_by_roughly_the_block_that_moved(self):
        self.assertLess(len(self.main.splitlines()), 12300)
        self.assertGreater(len(self.sect.splitlines()), 800)
