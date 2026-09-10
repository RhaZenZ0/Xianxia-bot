from __future__ import annotations

from typing import Any

SURNAMES = (
    "Chen", "Lin", "Zhao", "Shen", "Wei", "Su", "Bai", "Gu", "Han", "Luo",
    "Xu", "Yan", "Jiang", "Qin", "Mu", "Tang", "Ye", "Feng", "Song", "Xie",
)

STARTER_BIRTH_FAMILY_SURNAMES = {
    "martial_household": "Han",
    "escort_martial_family": "Chen",
    "weaponsmith_martial_family": "Wei",
    "body_tempering_family": "Zhao",
    "sword_hall_family": "Shen",
    "spear_guard_family": "Lin",
    "hidden_weapon_family": "Su",
    "border_garrison_family": "Gu",
    "fallen_martial_clan": "Luo",
    "noble_martial_clan": "Qin",
    "alchemy_family": "Bai",
}

MALE_NAMES = ("Wei", "Jun", "Hao", "Tian", "Rui", "Feng", "Ming", "Bo", "Jian", "Kai")
FEMALE_NAMES = ("Mei", "Lan", "Yue", "Xue", "Ling", "Hua", "Ning", "Qiao", "Yan", "Rin")


BLOODLINE_TRAITS = (
    ("Azure Wolf Bloodline", "Body", "Tracking instinct, endurance and coordinated hunting"),
    ("Vermilion Bird Emberline", "Fire", "Fire affinity and resilience to heat"),
    ("Black Tortoise Marrowline", "Earth", "Defense, vitality and patient cultivation"),
    ("White Tiger Warline", "Metal", "Battle instinct, killing intent and weapon affinity"),
    ("Moon Serpent Yin Line", "Water", "Yin sensitivity, concealment and poison resistance"),
    ("Thunder Roc Lineage", "Lightning", "Speed, lightning affinity and aerial techniques"),
    ("Jade River Spirit Line", "Water", "Water affinity, healing intuition and river-sense"),
    ("Stone Bear Ancestry", "Earth", "Powerful physique, endurance and mountain survival"),
)

# Martial-family alliances used by established clans.
CONFEDERACY_NAMES = (
    "Nine Banners Martial Alliance", "Northern River Clan League", "Hundred Peaks Covenant",
    "Red Blade Council", "Jade Plains Alliance", "Three Valleys Blood-Oath League",
)

