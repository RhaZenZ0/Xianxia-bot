"""The commission selection ladder (v0.22.0, app/rules/commissions.py).

The ladder is the half of the design that is pure: given standing, a held
commission, a cooldown and a pool, which of five things does the giver do?
Every branch here is reachable without a database, without Discord and without
a model, which is the point - the expensive parts of a commission (the engine
transaction, the voice call) only ever run for a branch this function chose.

Ordering is the property under test as much as the branches are: a player who
is holding one must not be told about the cooldown, and a player on cooldown
must not be shown a pool match, or the Steward contradicts himself.
"""
from __future__ import annotations

import unittest

from app.rules import commissions as rules

POOL = [
    {"quest_key": "c_tier1_a", "title": "The Replaced Crate", "tier": 1, "realm_band": "0-2",
     "objectives": [{"label": "Investigate the landings"}], "rewards": {"spirit_stones": 40},
     "deadline_game_minutes": 3 * 1440,
     "variants": [{"label": "standard", "rewards": {"spirit_stones": 40}, "deadline_game_minutes": 3 * 1440},
                  {"label": "rushed", "rewards": {"spirit_stones": 24}, "deadline_game_minutes": 1440}]},
    {"quest_key": "c_tier1_b", "title": "A Quiet Valuation", "tier": 1, "realm_band": "", "rewards": {}},
    {"quest_key": "c_tier2", "title": "The Marsh Consignment", "tier": 2, "realm_band": "1-4", "rewards": {}},
    {"quest_key": "c_tier3", "title": "Terms With the Furnace", "tier": 3, "realm_band": "2-6", "rewards": {}},
]


def offer(**overrides):
    kwargs = {
        "giver": "Steward Qiao",
        "relationship": {"trust": 5, "respect": 5, "grudge": 0},
        "held_commission": None,
        "cooldown_game_minutes": 0,
        "pool": POOL,
        "taken_keys": set(),
        "realm_index": 1,
        "user_id": 4242,
        "game_minute": 100_000,
        "player_sect": "",
    }
    kwargs.update(overrides)
    return rules.choose_offer(**kwargs)


SECT_BOARD = [
    {"quest_key": "s_azure_1", "title": "The Gate Roster", "tier": 1, "realm_band": "",
     "requires_sect": "Azure Cloud Sect", "rewards": {"spirit_stones": 35}},
    {"quest_key": "s_azure_2", "title": "A Word With the Inquisitor", "tier": 2, "realm_band": "",
     "requires_sect": "Azure Cloud Sect", "rewards": {"spirit_stones": 85}},
]

UNDISCLOSED_POOL = [
    {"quest_key": "u_bowl", "title": "A Bowl of Rice", "tier": 1, "realm_band": "",
     "reward_visibility": "hidden", "boast": "Bring the bowl back empty.",
     "rewards": {"spirit_stones": 150, "insight_xp": 40},
     "variants": [{"label": "his terms", "rewards": {"spirit_stones": 150, "insight_xp": 40},
                   "deadline_game_minutes": 4 * 1440}]},
]


class StandingTests(unittest.TestCase):
    def test_standing_is_derived_not_stored(self):
        self.assertEqual(rules.standing_value({"trust": 20, "respect": 10, "grudge": 5}), 25)
        self.assertEqual(rules.standing_value(None), 0)

    def test_the_bands_and_their_ceilings(self):
        for value, band in ((-60, "hostile"), (-20, "cold"), (0, "neutral"), (20, "warm"), (80, "trusted")):
            with self.subTest(value=value):
                self.assertEqual(rules.standing_band(value), band)
        # The ladder is monotonic: standing never buys a *lower* ceiling.
        ceilings = [rules.TIER_CEILING[rules.standing_band(v)] for v in (-60, -20, 0, 20, 80)]
        self.assertEqual(ceilings, sorted(ceilings))

    def test_a_hostile_giver_offers_nothing_at_all(self):
        result = offer(relationship={"trust": -30, "respect": -20, "grudge": 30})
        self.assertEqual(result.kind, "unavailable")
        self.assertEqual(result.reason, "standing too low")


class LadderOrderTests(unittest.TestCase):
    def test_a_held_commission_beats_everything_else(self):
        held = {"quest_key": "c_tier1_a", "title": "The Replaced Crate", "objectives_done": 1, "objectives_total": 3}
        result = offer(held_commission=held, cooldown_game_minutes=5000)
        self.assertEqual(result.kind, "progress")
        self.assertEqual(result.held["title"], "The Replaced Crate")

    def test_cooldown_beats_a_pool_match(self):
        result = offer(cooldown_game_minutes=2880, relationship={"trust": 5, "respect": 5, "grudge": 0,
                                                                 "last_commission_outcome": "failed"})
        self.assertEqual(result.kind, "cooldown")
        self.assertEqual(result.cooldown_game_minutes, 2880)
        self.assertIsNone(result.definition)

    def test_a_free_slot_and_a_match_produces_an_offer(self):
        result = offer()
        self.assertEqual(result.kind, "offer")
        self.assertIsNotNone(result.definition)

    def test_nothing_suitable_is_a_refusal_not_an_invention(self):
        result = offer(pool=[])
        self.assertEqual(result.kind, "nothing")
        self.assertIsNone(result.definition)


