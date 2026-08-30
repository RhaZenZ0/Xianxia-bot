from __future__ import annotations

import secrets
from typing import Any, Callable


# Presentation metadata only. Canonical starting attributes continue to come from
# content/world.json via World.starting_stats(). Keeping this file read-only with
# respect to mechanics prevents the onboarding UI from becoming a second rules engine.
CULTIVATION_STYLE_PROFILES: dict[str, dict[str, str]] = {
    "Sword Cultivator": {
        "emoji": "⚔️",
        "summary": "Weapon intent, precise movement and decisive duels.",
        "focus": "Agility • Will • sword techniques",
        "suggested_root": "Metal",
    },
    "Qi Refiner": {
        "emoji": "🌊",
        "summary": "Classical qi circulation, elemental arts and broad spiritual technique use.",
        "focus": "Spirit • Insight • spiritual arts",
        "suggested_root": "Wood",
    },
    "Body Refiner": {
        "emoji": "🥋",
        "summary": "Temper flesh, bone and vitality into a cultivation weapon.",
        "focus": "Body • Will • martial arts",
        "suggested_root": "Earth",
    },
    "Soul Cultivator": {
        "emoji": "🕯️",
        "summary": "Strengthen soul, will and subtle spiritual perception.",
        "focus": "Spirit • Will • soul arts",
        "suggested_root": "Yin",
    },
    "Beast Binder": {
        "emoji": "🐉",
        "summary": "Build equality contracts and grow alongside spirit beasts.",
        "focus": "Spirit • Presence • beastcraft",
        "suggested_root": "Wood",
    },
    "Formation Adept": {
        "emoji": "🧭",
        "summary": "Study arrays, inscriptions and battlefield control through insight.",
        "focus": "Insight • Spirit • formations",
        "suggested_root": "Earth",
    },
}


FAMILY_STYLE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "martial_household": ("Body Refiner", "Sword Cultivator", "Qi Refiner"),
    "escort_martial_family": ("Sword Cultivator", "Qi Refiner", "Body Refiner"),
    "weaponsmith_martial_family": ("Sword Cultivator", "Formation Adept", "Body Refiner"),
    "body_tempering_family": ("Body Refiner", "Qi Refiner", "Beast Binder"),
    "sword_hall_family": ("Sword Cultivator", "Qi Refiner", "Formation Adept"),
    "spear_guard_family": ("Body Refiner", "Formation Adept", "Sword Cultivator"),
    "hidden_weapon_family": ("Soul Cultivator", "Qi Refiner", "Sword Cultivator"),
    "border_garrison_family": ("Body Refiner", "Beast Binder", "Formation Adept"),
    "fallen_martial_clan": ("Qi Refiner", "Soul Cultivator", "Sword Cultivator"),
    "noble_martial_clan": ("Qi Refiner", "Sword Cultivator", "Formation Adept"),
    "alchemy_family": ("Qi Refiner", "Formation Adept", "Soul Cultivator"),
}


FAMILY_EMOJIS: dict[str, str] = {
    "martial_household": "🥋",
    "escort_martial_family": "🐎",
    "weaponsmith_martial_family": "🔨",
    "body_tempering_family": "💪",
    "sword_hall_family": "⚔️",
    "spear_guard_family": "🛡️",
    "hidden_weapon_family": "🗡️",
    "border_garrison_family": "🏰",
    "fallen_martial_clan": "🕯️",
    "noble_martial_clan": "🏯",
    "alchemy_family": "⚗️",
}


