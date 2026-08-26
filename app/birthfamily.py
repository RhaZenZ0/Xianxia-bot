from __future__ import annotations

import secrets
from typing import Any

SURNAMES = (
    "Chen", "Lin", "Zhao", "Shen", "Wei", "Su", "Bai", "Gu", "Han", "Luo",
    "Xu", "Yan", "Jiang", "Qin", "Mu", "Tang", "Ye", "Feng", "Song", "Xie",
)

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
        "location": "Greenriver Town",
        "boon": "Weapons, body-training knowledge, guards and combat-minded relatives.",
        "risk": "Feuds, injuries and expectations to defend the family name.",
    },
    {
        "id": "escort_martial_family", "category": "martial", "name": "Escort Agency Martial Family",
        "wealth": 58, "influence": 46, "stability": 54, "tier": 2, "alignment_bias": 2,
        "location": "Greenriver Town",
        "boon": "Travel contacts, caravan intelligence, practical combat training and escort equipment.",
        "risk": "Bandits, dangerous contracts, merchant enemies and relatives frequently traveling into danger.",
    },
    {
        "id": "weaponsmith_martial_family", "category": "martial", "name": "Weapon-Smith Martial Family",
        "wealth": 52, "influence": 38, "stability": 68, "tier": 2, "alignment_bias": 0,
        "location": "Greenriver Town",
        "boon": "Forge access, weapon maintenance, ore contacts and relatives skilled in practical weapon arts.",
        "risk": "Expensive materials, workshop rivalries and pressure to protect valuable forging knowledge.",
    },
    {
        "id": "body_tempering_family", "category": "martial", "name": "Body-Tempering Martial Family",
        "wealth": 36, "influence": 43, "stability": 70, "tier": 2, "alignment_bias": 1,
        "location": "Cloudspine Foothills",
        "boon": "Strong physique traditions, medicinal baths, endurance training and experienced sparring partners.",
        "risk": "Harsh training, frequent injuries and a culture that respects strength above comfort.",
    },
    {
        "id": "sword_hall_family", "category": "martial", "name": "Sword Hall Martial Family",
        "wealth": 48, "influence": 55, "stability": 58, "tier": 3, "alignment_bias": 3,
        "location": "Greenriver Town",
        "boon": "Sword instructors, dueling contacts, old forms and a respected local martial reputation.",
        "risk": "Rival schools, formal challenges and pressure to uphold the family's sword reputation.",
    },
    {
        "id": "spear_guard_family", "category": "martial", "name": "Spear Guard Martial Family",
        "wealth": 44, "influence": 59, "stability": 64, "tier": 3, "alignment_bias": 2,
        "location": "Greenriver Town",
        "boon": "Formation fighting, guard service, disciplined training and strong ties to local officials.",
        "risk": "Military obligations, dangerous guard duty and political pressure from powerful patrons.",
    },
    {
        "id": "hidden_weapon_family", "category": "martial", "name": "Hidden-Weapon Martial Family",
        "wealth": 50, "influence": 41, "stability": 50, "tier": 3, "alignment_bias": -3,
        "location": "Moonfen Marsh",
        "boon": "Concealed weapons, poison-resistance training, stealth methods and discreet underworld contacts.",
        "risk": "Suspicion from orthodox factions, secret feuds and dangerous family techniques.",
    },
    {
        "id": "border_garrison_family", "category": "martial", "name": "Border Garrison Martial Family",
        "wealth": 40, "influence": 62, "stability": 52, "tier": 3, "alignment_bias": 0,
        "location": "Cloudspine Foothills",
        "boon": "Battlefield experience, armor and weapon access, scouts and strong defensive discipline.",
        "risk": "Beast attacks, border wars, casualties and long periods away from home.",
    },
    {
        "id": "fallen_martial_clan", "category": "martial", "name": "Fallen Martial Clan",
        "wealth": 26, "influence": 36, "stability": 36, "tier": 2, "alignment_bias": -4,
        "location": "Greenriver Town",
        "boon": "Old manuals, ruined training grounds, forgotten enemies and a chance to recover a lost martial inheritance.",
        "risk": "Debt, broken alliances, old grudges and internal pressure to restore the clan's former glory.",
    },
    {
        "id": "noble_martial_clan", "category": "martial", "name": "Noble Martial Clan",
        "wealth": 82, "influence": 76, "stability": 46, "tier": 4, "alignment_bias": 0,
        "location": "Greenriver Town",
        "boon": "Strong resources, martial tutors, political protection, retainers and access to better cultivation contacts.",
        "risk": "Succession disputes, family politics, powerful enemies and heavy expectations placed on talented descendants.",
    },
    {
        "id": "alchemy_family", "category": "martial", "name": "Alchemy Family",
        "wealth": 61, "influence": 45, "stability": 67, "tier": 3, "alignment_bias": 2,
        "location": "Greenriver Town",
        "boon": "Medicinal herb gardens, furnace access, pill lore and relatives experienced in identifying and refining spirit medicines.",
        "risk": "Rare-herb debts, furnace accidents, pill poisoning and rival alchemists seeking the family's recipes.",
    },
)

