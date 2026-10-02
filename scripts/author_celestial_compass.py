"""Author the Celestial compass (v1.19.1): the ring stops folding back on itself.

A city's gates come from its roads, and both ends of a road face opposite ways,
so the Celestial World's twelve cities have always formed a ring - but its
compass read as a line. At Starroad Celestial City one East gate faced both of
its roads, and at Lunar Shadow Celestial City one West gate did the same, so a
walk around the ring went east, doubled back west, and doubled back again: ten
east gates and nine west against two north and two south, where the Spiritual
and Immortal rings are near even.

On the owner's call the two folds are relabelled. Starroad is the ring's west
end and turns its roads north (to Celestial River City) and south (to Solar
Crucible); Lunar Shadow is the east end and turns them north (to Froststar
Border City) and south (to Mandate Spear City). Every gate that loses its last
road is renamed to the side the road now leaves by, keeping its captain, its
encounters and its sense; Celestial River City's west gate folds into its south
gate, which already stood. Two new gates (Starroad's south, Lunar Shadow's
south) are new rows with a captain each.

A running world is carried onto the new names by migration 75, whose pairs are
`GATE_RENAMES` here, frozen in `app/database/core.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "content" / "world.json"

# Each road whose compass turns: (city, neighbour, the side city faces it by).
# Both ends are written; the far end faces back by the opposite side.
TURNS = (
    ("Starroad Celestial City", "Celestial River City", "North"),
    ("Starroad Celestial City", "Solar Crucible Celestial City", "South"),
    ("Lunar Shadow Celestial City", "Froststar Border City", "North"),
    ("Lunar Shadow Celestial City", "Mandate Spear City", "South"),
)
OPPOSITE = {"North": "South", "South": "North", "East": "West", "West": "East"}

# Old gate -> the gate it is now. Held equal to migration 75's frozen copy.
GATE_RENAMES = {
    "Celestial River City West Gate": "Celestial River City South Gate",
    "Solar Crucible Celestial City West Gate": "Solar Crucible Celestial City North Gate",
    "Starroad Celestial City East Gate": "Starroad Celestial City North Gate",
    "Lunar Shadow Celestial City West Gate": "Lunar Shadow Celestial City North Gate",
    "Froststar Border City East Gate": "Froststar Border City South Gate",
    "Mandate Spear City East Gate": "Mandate Spear City North Gate",
}

# The two gates no road had before, each with a captain of its own.
NEW_CAPTAINS = {
    "Starroad Celestial City South Gate": (
        "Gate Captain Yan Hui",
        "Unhurried; reads a token twice and a face three times.",
        "Patient, dry and impossible to rush.",
        "Keeps a ledger of every caravan that has failed to come back up the south road, and why.",
    ),
    "Lunar Shadow Celestial City South Gate": (
        "Gate Captain Qiu Lan",
        "Quiet, and answers a question with a shorter one.",
        "Watchful, careful and slow to trust a stranger's story.",
        "Was posted to the south gate after asking too many questions about who comes and goes at night.",
    ),
}


def gate_description(direction: str, city: str, faces: list[str]) -> str:
    """The sentence every gate carries, in the shape the other gates use."""
    return (
        f"The {direction.lower()} gate of {city}, facing the road to {', '.join(faces)}. "
        "a gatehouse, a queue of carts, a guard post that checks every cultivator's token and a board of the city's notices. "
        f"Everyone who arrives by road arrives here, and the streets of {city} begin a step inside."
    )


def rename(value, renames):
    if isinstance(value, str):
        for old, new in renames.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [rename(v, renames) for v in value]
    if isinstance(value, dict):
        return {rename(k, renames): rename(v, renames) for k, v in value.items()}
    return value


def main() -> None:
    w = json.loads(PATH.read_text(encoding="utf-8"))
    locations = w["locations"]
    if "Starroad Celestial City South Gate" in locations:
        print("already authored")
        return

    # The compass of each city whose roads turn.
    for city, neighbour, side in TURNS:
        for here, there, facing in ((city, neighbour, side), (neighbour, city, OPPOSITE[side])):
            gates = locations[here]["gates"]
            for names in gates.values():
                if there in names:
                    names.remove(there)
            gates.setdefault(facing, []).append(there)
            locations[here]["gates"] = {d: sorted(n) for d, n in gates.items() if n}

    # Every name in the file moves with its gate - the gate row's own key, the
    # captains, their schedules, the notice boards and the commissions.
    # A rename onto a gate that already stood (Celestial River City's west gate
    # into its south gate) keeps the row that stood: the old one is dropped
    # before the names move, or the two would collapse onto one key and the
    # later in the file would win.
    old_rows = {old: locations[old] for old in GATE_RENAMES}
    for old, new in GATE_RENAMES.items():
        if new in locations:
            del locations[old]
    w = rename(w, GATE_RENAMES)
    locations = w["locations"]

    # The two new gates, beside the gate of their city they now stand opposite.
    template = old_rows["Starroad Celestial City East Gate"]
    ordered: dict = {}
    for name, loc in locations.items():
        ordered[name] = loc
        for new_gate in NEW_CAPTAINS:
            city = new_gate.rsplit(" ", 2)[0]
            if name == f"{city} North Gate":
                ordered[new_gate] = {
                    **{k: v for k, v in template.items() if k not in ("description", "outside_location", "gate", "encounters")},
                    "outside_location": city,
                    "encounters": [e.replace("Starroad Celestial City", city) for e in template["encounters"]],
                    "gate": "South",
                }
    locations = ordered
    w["locations"] = locations

    # Every gate of every city whose compass moved says what it faces now.
    for city in {c for t in TURNS for c in (t[0], t[1])}:
        for side, faces in locations[city]["gates"].items():
            gate = locations[f"{city} {side} Gate"]
            gate["gate"] = side
            gate["description"] = gate_description(side, city, faces)

    npcs = w["npcs"]
    for gate, (name, speech, personality, secret) in NEW_CAPTAINS.items():
        city = gate.rsplit(" ", 2)[0]
        npcs[name] = {
            "role": f"{city} captain of the gate guard",
            "location": gate,
            "realm": "Celestial",
            "stage": 1,
            "district": gate,
            "speech": speech,
            "want": f"Keep {city}'s gate orderly and every token honest.",
            "fear": "A forged token in a hand that should not be inside the walls.",
            "personality": personality,
            "secret": secret,
            "schedule": {p: gate for p in ("Dawn", "Morning", "Afternoon", "Evening", "Night")},
        }

    PATH.write_text(json.dumps(w, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("authored")


if __name__ == "__main__":
    main()
