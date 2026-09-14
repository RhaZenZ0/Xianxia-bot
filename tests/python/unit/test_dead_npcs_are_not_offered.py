"""A dead NPC stops being somebody you can meet (v1.0.0-rc.23).

`current_npc_location` answered None for two different questions - "this NPC is
dead" and "nothing knows where they are" - and every caller read None as the
second. So the dead were offered in every picker at every location, `/talk`
carried words to a corpse, and `/npcinfo` showed one standing where they fell.

These are source scans because `app/bot` imports discord; they pin the
behaviour at the four places that had to learn the difference.
"""

import unittest

from tests.support import bot_function_source, bot_package_source


class DeadNPCsAreNotOfferedTests(unittest.TestCase):
    def test_the_sentinel_exists_and_is_not_a_place(self):
        source = bot_package_source()
        self.assertIn('DEAD = "\\x00dead"', source)

    def test_current_npc_location_separates_dead_from_unknown(self):
        source = bot_function_source("current_npc_location")
        self.assertIn("return DEAD", source)
        # An NPC with no simulation state at all is still "unknown", not dead:
        # that is the existing "do not filter on what you do not know" rule and
        # it has to survive.
        self.assertIn('not in ("", "alive", "missing")', source)
        # ...and a missing NPC is neither: they are exactly where they are,
        # the world simply does not know it (schema 47).
        self.assertIn('if status == "missing":', source)
        self.assertIn("return None", source)

    def test_the_picker_skips_the_dead(self):
        source = bot_function_source("local_npc_autocomplete")
        self.assertIn("if npc_location == DEAD:", source)
        self.assertIn("continue", source)
        # ...and still offers a living NPC nothing knows the location of.
        self.assertIn("if location and npc_location and npc_location != location:", source)

    def test_talk_refuses_a_corpse(self):
        source = bot_function_source("talk")
        self.assertIn("if npc_location == DEAD:", source)
        self.assertIn("is dead", source)

    def test_npcinfo_says_so_rather_than_showing_a_last_location(self):
        source = bot_function_source("npc_info_command")
        self.assertIn("resolved_location == DEAD", source)
        self.assertIn("🪦", source)

    def test_a_dead_sponsor_recommends_nobody(self):
        source = bot_package_source()
        self.assertIn("is dead and recommends nobody", source)


if __name__ == "__main__":
    unittest.main()
