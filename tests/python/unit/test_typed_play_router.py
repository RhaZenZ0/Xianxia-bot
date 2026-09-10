"""Typed play router (v0.21.1): the rules, pinned by a table of lines.

The router is pure - no Discord, no I/O - so this loads it on its own and
asserts what each kind of line becomes. A change in the verb table or the
matching rules that alters any row here is a change a player would feel.
"""
from __future__ import annotations

import ast
import json
import re
import unittest

from tests.support import PROJECT_ROOT, load_module_by_path

router = load_module_by_path("typed_play_router_under_test", "app/bot/typed_play_router.py")

TABLE = router.VerbTable.load()
PRESENT = ["Steward Qiao", "Elder Mu Feng", "Player 42: Li Feng"]
ALL_NPCS = ["Steward Qiao", "Elder Mu Feng", "Emperor Zhao Tianming", "Jade Sovereign Lian Xue"]


def _route(text: str, present=PRESENT):
    return router.route_line(text, table=TABLE, present=present, all_npcs=ALL_NPCS)


def _ids(route):
    return [c.id for c in route.candidates]


class PrefixTests(unittest.TestCase):
    def test_prefix_with_and_without_space(self):
        self.assertEqual(router.parse_prefixed("> I explore", ">"), "I explore")
        self.assertEqual(router.parse_prefixed(">I explore", ">"), "I explore")
        self.assertEqual(router.parse_prefixed("   > I explore  ", ">"), "I explore")

    def test_unprefixed_and_bare_prefix_are_not_actions(self):
        self.assertIsNone(router.parse_prefixed("I explore", ">"))
        self.assertIsNone(router.parse_prefixed(">", ">"))
        self.assertIsNone(router.parse_prefixed(">   ", ">"))

    def test_other_prefix_characters_work(self):
        self.assertEqual(router.parse_prefixed("! sneak", "!"), "sneak")
        self.assertIsNone(router.parse_prefixed("> sneak", "!"))

    def test_line_is_capped(self):
        long = "> " + "a" * 5000
        self.assertEqual(len(router.parse_prefixed(long, ">")), router.MAX_LINE_CHARS)


class RoutingTableTests(unittest.TestCase):
    """One row per rule. The expected value is the candidate ids, or the route kind."""

    ROWS = [
        # root commands, with and without the leading "I"
        ("I explore my location", ["root:explore"]),
        ("explore", ["root:explore"]),
        ("I'll look around", ["root:explore"]),
        ("let me search the area", ["root:explore"]),
        ("I meditate under the old pine", ["root:cultivate"]),
        ("I try to break through", ["root:breakthrough"]),
        ("I go hunting", ["root:hunt"]),
        # scene actions: the whole line is the detail, the target is who is named
        ("I sneak past the guards", ["scene:stealth:Environment"]),
        ("I sneak past Qiao", ["scene:stealth:Steward Qiao"]),
        ("I try to persuade Mu Feng to let me in", ["scene:influence:Elder Mu Feng"]),
        ("I help Li Feng up", ["scene:aid:Player 42: Li Feng"]),
        ("I search the room for clues", ["scene:investigate:Environment"]),
        ("I circulate qi", ["scene:qi:Environment"]),
        # dialogue
        ("I ask Qiao about the caravan", ["talk:Steward Qiao"]),
        ("Qiao, do you have work for me?", ["talk:Steward Qiao"]),
        ("Elder Mu Feng, may I enter?", ["talk:Elder Mu Feng"]),
        # a verb in the middle of a line still counts when it is the only one
        ("carefully I observe the crowd", ["scene:observe:Environment"]),
    ]

    def test_rows(self):
        for text, expected in self.ROWS:
            with self.subTest(text=text):
                route = _route(text)
                self.assertEqual(route.kind, "dispatch", route)
                self.assertEqual(_ids(route), expected)

    def test_a_shared_token_identifies_nobody(self):
        # "Feng" belongs to both Elder Mu Feng and the player Li Feng.
        self.assertEqual(_ids(_route("I help Feng up")), ["scene:aid:Environment"])

    def test_two_verbs_of_equal_standing_become_a_picker(self):
        route = _route("I explore and then meditate")
        # "explore" leads the line, "meditate" does not: a clear winner.
        self.assertEqual(route.kind, "dispatch")
        self.assertEqual(_ids(route), ["root:explore"])
        route = _route("while I meditate I explore")
        self.assertEqual(route.kind, "picker", route)
        self.assertEqual(set(_ids(route)), {"root:explore", "root:cultivate"})

    def test_talk_with_nobody_named_offers_everyone_present(self):
        route = _route("I ask around")
        self.assertEqual(route.kind, "picker")
        self.assertEqual(_ids(route), ["talk:Steward Qiao", "talk:Elder Mu Feng"])

    def test_nothing_matches_is_an_empty_picker_not_a_guess(self):
        route = _route("I punch Li Feng")
        self.assertEqual(route.kind, "picker")
        self.assertEqual(route.candidates, ())
        self.assertEqual(_route("hmm").kind, "picker")

    def test_naming_an_absent_npc_is_a_refusal(self):
        route = _route("I ask Emperor Zhao Tianming for mercy")
        self.assertEqual(route.kind, "refusal")
        self.assertIn("Emperor Zhao Tianming", route.message)
        self.assertEqual(route.candidates, ())

    def test_refusal_needs_the_full_name(self):
        # A bare token about an absent NPC is speech about them, not a refusal.
        route = _route("I ask about Tianming", present=["Steward Qiao"])
        self.assertNotEqual(route.kind, "refusal")

    def test_picker_is_capped(self):
        many = [f"Elder Wu{i}" for i in range(9)]
        route = _route("I ask around", present=many)
        self.assertLessEqual(len(route.candidates), router.MAX_PICKER_CANDIDATES)


