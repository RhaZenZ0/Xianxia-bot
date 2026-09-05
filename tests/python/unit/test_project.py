from tests.support import PROJECT_ROOT, bot_package_source, bot_source_files
import json
import ast
import unittest
from pathlib import Path


ROOT = PROJECT_ROOT
WORLD_PATH = ROOT / "content" / "world.json"



def _bot_ast_nodes():
    """Every AST node in every app/bot module. Phase 1 of the main.py split
    (v0.19.33): these reference checks used to parse main.py alone, so a bad
    DB./SIM. reference in a commands/ module was never seen."""
    import ast as _ast

    for path in bot_source_files():
        yield from _ast.walk(_ast.parse(path.read_text(encoding="utf-8")))


class ProjectDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = json.loads(WORLD_PATH.read_text(encoding="utf-8"))

    def test_world_json_has_expected_ladders(self):
        for key in ("realms", "body_realms"):
            realms = self.world[key]
            self.assertEqual(len(realms), 32, key)
            for realm in realms:
                self.assertEqual(len(realm["phase_costs"]), 9, realm["name"])
                self.assertIn("world", realm)
                self.assertIn("base_tn", realm)

    def test_aptitude_catalogs_are_complete_and_ordered(self):
        root_system = self.world["spiritual_root_system"]
        grades = root_system["grades"]
        self.assertEqual([grade["min_roll"] for grade in grades], sorted(grade["min_roll"] for grade in grades))
        self.assertEqual(grades[0]["name"], "Mortal")
        self.assertEqual(grades[-1]["name"], "Immortal")
        self.assertGreaterEqual(len(self.world["bloodlines"]), 8)
        self.assertGreaterEqual(len(self.world["physiques"]), 8)
        for bloodline_id, bloodline in self.world["bloodlines"].items():
            self.assertEqual(len(bloodline["evolutions"]), 3, bloodline_id)
            self.assertGreaterEqual(len(bloodline["ancestral_techniques"]), 3, bloodline_id)
        for physique_id, physique in self.world["physiques"].items():
            if physique_id == "ordinary_mortal_body":
                continue
            self.assertEqual(len(physique["evolutions"]), 3, physique_id)
            self.assertIn("advantage", physique)
            self.assertIn("drawback", physique)

    def test_perfection_progress_totals_100(self):
        for key in ("perfection", "body_perfection"):
            p = self.world[key]
            self.assertEqual(len(p["quests"]), len(p["quest_progress"]))
            self.assertEqual(p["training_cap"] + sum(p["quest_progress"]), 100)

    def test_all_item_references_exist(self):
        items = set(self.world["items"])
        for name, recipe in self.world["recipes"].items():
            for section in ("cost", "output"):
                for item_id in recipe.get(section, {}):
                    self.assertIn(item_id, items, f"recipe {name}: {item_id}")
        for event in self.world.get("unexpected_events", []):
            for item_id in event.get("player_reward", {}).get("items", {}):
                self.assertIn(item_id, items, f"event {event['id']}: {item_id}")
        for inheritance_id, inheritance in self.world.get("inheritances", {}).items():
            item_id = inheritance.get("item")
            if item_id:
                self.assertIn(item_id, items, f"inheritance {inheritance_id}: {item_id}")
        for realm_id, realm in self.world.get("secret_realms", {}).items():
            for room in realm.get("rooms", []):
                for item_id in room.get("items", {}):
                    self.assertIn(item_id, items, f"secret realm {realm_id}: {item_id}")

    def test_secret_realm_references_exist(self):
        secret_realms = self.world["secret_realms"]
        inheritances = self.world["inheritances"]
        locations = self.world["locations"]
        for event in self.world.get("unexpected_events", []):
            if event.get("kind") == "secret_realm":
                self.assertIn(event["secret_realm_id"], secret_realms)
        for realm_id, realm in secret_realms.items():
            self.assertIn(realm["location"], locations, realm_id)
            self.assertIn(realm["inheritance_id"], inheritances, realm_id)

    def test_every_npc_has_a_canonical_location(self):
        locations = self.world["locations"]
        for name, npc in self.world["npcs"].items():
            self.assertIn(npc.get("location"), locations, name)

    def test_hidden_master_realm_indices_are_valid(self):
        max_index = len(self.world["realms"]) - 1
        for name, npc in self.world["npcs"].items():
            hidden = npc.get("hidden_master")
            if not hidden:
                continue
            self.assertTrue(0 <= hidden["true_realm_index"] <= max_index, name)
            projected = hidden.get("projected_realm_index")
            if projected is not None:
                self.assertTrue(0 <= projected <= max_index, name)

    def test_bot_database_method_references_exist(self):
        db_tree = ast.parse((ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8"))
        methods = set()
        for node in db_tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "Database":
                methods.update(
                    child.name
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        references = {
            node.attr
            for node in _bot_ast_nodes()
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "DB"
        }
        self.assertEqual(references - methods, set())

    def test_no_duplicate_slash_command_names_within_same_group(self):
        seen: set[tuple[str, str]] = set()
        duplicates: list[tuple[str, str]] = []
        for node in _bot_ast_nodes():
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "registered_group_command"
                    and decorator.args
                ):
                    continue
                target = ast.unparse(decorator.args[0])
                command_name = node.name
                for keyword in decorator.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                        command_name = str(keyword.value.value)
                key = (target, command_name)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
        self.assertEqual(duplicates, [])

    def test_bot_worldsim_method_references_exist(self):
        sim_tree = ast.parse((ROOT / "app" / "simulation" / "world.py").read_text(encoding="utf-8"))
        methods = set()
        for node in sim_tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "WorldSimulator":
                methods.update(
                    child.name
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        references = {
            node.attr
            for node in _bot_ast_nodes()
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "SIM"
        }
        self.assertEqual(references - methods, set())

    def test_battle_ui_has_core_actions_and_explicit_spare_kill_finish(self):
        source = bot_package_source()
        for label in ('label="Attack"', 'label="Defend"', 'label="Flee"', 'label="Refresh"', 'label="Spare"', 'label="Kill"'):
            self.assertIn(label, source)
        self.assertIn("class BattleTechniqueSelect", source)
        self.assertIn("class BattleRecoverySelect", source)
        self.assertIn("interaction.edit_original_response", source)
        self.assertIn("interaction.response.edit_message", source)
        self.assertIn("expected_battle_id=self.battle_id", source)
        self.assertIn("COMBAT.finalize(", source)
        self.assertIn("COMBAT.turn(", source)
        self.assertNotIn("status='won' if nhp<=0", source)
        self.assertIn('@registered_group_command(battle_group, name="challenge"', source)
        self.assertIn('@registered_group_command(battle_group, name="finish"', source)
        self.assertNotIn('SIM.apply_player_action(', source)
        self.assertIn('result.get("impacts")', source)

    def test_random_event_pipeline_has_persistent_consequences_and_deduplication(self):
        bot_source = bot_package_source()
        database_source = (ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        worldsim_source = (ROOT / "app" / "simulation" / "world.py").read_text(encoding="utf-8")
        self.assertIn('"world_event.act"', bot_source)
        self.assertNotIn("SIM.apply_random_event(", bot_source)
        self.assertIn("for event in sim_run.events", bot_source)
        self.assertIn("idx_world_events_active_dedupe", database_source)
        self.assertIn('"autonomous_world_events"', worldsim_source)
        go_sim_source = (ROOT / "go_core" / "internal" / "simulation" / "world.go").read_text(encoding="utf-8")
        self.assertIn('"autonomous_world_events"', go_sim_source)
        self.assertIn("applyAutonomousWorldEffect", go_sim_source)


if __name__ == "__main__":
    unittest.main()
