from __future__ import annotations

import hashlib
from typing import Any


# Canonical item ids already present in content/world.json. Equipment instances
# consume one inventory item when first bound, then persist independently with
# durability and a loadout slot.
EQUIPMENT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "spirit_iron_sword": {
        "name": "Spirit-Iron Sword", "slot": "weapon", "max_durability": 120,
        "attack": 4, "defense": 0, "spirit": 1, "agility": 0,
    },
    "spirit_iron_armor": {
        "name": "Spirit-Iron Lamellar", "slot": "armor", "max_durability": 160,
        "attack": 0, "defense": 5, "spirit": 1, "agility": -1,
    },
    "cloud_stepping_boots": {
        "name": "Cloud-Stepping Boots", "slot": "boots", "max_durability": 100,
        "attack": 0, "defense": 1, "spirit": 0, "agility": 4,
    },
    "lesser_stygian_seal": {
        "name": "Lesser Stygian Seal", "slot": "accessory", "max_durability": 90,
        "attack": 1, "defense": 1, "spirit": 4, "agility": 0,
    },
    "bone_comb": {
        "name": "The Bone Comb", "slot": "accessory", "max_durability": 80,
        "attack": 0, "defense": 0, "spirit": 5, "agility": 1,
    },
    "cracked_nether_mirror": {
        "name": "Cracked Nether Mirror", "slot": "accessory", "max_durability": 75,
        "attack": 0, "defense": 2, "spirit": 3, "agility": 0,
    },
    # A one-of-a-kind GM reward granted via /admin player grant, never crafted
    # or bought (content/world.json marks it market_excluded). "indestructible"
    # and "unique" are read by app/bot/main.py's grant/equipment-status code;
    # the Go engine enforces the durability side via its own Indestructible
    # field on equipmentDefinitionsGo, which must stay in sync with the four
    # combat stats below (also mirrored into combat_actions.go's equipDefs -
    # see tests/python/contracts/test_equipment_stat_parity.py).
    "bugslayer_sword": {
        "name": "Bugslayer Sword", "slot": "weapon", "max_durability": 100,
        "indestructible": True, "unique": True,
        "attack": 5, "defense": 1, "spirit": 1, "agility": 1,
        "passive_name": "Heavenly Flawfinder",
        "passive_description": (
            "A strong normal attack (Strong Success or better) exposes a flaw: "
            "+2 bonus damage and the opponent's immediate counter/next hit is disrupted."
        ),
    },
}

FORMATION_POSITIONS: dict[str, dict[str, int]] = {
    "vanguard": {"attack": 1, "defense": 4, "support": 0},
    "core": {"attack": 4, "defense": 1, "support": 0},
    "flank": {"attack": 3, "defense": 2, "support": 0},
    "support": {"attack": 0, "defense": 2, "support": 4},
}
FORMATION_STANCES: dict[str, dict[str, int]] = {
    "balanced": {"attack": 0, "defense": 0, "cohesion_cost": 0},
    "aggressive": {"attack": 3, "defense": -2, "cohesion_cost": 2},
    "defensive": {"attack": -1, "defense": 4, "cohesion_cost": 1},
}

BOSS_TEMPLATES: dict[str, dict[str, Any]] = {
    "iron_tusk_boar_king": {
        "name": "Iron-Tusk Boar King", "location": "Greenriver Town", "realm_index": 2,
        "max_hp": 180, "reward_currency": 120, "reward_item": "beast_core", "reward_quantity": 2,
        "phases": [
            {"name": "Mountain-Shaking Charge", "threshold": 0.66, "attack": 8, "defense": 3, "cohesion_damage": 4},
            {"name": "Blood Frenzy", "threshold": 0.33, "attack": 11, "defense": 2, "cohesion_damage": 7},
            {"name": "Last Roar", "threshold": 0.0, "attack": 14, "defense": 1, "cohesion_damage": 10},
        ],
    },
    "moonfen_drowned_serpent": {
        "name": "Moonfen Drowned Serpent", "location": "Moonfen Marsh", "realm_index": 4,
        "max_hp": 260, "reward_currency": 220, "reward_item": "beast_core", "reward_quantity": 3,
        "phases": [
            {"name": "Drowning Mist", "threshold": 0.70, "attack": 10, "defense": 4, "cohesion_damage": 5},
            {"name": "Venom Tide", "threshold": 0.35, "attack": 14, "defense": 3, "cohesion_damage": 8},
            {"name": "Blackwater Coil", "threshold": 0.0, "attack": 18, "defense": 2, "cohesion_damage": 12},
        ],
    },
    "nine_echo_sword_wraith": {
        "name": "Nine-Echo Sword Wraith", "location": "Sword Grave of Nine Echoes", "realm_index": 7,
        "max_hp": 420, "reward_currency": 420, "reward_item": "nine_echo_sword_tablet", "reward_quantity": 1,
        "phases": [
            {"name": "First Three Echoes", "threshold": 0.70, "attack": 14, "defense": 7, "cohesion_damage": 6},
            {"name": "Sixfold Sword Domain", "threshold": 0.35, "attack": 19, "defense": 6, "cohesion_damage": 10},
            {"name": "Ninth Echo: Severing", "threshold": 0.0, "attack": 25, "defense": 4, "cohesion_damage": 15},
        ],
    },
}

