from __future__ import annotations

from dataclasses import dataclass

from .worldtime import MINUTES_PER_YEAR

# Standardized Xianxia longevity model adapted to this bot's canonical realm ladder.
# Values are lifespan CEILINGS, not additive bonuses. Stage 1-9 interpolates inside
# each realm's range. True Immortal (realm index 16) and above no longer die from
# natural aging, but remain vulnerable to combat, tribulations, soul destruction,
# curses, Samsara effects, and other explicit lethal mechanics.
REALM_LIFESPAN_RANGES: tuple[tuple[int, int], ...] = (
    (70, 80),                    # 0 Body Tempering / ordinary mortal baseline
    (100, 250),                  # 1 Qi Refining
    (200, 500),                  # 2 Foundation Establishment
    (500, 1_000),                # 3 Core Formation
    (2_000, 5_000),              # 4 Nascent Soul
    (10_000, 100_000),           # 5 Soul Formation
    (100_000, 1_000_000),        # 6 Divine Transformation
    (10_000_000, 150_000_000),   # 7 Ascension Realm
    (150_000_000, 300_000_000),  # 8 Spirit Body Transformation
    (300_000_000, 600_000_000),  # 9 Soul Transformation
    (600_000_000, 1_000_000_000),# 10 Spirit Transformation
    (1_000_000_000, 2_000_000_000), # 11 Nirvana
    (2_000_000_000, 5_000_000_000), # 12 Law Manifestation
    (5_000_000_000, 10_000_000_000),# 13 Dao Comprehension
    (10_000_000_000, 25_000_000_000),# 14 Dao Integration
    (25_000_000_000, 100_000_000_000),# 15 Transcendence Realm
)

IMMORTAL_REALM_INDEX = 16
DEFAULT_STARTING_AGE = 18
CHILD_CULTIVATION_AWAKENING_AGE = 12


@dataclass(frozen=True)
class LifespanStatus:
    age_years: float
    natural_years: int
    cultivation_bonus_years: int
    extension_years: int
    total_years: int | None
    ageless: bool
    remaining_years: float | None
    realm_floor_years: int | None = None
    realm_ceiling_years: int | None = None

    @property
    def age_display(self) -> str:
        return f"{self.age_years:.1f} years"


def age_years(*, current_game_minute: int, created_game_minute: int, age_at_creation: int) -> float:
    elapsed = max(0, int(current_game_minute) - int(created_game_minute))
    return float(age_at_creation) + (elapsed / MINUTES_PER_YEAR)


def realm_lifespan_range(realm_index: int) -> tuple[int, int] | None:
    idx = max(0, int(realm_index))
    if idx >= IMMORTAL_REALM_INDEX:
        return None
    return REALM_LIFESPAN_RANGES[min(idx, len(REALM_LIFESPAN_RANGES) - 1)]


def realm_lifespan_ceiling(realm_index: int, phase: int, natural_lifespan: int) -> int | None:
    """Return the finite lifespan ceiling for a Qi realm/stage.

    Stage 1 begins near the low end and Stage 9 reaches the high end. Realm 0
    keeps the character's rolled 70-80 year mortal lifespan rather than replacing
    it with a second random value.
    """
    idx = max(0, int(realm_index))
    if idx >= IMMORTAL_REALM_INDEX:
        return None
    if idx == 0:
        return max(1, int(natural_lifespan))
    low, high = REALM_LIFESPAN_RANGES[min(idx, len(REALM_LIFESPAN_RANGES) - 1)]
    p = max(1, min(9, int(phase)))
    return int(round(low + (high - low) * ((p - 1) / 8.0)))


def _body_longevity_bonus(character: dict, qi_ceiling: int) -> int:
    """Body cultivation adds physical longevity without duplicating Qi scaling.

    A synchronized/high body realm can add up to 20% of the Qi-track ceiling.
    This keeps dual cultivation valuable while Qi/realm attainment remains the
    primary source of exponential lifespan.
    """
    body_idx = max(0, int(character.get("body_realm_index", 0)))
    body_phase = max(1, min(9, int(character.get("body_phase", 1))))
    if body_idx <= 0 or qi_ceiling <= 0:
        return 0
    if body_idx >= IMMORTAL_REALM_INDEX:
        return 0
    qi_idx = max(1, int(character.get("realm_index", 0)))
    relative = min(1.0, body_idx / max(1.0, float(qi_idx)))
    stage_factor = 0.55 + 0.45 * ((body_phase - 1) / 8.0)
    return int(qi_ceiling * 0.20 * relative * stage_factor)


def cultivation_lifespan_bonus(character: dict) -> int:
    natural = max(1, int(character.get("natural_lifespan_years", 75)))
    ceiling = realm_lifespan_ceiling(
        int(character.get("realm_index", 0)),
        int(character.get("phase", 1)),
        natural,
    )
    if ceiling is None:
        return 0
    body_bonus = _body_longevity_bonus(character, ceiling)
    return max(0, int(ceiling) - natural) + body_bonus


def is_ageless(character: dict) -> bool:
    return (
        int(character.get("realm_index", 0)) >= IMMORTAL_REALM_INDEX
        or int(character.get("body_realm_index", 0)) >= IMMORTAL_REALM_INDEX
    )


def status(character: dict, current_game_minute: int) -> LifespanStatus:
    natural = max(1, int(character.get("natural_lifespan_years", 75)))
    extensions = max(0, int(character.get("life_extension_years", 0)))
    age = age_years(
        current_game_minute=current_game_minute,
        created_game_minute=int(character.get("created_game_minute", 0)),
        age_at_creation=int(character.get("age_at_creation_years", DEFAULT_STARTING_AGE)),
    )
    ageless = is_ageless(character)
    rng = realm_lifespan_range(int(character.get("realm_index", 0)))
    qi_ceiling = realm_lifespan_ceiling(
        int(character.get("realm_index", 0)),
        int(character.get("phase", 1)),
        natural,
    )
    bonus = cultivation_lifespan_bonus(character)
    total = None if ageless else natural + bonus + extensions
    remaining = None if total is None else max(0.0, float(total) - age)
    return LifespanStatus(
        age_years=age,
        natural_years=natural,
        cultivation_bonus_years=bonus,
        extension_years=extensions,
        total_years=total,
        ageless=ageless,
        remaining_years=remaining,
        realm_floor_years=(rng[0] if rng else None),
        realm_ceiling_years=(rng[1] if rng else None),
    )


def child_age_years(birth_game_minute: int, current_game_minute: int) -> float:
    return max(0, int(current_game_minute) - int(birth_game_minute)) / MINUTES_PER_YEAR
