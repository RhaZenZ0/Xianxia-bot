from __future__ import annotations

from typing import Any

ARRAY_DEPLOYMENTS: dict[str, dict[str, Any]] = {
    "minor_qi_gathering_array_disk": {
        "name": "Minor Qi Gathering Array",
        "duration_game_minutes": 360,
        "effect": {
            "description": "Formation flags draw ambient qi toward everyone cultivating at this location.",
            "modifiers": [
                {"stat": "cultivation_gain", "operation": "mul", "value": 1.10},
                {"stat": "spirit", "operation": "add", "value": 1},
            ],
            "tags": ["formation", "location", "qi"],
        },
    },
    "minor_warding_array_disk": {
        "name": "Minor Warding Array",
        "duration_game_minutes": 360,
        "effect": {
            "description": "A compact defensive formation steadies cultivators and reinforces combat exchanges in this location.",
            "modifiers": [
                {"stat": "will", "operation": "add", "value": 1},
                {"stat": "combat_bonus", "operation": "add", "value": 1},
            ],
            "tags": ["formation", "location", "defense"],
        },
    },
}


