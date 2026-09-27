from __future__ import annotations

import math
from typing import Any, Mapping

from app.rules.item_grades import grade_rung, split_item_grade


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
    # The higher worlds' arms and armour (v0.39.0), forged there and sold there.
    "spirit_crystal_sword": {
        "name": "Spirit-Crystal Sword", "slot": "weapon", "max_durability": 200,
        "attack": 6, "defense": 0, "spirit": 2, "agility": 0,
    },
    "spirit_crystal_mail": {
        "name": "Spirit-Crystal Mail", "slot": "armor", "max_durability": 240,
        "attack": 0, "defense": 8, "spirit": 2, "agility": -1,
    },
    "immortal_gold_sabre": {
        "name": "Immortal-Gold Sabre", "slot": "weapon", "max_durability": 320,
        "attack": 9, "defense": 1, "spirit": 3, "agility": 0,
    },
    "immortal_gold_plate": {
        "name": "Immortal-Gold Plate", "slot": "armor", "max_durability": 380,
        "attack": 1, "defense": 12, "spirit": 3, "agility": -1,
    },
    "starsteel_glaive": {
        "name": "Starsteel Glaive", "slot": "weapon", "max_durability": 480,
        "attack": 13, "defense": 1, "spirit": 4, "agility": 1,
    },
    "starsteel_aegis": {
        "name": "Starsteel Aegis", "slot": "armor", "max_durability": 560,
        "attack": 1, "defense": 17, "spirit": 4, "agility": 0,
    },
    # The flying sword (v1.0.0-rc.15) is the one artifact that both carries a
    # rider and takes the weapon slot - which is why the genre makes it the
    # default way to travel: it is still a sword when you arrive.
    "azure_flying_sword": {
        "name": "Azure Flying Sword", "slot": "weapon", "max_durability": 220,
        "attack": 7, "defense": 0, "spirit": 3, "agility": 3,
    },
    # The birth family's send-off (v1.0.0-rc.15): a household that can afford
    # to does not send a child out to walk. Deliberately under the shop ladder
    # - the cracked heirloom and the two training blades below
    # spirit_iron_sword, the clan sword under spirit_crystal_mail's tier -
    # because the gift is the flight, not the edge.
    "cracked_ancestral_blade": {
        "name": "Cracked Ancestral Blade", "slot": "weapon", "max_durability": 90,
        "attack": 2, "defense": 0, "spirit": 1, "agility": 1,
    },
    "hall_practice_sword": {
        "name": "Hall Practice Sword", "slot": "weapon", "max_durability": 110,
        "attack": 3, "defense": 0, "spirit": 1, "agility": 1,
    },
    "forge_proof_sword": {
        "name": "Forge-Proof Sword", "slot": "weapon", "max_durability": 130,
        "attack": 3, "defense": 1, "spirit": 1, "agility": 0,
    },
    "qin_ancestral_sword": {
        "name": "Qin Ancestral Sword", "slot": "weapon", "max_durability": 240,
        "attack": 5, "defense": 0, "spirit": 2, "agility": 2,
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

# What a piece of gear gives (v1.7.5), the presentation half. The engine owns
# every number below: `equipmentQualityMult` and `gradeEquipmentQuality` in
# item_grade.go, the per-stat rounding in `combatEquipment` (1v1, which also
# scales by condition) and `equipmentPowerRows` (raids, quality only), and
# `combatCompanionBonus` for a beast. These are display twins, held to the Go
# source by tests/python/unit/test_gear_says_what_it_gives.py. `spirit` is
# carried by every definition and read by no rule, so nothing here shows it.


def _go_round(value: float) -> int:
    """Go's math.Round: half away from zero (Python's round is half-even)."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def equipment_quality_mult(quality: Any) -> float:
    """`equipmentQualityMult`: quality 100 is x1, never below x0.5."""
    return max(0.5, 1 + (float(quality or 0) - 100) / 200)


def grade_equipment_quality(mult: Any) -> int:
    """`gradeEquipmentQuality`: the quality a grade binds a piece of gear at."""
    return _go_round(100 + 200 * (float(mult) - 1))


def equipment_definition(item_id: Any) -> dict[str, Any]:
    """The gear definition behind a carried id, graded or not; ``{}`` for
    anything that is not gear. A graded id (``spirit_iron_sword@high``) is not
    a key of EQUIPMENT_DEFINITIONS, so a bare ``.get`` answers nothing for it."""
    base, _ = split_item_grade(str(item_id or ""))
    return dict(EQUIPMENT_DEFINITIONS.get(base) or {})


def equipment_effective(
    item_id: Any,
    *,
    quality: Any = None,
    durability: Any = None,
    max_durability: Any = None,
    ladder: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Attack, defence and agility as the engine would count them.

    An equipped instance passes its own ``quality`` (and its durability, for
    the 1v1 condition scale); an item on a shelf or in a bag passes nothing,
    and its quality is the one its grade binds at.
    """
    definition = equipment_definition(item_id)
    if not definition:
        return {}
    if quality is None:
        _, entry = grade_rung(ladder, split_item_grade(str(item_id))[1])
        quality = grade_equipment_quality(entry.get("effect_mult") or 1.0)
    scale = equipment_quality_mult(quality)
    if durability is not None:
        top = max(1, int(max_durability or definition.get("max_durability") or 1))
        scale *= min(1.0, max(0.25, int(durability) / top))
    return {stat: _go_round(int(definition.get(stat) or 0) * scale) for stat in ("attack", "defense", "agility")}


def describe_equipment(
    item_id: Any,
    *,
    quality: Any = None,
    durability: Any = None,
    max_durability: Any = None,
    ladder: Mapping[str, Any] | None = None,
) -> str:
    """One line saying what a piece of gear gives, or ``""`` for anything else.

    Agility is the only stat that is a real percentage anywhere: each point is
    one point of boss-raid hit chance. The rest are modifiers on a 2d10 roll,
    shown as numbers rather than invented odds.
    """
    definition = equipment_definition(item_id)
    if not definition:
        return ""
    stats = equipment_effective(item_id, quality=quality, durability=durability, max_durability=max_durability, ladder=ladder)
    parts: list[str] = []
    if stats["attack"]:
        parts.append(f"⚔️ {stats['attack']:+d} attack")
    if stats["defense"]:
        parts.append(f"🛡️ {stats['defense']:+d} defence")
    if stats["agility"]:
        parts.append(f"💨 {stats['agility']:+d} agility ({stats['agility']:+d}% raid hit)")
    if definition.get("indestructible"):
        parts.append("indestructible")
    elif durability is not None:
        parts.append(f"durability {int(durability)}/{int(max_durability or definition.get('max_durability') or 0)}")
    else:
        parts.append(f"durability {int(definition.get('max_durability') or 0)}")
    if definition.get("passive_name"):
        parts.append(f"✨ {definition['passive_name']}")
    return " · ".join(parts)


def equipment_passive_line(item_id: Any) -> str:
    """What a piece of gear's passive does, or ``""`` (v1.9.1).

    `describe_equipment` named the passive and never said what it did, so a
    player holding the Bugslayer Sword asked how to see what Heavenly
    Flawfinder is. The words are the definition's own `passive_description`.
    """
    definition = equipment_definition(item_id)
    name = str(definition.get("passive_name") or "")
    if not name:
        return ""
    what = str(definition.get("passive_description") or "").strip()
    return f"✨ **{name}** — {what}" if what else f"✨ **{name}**"


def describe_equipment_in_full(item_id: Any, *, description: str = "", **stats: Any) -> str:
    """Every line a player needs about one piece of gear (v1.9.1): what it
    gives, the item's own description, and its passive. `description` is the
    catalogue's text, handed in because the item table lives in the bot's
    world, not in this module."""
    lines = [describe_equipment(item_id, **stats) or "No stat modifiers"]
    if description.strip():
        lines.append(f"*{description.strip()}*")
    passive = equipment_passive_line(item_id)
    if passive:
        lines.append(passive)
    return "\n".join(lines)


def equipment_totals_line(totals: Mapping[str, int]) -> str:
    """What the summed gear does in a fight, stated once for the status card.

    1v1: attack rides the attack roll and adds a third of itself to damage on
    a hit; defence raises what an opponent's counter must beat; agility rides
    the flee roll. A raid: attack adds to damage, half of defence comes off a
    boss's blow, and each point of agility is one point of hit chance.
    """
    attack = int(totals.get("attack") or 0)
    defense = int(totals.get("defense") or 0)
    agility = int(totals.get("agility") or 0)
    lines = []
    if attack:
        lines.append(f"⚔️ {attack:+d} to your attack roll, {max(0, attack) // 3:+d} damage on a hit")
    if defense:
        lines.append(f"🛡️ opponents need {defense:+d} more to hit you back · half of it comes off a boss's blow")
    if agility:
        lines.append(f"💨 {agility:+d} to flee · {agility:+d}% boss-raid hit chance")
    return "\n".join(lines)


def beast_milestone_bonus(rank: Any) -> int:
    """`beastMilestoneBonus`: +2 for every tenth rank a beast has reached."""
    rank = int(rank or 0)
    return 0 if rank < 10 else 2 * (rank // 10)


def companion_bonus(rank: Any, evolution_stage: Any, loyalty: Any) -> int:
    """`combatCompanionBonus` for one beast: rank/2 + stage + loyalty/40, in
    integer division, plus its milestones. Only the active beast counts, and
    only in 1v1 combat."""
    return int(rank or 0) // 2 + int(evolution_stage or 0) + int(loyalty or 0) // 40 + beast_milestone_bonus(rank)


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

# `ERA_CYCLE` stood here until v1.0.7: a third copy of the era roster, beside
# the Go literal in `advanced_maintenance.go` and the rows in `world_eras`,
# carrying the numbers of a cycle that ran 540 world days for the whole game.
# The roster is `world_era_cycles` in content/world.json now - one cycle per
# world - and `describe_era` resolves a row against it rather than against a
# list kept here, which is the fault rc.39 removed for the world clock and
# rc.44 for the world currencies.

BOUNTY_HUNTER_TITLES = (
    "Iron Badge Constable", "Black-Cloak Pursuer", "Seven Provinces Tracker",
    "Spirit-Hound Warden", "Heavenly Warrant Enforcer", "Jade Tribunal Hunter",
)


def boss_lair(template: dict[str, Any], secret_realms: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """Where a raid is fought, and the realm whose floor it is (v1.3.0).

    The twin of the engine's `bossLair`: a template whose location names a
    secret realm is fought at that realm's entrance, once the leader has
    walked the realm to its end. Returns ``(location, realm_id)``; the
    realm id is empty for an ordinary lair.
    """
    name = str(template.get("location") or "")
    for realm_id in sorted(secret_realms):
        realm = secret_realms[realm_id]
        if str(realm.get("name") or "") == name:
            return str(realm.get("location") or ""), str(realm_id)
    return name, ""


def boss_encounter_phase(encounter: dict[str, Any]) -> dict[str, Any]:
    """The phase an engine-owned boss encounter is in, for display.

    The engine advances `phase_index`; the phase's name and numbers come
    from the template the encounter was started from.
    """
    template = BOSS_TEMPLATES.get(str(encounter.get("template_key")), {})
    phases = list(template.get("phases") or [])
    if not phases:
        return {}
    return dict(phases[min(max(0, int(encounter.get("phase_index", 0))), len(phases) - 1)])


def era_template(
    cycles: dict[str, Any] | None, name: str, world: str = ""
) -> dict[str, Any] | None:
    """The authored entry for an era, by name, out of `world_era_cycles`.

    The roster is **injected** rather than imported: `WORLD` is built in
    `app/bot/runtime.py`, and `test_app_layout.py` puts `rules` at the bottom of
    the layering, so this module cannot reach it. That is the same reason
    `narrator.py` takes a duck-typed `npc_resolver` (rc.27).

    Resolved by **name across every world** rather than by the row's `world`,
    for two reasons. A row written before schema 60 carries no world at all and
    must still find its template; and the content gate holds era names unique
    across the four cycles precisely so a name is enough - `advanceWorldEra`
    finds a world's place in its own cycle the same way.
    """
    cycles = cycles or {}
    if world:
        for entry in cycles.get(world) or ():
            if str(entry.get("name")) == name:
                return dict(entry)
    for cycle in cycles.values():
        for entry in cycle or ():
            if str(entry.get("name")) == name:
                return dict(entry)
    return None


def describe_era(
    era: dict[str, Any] | None, cycles: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """An era row with the cycle template's modifiers and duration folded in.

    Old databases stored the baseline era before modifiers became
    mechanical; resolving the template by name gives them the mechanics
    without rewriting historical rows. The engine owns the era clock; this
    is presentation over the row it stores.
    """
    if not era:
        return None
    out = dict(era)
    template = era_template(cycles, str(out.get("name")), str(out.get("world") or ""))
    if template:
        merged = dict(template.get("modifiers") or {})
        merged.update(out.get("modifiers") or {})
        out["modifiers"] = merged
        out["duration_days"] = int(template["duration_days"])
    return out




# The engine's techniqueForbidden (manual_forbidden_actions.go): a karma cost,
# one of these tags on the technique, or a Demonic manual or one tagged with
# the first three.
_FORBIDDEN_TECHNIQUE_TAGS = frozenset({"forbidden", "demonic", "evil", "sacrificial", "soul_devouring"})
_FORBIDDEN_MANUAL_TAGS = frozenset({"forbidden", "demonic", "evil"})


def manual_technique_forbidden(technique: Mapping[str, Any], manual: Mapping[str, Any] | None = None) -> bool:
    """Display twin of the engine's techniqueForbidden."""
    tags = {str(tag).casefold() for tag in technique.get("tags") or []}
    if int(technique.get("karma_cost") or 0) > 0 or tags & _FORBIDDEN_TECHNIQUE_TAGS:
        return True
    manual = manual or {}
    manual_tags = {str(tag).casefold() for tag in manual.get("tags") or []}
    return str(manual.get("alignment") or "").casefold() == "demonic" or bool(manual_tags & _FORBIDDEN_MANUAL_TAGS)


def describe_manual_technique(
    technique: Mapping[str, Any],
    *,
    mastery: int = 0,
    manual: Mapping[str, Any] | None = None,
    raid: bool = False,
) -> str:
    """One line saying what a manual's technique does (v1.9.1).

    The picker used to print the technique's authored description, which for
    the generated catalogue reads "A mortal-tier sword cultivator technique
    preserved in ..." and says nothing about the fight. The numbers are the
    engine's: in a battle, damage and heal are the technique's own plus the
    manual's mastery (manualTechniqueAction); in a raid, damage plus mastery
    replaces an attack's +1 on the strike, never under 1, and a raid holds no
    suppression (bossActActionGo). The qi is the content's base cost - the
    engine scales it into the cultivator's own qi body.
    """
    damage = max(0, int(technique.get("damage") or 0) + int(mastery))
    heal = max(0, int(technique.get("heal") or 0) + int(mastery))
    parts: list[str] = []
    if raid:
        parts.append(f"💥 +{max(1, damage)} to the strike")
        if heal:
            parts.append(f"🩸 +{heal} raid vitality")
    else:
        if damage:
            parts.append(f"💥 {damage} damage")
        if heal:
            parts.append(f"🩸 +{heal} vitality")
        suppress = int(technique.get("suppress_turns") or 0)
        if suppress > 0:
            parts.append(f"⛓️ holds {suppress} turn{'s' if suppress != 1 else ''}")
    cost = f"⚡ {int(technique.get('qi_cost') or 0)} Qi"
    vitality_cost = int(technique.get("vitality_cost") or 0)
    if vitality_cost > 0:
        cost += f" + {vitality_cost} Vit"
    parts.append(cost)
    if manual_technique_forbidden(technique, manual):
        karma = int(technique.get("karma_cost") or 0)
        parts.append(f"☯️ forbidden{f', -{karma} karma' if karma else ''}")
    return " · ".join(parts)


def law_raid_strike_bonus(comprehension: int) -> int:
    """What a Law technique adds to a raid strike: bossActActionGo's 2 + comprehension/20."""
    return 2 + max(0, int(comprehension)) // 20


_DEED_WORDS = {
    "world_event_good_deed": "for helping in the event",
    "scene_resolve": "for standing firm",
    "personal_event_helped": "for seeing their trouble through",
    "event_site_cleared": "for clearing the last of the site",
}


def deed_karma_line(deed: Mapping[str, Any] | None) -> str:
    """The line a reply prints for karma a good deed paid (v1.9.1).

    Everything comes from the engine's `deed_karma` block; a deed that paid
    nothing - its cap reached - is absent and prints nothing.
    """
    deed = dict(deed or {})
    delta = int(deed.get("karma_delta") or 0)
    if not delta:
        return ""
    why = _DEED_WORDS.get(str(deed.get("deed") or ""), "for a good deed")
    return f"☯️ Karma **{delta:+d}** {why} → **{int(deed.get('karma_score') or 0):+d}**"
