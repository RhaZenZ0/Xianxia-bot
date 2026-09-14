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
        """A typed line carries the arguments the table declares, or none; a
        root that needs more would fail at call time (v0.33.0; two since
        v0.39.0). Every required handler parameter must be declared and every
        declared one must exist on the handler - an optional handler
        parameter may go undeclared, the handler's default stands in."""
        commands = self._registered_commands()
        for action in TABLE.actions:
            if action.kind != "root":
                continue
            node = commands[action.command]
            args = node.args
            names = [a.arg for a in args.args][1:]
            required = [a.arg for a in args.args[: len(args.args) - len(args.defaults)]][1:]
            declared = [a.parameter for a in action.arguments if not a.optional]
            with self.subTest(action=action.key):
                for parameter in [a.parameter for a in action.arguments]:
                    self.assertIn(parameter, names, f"{action.command} has no parameter {parameter}")
                self.assertTrue(set(required) <= set(declared), f"{action.command} requires {required}; the table declares {declared}")

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


class TwoArgumentRootTests(unittest.TestCase):
    """v0.39.0: a root with two arguments - an item and a player who is here."""

    def test_give_resolves_the_item_and_the_player(self):
        route = _route_with("I give the healing pill to Li Feng")
        self.assertEqual(route.kind, "dispatch", route)
        single = route.single
        self.assertEqual(single.payload["command"], "trade offer")
        self.assertEqual(single.payload["arguments"]["give_item"], "healing_pill")
        self.assertEqual(single.payload["sources"], {"give_item": "item", "player": "player"})
        self.assertEqual(single.payload["arguments"]["player"], next(t for t in PRESENT if t.startswith("Player ")).split(":", 1)[0].removeprefix("Player ").strip())
        self.assertEqual(single.label, "Offer → Healing Pill → Li Feng")

    def test_give_to_an_npc_is_not_a_trade(self):
        # Qiao is an NPC: the player argument stays unresolved, and the
        # picker says what the line lacks rather than guessing a cultivator.
        route = _route_with("I give the healing pill to Qiao")
        self.assertEqual(route.kind, "picker")
        self.assertEqual(route.candidates, ())
        self.assertIn("a cultivator who is here", route.message)

    def test_give_needs_both(self):
        route = _route_with("I hand over a pill to Li Feng")
        self.assertEqual(route.kind, "picker")
        self.assertIn("something you carry", route.message)

    def test_sell_takes_the_item(self):
        self.assertEqual(_ids(_route_with("I sell the healing pill to the smith")), ["root:shop sell:healing_pill"])

    def test_the_table_accepts_a_list_and_rejects_a_repeated_parameter(self):
        table = router.VerbTable.from_data({"actions": [{"key": "x", "kind": "root", "command": "use", "aliases": ["x"], "arguments": [{"parameter": "item", "source": "item"}, {"parameter": "to", "source": "player", "optional": True}]}]})
        self.assertEqual([a.parameter for a in table.actions[0].arguments], ["item", "to"])
        self.assertTrue(table.actions[0].arguments[1].optional)
        with self.assertRaises(ValueError):
            router.VerbTable.from_data({"actions": [{"key": "x", "kind": "root", "command": "use", "arguments": [{"parameter": "item", "source": "item"}, {"parameter": "item", "source": "player"}]}]})



# ---------------------------------------------------------------------------
# The shorthand: "x explore" names a command (v1.0.0)
# ---------------------------------------------------------------------------

_CS = router.CommandSpec
_CP = router.CommandParameter

