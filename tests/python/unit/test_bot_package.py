"""Static integrity checks for the app/bot package.

app/bot/main.py cannot be imported in the build sandbox (no discord.py), and
it is the file every Discord command routes through, so nothing here can be
validated by running it. These checks stand in for that. They grew one class
per phase of the main.py split (v0.19.33-v0.20.0, docs/history/MAIN_SPLIT_PLAN.md);
v0.20.3 folded the eleven phase classes into the invariants they were each a
special case of. Every failure mode below has cost a failed deploy at least
once, and each docstring says which.

Layout of the file:
  * name resolution, definition order, wrapping-decorator annotations - the
    three import-time NameError shapes, checked for every module;
  * the import graph: tiers, no cycles, nothing below main imports it, the
    composition root is only that;
  * ownership: every top-level name is defined once; the load-bearing shared
    names live where the rest of the package expects; the command surface
    (every group, root and leaf) is pinned by name to its module;
  * the registry back-edges EventSceneView takes instead of importing up.
"""
from tests.support import PROJECT_ROOT
import ast
import builtins
import re
import symtable
import unittest

BOT = PROJECT_ROOT / "app" / "bot"
WIRING = BOT / "surface.py"
# Every module in the package, discovered - a new file is guarded the moment
# it exists, which the hand-maintained list this replaced could not promise.
MODULES = sorted(
    str(p.relative_to(BOT)) for p in BOT.rglob("*.py")
    if "__pycache__" not in p.parts and p.name not in ("__init__.py", "__main__.py")
)
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



# ---------------------------------------------------------------------------
# The import graph
# ---------------------------------------------------------------------------

# A module may import from a lower tier, or from its own tier only where
# noted (command modules read each other: law -> battle, exploration -> sect;
# inspect_sim reads world_ops). The graph is also required to be acyclic.
TIERS = (
    ("registry.py", "scene_layout.py", "typed_play_router.py"),
    ("runtime.py", "hubs.py"),
    ("services.py", "typed_play.py"),
    ("formatting.py", "locations.py", "pickers.py"),
    ("discovery.py", "character_state.py", "channels.py", "status_cards.py"),
    ("threads.py", "auction_feed.py"),
    ("admin/core.py",),
    ("admin/channel_messages.py", "admin/bugs_forum.py", "ui/event_scene.py", "ui/creation.py", "ui/commissions.py"),
    ("admin/quest_control.py", "admin/narration_control.py", "admin/server_setup.py", "admin/playtest_board.py"),
    ("bot.py",),
    ("admin/world_ops.py", "admin/inspect_sim.py", "commands/*"),
    ("surface.py",),
    ("main.py",),
)
SAME_TIER_ALLOWED = (("commands/", "commands/"), ("admin/inspect_sim.py", "admin/world_ops.py"))


def _tier(name):
    for rank, tier in enumerate(TIERS):
        if name in tier or (name.startswith("commands/") and "commands/*" in tier):
            return rank
    return None


def _bot_imports(path):
    """Package-relative names of the app/bot modules `path` imports at module level."""
    return {
        str(target.relative_to(BOT)) for target in _module_level_relative_imports(path)
        if BOT in target.parents and target.name not in ("__init__.py",)
    }


