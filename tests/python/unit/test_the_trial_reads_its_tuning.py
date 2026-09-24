"""The sect trial's tuning is read by the engine, and the notes are its twin (v1.2.3).

`trial_modifier` in app/rules/sect_recruitment.py has printed a sect's `base_tn`,
`path_bonuses`, `root_affinities`, `family_archetype_bonus` and
`karma_preference` in the trial notes since the block was written, and the
engine read none of them. Python cannot call Go, so what is held is that the
Go trial consumes every key the notes print, and that the two defaults agree.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.support import code_only

ROOT = Path(__file__).resolve().parents[3]
GO = ROOT / "go_core" / "internal" / "game" / "sect_actions.go"
PY = ROOT / "app" / "rules" / "sect_recruitment.py"


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


if __name__ == "__main__":
    unittest.main()
