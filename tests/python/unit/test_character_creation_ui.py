import unittest
from pathlib import Path

from app.creation_ui import (
    CULTIVATION_STYLE_PROFILES,
    cultivation_style_profile,
    family_root_tendencies,
    family_root_weights,
    family_status_summary,
    location_theme,
    origin_vignette,
    recommended_cultivation_styles,
    roll_family_spiritual_root,
)


class CharacterCreationUITests(unittest.TestCase):
    def test_all_canonical_styles_have_guidance(self):
        self.assertEqual(
            set(CULTIVATION_STYLE_PROFILES),
            {
                "Sword Cultivator", "Qi Refiner", "Body Refiner",
                "Soul Cultivator", "Beast Binder", "Formation Adept",
            },
        )
        for profile in CULTIVATION_STYLE_PROFILES.values():
            self.assertTrue(profile["emoji"])
            self.assertTrue(profile["summary"])
            self.assertTrue(profile["focus"])

    def test_alchemy_family_recommends_paths_and_root_tendencies(self):
        family = {"id": "alchemy_family", "location": "Greenriver Town", "tier": 3}
        styles = recommended_cultivation_styles(family)
        self.assertEqual(styles[:2], ("Qi Refiner", "Formation Adept"))
        self.assertEqual(family_root_tendencies(family), ("Wood", "Fire", "Water"))
        weights = family_root_weights(family, ["Wood", "Metal", "Mortal Root"])
        self.assertGreater(weights["Wood"], weights["Metal"])

    def test_spiritual_root_is_random_but_family_weighted(self):
        roots = ["Wood", "Metal", "Mortal Root"]
        alchemy = {"id": "alchemy_family", "location": "Greenriver Town", "tier": 3}
        martial = {"id": "martial_household", "location": "Greenriver Town", "tier": 2}
        # The same deterministic roll lands in different weighted buckets.
        self.assertEqual(roll_family_spiritual_root(alchemy, roots, randbelow=lambda n: 30), "Wood")
        self.assertEqual(roll_family_spiritual_root(martial, roots, randbelow=lambda n: 30), "Metal")

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

    def test_bot_uses_one_family_at_a_time_then_style_dropdown(self):
        source = Path("app/bot/main.py").read_text(encoding="utf-8")
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
        self.assertIn("roll_family_spiritual_root(family, tuple(WORLD.roots))", source)
        self.assertIn("for field in (self.name_input, self.concept_input)", source)
        self.assertNotIn("self.style_input = discord.ui.TextInput", source)
        self.assertNotIn("self.root_input = discord.ui.TextInput", source)
        self.assertNotIn("self.gender_input = discord.ui.TextInput", source)
        self.assertIn("gender=self.selected_gender", source)
        self.assertNotIn('app_commands.Choice(name="Neutral", value="neutral")', source)
        self.assertIn("Open Character Form", source)


if __name__ == "__main__":
    unittest.main()
