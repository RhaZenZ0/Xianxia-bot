from __future__ import annotations

import secrets
from typing import Any

MORTAL_WORLD = "Mortal World"


def effective_cultivation(character: dict[str, Any]) -> tuple[int, int]:
    qi = (max(0, int(character.get("realm_index", 0))), max(1, int(character.get("phase", 1))))
    body = (max(0, int(character.get("body_realm_index", 0))), max(1, int(character.get("body_phase", 1))))
    return max(qi, body)


def reincarnation_scale(
    character: dict[str, Any],
    *,
    base_samsara_years: int = 320,
    max_wait_seconds: int = 300,
    max_law_comprehension: int = 0,
) -> dict[str, int]:
    """Return compressed Samsara soul-time.

    Cultivation increases how much subjective time/lives the soul experiences.
    Higher cultivation can also qualify the soul for rebirth in higher worlds,
    while real waiting never exceeds ``max_wait_seconds``.
    """
    realm, phase = effective_cultivation(character)
    base = max(1, int(base_samsara_years))
    tier = min(3, realm // 8)
    local = realm % 8

    if tier == 0:
        years = base + local * max(40, base // 4) + (phase - 1) * max(5, base // 32)
    elif tier == 1:
        years = base * 4 + local * max(160, base * 3 // 4) + (phase - 1) * max(20, base // 10)
    elif tier == 2:
        years = base * 12 + local * max(480, base * 2) + (phase - 1) * max(60, base // 4)
    else:
        years = base * 30 + local * max(1200, base * 5) + (phase - 1) * max(120, base // 2)

    law_bonus = max(0, min(200, int(max_law_comprehension)))
    years += (years * law_bonus) // 1000  # up to +20% subjective soul-time

    cap = max(30, int(max_wait_seconds))
    wait = 60 + realm * 7 + (phase - 1) * 2 + tier * 15
    wait = max(45, min(cap, wait))
    return {"years": max(1, int(years)), "wait_seconds": int(wait), "realm": realm, "phase": phase}


def retention_profile(
    character: dict[str, Any],
    *,
    max_law_comprehension: int = 0,
    karma_score: int = 0,
) -> dict[str, int]:
    """Calculate soul-legacy strengths, not directly restored character stats."""
    realm, phase = effective_cultivation(character)
    tier = min(3, realm // 8)
    karmic_mark = min(12, abs(int(karma_score)) // 80)
    memory = min(96, 5 + realm * 2 + tier * 8 + phase // 2 + karmic_mark)
    talent = min(100, 18 + realm * 2 + tier * 10 + phase + karmic_mark)
    law_echo = min(92, realm + tier * 10 + max(0, int(max_law_comprehension)) // 3)
    insight_echo = min(80, 5 + realm * 2 + tier * 8)
    return {
        "memory_percent": int(memory),
        "talent_percent": int(talent),
        "comprehension_percent": int(law_echo),
        "insight_percent": int(insight_echo),
    }


def choose_samsara_world(previous_realm_index: int, karma_score: int) -> str:
    """Choose a playable rebirth world from the soul's previous cultivation.

    Cultivation is the dominant factor. Karma only nudges the wheel slightly; it
    never guarantees a higher or lower destination. The choice is rolled once at
    true death and persisted in ``reincarnation_state``.
    """
    realm = max(0, min(31, int(previous_realm_index)))
    karma = max(-1000, min(1000, int(karma_score)))

    # Base weights by the world-tier reached in the life that just ended. Within
    # each tier, later realms move probability toward the next/higher worlds.
    tier = min(3, realm // 8)
    local = realm % 8
    if tier == 0:
        weights = [94 - local * 5, 6 + local * 5, 0, 0]
        if local >= 6:
            weights[2] = local - 5
            weights[0] -= weights[2]
    elif tier == 1:
        weights = [22 - min(12, local * 2), 68 - local * 2, 10 + local * 4, 0]
        if local >= 6:
            weights[3] = local - 5
            weights[1] -= weights[3]
    elif tier == 2:
        weights = [5, 22 - min(14, local * 2), 63 - local, 10 + local * 3]
    else:
        weights = [2, 5, max(8, 28 - local * 3), min(85, 65 + local * 3)]

    # Karma moves only a small amount of weight one step upward/downward.
    shift = min(8, abs(karma) // 125)
    if shift and karma > 0:
        for i in range(2, -1, -1):
            moved = min(shift, max(0, weights[i] - 1))
            weights[i] -= moved
            weights[i + 1] += moved
    elif shift and karma < 0:
        for i in range(1, 4):
            moved = min(shift, max(0, weights[i] - 1))
            weights[i] -= moved
            weights[i - 1] += moved

    worlds = ["Mortal World", "Spiritual World", "Immortal World", "Celestial World"]
    weights = [max(0, int(w)) for w in weights]
    total = sum(weights) or 1
    roll = secrets.randbelow(total)
    running = 0
    for world, weight in zip(worlds, weights):
        running += weight
        if roll < running:
            return world
    return MORTAL_WORLD


def samsara_echoes(*, years: int, karma_score: int, previous_realm_index: int) -> dict[str, Any]:
    years = max(1, int(years))
    total_lives = max(3, min(999999, years * 2 + max(0, previous_realm_index) * 20))
    positive = int(karma_score) >= 50
    negative = int(karma_score) <= -50
    forms = [
        "a mortal farmer", "a wandering merchant", "a village physician", "a hunting beast",
        "a mountain spirit", "an ancient tree", "a medicinal herb", "a river fish",
        "a minor cultivator", "a battlefield orphan", "a temple keeper", "a spirit bird",
    ]
    if positive:
        forms += ["a healer who protected a village", "a guardian spirit beast", "a hermit who taught children"]
    if negative:
        forms += ["a predatory spirit beast", "a poisonous marsh plant", "a bandit cultivator", "a resentful wandering ghost"]

    notable_count = min(10, max(4, total_lives // 50 + 4))
    notable: list[str] = []
    for _ in range(notable_count):
        form = forms[secrets.randbelow(len(forms))]
        span = 1 + secrets.randbelow(max(2, min(90, years // max(1, notable_count))))
        notable.append(f"You once lived as {form}; that life lasted roughly {span} years before the wheel turned again.")
    return {"total_lives": int(total_lives), "notable": notable}


def soul_legacy_profile(
    character: dict[str, Any],
    *,
    max_law_comprehension: int = 0,
    karma_score: int = 0,
    perfect_realms: int = 0,
) -> dict[str, Any]:
    retention = retention_profile(
        character,
        max_law_comprehension=max_law_comprehension,
        karma_score=karma_score,
    )
    realm, phase = effective_cultivation(character)
    legacy_points = max(
        1,
        realm * 4 + phase + int(max_law_comprehension) // 5 + max(0, int(perfect_realms)) * 4,
    )
    karmic_fortune = max(-100, min(100, int(karma_score) // 10))

    # Rare traits are weighted by the soul's accumulated depth, but none restore realm.
    trait_roll = secrets.randbelow(1000)
    trait = ""
    depth = min(300, legacy_points + abs(karmic_fortune))
    if trait_roll < min(18, depth // 8):
        trait = "Heaven-Defying Fate"
    elif trait_roll < min(45, 12 + depth // 5):
        trait = "Dao Memory" if int(max_law_comprehension) >= 50 else "Born Knowing"
    elif trait_roll < min(100, 35 + depth // 3):
        trait = "Old Soul"
    elif karma_score <= -400 and trait_roll < 150:
        trait = "Demonic Rebirth"
    elif karma_score >= 400 and trait_roll < 150:
        trait = "Karmic Eyes"

    return {
        "legacy_points": int(legacy_points),
        "memory_seed": int(retention["memory_percent"]),
        "talent_echo": int(retention["talent_percent"]),
        "law_echo": int(retention["comprehension_percent"]),
        "insight_echo": int(retention["insight_percent"]),
        "karmic_fortune": int(karmic_fortune),
        "special_trait": trait,
    }


def soul_legacy_modifiers(legacy: dict[str, Any] | None) -> dict[str, float | int]:
    legacy = legacy or {}
    talent = max(0, min(100, int(legacy.get("talent_echo", 0))))
    law_echo = max(0, min(100, int(legacy.get("law_echo", 0))))
    memory = max(0, min(100, int(legacy.get("memory_seed", 0))))
    trait = str(legacy.get("special_trait") or "")

    cultivation_mult = 1.0 + min(0.10, talent / 1000.0)
    law_bonus = min(4, law_echo // 25)
    insight_mult = 1.0 + min(0.05, memory / 2000.0)

    if trait == "Born Knowing":
        cultivation_mult += 0.03
        law_bonus += 1
    elif trait == "Dao Memory":
        law_bonus += 2
    elif trait == "Old Soul":
        cultivation_mult += 0.02
        insight_mult += 0.02
    elif trait == "Heaven-Defying Fate":
        cultivation_mult += 0.05
        law_bonus += 1
    elif trait == "Demonic Rebirth":
        law_bonus += 1
    elif trait == "Karmic Eyes":
        insight_mult += 0.03

    return {
        "cultivation_mult": round(cultivation_mult, 4),
        "law_bonus": int(law_bonus),
        "insight_mult": round(insight_mult, 4),
    }
