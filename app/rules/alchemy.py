from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .effects import medicine_toxicity_effect


@dataclass(frozen=True)
class AlchemyQuality:
    key: str
    label: str
    output_multiplier: int
    toxicity_multiplier: float
    xp_bonus: int


ALCHEMY_QUALITIES: tuple[AlchemyQuality, ...] = (
    AlchemyQuality("crude", "Crude", 1, 1.20, 0),
    AlchemyQuality("ordinary", "Ordinary", 1, 1.00, 0),
    AlchemyQuality("refined", "Refined", 1, 0.85, 3),
    AlchemyQuality("superior", "Superior", 2, 0.70, 6),
    AlchemyQuality("flawless", "Flawless", 3, 0.55, 10),
)

PILL_TOXICITY_DECAY_MINUTES = 12 * 60
PILL_TOXICITY_DECAY_AMOUNT = 1


def alchemy_quality(margin: int, *, success: bool) -> AlchemyQuality:
    """Resolve an alchemy batch quality from the crafting margin.

    Failed refinements are recorded as crude failures but produce no output.
    Successful batches improve at margins 2, 5 and 9, rewarding genuine
    mastery without requiring per-item quality instances in the inventory.
    """
    if not success:
        return ALCHEMY_QUALITIES[0]
    if margin >= 9:
        return ALCHEMY_QUALITIES[4]
    if margin >= 5:
        return ALCHEMY_QUALITIES[3]
    if margin >= 2:
        return ALCHEMY_QUALITIES[2]
    return ALCHEMY_QUALITIES[1]


def alchemy_output(base_output: dict[str, int], quality: AlchemyQuality) -> dict[str, int]:
    multiplier = max(1, int(quality.output_multiplier))
    return {str(item_id): max(0, int(quantity)) * multiplier for item_id, quantity in base_output.items()}


def is_pill(item_id: str, item: dict[str, Any]) -> bool:
    tags: list[str] = []
    use = item.get("use") if isinstance(item, dict) else None
    if isinstance(use, dict):
        effect = use.get("effect")
        if isinstance(effect, dict):
            tags.extend(str(tag).casefold() for tag in effect.get("tags", []))
    name = str(item.get("name", item_id)).casefold() if isinstance(item, dict) else str(item_id).casefold()
    return "pill" in tags or "pill" in name or str(item_id).casefold().endswith("_pill")


def pill_toxicity_value(item_id: str, item: dict[str, Any]) -> int:
    """Return medicinal saturation added by consuming a pill.

    Strong permanent/special medicines are intentionally more taxing than
    common recovery pills. Content can override the value with ``pill_toxicity``.
    """
    if not is_pill(item_id, item):
        return 0
    if "pill_toxicity" in item:
        return max(0, int(item.get("pill_toxicity", 0)))
    use = item.get("use", {}) if isinstance(item, dict) else {}
    if int(use.get("lifespan_years", 0) or 0) > 0:
        return 24
    effect = use.get("effect", {}) if isinstance(use, dict) else {}
    tags = {str(tag).casefold() for tag in effect.get("tags", [])} if isinstance(effect, dict) else set()
    if "risky" in tags:
        return 18
    if "cultivation" in tags:
        return 12
    if "mental" in tags:
        return 10
    if use.get("instant"):
        return 7
    return 8


def toxicity_band(value: int) -> tuple[str, str]:
    value = max(0, min(100, int(value)))
    if value < 20:
        return "Clear Meridians", "No meaningful medicinal saturation."
    if value < 40:
        return "Medicine Residue", "Further pills remain effective, but residue is accumulating."
    if value < 60:
        return "Pill Saturation", "Your meridians are becoming saturated with medicinal residue."
    if value < 80:
        return "Heavy Pill Toxicity", "Repeated pill use is straining circulation and cultivation efficiency."
    return "Severe Pill Toxicity", "Your body is overloaded with conflicting medicinal energies."


def alchemy_purge_refusal(engine_error: str) -> str:
    """Turn an `alchemy.purge` refusal into something a cultivator would hear.

    The engine states the reason in its own terms - it is the authority on
    whether a purge may happen, and it says so once. This is the only place
    that translation lives, so the command does not re-derive the reason from
    state it would have to read a second time (and could read differently).
    Anything unrecognised is passed through: an unfamiliar refusal should
    reach the player as words, not as silence.
    """
    reason = " ".join(str(engine_error or "").split())
    lowered = reason.casefold()
    if "no pill toxicity" in lowered:
        return "Your meridians contain no pill toxicity to purge."
    if "insufficient qi" in lowered:
        required = ""
        for token in reason.replace(":", " ").split():
            if token.isdigit():
                required = token
                break
        if required:
            return f"You need **{required} Qi** for a controlled medicinal purge."
        return "You lack the Qi for a controlled medicinal purge."
    if "cooldown active" in lowered:
        return "Your meridians need time before another purge cycle."
    if "living character not found" in lowered:
        return "You have no living character to purge."
    return f"The purge could not resolve: {reason}"


def forage_bonus_from_resources(spirit_resources: int) -> int:
    resources = max(0, min(100, int(spirit_resources)))
    if resources >= 85:
        return 4
    if resources >= 70:
        return 3
    if resources >= 55:
        return 2
    if resources >= 35:
        return 1
    if resources <= 15:
        return -2
    if resources <= 25:
        return -1
    return 0
