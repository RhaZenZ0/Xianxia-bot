from __future__ import annotations

from typing import Any

MAX_MANOR_FACILITY_LEVEL = 5
SECT_MANOR_ESTABLISH_RANK_LEVEL = 70
SECT_MANOR_UPGRADE_RANK_LEVEL = 50

SECT_MANOR_ESTABLISHMENT_COST: dict[str, int] = {
    "spirit_iron": 30,
    "spirit_herb": 20,
    "beast_core": 10,
}

SECT_MANOR_FACILITIES: dict[str, dict[str, Any]] = {
    "qi_array": {
        "name": "Qi Gathering Array",
        "column": "qi_array_level",
        "description": "Improves normal Qi cultivation and closed-door seclusion at the manor's location.",
        "cost_per_target_level": {"spirit_herb": 8, "beast_core": 3, "spirit_iron": 2},
    },
    "alchemy_hall": {
        "name": "Alchemy Hall",
        "column": "alchemy_hall_level",
        "description": "Adds a flat bonus to Alchemy crafting checks at the manor's location.",
        "cost_per_target_level": {"spirit_herb": 10, "beast_core": 2, "spirit_iron": 3},
    },
    "forge_pavilion": {
        "name": "Forge Pavilion",
        "column": "forge_pavilion_level",
        "description": "Adds a flat bonus to Forging crafting checks at the manor's location.",
        "cost_per_target_level": {"spirit_iron": 10, "beast_core": 2, "spirit_herb": 2},
    },
    "defense_array": {
        "name": "Defensive Grand Array",
        "column": "defense_array_level",
        "description": "Adds defensive power to sect members fortifying or repelling attacks on the manor territory.",
        "cost_per_target_level": {"spirit_iron": 8, "beast_core": 4, "spirit_herb": 2},
    },
}


def manor_upgrade_cost(facility: str, current_level: int) -> dict[str, int]:
    definition = SECT_MANOR_FACILITIES.get(str(facility))
    if definition is None:
        raise ValueError("Unknown sect-manor facility.")
    current_level = max(0, int(current_level))
    if current_level >= MAX_MANOR_FACILITY_LEVEL:
        raise ValueError("That sect-manor facility is already at the maximum level.")
    target_level = current_level + 1
    return {
        item_id: int(base) * target_level
        for item_id, base in dict(definition["cost_per_target_level"]).items()
    }


def manor_qi_multiplier(manor: dict[str, Any] | None) -> float:
    level = int((manor or {}).get("qi_array_level", 0))
    return 1.0 + 0.05 * max(0, min(MAX_MANOR_FACILITY_LEVEL, level))


def manor_seclusion_multiplier(manor: dict[str, Any] | None) -> float:
    level = int((manor or {}).get("qi_array_level", 0))
    return 1.0 + 0.08 * max(0, min(MAX_MANOR_FACILITY_LEVEL, level))


def manor_craft_bonus(manor: dict[str, Any] | None, profession: str) -> int:
    profession_key = str(profession).strip().casefold()
    if profession_key == "alchemy":
        level = int((manor or {}).get("alchemy_hall_level", 0))
    elif profession_key == "forging":
        level = int((manor or {}).get("forge_pavilion_level", 0))
    elif profession_key == "formation":
        # The manor's grand defensive array doubles as an inscription workshop
        # for formation masters, connecting shared sect construction to the
        # Formation profession without adding a redundant fifth facility.
        level = int((manor or {}).get("defense_array_level", 0))
    else:
        return 0
    return 2 * max(0, min(MAX_MANOR_FACILITY_LEVEL, level))


def manor_defense_power_bonus(manor: dict[str, Any] | None) -> int:
    level = int((manor or {}).get("defense_array_level", 0))
    return 6 * max(0, min(MAX_MANOR_FACILITY_LEVEL, level))


def manor_benefit_lines(manor: dict[str, Any]) -> list[str]:
    qi_level = int(manor.get("qi_array_level", 0))
    alchemy_level = int(manor.get("alchemy_hall_level", 0))
    forge_level = int(manor.get("forge_pavilion_level", 0))
    defense_level = int(manor.get("defense_array_level", 0))
    return [
        f"Qi Gathering Array Lv.{qi_level}: +{qi_level * 5}% normal Qi cultivation; +{qi_level * 8}% seclusion efficiency at the manor location.",
        f"Alchemy Hall Lv.{alchemy_level}: +{alchemy_level * 2} to Alchemy crafting checks at the manor location.",
        f"Forge Pavilion Lv.{forge_level}: +{forge_level * 2} to Forging crafting checks at the manor location.",
        f"Defensive Grand Array Lv.{defense_level}: +{defense_level * 6} war power when defending the manor territory with Fortify/Repel; +{defense_level * 2} to Formation crafting at the manor.",
    ]
