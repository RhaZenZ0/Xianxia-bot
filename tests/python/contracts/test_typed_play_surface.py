"""Typed play (v0.21.1): the boundary contracts.

Three things this feature promises, each held here by reading the source:

1. **Speech is free.** In ``on_message``, an un-prefixed line that neither
   @mentions the bot nor addresses a present NPC reaches no narrator call
   and no dispatch. Only three doors lead out of the un-prefixed branch:
   ``if mentioned``, ``if npc is not None``, and the canonical
   location-question shortcut (which is a template, not a model).
2. **Typed play owns no handler.** ``typed_play.py`` reaches handlers only
   through the registries (``ACTIONS`` for roots and ``/talk``,
   ``EVENT_HANDLERS`` for the scene-action resolver). It makes no engine
   call and no database write of its own, so the authority gate in
   ``test_authority_boundary.py`` has nothing new to allowlist.
3. **Every door is budgeted.** Each path out of ``on_message`` that can
   reach the engine or the narrator spends a token first, and the picker's
   buttons spend one on click.

The behavioural half (what a line becomes) is ``test_typed_play_router.py``;
the bucket itself is ``test_user_budget.py``.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT, bot_class_source, load_module_by_path

BOT_PY = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
TYPED_PLAY = (PROJECT_ROOT / "app" / "bot" / "typed_play.py").read_text(encoding="utf-8")
SCENE = (PROJECT_ROOT / "app" / "bot" / "commands" / "scene.py").read_text(encoding="utf-8")


def _method(class_source: str, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(class_source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    return next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _calls(node: ast.AST) -> set[str]:
    """Names of everything called anywhere inside ``node``."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _if_test_text(node: ast.If) -> str:
    return ast.unparse(node.test)


class SpeechIsFreeTests(unittest.TestCase):
    """Contract 1: the un-prefixed branch of on_message."""

    REACHING_CALLS = {"narrate", "_dispatch_typed", "narrate_freeform", "dispatch", "TypedPlayPicker"}

    def setUp(self) -> None:
        self.on_message = _method(bot_class_source("XianxiaBot"), "on_message")
        self.speech_branch = next(
            n for n in ast.walk(self.on_message)
            if isinstance(n, ast.If) and _if_test_text(n) == "typed is None"
        )

    def test_reaching_calls_only_behind_the_two_doors(self):
        doors = {"mentioned", "npc is not None"}
        guarded: set[int] = set()
        for node in ast.walk(self.speech_branch):
            if isinstance(node, ast.If) and _if_test_text(node) in doors:
                for sub in ast.walk(node):
                    guarded.add(id(sub))
        leaks = []
        for node in ast.walk(self.speech_branch):
            if isinstance(node, ast.Call) and id(node) not in guarded:
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in self.REACHING_CALLS:
                    leaks.append(f"line {node.lineno}: {name}()")
        self.assertEqual(leaks, [], "a call that can reach the narrator or engine is outside the doors:\n" + "\n".join(leaks))

    def test_both_doors_exist_and_are_budgeted(self):
        found = {}
        for node in ast.walk(self.speech_branch):
            if isinstance(node, ast.If) and _if_test_text(node) in {"mentioned", "npc is not None"}:
                found[_if_test_text(node)] = node
        self.assertEqual(set(found), {"mentioned", "npc is not None"})
        for test, node in found.items():
            with self.subTest(door=test):
                self.assertIn("budget_refusal", _calls(node), f"the {test} door does not spend a token")

    def test_speech_is_still_recorded_as_history(self):
        self.assertIn("add_history", _calls(self.speech_branch))

    def test_the_narrator_is_not_called_directly_from_on_message(self):
        # narrate_action lives in narrate_freeform now; on_message only reaches
        # it through the local `narrate` closure, which is behind the doors.
        self.assertNotIn("narrate_action", _calls(self.on_message))

    def test_the_prefixed_branch_is_budgeted_before_routing(self):
        body = self.on_message.body
        start = next(i for i, n in enumerate(body) if isinstance(n, ast.If) and _if_test_text(n) == "typed is None")
        after = body[start + 1:]
        order = [n for n in after if isinstance(n, (ast.Assign, ast.Expr, ast.If))]
        text = "\n".join(ast.unparse(n) for n in order)
        self.assertLess(text.index("budget_refusal("), text.index("route_line("))
        self.assertIn("EVENT_HANDLERS.invoke('scene_action_targets'", text)  # ast.unparse uses single quotes