LOCATION_THEMES: dict[str, dict[str, Any]] = {
    "Greenriver Town": {
        "emoji": "🌿",
        "color": 0x4F8A5B,
        "mood": "river mist, market bells and herb gardens beneath the shadow of nearby sects",
        "childhood": "Your earliest memories are of Jade River mist, market calls and cultivators passing through the old town gate.",
    },
    "Cloudspine Foothills": {
        "emoji": "⛰️",
        "color": 0x6B7280,
        "mood": "cedar ridges, old caves, beast trails and broken formation towers",
        "childhood": "You grew beneath cedar ridges where training yards face mountain winds and spirit-beast cries carry between the cliffs.",
    },
    "Moonfen Marsh": {
        "emoji": "🌙",
        "color": 0x52618D,
        "mood": "ghost-lotus pools, medicinal reeds, poison mists and hidden camps",
        "childhood": "Your childhood paths wound between black reeds and ghost-lotus pools, where every useful herb had a poisonous twin.",
    },
    "Spirit Jade Rebirth Enclave": {
        "emoji": "💠",
        "color": 0x3F8C8C,
        "mood": "spirit-jade courtyards and protected rebirth sanctums",
        "childhood": "You awakened beneath spirit-jade eaves where reborn souls are watched by patient guardians.",
    },
    "Nine-Heavens Rebirth Terrace": {
        "emoji": "☁️",
        "color": 0x8095B8,
        "mood": "immortal terraces, cloud bridges and ancient lineage halls",
        "childhood": "Your new life began among cloud bridges and ancestral halls suspended above the lower heavens.",
    },
    "Celestial Cradle Province": {
        "emoji": "✨",
        "color": 0x9A7CC1,
        "mood": "celestial courtyards, mandate sigils and primordial qi",
        "childhood": "You were raised beneath celestial sigils where even a child's first breath carries the pressure of higher heaven.",
    },
}

_DEFAULT_THEME: dict[str, Any] = {
    "emoji": "🌌",
    "color": 0x5865F2,
    "mood": "a cultivation settlement shaped by local qi, lineage and circumstance",
    "childhood": "Your early years were shaped by the customs, dangers and spiritual currents of your family's homeland.",
}


def family_archetype_id(family: dict[str, Any] | None) -> str:
    family = family or {}
    return str(family.get("id") or family.get("archetype") or "").strip()


def family_emoji(family: dict[str, Any] | None) -> str:
    return FAMILY_EMOJIS.get(family_archetype_id(family), "🏠")


def location_theme(location: str | None) -> dict[str, Any]:
    return dict(LOCATION_THEMES.get(str(location or ""), _DEFAULT_THEME))


def cultivation_style_profile(path: str | None) -> dict[str, str]:
    return dict(CULTIVATION_STYLE_PROFILES.get(str(path or ""), {
        "emoji": "☯️",
        "summary": "A personal road through cultivation.",
        "focus": "Cultivation fundamentals",
        "suggested_root": "Mortal Root",
    }))


def recommended_cultivation_styles(family: dict[str, Any] | None) -> tuple[str, ...]:
    preferred = FAMILY_STYLE_PREFERENCES.get(family_archetype_id(family), ())
    # Always return only canonical presentation profiles and preserve order.
    return tuple(path for path in preferred if path in CULTIVATION_STYLE_PROFILES)


RandBelow = Callable[[int], int]


# Innate roots are rolled by family/background rather than chosen by the player.
# Every root always remains possible; the mappings only bend the odds.
FAMILY_ROOT_AFFINITIES: dict[str, tuple[str, ...]] = {
    "martial_household": ("Earth", "Metal", "Yang"),
    "escort_martial_family": ("Wind", "Water", "Metal"),
    "weaponsmith_martial_family": ("Metal", "Fire", "Earth"),
    "body_tempering_family": ("Earth", "Yang", "Metal"),
    "sword_hall_family": ("Metal", "Wind", "Lightning"),
    "spear_guard_family": ("Metal", "Earth", "Yang"),
    "hidden_weapon_family": ("Yin", "Water", "Ice"),
    "border_garrison_family": ("Earth", "Wind", "Metal"),
    "fallen_martial_clan": ("Metal", "Yin", "Earth"),
    "noble_martial_clan": ("Metal", "Yang", "Lightning"),
    "alchemy_family": ("Wood", "Fire", "Water"),
}

