from __future__ import annotations

from typing import Any

from .lifespan import realm_lifespan_ceiling
from .worldtime import MINUTES_PER_YEAR

SECT_RANKS: tuple[str, ...] = (
    "Outer Disciple",
    "Inner Disciple",
    "Core Disciple",
    "Deacon",
    "Elder",
    "Hall Master",
    "Grand Elder",
)

INJURIES: tuple[tuple[int, str], ...] = (
    (18, "bruised flesh and strained qi"),
    (32, "a deep flesh wound"),
    (46, "a fractured bone"),
    (60, "damaged meridians"),
    (74, "a severe internal injury"),
    (88, "a grave foundation wound"),
)

NAME_SYLLABLES: tuple[str, ...] = (
    "An", "Bai", "Chen", "Feng", "Gu", "Hao", "Jian", "Lan", "Lei", "Lian",
    "Ming", "Ning", "Qiao", "Ren", "Shan", "Tao", "Wei", "Xue", "Yan", "Yue",
    "Zhen", "Zhi", "Rui", "Mei", "Hua", "Lin", "Mo", "Qin", "Su", "Tian",
)


def initial_rank(profile: dict[str, Any], faction: str, realm_index: int, influence: int) -> str:
    if not faction or faction == "Independent":
        return "Independent Cultivator"
    role = str(profile.get("role") or "").casefold()
    if "grand elder" in role or "patriarch" in role or "ancestor" in role:
        return "Grand Elder"
    if "hall master" in role or "palace master" in role:
        return "Hall Master"
    if "elder" in role:
        return "Elder"
    if "deacon" in role or "steward" in role:
        return "Deacon"
    if "core disciple" in role:
        return "Core Disciple"
    if "inner disciple" in role:
        return "Inner Disciple"
    if "disciple" in role or "recruit" in role:
        return "Inner Disciple" if realm_index >= 2 else "Outer Disciple"
    return rank_for_power(realm_index, influence)


def rank_for_power(realm_index: int, influence: int) -> str:
    realm_index = max(0, int(realm_index))
    influence = max(0, int(influence))
    score = realm_index * 18 + influence // 4
    if score >= 120:
        return "Grand Elder"
    if score >= 95:
        return "Hall Master"
    if score >= 70:
        return "Elder"
    if score >= 52:
        return "Deacon"
    if score >= 38:
        return "Core Disciple"
    if score >= 24:
        return "Inner Disciple"
    return "Outer Disciple"


def rank_index(rank: str) -> int:
    try:
        return SECT_RANKS.index(str(rank))
    except ValueError:
        return -1



def initial_age_years(realm_index: int, phase: int, natural_lifespan: int, seed: int) -> int:
    if int(realm_index) <= 0:
        return 18 + int(seed) % 43
    ceiling = realm_lifespan_ceiling(int(realm_index), int(phase), max(1, int(natural_lifespan)))
    if ceiling is None:
        # Immortals are ageless but still have a history. Give them a substantial
        # pre-simulation age without pretending the engine knows their true origin.
        return 10_000 + int(seed) % 900_001
    fraction = 0.08 + ((int(seed) % 25) / 100.0)
    return max(18, int(round(float(ceiling) * fraction)))


def npc_age_years(life: dict[str, Any], game_minute: int) -> float:
    born = int(life.get("birth_game_minute") or 0)
    age0 = int(life.get("age_at_creation_years") or 18)
    return float(age0) + max(0, int(game_minute) - born) / MINUTES_PER_YEAR


def npc_lifespan_years(life: dict[str, Any], realm_index: int, phase: int) -> int | None:
    return realm_lifespan_ceiling(
        int(realm_index),
        int(phase),
        max(1, int(life.get("natural_lifespan_years") or 75)),
    )


def relation_type(affinity: int, trust: int, grudge: int, *, married: bool = False) -> str:
    if married:
        return "marriage"
    if grudge >= 75:
        return "blood_feud"
    if grudge >= 50:
        return "grudge"
    if affinity >= 75 and trust >= 60:
        return "close_friend"
    if affinity >= 45 and trust >= 35:
        return "friend"
    if affinity <= -45:
        return "rival"
    return "acquaintance"


def injury_for_damage(damage: int) -> tuple[str, int]:
    damage = max(1, int(damage))
    chosen = INJURIES[0]
    for threshold, label in INJURIES:
        if damage >= threshold:
            chosen = (threshold, label)
    severity = max(1, min(10, (damage + 9) // 10))
    return chosen[1], severity


def generated_child_name(parent_a: str, parent_b: str, seed: int) -> str:
    # Prefer the first recognisable name token after honorifics. The generated
    # name is deliberately simple; uniqueness is enforced by the database layer.
    honorifics = {"elder", "abbess", "madam", "steward", "inquisitor", "apothecary", "handler", "emperor", "sovereign", "immortal", "celestial", "old", "disciple", "keeper", "magistrate"}
    tokens = [t for t in str(parent_a).replace("-", " ").split() if t.casefold() not in honorifics]
    surname = tokens[0] if tokens else (str(parent_b).split()[0] if str(parent_b).split() else "Lin")
    a = NAME_SYLLABLES[int(seed) % len(NAME_SYLLABLES)]
    b = NAME_SYLLABLES[(int(seed) // 7 + 11) % len(NAME_SYLLABLES)]
    given = a if a == b else f"{a}{b}"
    return f"{surname} {given}"
