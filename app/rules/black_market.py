from __future__ import annotations


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


