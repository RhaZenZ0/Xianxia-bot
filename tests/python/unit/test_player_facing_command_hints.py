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
import sys
import unittest


def _parse(module):
    """Parse a bot module, or return None when this interpreter cannot.

    main.py uses a PEP 701 f-string that Python 3.11 cannot parse. The bot ships
    on 3.12 (see the Dockerfile), so on 3.11 these checks cover what they can
    rather than erroring out - but every caller asserts something was actually
    parsed, so a silently empty scan is still a failure. On 3.12 a syntax error
    in main.py fails loudly, which is the case that matters.
    """
    try:
        return ast.parse(module.read_text(encoding="utf-8"))
    except SyntaxError:
        if sys.version_info >= (3, 12):
            raise
        return None

# Scan the whole bot package, not just main.py: the decomposition moved some
# player-facing strings into runtime.py and later stages will move more into
# app/bot/commands/. A check anchored to one file would quietly stop covering
# them the moment they move.
BOT = PROJECT_ROOT / "app" / "bot"
BOT_MODULES = sorted(BOT.rglob("*.py"))
MAIN = BOT / "main.py"

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
        parsed = 0
        for module in BOT_MODULES:
            tree = _parse(module)
            if tree is None:
                continue
            parsed += 1
            for lineno, text in _string_constants(tree):
                for match in BAD_HINT.finditer(text):
                    if match.group(2) in PROSE_FOLLOWERS:
                        continue
                    offenders.append(f"  {module.name}:{lineno}  {match.group(0)!r}")
        self.assertGreater(parsed, 0, "no bot module could be parsed at all")
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
        """Find the module that DEFINES the table, wherever the split has put it."""
        defining = [m for m in BOT_MODULES
                    if re.search(r"^PRIVATE_LOCATION_EXITS", m.read_text(encoding="utf-8"), re.M)]
        self.assertEqual(len(defining), 1, f"expected exactly one definition, found {defining}")
        source = defining[0].read_text(encoding="utf-8")
        start = re.search(r"^PRIVATE_LOCATION_EXITS", source, re.M).start()
        block = source[start : start + 500]
        for command in ("/family → Leave", "/abode → Leave", "/innerworld → Leave"):
            self.assertIn(command, block, f"{command} missing from the exit table")


if __name__ == "__main__":
    unittest.main()


# --- arrow-form paths -------------------------------------------------------
# The check above catches "/family leave". It does NOT catch "/economy -> Market
# -> Buy", which is the right SHAPE with a wrong LABEL: the page is called "Local
# Market". That reads as correct to a reviewer and is a dead end to a player, and
# it shipped in the equipment hints before this test existed.
# Only the FIRST segment after the hub is a page; deeper segments are subgroups
# and leaf actions ("/quest -> Body Perfection -> Quest -> Attempt" is valid).
ARROW_HINT = re.compile(r"/([a-z]+)\s*(?:->|→)\s*([^→>*\n]+?)\s*(?:->|→)")


def _hub_pages() -> dict[str, set[str]]:
    """{hub name: {page label}} straight out of _HUB_DEFINITIONS in the source."""
    source = MAIN.read_text(encoding="utf-8")
    pages: dict[str, set[str]] = {}
    for block in re.finditer(
        r'HubDefinition\(\s*name="([a-z]+)"(.*?)\n    \),', source, re.S
    ):
        hub = block.group(1)
        labels = set(re.findall(r'_hub_page\(\s*"[a-z_]+",\s*"([^"]+)"', block.group(2)))
        labels |= set(re.findall(r'HubPage\(key="[a-z_]+", label="([^"]+)"', block.group(2)))
        if labels:
            pages[hub] = labels
    # /admin is a 17th hub, defined separately because it is GM-gated.
    admin = re.search(r"_ADMIN_HUB_DEFINITION = HubDefinition\((.*?)\n\)", source, re.S)
    if admin:
        pages["admin"] = set(re.findall(r'HubPage\(key="[a-z_]+", label="([^"]+)"', admin.group(1)))
    return pages


class ArrowPathTests(unittest.TestCase):
    def setUp(self):
        self.pages = _hub_pages()

    def test_the_hub_page_table_was_actually_parsed(self):
        # A parser that matched nothing would make the test below vacuous.
        self.assertGreaterEqual(len(self.pages), 10)
        self.assertIn("Local Market", self.pages.get("economy", set()))

    def test_every_arrow_path_names_a_real_hub_and_page(self):
        problems = []
        parsed = 0
        for path in sorted(BOT_MODULES):
            tree = _parse(path)
            if tree is None:
                continue
            parsed += 1
            for line, text in _string_constants(tree):
                for hub, page in ARROW_HINT.findall(text):
                    if hub not in self.pages:
                        problems.append(f"{path.name}:{line} /{hub} is not a hub")
                    elif page not in self.pages[hub]:
                        problems.append(
                            f"{path.name}:{line} /{hub} has no page {page!r} "
                            f"(pages: {sorted(self.pages[hub])})"
                        )
        self.assertGreater(parsed, 0, "no bot module could be parsed at all")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))
