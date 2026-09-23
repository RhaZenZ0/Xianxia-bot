"""A rumour is public, distinct, and about this city (v1.0.13).

**Found by playing**, and the report is the whole of the first half: the page
printed *"Xie Kormaq discovered Ironbanner City"* **five times**, over a
`/city rumours` opened in Ashenwall City, about a route charted from Frostwatch
City North Gate.

`get_structured_world_history`'s relevance clause is an **OR** -
`location=? OR related_user_id=? OR actor_key=? OR target_key=?` - and the page
called it once per place with `user_id=` filled in, so every row about the
asking player came back for *every* place queried. Ashenwall City has seven
parts, so one discovery was eight rows.

**The second half is why the fix is not a `set()`.** That function's own
docstring says it *"deliberately returns a superset. The RAG retriever performs
the final viewpoint/visibility check so one code path owns knowledge safety"* -
and `city_rumours` was a second consumer performing neither. `rag.py` dedupes by
`history_id` into a dict and drops `hidden` at its line 265; the rumours page did
neither, so a landlady could repeat a `participant` row the player alone was
party to, and a `hidden` one - `npc_deeds` writes an unwitnessed robbery and a
contraband drop at an NPC's own location, which is a city.

The gate holds the rule rather than the spelling: the page may not ask about the
player, and what it prints is public, distinct and ordered.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

SOURCE = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


def _rumours():
    """The helper itself, compiled from its own source.

    Importing the module would boot the whole bot for one pure function, so
    the gate compiles that one `def` against a namespace carrying only what it
    reads. It is the same helper either way - this file is the source of it.
    """
    node = _function("rumours_a_city_has_heard")
    namespace: dict[str, object] = {"Any": object, "RUMOUR_LIMIT": _rumour_limit()}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<gate>", "exec"), namespace)  # noqa: S102
    return namespace["rumours_a_city_has_heard"]


def _rumour_limit() -> int:
    for node in ast.walk(TREE):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "RUMOUR_LIMIT" for t in node.targets)
                and isinstance(node.value, ast.Constant)):
            return int(node.value.value)
    raise AssertionError("RUMOUR_LIMIT is gone; the gate is broken, not the tree")


def _row(history_id: int, *, visibility: str = "public", minute: int = 0, title: str = "t") -> dict:
    return {"history_id": history_id, "visibility": visibility, "game_minute": minute, "title": title}


class ARumourIsWhatTheCityHeard(unittest.TestCase):
    def setUp(self):
        self.pick = _rumours()
        # A reader asserted before it is trusted (rc.57): a helper that came
        # back doing nothing would make every assertion below vacuous.
        self.assertEqual([r["history_id"] for r in self.pick([_row(1)])], [1],
                         "the helper did not pass a plain public row through; the gate is broken, not the tree")

    def test_the_same_row_is_printed_once(self):
        """The reported bug: one discovery, once per place queried."""
        eight = [_row(7, title="Xie Kormaq discovered Ironbanner City") for _ in range(8)]
        titles = [r["title"] for r in self.pick(eight)]
        self.assertEqual(len(titles), 1, (
            f"one row came back {len(titles)} times. Ashenwall City and its seven parts are eight "
            "queries, and the OR-relevance clause returned the player's own row for every one of "
            "them - which is exactly what a player saw printed five times"))

    def test_only_public_news_is_repeated(self):
        for visibility in ("participant", "hidden", "faction"):
            with self.subTest(visibility=visibility):
                kept = self.pick([_row(1, visibility=visibility)])
                # assertFalse, not assertEqual: a list diff prints first and
                # the finding last (v1.0.8).
                self.assertFalse(kept, (
                    f"a {visibility!r} row was offered as a rumour. get_structured_world_history "
                    "returns a superset and names RAG as the one path that filters; a teller "
                    "repeating this knows something the tree says nobody in the city knows"))

    def test_the_newest_lead_and_the_page_is_bounded(self):
        rows = [_row(i, minute=i * 10) for i in range(1, 20)]
        picked = self.pick(rows)
        self.assertEqual(len(picked), _rumour_limit())
        self.assertEqual([r["history_id"] for r in picked], sorted([r["history_id"] for r in picked], reverse=True),
                         "rumours are not ordered newest first")

    def test_the_page_never_asks_about_the_player(self):
        """The duplication's actual cause, held where it happened."""
        offenders = []
        for node in ast.walk(_function("city_rumours")):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name != "get_structured_world_history":
                continue
            offenders += [f"line {node.lineno}: {kw.arg}=" for kw in node.keywords if kw.arg == "user_id"]
        self.assertFalse(offenders, (
            "the rumours page asks get_structured_world_history about the player. Its relevance "
            "clause is an OR, so every row about them is returned for every place queried, and the "
            f"page prints one discovery once per part of the city: {offenders}"))

    def test_the_selection_goes_through_the_one_helper(self):
        body = ast.dump(_function("city_rumours"))
        self.assertIn("rumours_a_city_has_heard", body, (
            "city_rumours selects its own rumours again rather than asking the helper, so the "
            "public-only and dedupe rules hold in one place and not in the page that prints them"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
