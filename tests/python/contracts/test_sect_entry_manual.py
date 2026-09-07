"""One manual on joining a sect (v0.21.3) - the Python side of the boundary.

The grant itself is the engine's (`sectEntryManual` in
`go_core/internal/game/sect_actions.go`, inside the trial transaction; Go
tests cover the rules). Python only reads `granted_manual` off the result
and says so. These hold that Python never grants it itself, and that the
authority gate did not grow.
"""
from __future__ import annotations

import unittest

from tests.support import PROJECT_ROOT, bot_function_source

SECT_PY = (PROJECT_ROOT / "app" / "bot" / "commands" / "sect.py").read_text(encoding="utf-8")
GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "sect_actions.go").read_text(encoding="utf-8")


class SectEntryManualBoundaryTests(unittest.TestCase):
    def test_python_reads_the_gift_and_does_not_make_one(self):
        source = bot_function_source("sect_recruitment_trial")
        self.assertIn("granted_manual", source)
        for forbidden in ("add_items(", "record_item_provenance(", "sectEntryManual", "WORLD.manuals"):
            self.assertNotIn(forbidden, source, f"the trial handler must not {forbidden}")

    def test_the_reply_tells_the_player_when_they_can_study_it(self):
        source = bot_function_source("sect_recruitment_trial")
        self.assertIn("min_realm_index", source)
        self.assertIn("Manuals & Techniques", source)

    def test_the_engine_grants_inside_the_trial_transaction(self):
        start = GO.index("func sectTrialActionGo(")
        body = GO[start:]
        membership = body.index("INSERT INTO sect_membership")
        grant = body.index("sectEntryManual(catalog, p.SectName, c, owned)")
        result = body.index('out["granted_manual"] = granted')
        self.assertLess(membership, grant)
        self.assertLess(grant, result)
        self.assertIn("'sect_entry'", body[:result], "provenance names the source")

    def test_the_selector_never_gives_a_forbidden_art_to_a_righteous_sect(self):
        start = GO.index("func sectEntryManual(")
        end = GO.index("func ownedManualKeys(")
        selector = GO[start:end]
        self.assertIn('case "demonic":\n\t\tallowed = []string{"demonic"}', selector)
        self.assertIn('case "neutral":\n\t\tallowed = []string{"neutral", "orthodox"}', selector)
        self.assertIn('default:\n\t\tallowed = []string{"orthodox"}', selector)

    def test_the_hidden_sect_grant_moved_to_the_engine_too(self):
        # /sect shadow's grant was the last PLAYER_MUTATIONS row of this kind
        # and closed in v0.23.0. It has its own selector because the rule
        # differs from sectEntryManual's: the shadow cell picks by alignment
        # and the character's path, not by sect.
        shadow_py = bot_function_source("sect_shadow")
        for forbidden in ("add_items(", "record_item_provenance(", "WORLD.manuals", "initiate_hidden_sect"):
            self.assertNotIn(forbidden, shadow_py, f"the shadow handler must not {forbidden}")
        self.assertIn('"sect.shadow"', shadow_py)

        hidden_go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "hidden_sect_actions.go").read_text(encoding="utf-8")
        body = hidden_go[hidden_go.index("func shadowAction("):]
        membership = body.index("INSERT INTO hidden_sect_membership")
        inventory = body.index("INSERT INTO inventory")
        provenance = body.index("INSERT INTO item_provenance")
        self.assertLess(membership, inventory, "membership is written before the manual it justifies")
        self.assertLess(inventory, provenance, "the manual is recorded before its provenance")
        self.assertIn("'hidden_sect_initiation'", body[:provenance + 400])


if __name__ == "__main__":
    unittest.main()