class CandidateFilterTests(unittest.TestCase):
    def test_the_tier_ceiling_is_what_standing_buys(self):
        # neutral: tier 1 only.
        neutral = offer(relationship={"trust": 0, "respect": 0, "grudge": 0}, realm_index=3)
        self.assertEqual({c["quest_key"] for c in neutral.candidates}, {"c_tier1_b"})
        # trusted at the same realm reaches tier 3 as well.
        trusted = offer(relationship={"trust": 40, "respect": 20, "grudge": 0}, realm_index=3)
        self.assertEqual({c["quest_key"] for c in trusted.candidates}, {"c_tier1_b", "c_tier2", "c_tier3"})

    def test_the_realm_band_filters_by_realm_index(self):
        low = offer(relationship={"trust": 40, "respect": 20, "grudge": 0}, realm_index=0)
        self.assertNotIn("c_tier2", {c["quest_key"] for c in low.candidates})
        self.assertIn("c_tier1_a", {c["quest_key"] for c in low.candidates})

    def test_bands(self):
        self.assertTrue(rules.realm_band_allows("", 9))
        self.assertTrue(rules.realm_band_allows("1-4", 4))
        self.assertFalse(rules.realm_band_allows("1-4", 5))
        self.assertTrue(rules.realm_band_allows("2", 2))
        self.assertFalse(rules.realm_band_allows("2", 3))
        # A malformed band must not hide an approved commission; the tier
        # ceiling is the real gate.
        self.assertTrue(rules.realm_band_allows("qi_refining:1-9", 3))

    def test_a_commission_already_taken_is_never_offered_again(self):
        result = offer(taken_keys={"c_tier1_a", "c_tier1_b", "c_tier2", "c_tier3"})
        self.assertEqual(result.kind, "nothing")


class DeterminismTests(unittest.TestCase):
    def test_the_same_player_on_the_same_day_is_told_about_the_same_one(self):
        first = offer()
        second = offer()
        self.assertEqual(first.definition["quest_key"], second.definition["quest_key"])

    def test_the_pick_moves_with_the_world_day(self):
        picks = {offer(game_minute=day * 1440).definition["quest_key"] for day in range(40)}
        self.assertGreater(len(picks), 1, "the same commission every day is not a rotation")

    def test_two_players_do_not_have_to_share_one_crate(self):
        picks = {offer(user_id=uid).definition["quest_key"] for uid in range(60)}
        self.assertGreater(len(picks), 1)


class TermsTests(unittest.TestCase):
    def test_a_commission_with_no_variants_still_has_one_set_of_terms(self):
        terms = rules.variant_terms({"rewards": {"insight_xp": 5}, "deadline_game_minutes": 4320})
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0]["label"], "standard")
        self.assertEqual(terms[0]["deadline_game_minutes"], 4320)

    def test_variants_are_index_aligned_with_what_the_engine_locks(self):
        terms = rules.variant_terms(POOL[0])
        self.assertEqual([t["label"] for t in terms], ["standard", "rushed"])
        self.assertEqual(terms[1]["deadline_game_minutes"], 1440)

    def test_items_are_named_not_keyed(self):
        text = rules.format_rewards(
            {"spirit_stones": 40, "insight_xp": 8, "items": {"qi_pill": 2}},
            item_names={"qi_pill": "Qi Nourishing Pill"},
        )
        self.assertIn("40 spirit stones", text)
        self.assertIn("2× Qi Nourishing Pill", text)
        self.assertNotIn("qi_pill", text)

    def test_waits_are_read_in_world_days(self):
        self.assertEqual(rules.format_wait_days(4320), "3 world-days")
        self.assertEqual(rules.format_wait_days(1440), "1 world-day")
        self.assertEqual(rules.format_wait_days(90), "1h 30m")
        self.assertEqual(rules.format_wait_days(0), "no time at all")


