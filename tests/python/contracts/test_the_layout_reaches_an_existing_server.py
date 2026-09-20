"""A layout change that only reaches a fresh guild reaches nobody (v1.0.0-rc.59).

This is the gate for a class this repo has now named six times. `/learn`
(rc.43) was implemented, priced and authored and sat on no page. The quest
journal (rc.46) offered what nothing handed over. The event bands (rc.49) were
filtered on by content that never set them. The peach (rc.50) had two working
halves and no producer. The auction channels (rc.51) were created in the right
category on a new server and left wherever they were on an old one - and that
last one is exactly this, found once and fixed once.

Because it was fixed *once*. `ensure_auction_house_channels` gained a
re-parent in rc.51 and `ensure_world_event_channels` in rc.52, while
`ensure_base_xianxia_channels`, `ensure_realm_hub_channels` and
`ensure_bugs_forum_channel` never did - so until this release a `#world-events`
or a realm capital or the `#bugs` forum that already existed was bound where it
lay and stayed there for ever. `channel_messages.py`'s own guide text has
claimed since rc.52 that Repair "moves existing ones into the category they
belong in", which was true of two helpers out of five.

The same sentence is true of the read-only overwrite: it was computed inside
`if channel is None and can_create:`, so `#xianxia-info`, `#expeditions` and
`#player-homes` were read-only only on a server where the bot had created them.

So this asserts the rule for **every** helper that places a channel in a
category, rather than for the ones somebody remembered.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT, bot_function_source

#: Every helper that puts a channel in a category, and the module it lives in.
PLACING_HELPERS = {
    "ensure_base_xianxia_channels": "app/bot/admin/channel_messages.py",
    "ensure_realm_hub_channels": "app/bot/channels.py",
    "ensure_auction_house_channels": "app/bot/channels.py",
    "ensure_world_event_channels": "app/bot/channels.py",
    "ensure_bugs_forum_channel": "app/bot/admin/bugs_forum.py",
}

#: A placing helper allowed not to re-parent, with the reason. Empty, and
#: empty the day it was written: every one of the five moves a channel it
#: finds in the wrong category.
NEVER_MOVES: dict[str, str] = {}


class EveryHelperMovesAChannelItFindsInTheWrongPlace(unittest.TestCase):
    def test_the_sweep_still_sees_the_helpers(self):
        """A reader that silently finds nothing makes every assertion after it
        vacuous - the rc.57 lesson, and the rc.58 drill that caught it."""
        for name, module in PLACING_HELPERS.items():
            with self.subTest(helper=name):
                self.assertTrue((PROJECT_ROOT / module).exists(), module)
                source = bot_function_source(name)
                self.assertTrue(source.strip(), f"{name} has no source")
                self.assertIn("can_create", source, f"{name} no longer gates on can_create")

    def test_every_helper_re_parents(self):
        offenders = []
        for name in sorted(PLACING_HELPERS):
            if name in NEVER_MOVES:
                continue
            source = bot_function_source(name)
            compares = "category_id != category.id" in source
            moves = "edit(category=category" in source
            if not (compares and moves):
                offenders.append(
                    f"{name}: compares={compares} moves={moves}")
        self.assertEqual(offenders, [], (
            "a helper that places a channel in a category but never moves one it finds "
            "elsewhere reaches a fresh guild and no server anybody is running"))

    def test_the_move_is_behind_can_create(self):
        """Discord layout is the dashboard's to own: the `/admin` slash path
        validates and binds, and must not silently re-arrange a server."""
        for name in sorted(PLACING_HELPERS):
            if name in NEVER_MOVES:
                continue
            with self.subTest(helper=name):
                source = bot_function_source(name)
                for line in source.splitlines():
                    if "edit(category=category" in line:
                        break
                else:
                    self.fail(f"{name} does not move a channel")
                self.assertIn("can_create", source)

    def test_read_only_is_applied_to_a_channel_that_already_existed(self):
        """The other half, and the one no test could see before.

        `READ_ONLY_BASE_CHANNELS` was consumed at exactly one place - the
        `overwrites=` argument of `create_text_channel` - so a bound channel
        was never locked. Read by AST rather than by substring: the set's name
        appearing in the function proves nothing about *where*.
        """
        source = bot_function_source("ensure_base_xianxia_channels")
        tree = ast.parse(source)

        creating: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = ast.unparse(node.test)
            if "channel is None" not in test:
                continue
            for child in ast.walk(node):
                creating.add(id(child))

        outside = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "READ_ONLY_BASE_CHANNELS"
            and id(node) not in creating
        ]
        self.assertTrue(outside, (
            "READ_ONLY_BASE_CHANNELS is read only inside the `channel is None` branch, "
            "so a channel the bot did not create is never made read-only"))

    def test_the_allowlist_is_empty(self):
        self.assertEqual(NEVER_MOVES, {}, (
            "an entry here is a new decision about a helper that may leave a channel "
            "where it found it, never a backlog inherited from rc.59"))


if __name__ == "__main__":
    unittest.main()