COMMANDS = {
    spec.name: spec
    for spec in (
        _CS("explore"),
        _CS("inventory"),
        _CS("travel go", (_CP("destination", "str", True),)),
        _CS("travel status"),
        _CS("use", (_CP("item", "str", True),)),
        _CS("npcinfo", (_CP("npc", "str", True),)),
        _CS("talk", (_CP("npc", "str", True), _CP("message", "str", True))),
        _CS("conceal", (_CP("active", "bool", True),)),
        _CS("check", (_CP("attribute", "choice", True), _CP("action", "str", True))),
        _CS("sense", (_CP("target", "member", False), _CP("area", "bool", False))),
        _CS("stipend", (_CP("amount", "int", True),)),
        _CS("shop buy", (_CP("item", "str", True),)),
        _CS("auction bid", (_CP("auction_id", "int", True), _CP("amount", "int", True))),
        _CS("merchant buy", (_CP("merchant", "str", True), _CP("item", "str", True))),
        _CS("worldevents"),
        _CS("world"),
        # A group with one leaf: "prof" names it, and the leaf's own word is
        # still in the line when a player spells the group out.
        _CS("profession status"),
        _CS("seclusion start", (_CP("days", "int", True),)),
        _CS("seclusion end"),
    )
}


def _shorthand(text: str):
    """The Route a shorthand line becomes, or None when it names no command."""
    match = router.command_named(text, commands=COMMANDS)
    if match is None:
        return None
    return router.route_command(text, match=match, table=TABLE, present=PRESENT,
                                locations=KNOWN, items=CARRIED)


class ShorthandParseTests(unittest.TestCase):
    def test_token_is_taken_as_a_whole_word(self):
        self.assertEqual(router.parse_shorthand("x explore", "x"), "explore")
        self.assertEqual(router.parse_shorthand("  x  explore the ravine ", "x"), "explore the ravine")

    def test_case_does_not_matter(self):
        self.assertEqual(router.parse_shorthand("X explore", "x"), "explore")

    def test_a_word_merely_starting_with_the_token_is_speech(self):
        self.assertIsNone(router.parse_shorthand("xexplore", "x"))
        self.assertIsNone(router.parse_shorthand("xylophone lessons", "x"))

    def test_a_bare_token_is_speech(self):
        self.assertIsNone(router.parse_shorthand("x", "x"))
        self.assertIsNone(router.parse_shorthand("x   ", "x"))

    def test_an_empty_token_disables_the_shorthand(self):
        self.assertIsNone(router.parse_shorthand("x explore", ""))

    def test_a_longer_token_works(self):
        self.assertEqual(router.parse_shorthand("do explore", "do"), "explore")
        self.assertIsNone(router.parse_shorthand("done exploring", "do"))

    def test_line_is_capped(self):
        long = "x " + "a" * 5000
        self.assertEqual(len(router.parse_shorthand(long, "x")), router.MAX_LINE_CHARS)