LOCATION_ROOT_AFFINITIES: dict[str, tuple[str, ...]] = {
    'Greenriver Town': ('Water', 'Wood'),
    'Cloudspine Foothills': ('Earth', 'Wind'),
    'Moonfen Marsh': ('Water', 'Yin', 'Wood'),
    'Spirit Jade Rebirth Enclave': ('Wood', 'Water', 'Yang'),
    'Nine-Heavens Rebirth Terrace': ('Wind', 'Yang', 'Lightning'),
    'Celestial Cradle Province': ('Yang', 'Void', 'Chaos'),
    'Riverguard City': ('Water', 'Earth', 'Yang'),
    'Jadeflow Spirit City': ('Water', 'Earth', 'Yang'),
    'Immortal River City': ('Water', 'Earth', 'Yang'),
    'Celestial River City': ('Water', 'Earth', 'Yang'),
    'Four-Roads Caravan City': ('Wind', 'Water', 'Metal'),
    'Galevein Spirit City': ('Wind', 'Water', 'Metal'),
    'Skyroad Immortal City': ('Wind', 'Water', 'Metal'),
    'Starroad Celestial City': ('Wind', 'Water', 'Metal'),
    'Emberforge City': ('Fire', 'Metal', 'Earth'),
    'Vermilion Furnace City': ('Fire', 'Metal', 'Earth'),
    'Solar Furnace Immortal City': ('Fire', 'Metal', 'Earth'),
    'Solar Crucible Celestial City': ('Fire', 'Metal', 'Earth'),
    'Stoneback Mountain City': ('Earth', 'Yang', 'Metal'),
    'Stoneheart Spirit City': ('Earth', 'Yang', 'Metal'),
    'Adamant Body Immortal City': ('Earth', 'Yang', 'Metal'),
    'Worldstone Celestial City': ('Earth', 'Yang', 'Metal'),
    'Cloudblade City': ('Wind', 'Metal', 'Lightning'),
    'Cloudedge Spirit City': ('Wind', 'Metal', 'Lightning'),
    'Heavenblade Immortal City': ('Wind', 'Metal', 'Lightning'),
    'Firmament Blade City': ('Wind', 'Metal', 'Lightning'),
    'Ironbanner City': ('Metal', 'Earth', 'Yang'),
    'Spearwall Spirit City': ('Metal', 'Earth', 'Yang'),
    'Golden Spear Immortal City': ('Metal', 'Earth', 'Yang'),
    'Mandate Spear City': ('Metal', 'Earth', 'Yang'),
    'Moonfen City': ('Yin', 'Water', 'Ice'),
    'Moonfrost Spirit City': ('Yin', 'Water', 'Ice'),
    'Lunar Veil Immortal City': ('Yin', 'Water', 'Ice'),
    'Lunar Shadow Celestial City': ('Yin', 'Water', 'Ice'),
    'Frostwatch City': ('Ice', 'Wind', 'Earth'),
    'Northwind Spirit City': ('Ice', 'Wind', 'Earth'),
    'Polar Gate Immortal City': ('Ice', 'Wind', 'Earth'),
    'Froststar Border City': ('Ice', 'Wind', 'Earth'),
    'Ashenwall City': ('Earth', 'Metal', 'Yin'),
    'Broken Halo Spirit City': ('Earth', 'Metal', 'Yin'),
    'Fallen Star Immortal City': ('Earth', 'Metal', 'Yin'),
    'Ruined Constellation City': ('Earth', 'Metal', 'Yin'),
    'Azure Crown Imperial City': ('Yang', 'Metal', 'Lightning'),
    'Jade Crown Spirit City': ('Yang', 'Metal', 'Lightning'),
    'Ninefold Noble Immortal City': ('Yang', 'Metal', 'Lightning'),
    'Mandate Crown Celestial City': ('Yang', 'Metal', 'Lightning'),
    'Jadewood Medicine City': ('Wood', 'Water', 'Fire'),
    'Hundred Herb Spirit City': ('Wood', 'Water', 'Fire'),
    'Jade Cauldron Immortal City': ('Wood', 'Water', 'Fire'),
    'Divine Herb Celestial City': ('Wood', 'Water', 'Fire'),
}

