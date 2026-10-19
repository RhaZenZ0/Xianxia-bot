"""No element in the dashboard is given an id the browser's window already owns.

The dashboard reaches its controls by bare id (`forceSim.onclick = ...`), which
works because a browser exposes every element id as a property of `window`. The
exposure does not override a property the window already has: `<button
id="setInterval">` wired as `setInterval.onclick = ...` set a property on the
timer function, the button stayed dead, and nothing failed - the Simulation
card's "Set interval" did nothing for as long as it shipped, with the Discord
command beside it working.

So no id in `dashboard/index.html` or the templates in `dashboard/app.js` may be
one of the names below. The list is the window properties a control is likely to
be named after; `node`'s own globals are not the browser's, so it is held as a
literal rather than read.
"""
from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

WINDOW_PROPERTIES = {
    "setInterval", "setTimeout", "clearInterval", "clearTimeout", "status", "name", "length", "top", "parent",
    "close", "closed", "open", "print", "stop", "focus", "blur", "alert", "confirm", "prompt", "event", "history",
    "location", "screen", "scroll", "self", "frames", "window", "document", "opener", "origin", "fetch", "find",
    "navigator", "performance", "external", "menubar", "toolbar", "statusbar", "scrollbars", "locationbar",
    "personalbar", "scrollX", "scrollY", "innerWidth", "innerHeight", "outerWidth", "outerHeight", "crypto",
    "localStorage", "sessionStorage", "caches", "customElements", "onload", "onerror", "onclick",
}

DASHBOARD = PROJECT_ROOT / "dashboard"
ID = re.compile(r"""\bid=["']([A-Za-z_][\w-]*)["']""")


def ids(name: str) -> set[str]:
    return set(ID.findall((DASHBOARD / name).read_text(encoding="utf-8")))


class NoDashboardIdShadowsAWindowProperty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A reader that finds nothing would make the check below vacuous.
        cls.html = ids("index.html")
        cls.app = ids("app.js")
        assert {"nav", "app", "drawer"} <= cls.html, f"index.html ids read as {sorted(cls.html)}"
        assert {"forceSim", "simSystem", "simSteps"} <= cls.app, f"app.js ids read as {sorted(cls.app)}"

    def test_no_id_is_a_window_property(self):
        for name, found in (("index.html", self.html), ("app.js", self.app)):
            clash = sorted(found & WINDOW_PROPERTIES)
            self.assertEqual(
                clash,
                [],
                f"{name} gives an element the id {clash}, which the window already owns: a bare reference to it "
                f"reaches the window's property, not the element, and the control is dead (the Set interval button, v1.34.0)",
            )

    def test_the_simulation_controls_are_still_wired_by_their_ids(self):
        # The rename must not have left a handler on the old name.
        source = (DASHBOARD / "app.js").read_text(encoding="utf-8")
        self.assertIn("setSimInterval.onclick=", source)
        self.assertNotRegex(source, r"(?m)^\s*setInterval\.onclick")


if __name__ == "__main__":
    unittest.main()
