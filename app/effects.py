from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class EffectModifier:
    stat: str
    operation: str
    value: float


@dataclass(frozen=True)
class EffectView:
    effect_key: str
    name: str
    source_type: str
    source_id: str
    modifiers: tuple[EffectModifier, ...]
    tags: tuple[str, ...]
    stacks: int = 1
    starts_game_minute: int = 0
    ends_game_minute: int | None = None

    @property
    def permanent(self) -> bool:
        return self.ends_game_minute is None


def normalize_effect_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize an effect definition into a stable serializable shape."""
    modifiers: list[dict[str, Any]] = []
    for raw in payload.get("modifiers", []):
        if not isinstance(raw, dict) or not raw.get("stat"):
            continue
        operation = str(raw.get("operation", "add")).lower()
        if operation not in {"add", "mul", "set", "flag"}:
            operation = "add"
        modifiers.append(
            {
                "stat": str(raw["stat"]),
                "operation": operation,
                "value": float(raw.get("value", 0)),
            }
        )
    return {
        "name": str(payload.get("name", payload.get("effect_key", "Effect"))),
        "description": str(payload.get("description", "")),
        "category": str(payload.get("category", "General")),
        "severity": max(0, int(payload.get("severity", 0))),
        "special": bool(payload.get("special", False)),
        "resistance_stat": str(payload.get("resistance_stat", "")),
        "modifiers": modifiers,
        "tags": [str(tag) for tag in payload.get("tags", []) if str(tag).strip()],
        "stacking": str(payload.get("stacking", "replace")),
        "max_stacks": max(1, int(payload.get("max_stacks", 1))),
    }


def aggregate_modifiers(effects: Iterable[dict[str, Any]]) -> dict[str, float]:
    """Aggregate numeric effect modifiers into resolved stat deltas/multipliers.

    Keys ending in ``_mult`` contain multiplicative factors; regular keys contain
    additive totals. ``set`` and ``flag`` are exposed as ``set:<stat>`` and
    ``flag:<stat>`` so callers can apply them deliberately.
    """
    out: dict[str, float] = {}
    for effect in effects:
        stacks = max(1, int(effect.get("stacks", 1)))
        for mod in effect.get("modifiers", []):
            stat = str(mod.get("stat", "")).strip()
            if not stat:
                continue
            op = str(mod.get("operation", "add")).lower()
            value = float(mod.get("value", 0))
            if op == "add":
                out[stat] = out.get(stat, 0.0) + value * stacks
            elif op == "mul":
                key = f"{stat}_mult"
                out[key] = out.get(key, 1.0) * (value ** stacks)
            elif op == "set":
                out[f"set:{stat}"] = value
            elif op == "flag":
                out[f"flag:{stat}"] = max(out.get(f"flag:{stat}", 0.0), value or 1.0)
    return out


def apply_numeric_modifier(base: float, modifiers: dict[str, float], stat: str) -> float:
    if f"set:{stat}" in modifiers:
        base = modifiers[f"set:{stat}"]
    base += modifiers.get(stat, 0.0)
    base *= modifiers.get(f"{stat}_mult", 1.0)
    return base


def medicine_toxicity_effect(value: int) -> dict[str, Any] | None:
    """Return a shared-effect payload for accumulated medicinal residue.

    Keeping this in the generic effects layer lets alchemy, future poison/
    detox systems, NPC medicines and scripted world events all use the same
    cultivation/alchemy penalties instead of hard-coding them in Discord UI.
    """
    value = max(0, min(100, int(value)))
    if value < 40:
        return None
    if value < 60:
        cultivation_mult = 0.95
        will_penalty = 0
    elif value < 80:
        cultivation_mult = 0.85
        will_penalty = -1
    else:
        cultivation_mult = 0.70
        will_penalty = -2
    modifiers: list[dict[str, Any]] = [
        {"stat": "cultivation_gain", "operation": "mul", "value": cultivation_mult},
        {"stat": "alchemy_bonus", "operation": "add", "value": -max(2, value // 10)},
    ]
    if will_penalty:
        modifiers.append({"stat": "will", "operation": "add", "value": will_penalty})
    return {
        "name": "Pill Toxicity",
        "description": "Accumulated medicinal residue makes further cultivation and medicine harder to assimilate.",
        "category": "Medicine",
        "severity": max(1, value // 20),
        "modifiers": modifiers,
        "tags": ["pill", "toxicity", "medicine"],
        "stacking": "replace",
        "max_stacks": 1,
    }