_ROOT_BASE_WEIGHTS: dict[str, int] = {
    "Fire": 10, "Water": 10, "Wood": 10, "Earth": 10, "Metal": 10,
    "Lightning": 5, "Wind": 7, "Ice": 5, "Yin": 5, "Yang": 5,
    "Mortal Root": 18, "Void": 2, "Chaos": 1,
}


def family_root_tendencies(family: dict[str, Any] | None) -> tuple[str, ...]:
    """Public root tendencies shown during character creation."""
    return tuple(FAMILY_ROOT_AFFINITIES.get(family_archetype_id(family), ()))


def family_root_weights(
    family: dict[str, Any] | None,
    valid_roots: list[str] | tuple[str, ...],
) -> dict[str, int]:
    """Return public/background-biased weights for an innate root roll.

    Family archetype and homeland have the strongest influence. A generated
    ancestral bloodline affinity may add a small hidden nudge, but it never
    guarantees the result and is not exposed by the creation UI.
    """
    family = family or {}
    roots = [str(root) for root in valid_roots]
    weights = {root: max(1, int(_ROOT_BASE_WEIGHTS.get(root, 6))) for root in roots}

    favored = FAMILY_ROOT_AFFINITIES.get(family_archetype_id(family), ())
    for index, root in enumerate(favored):
        if root in weights:
            weights[root] += (28, 18, 12)[min(index, 2)]

    location = str(family.get("location") or "")
    for index, root in enumerate(LOCATION_ROOT_AFFINITIES.get(location, ())):
        if root in weights:
            weights[root] += (10, 7, 5)[min(index, 2)]

    hidden_affinity = str(family.get("bloodline_affinity") or "")
    if hidden_affinity in weights:
        weights[hidden_affinity] += 10

    # Better-established cultivation families are slightly less likely to have
    # a completely ordinary root, but Mortal Root is never removed.
    if "Mortal Root" in weights:
        tier = max(1, min(5, int(family.get("tier", 1))))
        weights["Mortal Root"] = max(5, weights["Mortal Root"] - (tier - 1) * 2)
    return weights


def roll_family_spiritual_root(
    family: dict[str, Any] | None,
    valid_roots: list[str] | tuple[str, ...],
    *,
    randbelow: RandBelow = secrets.randbelow,
) -> str:
    """Roll an innate spiritual root with family/location weighted odds.

    The chosen cultivation style intentionally does not alter this roll: the
    root is innate, while the cultivation path is the player's deliberate choice.
    """
    weights = family_root_weights(family, valid_roots)
    if not weights:
        return "Mortal Root"
    total = sum(max(1, int(weight)) for weight in weights.values())
    roll = randbelow(total)
    running = 0
    for root, weight in weights.items():
        running += max(1, int(weight))
        if roll < running:
            return root
    return next(reversed(weights))


def _status_band(value: int, bands: tuple[tuple[int, str], ...]) -> str:
    score = max(0, min(100, int(value)))
    for ceiling, label in bands:
        if score <= ceiling:
            return label
    return bands[-1][1]


def family_status_summary(family: dict[str, Any] | None) -> str:
    """Public household status used by both the family card and origin prose."""
    family = family or {}
    wealth = _status_band(int(family.get("wealth", 0)), (
        (30, "struggling"), (45, "modest"), (60, "comfortable"), (75, "prosperous"), (100, "wealthy"),
    ))
    influence = _status_band(int(family.get("influence", 0)), (
        (30, "obscure"), (45, "locally known"), (60, "respected"), (75, "prominent"), (100, "powerful"),
    ))
    stability = _status_band(int(family.get("stability", 0)), (
        (35, "fractured"), (50, "strained"), (65, "steady"), (80, "united"), (100, "deeply rooted"),
    ))
    alignment = int(family.get("alignment_bias", 0))
    reputation = "upright" if alignment >= 3 else ("ruthless" if alignment <= -3 else "pragmatic")
    return f"{wealth.title()} resources • {influence} influence • {stability} household • {reputation} reputation"


