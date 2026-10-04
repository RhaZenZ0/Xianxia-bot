"""The Player Editor offers what the engine will take (v1.23.0).

The editor's cards became pickers: every field with a vocabulary is offered
from the content file or the character's own rows, through
`_editor_catalogue()` sent with `/api/player`. A picker built from a copy
offers what the engine will refuse the day the two disagree (rc.46), so the
two vocabularies that are *code* on the engine side - the trades a GM may set
and the world-crossing gates - are held here to the Go that states them, and
the editor is held to drawing each catalogue-backed field as a picker.

Every `player.*` action the controller maps is also one the engine dispatches,
and every `admin.player.*` lever the engine dispatches is either mapped or
named in `DISCORD_ONLY` with its reason - so the next lever added to the
engine cannot sit unreachable from the editor the way five did until v1.23.0.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GAME = ROOT / "go_core" / "internal" / "game"
APP_JS = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")

# Levers the engine has that the dashboard deliberately does not drive.
DISCORD_ONLY = {
    "admin.player.erase": (
        "an erasure also deletes the player's private Discord threads (v1.0.8), which only the "
        "bot can reach; the editor names /admin player erase instead"),
    "admin.player.set_sect_rank": (
        "the sect card's player.set_sect already sets the rank name and level in one audited "
        "write; a second rank lever would be two doors to one row"),
}


def _go_sources() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in GAME.glob("*.go") if not p.name.endswith("_test.go"))


def _function(source: str, name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    if not match:
        raise AssertionError(f"{name} is gone from app.js; the gate is broken, not the tree")
    start = source.index("{", match.end() - 1)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"{name}'s body never closes; the gate is broken, not the tree")


class TheVocabulariesAgreeWithTheEngine(unittest.TestCase):
    def setUp(self):
        from app.dashboard.server import _editor_catalogue

        _editor_catalogue.cache_clear()
        self.addCleanup(_editor_catalogue.cache_clear)
        self.catalogue = _editor_catalogue()
        self.go = _go_sources()

    def test_the_trades_offered_are_the_trades_the_lever_takes(self):
        body = self.go.split("func adminProfessions() []string {", 1)[1].split("\n}\n", 1)[0]
        consts = dict(re.findall(r"(\w+Profession)\s*=\s*\"([^\"]+)\"", self.go))
        household = re.search(r"var householdLessonTrades = \[\]string\{([^}]*)\}", self.go)
        self.assertIsNotNone(household, "householdLessonTrades moved; the gate is broken, not the tree")
        names = set(re.findall(r'"([^"]+)"', household.group(1)))
        for token in re.findall(r"\b(\w+Profession)\b", body):
            self.assertIn(token, consts, f"{token} is not a string constant the gate can read")
            names.add(consts[token])
        names.update(re.findall(r'"([^"]+)"', body))
        self.assertGreaterEqual(len(names), 9, "the reader found too few trades; the gate is broken, not the tree")
        self.assertEqual(sorted(self.catalogue["professions"]), sorted(names), (
            "the trade picker and adminProfessions disagree, so the editor offers a trade the "
            "engine refuses or hides one it would take"))
        # The dashboard keeps its own tuple (it may import only two rules
        # modules), so it is held to the bot's list as well as the engine's.
        from app.rules.progression_systems import PROFESSIONS

        self.assertEqual(sorted(self.catalogue["professions"]), sorted(PROFESSIONS), (
            "the dashboard's EDITOR_TRADES and app.rules' PROFESSIONS disagree"))

    def test_the_gates_offered_are_the_gates_the_engine_has(self):
        gates = re.search(r"var tribulationGates = map\[int64\]tribulationGate\{(.*)\}\n", self.go)
        self.assertIsNotNone(gates, "tribulationGates moved; the gate is broken, not the tree")
        engine = sorted(int(x) for x in re.findall(r"(\d+):\s*\{", gates.group(1)))
        offered = sorted(g["realm"] for g in self.catalogue["tribulation_gates"])
        self.assertEqual(offered, engine, (
            "the tribulation gates read off the realm ladder are not the engine's gates; the card "
            "carried [7,15,23] as a literal until v1.23.0"))

    def test_the_catalogue_is_whole(self):
        for key in ("flames", "laws", "sect_ranks", "realms", "body_realms", "root_mutations", "items", "storage_presets"):
            with self.subTest(key=key):
                self.assertTrue(self.catalogue[key], f"{key} came back empty from the shipped content file")
        self.assertEqual(len(self.catalogue["realms"]), 32)
        self.assertEqual([r["level"] for r in self.catalogue["sect_ranks"]],
                         sorted(r["level"] for r in self.catalogue["sect_ranks"]),
                         "the rank ladder is offered out of its own order")


class EveryLeverIsReachable(unittest.TestCase):
    def test_every_engine_player_lever_is_mapped_or_named(self):
        from app.dashboard.server import AdminDashboardController

        dispatched = set(re.findall(r'case "(admin\.player\.[a-z_]+)":', _go_sources()))
        self.assertGreaterEqual(len(dispatched), 30, "the dispatch reader found too little; the gate is broken")
        mapped = {v for k, v in AdminDashboardController.ACTION_MAP.items() if k.startswith("player.")}
        self.assertEqual(sorted(mapped - dispatched), [], "the dashboard maps a lever the engine does not dispatch")
        self.assertEqual(sorted(dispatched - mapped - set(DISCORD_ONLY)), [], (
            "an admin.player.* lever the engine dispatches has no card in the Player Editor and no "
            "reason in DISCORD_ONLY"))
        self.assertEqual(sorted(set(DISCORD_ONLY) & mapped), [], "a DISCORD_ONLY lever is mapped after all")


class EveryCatalogueFieldIsAPicker(unittest.TestCase):
    def setUp(self):
        self.body = _function(APP_JS, "loadPlayerEditor")

    def test_the_reader_works(self):
        self.assertTrue("editor_catalogue" in self.body, "the brace reader did not return the editor's body")

    def test_each_vocabulary_field_is_drawn_as_a_picker(self):
        pickers = ["realmIndex", "realmPhase", "realmBodyIndex", "realmBodyPhase", "perfRealmIndex", "tribGate",
                   "rootGrade", "rootMutation", "physId", "lawId", "senseStage", "flameId", "flameRefinement",
                   "tradeName", "sectName", "masterId", "storagePreset", "beastId", "abodeGuest", "cooldownAction",
                   "currency", "teleLocation", "questKey", "modHours"]
        missing = [i for i in pickers if f'<select id="{i}"' not in self.body]
        self.assertEqual(missing, [], "a field with a vocabulary is a free-text box again")
        self.assertTrue('list="itemCatalogue"' in self.body, "the item field lost its catalogue")

    def test_the_catalogue_is_read_off_the_player_response(self):
        self.assertTrue("p.editor_catalogue" in self.body)
        self.assertFalse("d.editor_catalogue" in self.body, "the /api/admin snapshot carries no catalogue")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
