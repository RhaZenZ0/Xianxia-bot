from __future__ import annotations


def vitality_percentage(current: int, maximum: int) -> int:
    cap = max(1, int(maximum))
    return max(0, min(100, round(max(0, int(current)) * 100 / cap)))


def vitality_band(current: int, maximum: int) -> tuple[str, int]:
    """Return the Discord bar color emoji and matching embed color."""
    percentage = vitality_percentage(current, maximum)
    if percentage > 60:
        return "🟩", 0x2ECC71
    if percentage > 30:
        return "🟨", 0xF1C40F
    return "🟥", 0xE74C3C


def vitality_bar(current: int, maximum: int, *, width: int = 10) -> str:
    cap = max(1, int(maximum))
    value = max(0, min(cap, int(current)))
    percentage = vitality_percentage(value, cap)
    color, _ = vitality_band(value, cap)
    cells = max(1, min(12, int(width)))
    filled = min(cells, max(0, round(percentage * cells / 100)))
    return f"{color * filled}{'⬛' * (cells - filled)} **{value}/{cap}** • **{percentage}%**"


def matchup_label(player_realm: int, player_stage: int, npc_realm: int, npc_stage: int) -> str:
    player_power = max(0, int(player_realm)) * 9 + max(1, int(player_stage))
    npc_power = max(0, int(npc_realm)) * 9 + max(1, int(npc_stage))
    gap = player_power - npc_power
    if gap >= 18:
        return "🟢 Overwhelming advantage"
    if gap >= 5:
        return "🟩 Advantage"
    if gap > -5:
        return "⚖️ Evenly matched"
    if gap > -18:
        return "🟧 Disadvantage"
    return "🔴 Lethal suppression"


def suppression_label(turns: int) -> str:
    remaining = max(0, int(turns))
    if remaining <= 0:
        return "None"
    suffix = "counter" if remaining == 1 else "counters"
    return f"🌌 Spatially suppressed • {remaining} {suffix} blocked"