def _birth_order_phrase(birth_order: int) -> str:
    order = max(1, int(birth_order or 1))
    labels = {1: "the eldest child", 2: "the second child", 3: "the third child", 4: "the fourth child"}
    return labels.get(order, f"the {order}th child")


def origin_vignette(family: dict[str, Any], path: str, spiritual_root: str, gender: str | None = None) -> str:
    """Build a deterministic public childhood from canonical family circumstances.

    The prose is intentionally derived from location, archetype, standing and
    household pressure. Hidden bloodlines remain hidden; only the rolled root is
    revealed after creation.
    """
    location = str(family.get("location") or "Unknown homeland")
    theme = location_theme(location)
    family_name = str(family.get("family_name") or "your family")
    archetype = str(family.get("name") or "cultivation household")
    style = cultivation_style_profile(path)
    family_line = {
        "martial_household": "Weapons racks, guard drills and practical sparring were part of ordinary household life.",
        "escort_martial_family": "Caravan bells, road maps and stories from dangerous escort routes filled the family compound.",
        "weaponsmith_martial_family": "Hammer-song, quenching steam and the care of weapons formed the soundtrack of your household.",
        "body_tempering_family": "Medicinal baths, stone weights and bruising morning drills were treated as ordinary upbringing.",
        "sword_hall_family": "Wooden practice swords and formal challenges marked the rhythm of the household courtyard.",
        "spear_guard_family": "Formation drills, watch rotations and disciplined spear practice shaped the family's daily rhythm.",
        "hidden_weapon_family": "Quiet hands, careful observation and lessons about what should remain unseen shaped your habits early.",
        "border_garrison_family": "Alarm horns, scout reports and defensive drills made the distant wilderness feel close to home.",
        "fallen_martial_clan": "Ruined halls and stories of former glory taught you that inheritance can be both treasure and burden.",
        "noble_martial_clan": "Retainers, tutors and clan expectations made even childhood feel like preparation for future responsibility.",
        "alchemy_family": "Herb ledgers, furnace smoke and the bitter scent of medicinal residue were ordinary parts of family life.",
    }.get(family_archetype_id(family), f"The customs of the {family_name} {archetype.lower()} shaped your first understanding of strength and duty.")

    status = family_status_summary(family)
    status_text = status[:1].lower() + status[1:] if status else "ordinary circumstances"
    birth_order = _birth_order_phrase(int(family.get("birth_order", 1)))
    birth_sex = str(gender or "").strip().lower()
    if birth_sex == "male":
        birth_order = birth_order.replace(" child", " son")
    elif birth_sex == "female":
        birth_order = birth_order.replace(" child", " daughter")
    head_title = str(family.get("head_title") or "Family Head")
    head_name = str(family.get("head_name") or "the family head")
    boon = str(family.get("boon") or "Family tradition offered no special protection.").rstrip(".")
    risk = str(family.get("risk") or "Every household carried its own pressures.").rstrip(".")

    return (
        f"{theme['childhood']} Born as **{birth_order}** of **{family_name}**, a **{archetype}**, "
        f"you grew within a household marked by {status_text}. {family_line} "
        f"Under **{head_title} {head_name}**, your household could offer {boon[:260].lower()}, "
        f"while you also lived with {risk[:260].lower()}. "
        f"Heaven revealed a **{spiritual_root}** spiritual root at your awakening; it was influenced by lineage and homeland, not chosen. "
        f"Your own decision was the **{style['emoji']} {path}** road, the first step that truly belonged to you."
    )