class AddressingTests(unittest.TestCase):
    """Un-prefixed lines: only a line that addresses a present NPC is dialogue."""

    def test_addressed(self):
        self.assertEqual(router.addressed_npc("Qiao, what is the caravan carrying?", PRESENT), "Steward Qiao")
        self.assertEqual(router.addressed_npc("Steward Qiao I need work", PRESENT), "Steward Qiao")
        self.assertEqual(router.addressed_npc("Elder Mu Feng, may I enter?", PRESENT), "Elder Mu Feng")
        self.assertEqual(router.addressed_npc("where did Qiao go?", PRESENT), "Steward Qiao")

    def test_mentioned_in_passing_is_speech(self):
        self.assertIsNone(router.addressed_npc("I think Qiao is lying", PRESENT))
        self.assertIsNone(router.addressed_npc("we should avoid the Steward for now", PRESENT))
        self.assertIsNone(router.addressed_npc("what a beautiful morning", PRESENT))

    def test_players_are_never_addressed_npcs(self):
        self.assertIsNone(router.addressed_npc("Li Feng, over here!", PRESENT))

    def test_nobody_present(self):
        self.assertIsNone(router.addressed_npc("Qiao, hello?", []))


class HintTests(unittest.TestCase):
    def test_action_shaped_lines(self):
        self.assertTrue(TABLE.looks_like_action("I explore the ravine"))
        self.assertTrue(TABLE.looks_like_action("sneak past the guards"))

    def test_speech_is_not_action_shaped(self):
        self.assertFalse(TABLE.looks_like_action("the ravine is beautiful, we should explore it someday"))
        self.assertFalse(TABLE.looks_like_action("hello everyone"))


