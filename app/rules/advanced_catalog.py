from __future__ import annotations

from typing import Any

PATHS = ("Sword Cultivator", "Qi Refiner", "Body Refiner", "Soul Cultivator", "Beast Binder", "Formation Adept")
WORLDS = ("Mortal", "Spiritual", "Immortal", "Celestial")
GRADES = ("Mortal", "Spirit", "Earth", "Heaven", "Immortal", "Dao")
ORTHODOX_PREFIXES = (
    "Azure Cloud", "Golden Meridian", "Vermilion Crane", "Jade River", "Nine Echo", "White Lotus",
    "Starfall", "Thunder Peak", "Moonshadow", "Heavenly Reed", "Iron Mountain", "Clear Sky",
    "Void Lantern", "Dragon Gate", "Phoenix Feather", "Radiant Sun", "Frost Moon", "Endless Tide",
)
DEMONIC_PREFIXES = (
    "Blood Moon", "Soul Furnace", "White Bone", "Abyss Maw", "Corpse Lantern", "Black Venom",
    "Heart Demon", "Nether Sacrifice", "Ghost Banner", "Scarlet Hunger", "Ashen Veil", "Devouring Heaven",
)
ORTHODOX_SUFFIXES = ("Canon", "Scripture", "Manual", "Record", "Codex", "Sutra", "Art", "Inheritance")
DEMONIC_SUFFIXES = ("Forbidden Scripture", "Demon Codex", "Blood Record", "Ghost Sutra", "Black Manual")
TECHNIQUE_VERBS = ("Strike", "Step", "Guard", "Seal", "Wave", "Edge", "Palm", "Thread", "Domain", "Breath")

# v1.0.0-rc.9: every manual draws one kind of qi, and which kind decides how
# well a given spiritual root can absorb it. The element is read off the
# method's own name first - a Vermilion Crane canon is a fire method however it
# was generated - and otherwise falls to a stable spread over the five phases
# by the manual id, so the catalogue is reproducible and the content and this
# generator can never drift apart.
MANUAL_ELEMENTS = ("Fire", "Water", "Wood", "Metal", "Earth", "Lightning", "Wind", "Ice", "Yin", "Yang", "Void", "Chaos")
FIVE_PHASES = ("Wood", "Fire", "Earth", "Metal", "Water")
# The generated catalogue is built from fixed prefixes, so the element follows
# the prefix: it fits the name, and because the generator cycles the prefixes
# every element ends up with methods a cultivator of that root can seek out.
_PREFIX_ELEMENTS: dict[str, str] = {
    "Azure Cloud": "Wind", "Golden Meridian": "Metal", "Vermilion Crane": "Fire",
    "Jade River": "Water", "Nine Echo": "Void", "White Lotus": "Wood",
    "Starfall": "Chaos", "Thunder Peak": "Lightning", "Moonshadow": "Yin",
    "Heavenly Reed": "Wood", "Iron Mountain": "Metal", "Clear Sky": "Wind",
    "Void Lantern": "Void", "Dragon Gate": "Earth", "Phoenix Feather": "Fire",
    "Radiant Sun": "Yang", "Frost Moon": "Ice", "Endless Tide": "Water",
    "Blood Moon": "Yin", "Soul Furnace": "Fire", "White Bone": "Metal",
    "Abyss Maw": "Void", "Corpse Lantern": "Yin", "Black Venom": "Water",
    "Heart Demon": "Chaos", "Nether Sacrifice": "Void", "Ghost Banner": "Wind",
    "Scarlet Hunger": "Fire", "Ashen Veil": "Earth", "Devouring Heaven": "Chaos",
}
# The authored manuals name no prefix from that table, so they are read by the
# words they do use.
_ELEMENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Chaos", ("chaos",)),
    ("Void", ("void", "abyss", "nether", "hollow")),
    ("Yin", ("yin", "shadow", "ghost", "corpse", "blood", "demon", "bone", "soul", "spectral", "sacrifice")),
    ("Yang", ("yang", "solar", "radiant", "sun")),
    ("Lightning", ("thunder", "lightning", "storm")),
    ("Ice", ("frost", "glacier", "winter", "snow")),
    ("Wind", ("wind", "gale", "cloud", "sky", "feather")),
    ("Fire", ("flame", "fire", "ember", "vermilion", "furnace", "crimson", "blaze", "scarlet", "phoenix")),
    ("Water", ("tide", "river", "sea", "rain", "wave", "mist", "moon", "water", "lotus", "venom", "poison")),
    ("Wood", ("verdant", "reed", "herb", "vine", "forest", "wood", "spring")),
    ("Metal", ("iron", "golden", "gold", "blade", "sword", "sabre", "steel", "edge", "spear", "needle")),
    ("Earth", ("earth", "stone", "mountain", "soil", "sand", "dust", "tortoise", "jade")),
)


