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