ERA_CYCLE: tuple[dict[str, Any], ...] = (
    {
        "name": "Jade Meridian Awakening Era",
        "description": "Spiritual veins awaken and new inheritances surface across the four worlds.",
        "duration_days": 180,
        "modifiers": {"cultivation_gain": 1.05, "secret_realm_frequency": 1.10},
    },
    {
        "name": "Hundred Sects Strife Era",
        "description": "Competition over spirit veins hardens into open territorial conflict.",
        "duration_days": 120,
        "modifiers": {"war_pressure": 1.25, "market_volatility": 1.10},
    },
    {
        "name": "Beast Tide Era",
        "description": "Ancient bloodlines stir and spirit beasts migrate in destructive tides.",
        "duration_days": 90,
        "modifiers": {"beast_encounter_rate": 1.35, "caravan_risk": 1.15},
    },
    {
        "name": "Quiet Heaven Era",
        "description": "After upheaval, the heavens settle and orthodox institutions rebuild order.",
        "duration_days": 150,
        "modifiers": {"recovery_rate": 1.10, "crime_pressure": 0.85},
    },
)

BOUNTY_HUNTER_TITLES = (
    "Iron Badge Constable", "Black-Cloak Pursuer", "Seven Provinces Tracker",
    "Spirit-Hound Warden", "Heavenly Warrant Enforcer", "Jade Tribunal Hunter",
)


def stable_percent(*parts: object) -> int:
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big") % 100


def boss_phase(template: dict[str, Any], current_hp: int) -> tuple[int, dict[str, Any]]:
    maximum = max(1, int(template["max_hp"]))
    ratio = max(0.0, min(1.0, int(current_hp) / maximum))
    phases = list(template["phases"])
    for idx, phase in enumerate(phases):
        if ratio > float(phase["threshold"]):
            return idx, phase
    return len(phases) - 1, phases[-1]


def equipment_definition(item_id: str) -> dict[str, Any] | None:
    data = EQUIPMENT_DEFINITIONS.get(str(item_id))
    return dict(data) if data else None


def equipment_power(rows: list[dict[str, Any]]) -> dict[str, int]:
    total = {"attack": 0, "defense": 0, "spirit": 0, "agility": 0}
    for row in rows:
        if not row.get("equipped") or int(row.get("durability", 0)) <= 0:
            continue
        definition = EQUIPMENT_DEFINITIONS.get(str(row.get("item_id")), {})
        condition = max(0.25, min(1.0, int(row.get("durability", 0)) / max(1, int(row.get("max_durability", 1)))))
        quality = max(0.5, 1.0 + (int(row.get("quality", 100)) - 100) / 200)
        for key in total:
            total[key] += int(round(int(definition.get(key, 0)) * condition * quality))
    return total


def formation_bonus(position: str | None, stance: str = "balanced", cohesion: int = 100) -> dict[str, int]:
    base = FORMATION_POSITIONS.get(str(position or "").lower(), {"attack": 0, "defense": 0, "support": 0})
    stance_mod = FORMATION_STANCES.get(str(stance).lower(), FORMATION_STANCES["balanced"])
    scale = max(0.25, min(1.0, int(cohesion) / 100))
    return {
        "attack": int(round((base["attack"] + stance_mod["attack"]) * scale)),
        "defense": int(round((base["defense"] + stance_mod["defense"]) * scale)),
        "support": int(round(base["support"] * scale)),
        "cohesion_cost": int(stance_mod["cohesion_cost"]),
    }


def era_index(name: str) -> int:
    for idx, era in enumerate(ERA_CYCLE):
        if era["name"] == name:
            return idx
    return 0
