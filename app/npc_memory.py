from __future__ import annotations

import re
from typing import Any, Iterable


_IMPORTANT_PATTERNS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("vow", ("i swear", "i vow", "promise", "oath", "my word"), 32),
    ("threat", ("i will kill", "i'll kill", "destroy you", "you will regret", "threat"), 30),
    ("debt", ("i owe you", "you owe me", "repay", "debt", "favor"), 26),
    ("secret", ("secret", "don't tell", "do not tell", "confidential", "between us"), 24),
    ("alliance", ("alliance", "ally", "join forces", "work together", "cooperate"), 22),
    ("mentorship", ("teach me", "disciple", "master", "shifu", "inheritance"), 20),
    ("betrayal", ("betray", "betrayed", "traitor", "deceived", "lied to me"), 28),
    ("rescue", ("saved", "rescued", "spared", "protected"), 24),
)


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def classify_memory(player_text: str, npc_reply: str = "") -> tuple[str, int]:
    """Classify a real exchange without asking the narrator to invent a summary.

    The memory is deliberately based on words that actually occurred.  It is a
    continuity aid, not a second authority over game mechanics.
    """
    combined = f"{player_text}\n{npc_reply}".casefold()
    best_kind = "conversation"
    bonus = 0
    for kind, phrases, points in _IMPORTANT_PATTERNS:
        if any(phrase in combined for phrase in phrases) and points > bonus:
            best_kind = kind
            bonus = points

    length_bonus = min(16, max(0, len(str(player_text)) - 80) // 35)
    question_bonus = 5 if "?" in str(player_text) else 0
    salience = min(100, 24 + bonus + length_bonus + question_bonus)
    return best_kind, salience


def exchange_memory_summary(player_text: str, npc_name: str, npc_reply: str) -> str:
    return (
        f"Player said: {_clip(player_text, 230)} | "
        f"{_clip(npc_name, 80)} replied: {_clip(npc_reply, 360)}"
    )


def format_memories(rows: Iterable[dict[str, Any]], *, limit: int = 6) -> str:
    values = list(rows)[: max(1, int(limit))]
    if not values:
        return "No salient long-term memories with this player yet."
    lines = []
    for row in values:
        kind = str(row.get("memory_kind") or "conversation").replace("_", " ")
        salience = int(row.get("salience") or 0)
        summary = _clip(row.get("summary"), 520)
        lines.append(f"- [{kind}; salience {salience}/100] {summary}")
    return "\n".join(lines)


def public_mood_hint(mood: str) -> str:
    """Return only moods safe to describe as an outward demeanor."""
    allowed = {
        "calm", "focused", "guarded", "weary", "restless", "curious",
        "irritated", "satisfied", "concerned", "competitive", "patient",
        "watchful", "distracted", "businesslike",
    }
    value = str(mood or "").strip().casefold()
    return value if value in allowed else ""


def scene_memory_summary(player_text: str, npc_name: str, narration: str) -> str:
    return (
        f"Player action/dialogue involving {_clip(npc_name, 80)}: {_clip(player_text, 250)} | "
        f"Observed scene response: {_clip(narration, 360)}"
    )
