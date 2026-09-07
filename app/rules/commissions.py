"""Commissions: who may be offered what, and how it reads (v0.22.0).

Pure. Everything here is a decision about *presentation and selection* made
from engine facts the caller has already read - it decides nothing the engine
owns. The four outcomes, the standing deltas, the deadline and the payout live
in `go_core/internal/game/commission_actions.go`; this module never computes
them, it only says which commission a giver would raise next and how to write
it down.

The selection ladder (docs/COMMISSIONS_DESIGN.md, "Selection at offer time")
runs before any model call, so a Steward who has nothing to give costs nothing
to ask.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

MINUTES_PER_DAY = 1440

# Standing is derived, never stored: trust + respect - grudge. The bands are
# presentation - the seed hands the model a word, not a number, so the Steward
# cannot narrate a statistic at the player.
STANDING_BANDS: tuple[tuple[int, str], ...] = (
    (-30, "hostile"),
    (-10, "cold"),
    (10, "neutral"),
    (35, "warm"),
    (10_000, "trusted"),
)

# How high a tier each band may be offered. A player who has never worked for
# this giver starts at tier 1; the ladder is the reward for finishing things.
TIER_CEILING: dict[str, int] = {
    "hostile": 0,
    "cold": 1,
    "neutral": 1,
    "warm": 2,
    "trusted": 3,
}

OUTCOMES = ("active", "completed", "failed", "abandoned")

# Some givers do not say what the work pays. `hidden` is a presentation rule
# and nothing more: the engine still locks exact terms at accept and pays
# exactly those, and the payout is stated in full the moment the commission
# completes. What the player is told before accepting is that the terms are
# undisclosed - never a number the giver made up, and never a number this code
# quietly rounds off. Taking undisclosed work is a gamble the player can see
# they are making.
#
# It cuts both ways on purpose. An old beggar who will not discuss payment can
# be worth far more than the errand looked; a self-declared hidden master who
# talks about mountains of spirit stones can be worth almost nothing. From the
# offer card the two are indistinguishable, which is the whole point - the only
# way to learn which is which is to work for them and remember.
REWARD_VISIBILITY = ("shown", "hidden")
UNDISCLOSED = "undisclosed — they will not say what it pays"

# A failed commission is one the Steward chased; he may raise it himself. An
# abandoned one the player already told him about, so he does not.
STEWARD_INITIATES = ("failed",)


def standing_value(relationship: dict[str, Any] | None) -> int:
    r = dict(relationship or {})
    return int(r.get("trust", 0) or 0) + int(r.get("respect", 0) or 0) - int(r.get("grudge", 0) or 0)


def standing_band(value: int) -> str:
    for ceiling, name in STANDING_BANDS:
        if value < ceiling:
            return name
    return STANDING_BANDS[-1][1]


def tier_ceiling(relationship: dict[str, Any] | None) -> int:
    return TIER_CEILING[standing_band(standing_value(relationship))]


def sect_allows(required: str, player_sect: str) -> bool:
    """A sect commission is sect business. No standing, realm or tier substitutes
    for membership - an outsider is not refused for being unworthy, they are
    refused for not being a disciple."""
    required = str(required or "").strip()
    if not required:
        return True
    return required.casefold() == str(player_sect or "").strip().casefold()


def realm_band_allows(band: str, realm_index: int) -> bool:
    """`realm_band` is an inclusive realm-index range, "0-2", or "" for any.

    A single number is that realm alone. Anything unparseable is treated as
    "any": a malformed band must not silently hide a GM's approved commission,
    and the tier ceiling is the real gate.
    """
    text = str(band or "").strip()
    if not text:
        return True
    try:
        if "-" in text:
            low, high = text.split("-", 1)
            return int(low) <= int(realm_index) <= int(high)
        return int(text) == int(realm_index)
    except (TypeError, ValueError):
        return True


def format_wait_days(minutes: int) -> str:
    """Game-time waits are read in world-days; hours only when it is under one."""
    minutes = max(0, int(minutes))
    if minutes == 0:
        return "no time at all"
    days, rest = divmod(minutes, MINUTES_PER_DAY)
    if days and not rest:
        return f"{days} world-day" if days == 1 else f"{days} world-days"
    if days:
        return f"{days}d {rest // 60}h"
    hours, rest = divmod(rest, 60)
    if hours:
        return f"{hours}h" if not rest else f"{hours}h {rest}m"
    return f"{rest}m"


def reward_visibility(definition: dict[str, Any] | None) -> str:
    value = str((definition or {}).get("reward_visibility") or "shown").strip().lower()
    return value if value in REWARD_VISIBILITY else "shown"


def rewards_are_hidden(definition: dict[str, Any] | None) -> bool:
    return reward_visibility(definition) == "hidden"


def format_rewards(rewards: dict[str, Any] | None, *, item_names: dict[str, str] | None = None,
                   hidden: bool = False) -> str:
    """The terms as the giver states them. Items are named, never keyed.

    `hidden` returns the undisclosed line instead. It takes the real rewards as
    an argument and throws them away rather than being handed nothing, so a
    caller cannot accidentally print them by forgetting to pass the flag - the
    flag is at the point where the string is made."""
    if hidden:
        return UNDISCLOSED
    parts: list[str] = []
    data = dict(rewards or {})
    if int(data.get("spirit_stones", 0) or 0):
        parts.append(f"{int(data['spirit_stones'])} spirit stones")
    if int(data.get("insight_xp", 0) or 0):
        parts.append(f"{int(data['insight_xp'])} insight")
    names = dict(item_names or {})
    for item_id, qty in dict(data.get("items") or {}).items():
        label = names.get(str(item_id), str(item_id).replace("_", " ").title())
        parts.append(f"{int(qty)}× {label}")
    return " · ".join(parts) or "nothing but the Pavilion's goodwill"


def variant_terms(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """Every set of terms a commission may be accepted on, index-aligned with
    `variant_index` on the accepted row. A commission with no variants has
    exactly one: the definition's own rewards and deadline."""
    variants = list(definition.get("variants") or [])
    if not variants:
        return [{
            "label": "standard",
            "rewards": dict(definition.get("rewards") or {}),
            "deadline_game_minutes": int(definition.get("deadline_game_minutes", 0) or 0),
        }]
    out: list[dict[str, Any]] = []
    fallback = int(definition.get("deadline_game_minutes", 0) or 0)
    for index, raw in enumerate(variants):
        entry = dict(raw or {})
        out.append({
            "label": str(entry.get("label") or f"terms {index + 1}"),
            "rewards": dict(entry.get("rewards") or {}),
            "deadline_game_minutes": int(entry.get("deadline_game_minutes", fallback) or 0),
        })
    return out


