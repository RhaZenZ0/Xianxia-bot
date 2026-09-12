import unittest
from pathlib import Path

from tests.support import bot_class_source, bot_package_source

from app.rules.creation_ui import CULTIVATION_STYLE_PROFILES, cultivation_style_profile, family_root_tendencies, family_status_summary, location_theme, origin_vignette, recommended_cultivation_styles, selectable_cultivation_styles, style_is_open_to


class CharacterCreationUITests(unittest.TestCase):
    def test_all_canonical_styles_have_guidance(self):
        self.assertEqual(
            set(CULTIVATION_STYLE_PROFILES),
            {
                "Sword Cultivator", "Qi Refiner", "Body Refiner",
                "Soul Cultivator", "Beast Binder", "Formation Adept",
                # v1.0.0-rc.8: the birth-gated seventh.
                "Ghost Cultivator",
            },
        )
        for profile in CULTIVATION_STYLE_PROFILES.values():
            self.assertTrue(profile["emoji"])
            self.assertTrue(profile["summary"])
            self.assertTrue(profile["focus"])

    def test_the_ghost_road_is_offered_only_to_those_born_to_it(self):
        """v1.0.0-rc.8: the picker never shows a path the engine would refuse."""
        paths = ("Sword Cultivator", "Qi Refiner", "Body Refiner", "Soul Cultivator",
                 "Beast Binder", "Formation Adept", "Ghost Cultivator")
        ordinary = {"id": "alchemy_family", "location": "Jadewood Medicine City", "tier": 3}
        ghost = {"id": "tomb_watch_clan", "location": "Ashenwall City", "tier": 2}
        self.assertNotIn("Ghost Cultivator", selectable_cultivation_styles(paths, ordinary))
        self.assertIn("Ghost Cultivator", selectable_cultivation_styles(paths, ghost))
        # And it leads for a household born to it.
        self.assertEqual(selectable_cultivation_styles(paths, ghost)[0], "Ghost Cultivator")
        # Every other path is still offered to everyone.
        self.assertEqual(set(selectable_cultivation_styles(paths, ordinary)), set(paths) - {"Ghost Cultivator"})
        self.assertFalse(style_is_open_to("Ghost Cultivator", ordinary))
        self.assertTrue(style_is_open_to("Qi Refiner", ghost))

    def test_alchemy_family_recommends_paths_and_root_tendencies(self):
        family = {"id": "alchemy_family", "location": "Greenriver Town", "tier": 3}
        styles = recommended_cultivation_styles(family)
        self.assertEqual(styles[:2], ("Qi Refiner", "Formation Adept"))
        self.assertEqual(family_root_tendencies(family), ("Wood", "Fire", "Water"))

    def test_bot_uses_one_family_at_a_time_then_style_dropdown(self):
        # Phase 1 of the main.py split: read the package, the creation UI
        # classes move to ui/creation.py in phase 8 of the plan.
        source = bot_package_source()
        self.assertIn("class BirthFamilyPreviousButton", source)
        self.assertIn("class BirthFamilyNextButton", source)
        self.assertIn('label="Choose Family"', source)
        self.assertNotIn("class BirthFamilySelect", source)
        self.assertIn("class CultivationStyleSelect", source)
        self.assertIn('placeholder="Choose your cultivation path"', source)
        self.assertIn("class BirthSexSelect", source)
        self.assertIn('placeholder="Choose birth sex"', source)
        self.assertIn('label="Male", value="male"', source)
        self.assertIn('label="Female", value="female"', source)
        # v0.30.0: the Python root roll is gone from the rules tier entirely,
        # not merely unused by the creation flow.
        self.assertNotIn("roll_family_spiritual_root", source)
        self.assertIn("ENGINE.authoritative_action(", source)
        self.assertIn('"character.create"', source)
        self.assertIn('"character.family_options"', source)
        self.assertIn('"family_choice_id"', source)
        modal_source = bot_class_source("CharacterModal")
        self.assertNotIn('"family": family', modal_source)
        self.assertIn("for field in (self.name_input, self.concept_input)", source)
        self.assertNotIn("self.style_input = discord.ui.TextInput", source)
        self.assertNotIn("self.root_input = discord.ui.TextInput", source)
        self.assertNotIn("self.gender_input = discord.ui.TextInput", source)
        self.assertIn('"gender": self.selected_gender', source)
        self.assertNotIn('app_commands.Choice(name="Neutral", value="neutral")', source)
        self.assertIn("Open Character Form", source)

    def test_public_root_tendencies_do_not_leak_hidden_bloodline(self):
        family = {"id": "martial_household", "bloodline_affinity": "Lightning"}
        self.assertEqual(family_root_tendencies(family), ("Earth", "Metal", "Yang"))

    def test_location_family_status_and_origin_shape_background(self):
        green = location_theme("Greenriver Town")
        marsh = location_theme("Moonfen Marsh")
        self.assertNotEqual(green["color"], marsh["color"])
        family = {
            "id": "alchemy_family", "family_name": "Su Family", "name": "Alchemy Family",
            "location": "Greenriver Town", "tier": 3, "wealth": 61, "influence": 55,
            "stability": 67, "alignment_bias": 2, "birth_order": 2,
            "head_title": "Matriarch", "head_name": "Su Mei",
            "boon": "Medicinal herb gardens and furnace access.",
            "risk": "Rare-herb debts and rival alchemists.",
        }
        status = family_status_summary(family)
        self.assertIn("Prosperous", status)
        self.assertIn("respected", status)
        text = origin_vignette(family, "Qi Refiner", "Wood", "female")
        self.assertIn("Jade River", text)
        self.assertIn("furnace", text.lower())
        self.assertIn("second daughter", text)
        self.assertIn("Matriarch Su Mei", text)
        self.assertIn("Wood", text)
        self.assertIn("Qi Refiner", text)

    def test_style_profile_has_safe_fallback(self):
        self.assertEqual(cultivation_style_profile("Unknown")["emoji"], "☯️")

if __name__ == "__main__":
    unittest.main()