# Initial creation offers a compact set of classic Xianxia family starts.
FAMILY_ARCHETYPES: tuple[dict[str, Any], ...] = (
    {
        "id": "martial_household", "category": "martial", "name": "Martial Household",
        "wealth": 42, "influence": 48, "stability": 62, "tier": 2, "alignment_bias": 0,
        "location": "Riverguard City",
        "boon": "Weapons, body-training knowledge, guards and combat-minded relatives.",
        "risk": "Feuds, injuries and expectations to defend the family name.",
    },
    {
        "id": "escort_martial_family", "category": "martial", "name": "Escort Agency Martial Family",
        "wealth": 58, "influence": 46, "stability": 54, "tier": 2, "alignment_bias": 2,
        "location": "Four-Roads Caravan City",
        "boon": "Travel contacts, caravan intelligence, practical combat training and escort equipment.",
        "risk": "Bandits, dangerous contracts, merchant enemies and relatives frequently traveling into danger.",
    },
    {
        "id": "weaponsmith_martial_family", "category": "martial", "name": "Weapon-Smith Martial Family",
        "wealth": 52, "influence": 38, "stability": 68, "tier": 2, "alignment_bias": 0,
        "location": "Emberforge City",
        "boon": "Forge access, weapon maintenance, ore contacts and relatives skilled in practical weapon arts.",
        "risk": "Expensive materials, workshop rivalries and pressure to protect valuable forging knowledge.",
    },
    {
        "id": "body_tempering_family", "category": "martial", "name": "Body-Tempering Martial Family",
        "wealth": 36, "influence": 43, "stability": 70, "tier": 2, "alignment_bias": 1,
        "location": "Stoneback Mountain City",
        "boon": "Strong physique traditions, medicinal baths, endurance training and experienced sparring partners.",
        "risk": "Harsh training, frequent injuries and a culture that respects strength above comfort.",
    },
    {
        "id": "sword_hall_family", "category": "martial", "name": "Sword Hall Martial Family",
        "wealth": 48, "influence": 55, "stability": 58, "tier": 3, "alignment_bias": 3,
        "location": "Cloudblade City",
        "boon": "Sword instructors, dueling contacts, old forms and a respected local martial reputation.",
        "risk": "Rival schools, formal challenges and pressure to uphold the family's sword reputation.",
    },
    {
        "id": "spear_guard_family", "category": "martial", "name": "Spear Guard Martial Family",
        "wealth": 44, "influence": 59, "stability": 64, "tier": 3, "alignment_bias": 2,
        "location": "Ironbanner City",
        "boon": "Formation fighting, guard service, disciplined training and strong ties to local officials.",
        "risk": "Military obligations, dangerous guard duty and political pressure from powerful patrons.",
    },
    {
        "id": "hidden_weapon_family", "category": "martial", "name": "Hidden-Weapon Martial Family",
        "wealth": 50, "influence": 41, "stability": 50, "tier": 3, "alignment_bias": -3,
        "location": "Moonfen City",
        "boon": "Concealed weapons, poison-resistance training, stealth methods and discreet underworld contacts.",
        "risk": "Suspicion from orthodox factions, secret feuds and dangerous family techniques.",
    },
    {
        "id": "border_garrison_family", "category": "martial", "name": "Border Garrison Martial Family",
        "wealth": 40, "influence": 62, "stability": 52, "tier": 3, "alignment_bias": 0,
        "location": "Frostwatch City",
        "boon": "Battlefield experience, armor and weapon access, scouts and strong defensive discipline.",
        "risk": "Beast attacks, border wars, casualties and long periods away from home.",
    },
    {
        "id": "fallen_martial_clan", "category": "martial", "name": "Fallen Martial Clan",
        "wealth": 26, "influence": 36, "stability": 36, "tier": 2, "alignment_bias": -4,
        "location": "Ashenwall City",
        "boon": "Old manuals, ruined training grounds, forgotten enemies and a chance to recover a lost martial inheritance.",
        "risk": "Debt, broken alliances, old grudges and internal pressure to restore the clan's former glory.",
    },
    {
        "id": "noble_martial_clan", "category": "martial", "name": "Noble Martial Clan",
        "wealth": 82, "influence": 76, "stability": 46, "tier": 4, "alignment_bias": 0,
        "location": "Azure Crown Imperial City",
        "boon": "Strong resources, martial tutors, political protection, retainers and access to better cultivation contacts.",
        "risk": "Succession disputes, family politics, powerful enemies and heavy expectations placed on talented descendants.",
    },
    {
        "id": "alchemy_family", "category": "martial", "name": "Alchemy Family",
        "wealth": 61, "influence": 45, "stability": 67, "tier": 3, "alignment_bias": 2,
        "location": "Jadewood Medicine City",
        "boon": "Medicinal herb gardens, furnace access, pill lore and relatives experienced in identifying and refining spirit medicines.",
        "risk": "Rare-herb debts, furnace accidents, pill poisoning and rival alchemists seeking the family's recipes.",
    },
)

