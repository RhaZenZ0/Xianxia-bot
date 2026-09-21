"""A dead NPC stops being somebody you can meet (v1.0.0-rc.24).

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
        """The rule, not its spelling (v1.0.8).

        This used to assert three substrings of the picker's own catalogue
        tail - `if npc_location == DEAD:` and the location filter beside it -
        and when v1.0.8 replaced that tail with the one resolver the gate went
        red against correct code while never having checked the rule itself
        anywhere. A gate that pins where a rule is written rather than that it
        holds fails exactly when the rule is moved, which is the one time it
        should stay green.

        So it holds the wire instead: the picker asks `npcs_present`, which is
        where refusing the dead now lives - on all three of its paths, the
        engine's `status IN ('alive','missing')`, the registry's hardcoded
        `'alive'`, and `current_npc_location` answering DEAD for the
        catalogue. `WhoIsHereRefusesTheDead` in
        `tests/python/contracts/test_who_is_here.py` is the behavioural half,
        and it drives the third.
        """
        source = bot_function_source("local_npc_autocomplete")
        self.assertIn(
            "npcs_present(location, wt.period)", source,
            "the picker no longer asks npcs_present, so nothing filters the dead out of it: "
            "the resolver is where that rule lives since v1.0.8",
        )
        # A grave is still a name you can address, and it is added by the block
        # above deliberately - the dead are excluded as *people standing here*,
        # not as names (schema 48). The two must not be collapsed.
        self.assertIn("DB.list_graves_at(location)", source)

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


class GravesAreFoundWhereTheyStandTests(unittest.TestCase):
    """Schema 48. A grave is a name you can address that the catalogue picker
    filters out, because the dead are filtered out of it. It belongs in the
    list exactly where it stands - the same rule the missing follow."""

    def test_the_picker_offers_a_grave_where_it_stands(self):
        source = bot_function_source("local_npc_autocomplete")
        self.assertIn("DB.list_graves_at(location)", source)
        # Beside the live event cast, and for the same reason.
        self.assertIn("DB.list_active_event_npcs(location)", source)

    def test_talk_at_a_grave_finds_the_answer_instead_of_refusing(self):
        source = bot_function_source("talk")
        self.assertIn("_claim_grave_if_here", source)
        # The plain refusal survives as the fallback when there is no grave here.
        self.assertIn("is dead. Whatever you have to say to them", source)

    def test_the_grave_is_claimed_through_the_engine_not_python(self):
        source = bot_function_source("_claim_grave_if_here")
        self.assertIn('"npc.found"', source)
        self.assertIn('"location"', source)
        # Not handler-shaped: it takes a bare user id, so it cannot ack and
        # does not have to - the caller defers before reaching it.
        self.assertIn("async def _claim_grave_if_here(user_id: int", source)
        self.assertNotIn("interaction.response", source)
        # Python reports; it does not decide. An already-visited grave still
        # says what it says and hands over nothing.
        self.assertIn('result.get("already_claimed")', source)
        self.assertIn('result.get("claimed")', source)