class NoHandlerOfItsOwnTests(unittest.TestCase):
    """Contract 2: typed_play.py reaches handlers only by registry."""

    def test_dispatch_targets_are_registry_lookups(self):
        tree = ast.parse(TYPED_PLAY)
        dispatch = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "dispatch")
        text = ast.unparse(dispatch)
        self.assertIn("ACTIONS.root(", text)
        self.assertIn("ACTIONS.handler_for(", text)
        self.assertIn("EVENT_HANDLERS.invoke('scene_action_resolve'", text)  # ast.unparse uses single quotes
        self.assertNotIn("_resolve_scene_action(", text)
        self.assertNotIn("from .commands", text, "typed play must not import a command module")

    def test_no_engine_call_and_no_database_write(self):
        for forbidden in ("authoritative_action(", "ENGINE.", "DB.", "add_history(", "add_rag_memory("):
            self.assertNotIn(forbidden, TYPED_PLAY, forbidden)
        self.assertNotIn("from .commands", TYPED_PLAY)

    def test_the_resolver_is_bound_beside_its_definition(self):
        self.assertRegex(SCENE, r'(?m)^EVENT_HANDLERS\.register\("scene_action_resolve", _resolve_scene_action\)')
        self.assertEqual(SCENE.count('EVENT_HANDLERS.register("scene_action_resolve"'), 1)

    def test_the_verb_table_is_content_not_code(self):
        self.assertTrue((PROJECT_ROOT / "content" / "typed_play.json").is_file())
        self.assertIn('"content" / "typed_play.json"', (PROJECT_ROOT / "app" / "bot" / "typed_play_router.py").read_text(encoding="utf-8"))
        self.assertNotIn("aliases", TYPED_PLAY, "aliases belong in content/typed_play.json")


class EveryDoorIsBudgetedTests(unittest.TestCase):
    """Contract 3: the picker's buttons and the message paths spend a token."""

    def test_picker_buttons_that_can_reach_the_engine_or_narrator_spend_first(self):
        tree = ast.parse(TYPED_PLAY)
        for cls_name in ("_CandidateButton", "_NarrateButton"):
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
            callback = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "callback")
            text = ast.unparse(callback)
            with self.subTest(button=cls_name):
                self.assertIn("budget_refusal(", text)
                self.assertLess(text.index("budget_refusal("), text.index("_close("), "spend before acting")

    def test_say_it_button_is_free(self):
        tree = ast.parse(TYPED_PLAY)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_SayItButton")
        self.assertNotIn("budget_refusal", ast.unparse(cls))
        self.assertNotIn("dispatch(", ast.unparse(cls))

    def test_the_picker_is_owner_only(self):
        self.assertIn("async def interaction_check", TYPED_PLAY)
        self.assertIn("self.owner_id", TYPED_PLAY)

    def test_the_budget_singleton_is_configured_from_settings(self):
        runtime = (PROJECT_ROOT / "app" / "bot" / "runtime.py").read_text(encoding="utf-8")
        self.assertRegex(runtime, r"TYPED_PLAY_BUDGET = UserBudget\(burst=SETTINGS\.typed_play_burst, per_minute=SETTINGS\.typed_play_per_minute\)")


class PrefixSettingTests(unittest.TestCase):
    """TYPED_PLAY_PREFIX: one character, never a letter, digit or space."""

    def setUp(self) -> None:
        from tests.support import install_dotenv_shim
        install_dotenv_shim()
        self.config = load_module_by_path("config_under_test_typed_play", "app/ops/config.py")

    def test_default_and_common_choices(self):
        # v0.25.1: the default is "$". It used to be ">", which Discord renders
        # as a blockquote - the reason it was picked. A server that wants that
        # back sets it explicitly.
        self.assertEqual(self.config._typed_play_prefix(None), "$")
        self.assertEqual(self.config._typed_play_prefix(""), "$")
        self.assertEqual(self.config._typed_play_prefix(">"), ">")
        self.assertEqual(self.config._typed_play_prefix("!"), "!")
        self.assertEqual(self.config._typed_play_prefix("*"), "*")

    def test_a_regex_metacharacter_prefix_matches_literally(self):
        # The router uses str.startswith, not a pattern, so "$" - a regex
        # end-anchor - is matched as the character it is.
        router = (PROJECT_ROOT / "app" / "bot" / "typed_play_router.py").read_text(encoding="utf-8")
        self.assertIn("raw.startswith(prefix)", router)
        self.assertNotIn("re.compile(prefix", router)

    def test_rejects_letters_digits_space_and_strings(self):
        for bad in ("i", "I", "7", " ", ">>", "act"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.config._typed_play_prefix(bad)

    def test_env_example_documents_the_knobs(self):
        env = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        for key in ("TYPED_PLAY_PREFIX=", "TYPED_PLAY_BURST=", "TYPED_PLAY_PER_MINUTE=", "TYPED_PLAY_HINT="):
            self.assertIn(key, env, key)


class HintTests(unittest.TestCase):
    def test_hint_is_once_per_day_per_player(self):
        # The pure part of the hint lives in typed_play.py but that module
        # imports discord; test the rule through its source shape instead.
        self.assertIn("def hint_due(", TYPED_PLAY)
        self.assertRegex(TYPED_PLAY, r"_HINTED\.get\(int\(user_id\)\) == day")
        self.assertRegex(BOT_PY, r"looks_like_action\(content\) and hint_due\(")
        self.assertIn("delete_after=45", BOT_PY)


if __name__ == "__main__":
    unittest.main()