FAMILY_HOMELANDS: dict[str, dict[str, str]] = {
    "martial_household": {
        "theme": "temperate river martial district", "climate": "temperate",
        "Mortal World": "Riverguard City",
        "Spiritual World": "Jadeflow Spirit City",
        "Immortal World": "Immortal River City",
        "Celestial World": "Celestial River City",
    },
    "escort_martial_family": {
        "theme": "open-road trade crossroads", "climate": "warm-breezy",
        "Mortal World": "Four-Roads Caravan City",
        "Spiritual World": "Galevein Spirit City",
        "Immortal World": "Skyroad Immortal City",
        "Celestial World": "Starroad Celestial City",
    },
    "weaponsmith_martial_family": {
        "theme": "forge and volcanic firelands", "climate": "hot",
        "Mortal World": "Emberforge City",
        "Spiritual World": "Vermilion Furnace City",
        "Immortal World": "Solar Furnace Immortal City",
        "Celestial World": "Solar Crucible Celestial City",
    },
    "body_tempering_family": {
        "theme": "rugged mountain highlands", "climate": "cool-highland",
        "Mortal World": "Stoneback Mountain City",
        "Spiritual World": "Stoneheart Spirit City",
        "Immortal World": "Adamant Body Immortal City",
        "Celestial World": "Worldstone Celestial City",
    },
    "sword_hall_family": {
        "theme": "high windy sword peaks", "climate": "cool-windy",
        "Mortal World": "Cloudblade City",
        "Spiritual World": "Cloudedge Spirit City",
        "Immortal World": "Heavenblade Immortal City",
        "Celestial World": "Firmament Blade City",
    },
    "spear_guard_family": {
        "theme": "fortified dry plains", "climate": "dry-temperate",
        "Mortal World": "Ironbanner City",
        "Spiritual World": "Spearwall Spirit City",
        "Immortal World": "Golden Spear Immortal City",
        "Celestial World": "Mandate Spear City",
    },
    "hidden_weapon_family": {
        "theme": "misty yin wetlands", "climate": "cold-damp",
        "Mortal World": "Moonfen City",
        "Spiritual World": "Moonfrost Spirit City",
        "Immortal World": "Lunar Veil Immortal City",
        "Celestial World": "Lunar Shadow Celestial City",
    },
    "border_garrison_family": {
        "theme": "northern frozen frontier", "climate": "cold",
        "Mortal World": "Frostwatch City",
        "Spiritual World": "Northwind Spirit City",
        "Immortal World": "Polar Gate Immortal City",
        "Celestial World": "Froststar Border City",
    },
    "fallen_martial_clan": {
        "theme": "weathered ruins and old battlefields", "climate": "cool-dry",
        "Mortal World": "Ashenwall City",
        "Spiritual World": "Broken Halo Spirit City",
        "Immortal World": "Fallen Star Immortal City",
        "Celestial World": "Ruined Constellation City",
    },
    "noble_martial_clan": {
        "theme": "prosperous imperial heartland", "climate": "temperate",
        "Mortal World": "Azure Crown Imperial City",
        "Spiritual World": "Jade Crown Spirit City",
        "Immortal World": "Ninefold Noble Immortal City",
        "Celestial World": "Mandate Crown Celestial City",
    },
    "alchemy_family": {
        "theme": "warm herb forests and medicine gardens", "climate": "warm-humid",
        "Mortal World": "Jadewood Medicine City",
        "Spiritual World": "Hundred Herb Spirit City",
        "Immortal World": "Jade Cauldron Immortal City",
        "Celestial World": "Divine Herb Celestial City",
    },
}


WORLD_REBIRTH_LOCATIONS = {
    "Mortal World": "",
    "Spiritual World": "Spirit Jade Rebirth Enclave",
    "Immortal World": "Nine-Heavens Rebirth Terrace",
    "Celestial World": "Celestial Cradle Province",
}

WORLD_REALM_FLOORS = {
    "Mortal World": 0,
    "Spiritual World": 8,
    "Immortal World": 16,
    "Celestial World": 24,
}


def family_tier_name(tier: int) -> str:
    names = {
        1: "Struggling Household",
        2: "Established Family",
        3: "Prominent Clan",
        4: "Great Clan",
        5: "Ancient/Regional Power",
    }
    return names.get(max(1, min(5, int(tier))), "Established Family")