class ImportGraphTests(unittest.TestCase):
    """runtime.py sits below main.py and must never import back up into it.

    That one rule is what made the decomposition possible: command modules
    import the shared core without dragging main.py in, so there is no cycle.
    The tiers generalise it to the whole package."""

    def test_every_module_has_a_tier(self):
        untiered = [m for m in MODULES if _tier(m) is None]
        self.assertEqual(untiered, [], f"add these to TIERS: {untiered}")

    def test_imports_run_down_the_tiers(self):
        problems = []
        for name in MODULES:
            rank = _tier(name)
            for dep in sorted(_bot_imports(BOT / name)):
                dep_rank = _tier(dep)
                if dep_rank is None:
                    continue
                if dep_rank > rank:
                    problems.append(f"{name} (tier {rank}) imports {dep} (tier {dep_rank}) - upward")
                elif dep_rank == rank and not any(
                    name.startswith(a) and dep.startswith(b) for a, b in SAME_TIER_ALLOWED
                ):
                    problems.append(f"{name} imports {dep} in the same tier")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_module_level_import_graph_is_acyclic(self):
        graph = {name: _bot_imports(BOT / name) for name in MODULES}
        state = {}
        stack_path = []

        def visit(node):
            if state.get(node) == "done":
                return None
            if state.get(node) == "active":
                return stack_path[stack_path.index(node):] + [node]
            state[node] = "active"
            stack_path.append(node)
            for dep in sorted(graph.get(node, ())):
                cycle = visit(dep)
                if cycle:
                    return cycle
            stack_path.pop()
            state[node] = "done"
            return None

        for name in MODULES:
            cycle = visit(name)
            self.assertIsNone(cycle, f"import cycle: {' -> '.join(cycle or [])}")

    def test_no_module_below_main_imports_it_anywhere(self):
        """A module-level `from ..main import` below main.py is a real cycle
        (main imports everything); a call-time one inside a function was the
        workaround the split existed to remove - ten at the start, zero since
        phase 4 (v0.19.39). Both are bugs now."""
        for name in MODULES:
            if name == "main.py":
                continue
            for node in ast.walk(ast.parse((BOT / name).read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        "main", (node.module or "").split("."),
                        f"{name} imports main (line {node.lineno})",
                    )

    def test_every_module_is_loaded_at_startup(self):
        """Registration is a side effect of import, package-wide: a command
        module nobody imports is a command that silently vanishes. Follow
        module-level imports from main.py and require every file to be
        reached."""
        seen = set()
        stack = [BOT / "main.py"]
        while stack:
            path = stack.pop()
            if path in seen:
                continue
            seen.add(path)
            stack.extend(_module_level_relative_imports(path))
        unreached = sorted(m for m in MODULES if BOT / m not in seen)
        self.assertEqual(unreached, [], unreached)
        for leaf in ("commands/sense.py", "admin/inspect_sim.py", "ui/creation.py", "pickers.py"):
            self.assertIn(BOT / leaf, seen, leaf)


class CompositionRootTests(unittest.TestCase):
    """main.py configures logging, wires the surface onto the bot and runs it.
    Everything else is somewhere below; app/bot/__init__.py re-exports four
    names from it and `python -m app.bot` depends on those."""

    def setUp(self):
        self.main = (BOT / "main.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.main)

    def test_main_defines_only_run_and_imports_exactly_what_it_wires(self):
        defined = [n.name for n in self.tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.assertEqual(defined, ["run"])
        self.assertEqual(
            sorted(n.module for n in self.tree.body if isinstance(n, ast.ImportFrom) and n.level),
            ["bot", "runtime", "surface"],
        )
        imported = sorted(a.name for n in self.tree.body if isinstance(n, ast.ImportFrom) and n.level for a in n.names)
        self.assertEqual(imported, ["SETTINGS", "XianxiaBot", "bot", "register_command_surface", "register_event_handlers"])
        calls = [ast.unparse(n.value) for n in self.tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
        self.assertEqual(calls[-2:], ["register_event_handlers()", "register_command_surface(bot)"])
        self.assertLess(len(self.main.splitlines()), 50)

    def test_the_package_init_gets_its_four_names_from_main(self):
        init = (BOT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .main import XianxiaBot, bot, register_command_surface, run", init)
        exposed = _top_level_bindings(self.tree)
        for name in ("XianxiaBot", "bot", "register_command_surface", "run"):
            self.assertIn(name, exposed, name)

    def test_the_wiring_owns_the_surface_and_imports_every_command_module_directly(self):
        wiring = WIRING.read_text(encoding="utf-8")
        bound = _top_level_bindings(ast.parse(wiring))
        for name in ("_GROUP_ACTION_ROOTS", "_MIGRATED_ROOTS", "_ROOT_ACTIONS", "_HUB_DEFINITIONS", "_HUB_COMMANDS",
                     "_ADMIN_HUB_DEFINITION", "admin_panel", "on_app_command_error", "register_command_surface",
                     "register_event_handlers"):
            self.assertIn(name, bound, name)
        # Not merely transitively: the surface is the one place that says
        # what the bot is made of, and the side-effect-only imports
        # (commands/sense.py, admin/world_ops.py, admin/inspect_sim.py) are
        # load-bearing registrations with no other reader.
        direct = _bot_imports(WIRING)
        for module in MODULES:
            if module.startswith("commands/") or module in ("admin/world_ops.py", "admin/inspect_sim.py"):
                self.assertIn(module, direct, module)
        for name in ("ENGINE", "require_character", "reply_long", "serialized_user_action"):
            # An orphaned import is how main.py used to hide a name nothing resolved.
            self.assertNotRegex(wiring, rf"(?m)^\s+{name},$|import [^\n]*\b{name}\b", name)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

# Names that legitimately exist in two modules. Anything else defined twice
# is a copy left behind by a move, and a copy silently shadows.
KNOWN_DUPLICATES = {
    "log": {"hubs.py", "runtime.py"},
    "LAYOUT_COMPONENTS_AVAILABLE": {"hubs.py", "scene_layout.py"},
    "_LAYOUT_COMPONENT_NAMES": {"hubs.py", "scene_layout.py"},
    "law_technique_autocomplete": {"commands/boss.py", "commands/law.py"},  # stage-4 copy, predates the split
}

# The shared names the rest of the package reads by module. Each was moved
# ("not copied") in a specific phase; a later move that carries one along by
# accident, or a helper that drifts back toward a command module, fails here.
OWNERS = {
    "runtime.py": ("DB", "ENGINE", "SETTINGS", "WORLD", "GENDER_CHOICES", "PLAYER_PROPERTY_TYPES",
                   "PRIVATE_LOCATION_EXITS", "_USER_ACTION_LOCKS", "serialized_user_action", "require_character",
                   "reply_long", "_explain_engine_error", "carried_item_autocomplete", "current_world_time",
                   "character_location_display", "authoritative_lifespan", "_record_true_death_history"),
    "services.py": ("GUILD", "SCENES", "NPC_RELATIONSHIPS", "QUESTS", "EXPLORATION", "COMBAT", "NARRATOR_QUEUE",
                    "SIM", "AI_ROUTER", "NARRATOR", "NARRATOR_CONTEXT", "ALERTS", "PLAYER_PROPERTY_HOME_TYPES",
                    "PLAYER_PROPERTY_FACILITY_KEYS", "PLAYER_PROPERTY_FACILITY_LABELS"),
    "formatting.py": ("player_property_emoji", "player_property_facility_lines", "human_duration", "roll_line",
                      ),
    "locations.py": ("current_npc_location", "_world_min_realm_index", "_world_is_unlocked", "_known_locations",
                     "_location_is_visible", "location_autocomplete", "local_npc_autocomplete"),
    "pickers.py": ("auction_currency_autocomplete", "_market_item_matches", "usable_item_autocomplete"),
    "discovery.py": ("LOCATION_DISCOVERY_IMAGES", "location_discovery_image_path", "location_discovery_embed",
                     "send_location_discovery_image", "travel_first_discovers_location"),
    "character_state.py": ("current_effect_modifiers",
                           "_npc_name_mentioned", "_remember_freeform_npc_scene"),
    "channels.py": ("_event_archive_minutes", "_resolve_text_channel", "_ensure_realm_access_roles",
                    "ensure_realm_hub_channels", "event_channels", "home_scene_channel", "exploration_scene_channel",
                    "configured_info_channel", "_get_thread", "send_long_to_thread", "configured_log_channel",
                    "configured_begin_channel", "post_server_log", "_report_game_ui_error"),
    "threads.py": ("_expedition_thread_intro", "ensure_expedition_thread", "open_expedition_thread_after_exit",
                   "ensure_birth_family_household_thread", "_sect_abode_name", "ensure_sect_abode_record",
                   "ensure_sect_abode_thread_for", "_private_scene_for_thread", "active_private_location_thread",
                   "ensure_abode_thread"),
    "admin/core.py": ("_admin_command_option_summary", "log_admin_command_invocation", "require_admin", "audit_admin",
                      "admin_group", "admin_server_group", "admin_world_group", "admin_player_group",
                      "admin_sect_group", "admin_family_group", "admin_npc_group", "admin_sim_group"),
    "admin/channel_messages.py": ("XianxiaInfoView",),
    # Re-runs the complete server setup, so it lives with it (the other placement is a cycle).
    "admin/server_setup.py": ("dashboard_discord_control", "clear_managed_channel_messages"),
    "ui/event_scene.py": ("EVENT_ACTION_RULES", "_event_action_keys", "EventActionSelect", "EventNpcTalkModal",
                          "EventNpcSelect", "EventNpcSelectView", "EventSystemsSelect", "EventSystemsView",
                          "EventSceneView", "_event_scene_location", "_event_scene_profile", "spawn_event_thread",
                          "spawn_system_event_thread"),
    "ui/creation.py": ("CharacterModal", "_birth_family_preview_embed", "BirthFamilyPreviousButton",
                       "BirthFamilyChooseButton", "BirthFamilyNextButton", "CultivationStyleSelect", "BirthSexSelect",
                       "BirthFamilyBackButton", "BirthFamilyConfirmButton", "BirthFamilyView"),
    "bot.py": ("XianxiaBot", "bot"),
    # Cross-domain helpers: each has callers in two command modules and sits
    # on the side the import edge points to.
    "commands/sect.py": ("_sect_recruitment_at_location",),   # read by exploration (explore road discovery)
    "commands/exploration.py": ("_run_crafting",),            # craft and /alchemy refine
    "commands/battle.py": ("_battle_panel", "_execute_battle_law_technique"),  # read by law and BattleView
    "commands/scene.py": ("_scene_action_targets", "scene_action_panel"),
    "surface.py": ("_GROUP_ACTION_ROOTS", "_MIGRATED_ROOTS", "_ROOT_ACTIONS", "_HUB_DEFINITIONS",
                   "_ADMIN_HUB_DEFINITION", "admin_panel", "on_app_command_error", "register_command_surface",
                   "register_event_handlers"),
}


def _definitions():
    """{name: [modules defining it at top level]} over the whole package."""
    where = {}
    for name in MODULES:
        for bound in _top_level_bindings_no_imports(ast.parse((BOT / name).read_text(encoding="utf-8"))):
            where.setdefault(bound, []).append(name)
    return where


def _top_level_bindings_no_imports(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


class OwnershipTests(unittest.TestCase):
    def test_every_top_level_name_is_defined_exactly_once(self):
        """Moved, not copied. roll_line had two copies (beast.py, duel.py)
        before phase 2 and a third would have shadowed formatting's."""
        problems = []
        for name, modules in sorted(_definitions().items()):
            if len(modules) > 1 and set(modules) != KNOWN_DUPLICATES.get(name):
                problems.append(f"{name} is defined in {modules}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))
        # ... and the allowlist is not stale.
        where = _definitions()
        for name, modules in KNOWN_DUPLICATES.items():
            self.assertEqual(set(where.get(name, ())), modules, f"KNOWN_DUPLICATES[{name!r}] is stale")

    def test_the_shared_names_live_where_the_package_expects(self):
        where = _definitions()
        problems = []
        for module, names in OWNERS.items():
            for name in names:
                if where.get(name) != [module]:
                    problems.append(f"{name}: expected in {module}, found in {where.get(name)}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_wrapping_decorator_and_the_dead_helper(self):
        runtime = (BOT / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("@wraps(", runtime, "the __globals__ hazard only exists for a wrapping decorator")
        # _tribulation_currency: no caller since the engine took tribulation
        # costs; deleted in the phase-10 sweep (v0.20.0).
        self.assertNotIn("_tribulation_currency", _definitions())

    def test_the_dashboard_bridge_does_not_name_the_bot_class(self):
        # server_setup sits below bot.py; naming XianxiaBot there is the cycle
        # phase 6 (v0.19.41) untangled with `client: commands.Bot`.
        source = (BOT / "admin" / "server_setup.py").read_text(encoding="utf-8")
        names = {n.id for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Name)}
        self.assertNotIn("XianxiaBot", names)
        self.assertIn("client: commands.Bot", source)

    def test_the_admin_root_keeps_its_permission_and_guild_lock(self):
        wiring = WIRING.read_text(encoding="utf-8")
        start = wiring.index("async def admin_panel(")
        head = wiring[max(0, start - 400):start]
        self.assertIn("@app_commands.default_permissions(administrator=True)", head)
        self.assertIn("@app_commands.guild_only()", head)
        self.assertRegex(head, r'registered_root_command\(\s*name="admin",[^)]*guild=GUILD')


# ---------------------------------------------------------------------------
# The command surface
# ---------------------------------------------------------------------------

# Every group object, root command and group leaf, by the module that defines
# it - 51 groups, 40 roots, 225 leaves as of v0.20.6 (Quest Forge added two). A leaf lost in a move is
# silent until a player looks for it; so is a renamed one (phase 9d found no
# guard caught `atone` -> `atone2`). The table is the pin; regenerate it
# deliberately when a command is added, renamed or removed.
SURFACE = {
    "admin/core.py": {
        "groups": ('admin_group', 'admin_server_group', 'admin_world_group', 'admin_player_group', 'admin_sect_group', 'admin_family_group', 'admin_npc_group', 'admin_sim_group'),
        "roots": (),
        "leaves": {
        },
    },
    "admin/inspect_sim.py": {
        "groups": (),
        "roots": (),
        "leaves": {
            "admin_player_group": ('inspect', 'teleport', 'revive', 'clearbattle', 'forceendscene', 'mute', 'unmute', 'freeze', 'unfreeze', 'ban', 'unban'),
            "admin_family_group": ('familyinspect',),
            "admin_npc_group": ('npcinspect',),
            "admin_sim_group": ('toggle', 'automation', 'status', 'run', 'interval', 'region', 'npc', 'sect', 'market', 'clan', 'actions', 'world'),
            "admin_server_group": ('backup', 'audit'),
        },
    },
    "admin/server_setup.py": {
        "groups": (),
        "roots": (),
        "leaves": {
            "admin_server_group": ('bind_channels', 'status', 'basechannels', 'setup', 'realmhubs', 'observability', 'ai_status', 'chat_digest'),
        },
    },
    "admin/playtest_board.py": {
        "groups": (),
        "roots": (),
        "leaves": {
            "admin_server_group": ('playtest',),
        },
    },
    "admin/world_ops.py": {
        "groups": (),
        "roots": (),
        "leaves": {
            "admin_player_group": ('karma', 'grantstorage', 'grantcurrency', 'grant'),
            "admin_sect_group": ('setsect', 'removesect', 'setmaster', 'clearmaster', 'sectrank', 'masterattention'),
            "admin_world_group": ('advancetime', 'events', 'spawnrealm', 'closeevent', 'questforge', 'quests'),
            "admin_server_group": ('maintenance',),
        },
    },
    "commands/abode.py": {
        "groups": ('abode_group', 'array_group', 'innerworld_group'),
        "roots": ('spatialkey',),
        "leaves": {
            "abode_group": ('establish', 'status', 'thread', 'enter', 'visit', 'leave', 'invite', 'revoke', 'guests', 'upgrade', 'focus'),
            "array_group": ('list', 'use'),
            "innerworld_group": ('create', 'status', 'setrule', 'enter', 'leave'),
        },
    },
    "commands/aptitude.py": {
        "groups": ('aptitude_group',),
        "roots": (),
        "leaves": {
            "aptitude_group": ('status', 'root', 'bloodline', 'physique', 'temper', 'awaken', 'evolve', 'harmonize'),
        },
    },
    "commands/artifact.py": {
        "groups": ('artifact_group',),
        "roots": (),
        "leaves": {
            "artifact_group": ('status', 'bond', 'awaken'),
        },
    },
    "commands/battle.py": {
        "groups": ('battle_group',),
        "roots": ('bounty',),
        "leaves": {
            "battle_group": ('status', 'finish', 'challenge', 'act'),
        },
    },
    "commands/beast.py": {
        "groups": ('beast_group',),
        "roots": (),
        "leaves": {
            "beast_group": ('status', 'encounters', 'tame', 'feed', 'train', 'evolve', 'active'),
        },
    },
    "commands/boss.py": {
        "groups": ('boss_group', 'hunter_group'),
        "roots": (),
        "leaves": {
            "boss_group": ('list', 'start', 'status', 'act', 'claim'),
            "hunter_group": ('status', 'act'),
        },
    },
    "commands/character.py": {
        "groups": ('fate_group', 'bond_group'),
        "roots": ('begin', 'gender', 'sheet', 'me', 'quests', 'inventory', 'inheritances', 'effects', 'reputation', 'grudges', 'daoheart', 'provenance', 'era', 'specialeffects', 'lifespan', 'karma', 'soul', 'afterlife', 'reincarnate'),
        "leaves": {
            "fate_group": ('status', 'history'),
            "bond_group": ('status', 'propose', 'respond', 'dual_cultivate', 'sever'),
        },
    },
    "commands/cultivation.py": {
        "groups": ('seclusion_group', 'body_group', 'bodyperfect_group', 'perfect_group', 'tribulation_group', 'meridian_group', 'dantian_group'),
        "roots": ('cultivate', 'stance', 'insight', 'breakthrough'),
        "leaves": {
            "seclusion_group": ('start', 'status', 'end'),
            "body_group": ('sheet', 'cultivate', 'breakthrough'),
            "bodyperfect_group": ('start', 'info', 'quest', 'clues', 'trial', 'abandon'),
            "perfect_group": ('start', 'info', 'quest', 'clues', 'trial', 'abandon'),
            "tribulation_group": ('status', 'prepare', 'attempt'),
            "meridian_group": ('status', 'open', 'heal'),
            "dantian_group": ('status', 'refine'),
        },
    },
    "commands/duel.py": {
        "groups": ('duel_group',),
        "roots": (),
        "leaves": {
            "duel_group": ('challenge', 'respond', 'status', 'act'),
        },
    },
    "commands/economy.py": {
        "groups": ('storage_group', 'auction_group', 'merchant_group', 'shop_group', 'trade_group', 'civilization_group', 'market_group', 'blackmarket_group'),
        "roots": ('wallet', 'use'),
        "leaves": {
            "storage_group": ('status', 'deposit', 'withdraw'),
            "auction_group": ('enter', 'leave', 'browse', 'sell', 'bid'),
            "merchant_group": ('status', 'buy'),
            "shop_group": ('here', 'browse', 'buy', 'sell'),
            "trade_group": ('offer', 'status', 'accept', 'decline'),
            "civilization_group": ('status', 'npcs'),
            "blackmarket_group": ('rumors', 'status', 'buy', 'sell'),
            "market_group": ('prices', 'buy', 'sell'),
        },
    },
    "commands/equipment.py": {
        "groups": ('equipment_group',),
        "roots": (),
        "leaves": {
            "equipment_group": ('status', 'bind', 'equip', 'unequip', 'repair'),
        },
    },
    "commands/exploration.py": {
        "groups": ('alchemy_group', 'realmhub_group', 'city_group', 'travel_group'),
        "roots": ('explore', 'hunt', 'craft'),
        "leaves": {
            "alchemy_group": ('status', 'refine', 'forage', 'purge'),
            "city_group": ('look', 'board', 'accept', 'envoys', 'rumours', 'inn'),
            "realmhub_group": ('status', 'go'),
            "travel_group": ('go', 'status'),
        },
    },
    "commands/family.py": {
        "groups": ('family_group',),
        "roots": (),
        "leaves": {
            "family_group": ('view', 'enter', 'leave', 'clan', 'support', 'history', 'ancestry', 'investigate', 'legacy', 'quest', 'claim', 'conflict', 'child', 'descendants'),
        },
    },
    "commands/formation.py": {
        "groups": ('formation_group',),
        "roots": (),
        "leaves": {
            "formation_group": ('create', 'assign', 'activate', 'stance', 'status'),
        },
    },
    "commands/law.py": {
        "groups": ('law_group', 'manual_group', 'condition_group', 'profession_group', 'crime_group'),
        "roots": (),
        "leaves": {
            "law_group": ('status', 'comprehend', 'technique'),
            "manual_group": ('list', 'study', 'practise', 'technique'),
            "condition_group": ('status', 'treat'),
            "profession_group": ('status',),
            "crime_group": ('status', 'atone'),
        },
    },
    "commands/scene.py": {
        "groups": ('scene_group',),
        "roots": ('talk', 'action', 'npcinfo'),
        "leaves": {
            "scene_group": ('status',),
        },
    },
    "commands/secretrealm.py": {
        "groups": ('secret_group',),
        "roots": (),
        "leaves": {
            "secret_group": ('status', 'enter', 'explore', 'leave'),
        },
    },
    "commands/sect.py": {
        "groups": ('sect_group', 'sect_manor_group', 'sect_disciple_group', 'sect_recruitment_group'),
        "roots": (),
        "leaves": {
            "sect_recruitment_group": ('status', 'info', 'recommendation', 'recommendations', 'trial', 'history'),
            "sect_group": ('form', 'status', 'abode', 'shadow', 'roster', 'politics', 'treasury', 'contribute', 'redeem', 'address', 'family'),
            "sect_disciple_group": ('status', 'request', 'accept', 'reject', 'leave'),
            "sect_manor_group": ('status', 'establish', 'upgrade'),
        },
    },
    "commands/sense.py": {
        "groups": (),
        "roots": ('sense', 'conceal', 'check', 'world', 'worldevents', 'time', 'rulers', 'worldrules'),
        "leaves": {
        },
    },
    "commands/territory.py": {
        "groups": ('territory_group', 'war_group', 'caravan_group', 'party_group'),
        "roots": (),
        "leaves": {
            "territory_group": ('status', 'claim'),
            "war_group": ('status', 'act'),
            "caravan_group": ('dispatch', 'status', 'events'),
            "party_group": ('create', 'join', 'status', 'leave'),
        },
    },
    "surface.py": {
        "groups": (),
        "roots": ('menu', 'admin'),
        "leaves": {
        },
    },
}


class CommandSurfaceTests(unittest.TestCase):
    GROUP_RE = re.compile(r"(?m)^(\w+)\s*=\s*app_commands\.Group\(")
    ROOT_RE = re.compile(r'registered_root_command\(\s*name="([a-z_]+)"')
    LEAF_RE = re.compile(r'@registered_group_command\(\s*(\w+),\s*name="([a-z_]+)"')

    def setUp(self):
        self.src = {m: (BOT / m).read_text(encoding="utf-8") for m in MODULES}

    def test_the_table_covers_every_module_that_registers_anything(self):
        registering = sorted(
            m for m, src in self.src.items()
            if self.GROUP_RE.search(src) or self.ROOT_RE.search(src) or self.LEAF_RE.search(src)
        )
        self.assertEqual(registering, sorted(SURFACE))

    def test_each_module_registers_exactly_its_groups_roots_and_leaves(self):
        for module, expected in SURFACE.items():
            with self.subTest(module=module):
                src = self.src[module]
                self.assertEqual(tuple(self.GROUP_RE.findall(src)), expected["groups"])
                self.assertEqual(tuple(self.ROOT_RE.findall(src)), expected["roots"])
                leaves = {}
                for group, name in self.LEAF_RE.findall(src):
                    leaves.setdefault(group, []).append(name)
                self.assertEqual({g: tuple(v) for g, v in leaves.items()}, expected["leaves"])

    def test_every_registration_is_unique_across_the_package(self):
        # A group or root registered twice raises at startup; a leaf
        # registered twice under one group raises too. Checked package-wide
        # so a copy left behind by a move is caught wherever it lands.
        groups, roots, leaves = [], [], []
        for src in self.src.values():
            groups += self.GROUP_RE.findall(src)
            roots += self.ROOT_RE.findall(src)
            leaves += self.LEAF_RE.findall(src)
        for kind, items in (("group", groups), ("root", roots), ("leaf", leaves)):
            dupes = sorted({x for x in items if items.count(x) > 1})
            self.assertEqual(dupes, [], f"{kind} registered more than once: {dupes}")

    def test_every_group_is_registered_under_the_hub_wiring_or_the_admin_panel(self):
        # A group object nothing wires is a group Discord never sees - unless
        # it is nested under another group (`parent=`), which is wired instead.
        wiring = WIRING.read_text(encoding="utf-8")
        nested = set()
        for module, src in self.src.items():
            for node in ast.parse(src).body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    for kw in node.value.keywords:
                        if kw.arg == "parent" and isinstance(kw.value, ast.Name):
                            nested.update(t.id for t in node.targets if isinstance(t, ast.Name))
                            self.assertIn(kw.value.id, SURFACE[module]["groups"], f"{module}: parent {kw.value.id}")
        self.assertEqual(nested, {"admin_server_group", "admin_world_group", "admin_player_group", "admin_sect_group",
                                  "admin_family_group", "admin_npc_group", "admin_sim_group",
                                  "sect_manor_group", "sect_disciple_group", "sect_recruitment_group"})
        for module, expected in SURFACE.items():
            for group in expected["groups"]:
                if group in nested or group == "admin_group":
                    # admin_group itself is never added to the tree: /admin is
                    # a hub whose pages are its seven children.
                    continue
                self.assertRegex(wiring, rf"\b{group}\b", f"{group} ({module}) is not wired in surface.py")
        for group in ("admin_server_group", "admin_world_group", "admin_player_group", "admin_sect_group",
                      "admin_family_group", "admin_npc_group", "admin_sim_group"):
            self.assertIn(f"command={group}", wiring, group)

    def test_the_module_level_autocompletes_are_imported_where_they_decorate(self):
        # `@app_commands.autocomplete(npc=local_npc_autocomplete)` evaluates
        # at import; a deferred import can never serve it.
        for name in ("location_autocomplete", "local_npc_autocomplete", "carried_item_autocomplete",
                     "auction_currency_autocomplete", "usable_item_autocomplete"):
            owner = next(m for m, names in OWNERS.items() if name in names)
            for module, src in self.src.items():
                if module == owner or f"={name}" not in src:
                    continue
                top = {a.name for n in ast.parse(src).body if isinstance(n, ast.ImportFrom) for a in n.names}
                self.assertIn(name, top, f"{module} uses {name} without a module-level import")


# ---------------------------------------------------------------------------
# Registry back-edges
# ---------------------------------------------------------------------------

class RegistryBindingTests(unittest.TestCase):
    """The plan found three places where ui/event_scene.py needs something
    defined in a command module above it: the battle panel and the two
    scene-action helpers. EventSceneView dispatches those by name through
    EVENT_HANDLERS; each binding runs at import of the module that defines
    the target, or the first ambush after startup KeyErrors."""

    BINDINGS = {
        "battle_panel": "commands/battle.py",
        "scene_action_targets": "commands/scene.py",
        "scene_action_panel": "commands/scene.py",
        "scene_action_resolve": "commands/scene.py",  # typed play (v0.21.1)
    }

    def test_the_event_view_dispatches_by_name_and_never_calls_directly(self):
        source = (BOT / "ui" / "event_scene.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in source.splitlines() if not l.strip().startswith("#") and '"""' not in l)
        for direct in ("_scene_action_targets(", "scene_action_panel(", "_battle_panel("):
            self.assertNotIn(direct, code, f"{direct} is called directly in ui/event_scene.py")
        for key in self.BINDINGS:
            if key == "scene_action_resolve":
                continue  # invoked from typed_play.py, checked below
            self.assertRegex(source, rf'EVENT_HANDLERS\.invoke\(\s*"{key}"')
        typed = (BOT / "typed_play.py").read_text(encoding="utf-8")
        self.assertNotIn("_resolve_scene_action(", typed, "typed play must reach the resolver by name")
        self.assertRegex(typed, r'EVENT_HANDLERS\.invoke\(\s*"scene_action_resolve"')

    def test_each_binding_is_registered_once_beside_its_target(self):
        for key, owner in self.BINDINGS.items():
            binders = sorted(
                m for m in MODULES
                if f'EVENT_HANDLERS.register("{key}"' in (BOT / m).read_text(encoding="utf-8")
            )
            self.assertEqual(binders, [owner], key)
            src = (BOT / owner).read_text(encoding="utf-8")
            self.assertRegex(src, rf'(?m)^EVENT_HANDLERS\.register\("{key}", ')
            self.assertIn("EVENT_HANDLERS", {
                a.name for n in ast.parse(src).body if isinstance(n, ast.ImportFrom) and n.module == "registry" for a in n.names
            })

    def test_the_status_handlers_the_wiring_binds_are_the_registered_ones(self):
        wiring = WIRING.read_text(encoding="utf-8")
        block = wiring[wiring.index("def register_event_handlers"):]
        for key, handler in (("talk", "talk"), ("battle", "battle_status"), ("secret", "secret_status"),
                             ("war", "war_status"), ("auction", "auction_browse"), ("party", "party_status"),
                             ("boss", "boss_status"), ("formation", "formation_status"), ("hunter", "hunter_status"),
                             ("blackmarket", "blackmarket_status"), ("civilization", "civilization_status_command"),
                             ("scene", "scene_status")):
            self.assertIn(f'"{key}": {handler},', block)
        self.assertIn("EVENT_HANDLERS.register(name, ACTIONS.handler_for(command))", block)


if __name__ == "__main__":
    unittest.main()
