from __future__ import annotations

from dataclasses import dataclass



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


