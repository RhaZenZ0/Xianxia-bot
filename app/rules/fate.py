from __future__ import annotations

FATE_MIN = 0
FATE_MAX = 9


def clamp_fate(value: int) -> int:
    return max(FATE_MIN, min(FATE_MAX, int(value)))


def fate_label(points: int) -> str:
    value = clamp_fate(points)
    if value <= 0:
        return "Ordinary Destiny"
    if value <= 2:
        return "Faint Fortune"
    if value <= 4:
        return "Favored by Chance"
    if value <= 6:
        return "Fortune-Gathering"
    if value <= 8:
        return "Heaven-Favored"
    return "Chosen by Fate"
