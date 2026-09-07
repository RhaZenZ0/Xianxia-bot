"""The commission surface as wired, read in source (v0.22.0).

The design's gate, minus the parts Go owns (those are in
go_core/internal/game/commission_actions_test.go) and the ladder (that is
tests/python/unit/test_commissions.py). What is left is the wiring, and the
wiring is where this feature could quietly go wrong in ways no unit test would
notice:

- accepting a quest is an engine action and there is no Python write left;
- the Accept button's payload comes from the engine's definition, never from
  anything the narrator wrote;
- a personal (invented) commission is never listed for another player;
- abandoning goes through a confirmation that states its cost;
- the giver NPCs the content names actually exist, at the locations it claims,
  with the fields the voice call reads.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
CORE_SERVICES = (APP / "ops" / "core_services.py").read_text(encoding="utf-8")
DB_CORE = (APP / "database" / "core.py").read_text(encoding="utf-8")
UI = (APP / "bot" / "ui" / "commissions.py").read_text(encoding="utf-8")
SCENE = (APP / "bot" / "commands" / "scene.py").read_text(encoding="utf-8")
CHARACTER = (APP / "bot" / "commands" / "character.py").read_text(encoding="utf-8")
NARRATOR = (APP / "ai" / "narrator.py").read_text(encoding="utf-8")


class AuthorityTests(unittest.TestCase):
    def test_python_no_longer_writes_the_accepted_quest_row(self):
        self.assertNotIn("INSERT INTO character_quests", DB_CORE,
                         "accepting is commission.accept in the engine; Python must not insert the row")
        self.assertNotIn("async def accept_quest", DB_CORE)

    def test_accept_goes_through_the_engine_action(self):
        self.assertIn('"commission.accept"', CORE_SERVICES)
        self.assertIn("authoritative_action", CORE_SERVICES)

    def test_resolving_is_the_engines_too_and_carries_the_outcome(self):
        self.assertIn('"commission.resolve"', CORE_SERVICES)
        self.assertIn("admin_retire", CORE_SERVICES)


class OfferViewTests(unittest.TestCase):
    """The offer view is built from the definition, not from the reply."""

    def setUp(self):
        self.tree = ast.parse(UI)
        self.classes = {n.name: n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)}

    def test_the_view_exists_and_is_built_from_the_engines_definition(self):
        self.assertIn("CommissionOfferView", self.classes)
        source = ast.get_source_segment(UI, self.classes["CommissionOfferView"]) or ""
        self.assertIn("offer.definition", source)
        self.assertIn("variant_terms", source)

    def test_the_accept_button_sends_an_index_not_a_reward(self):
        source = ast.get_source_segment(UI, self.classes["CommissionAcceptButton"]) or ""
        self.assertIn("variant_index=self.variant_index", source)
        for leaked in ("spirit_stones", "insight_xp", "rewards="):
            self.assertNotIn(leaked, source,
                             "the accept payload must name terms by index; the engine owns what they pay")

    def test_the_surface_cannot_reach_the_narrator_at_all(self):
        # The strongest form of "the buttons do not come from the reply": this
        # module has no way to see a reply. It imports nothing from app.ai.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("ai", (node.module or "").split("."),
                                 "the commission surface must not import the narrator")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("ai", alias.name.split("."))

    def test_every_argument_to_an_engine_call_is_state_this_module_owns(self):
        """Each value handed to COMMISSIONS.accept / .resolve is a constant, a
        `self.*` attribute set from the engine's definition, or an action id
        built from the interaction - never a variable that could hold text."""
        allowed = {"str", "int", "f-string of interaction.id", "self attribute", "constant"}
        self.assertTrue(allowed)  # documents the rule the loop below enforces
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in ("accept", "resolve"):
                continue
            base = node.func.value
            if not (isinstance(base, ast.Name) and base.id == "COMMISSIONS"):
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                with self.subTest(arg=ast.dump(arg)[:60]):
                    self.assertTrue(
                        isinstance(arg, (ast.Constant, ast.JoinedStr))
                        or (isinstance(arg, ast.Attribute) and isinstance(arg.value, (ast.Name, ast.Attribute)))
                        or (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id in ("int", "str")),
                        "an engine argument that is not owned state could carry narration",
                    )

    def test_the_reply_attaches_buttons_only_to_the_canonical_card(self):
        self.assertIn("commission_reply_extras", SCENE)
        self.assertIn("view=commission_view", SCENE)
        # The card is its own followup message, so the buttons are never
        # attached to the paragraph the narrator produced.
        self.assertIn("interaction.followup.send(commission_card", SCENE)


class VoiceCallTests(unittest.TestCase):
    def test_the_narrator_takes_a_commission_block_and_is_told_it_is_canon(self):
        self.assertIn("commission_context", NARRATOR)
        self.assertIn("format_commission_block", NARRATOR)
        self.assertIn("narrate it, never change it", NARRATOR)

    def test_the_block_is_passed_from_the_ladder_not_from_the_player(self):
        self.assertIn("commission_context=commission_block", SCENE)
        self.assertIn("commission_rules.commission_context(", SCENE)


class VisibilityTests(unittest.TestCase):
    def test_the_pool_query_never_returns_another_players_commission(self):
        self.assertIn("owner_user_id IS NULL OR owner_user_id=?", DB_CORE)

    def test_the_journal_and_catalog_filter_by_owner(self):
        self.assertIn("def visible_to", CORE_SERVICES)
        self.assertIn("visible_catalog", CORE_SERVICES)
        self.assertIn("QUESTS.visible_catalog(", CHARACTER)

    def test_commissions_are_not_offered_from_the_quest_dropdown(self):
        # A commission is something a giver offers you in person.
        self.assertIn('str(definition.get("giver_npc") or "")', CORE_SERVICES)


class AbandonTests(unittest.TestCase):
    def test_abandoning_states_its_cost_before_it_can_be_confirmed(self):
        self.assertIn("def abandon_warning", UI)
        self.assertIn("as if you had failed it", UI)
        self.assertIn("AbandonCommissionView", CHARACTER)

    def test_the_danger_button_is_a_danger_button(self):
        self.assertIn("discord.ButtonStyle.danger", UI)


class GiverContentTests(unittest.TestCase):
    """Content prerequisites from the design: a giver has to be a real NPC,
    where the table says he is, with the fields the voice call reads."""

    NARRATOR_FIELDS = ("personality", "speech", "want", "fear", "secret")

    def setUp(self):
        self.givers = WORLD["commission_givers"]
        self.commissions = WORLD["commissions"]

    def test_there_are_givers_and_they_are_real_npcs_where_the_table_says(self):
        self.assertGreaterEqual(len(self.givers), 10)
        for name, entry in self.givers.items():
            with self.subTest(giver=name):
                npc = WORLD["npcs"].get(name)
                self.assertIsNotNone(npc, f"{name} is not an NPC")
                self.assertEqual(npc.get("location"), entry.get("location"))
                for field in self.NARRATOR_FIELDS:
                    self.assertTrue(str(npc.get(field) or "").strip(), f"{name} lacks {field}")

    def test_every_commission_belongs_to_a_giver_and_has_terms(self):
        self.assertGreaterEqual(len(self.commissions), 6)
        keys = set()
        for entry in self.commissions:
            with self.subTest(commission=entry.get("quest_key")):
                self.assertIn(entry["giver_npc"], self.givers)
                self.assertNotIn(entry["quest_key"], keys, "duplicate quest_key")
                keys.add(entry["quest_key"])
                self.assertGreaterEqual(len(entry["objectives"]), 2)
                variants = entry["variants"]
                self.assertGreaterEqual(len(variants), 1)
                if entry["reward_visibility"] == "hidden":
                    # There is nothing to negotiate with someone who will not
                    # name the figure in the first place.
                    self.assertEqual(len(variants), 1, "undisclosed work offers one set of terms")
                else:
                    self.assertEqual(variants[0]["label"], "standard")
                self.assertEqual(entry["rewards"], variants[0]["rewards"],
                                 "rewards_json must be the standard variant, so an un-negotiated accept pays it")
                self.assertEqual(entry["deadline_game_minutes"], variants[0]["deadline_game_minutes"])
                for terms in variants:
                    self.assertGreater(int(terms["deadline_game_minutes"]), 0, "a commission needs a clock")

    def test_a_rushed_variant_is_shorter_and_pays_less(self):
        """The negotiation has to be a real trade, or it is just a bigger number."""
        checked = 0
        for entry in self.commissions:
            variants = {t["label"]: t for t in entry["variants"]}
            standard = variants.get("standard")
            rushed = variants.get("rushed")
            if not rushed:
                continue
            checked += 1
            with self.subTest(commission=entry["quest_key"]):
                self.assertLess(rushed["deadline_game_minutes"], standard["deadline_game_minutes"])
                self.assertLess(int(rushed["rewards"].get("spirit_stones", 0)),
                                int(standard["rewards"].get("spirit_stones", 0)))
        self.assertGreaterEqual(checked, 3)

    def test_harder_terms_pay_more_for_the_same_clock(self):
        for entry in self.commissions:
            variants = {t["label"]: t for t in entry["variants"]}
            harder = variants.get("harder terms")
            standard = variants.get("standard")
            if not harder:
                continue
            with self.subTest(commission=entry["quest_key"]):
                self.assertEqual(harder["deadline_game_minutes"], standard["deadline_game_minutes"])
                self.assertGreater(int(harder["rewards"].get("spirit_stones", 0)),
                                   int(standard["rewards"].get("spirit_stones", 0)))

    def test_the_pool_is_seeded_insert_only_so_gm_decisions_survive_a_restart(self):
        self.assertIn("async def sync_commission_pool", DB_CORE)
        self.assertIn("SELECT 1 FROM quest_definitions WHERE quest_key=?", DB_CORE)


if __name__ == "__main__":
    unittest.main()


class HiddenMasterGiverTests(unittest.TestCase):
    """v0.22.1: a hidden master may hand out work — that is most of the point of
    the two old men in Greenriver. What they may not be is a quest target."""

    def setUp(self):
        self.givers = WORLD["commission_givers"]
        self.commissions = WORLD["commissions"]

    def test_both_old_men_are_givers_and_neither_states_his_terms(self):
        for name in ("Old Beggar Chen", "Old Gou"):
            with self.subTest(giver=name):
                self.assertIn(name, self.givers)
                self.assertIsInstance(WORLD["npcs"][name].get("hidden_master"), dict)
                theirs = [c for c in self.commissions if c["giver_npc"] == name]
                self.assertGreaterEqual(len(theirs), 2)
                for entry in theirs:
                    self.assertEqual(entry["reward_visibility"], "hidden")
                    self.assertTrue(entry["boast"].strip(), "an undisclosed offer needs something said about it")

    def test_a_hidden_master_is_never_his_own_quest_target(self):
        """`validate_quest_definition` refuses a hidden master as a target, and
        pointing a player back at one would leak that he matters."""
        hidden = {n for n, npc in WORLD["npcs"].items() if isinstance(npc.get("hidden_master"), dict)}
        for entry in self.commissions:
            for objective in entry["objectives"]:
                with self.subTest(commission=entry["quest_key"], objective=objective.get("id")):
                    self.assertNotIn(str(objective.get("target") or ""), hidden)

    def test_the_two_old_men_are_a_gamble_in_both_directions(self):
        """The mechanic only works if undisclosed can mean either thing. If
        every hidden reward were bad it would be a trap; if every one were good
        it would be a free lunch."""
        payouts = {}
        for name in ("Old Beggar Chen", "Old Gou"):
            payouts[name] = [int(c["variants"][0]["rewards"].get("spirit_stones", 0))
                             for c in self.commissions if c["giver_npc"] == name]
        self.assertGreater(max(payouts["Old Beggar Chen"]), 100, "the real master should be worth working for")
        self.assertLess(min(payouts["Old Gou"]), 15, "the fraud should sometimes be a fraud")
        self.assertGreater(max(payouts["Old Gou"]), 40, "and not always, or he is just a wall")


class SectBoardContentTests(unittest.TestCase):
    """Every public sect has a board, and its work is not in the quest journal."""

    def setUp(self):
        self.givers = WORLD["commission_givers"]
        self.commissions = WORLD["commissions"]
        self.public_sects = {n for n, d in WORLD["sects"].items() if not d.get("hidden")}

    def test_every_public_sect_has_a_giver_who_belongs_to_it(self):
        by_sect = {str(g.get("sect")): name for name, g in self.givers.items() if g.get("sect")}
        self.assertEqual(set(by_sect), self.public_sects)
        for sect, giver in by_sect.items():
            with self.subTest(sect=sect):
                self.assertEqual(WORLD["npcs"][giver].get("sect_affiliation"), sect)
                self.assertEqual(WORLD["npcs"][giver].get("location"), self.givers[giver]["location"])

    def test_every_sect_has_at_least_two_standing_tasks_gated_to_it(self):
        for sect in self.public_sects:
            work = [c for c in self.commissions if c.get("requires_sect") == sect]
            with self.subTest(sect=sect):
                self.assertGreaterEqual(len(work), 2)
                for entry in work:
                    self.assertEqual(self.givers[entry["giver_npc"]].get("sect"), sect)
                    self.assertEqual(entry["reward_visibility"], "shown",
                                     "sect work is stated work; the sect is not a mystery to its own disciples")

    def test_the_hidden_sect_has_no_public_board(self):
        hidden = {n for n, d in WORLD["sects"].items() if d.get("hidden")}
        for entry in self.commissions:
            self.assertNotIn(entry.get("requires_sect"), hidden)


class UndisclosedSurfaceTests(unittest.TestCase):
    """The card says the terms are undisclosed. It does not print them, and it
    does not pretend there are none."""

    def test_the_offer_card_branches_on_the_definition_not_on_the_giver(self):
        self.assertIn("rules.rewards_are_hidden(definition)", UI)
        self.assertIn("rules.UNDISCLOSED", UI)

    def test_the_accepted_card_says_terms_are_fixed_without_saying_what_they_are(self):
        self.assertIn("fixed now, told to you when the work is done", UI)

    def test_the_player_is_told_it_is_a_gamble_before_they_accept(self):
        self.assertIn("It may be worth far more", UI)

    def test_the_narrator_is_forbidden_from_pricing_undisclosed_work(self):
        self.assertIn("HE HAS NOT SAID WHAT IT PAYS", NARRATOR)
        self.assertIn("Name no number", NARRATOR)

    def test_the_engine_checks_membership_and_not_only_the_ladder(self):
        engine = (PROJECT_ROOT / "go_core" / "internal" / "game" / "commission_actions.go").read_text(encoding="utf-8")
        self.assertIn("def.RequiresSect", engine)
        self.assertIn("sectMembershipRow", engine)