class VerbTableContentTests(unittest.TestCase):
    """The table names only handlers that exist, so a typo cannot ship."""

    SOURCE = PROJECT_ROOT / "content" / "typed_play.json"

    def test_json_is_valid_and_loads(self):
        data = json.loads(self.SOURCE.read_text(encoding="utf-8"))
        table = router.VerbTable.from_data(data)
        self.assertGreaterEqual(len(table.actions), 10)

    @staticmethod
    def _registered_commands() -> dict[str, ast.AsyncFunctionDef]:
        """Every registered root by name and every group leaf by qualified name."""
        groups: dict[str, str] = {}
        for path in (PROJECT_ROOT / "app" / "bot" / "commands").glob("*.py"):
            for var, name in re.findall(r'^(\w+)\s*=\s*app_commands\.Group\(\s*name="([^"]+)"', path.read_text(encoding="utf-8"), re.M):
                groups[var] = name
        out: dict[str, ast.AsyncFunctionDef] = {}
        for path in (PROJECT_ROOT / "app" / "bot" / "commands").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    kind = getattr(dec.func, "id", "")
                    name = next((k.value.value for k in dec.keywords if k.arg == "name"), None)
                    if not name:
                        continue
                    if kind == "registered_root_command":
                        out[name] = node
                    elif kind == "registered_group_command" and dec.args and isinstance(dec.args[0], ast.Name):
                        group = groups.get(dec.args[0].id)
                        if group:
                            out[f"{group} {name}"] = node
        return out

    def test_every_root_command_is_a_registered_root_or_leaf(self):
        commands = self._registered_commands()
        for action in TABLE.actions:
            if action.kind == "root":
                with self.subTest(action=action.key):
                    self.assertIn(action.command, commands)

    def test_root_commands_take_exactly_the_declared_parameters(self):
        """A typed line carries the one argument the table declares, or none;
        a root that needs more would fail at call time (v0.33.0)."""
        commands = self._registered_commands()
        for action in TABLE.actions:
            if action.kind != "root":
                continue
            node = commands[action.command]
            args = node.args
            required = [a.arg for a in args.args[: len(args.args) - len(args.defaults)]][1:]
            with self.subTest(action=action.key):
                if action.parameter:
                    self.assertEqual(required, [action.parameter], f"{action.command} must take exactly {action.parameter}")
                else:
                    self.assertEqual(required, [], f"{action.command} takes required parameters beyond the interaction")

    def test_every_argument_source_is_one_the_bot_supplies(self):
        bot = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("locations=sorted(known), items=carried", bot)
        for action in TABLE.actions:
            if action.parameter:
                self.assertIn(action.source, router.ARGUMENT_SOURCES)

    def test_every_scene_action_exists(self):
        scene = (PROJECT_ROOT / "app" / "bot" / "commands" / "scene.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'^\s+"([a-z]+)": \{"label"', scene, re.M))
        self.assertTrue(keys, "could not read SCENE_ACTION_TYPES")
        for action in TABLE.actions:
            if action.kind == "scene":
                with self.subTest(action=action.key):
                    self.assertIn(action.scene_action, keys)

    def test_no_alias_is_claimed_twice(self):
        seen: dict[str, str] = {}
        for action in TABLE.actions:
            for alias in action.aliases:
                key = alias.casefold().strip()
                self.assertNotIn(key, seen, f"alias {alias!r} is in both {seen.get(key)} and {action.key}")
                seen[key] = action.key

    def test_no_alias_is_a_leading_phrase(self):
        for phrase in TABLE.leading_phrases:
            for action in TABLE.actions:
                self.assertNotIn(phrase, [a.casefold() for a in action.aliases])

    def test_table_rejects_bad_rows(self):
        with self.assertRaises(ValueError):
            router.VerbTable.from_data({"actions": [{"key": "x", "kind": "root"}]})
        with self.assertRaises(ValueError):
            router.VerbTable.from_data({"actions": [{"key": "x", "kind": "nope", "command": "explore"}]})
        with self.assertRaises(ValueError):
            router.VerbTable.from_data({"actions": [
                {"key": "x", "kind": "root", "command": "explore"},
                {"key": "x", "kind": "root", "command": "hunt"},
            ]})


if __name__ == "__main__":
    unittest.main()


KNOWN = ["Greenriver Town", "Azure Crown Imperial City", "Jadewood Medicine City", "Frostwatch City"]
CARRIED = [("qi_gathering_pill", "Qi Gathering Pill"), ("healing_pill", "Healing Pill"), ("spirit_iron_sword", "Spirit-Iron Sword")]


def _route_with(text: str):
    return router.route_line(text, table=TABLE, present=PRESENT, all_npcs=ALL_NPCS, locations=KNOWN, items=CARRIED)


class ArgumentRootTests(unittest.TestCase):
    """v0.33.0: a root with one argument dispatches only with it resolved."""

    def test_a_full_place_name_travels(self):
        route = _route_with("I travel to Greenriver Town")
        self.assertEqual(route.kind, "dispatch", route)
        self.assertEqual(_ids(route), ["root:travel go:Greenriver Town"])
        self.assertEqual(route.single.payload["arguments"], {"destination": "Greenriver Town"})
        self.assertEqual(route.single.label, "Travel → Greenriver Town")

    def test_a_distinctive_token_is_enough(self):
        self.assertEqual(_ids(_route_with("go to greenriver")), ["root:travel go:Greenriver Town"])
        self.assertEqual(_ids(_route_with("I head for Frostwatch")), ["root:travel go:Frostwatch City"])

    def test_a_shared_token_names_no_place(self):
        # "City" belongs to three known places.
        route = _route_with("I go to the city")
        self.assertEqual(route.kind, "picker")
        self.assertEqual(route.candidates, ())
        self.assertIn("a place you know", route.message)

    def test_an_unknown_place_is_not_guessed(self):
        route = _route_with("I travel to Ashenwall City")
        self.assertEqual(route.kind, "picker")
        self.assertEqual(route.candidates, ())

    def test_an_item_by_name_or_token(self):
        self.assertEqual(_ids(_route_with("I drink a Healing Pill")), ["root:use:healing_pill"])
        self.assertEqual(_ids(_route_with("I use the healing pill")), ["root:use:healing_pill"])
        route = _route_with("I swallow a pill")
        self.assertEqual(route.kind, "picker")
        self.assertEqual(route.candidates, ())
        self.assertIn("something you carry", route.message)

    def test_without_sources_nothing_is_offered(self):
        route = _route("I travel to Greenriver Town")
        self.assertEqual(route.kind, "picker")
        self.assertEqual(route.candidates, ())

    def test_an_argument_root_and_a_scene_verb_still_pick(self):
        route = _route_with("I sneak to Greenriver Town")
        # "sneak" leads the line; the travel alias "to" alone is not one.
        self.assertEqual(route.kind, "dispatch")
        self.assertEqual(_ids(route), ["scene:stealth:Environment"])

    def test_resolve_argument_ties_break_only_on_a_full_name(self):
        pairs = [("a", "Cloud Pill"), ("b", "Cloud Pill of the East")]
        self.assertEqual(router.resolve_argument("I take a cloud pill of the east", pairs), ("b", "Cloud Pill of the East"))
        self.assertIsNone(router.resolve_argument("I take a cloud", pairs))
        self.assertIsNone(router.resolve_argument("nothing here", []))

    def test_the_table_rejects_a_bad_argument(self):
        with self.assertRaises(ValueError):
            router.VerbTable.from_data({"actions": [{"key": "x", "kind": "root", "command": "use", "argument": {"parameter": "item", "source": "planet"}}]})
        with self.assertRaises(ValueError):
            router.VerbTable.from_data({"actions": [{"key": "x", "kind": "scene", "scene_action": "observe", "argument": {"parameter": "item", "source": "item"}}]})