WORLD_REBIRTH_LOCATIONS = {
    "Mortal World": "Greenriver Town",
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


def _given_name(gender: str) -> str:
    pool = MALE_NAMES if gender == "male" else FEMALE_NAMES
    return pool[secrets.randbelow(len(pool))]


def _relative(surname: str, relation: str, gender: str, age: int, realm_index: int, phase: int) -> dict[str, Any]:
    return {
        "name": f"{surname} {_given_name(gender)}",
        "relation": relation,
        "gender": gender,
        "age": age,
        "realm_index": realm_index,
        "phase": phase,
    }


def generate_family_options(world_name: str = "Mortal World") -> list[dict[str, Any]]:
    """Generate the available birth-family choices.

    /begin uses the Mortal World. Samsara can reuse the same cultural archetypes
    inside protected rebirth districts of higher worlds.
    """
    world_name = world_name if world_name in WORLD_REBIRTH_LOCATIONS else "Mortal World"
    surnames = list(SURNAMES)
    for i in range(len(surnames) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        surnames[i], surnames[j] = surnames[j], surnames[i]
    options: list[dict[str, Any]] = []
    world_floor = WORLD_REALM_FLOORS[world_name]
    for idx, archetype in enumerate(FAMILY_ARCHETYPES):
        surname = surnames[idx]
        profile = dict(archetype)
        profile["surname"] = surname
        family_word = "Clan" if "clan" in profile["id"] else "Family"
        profile["family_name"] = f"{surname} {family_word}"

        # First-life choices keep their own Mortal World locations. Rebirths in
        # higher worlds are born inside protected cradle districts.
        if world_name != "Mortal World":
            profile["location"] = WORLD_REBIRTH_LOCATIONS[world_name]
            world_bonus = {"Spiritual World": 12, "Immortal World": 24, "Celestial World": 36}[world_name]
            profile["wealth"] = min(100, int(profile["wealth"]) + world_bonus)
            profile["influence"] = min(100, int(profile["influence"]) + world_bonus)
            profile["tier"] = min(5, int(profile["tier"]) + {"Spiritual World": 1, "Immortal World": 2, "Celestial World": 3}[world_name])
        profile["rebirth_world"] = world_name

        # The family head should be credible in the world where the child is born.
        if world_name == "Mortal World":
            if profile["id"] in {"fallen_martial_clan"}:
                head_realm = 1 + secrets.randbelow(2)
            elif profile["id"] in {"noble_martial_clan"}:
                head_realm = 2 + secrets.randbelow(3)
            elif profile["id"] in {"noble_martial_clan", "sword_hall_family", "spear_guard_family", "border_garrison_family"}:
                head_realm = 1 + secrets.randbelow(3)
            elif profile["id"] == "martial_household":
                head_realm = secrets.randbelow(2)
            else:
                head_realm = secrets.randbelow(3)
        else:
            head_realm = min(31, world_floor + secrets.randbelow(5))

        head_gender = "male" if secrets.randbelow(2) == 0 else "female"
        head_title = "Patriarch" if head_gender == "male" else "Matriarch"
        profile["head_name"] = f"{surname} {_given_name(head_gender)}"
        profile["head_gender"] = head_gender
        profile["head_title"] = head_title
        profile["head_realm_index"] = head_realm
        profile["head_phase"] = 1 + secrets.randbelow(9)
        profile["birth_order"] = 1 + secrets.randbelow(4)

        # These starts are established Xianxia households/clans. Larger or older
        # clans have more branches and a stronger chance of an awakened bloodline.
        is_established_martial_clan = profile["id"] in {"fallen_martial_clan", "noble_martial_clan"}
        profile["clan_structure"] = "bloodline_clan" if is_established_martial_clan else "martial_household"

        bloodline_chance = 82 if is_established_martial_clan else 50 + min(20, int(profile.get("tier", 1)) * 4)
        if secrets.randbelow(100) < bloodline_chance:
            bloodline_name, affinity, trait = BLOODLINE_TRAITS[secrets.randbelow(len(BLOODLINE_TRAITS))]
            if profile["id"] == "fallen_martial_clan":
                base_purity = 38
            elif profile["id"] == "noble_martial_clan":
                base_purity = 72
            elif profile["id"] in {"body_tempering_family", "sword_hall_family", "hidden_weapon_family"}:
                base_purity = 52
            else:
                base_purity = 45
            profile["bloodline_name"] = bloodline_name
            profile["bloodline_affinity"] = affinity
            profile["bloodline_trait"] = trait
            profile["bloodline_purity"] = max(5, min(100, base_purity - 10 + secrets.randbelow(21)))
        else:
            profile["bloodline_name"] = "None"
            profile["bloodline_affinity"] = "None"
            profile["bloodline_trait"] = "No awakened ancestral bloodline"
            profile["bloodline_purity"] = 0

        profile["branch_count"] = 2 + secrets.randbelow(3 + int(profile.get("tier", 1))) if is_established_martial_clan else 1
        profile["retainer_count"] = max(0, 3 + secrets.randbelow(8 + int(profile.get("wealth", 20)) // 2))
        if is_established_martial_clan and secrets.randbelow(100) < 30:
            profile["confederacy_name"] = CONFEDERACY_NAMES[secrets.randbelow(len(CONFEDERACY_NAMES))]
        else:
            profile["confederacy_name"] = "None"

        relative_floor = world_floor if world_name != "Mortal World" else 0
        profile["relatives"] = [
            _relative(surname, "Father", "male", 34 + secrets.randbelow(18), max(relative_floor, min(head_realm, relative_floor + 2)), 1 + secrets.randbelow(9)),
            _relative(surname, "Mother", "female", 32 + secrets.randbelow(18), max(relative_floor, min(head_realm, relative_floor + 1)), 1 + secrets.randbelow(9)),
            _relative(surname, "Older/Younger Sibling", "male" if secrets.randbelow(2) == 0 else "female", 12 + secrets.randbelow(16), relative_floor, 1),
        ]
        options.append(profile)
    return options

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


def family_forage_bonus(family: dict[str, Any] | None) -> int:
    """Return inherited medicinal-herb fieldcraft from the Alchemy Family."""
    if not family:
        return 0
    return 2 if str(family.get("archetype") or family.get("id") or "") == "alchemy_family" else 0


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


def inherited_root(parent_root: str, roots: list[str], *, family_tier: int, karma_score: int = 0, bloodline_purity: int = 0) -> str:
    parent_root = parent_root or "Mortal Root"
    # Family cultivation history and karma slightly influence fate without guaranteeing talent.
    echo_chance = min(88, 40 + int(family_tier) * 6 + min(10, abs(int(karma_score)) // 50) + max(0, int(bloodline_purity)) // 10)
    if secrets.randbelow(100) < echo_chance:
        return parent_root
    if not roots:
        return "Mortal Root"
    # Higher-tier families are a little less likely to roll the completely ordinary root.
    candidates = list(roots)
    for _ in range(max(1, int(family_tier))):
        pick = candidates[secrets.randbelow(len(candidates))]
        if pick != "Mortal Root" or secrets.randbelow(100) < 45:
            return pick
    return "Mortal Root"

SAMSARA_WORLD_LOCATIONS = dict(WORLD_REBIRTH_LOCATIONS)


def generate_samsara_family(world_name: str = "Mortal World", karma_score: int = 0) -> dict[str, Any]:
    """Generate a fresh NPC birth family in the Samsara-selected world.

    The same family archetypes are reused. Higher-world rebirths are placed
    in protected cradle districts and their family circumstances are scaled to
    make sense for that world. Karma bends the odds without determining them.
    """
    world_name = world_name if world_name in SAMSARA_WORLD_LOCATIONS else "Mortal World"
    options = generate_family_options(world_name)
    karma = max(-1000, min(1000, int(karma_score)))
    weights: list[int] = []
    for profile in options:
        pid = str(profile.get("id", ""))
        weight = 20
        if karma >= 50:
            # Positive karma nudges the wheel toward stable/protective martial homes.
            if pid in {"martial_household", "weaponsmith_martial_family", "body_tempering_family", "spear_guard_family", "noble_martial_clan", "alchemy_family"}:
                weight += min(24, karma // 45)
            if pid in {"fallen_martial_clan", "hidden_weapon_family"}:
                weight = max(6, weight - min(10, karma // 90))
        elif karma <= -50:
            # Negative karma favors harsher or conflict-heavy starts without
            # making them weaker in cultivation potential.
            if pid in {"fallen_martial_clan", "hidden_weapon_family", "escort_martial_family", "border_garrison_family"}:
                weight += min(28, (-karma) // 40)
            if pid == "noble_martial_clan":
                weight = max(8, weight - min(8, (-karma) // 120))
        weights.append(max(1, weight))

    roll = secrets.randbelow(sum(weights))
    running = 0
    profile = dict(options[0])
    for candidate, weight in zip(options, weights):
        running += weight
        if roll < running:
            profile = dict(candidate)
            break

    if karma > 0:
        profile["stability"] = min(100, int(profile.get("stability", 50)) + min(10, karma // 100))
    elif karma < 0:
        profile["influence"] = min(100, int(profile.get("influence", 20)) + min(8, (-karma) // 125))
        profile["stability"] = max(10, int(profile.get("stability", 50)) - min(8, (-karma) // 125))

    profile["location"] = SAMSARA_WORLD_LOCATIONS[world_name]
    profile["rebirth_world"] = world_name
    profile["boon"] = f"A fresh {world_name} birth after Samsara. " + str(profile.get("boon", ""))
    return profile

