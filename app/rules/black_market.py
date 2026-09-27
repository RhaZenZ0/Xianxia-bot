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




def underworld_trust_line(result: dict) -> str:
    """What a fence said about the brokers' trust (v1.11.1), read off the
    engine's reply: the standing it left and the standing at which a broker
    sells. Nothing when the seller was already trusted for another reason."""
    result = dict(result or {})
    if str(result.get("access") or "") != "fencing as a stranger":
        return ""
    rep = int(result.get("underworld_reputation") or 0)
    trust = int(result.get("trust_reputation") or BLACK_MARKET_ACCESS_REPUTATION)
    if rep >= trust:
        return f"\n🌑 Underworld Contacts **{rep:+d}** — the brokers know you now and will sell to you."
    return f"\n🌑 Underworld Contacts **{rep:+d}** of **{trust}** — {trust - rep} more before a broker sells to you."
