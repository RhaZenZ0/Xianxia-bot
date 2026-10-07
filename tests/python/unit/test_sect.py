"""Sect address, manor, recruitment, trial tuning and exchange tests.

Merged from:

test_sect_recruitment.py — (no docstring)

test_the_trial_reads_its_tuning.py — The sect trial's tuning is read by the engine, and the notes are its twin (v1.2.3).

`trial_modifier` in app/rules/sect_recruitment.py has printed a sect's `base_tn`,
`path_bonuses`, `root_affinities`, `family_archetype_bonus` and
`karma_preference` in the trial notes since the block was written, and the
engine read none of them. Python cannot call Go, so what is held is that the
Go trial consumes every key the notes print, and that the two defaults agree.

test_the_sect_exchange.py — What a sect issues its members, and how they climb (v1.8.0).

`sect_system.exchange` is content the engine acts on: the stock every sect
issues, each sect's own item, the price multiple, the earning ratios and the
promotion ladder. The engine refuses an id it does not know and a rank it
cannot name only at the moment somebody presses Redeem, so a typo here is a
button that never works rather than an error anywhere - the class `/learn` and
the peach were. This holds the shape; the rules are Go's, and their tests are
in `go_core/internal/game/sect_exchange_test.go`.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT, code_only

from app.rules.game import World
from app.rules.sect import resolve_address
from app.rules.sect_recruitment import recruitment_definition, trial_modifier

def person(uid, name, style="neutral", realm=0, phase=1, accepted=None):
    d = {
        "user_id": uid,
        "name": name,
        "address_style": style,
        "realm_index": realm,
        "phase": phase,
    }
    if accepted is not None:
        d["lineage_accepted_at"] = accepted
    return d


class SectAddressTests(unittest.TestCase):
    def setUp(self):
        self.observer = person(1, "Jian", "masculine", accepted=20)
        self.master = person(2, "Elder Yun", "masculine", accepted=10)
        self.grandmaster = person(3, "Patriarch Shen", "masculine")
        self.senior_brother = person(4, "Wei", "masculine")
        self.senior_brother["accepted_at"] = 15
        self.junior_sister = person(5, "Lan", "feminine")
        self.junior_sister["accepted_at"] = 25
        self.base = dict(
            observer_membership={"sect_name": "Azure Cloud Sect", "rank_level": 1},
            target_membership={"sect_name": "Azure Cloud Sect", "rank_level": 1},
            observer_master=self.master,
            target_master=None,
            observer_grandmaster=self.grandmaster,
            observer_master_master=self.grandmaster,
            sibling_rows=[self.senior_brother, self.junior_sister],
            master_sibling_rows=[],
        )

    def test_master_english_default(self):
        r = resolve_address(self.observer, self.master, **self.base)
        self.assertEqual(r.translation, "Master")
        self.assertNotIn("Shifu", r.display)
        self.assertIn("Master", r.display)
        self.assertIn("Shifu", r.display_chinese)

    def test_senior_brother(self):
        r = resolve_address(self.observer, self.senior_brother, **self.base)
        self.assertEqual(r.translation, "Senior Brother")

    def test_junior_sister(self):
        r = resolve_address(self.observer, self.junior_sister, **self.base)
        self.assertEqual(r.translation, "Junior Sister")


class SectManorMathTests(unittest.TestCase):
    """Facility benefit scaling (merged from integration/test_sect_manor.py in
    v0.20.3; that file seeded three members and a treasury no test read,
    and its `sect_manors`-table check is test_startup_health's)."""

# --- from test_sect_recruitment.py ---


ROOT = PROJECT_ROOT


ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 5}


