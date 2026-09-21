"""The dashboard's own status line is not one view's leftovers (v1.0.8).

Reported from live play, as what the sidebar footer actually read:

    SQLite
    engine —

Those are the literal placeholders in `dashboard/index.html`. Both spans were
painted **only** inside `loadOverview`, so on any other view they sat on that
text for as long as the tab stayed open - and landing on a deep link
(`#admin`, a bookmark, a reload anywhere but Overview) never painted them at
all, because `switchView` runs one loader and `loadOverview` was not it.

**Half of the wire was already there**, which is what makes this the shape this
repository keeps recording rather than an oversight: the boot path fetched
`/api/overview` when it started on another view and used the response to set
the world clock **and nothing else**, three lines above the two spans made from
the same response. The data was fetched and thrown away.

And a placeholder that looks like a value is not a sentinel - the
`seller_user_id=0` lesson in a readout. "engine —" reads as an engine that
answered and had nothing to say; a GM could not tell it from an unreachable
one. `shellStatusUnknown` is what says which.
"""
from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

DASHBOARD = PROJECT_ROOT / "dashboard"
APP_JS = (DASHBOARD / "app.js").read_text(encoding="utf-8")
INDEX = (DASHBOARD / "index.html").read_text(encoding="utf-8")

# The spans the shell owns: the world clock in the topbar and the two footer
# lines. Every one of them was Overview's to paint and nobody else's.
SHELL_SPANS = ("worldClock", "schema", "engineHealth")


def _function(source: str, name: str) -> str:
    """One JavaScript function body, by brace matching from its own header.

    There is no JS parser here, so this counts braces - and it asserts it found
    a balanced body rather than returning a truncated one, because a reader
    that silently finds half a function makes every assertion after it vacuous
    (rc.57).
    """
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    if not match:
        raise AssertionError(f"{name} is gone from app.js; this gate is guarding a function that moved")
    start = source.index("{", match.end() - 1)
    depth, i = 0, start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"{name}'s body never closes; the gate is broken, not the tree")


class TheShellPaintsItselfFromAnywhere(unittest.TestCase):
    def test_the_reader_works(self):
        body = _function(APP_JS, "loadOverview")
        self.assertIn(
            "api('/api/overview')", body,
            "the brace reader did not return loadOverview's body; the gate is broken, not the tree",
        )

    def test_one_painter_owns_every_shell_span(self):
        """Not "is each span written somewhere" - that was true before, in the
        one view that could not reach the others."""
        painter = _function(APP_JS, "paintShellStatus")
        for span in SHELL_SPANS:
            self.assertIn(
                f"'{span}'", painter,
                f"paintShellStatus does not write #{span}, so that line of the shell is painted "
                "by whichever view happens to remember to",
            )

    def test_no_view_loader_paints_the_shell_behind_the_painter_s_back(self):
        for name in ("loadOverview",):
            body = _function(APP_JS, name)
            for span in SHELL_SPANS:
                self.assertNotIn(
                    f"getElementById('{span}')", body,
                    f"{name} writes #{span} directly again. That is how this broke: the span was "
                    "one view's to paint, so every other view left it on its placeholder.",
                )

    def test_landing_on_another_view_still_paints_it(self):
        """The reported bug, exactly: open the dashboard on `#admin` and the
        footer never gets painted at all."""
        # assertTrue over a search, not assertRegex: assertRegex prints the whole
        # haystack on failure, and the haystack here is a 1,100-line file. A gate
        # whose message has to be scrolled past is one nobody reads.
        self.assertTrue(
            re.search(r"if\(boot!=='overview'\)\s*refreshShellStatus\(\)", APP_JS),
            "the boot path no longer paints the shell when it starts on another view, so a deep "
            "link leaves the footer reading 'SQLite' and 'engine —' until the GM clicks Overview",
        )

    def test_it_keeps_ticking_off_the_overview(self):
        """A frozen 'simulation current' is worse than no line at all: it is
        the line a GM reads to know the world is still running."""
        interval = APP_JS[APP_JS.index("setInterval(") :]
        self.assertIn(
            "refreshShellStatus()", interval,
            "the poll no longer refreshes the shell off the Overview, so the footer freezes at "
            "whatever it said when the GM navigated away",
        )

    def test_an_unreachable_engine_says_so(self):
        body = _function(APP_JS, "refreshShellStatus")
        self.assertIn(
            "shellStatusUnknown()", body,
            "a failed status fetch no longer says so, so the footer keeps showing the last good "
            "reading - or its placeholder - while the engine is down",
        )
        unknown = _function(APP_JS, "shellStatusUnknown")
        self.assertIn("status-dot bad", unknown)

    def test_the_placeholders_read_as_loading_not_as_an_answer(self):
        """`SQLite` and `engine —` are indistinguishable from a real readout,
        which is why this went unreported for as long as it did: the footer did
        not look broken."""
        footer = INDEX[INDEX.index('id="schema"') : INDEX.index("</aside>")]
        for looks_like_an_answer in (">SQLite<", ">engine —<"):
            self.assertNotIn(
                looks_like_an_answer, footer,
                f"the footer placeholder {looks_like_an_answer!r} reads as a value rather than as "
                "a state that has not loaded yet",
            )


if __name__ == "__main__":
    unittest.main()
