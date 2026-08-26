from __future__ import annotations

from typing import Any

BLACK_MARKET_ROTATION_MINUTES = 3 * 24 * 60
BLACK_MARKET_OPEN_MINUTES = 2 * 24 * 60
BLACK_MARKET_MIN_ACCESS_KARMA = -40
BLACK_MARKET_ACCESS_REPUTATION = 15


def access_reason(*, karma: int, underworld_reputation: int, sect_alignment: str) -> str | None:
    if int(karma) <= BLACK_MARKET_MIN_ACCESS_KARMA:
        return "your karmic reputation is dark enough that underworld brokers recognize you"
    if int(underworld_reputation) >= BLACK_MARKET_ACCESS_REPUTATION:
        return "your Underworld Contacts reputation vouches for you"
    if str(sect_alignment).casefold() == "demonic":
        return "your demonic-sect affiliation grants you an introduction"
    return None


def black_market_price(base_value: int, *, heat: int, stock: int) -> int:
    base = max(1, int(base_value))
    scarcity = 1.0 + max(0, 8 - int(stock)) * 0.05
    danger = 1.15 + max(0, min(100, int(heat))) / 250.0
    return max(1, int(round(base * scarcity * danger)))


def candidate_black_market_items(items: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for item_id, item in items.items():
        legal = str(item.get("legal_status", "")).casefold()
        interest = str(item.get("auction_interest", "")).casefold()
        item_type = str(item.get("type", "")).casefold()
        if legal in {"forbidden", "contraband", "restricted"}:
            out.append(item_id)
        elif item_type == "manual" and any(word in str(item.get("name", "")).casefold() for word in ("demon", "blood", "soul", "nether", "bone", "venom")):
            out.append(item_id)
        elif interest in {"special", "legendary"}:
            out.append(item_id)
    return sorted(set(out))