def family_profession_bonus(family: dict[str, Any] | None, profession: str) -> int:
    """Return a small inherited profession bonus from a birth-family tradition.

    Family backgrounds should provide identity and a modest head start without
    replacing profession mastery, facilities, attributes or actual crafting
    progression.  The Alchemy Family therefore contributes a flat +2 only to
    Alchemy-related checks.
    """
    if not family:
        return 0
    if str(family.get("archetype") or family.get("id") or "") == "alchemy_family" and str(profession).casefold() == "alchemy":
        return 2
    return 0


def karma_label(score: int) -> str:
    score = int(score)
    if score >= 500:
        return "Saintly"
    if score >= 200:
        return "Righteous"
    if score >= 50:
        return "Good"
    if score <= -500:
        return "Demonic"
    if score <= -200:
        return "Evil"
    if score <= -50:
        return "Ruthless"
    return "Neutral"


def karma_description(score: int) -> str:
    label = karma_label(score)
    if label in {"Saintly", "Righteous", "Good"}:
        return "Your deeds lean toward mercy, protection and honorable conduct. Righteous NPCs are more inclined to trust your reputation."
    if label in {"Demonic", "Evil", "Ruthless"}:
        return "Your deeds lean toward cruelty, selfish gain and ruthless methods. Fearful or demonic NPCs may respect your reputation while righteous factions distrust it."
    return "Your karmic record has not strongly committed to either a righteous or evil path."



SAMSARA_WORLD_LOCATIONS = dict(WORLD_REBIRTH_LOCATIONS)

