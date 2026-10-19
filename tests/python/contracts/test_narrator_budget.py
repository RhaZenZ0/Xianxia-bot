"""The narrator budget (v0.31.0, docs/history/ROADMAP_1_0.md "v0.31").

Three promises, each held here rather than in prose:

1. A live model call is made for three reasons only - an NPC answering a
   player (dialogue), an epic beat, or an explicit ask (Narrate it, an
   @mention, the GM's flag). A scene the engine already decided - an
   exploration opening, a hunt result - reads from the procedural pool
   unless upgraded, and the narrator says so with `upgrade`.
2. The per-player budget guards every door: typed lines, the slash and hub
   path through `serialized_user_action`, and the Narrate-it asks.
3. The procedural floor is content: a pool with several variants per scene
   kind and world tier, chosen deterministically, with no empty cells.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT, bot_class_source, bot_function_source

from app.rules.narration_pool import SCENE_KINDS, TIERS, narration_tier, pool_variants, procedural_narration

NARRATOR = PROJECT_ROOT / "app" / "ai" / "narrator.py"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))

# Methods that may call _generate unconditionally, with the purpose each
# must declare. Anything else that calls _generate must guard it behind an
# `upgrade` flag - the explicit-ask path.
LIVE_CALLERS = {
    "talk_to_npc": {"dialogue"},
    "narrate_breakthrough": {"epic"},
    "narrate_action": {"epic", "narrate_it"},
}
PROCEDURAL_FIRST = {"narrate_exploration", "narrate_hunt_result"}


def _narrator_methods() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(NARRATOR.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Narrator")
    return {n.name: n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)}


def _generate_calls(fn: ast.AST) -> list[ast.Call]:
    return [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_generate"
    ]


def _purposes(call: ast.Call) -> set[str]:
    out: set[str] = set()
    for keyword in call.keywords:
        if keyword.arg != "purpose":
            continue
        for node in ast.walk(keyword.value):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
    return out


def _returns_before_generate_when_not_upgraded(fn: ast.AsyncFunctionDef) -> bool:
    """`if not upgrade: ... return ...` appears before the first _generate."""
    first_generate = min((c.lineno for c in _generate_calls(fn)), default=None)
    if first_generate is None:
        return False
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or node.lineno >= first_generate:
            continue
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Name) and test.operand.id == "upgrade":
            if any(isinstance(stmt, ast.Return) for stmt in node.body):
                return True
    return False


# Callers of a live narration that do not wear the decorator, and who spent the
# token for them instead.
TOKEN_SPENT_BEFORE_THE_CALL = {
    # Typed play spends it at the picker (`budget_refusal`); the panel's modal is
    # `_submit_scene_action`, held in the test below.
    "_resolve_scene_action",
    # Reached only by an @mention or the picker's "Narrate it", each of which
    # spent the token (`budget_refusal`) before it called.
    "narrate_freeform",
}
LIVE_NARRATION_METHODS = {"talk_to_npc", "narrate_action", "narrate_breakthrough"}


def _wears_the_meter(fn: ast.AST) -> bool:
    """Wears `@serialized_user_action` bare - `metered=False` keeps the lock and drops the meter."""
    for dec in getattr(fn, "decorator_list", []):
        if isinstance(dec, ast.Name) and dec.id == "serialized_user_action":
            return True
    return False


def _live_narration_callers() -> dict[str, tuple[bool, str]]:
    """Every function in the bot that calls a live narration method, innermost first."""
    out: dict[str, tuple[bool, str]] = {}
    for path in sorted((PROJECT_ROOT / "app" / "bot").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        stack: list[ast.AST] = []

        class Walker(ast.NodeVisitor):
            def visit_FunctionDef(self, node):  # noqa: N802
                stack.append(node)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):  # noqa: N802
                if isinstance(node.func, ast.Attribute) and node.func.attr in LIVE_NARRATION_METHODS and stack:
                    fn = stack[-1]
                    out[fn.name] = (_wears_the_meter(fn), str(path.relative_to(PROJECT_ROOT)))
                self.generic_visit(node)

        Walker().visit(tree)
    return out


class RouteByTierTests(unittest.TestCase):
    def test_no_generate_call_runs_by_default_outside_dialogue_epic_and_explicit_asks(self):
        methods = _narrator_methods()
        callers = {name: fn for name, fn in methods.items() if _generate_calls(fn) and name != "_generate"}
        self.assertEqual(set(callers), set(LIVE_CALLERS) | PROCEDURAL_FIRST, sorted(callers))
        for name, allowed in LIVE_CALLERS.items():
            for call in _generate_calls(methods[name]):
                purposes = _purposes(call)
                self.assertTrue(purposes, f"{name} names no purpose")
                self.assertTrue(purposes <= allowed, f"{name} declares {purposes}, allowed {allowed}")
        for name in PROCEDURAL_FIRST:
            fn = methods[name]
            self.assertIn("upgrade", {a.arg for a in fn.args.args + fn.args.kwonlyargs}, name)
            self.assertTrue(_returns_before_generate_when_not_upgraded(fn), f"{name} reaches the model without an upgrade")
            for call in _generate_calls(fn):
                self.assertEqual(_purposes(call), {"narrate_it"}, name)

    def test_the_dead_sect_narrations_stay_gone(self):
        methods = _narrator_methods()
        self.assertNotIn("narrate_sect_recommendation", methods)
        self.assertNotIn("narrate_sect_trial", methods)

    def test_explore_and_hunt_ask_for_the_model_only_on_the_flag_or_the_button(self):
        for handler in ("explore", "hunt"):
            body = bot_function_source(handler)
            self.assertIn("_routine_narration_by_default()", body, handler)
            self.assertIn("upgrade=True", body, handler)
            self.assertIn("NarrateItView(", body, handler)
        view = bot_class_source("NarrateItView")
        self.assertIn('budget_refusal_line(interaction.user.id, "narrate_it")', view)


class BudgetOnEveryDoorTests(unittest.TestCase):
    def test_the_slash_and_hub_path_spends_the_same_bucket(self):
        runtime = (PROJECT_ROOT / "app" / "bot" / "runtime.py").read_text(encoding="utf-8")
        wrapper = runtime[runtime.index("def serialized_user_action("):runtime.index("def chunk_text(")]
        self.assertIn('budget_refusal_line(interaction.user.id, "slash")', wrapper)
        # The refusal is answered before the lock is taken and before any engine call.
        self.assertLess(wrapper.index("budget_refusal_line("), wrapper.index("_user_action_lock("))
        self.assertIn("if refusal:", wrapper)
        typed = (PROJECT_ROOT / "app" / "bot" / "typed_play.py").read_text(encoding="utf-8")
        self.assertIn('def budget_refusal(user_id: int, door: str = "typed")', typed)
        self.assertIn('budget_refusal(interaction.user.id, door="narrate_it")', typed)

    def test_every_command_that_reaches_a_live_narration_spends_the_meter(self):
        """`/check` and the `/action` modal narrated live and spent nothing (v1.34.0).

        Both are doors on the live methods (`talk_to_npc`, `narrate_action`,
        `narrate_breakthrough`) and neither wore `serialized_user_action`, so
        the promise above - every door is metered - held for the doors the first
        author remembered. This walks every caller of those methods in the bot
        and asks what spends the token for it.
        """
        callers = _live_narration_callers()
        self.assertGreaterEqual(len(callers), 4, f"the reader finds too few callers to prove anything: {sorted(callers)}")
        self.assertIn("check", callers, "the reader no longer finds /check")
        for name, (wears, path) in sorted(callers.items()):
            if name in TOKEN_SPENT_BEFORE_THE_CALL:
                continue
            self.assertTrue(
                wears,
                f"{name} ({path}) reaches a live narration with no meter: /check and the /action modal were unmetered live-narration doors",
            )

    def test_the_scene_action_modal_spends_the_token_and_typed_play_does_not_spend_it_twice(self):
        scene = (PROJECT_ROOT / "app" / "bot" / "commands" / "scene.py").read_text(encoding="utf-8")
        tree = ast.parse(scene)
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
        self.assertTrue(_wears_the_meter(funcs["_submit_scene_action"]), "the modal's door is not metered")
        modal = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SceneActionDetailModal")
        submit = next(n for n in modal.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_submit")
        called = {c.func.id for c in ast.walk(submit) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        self.assertIn("_submit_scene_action", called)
        self.assertNotIn("_resolve_scene_action", called, "the modal resolves without spending a token")
        resolver = funcs["_resolve_scene_action"]
        self.assertFalse(_wears_the_meter(resolver), "typed play's picker already spent the token the resolver would spend again")
        self.assertNotIn("budget_refusal_line", ast.get_source_segment(scene, resolver) or "")

    def test_check_keeps_its_place_among_the_reads_a_closed_door_leaves_open(self):
        # Read off the source: importing the bot package needs a token in the environment.
        tree = ast.parse((PROJECT_ROOT / "app" / "bot" / "seclusion.py").read_text(encoding="utf-8"))
        literal = next(
            ast.literal_eval(n.value.args[0]) for n in tree.body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "OPEN_COMMANDS"
        )
        self.assertIn("check", literal, "/check lost its place among the reads a closed door leaves open")

    def test_idle_locks_are_evicted(self):
        runtime = (PROJECT_ROOT / "app" / "bot" / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("USER_ACTION_LOCK_IDLE_SECONDS", runtime)
        evictor = runtime[runtime.index("def _user_action_lock("):runtime.index("def serialized_user_action(")]
        self.assertIn("_USER_ACTION_LOCKS.pop(stale_id, None)", evictor)
        self.assertIn("not lock.locked()", evictor)


class FallbackPoolTests(unittest.TestCase):
    def test_every_scene_kind_has_variants_for_every_tier(self):
        pool = WORLD["narration_pool"]
        self.assertEqual(tuple(pool["tiers"]), TIERS)
        self.assertEqual(set(pool["scenes"]), set(SCENE_KINDS))
        for kind, scene in pool["scenes"].items():
            placeholders = set(scene["placeholders"])
            for tier in TIERS:
                rows = pool_variants(WORLD, kind, tier)
                self.assertGreaterEqual(len(rows), 3, f"{kind}/{tier}")
                self.assertEqual(len(set(rows)), len(rows), f"{kind}/{tier} repeats a variant")
                for row in rows:
                    used = {name for _, name, _, _ in __import__("string").Formatter().parse(row) if name}
                    self.assertTrue(used <= placeholders, f"{kind}/{tier}: {used - placeholders}")
                    self.assertNotIn("reward", row.lower(), f"{kind}/{tier}: the floor must not grant anything")

    def test_the_choice_is_deterministic_and_filled(self):
        first = procedural_narration(WORLD, "hunt_success", tier="Mortal World", seed=(1, "Boar"), fallback="x", beast="Boar", location="Greenriver Town")
        again = procedural_narration(WORLD, "hunt_success", tier="Mortal World", seed=(1, "Boar"), fallback="x", beast="Boar", location="Greenriver Town")
        other = {procedural_narration(WORLD, "hunt_success", tier="Mortal World", seed=(i, "Boar"), fallback="x", beast="Boar", location="Greenriver Town") for i in range(40)}
        self.assertEqual(first, again)
        self.assertIn("Boar", first)
        self.assertNotIn("{", first)
        self.assertGreater(len(other), 1, "forty seeds should not all land on one variant")
        self.assertEqual(procedural_narration({}, "hunt_success", tier="Mortal World", seed=(1,), fallback="the old line"), "the old line")

    def test_the_tier_comes_from_the_location_or_the_realm(self):
        self.assertEqual(narration_tier(WORLD, "Greenriver Town"), "Mortal World")
        self.assertEqual(narration_tier(WORLD, "Spirit Jade Capital"), "Spiritual World")
        self.assertEqual(narration_tier(WORLD, "abode:42", realm_index=9), "Spiritual World")
        self.assertEqual(narration_tier(WORLD, "sect_abode:42", realm_index=0), "Mortal World")
        self.assertEqual(narration_tier({}, None), "Mortal World")


if __name__ == "__main__":
    unittest.main()
