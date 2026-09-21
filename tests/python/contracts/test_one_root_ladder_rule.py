"""What a spiritual root's grade may be is said once, in the content file (v1.0.11).

`content/world.json` declares `spiritual_root_system.grades` - six rungs, in
order, each with the `cultivation_mult` and `breakthrough_bonus` rc.55 wired.
Which names are rungs is therefore a fact the file already states, and it was
restated twice:

    go_core/internal/game/actions.go   a six-name map literal in adminSetSpiritualRoot
    dashboard/app.js                   a six-name array literal in gradeOpts

Neither could be wrong in an interesting way, because both agreed with the file
the day they were written - which is what made this the shape rc.44 removed for
the world currencies and v1.0.7 for the era roster rather than a live bug. The
day a rung is renamed or added, the engine refuses the real grade and accepts a
stale one, and the GM's picker offers what the engine will refuse (rc.46's rule,
from the wrong side of the counter).

What makes a wrong grade quiet rather than loud is `gradeIndex`, which answers 0
for a name it does not know: since rc.55 that is Mortal's 0.88x cultivation and
-1 on every breakthrough, for the character's whole life. A fallback that looks
like a value is not a sentinel.

**There is deliberately no tree-wide sweep here**, and the first draft of this
file is why. Written in `test_one_world_currency_rule.py`'s shape - a production
file naming three or more rungs is restating the ladder - it reported five
offenders and every one was a false positive: `Mortal`, `Earth`, `Heaven` and
`Immortal` are *also* manual grades (`cultivation_manual.go`), qi-body grades
(`qi_body.go`), world names (`law.py`, `playtest_engine.py`) and the generated
catalogue's tiers. That is CLAUDE.md's own "a name is not a reader" one level
out: the currency ids are unique strings and these are four ordinary words four
vocabularies share. A sweep whose every hit needs hand-checking is not a gate
(v1.0.1), so it was deleted rather than allowlisted - five entries would have
been five places for a real copy to hide.

What is held instead is the wire, on both sides and each where it can be told
apart. The engine's half is behavioural and lives in Go
(`TestTheRootGradeLeverAsksTheLadderRatherThanACopy`), because only a lever
driven against a *widened* ladder separates a reader from a copy that currently
agrees. The dashboard's half is here: the catalogue the browser is sent, and
`loadPlayerEditor`'s own body, read by brace matching rather than searched for
across the file.
"""
from __future__ import annotations

import json
import re
import unittest
from unittest import mock

from tests.support import PROJECT_ROOT

APP_JS = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")


def _world() -> dict:
    return json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _rungs() -> list[str]:
    return [
        str(rung.get("name") or "")
        for rung in (_world().get("spiritual_root_system") or {}).get("grades") or []
        if str(rung.get("name") or "")
    ]


def _function(source: str, name: str) -> str:
    """One JavaScript function body, by brace matching from its own header.

    The same reader `test_the_shell_reports_its_own_status.py` uses: there is no
    JS parser here, so it counts braces, and it refuses a truncated body rather
    than returning one, because a reader that silently finds half a function
    makes every assertion after it vacuous (rc.57).
    """
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    if not match:
        raise AssertionError(f"{name} is gone from app.js; this gate is guarding a function that moved")
    start = source.index("{", match.end() - 1)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"{name}'s body never closes; the gate is broken, not the tree")


class TheLadderIsWorthReading(unittest.TestCase):
    def test_the_content_file_declares_a_ladder(self):
        """Every assertion below is only meaningful while this holds."""
        rungs = _rungs()
        self.assertGreaterEqual(len(rungs), 4, "the ladder is missing from content/world.json")
        self.assertEqual(len(rungs), len(set(rungs)), f"two rungs share a name: {rungs}")


class ThePlayerEditorIsFedTheRealCatalogue(unittest.TestCase):
    """The two aptitude pickers are built from the file, not from the browser.

    `_aptitude_catalogue` is what makes the ladder reach `gradeOpts` and the
    physiques reach the card that could not grant one. rc.37 states the Player
    Editor's whole rationale - the ids a lever needs are picked, not typed - and
    v1.0.3's item-suggester finding is what a typed id costs when it is nearly
    right.
    """

    def setUp(self):
        from app.dashboard.server import _aptitude_catalogue

        _aptitude_catalogue.cache_clear()
        self.addCleanup(_aptitude_catalogue.cache_clear)
        self.catalogue = _aptitude_catalogue()

    def test_every_rung_the_file_carries_reaches_the_picker(self):
        offered = [entry["name"] for entry in self.catalogue["root_grades"]]
        self.assertEqual(offered, _rungs(), (
            "the grade picker is not offered the ladder the content file carries, in its own "
            "order - a grade is an order, so sorting it alphabetically would put Common above Earth"))

    def test_every_physique_the_file_carries_can_be_granted(self):
        offered = {entry["id"] for entry in self.catalogue["physiques"]}
        authored = {str(pid) for pid in (_world().get("physiques") or {})}
        self.assertEqual(offered, authored, (
            "the physique picker and the content file disagree about what exists. Before v1.0.11 "
            "the card offered nothing at all: the only writers of physique_id were character "
            "creation and samsara, so a GM could not correct one rolled wrong"))
        for entry in self.catalogue["physiques"]:
            self.assertTrue(entry["name"].strip(), f"{entry['id']} reaches the picker unnamed")

    def test_an_unreadable_content_file_offers_nothing_rather_than_guessing(self):
        """A surface must not offer what the engine will refuse (rc.46).

        An empty list makes each card say it has nothing to offer; a fallback
        list would be the hand-written copy this release exists to remove,
        wearing an exception's hat.
        """
        from app.dashboard.server import _aptitude_catalogue

        _aptitude_catalogue.cache_clear()
        with mock.patch("pathlib.Path.read_text", side_effect=OSError("no content file")):
            with self.assertLogs("xianxia.dashboard", level="WARNING"):
                empty = _aptitude_catalogue()
        self.assertEqual(empty, {"physiques": [], "root_grades": []})


class TheBrowserKeepsNoCopyOfTheLadder(unittest.TestCase):
    def setUp(self):
        self.body = _function(APP_JS, "loadPlayerEditor")

    def test_the_reader_works(self):
        self.assertIn("gradeOpts", self.body, (
            "the brace reader did not return loadPlayerEditor's body; the gate is broken, not "
            "the tree"))

    def test_both_pickers_are_built_from_what_the_server_sent(self):
        self.assertIn("aptitude_catalogue", self.body, (
            "loadPlayerEditor does not read the catalogue /api/player sends it, so its pickers "
            "are built from something else"))
        for source in ("root_grades", "physiques"):
            self.assertIn(source, self.body, f"the {source} picker is not fed from the catalogue")

    def test_no_rung_is_spelled_out_in_the_browser(self):
        """`gradeOpts` was `['Mortal','Common','Refined','Earth','Heaven','Immortal']`.

        Scoped to this one function rather than swept across the file, because
        four of the six names are ordinary words other vocabularies use - which
        is the sweep this file's docstring explains was deleted.
        """
        spelled = [rung for rung in _rungs() if f"'{rung}'" in self.body or f'"{rung}"' in self.body]
        self.assertEqual(spelled, [], (
            "loadPlayerEditor spells out rungs of the spiritual-root ladder. The ladder comes off "
            f"content/world.json with the row, through _aptitude_catalogue(): {spelled}"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