UPPER_SAMSARA_FAMILIES: dict[str, tuple[dict[str, Any], ...]] = {
    "Spiritual World": (
        {"id": "spirit_river_ward_house", "archetype": "river_ward_house", "label": "River-Ward House", "location": "Jadeflow Spirit City", "clan_structure": "martial_household", "wealth": 46, "influence": 43, "stability": 68, "tier": 2, "alignment_bias": 2},
        {"id": "gale_merchant_house", "archetype": "spirit_caravan_house", "label": "Gale Merchant House", "location": "Galevein Spirit City", "clan_structure": "extended_household", "wealth": 64, "influence": 48, "stability": 58, "tier": 2, "alignment_bias": 1},
        {"id": "vermilion_forge_house", "archetype": "spirit_forge_house", "label": "Vermilion Forge House", "location": "Vermilion Furnace City", "clan_structure": "extended_household", "wealth": 58, "influence": 45, "stability": 70, "tier": 3, "alignment_bias": 1},
        {"id": "stone_marrow_house", "archetype": "spirit_body_house", "label": "Stone-Marrow House", "location": "Stoneheart Spirit City", "clan_structure": "bloodline_clan", "wealth": 41, "influence": 51, "stability": 72, "tier": 3, "alignment_bias": 0},
        {"id": "cloudedge_sword_courtyard", "archetype": "spirit_sword_house", "label": "Cloudedge Sword Courtyard", "location": "Cloudedge Spirit City", "clan_structure": "martial_household", "wealth": 54, "influence": 62, "stability": 57, "tier": 3, "alignment_bias": 3},
        {"id": "iron_spear_watch_house", "archetype": "spirit_guard_house", "label": "Iron Spear Watch House", "location": "Spearwall Spirit City", "clan_structure": "extended_household", "wealth": 49, "influence": 66, "stability": 64, "tier": 3, "alignment_bias": 2},
        {"id": "moonveil_covert_house", "archetype": "spirit_hidden_house", "label": "Moonveil Covert House", "location": "Moonfrost Spirit City", "clan_structure": "bloodline_clan", "wealth": 55, "influence": 39, "stability": 48, "tier": 3, "alignment_bias": -3},
        {"id": "northwind_frontier_house", "archetype": "spirit_frontier_house", "label": "Northwind Frontier House", "location": "Northwind Spirit City", "clan_structure": "extended_household", "wealth": 43, "influence": 61, "stability": 52, "tier": 3, "alignment_bias": 0},
        {"id": "broken_halo_remnant_house", "archetype": "spirit_fallen_house", "label": "Broken Halo Remnant House", "location": "Broken Halo Spirit City", "clan_structure": "bloodline_clan", "wealth": 29, "influence": 31, "stability": 34, "tier": 2, "alignment_bias": -4},
        {"id": "jade_crown_cadet_house", "archetype": "spirit_cadet_house", "label": "Jade Crown Cadet House", "location": "Jade Crown Spirit City", "clan_structure": "extended_household", "wealth": 60, "influence": 58, "stability": 53, "tier": 3, "alignment_bias": 0},
        {"id": "hundred_herb_apothecary_house", "archetype": "spirit_medicine_house", "label": "Hundred-Herb Apothecary House", "location": "Hundred Herb Spirit City", "clan_structure": "extended_household", "wealth": 63, "influence": 47, "stability": 73, "tier": 3, "alignment_bias": 2},
    ),
    "Immortal World": (
        {"id": "immortal_tide_house", "archetype": "immortal_river_house", "label": "Tide-Listening House", "location": "Immortal River City", "clan_structure": "extended_household", "wealth": 51, "influence": 45, "stability": 70, "tier": 2, "alignment_bias": 2},
        {"id": "skyroad_wayfarer_clan", "archetype": "immortal_wayfarer_clan", "label": "Skyroad Wayfarer Clan", "location": "Skyroad Immortal City", "clan_structure": "bloodline_clan", "wealth": 66, "influence": 54, "stability": 59, "tier": 3, "alignment_bias": 1},
        {"id": "sunforge_lineage", "archetype": "immortal_forge_lineage", "label": "Sunforge Lineage", "location": "Solar Furnace Immortal City", "clan_structure": "bloodline_clan", "wealth": 71, "influence": 58, "stability": 68, "tier": 3, "alignment_bias": 1},
        {"id": "adamant_bone_lineage", "archetype": "immortal_body_lineage", "label": "Adamant-Bone Lineage", "location": "Adamant Body Immortal City", "clan_structure": "bloodline_clan", "wealth": 48, "influence": 63, "stability": 75, "tier": 3, "alignment_bias": 0},
        {"id": "heaven_edge_sword_house", "archetype": "immortal_sword_house", "label": "Heaven-Edge Sword House", "location": "Heavenblade Immortal City", "clan_structure": "martial_household", "wealth": 61, "influence": 69, "stability": 60, "tier": 4, "alignment_bias": 3},
        {"id": "golden_lance_house", "archetype": "immortal_guard_house", "label": "Golden Lance House", "location": "Golden Spear Immortal City", "clan_structure": "extended_household", "wealth": 57, "influence": 71, "stability": 66, "tier": 4, "alignment_bias": 2},
        {"id": "lunar_veil_house", "archetype": "immortal_hidden_house", "label": "Lunar Veil House", "location": "Lunar Veil Immortal City", "clan_structure": "bloodline_clan", "wealth": 62, "influence": 50, "stability": 49, "tier": 4, "alignment_bias": -3},
        {"id": "polar_gate_house", "archetype": "immortal_frontier_house", "label": "Polar Gate House", "location": "Polar Gate Immortal City", "clan_structure": "extended_household", "wealth": 52, "influence": 67, "stability": 55, "tier": 3, "alignment_bias": 0},
        {"id": "fallen_star_successor_house", "archetype": "immortal_successor_house", "label": "Fallen-Star Successor House", "location": "Fallen Star Immortal City", "clan_structure": "extended_household", "wealth": 38, "influence": 44, "stability": 41, "tier": 3, "alignment_bias": -2},
        {"id": "ninefold_cadet_house", "archetype": "immortal_cadet_house", "label": "Ninefold Cadet House", "location": "Ninefold Noble Immortal City", "clan_structure": "extended_household", "wealth": 65, "influence": 62, "stability": 55, "tier": 3, "alignment_bias": 0},
        {"id": "jade_cauldron_house", "archetype": "immortal_medicine_house", "label": "Jade Cauldron House", "location": "Jade Cauldron Immortal City", "clan_structure": "extended_household", "wealth": 72, "influence": 57, "stability": 74, "tier": 4, "alignment_bias": 2},
    ),
    "Celestial World": (
        {"id": "star_river_house", "archetype": "celestial_river_house", "label": "Star-River House", "location": "Celestial River City", "clan_structure": "extended_household", "wealth": 55, "influence": 51, "stability": 73, "tier": 2, "alignment_bias": 2},
        {"id": "constellation_wayfarer_house", "archetype": "celestial_wayfarer_house", "label": "Constellation Wayfarer House", "location": "Starroad Celestial City", "clan_structure": "extended_household", "wealth": 68, "influence": 60, "stability": 61, "tier": 3, "alignment_bias": 1},
        {"id": "solar_crucible_house", "archetype": "celestial_forge_house", "label": "Solar Crucible House", "location": "Solar Crucible Celestial City", "clan_structure": "bloodline_clan", "wealth": 76, "influence": 66, "stability": 71, "tier": 4, "alignment_bias": 1},
        {"id": "worldstone_body_house", "archetype": "celestial_body_house", "label": "Worldstone Body House", "location": "Worldstone Celestial City", "clan_structure": "bloodline_clan", "wealth": 53, "influence": 69, "stability": 78, "tier": 4, "alignment_bias": 0},
        {"id": "firmament_sword_house", "archetype": "celestial_sword_house", "label": "Firmament Sword House", "location": "Firmament Blade City", "clan_structure": "martial_household", "wealth": 67, "influence": 76, "stability": 62, "tier": 4, "alignment_bias": 3},
        {"id": "mandate_spear_house", "archetype": "celestial_guard_house", "label": "Mandate Spear House", "location": "Mandate Spear City", "clan_structure": "extended_household", "wealth": 61, "influence": 79, "stability": 68, "tier": 4, "alignment_bias": 2},
        {"id": "lunar_shadow_house", "archetype": "celestial_hidden_house", "label": "Lunar Shadow House", "location": "Lunar Shadow Celestial City", "clan_structure": "bloodline_clan", "wealth": 69, "influence": 56, "stability": 50, "tier": 4, "alignment_bias": -3},
        {"id": "froststar_watch_house", "archetype": "celestial_frontier_house", "label": "Froststar Watch House", "location": "Froststar Border City", "clan_structure": "extended_household", "wealth": 58, "influence": 72, "stability": 57, "tier": 4, "alignment_bias": 0},
        {"id": "ruined_constellation_successor", "archetype": "celestial_successor_house", "label": "Ruined-Constellation Successor House", "location": "Ruined Constellation City", "clan_structure": "extended_household", "wealth": 42, "influence": 48, "stability": 43, "tier": 3, "alignment_bias": -2},
        {"id": "mandate_crown_minor_house", "archetype": "celestial_cadet_house", "label": "Mandate-Crown Minor House", "location": "Mandate Crown Celestial City", "clan_structure": "extended_household", "wealth": 70, "influence": 65, "stability": 58, "tier": 3, "alignment_bias": 0},
        {"id": "divine_herb_house", "archetype": "celestial_medicine_house", "label": "Divine Herb House", "location": "Divine Herb Celestial City", "clan_structure": "extended_household", "wealth": 78, "influence": 62, "stability": 76, "tier": 4, "alignment_bias": 2},
    ),
}

UPPER_SAMSARA_SURNAMES: dict[str, tuple[str, ...]] = {
    "Spiritual World": ("Yu", "Pei", "Huo", "Shi", "Ji", "Duan", "Nie", "Xue", "Mo", "Rong", "Yao"),
    "Immortal World": ("Cang", "Lu", "Zhu", "He", "Sikong", "Zhen", "Wen", "Leng", "Qu", "Nangong", "Zuo"),
    "Celestial World": ("Xuanyuan", "Tantai", "Baili", "Helian", "Gongye", "Shangguan", "Murong", "Dugu", "Yuwen", "Nalan", "Ouyang"),
}


