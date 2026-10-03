"""The body path is sped up three ways (v1.20.0).

Asked for by the owner: *"We need something to speed up the body cultivation."* The
body ladder was the slower one for three reasons: its stages cost more than the
qi ladder's, nothing anybody could buy sped it up (the one body pill was a
single sect's members-only stock), and every multiplier written for qi skips
it. On the owner's call:

- **Pills.** A Bone-Tempering Pill and an Iron-Blood Pill, the body's twins of
  the Qi Nourishing Pill and the Spirit Condensation Pill, on every shelf that
  carries the twin, at the twin's price and strength.
- **Tempered by use.** A successful hunt, a successful dig and a battle won add
  a share of a body session (`temperBodyByUseTx`, held in Go); the reply says so.
- **Tempering grounds.** Hunting grounds, forge terraces, garrison wards and the
  wilds multiply a body session (`bodyTemperingGround`, held in Go).

Held here: the content - the pills, the shares and the grounds - and that each
reply prints what the engine tempered rather than restating the share.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
TWINS = {"qi_pill": "bone_tempering_pill", "spirit_condensation_pill": "iron_blood_pill"}


def _modifier(item_id: str) -> dict:
    (mod,) = CONTENT["items"][item_id]["use"]["effect"]["modifiers"]
    return mod


class TheBodyHasPillsOnTheShelves(unittest.TestCase):
    def test_each_body_pill_matches_its_qi_twin(self):
        for qi, body in TWINS.items():
            qmod, bmod = _modifier(qi), _modifier(body)
            self.assertEqual(qmod["stat"], "cultivation_gain")
            self.assertEqual(bmod["stat"], "body_cultivation_gain", f"{body} does not speed the body")
            self.assertEqual(bmod["value"], qmod["value"], f"{body} is not as strong as {qi}")
            for key in ("base_price", "sect_value"):
                self.assertEqual(CONTENT["items"][body][key], CONTENT["items"][qi][key], f"{body}.{key}")
            self.assertFalse(CONTENT["items"][body].get("market_excluded"), f"{body} is kept off the market")

    def test_every_shelf_with_the_qi_pill_carries_the_body_pill(self):
        found = 0
        for key, shop in CONTENT["shops"].items():
            sells = {line["item_id"]: line for line in shop["sells"]}
            for qi, body in TWINS.items():
                if qi not in sells:
                    continue
                found += 1
                self.assertIn(body, sells, f"{key} sells {qi} and not {body}")
                self.assertEqual(sells[body]["price"], sells[qi]["price"], f"{key}: {body} is priced apart from {qi}")
                self.assertEqual(shop["buys"].get(body), shop["buys"].get(qi), f"{key} buys {body} back apart from {qi}")
        self.assertGreater(found, 30, "no shelf carries the qi pills; the test is vacuous")

    def test_the_sects_own_body_draught_stays_its_own(self):
        self.assertTrue(CONTENT["items"]["blood_river_essence"].get("market_excluded"))


class TheBodyIsTemperedByUseAndByGround(unittest.TestCase):
    def setUp(self):
        self.block = CONTENT["body_tempering"]

    def test_each_deed_has_a_share_of_a_session(self):
        for deed in ("hunt", "mine", "battle"):
            share = self.block["by_use"].get(deed, 0)
            self.assertTrue(0 < share < 1, f"{deed} tempers {share} of a session; a deed is less than sitting down to train")

    def test_every_ground_names_a_kind_the_world_carries(self):
        grounds = self.block["grounds"]
        sites = {loc.get("road_site") for loc in CONTENT["locations"].values()}
        districts = {loc.get("district") for loc in CONTENT["locations"].values()}
        for kind, mult in grounds["road_sites"].items():
            self.assertIn(kind, sites, f"no place is a {kind}")
            self.assertGreater(mult, 1)
        for kind, mult in grounds["districts"].items():
            self.assertIn(kind, districts, f"no place is a {kind} district")
            self.assertGreater(mult, 1)
        self.assertGreater(grounds["wilds"], 1)
        self.assertTrue(any(loc.get("wilds_of") for loc in CONTENT["locations"].values()), "no place lies in the wilds")
        # A shrine and a temple are the qi path's ground already; a tempering
        # ground there would price one place twice.
        self.assertNotIn("shrine", grounds["road_sites"])
        self.assertNotIn("temple", grounds["districts"])


class EachReplySaysWhatTheEngineTempered(unittest.TestCase):
    def test_the_line(self):
        from app.rules.body_tempering import tempered_line

        self.assertEqual(tempered_line({}, "hunt"), "")
        self.assertEqual(tempered_line({"body_tempered": 0}, "hunt"), "")
        self.assertIn("+3", tempered_line({"body_tempered": 3}, "hunt"))

    def test_hunt_dig_and_fight_each_print_it(self):
        want = {
            ("app/bot/commands/exploration.py", "hunt"): "hunt",
            ("app/bot/commands/exploration.py", "mine"): "dig",
            ("app/bot/commands/battle.py", "_finish_battle"): "fight",
        }
        found = {}
        for (path, fn_name) in want:
            tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                    for call in ast.walk(node):
                        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "tempered_line":
                            found[(path, fn_name)] = call.args[1].value
        self.assertEqual(found, want, "a deed the engine tempers the body for does not say so")


if __name__ == "__main__":
    unittest.main()
