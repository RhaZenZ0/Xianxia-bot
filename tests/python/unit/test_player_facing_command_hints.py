"""Player-facing text must only name commands a player can actually reach.

Only 21 slash commands are registered with Discord: /begin, /me, /quests,
/action, /check, /admin and the 16 hub commands (see register_command_surface).
Every gameplay command - /family leave, /abode enter, /world explore - is
metadata only, bound into ActionRegistry and reachable through a hub panel, never
by typing. So a message telling a player to "use /family leave" is pointing at
something that does not exist.

This has shipped twice: once in the birth-household thread opener and once in the
blocked-exploration hint, both in the exact place a stuck player looks for help.
The correct form names the hub and the action inside it: "/family -> Leave".
"""
from tests.support import PROJECT_ROOT
import ast
import re
import unittest

MAIN = PROJECT_ROOT / "app" / "bot" / "main.py"

# The hubs a player can actually type, from _HUB_DEFINITIONS plus the standalone roots.
HUB_COMMANDS = {
    "character", "quest", "cultivation", "items", "npc", "world", "travel",
    "combat", "economy", "craft", "beast", "sect", "family", "abode",
    "innerworld", "realm",
}

# "/family leave" - a hub followed by a bare word, with no arrow between them.
# The lookbehind matters: without it this also flags ordinary compound phrases
# where a hub name happens to follow a slash - "NPC/sect locations",
# "breakthrough/combat checks", "GM/world decision" - none of which are commands.
BAD_HINT = re.compile(
    r"(?<![A-Za-z])/(" + "|".join(sorted(HUB_COMMANDS)) + r")\s+(?!->|→)([a-z][a-z_]{2,})\b"
)

# Words that follow a hub name in ordinary prose rather than as a subcommand
# ("/world and /travel are hubs", "/family or /abode").
PROSE_FOLLOWERS = {"and", "are", "for", "the", "hub", "hubs", "panel", "opens", "or", "with"}


def _string_constants(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string literal that is not a docstring.

    Docstrings and comments explain the rule (including the one in
    _explain_engine_error that quotes the wrong form on purpose), so they are
    exempt; only text that can reach a player is checked.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            found.append((getattr(node, "lineno", 0), node.value))
    return found


class PlayerFacingCommandHintTests(unittest.TestCase):
    def test_no_player_facing_text_names_an_untypable_subcommand(self):
        offenders = []
        for lineno, text in _string_constants(ast.parse(MAIN.read_text(encoding="utf-8"))):
            for match in BAD_HINT.finditer(text):
                if match.group(2) in PROSE_FOLLOWERS:
                    continue
                offenders.append(f"  main.py:{lineno}  {match.group(0)!r}")
        self.assertEqual(
            offenders,
            [],
            "These name a command a player cannot type. Only the hub itself is "
            "registered with Discord, so write it as '/family -> Leave':\n"
            + "\n".join(sorted(set(offenders))),
        )

    def test_the_detector_actually_catches_the_shape_it_is_for(self):
        """A guard that cannot fail is worse than no guard."""
        self.assertTrue(BAD_HINT.search("Use **/family leave** to go outside"))
        self.assertTrue(BAD_HINT.search("try /abode enter now"))
        # The correct forms must not trip it.
        self.assertFalse(BAD_HINT.search("Use **/family → Leave** to go outside"))
        self.assertFalse(BAD_HINT.search("Open **/family** and choose Leave"))
        self.assertFalse(BAD_HINT.search("**/world → Explore**"))

    def test_the_three_private_location_exits_are_all_reachable_forms(self):
        source = MAIN.read_text(encoding="utf-8")
        start = source.index("PRIVATE_LOCATION_EXITS")
        block = source[start : start + 500]
        for command in ("/family → Leave", "/abode → Leave", "/innerworld → Leave"):
            self.assertIn(command, block, f"{command} missing from the exit table")


if __name__ == "__main__":
    unittest.main()