class ShorthandCommandTests(unittest.TestCase):
    # (line, expected candidate ids) - None means "names no command at all",
    # which is what keeps an ordinary chat channel silent.
    ROWS = (
        ("explore", ["root:explore"]),
        ("explore the ravine", ["root:explore"]),          # a zero-arg root ignores the rest
        ("travel status", ["root:travel status"]),         # a group leaf by its qualified name
        ("travel go Greenriver Town", ["root:travel go:Greenriver Town"]),
        ("npcinfo Steward Qiao", ["root:npcinfo:Steward Qiao"]),
        ("shop buy jade talisman", ["root:shop buy:jade talisman"]),  # one free-text argument, greedy
        ("auction bid 4 500", ["root:auction bid:4:500"]),            # numbers have their own boundaries
        ("conceal on", ["root:conceal:True"]),
        ("stipend 40", ["root:stipend:40"]),
        ("sense", ["root:sense"]),                         # all-optional runs bare
        ("marks the spot", None),
        ("", None),
    )

    def test_rows(self):
        for line, expected in self.ROWS:
            with self.subTest(text=line):
                route = _shorthand(line)
                if expected is None:
                    self.assertIsNone(route)
                    continue
                self.assertEqual(route.kind, "dispatch", route.message)
                self.assertEqual(_ids(route), expected)

    def test_a_verb_table_argument_is_resolved_against_what_is_carried(self):
        # "use" is in the verb table, so the shorthand fills it the way the
        # prefix does: a real inventory id, not the words the player typed.
        route = _shorthand("use healing pill")
        self.assertEqual(_ids(route), ["root:use:healing_pill"])

    def test_a_verb_table_argument_that_resolves_to_nothing_is_answered(self):
        route = _shorthand("travel go")
        self.assertEqual(route.kind, "refusal")
        self.assertIn("place you know", route.message)

    def test_a_choice_parameter_is_sent_to_the_slash_command(self):
        route = _shorthand("check might climb the wall")
        self.assertEqual(route.kind, "refusal")
        self.assertIn("/check", route.message)
        self.assertIn("from a list", route.message)

    def test_a_required_parameter_with_nothing_to_fill_it_is_refused(self):
        route = _shorthand("npcinfo")
        self.assertEqual(route.kind, "refusal")
        self.assertIn("/npcinfo", route.message)

    def test_a_word_that_is_not_the_parameters_type_is_refused(self):
        self.assertEqual(_shorthand("stipend plenty").kind, "refusal")
        self.assertEqual(_shorthand("conceal maybe").kind, "refusal")

    def test_two_free_text_parameters_are_never_split(self):
        # "merchant buy zhao jade talisman" has no boundary between the two,
        # and a guess here would buy the wrong thing from the wrong person.
        route = _shorthand("merchant buy zhao jade talisman")
        self.assertEqual(route.kind, "refusal")
        self.assertIn("/merchant buy", route.message)
        self.assertEqual(_shorthand("talk Qiao what news").kind, "refusal")

    def test_a_name_spelled_as_one_word_is_found_when_typed_as_two(self):
        # Otherwise "x world events" would silently run /world and drop the rest.
        self.assertEqual(_ids(_shorthand("world events")), ["root:worldevents"])
        self.assertEqual(_ids(_shorthand("world")), ["root:world"])

    def test_an_exact_name_beats_an_abbreviation(self):
        self.assertEqual(_ids(_shorthand("use healing pill")), ["root:use:healing_pill"])

    def test_a_unique_abbreviation_is_the_command_it_begins(self):
        self.assertEqual(_ids(_shorthand("inv")), ["root:inventory"])
        self.assertEqual(_ids(_shorthand("expl")), ["root:explore"])

    def test_an_ambiguous_abbreviation_names_nothing(self):
        # "travel go" and "travel status" both begin with "tra".
        self.assertIsNone(_shorthand("tra somewhere"))

    def test_an_abbreviation_onto_a_single_leaf_group_eats_the_leaf_word(self):
        # "prof" is the only command whose first word begins that way, so it
        # names "profession status" - and "status" must be consumed, not left
        # in the line for the command's first parameter to swallow.
        match = router.command_named("prof status", commands=COMMANDS)
        self.assertEqual((match.spec.name, match.words), ("profession status", 2))
        # Without the leaf word it is still the same command, one word spent.
        bare = router.command_named("prof", commands=COMMANDS)
        self.assertEqual((bare.spec.name, bare.words), ("profession status", 1))
        self.assertEqual(_ids(_shorthand("prof status")), ["root:profession status"])

    def test_a_leaf_word_is_only_eaten_when_it_is_the_leafs_own(self):
        # "secl" is ambiguous (start and end), so nothing is named - the rule
        # above must not make a group with two leaves resolvable.
        self.assertIsNone(router.command_named("secl start 7", commands=COMMANDS))
        # And a word that is not the leaf's name stays an argument.
        match = router.command_named("profession elsewhere", commands=COMMANDS)
        self.assertEqual((match.spec.name, match.words), ("profession status", 1))

    def test_an_abbreviation_must_be_long_enough_to_mean_it(self):
        self.assertIsNone(router.command_named("in", commands=COMMANDS))
        self.assertEqual(router.command_named("inv", commands=COMMANDS).spec.name, "inventory")

    def test_the_longest_qualified_name_wins(self):
        match = router.command_named("travel go Greenriver Town", commands=COMMANDS)
        self.assertEqual((match.spec.name, match.words), ("travel go", 2))

    def test_a_command_absent_from_the_table_is_never_named(self):
        # /admin is excluded from the table the bot builds, so no shorthand
        # line can reach it however it is spelled.
        self.assertIsNone(router.command_named("admin world spawn", commands=COMMANDS))