@dataclass(frozen=True)
class Offer:
    """What the giver would do next. `kind` decides which buttons the UI
    attaches and which block the voice call is handed - never the narration."""

    kind: str  # offer | progress | cooldown | nothing | unavailable
    giver: str = ""
    boast: str = ""
    definition: dict[str, Any] | None = None
    held: dict[str, Any] | None = None
    reason: str = ""
    cooldown_game_minutes: int = 0
    last_outcome: str = ""
    standing: str = "neutral"
    candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def steward_initiates(self) -> bool:
        return self.kind == "cooldown" and self.last_outcome in STEWARD_INITIATES


def _stable_pick(user_id: int, game_minute: int, count: int) -> int:
    """Deterministic, per-player, per-world-day. Two players asking on the same
    day are unlikely to be sent after the same crate, and one player asking
    twice in a day is told about the same one both times."""
    if count <= 1:
        return 0
    day = int(game_minute) // MINUTES_PER_DAY
    digest = hashlib.blake2b(f"{int(user_id)}:{day}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % count


def choose_offer(
    *,
    giver: str,
    relationship: dict[str, Any] | None,
    held_commission: dict[str, Any] | None,
    cooldown_game_minutes: int,
    pool: list[dict[str, Any]],
    taken_keys: set[str] | frozenset[str],
    realm_index: int,
    user_id: int,
    game_minute: int,
    player_sect: str = "",
) -> Offer:
    """The ladder, in order, on engine facts only. No model call happens for
    any branch but `offer`, and even that one only narrates it."""
    band = standing_band(standing_value(relationship))
    last_outcome = str((relationship or {}).get("last_commission_outcome") or "")
    if held_commission:
        return Offer(kind="progress", giver=giver, held=dict(held_commission), standing=band,
                     last_outcome=last_outcome)
    if int(cooldown_game_minutes) > 0:
        return Offer(kind="cooldown", giver=giver, cooldown_game_minutes=int(cooldown_game_minutes),
                     last_outcome=last_outcome, standing=band,
                     reason=f"cooldown after a {last_outcome or 'previous'} commission")
    ceiling = TIER_CEILING[band]
    if ceiling <= 0:
        return Offer(kind="unavailable", giver=giver, standing=band, last_outcome=last_outcome,
                     reason="standing too low")
    candidates = [
        dict(row) for row in pool
        if str(row.get("quest_key")) not in set(taken_keys)
        and int(row.get("tier", 1) or 1) <= ceiling
        and realm_band_allows(str(row.get("realm_band") or ""), realm_index)
        and sect_allows(str(row.get("requires_sect") or ""), player_sect)
    ]
    if not candidates:
        # Distinguish "you are not one of us" from "nothing today": a sect board
        # with work on it that this player cannot take should say so, or they
        # will keep asking.
        blocked_by_sect = any(
            not sect_allows(str(row.get("requires_sect") or ""), player_sect)
            for row in pool if str(row.get("quest_key")) not in set(taken_keys)
        )
        return Offer(kind="nothing", giver=giver, standing=band, last_outcome=last_outcome,
                     reason="sect members only" if blocked_by_sect else "nothing suitable in the pool")
    candidates.sort(key=lambda row: (int(row.get("tier", 1) or 1), str(row.get("quest_key"))))
    chosen = candidates[_stable_pick(user_id, game_minute, len(candidates))]
    return Offer(kind="offer", giver=giver, definition=chosen, standing=band,
                 last_outcome=last_outcome, candidates=candidates,
                 boast=str(chosen.get("boast") or ""))


def commission_context(offer: Offer, *, item_names: dict[str, str] | None = None) -> dict[str, Any]:
    """The block handed to the voice call. Canonical facts only, in the shape
    `Narrator.talk_to_npc` renders - the model is told what is true and asked
    to say it in character, never asked to decide any of it."""
    block: dict[str, Any] = {
        "kind": offer.kind,
        "giver": offer.giver,
        "standing_band": offer.standing,
        "last_outcome": offer.last_outcome or "none",
        "steward_initiates": offer.steward_initiates,
    }
    if offer.kind == "offer" and offer.definition:
        terms = variant_terms(offer.definition)
        hidden = rewards_are_hidden(offer.definition)
        block["commission"] = {
            "title": str(offer.definition.get("title", "")),
            "summary": str(offer.definition.get("description", "")),
            "objectives": [str(o.get("label") or "") for o in offer.definition.get("objectives") or []],
            "terms_offered": format_rewards(terms[0]["rewards"], item_names=item_names, hidden=hidden),
            "alternative_terms": [t["label"] for t in terms[1:]],
            "deadline": format_wait_days(int(terms[0]["deadline_game_minutes"])) if terms[0]["deadline_game_minutes"] else "no fixed deadline",
            "rewards_hidden": hidden,
        }
        if hidden and offer.boast:
            # Authored, not invented: whatever grand thing this giver says the
            # work is worth is content a GM wrote and the validator never saw a
            # number in. The model may repeat it and may not improve on it.
            block["commission"]["boast"] = offer.boast
    elif offer.kind == "progress" and offer.held:
        block["held"] = {
            "title": str(offer.held.get("title", "")),
            "objectives_done": int(offer.held.get("objectives_done", 0) or 0),
            "objectives_total": int(offer.held.get("objectives_total", 0) or 0),
            "deadline_in": format_wait_days(int(offer.held.get("deadline_game_minutes_remaining", 0) or 0)),
        }
    elif offer.kind == "cooldown":
        block["cooldown_in"] = format_wait_days(offer.cooldown_game_minutes)
    else:
        block["refusal"] = offer.reason
    return block
