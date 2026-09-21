"""A line that cannot know who is here does not claim to (v1.0.10).

**Found by playing.** The Family Hub header read:

    Here the East Gate of Cloudblade City, facing Ironbanner City ·
    Gate Captain Yue Dong

and `/talk`, opened in the same breath at the same place, offered
**Drillmaster Zhai Kang** - whom `content/world.json` puts at Cloudblade Blade
Yards in all five periods. The simulation had walked him to the gate.

Two lines, two sources, one place:

- `here_summary` appended
  ``sorted(n for n, npc in WORLD.npcs.items() if npc["location"] == name)`` -
  the content file's **residents**, read with no schedule and no simulation.
- the picker asks `npcs_present`, which reads the engine's rows.

Both were right by their own definition, and they disagreed - which is the
state v1.0.0-rc.28 forbids in as many words: *"a picker that offers somebody
/talk then refuses them is worse than either being wrong alone."* rc.28 fixed
the **cards** (`/scene status`, `/sense`) and v1.0.8 fixed the **picker**; the
*panel header* was the third reader and asked neither of them.

A synchronous function cannot answer this: who is standing somewhere is a
simulation row, an engine round trip away. So `here_summary` stops guessing and
names people only when a caller hands them over. Both production callers were
already inside async status builders, so each pays one `await` and nothing was
restructured.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
LOCATIONS = (BOT / "locations.py").read_text(encoding="utf-8")
SURFACE = (BOT / "surface.py").read_text(encoding="utf-8")
CARDS = (BOT / "status_cards.py").read_text(encoding="utf-8")


def _function(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


def _code(source: str, name: str) -> str:
    """The function's statements without its docstring.

    rc.52's rule, and this file needs it: the docstring below quotes the very
    expression the test forbids, so a scan of the whole body would find the
    fault in the prose explaining it and pass against a broken tree.
    """
    node = _function(source, name)
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.get_source_segment(source, stmt) or "" for stmt in body)


class TheHeaderNamesOnlyWhatItWasTold(unittest.TestCase):
    def test_the_reader_works(self):
        """Asserted before it is trusted (rc.57)."""
        self.assertIn(
            "WORLD.locations.get(name)", _code(LOCATIONS, "here_summary"),
            "the AST reader did not return here_summary's body; the gate is broken, not the tree",
        )

    def test_it_no_longer_reads_the_catalogue_for_who_is_present(self):
        body = _code(LOCATIONS, "here_summary")
        self.assertNotIn(
            "WORLD.npcs.items()", body,
            "here_summary is back to listing the content file's residents. That is who *lives* "
            "here, not who *is* here: the header named Gate Captain Yue Dong while /talk offered "
            "Drillmaster Zhai Kang, whom the simulation had walked to that gate.",
        )

    def test_it_takes_the_people_from_its_caller(self):
        node = _function(LOCATIONS, "here_summary")
        names = [a.arg for a in node.args.kwonlyargs] + [a.arg for a in node.args.args]
        self.assertIn(
            "present", names,
            "here_summary no longer accepts the people its caller resolved, so it can only guess "
            "or say nothing",
        )

    def test_a_caller_that_knows_nothing_names_nobody(self):
        """The honest half of what a pure function knows: describe the place,
        claim no people."""
        import os
        from unittest.mock import patch

        env = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
               "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
               "DATABASE_PATH": "data/test.sqlite3", "HEALTH_PORT": "18099"}
        with patch.dict(os.environ, env):
            import importlib

            locations = importlib.import_module("app.bot.locations")
        where = "Cloudblade City East Gate"
        self.assertIn(where, locations.WORLD.locations, "the catalogue lost the reported location")
        plain = locations.here_summary(where)
        self.assertTrue(plain, "here_summary said nothing at all about a real place")
        self.assertNotIn(
            "Yue Dong", plain,
            "the header still names the content file's resident when nobody told it who is here",
        )
        told = locations.here_summary(where, present=["Drillmaster Zhai Kang"])
        self.assertIn(
            "Drillmaster Zhai Kang", told,
            "the header ignores the people its caller resolved, so asking costs a round trip and "
            "changes nothing",
        )


class BothHeadersAskTheOneResolver(unittest.TestCase):
    """The same resolver `/talk` asks, or the two can disagree again."""

    def test_the_helper_asks_npcs_present(self):
        body = _code(CARDS, "_who_is_here")
        self.assertIn(
            "npcs_present(", body,
            "_who_is_here no longer asks npcs_present, so the header and the picker are back to "
            "two sources for one question",
        )

    def test_a_failed_lookup_costs_the_names_and_not_the_card(self):
        """The Here line is drawn beside everything else a panel shows."""
        node = _function(CARDS, "_who_is_here")
        self.assertTrue(
            any(isinstance(n, ast.ExceptHandler) for n in ast.walk(node)),
            "_who_is_here can raise, and it is called while building a status card - so one "
            "unavailable lookup would cost the whole panel rather than one line of it",
        )

    def test_every_here_line_hands_the_people_over(self):
        for label, source, function in (
            ("the hub panel header", SURFACE, "_here_field"),
            ("the menu facts line", CARDS, "menu_facts_line"),
        ):
            with self.subTest(label=label):
                body = _code(source, function)
                self.assertIn(
                    "present=", body,
                    f"{label} calls here_summary without the people it resolved, so that header "
                    "goes back to naming whoever content says lives there",
                )
                self.assertIn("_who_is_here", body)


if __name__ == "__main__":
    unittest.main()
