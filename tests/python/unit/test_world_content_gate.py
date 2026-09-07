"""The content the narrator and /explore read is complete (v0.21.5).

The 1.0 roadmap's v0.25 gate, landed early: every location has sense
hints, every location /explore can be used in has encounters (the engine
hard-errors on an empty list - `exploration_actions.go`: "current location
has no exploration encounters"), and every NPC the narrator is handed has
the fields it reads - personality, speech, want, fear, secret. Before this
release six locations hard-errored, seven had no hints, and the four world
rulers shared one copy-pasted speech/want/fear.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
NARRATOR_NPC_FIELDS = ("personality", "speech", "want", "fear", "secret")


class LocationContentTests(unittest.TestCase):
    def test_every_location_has_sense_hints(self):
        for name, loc in WORLD["locations"].items():
            with self.subTest(location=name):
                hints = loc.get("sense_hints") or []
                self.assertGreaterEqual(len(hints), 1, f"{name} has no sense_hints")
                for hint in hints:
                    self.assertGreater(len(hint.strip()), 20)

    def test_every_location_has_encounters(self):
        for name, loc in WORLD["locations"].items():
            with self.subTest(location=name):
                encounters = loc.get("encounters") or []
                self.assertGreaterEqual(len(encounters), 3, f"/explore hard-errors at {name}: no encounters")
                self.assertEqual(len(set(encounters)), len(encounters), "duplicate encounter")
                for text in encounters:
                    self.assertGreater(len(text.strip()), 20)

    def test_safe_zone_encounters_do_not_start_violence(self):
        # A protected interior forbids violence (system prompt rule 15); its
        # encounters must be things that happen around you, not attacks on you.
        attack_words = ("attacks you", "ambushes you", "lunges at you", "strikes you")
        for name, loc in WORLD["locations"].items():
            if not loc.get("safe_zone"):
                continue
            for text in loc.get("encounters") or []:
                with self.subTest(location=name, encounter=text[:40]):
                    self.assertFalse(any(w in text.lower() for w in attack_words), text)


class NpcContentTests(unittest.TestCase):
    def test_every_npc_has_the_narrator_fields(self):
        for name, npc in WORLD["npcs"].items():
            for field in NARRATOR_NPC_FIELDS:
                with self.subTest(npc=name, field=field):
                    self.assertTrue(str(npc.get(field) or "").strip(), f"{name} lacks {field}")

    def test_the_world_rulers_are_not_one_template(self):
        rulers = [n for n, npc in WORLD["npcs"].items() if str(npc.get("role", "")).startswith("Public ruler of")]
        self.assertEqual(len(rulers), 4)
        for field in ("personality", "speech", "want", "fear", "secret"):
            values = {WORLD["npcs"][n][field] for n in rulers}
            with self.subTest(field=field):
                self.assertEqual(len(values), 4, f"rulers share a {field}")

    def test_steward_qiao_is_ready_to_be_a_giver(self):
        qiao = WORLD["npcs"]["Steward Qiao"]
        self.assertIn("commission", qiao["secret"].lower())
        self.assertEqual(qiao["location"], "Golden Pavilion Auction House")


if __name__ == "__main__":
    unittest.main()