class SectRecruitmentTests(unittest.TestCase):
    """Recruitment content and the pure trial/recommendation modifiers.
    (Moved from integration/ in v0.20.3: the seeded database no test read
    is gone; ATTRS stays because the modifier test reads it.)"""

    @classmethod
    def setUpClass(cls):
        cls.world = World(ROOT / "content" / "world.json")

    def test_all_public_sects_have_story_recruitment_configuration(self):
        configured = {name: recruitment_definition(self.world.sects, name) for name in self.world.sects}
        configured = {name: rec for name, rec in configured.items() if rec is not None}
        self.assertGreaterEqual(len(configured), 6)
        for name, rec in configured.items():
            self.assertIn(rec["location"], self.world.locations)
            self.assertTrue(rec.get("trial_name"))
            self.assertTrue(rec.get("examiner"))
            self.assertTrue(rec.get("description"))

    def test_recommender_npcs_exist_for_every_sect(self):
        recommended = {str(npc.get("sect_affiliation")) for npc in self.world.npcs.values() if npc.get("can_recommend")}
        configured = {name for name in self.world.sects if recruitment_definition(self.world.sects, name)}
        self.assertTrue(configured.issubset(recommended))

    def test_trial_modifiers_reward_matching_path_family_and_recommendation(self):
        char = {
            "attributes": ATTRS, "path": "Qi Refiner", "spiritual_root": "Fire", "karma_score": 20,
        }
        rec = recruitment_definition(self.world.sects, "Crimson Furnace Sect")
        mod, notes, rejection = trial_modifier(
            char, rec, attribute="insight", family={"archetype": "alchemy_family"}, recommendation_bonus=2
        )
        self.assertIsNone(rejection)
        self.assertGreater(mod, ATTRS["insight"])
        text = " ".join(notes)
        self.assertIn("family tradition", text)
        self.assertIn("NPC recommendation", text)

    def test_source_contains_story_and_npc_recommendation_gui_paths(self):
        # /sect -> Recruitment moved to app/bot/commands/sect.py in split stage 3
        # (v0.19.30). A check anchored to one file would quietly stop covering
        # some of these the moment the code moved - the exact mistake stage 2's
        # notes warn about - so this checks the whole app/bot package rather
        # than guessing which file each string still lives in.
        source = "".join(
            path.read_text(encoding="utf-8") for path in (ROOT / "app" / "bot").rglob("*.py")
        )
        self.assertIn('name="recruitment"', source)
        self.assertIn('name="recommendation"', source)
        self.assertIn('name="trial"', source)
        self.assertIn("sect_recommender_autocomplete", source)
        self.assertIn("sect.recruitment.trial", source)
        self.assertIn("**/sect → Recruitment → Recommendation**", source)

# --- from test_the_trial_reads_its_tuning.py ---


ROOT_tuning = Path(__file__).resolve().parents[3]


GO = ROOT_tuning / "go_core" / "internal" / "game" / "sect_actions.go"


PY = ROOT_tuning / "app" / "rules" / "sect_recruitment.py"


class TheTrialReadsItsTuning(unittest.TestCase):
    def test_every_key_the_notes_print_is_read_by_the_engine(self):
        go = GO.read_text(encoding="utf-8")
        body = go[go.index("func sectTrialTuningTx("):go.index("func sectTrialActionGo(")]
        body = re.sub(r"//[^\n]*", "", body)
        for field in ("rec.BaseTN", "rec.PathBonuses[", "rec.RootAffinities", "rec.FamilyArchetypeBonus[", "rec.KarmaPreference"):
            self.assertIn(field, body, f"the engine's trial tuning no longer reads {field}; the notes print a number the roll ignores")
        self.assertIn("tuning.Bonus", go, "the tuning is computed and not added to the rolls")

    def test_the_two_defaults_agree(self):
        go = GO.read_text(encoding="utf-8")
        m = re.search(r"sectTrialDefaultTN = int64\((\d+)\)", go)
        self.assertIsNotNone(m, "the Go default could not be read; the reader is broken, not the tree")
        py = code_only(PY.read_text(encoding="utf-8"))
        p = re.search(r'rec\.get\("base_tn", (\d+)\)', py)
        self.assertIsNotNone(p, "the Python default could not be read; the reader is broken, not the tree")
        self.assertEqual(m.group(1), p.group(1), "the profile's default TN and the engine's disagree")

# --- from test_the_sect_exchange.py ---


PROJECT_ROOT_exchange = Path(__file__).resolve().parents[3]


WORLD = json.loads((PROJECT_ROOT_exchange / "content" / "world.json").read_text(encoding="utf-8"))


EXCHANGE = WORLD["sect_system"].get("exchange") or {}


RANKS = {int(r["level"]): r["name"] for r in WORLD["sect_system"]["ranks"]}