class VoiceBlockTests(unittest.TestCase):
    """What the model is handed. The block is canon; the model narrates it."""

    def test_an_offer_block_carries_the_terms_and_never_a_number_to_invent(self):
        block = rules.commission_context(offer())
        self.assertEqual(block["kind"], "offer")
        self.assertIn("commission", block)
        self.assertIn("terms_offered", block["commission"])
        # The band, not the number: the Steward cannot read a statistic aloud.
        self.assertEqual(block["standing_band"], "warm")  # trust 5 + respect 5
        self.assertNotIn("trust", block)

    def test_a_refusal_block_has_nothing_to_offer_in_it(self):
        block = rules.commission_context(offer(pool=[]))
        self.assertEqual(block["kind"], "nothing")
        self.assertNotIn("commission", block)

    def test_only_a_failed_commission_lets_the_giver_raise_it_himself(self):
        failed = offer(cooldown_game_minutes=2880,
                       relationship={"trust": 0, "respect": 0, "grudge": 0, "last_commission_outcome": "failed"})
        abandoned = offer(cooldown_game_minutes=2880,
                          relationship={"trust": 0, "respect": 0, "grudge": 0, "last_commission_outcome": "abandoned"})
        self.assertTrue(failed.steward_initiates)
        self.assertFalse(abandoned.steward_initiates)
        self.assertTrue(rules.commission_context(failed)["steward_initiates"])
        self.assertFalse(rules.commission_context(abandoned)["steward_initiates"])

    def test_a_progress_block_reports_the_held_one_and_offers_nothing(self):
        held = {"title": "The Replaced Crate", "objectives_done": 1, "objectives_total": 3,
                "deadline_game_minutes_remaining": 1440}
        block = rules.commission_context(offer(held_commission=held))
        self.assertEqual(block["kind"], "progress")
        self.assertEqual(block["held"]["objectives_done"], 1)
        self.assertEqual(block["held"]["deadline_in"], "1 world-day")
        self.assertNotIn("commission", block)


if __name__ == "__main__":
    unittest.main()


class SectWorkTests(unittest.TestCase):
    """Sect commissions are sect business, and the refusal says which."""

    def test_an_outsider_is_offered_nothing_from_a_sect_board(self):
        result = offer(pool=SECT_BOARD, player_sect="")
        self.assertEqual(result.kind, "nothing")
        self.assertEqual(result.reason, "sect members only")

    def test_a_rival_disciple_is_still_an_outsider(self):
        result = offer(pool=SECT_BOARD, player_sect="Blood River Sect")
        self.assertEqual(result.kind, "nothing")
        self.assertEqual(result.reason, "sect members only")

    def test_a_disciple_of_the_right_sect_is_offered_work(self):
        result = offer(pool=SECT_BOARD, player_sect="Azure Cloud Sect")
        self.assertEqual(result.kind, "offer")
        self.assertEqual(result.definition["requires_sect"], "Azure Cloud Sect")

    def test_membership_is_matched_case_insensitively_not_loosely(self):
        self.assertTrue(rules.sect_allows("Azure Cloud Sect", "azure cloud sect"))
        self.assertFalse(rules.sect_allows("Azure Cloud Sect", "Azure Cloud"))
        self.assertTrue(rules.sect_allows("", "anything at all"))
        self.assertFalse(rules.sect_allows("Azure Cloud Sect", ""))

    def test_the_tier_ceiling_still_applies_inside_a_sect(self):
        # Being a disciple is not standing. A new member sees the tier-1 task.
        result = offer(pool=SECT_BOARD, player_sect="Azure Cloud Sect",
                       relationship={"trust": 0, "respect": 0, "grudge": 0})
        self.assertEqual([c["quest_key"] for c in result.candidates], ["s_azure_1"])

    def test_a_sect_board_with_nothing_on_it_does_not_claim_membership_is_the_problem(self):
        result = offer(pool=[], player_sect="")
        self.assertEqual(result.reason, "nothing suitable in the pool")


class UndisclosedTermsTests(unittest.TestCase):
    """A giver who will not say what the work pays. The card says that; it does
    not invent a number and it does not print the real one."""

    def test_the_flag_reads_off_the_definition(self):
        self.assertTrue(rules.rewards_are_hidden(UNDISCLOSED_POOL[0]))
        self.assertFalse(rules.rewards_are_hidden(POOL[0]))
        self.assertFalse(rules.rewards_are_hidden({"reward_visibility": "nonsense"}))
        self.assertFalse(rules.rewards_are_hidden(None))

    def test_formatting_hidden_terms_discards_the_real_ones(self):
        text = rules.format_rewards({"spirit_stones": 150, "insight_xp": 40}, hidden=True)
        self.assertEqual(text, rules.UNDISCLOSED)
        self.assertNotIn("150", text)
        self.assertNotIn("40", text)

    def test_the_voice_block_carries_no_figure_and_no_invitation_to_invent_one(self):
        block = rules.commission_context(offer(pool=UNDISCLOSED_POOL))
        commission = block["commission"]
        self.assertTrue(commission["rewards_hidden"])
        self.assertEqual(commission["terms_offered"], rules.UNDISCLOSED)
        # The authored boast rides along; nothing numeric does.
        self.assertEqual(commission["boast"], "Bring the bowl back empty.")
        blob = repr(block)
        for leaked in ("150", "40", "spirit_stones"):
            self.assertNotIn(leaked, blob, "a hidden reward reached the prompt")

    def test_a_shown_commission_carries_no_boast_field(self):
        block = rules.commission_context(offer())
        self.assertNotIn("boast", block["commission"])
        self.assertFalse(block["commission"]["rewards_hidden"])

    def test_the_deadline_is_never_hidden(self):
        # Hiding the pay is a gamble the player takes knowingly; hiding the
        # clock would just be a trap.
        block = rules.commission_context(offer(pool=UNDISCLOSED_POOL))
        self.assertEqual(block["commission"]["deadline"], "4 world-days")
