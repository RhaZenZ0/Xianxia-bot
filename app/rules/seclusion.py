from __future__ import annotations

from typing import Any


def seclusion_daily_gain(
    character: dict[str, Any],
    *,
    mode: str,
    environment_mult: float = 1.0,
    soul_cultivation_mult: float = 1.0,
) -> int:
    """Deterministic daily background-cultivation rate.

    Seclusion is intentionally slower than actively using cultivation commands,
    because it progresses while the player is offline and requires no input.
    It can never auto-break through; callers cap awards at the current stage.
    """
    attrs = dict(character.get("attributes") or {})
    mode = str(mode).lower()
    if mode == "body":
        base = 7 + int(attrs.get("body", 0)) + int(attrs.get("will", 0)) // 3
        base += int(character.get("body_realm_index", 0)) // 2
    else:
        base = 8 + int(attrs.get("will", 0)) + int(attrs.get("insight", 0)) // 2
        base += int(character.get("realm_index", 0)) // 2
    # Around 60% of an active cultivation day's expected output.
    value = base * 0.60 * max(0.5, min(1.75, float(environment_mult)))
    value *= max(1.0, min(1.25, float(soul_cultivation_mult)))
    return max(1, int(round(value)))


def seclusion_environment_multiplier(*, abode_cultivation_level: int = 0, safe_zone: bool = False) -> float:
    if abode_cultivation_level > 0:
        return min(1.45, 1.05 + 0.05 * int(abode_cultivation_level))
    return 1.0 if safe_zone else 0.85
