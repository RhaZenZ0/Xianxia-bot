"""Author the sect seats (v1.19.0): each public sect's gate becomes a district of
the city it keeps its seat in.

On the owner's call (Option A of the rival-capitals question): one capital per
world stays as it is, and the sect politics the tree already has - claims, wars,
relations - carry the rivalry between the cities the sects sit in. The gate
location keeps its name, its people, its description and its flags (a private
gate stays private: a sponsor still reveals it); it gains `outside_location`
and `district: "sect_gate"`, so `cityOf` answers the seat and the sect claims
the city as its home ground. Every NPC kept at the gate is stamped with its
district, which is what the content gate asks of every district's people.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "content" / "world.json"

SEATS = {
    "Azure Cloud Sect": "Cloudblade City",
    "Crimson Furnace Sect": "Emberforge City",
    "Frozen Moon Palace": "Frostwatch City",
    "Black Serpent Clan": "Moonfen City",
    "Blood River Sect": "Riverguard City",
    "Corpse Lantern Pavilion": "Ashenwall City",
    "Jade Meridian Sect": "Jade Crown Spirit City",
    "Thousand Beast Valley": "Galevein Spirit City",
    "Heavenblade Immortal Sect": "Heavenblade Immortal City",
    "Ashen Lotus Pavilion": "Lunar Veil Immortal City",
    "Celestial Mandate Academy": "Mandate Crown Celestial City",
    "Void Serpent Cult": "Lunar Shadow Celestial City",
}


def main() -> None:
    w = json.loads(PATH.read_text(encoding="utf-8"))
    locations, sects, npcs = w["locations"], w["sects"], w["npcs"]
    cities = {h["entrance_location"] for h in w["auction_houses"].values()}
    seen: set[str] = set()
    for sect, city in SEATS.items():
        gate = str((sects[sect].get("recruitment") or {}).get("location") or "")
        if gate not in locations or city not in locations:
            raise SystemExit(f"{sect}: gate {gate!r} or seat {city!r} is not in the catalogue")
        if city not in cities or locations[city].get("realm_hub"):
            raise SystemExit(f"{sect}: {city} is not a walled city, or is a capital")
        if locations[gate]["world"] != locations[city]["world"]:
            raise SystemExit(f"{sect}: the gate stands in {locations[gate]['world']} and the seat in {locations[city]['world']}")
        if city in seen:
            raise SystemExit(f"{city} would seat two sects")
        seen.add(city)
        locations[gate]["outside_location"] = city
        locations[gate]["district"] = "sect_gate"
        for npc in npcs.values():
            if npc.get("location") == gate:
                npc["district"] = gate
    public = {n for n, s in sects.items() if not s.get("hidden") and (s.get("recruitment") or {}).get("location")}
    if public != set(SEATS):
        raise SystemExit(f"the seats do not cover every public sect: {sorted(public ^ set(SEATS))}")
    PATH.write_text(json.dumps(w, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"seated {len(SEATS)} sects")


if __name__ == "__main__":
    main()
