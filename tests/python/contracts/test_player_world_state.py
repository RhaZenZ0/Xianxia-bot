"""The world_state rows a player owns are cleared by a rebirth, a reset and an
erasure (v1.34.0).

`world_state` is the engine's key/value shelf, and seven of its keys are one
person's: `<prefix>:<discord user id>`. No schema walk finds them - the id is
inside the key, not in a column - so the rebirth wipe, the reset sweep and the
erasure sweep each passed them over, and the next body inherited the last one's
banked insight and its lifespan clock's `PausedGameMinutes`.

`go_core/internal/game/player_world_state.go` is the one list of those prefixes
and `clearPlayerWorldStateTx` the one function that deletes them. The Go tests
write every key and hold each door to clearing it. This is the other half: it
reads the prefixes off the Go and holds the list equal to the key functions the
engine actually has, so a new writer that nobody put on the list fails here
rather than leaving a row in a table that has no owner to ask.
"""
from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

GAME = PROJECT_ROOT / "go_core" / "internal" / "game"
LIST_GO = (GAME / "player_world_state.go").read_text(encoding="utf-8")

# `func xKey(userID int64) string { return fmt.Sprintf("<prefix>:%d", userID) }`
# in either layout. By shape rather than by name, so a writer called something
# new is found as well as the seven that are called `...Key`.
KEY_FUNCTION = re.compile(
    r'func\s+(\w+)\(userID int64\)\s+string\s*\{\s*return\s+fmt\.Sprintf\("([a-z_]+):%d",\s*userID\)\s*\}'
)


def listed_prefixes() -> list[str]:
    match = re.search(r"var playerWorldStatePrefixes = \[\]string\{(.*?)\n\}", LIST_GO, re.S)
    if not match:
        raise AssertionError("playerWorldStatePrefixes is not a slice literal any more")
    return re.findall(r'"([a-z_]+)"', match.group(1))


def written_prefixes() -> dict[str, str]:
    """prefix -> the function that spells it, over every non-test Go file."""
    found: dict[str, str] = {}
    for path in sorted((PROJECT_ROOT / "go_core").rglob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        for name, prefix in KEY_FUNCTION.findall(path.read_text(encoding="utf-8")):
            found[prefix] = name
    return found


class EveryPlayerKeyIsOnTheList(unittest.TestCase):
    def test_the_readers_find_what_they_are_meant_to(self):
        """Asserted before it is trusted: a reader that finds nothing makes the
        equality below hold of two empty sets."""
        listed, written = listed_prefixes(), written_prefixes()
        self.assertGreaterEqual(len(listed), 7, "the list reader found fewer than the seven known prefixes")
        for known in ("cultivation_insight", "player_lifespan_clock", "road_transit"):
            self.assertIn(known, written, f"the writer reader no longer finds {known}; the gate is broken, not the tree")

    def test_the_list_is_exactly_the_keys_the_engine_writes(self):
        listed, written = set(listed_prefixes()), written_prefixes()
        self.assertEqual(
            sorted(set(written) - listed), [],
            "These player-keyed world_state keys have a writer and are on no list "
            f"({', '.join(f'{p} from {written[p]}' for p in sorted(set(written) - listed))}). "
            "Add the prefix to playerWorldStatePrefixes in go_core/internal/game/player_world_state.go: "
            "otherwise a rebirth, a reset and an erasure all leave the row behind.",
        )
        self.assertEqual(
            sorted(listed - set(written)), [],
            "These prefixes are on the list and nothing writes them any more "
            f"({', '.join(sorted(listed - set(written)))}): a list entry for a retired key reads as a decision.",
        )

    def test_the_list_has_no_duplicates(self):
        listed = listed_prefixes()
        self.assertEqual(len(listed), len(set(listed)))

    def test_the_two_sweeps_and_the_rebirth_call_the_one_function(self):
        """By call, not by substring: each body is read without its comments."""
        def body(path: str, name: str) -> str:
            source = (GAME / path).read_text(encoding="utf-8")
            start = source.index(f"func {name}(")
            end = source.index("\nfunc ", start + 1)
            return re.sub(r"//[^\n]*", "", source[start:end])

        for path, name in (
            ("lifecycle_actions.go", "clearIncarnationStateTx"),
            ("privacy_actions.go", "applyErasureTargets"),
        ):
            with self.subTest(door=name):
                self.assertIn(
                    "clearPlayerWorldStateTx(conn, userID)", body(path, name),
                    f"{name} no longer clears the player-keyed world_state rows",
                )


if __name__ == "__main__":
    unittest.main()
