"""The catalog on disk is the catalog (v0.21.3).

`augment_advanced_catalog` used to run only in Python's memory, so the Go
engine - which reads `content/world.json` raw - had 6 manuals where Python
had 148, refused the other 142 in `manual.study`, and no righteous manual was
obtainable at all. `scripts/materialize_world_catalog.py` writes the expansion
into the file; these tests fail the moment the file drifts from it, so the
two sides can never disagree again.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from tests.support import PROJECT_ROOT

from app.rules.advanced_catalog import augment_advanced_catalog

WORLD = PROJECT_ROOT / "content" / "world.json"
SCRIPT = PROJECT_ROOT / "scripts" / "materialize_world_catalog.py"


class MaterialisedCatalogTests(unittest.TestCase):
    def test_disk_equals_expansion(self):
        raw = WORLD.read_text(encoding="utf-8")
        data = json.loads(raw)
        augment_advanced_catalog(data)
        self.assertEqual(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", raw,
            "content/world.json is not materialised: run scripts/materialize_world_catalog.py",
        )

    def test_the_check_script_agrees(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_documented_scale_is_on_disk(self):
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        system = data["technique_system"]
        self.assertEqual(len(system["manuals"]), 160)  # 148 generated + one authored entry manual per public sect (twelve since v0.39.0)
        self.assertEqual(len(system["techniques"]), 564)
        demonic = [m for m in system["manuals"].values() if str(m.get("alignment", "")).casefold() == "demonic"]
        self.assertEqual(len(demonic), 46)

    def test_every_manual_has_an_item_and_none_is_market_stock(self):
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        items = data["items"]
        for manual_id, manual in data["technique_system"]["manuals"].items():
            with self.subTest(manual=manual_id):
                item = items[manual["item_id"]]
                self.assertEqual(item.get("type"), "manual")
                self.assertTrue(item.get("market_excluded"), "an inheritance is given or found, never town-market stock")

    def test_every_public_sect_has_exactly_one_tier_zero_entry_manual(self):
        """v0.21.4: "every sect has a genuine tier-0 entry manual". Authored,
        named, aligned with the sect, path-agnostic, studyable the day you join."""
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        manuals = data["technique_system"]["manuals"]
        techniques = data["technique_system"]["techniques"]
        items = data["items"]
        for sect, definition in data["sects"].items():
            entries = {mid: m for mid, m in manuals.items() if m.get("sect") == sect}
            with self.subTest(sect=sect):
                if definition.get("hidden"):
                    self.assertEqual(entries, {}, "a hidden sect has no public entry manual")
                    continue
                self.assertEqual(len(entries), 1, f"{sect} needs exactly one entry manual, has {sorted(entries)}")
                (mid, manual), = entries.items()
                self.assertEqual(manual["min_realm_index"], 0)
                self.assertEqual(manual["alignment"], definition["alignment"])
                self.assertEqual(manual["path"], "Any")
                self.assertGreaterEqual(len(manual["techniques"]), 3)
                for tid in manual["techniques"]:
                    self.assertEqual(techniques[tid]["manual"], mid)
                    self.assertIn(techniques[tid]["min_mastery"], (0, 1, 2))
                item = items[manual["item_id"]]
                self.assertTrue(item.get("market_excluded"))
                self.assertEqual(item.get("legal_status"), "forbidden" if manual["alignment"] == "Demonic" else "clean")
                self.assertNotIn("generated_advanced_catalog", manual, "authored, not generated")

    def test_the_hidden_sect_is_on_disk_and_flagged(self):
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        sect = data["sects"]["Heaven-Devouring Demon Sect"]
        self.assertTrue(sect.get("hidden"))
        public = [name for name, s in data["sects"].items() if not s.get("hidden")]
        self.assertEqual(len(public), 12)  # two per higher world since v0.39.0


if __name__ == "__main__":
    unittest.main()
