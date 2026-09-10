"""v0.33.0 Gameplay-complete I gate: every id-typed parameter has a picker.

Walks every registered command (root and group leaf, player and admin) at
source level - app/bot cannot be imported without a Discord environment -
and classifies each ``str`` parameter:

- guided: it has choices, a slash autocomplete (decorator or attribute
  form), or a registered hub option provider, so the hub renders a picker
  and the slash command completes it;
- prose: its name is on the FREE_TEXT list below - a name to invent, a
  reason to record, a line to speak - and a picker would be wrong.

Anything else fails: a new id parameter must come with its picker, and a
new prose parameter must be added to the list on purpose. The seven that
were missing at v0.32.0 are pinned by name, with the hints that explain an
empty picker where one can be empty.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
COMMAND_FILES = sorted((BOT / "commands").glob("*.py")) + sorted((BOT / "admin").glob("*.py"))
ALL_SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in COMMAND_FILES)

# Parameters that are prose by design. A name here is a decision: the hub
# asks for it in a modal and the slash command takes it typed.
FREE_TEXT = frozenset({
    "name",            # something the player is naming: a formation, a manor, a child, an abode, a world, a party, a new life
    "spirit_name",     # the awakened artifact's name
    "reason",          # the GM's audit line
    "message",         # what is said to an NPC
    "action",          # a few words on how a battle move or a check is attempted
    "stakes",          # what a duel is fought for
    "rule", "definition",  # a personal-world law and what it does
    "duration",        # 30m / 2h / 1d, parsed by rules.moderation
    "category_name",   # a Discord category to create
    "story",           # the Quest Forge brief
})

# The parameters the roadmap named as picker-less at v0.32.0 (five), plus the
# two the walk found beside them.
PINNED = (
    ("artifact_bond", "item"),
    ("artifact_awaken", "item"),
    ("boss_start", "boss"),
    ("secret_enter", "realm"),
    ("caravan_dispatch", "item"),
    ("provenance_command", "item"),
    ("reincarnate", "path"),
)
# Pickers that can legitimately be empty, and so explain themselves.
HINTED = (
    ("artifact_bond", "item"),
    ("artifact_awaken", "item"),
    ("boss_start", "boss"),
    ("secret_enter", "realm"),
)


def _registered_commands():
    """(function name, parameter name, coverage) for every str parameter."""
    rows = []
    for path in COMMAND_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if not any(d.startswith(("registered_group_command", "registered_root_command")) for d in decorators):
                continue
            choices: set[str] = set()
            autocompletes: set[str] = set()
            for d in node.decorator_list:
                # Top-level keywords only: `Choice(name=grade, ...)` inside a
                # choices list is not a parameter called name.
                if not isinstance(d, ast.Call) or not isinstance(d.func, ast.Attribute):
                    continue
                keywords = {k.arg for k in d.keywords if k.arg}
                if d.func.attr == "choices":
                    choices |= keywords
                elif d.func.attr == "autocomplete":
                    autocompletes |= keywords
            for arg in node.args.args[1:]:
                if arg.annotation is None or ast.unparse(arg.annotation) != "str":
                    continue
                name = arg.arg
                coverage = set()
                if name in choices:
                    coverage.add("choices")
                if name in autocompletes or re.search(rf"@{node.name}\.autocomplete\(\"{name}\"\)", ALL_SOURCE):
                    coverage.add("autocomplete")
                if re.search(rf"register_hub_option_provider\({node.name},\s*\"{name}\"", ALL_SOURCE):
                    coverage.add("provider")
                if re.search(rf"register_hub_option_hint\(\s*{node.name},\s*\"{name}\"", ALL_SOURCE):
                    coverage.add("hint")
                rows.append((node.name, name, frozenset(coverage)))
    return rows


class EveryIdParameterHasAPicker(unittest.TestCase):
    def test_the_walk_sees_the_surface(self):
        rows = _registered_commands()
        self.assertGreater(len(rows), 60)
        self.assertIn(("battle_challenge", "target"), {(fn, p) for fn, p, _ in rows})

    def test_every_str_parameter_is_guided_or_declared_prose(self):
        problems = []
        for function, parameter, coverage in _registered_commands():
            guided = bool(coverage & {"choices", "autocomplete", "provider"})
            if parameter in FREE_TEXT and guided:
                problems.append(f"{function}({parameter}) is on FREE_TEXT but has a picker; drop it from the list")
            elif not guided and parameter not in FREE_TEXT:
                problems.append(f"{function}({parameter}) has no picker and is not declared prose")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_seven_named_parameters_have_pickers(self):
        by_key = {(fn, p): cov for fn, p, cov in _registered_commands()}
        for key in PINNED:
            with self.subTest(parameter=key):
                self.assertIn(key, by_key)
                self.assertTrue(by_key[key] & {"autocomplete", "provider"}, key)
        for key in HINTED:
            with self.subTest(hint=key):
                self.assertIn("hint", by_key[key], key)

    def test_the_hub_reads_the_same_provider_as_the_slash_command(self):
        hubs = (BOT / "hubs.py").read_text(encoding="utf-8")
        self.assertIn('getattr(action.command, "_params", None)', hubs)
        self.assertIn("_HUB_OPTION_PROVIDERS.get((qualified, spec.name))", hubs)

    def test_hints_name_a_hub_path_not_a_slash_command(self):
        # An empty picker says where to go in hub terms, the way the player
        # reached it (test_player_facing_command_hints holds the format).
        for path in COMMAND_FILES:
            text = path.read_text(encoding="utf-8")
            for start in [m.start() for m in re.finditer(r"register_hub_option_hint\(", text)]:
                block = text[start:text.index(")\n", start)]
                self.assertNotRegex(block, r"`/[a-z]", f"{path.name}: hint uses a slash command in backticks")


class TheBossPickerKnowsWhere(unittest.TestCase):
    def test_boss_options_put_the_local_boss_first_and_name_the_others_place(self):
        source = (BOT / "commands" / "boss.py").read_text(encoding="utf-8")
        start = source.index("async def boss_template_autocomplete")
        body = source[start:start + 1600]
        self.assertIn('str(c.get("location")', body)
        self.assertIn("0 if location == here else 1", body)


class SecretRealmPickerReadsTheStatusQuery(unittest.TestCase):
    def test_the_picker_and_the_status_line_share_one_engine_query(self):
        source = (BOT / "commands" / "secretrealm.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('ENGINE.action("secret_realm.status"'), 2)
        start = source.index("async def open_realm_autocomplete")
        self.assertIn('result.get("available")', source[start:start + 1200])


class FateAdjustIsGone(unittest.TestCase):
    def test_the_engine_no_longer_allowlists_a_player_fate_write(self):
        # fate.adjust was implemented in Go with no caller anywhere since
        # v0.18; a player writing their own Fate was never a feature. The GM
        # path (admin.player.fate) is the one that remains.
        game = PROJECT_ROOT / "go_core" / "internal" / "game"
        for path in game.glob("*.go"):
            self.assertNotIn('"fate.adjust"', path.read_text(encoding="utf-8"), path.name)
        actions = (game / "actions.go").read_text(encoding="utf-8")
        self.assertIn("adjustFateGo(conn, uid, delta", actions)


if __name__ == "__main__":
    unittest.main()
