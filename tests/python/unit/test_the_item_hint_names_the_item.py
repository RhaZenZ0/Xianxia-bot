"""An unknown item is answered with the item (v1.0.3).

The Admin Console's Inventory card refuses a display name and offers the closest
ids, so a GM can correct the field instead of the database. It offered the wrong
ones. Typing **"Qi Nourishment Pills"** answered:

    Did you mean: advanced_demonic_002_qi_refiner_manual,
    advanced_demonic_008_qi_refiner_manual, ...

Five demonic cultivation manuals, for a pill. The filter took `any()` token hit -
so the single token "qi" was enough - and then **sorted the survivors by id**, so
the 142 generated `advanced_*` ids win every time on the letter 'a'. `qi_pill`,
which is the item, matched exactly as well and was never shown.

And the near-misses a GM actually types are inflections - "Nourishment" for
"Nourishing", "Pills" for "Pill" - which a substring test cannot see at all.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _suggest(typed: str) -> list[str]:
    """The dashboard's ranking, driven through the real server module."""
    from app.dashboard.server import AdminDashboardController

    catalog = {item_id: str(entry.get("name") or item_id) for item_id, entry in WORLD["items"].items()}
    server = AdminDashboardController.__new__(AdminDashboardController)
    server.item_catalog = lambda: catalog  # type: ignore[method-assign]
    try:
        server.resolve_item_id(typed)
    except ValueError as exc:
        message = str(exc)
        if "Did you mean: " not in message:
            return []
        return [s.strip() for s in message.split("Did you mean: ", 1)[1].rstrip("?").split(", ")]
    return []


class TheItemHintNamesTheItemTests(unittest.TestCase):
    def test_the_reported_case_answers_with_the_pill(self):
        suggestions = _suggest("Qi Nourishment Pills")
        self.assertTrue(suggestions, "an unknown item must still be answered with the closest ids")
        self.assertTrue(
            suggestions[0].startswith("qi_pill"),
            f"'Qi Nourishment Pills' should first suggest qi_pill; it offered {suggestions}",
        )

    def test_a_suggestion_carries_the_name_a_gm_was_reading(self):
        """The id alone does not tell a GM which of five near-identical ids is
        the thing they typed the display name of."""
        self.assertIn("(Qi Nourishing Pill)", " ".join(_suggest("Qi Nourishment Pills")))

    def test_a_generated_manual_no_longer_wins_on_the_letter_a(self):
        for typed in ("Qi Nourishment Pills", "Recovery Pills", "Spirit Herbs"):
            with self.subTest(typed=typed):
                first = _suggest(typed)[0]
                self.assertFalse(
                    first.startswith("advanced_"),
                    f"{typed!r} still answers with a generated manual first: {first}",
                )

    def test_an_exact_id_and_an_exact_name_still_resolve(self):
        """The hint is the last resort; the two things that should just work
        must not have been broken to get it."""
        from app.dashboard.server import AdminDashboardController

        catalog = {item_id: str(entry.get("name") or item_id) for item_id, entry in WORLD["items"].items()}
        server = AdminDashboardController.__new__(AdminDashboardController)
        server.item_catalog = lambda: catalog  # type: ignore[method-assign]
        self.assertEqual("qi_pill", server.resolve_item_id("qi_pill"))
        self.assertEqual("qi_pill", server.resolve_item_id("Qi Nourishing Pill"))


if __name__ == "__main__":
    unittest.main()