class TheSectExchangeIsWellFormed(unittest.TestCase):
    def test_the_reader_found_an_exchange(self):
        self.assertTrue(EXCHANGE.get("stock"), "content carries no sect exchange stock; the gate is broken, not the tree")
        self.assertGreater(int(EXCHANGE.get("points_per_sect_value") or 0), 1,
                           "an issued item must cost more points than donating it earns")

    def test_every_lot_names_a_real_item_and_a_real_rank(self):
        lots = list(EXCHANGE["stock"]) + [lot for own in EXCHANGE.get("sect_stock", {}).values() for lot in own]
        for lot in lots:
            self.assertIn(lot["item_id"], WORLD["items"], f"{lot['item_id']} is issued and does not exist")
            self.assertIn(int(lot["min_rank_level"]), RANKS, f"{lot['item_id']} waits for rank {lot['min_rank_level']}, which is no rung")
            self.assertGreater(int(WORLD["items"][lot["item_id"]].get("sect_value") or 0), 0,
                               f"{lot['item_id']} has no sect value to be priced from")

    def test_each_sects_own_item_is_its_own_alone(self):
        seen: dict[str, str] = {}
        for sect, lots in EXCHANGE.get("sect_stock", {}).items():
            self.assertIn(sect, WORLD["sects"], f"{sect} issues stock and is not a sect")
            self.assertTrue(WORLD["sects"][sect].get("recruitment"), f"{sect} is the hidden sect; its members are not on the exchange")
            for lot in lots:
                item = WORLD["items"][lot["item_id"]]
                self.assertTrue(item.get("market_excluded"), f"{lot['item_id']} is {sect}'s own and could be bought on a market")
                self.assertNotIn(lot["item_id"], seen, f"{lot['item_id']} is issued by both {seen.get(lot['item_id'])} and {sect}")
                seen[lot["item_id"]] = sect
        common = {lot["item_id"] for lot in EXCHANGE["stock"]}
        self.assertFalse(common & set(seen), "a sect's own item is also in the common stock")
        recruiting = {name for name, sect in WORLD["sects"].items() if sect.get("recruitment")}
        self.assertEqual(recruiting, set(EXCHANGE.get("sect_stock", {})), "every recruiting sect keeps one item of its own")

    def test_the_promotion_ladder_climbs(self):
        ladder = EXCHANGE.get("promotion") or []
        self.assertTrue(ladder, "no promotion ladder; a member's rank would never move")
        last_rank, last_earned = 10, 0  # a member joins as Outer Disciple with nothing earned
        for rung in ladder:
            self.assertIn(int(rung["rank_level"]), RANKS)
            self.assertGreater(int(rung["rank_level"]), last_rank)
            self.assertGreater(int(rung["earned"]), last_earned)
            last_rank, last_earned = int(rung["rank_level"]), int(rung["earned"])
        self.assertLess(last_rank, max(RANKS), "the ladder reaches the top; the sect's own offices are not earned by donating")

    def test_every_rank_that_locks_stock_can_be_earned(self):
        reachable = {10} | {int(r["rank_level"]) for r in EXCHANGE.get("promotion") or []}
        lots = list(EXCHANGE["stock"]) + [lot for own in EXCHANGE.get("sect_stock", {}).values() for lot in own]
        for lot in lots:
            self.assertIn(int(lot["min_rank_level"]), reachable,
                          f"{lot['item_id']} waits for {RANKS[int(lot['min_rank_level'])]}, which no player can earn")


class TheBotRestatesNoPrice(unittest.TestCase):
    """The treasury page and the redeem picker print the engine's numbers;
    the old page carried its own copy of the scarcity multiplier."""

    def test_no_multiplier_is_restated(self):
        tree = ast.parse((PROJECT_ROOT_exchange / "app" / "bot" / "commands" / "sect.py").read_text(encoding="utf-8"))
        floats = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, float)}
        self.assertFalse(floats & {1.60, 1.35, 1.20}, "sect.py restates the engine's redemption multiplier")
        called = {node.args[0].value for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "action"
                  and node.args and isinstance(node.args[0], ast.Constant)}
        self.assertIn("sect.exchange", called, "the treasury page does not ask the engine for the exchange")


if __name__ == "__main__":
    unittest.main()