def manual_element(manual_id: str, name: str = "", description: str = "") -> str:
    """The element a cultivation manual draws, from its own name.

    Deterministic and total: a manual that names no element at all is spread
    over the five phases by a stable sum of its id, so the same catalogue
    always produces the same elements.
    """
    label = str(name or manual_id)
    for prefix, element in _PREFIX_ELEMENTS.items():
        if label.startswith(prefix):
            return element
    # The name only: the generated catalogue shares two boilerplate
    # descriptions, so reading them would drown the five phases in whichever
    # element those two paragraphs happen to mention.
    haystack = label.casefold()
    for element, words in _ELEMENT_KEYWORDS:
        if any(word in haystack for word in words):
            return element
    return FIVE_PHASES[sum(manual_id.encode("utf-8")) % len(FIVE_PHASES)]


def _slug(text: str) -> str:
    return "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


def _technique_record(*, name: str, manual_id: str, index: int, demonic: bool, path: str, world_index: int) -> dict[str, Any]:
    mastery = min(4, index // 2)
    qi_cost = 2 + world_index * 2 + index
    base_power = 4 + world_index * 3 + index * 2
    tags = [path.casefold().replace(" cultivator", "").replace(" ", "_"), WORLDS[world_index].casefold()]
    record: dict[str, Any] = {
        "name": name,
        "manual": manual_id,
        "min_mastery": mastery,
        "qi_cost": qi_cost,
        "vitality_cost": 0,
        "damage": base_power,
        "heal": 0,
        "suppress_turns": 0,
        "karma_cost": 0,
        "exposure": 1,
        "tags": tags,
        "description": f"A {WORLDS[world_index].lower()}-tier {path.lower()} technique preserved in {manual_id.replace('_', ' ')}.",
    }
    # Every generated art is executable by the existing battle resolver, but
    # not every art is just a larger damage number.
    mode = index % 4
    if mode == 1:
        record["damage"] = max(1, base_power - 2)
        record["suppress_turns"] = 1 + (world_index // 2)
        record["tags"].append("control")
    elif mode == 2:
        record["damage"] = max(1, base_power // 2)
        record["heal"] = 2 + world_index + mastery
        record["tags"].append("recovery")
    elif mode == 3:
        record["damage"] = base_power + 2
        record["qi_cost"] += 2
        record["tags"].append("burst")
    if demonic:
        record["karma_cost"] = 1 + world_index + (1 if index >= 3 else 0)
        record["vitality_cost"] = 1 if index % 3 == 0 else 0
        record["exposure"] = 3 + world_index + index
        record["tags"].extend(["demonic", "forbidden"])
        record["description"] = f"A forbidden {path.lower()} art that trades karmic safety for immediate power."
    return record


def _add_manual(
    data: dict[str, Any], *, manual_id: str, name: str, path: str, grade: str,
    min_realm_index: int, alignment: str, technique_count: int, ordinal: int,
) -> None:
    system = data.setdefault("technique_system", {})
    manuals = system.setdefault("manuals", {})
    techniques = system.setdefault("techniques", {})
    items = data.setdefault("items", {})
    if manual_id in manuals:
        return
    demonic = alignment.casefold() == "demonic"
    world_index = min(3, max(0, min_realm_index // 8))
    technique_ids: list[str] = []
    for i in range(technique_count):
        verb = TECHNIQUE_VERBS[(ordinal + i) % len(TECHNIQUE_VERBS)]
        technique_name = f"{name.split(' — ')[0]} {verb} {i + 1}"
        tid = f"{manual_id}_{_slug(verb)}_{i + 1}"
        # Guaranteed uniqueness if a seed record already happens to use a name.
        if tid in techniques:
            tid = f"{tid}_{ordinal}"
        techniques[tid] = _technique_record(
            name=technique_name, manual_id=manual_id, index=i,
            demonic=demonic, path=path, world_index=world_index,
        )
        technique_ids.append(tid)
    item_id = f"{manual_id}_manual"
    manuals[manual_id] = {
        "name": name,
        "item_id": item_id,
        "alignment": alignment,
        "path": path,
        "grade": grade,
        # v1.0.0-rc.9: the kind of qi this method draws.
        "element": manual_element(manual_id, name),
        "min_realm_index": min_realm_index,
        "description": (
            "A forbidden inheritance whose shortcuts accumulate exposure, karma debt and backlash."
            if demonic else
            "A complete cultivation inheritance with progressively unlocked named combat arts."
        ),
        "techniques": technique_ids,
        "generated_advanced_catalog": True,
    }
    items[item_id] = {
        "name": f"{name} — Jade Manual",
        "type": "manual",
        "manual_id": manual_id,
        "sect_value": 18 + min_realm_index * 3,
        "base_price": 90 + min_realm_index * 25,
        "special": bool(demonic or min_realm_index >= 16),
        # Inheritances are given (sect entry, hidden-sect initiation), found
        # or traded under the counter - never stocked on a town market. Without
        # this every regional market would list all 142 of them at base_price
        # the moment the catalog is on disk (v0.21.3).
        "market_excluded": True,
        "description": "A mechanically learnable cultivation manual from the expanded inheritance catalog.",
    }


def augment_advanced_catalog(data: dict[str, Any]) -> None:
    """Expand the compact seed into the advanced branch's documented catalog scale.

    This is deterministic and idempotent. Authored seed manuals are preserved.
    The generated catalog intentionally targets the documented branch contract:
    148 manuals/inheritances, 528 executable techniques, including exactly
    42 demonic manuals and 160 demonic techniques.
    """
    system = data.setdefault("technique_system", {})
    manuals: dict[str, dict[str, Any]] = system.setdefault("manuals", {})
    techniques: dict[str, dict[str, Any]] = system.setdefault("techniques", {})

    seed_evil_manuals = sum(1 for m in manuals.values() if str(m.get("alignment", "")).casefold() == "demonic")
    seed_evil_techniques = sum(
        1 for t in techniques.values()
        if str((manuals.get(str(t.get("manual"))) or {}).get("alignment", "")).casefold() == "demonic"
    )

    # The known compact seed is 6/12 evil records. Refuse silent drift: if a
    # future authored seed grows beyond the target, keep it intact rather than
    # deleting content to force a number.
    evil_manuals_to_add = max(0, 42 - seed_evil_manuals)
    evil_techniques_to_add = max(0, 160 - seed_evil_techniques)
    if evil_manuals_to_add:
        base_each, extra = divmod(evil_techniques_to_add, evil_manuals_to_add)
        for i in range(evil_manuals_to_add):
            path = PATHS[i % len(PATHS)]
            world_index = (i // len(PATHS)) % 4
            prefix = DEMONIC_PREFIXES[i % len(DEMONIC_PREFIXES)]
            suffix = DEMONIC_SUFFIXES[(i // len(DEMONIC_PREFIXES)) % len(DEMONIC_SUFFIXES)]
            name = f"{prefix} {suffix} — {WORLDS[world_index]} Volume {i + 1}"
            _add_manual(
                data, manual_id=f"advanced_demonic_{i + 1:03d}_{_slug(path)}", name=name,
                path=path, grade=GRADES[min(len(GRADES)-1, world_index + 2)],
                min_realm_index=world_index * 8 + (i % 8), alignment="Demonic",
                technique_count=base_each + (1 if i < extra else 0), ordinal=1000 + i,
            )

    # Re-read after evil expansion, then fill the orthodox/neutral side to the
    # exact total without modifying authored records.
    manuals = system["manuals"]
    techniques = system["techniques"]
    non_evil_target_manuals = 148 - sum(1 for m in manuals.values() if str(m.get("alignment", "")).casefold() == "demonic")
    non_evil_existing = sum(1 for m in manuals.values() if str(m.get("alignment", "")).casefold() != "demonic")
    non_evil_to_add = max(0, non_evil_target_manuals - non_evil_existing)
    evil_tech_count = sum(
        1 for t in techniques.values()
        if str((manuals.get(str(t.get("manual"))) or {}).get("alignment", "")).casefold() == "demonic"
    )
    non_evil_tech_target = 528 - evil_tech_count
    non_evil_tech_existing = len(techniques) - evil_tech_count
    non_evil_tech_to_add = max(0, non_evil_tech_target - non_evil_tech_existing)
    if non_evil_to_add:
        base_each, extra = divmod(non_evil_tech_to_add, non_evil_to_add)
        for i in range(non_evil_to_add):
            path = PATHS[i % len(PATHS)]
            world_index = (i // (len(PATHS) * 5)) % 4
            prefix = ORTHODOX_PREFIXES[i % len(ORTHODOX_PREFIXES)]
            suffix = ORTHODOX_SUFFIXES[(i // len(ORTHODOX_PREFIXES)) % len(ORTHODOX_SUFFIXES)]
            alignment = "Orthodox" if i % 4 else "Neutral"
            name = f"{prefix} {suffix} — {path} {i + 1}"
            _add_manual(
                data, manual_id=f"advanced_orthodox_{i + 1:03d}_{_slug(path)}", name=name,
                path=path, grade=GRADES[min(len(GRADES)-1, world_index + 1)],
                min_realm_index=world_index * 8 + (i % 8), alignment=alignment,
                technique_count=base_each + (1 if i < extra else 0), ordinal=2000 + i,
            )

    system.setdefault("mastery_levels", ["Learned", "Practiced", "Proficient", "Mastered", "Perfected"])
    system["catalog_contract"] = {
        "manuals": len(system["manuals"]),
        "techniques": len(system["techniques"]),
        "demonic_manuals": sum(1 for m in system["manuals"].values() if str(m.get("alignment", "")).casefold() == "demonic"),
        "demonic_techniques": sum(
            1 for t in system["techniques"].values()
            if str((system["manuals"].get(str(t.get("manual"))) or {}).get("alignment", "")).casefold() == "demonic"
        ),
    }

    # Advanced branch world-law/social-law contract and hidden demonic lineage.
    rules = data.setdefault("world_rules", {})
    rules.setdefault("physical_laws", {
        "qi_density": "world_and_region_scaled",
        "law_strength": "higher_worlds_are_stricter",
        "realm_suppression": True,
        "realm_ceiling": "location_and_world_bound",
        "ascension_pressure": True,
        "allowed_energies": ["qi", "body", "soul", "blood", "law"],
        "local_time_flow": {"Mortal World": 1, "Spiritual World": 3, "Immortal World": 9, "Celestial World": 27},
    })
    rules.setdefault("social_laws", {
        "npc_ambition": True,
        "hierarchy_pressure": True,
        "lineage_pressure": True,
        "righteous_enforcement": True,
        "demonic_visibility": "witness_and_concealment_based",
        "succession_pressure": True,
        "conflict_multiplier": "world_state_driven",
    })
    sects = data.setdefault("sects", {})
    sects.setdefault("Heaven-Devouring Demon Sect", {
        "alignment": "Demonic",
        "hidden": True,
        "description": "A concealed cross-world lineage that recruits cultivators already stained by severe karma debt.",
        "branches": {
            "Mortal World": "Ashen Veil Cell",
            "Spiritual World": "Blood Moon Hall",
            "Immortal World": "Abyss Palace",
            "Celestial World": "Heaven-Devouring Court",
        },
        "karma_observation": -50,
        "karma_initiation": -200,
        "righteous_enemy": 50,
    })
