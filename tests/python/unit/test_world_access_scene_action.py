import unittest

from tests.support import bot_package_source


class WorldAccessAndSceneActionTests(unittest.TestCase):
    """Source scans of the /action command and the visibility guards. (Moved
    from integration/ in v0.20.3: the seeded database no test read is gone.)"""

    def test_scene_action_replaces_freeform_act(self):
        source = bot_package_source()
        self.assertIn('@registered_root_command(name="action", description="Open the guided Scene Action panel"', source)
        self.assertNotIn('@registered_root_command(name="act"', source)
        for key in ("observe", "investigate", "influence", "stealth", "physical", "qi", "resolve", "aid"):
            self.assertIn(f'"{key}":', source)
        self.assertIn("fixed_roll=fixed", source)
        self.assertIn("structured_scene_action", source)
        self.assertIn('name="🎲 Result"', source)
        self.assertIn('name="🧠 Attribute"', source)
        self.assertIn('name="🎯 Target"', source)
        self.assertIn('name="📝 Attempt"', source)
        self.assertIn("await interaction.followup.send(embed=embed, ephemeral=False)", source)
        self.assertIn('\"scene.action\"', source)
        self.assertIn('self_action = bool(mechanics.get(\"automatic\", False))', source)
        self.assertIn("Outcome: Automatic success — self-directed action.", source)
        self.assertIn("Hidden canonical information remains concealed.", source)
        self.assertIn("This does not reveal hidden canonical information.", source)

    def test_visibility_guards_cover_world_npcs_and_realm_hubs(self):
        source = bot_package_source()
        self.assertIn("_known_locations", source)
        self.assertIn("_location_is_visible", source)
        self.assertIn("_world_is_unlocked", source)
        self.assertIn("realmhub_world_autocomplete", source)
        self.assertIn("You have no reliable knowledge of that cultivator yet", source)
        self.assertIn("Explore known regions to discover additional routes", source)
        self.assertIn("Higher worlds remain beyond perception", source)


if __name__ == "__main__":
    unittest.main()
