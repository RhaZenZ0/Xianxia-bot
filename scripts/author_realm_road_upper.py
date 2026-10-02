"""Author realm_road stages 8-31 (v1.18.0): the road through the three upper worlds.

Same mechanism as v1.16.0's seven Mortal stages, one realm further twenty-four
times. Every place named is read off the content file rather than typed, so a
renamed city or realm fails here rather than in a label.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "content" / "world.json"
w = json.loads(PATH.read_text(encoding="utf-8"))

realms = [r["name"] for r in w["realms"]]
locations = w["locations"]
capitals = {d["world"]: n for n, d in locations.items() if d.get("realm_hub")}
flames = {v["world"]: (k, v) for k, v in w["flame_system"]["flames"].items()}
secret = w["secret_realms"]
bosses = {
    "Spiritual World": "Hundred-Horn Ancestor Stag",
    "Immortal World": "Starfall Iron Colossus",
    "Celestial World": "Unmoored Star Leviathan",
}
wilds = {d["world"]: n for n, d in locations.items() if d.get("wilds_of") and d["world"] != "Mortal World"}
jobs = {d["world"]: n for n, d in locations.items() if d.get("district") in ("archive", "court", "altar")}
if set(jobs) != {"Spiritual World", "Immortal World", "Celestial World"}:
    raise SystemExit(f"the job districts are not one per upper world: {jobs}")
if set(wilds) != set(bosses):
    raise SystemExit(f"the wild places and the bosses disagree about the worlds: {wilds}")


def realm_name(i: int) -> str:
    return realms[i]


def gate(i: int) -> dict:
    return {"id": "gate", "type": "breakthrough", "target": realm_name(i), "count": 1,
            "label": f"Break through into {realm_name(i)} - **/cultivation → Cultivate → Breakthrough**"}


def travel(oid: str, place: str) -> dict:
    return {"id": oid, "type": "travel", "target": place, "count": 1,
            "label": f"Walk the roads to {place} - **/travel → Destinations → Go**"}


def explore(oid: str, place: str, what: str) -> dict:
    return {"id": oid, "type": "explore", "target": place, "count": 1,
            "label": f"{what} - **/world → Act → Explore**"}


def flame(world: str) -> dict:
    key, f = flames[world]
    return {"id": "flame", "type": "flame_capture", "target": key, "count": 1,
            "label": f"Capture the {f['name']} at {f['location']} - **/craft → Flames → Capture**"}


def raid(world: str) -> dict:
    return {"id": "raid", "type": "raid_win", "target": bosses[world], "count": 1,
            "label": f"Bring down the {bosses[world]} and claim your share - **/combat → Boss Raids → Claim**"}


def enter(oid: str, realm_key: str) -> dict:
    r = secret[realm_key]
    return {"id": oid, "type": "realm_enter", "target": realm_key, "count": 1,
            "label": f"Enter the {r['name']} at {r['location']} - **/realm → Secret Realms → Enter**"}


def law(oid: str, target: str | None, what: str) -> dict:
    o = {"id": oid, "type": "law_comprehend", "count": 1, "label": f"{what} - **/cultivation → Laws → Comprehend**"}
    if target:
        o["target"] = target
    return o


def technique(oid: str, target: str | None, what: str) -> dict:
    o = {"id": oid, "type": "law_technique", "count": 1, "label": f"{what} - **/cultivation → Laws → Technique**"}
    if target:
        o["target"] = target
    return o


def plain(oid: str, kind: str, label: str) -> dict:
    return {"id": oid, "type": kind, "count": 1, "label": label}


def rewards(i: int) -> dict:
    return {"insight_xp": min(1000, 320 + (i - 7) * 28), "spirit_stones": min(1000, 400 + (i - 7) * 25)}


S, I, C = "Spiritual World", "Immortal World", "Celestial World"
stages: list[dict] = []


def stage(i: int, title: str, description: str, opening: str, objectives: list[dict]) -> None:
    stages.append({
        "quest_key": f"realm_road_{i}", "realm_index": i, "title": title, "description": description,
        "opening": opening, "objectives": [gate(i), *objectives], "rewards": rewards(i), "follow_on": f"realm_road_{i + 1}",
    })


# ---- Spiritual World, 8-15 -------------------------------------------------
stage(8, "The Second World",
      f"The Spiritual World is the Mortal World again with more in it, and its capital is {capitals[S]}. Chart a road to it with **/world → Act → Explore** from the cities you know and walk there. "
      f"If you carry a Mortal sect's standing, its elders have a letter for you: the allied sect above keeps a gate in this world, and **/sect → Recruitment → Ascend** reads it there.",
      "The seam closes behind you and the qi here is a river where the Mortal World's was a stream. Every city on this side is a stranger, and one of them is the capital.",
      [travel("walk", capitals[S]), explore("look", capitals[S], "Look around the capital")])
stage(9, "The Flame and the Archive",
      f"Two things this world keeps that the Mortal World did not: the {flames[S][1]['name']} on the {flames[S][1]['location']}, and the Laws. {jobs[S]} is where the Laws are read, and a Law comprehended under its roof is comprehended more clearly.",
      "Your trade has a flame waiting for it, and your mind has a Law. Neither is given; both are at the end of a road.",
      [flame(S), law("law", None, f"Comprehend a Law - under the roof of {jobs[S]} if you can")])
stage(10, "The Steppe",
      f"{wilds[S]} lies in the wilds of {locations[wilds[S]]['wilds_of']}; an explore from that city finds it. The {secret['ancestor_stag_bone_hall']['name']} opens there to a key the world's array workshops sell.",
      "Beyond the last road out of the spirit city the grass goes on to the horizon, and under it is bone.",
      [travel("steppe", wilds[S]), enter("hall", "ancestor_stag_bone_hall")])
stage(11, "The Hundred-Horn Ancestor",
      f"Nirvana is the floor of a supreme Law, and the {bosses[S]} walks the steppe at this realm. Gather a party, or go alone and face it at its lesser strength.",
      "The herd has an ancestor, and the ancestor has been waiting for somebody at your realm since before the steppe had a name.",
      [raid(S), law("space", "space", "Begin the Law of Space, the supreme Law whose floor is Nirvana")])
stage(12, "A Law Made Manifest",
      f"Law Manifestation is where a comprehended Law first does something: every Law has a technique at this realm. The {secret['last_lantern_wake']['name']} at {secret['last_lantern_wake']['location']} opens on the rotation.",
      "The Law you have been reading turns in your hands and asks to be used.",
      [technique("manifest", None, "Manifest a Law technique"), enter("wake", "last_lantern_wake")])
stage(13, "The Ninth Stage Again",
      f"The Perfect Path is open at the ninth stage of every realm, and this world's are worth perfecting. The {secret['broken_pagoda_sutra_hall']['name']} at {secret['broken_pagoda_sutra_hall']['location']} is the Spiritual World's first ruin.",
      "You have stood at a ninth stage before. This time you know what it is for.",
      [plain("perfect", "perfection_start", "Begin a Perfect Path at the ninth stage - **/ascend → Perfection → Start**"), enter("pagoda", "broken_pagoda_sutra_hall")])
stage(14, "The Toppled Stele",
      f"The {secret['toppled_stele_sword_field']['name']} at {secret['toppled_stele_sword_field']['location']} is the deepest ruin of this world that needs no key, and its last room is where a swordsman's trial leaves its mark.",
      "One realm stands between you and the Transcendence Tribulation. The sword field is where the old ones went to be sure of themselves first.",
      [enter("stele", "toppled_stele_sword_field")])
stage(15, "The Transcendence Tribulation",
      "The Transcendence Realm is the last of this world. Prepare at the place you will stand with **/ascend → Tribulation / Ascension → Prepare** and survive the three waves; what survives them is told where the seam is.",
      "The sky over the Spiritual World is a different colour from the Mortal one, and it has noticed you all the same.",
      [plain("heaven", "tribulation_cleared", "Survive the Transcendence Tribulation - **/ascend → Tribulation / Ascension → Attempt**")])

# ---- Immortal World, 16-23 -------------------------------------------------
stage(16, "The Immortal Court",
      f"The Immortal World is where the crafts are judged. Its capital is {capitals[I]}; chart a road to it and walk there. A sect's letter is read at the gate of the allied sect in this world, as before.",
      "Immortals are not what the Mortal World's stories said. They are tired, mostly, and very good at one thing each.",
      [travel("walk", capitals[I]), explore("look", capitals[I], "Look around the court")])
stage(17, "The Sun Flame and the Grandmasters",
      f"The {flames[I][1]['name']} on the {flames[I][1]['location']} is the first flame that opens the top grade. {jobs[I]} judges every craft made in its square and lends the roll its eye.",
      "What you make here is weighed by people who have made it better. That is the point of coming.",
      [flame(I), plain("craft", "craft", f"Make something in your trade - in {jobs[I]} if you can - **/craft → General Crafting → Craft**")])
stage(18, "The Crater",
      f"{wilds[I]} lies in the wilds of {locations[wilds[I]]['wilds_of']}. The {secret['fallen_star_forge']['name']} opens there to a key the world's array workshops sell.",
      "Something fell here long before the court was built, and the court was built where it could keep an eye on the hole.",
      [travel("crater", wilds[I]), enter("forge", "fallen_star_forge")])
stage(19, "The Iron Colossus",
      f"The {bosses[I]} stands in the crater at this realm. A party fights it at full strength; one cultivator alone at its lesser.",
      "It was a forge once. Then it was a weapon. Now it is waiting.",
      [raid(I)])
stage(20, "A Domain of Your Own",
      f"At Golden Immortal every Law has a Domain - a technique that holds the ground around you. The {secret['weeping_wall_sanctum']['name']} at {secret['weeping_wall_sanctum']['location']} is this world's first ruin, and its last room keeps a ring worth a city.",
      "A technique strikes once. A Domain is a place that is yours while you stand in it.",
      [technique("domain", None, "Manifest a Law technique - a Domain, if your Law has reached one"), enter("sanctum", "weeping_wall_sanctum")])
stage(21, "The Ash Gate",
      f"The {secret['ash_gate_threshold']['name']} at {secret['ash_gate_threshold']['location']} opens on the rotation. The halls of this world examine the trades; sit the one your trade is ready for.",
      "The gate is ash because everything that came through it burned. The threshold is still there.",
      [enter("ash", "ash_gate_threshold"), plain("exam", "profession_exam", "Pass a hall's examination in your trade - **/craft → Profession → Profession Exam**")])
stage(22, "The Cracked Altar",
      f"The {secret['cracked_altar_sanctum']['name']} at {secret['cracked_altar_sanctum']['location']} is the deepest ruin of this world that needs no key. Every Law has a Domain by now; the Immortal Sovereign's tribulation is one realm on.",
      "The altar cracked when somebody asked the heavens a question they did not want answered. Ask a smaller one.",
      [enter("altar", "cracked_altar_sanctum")])
stage(23, "The Celestial Ascension Tribulation",
      "Immortal Sovereign is the last of this world. Prepare at the place you will stand with **/ascend → Tribulation / Ascension → Prepare** and survive the three waves.",
      "The heavens over the Immortal World are close enough to hear, and they are not pleased.",
      [plain("heaven", "tribulation_cleared", "Survive the Celestial Ascension Tribulation - **/ascend → Tribulation / Ascension → Attempt**")])

# ---- Celestial World, 24-31 ------------------------------------------------
stage(24, "The Mandate Palace",
      f"The Celestial World is where the heavens are nearest. Its capital is {capitals[C]}; chart a road to it and walk there. {jobs[C]} stands in it, and a breakthrough made on its stone is made a little more surely.",
      "There is no sky here. There is the Mandate, and it is looking down.",
      [travel("walk", capitals[C]), explore("look", capitals[C], "Look around the palace")])
stage(25, "The Chaos Flame",
      f"The {flames[C][1]['name']} on the {flames[C][1]['location']} is the last flame. The {secret['hollow_throne_vault']['name']} at {secret['hollow_throne_vault']['location']} is this world's first ruin.",
      "The flame that was here before anything was made is still here, and it does not care who holds it.",
      [flame(C), enter("throne", "hollow_throne_vault")])
stage(26, "The Firmament",
      f"{wilds[C]} lies in the wilds of {locations[wilds[C]]['wilds_of']}. The {secret['unmoored_star_hollow']['name']} opens there to a key the world's array workshops sell, and nothing in it fades with your realm.",
      "Where the firmament broke, the stars came loose. One of them is still falling.",
      [travel("firmament", wilds[C]), enter("hollow", "unmoored_star_hollow")])
stage(27, "The Leviathan",
      f"The {bosses[C]} swims the broken firmament at this realm. It is the last raid in the world.",
      "It has no lair because it needs none. It is the thing the stars were afraid of.",
      [raid(C)])
stage(28, "The Buried Court",
      f"The {secret['buried_court_assize']['name']} at {secret['buried_court_assize']['location']} opens on the rotation. At Celestial Emperor the deepest Laws - Life, Death, the Void - have a Domain.",
      "The court was buried with its judges in it. They are still in session.",
      [enter("assize", "buried_court_assize"), technique("deep", None, "Manifest a Law technique - the deep Laws have theirs now")])
stage(29, "Nine Pillars",
      f"The {secret['nine_pillar_vault']['name']} at {secret['nine_pillar_vault']['location']} is the deepest ruin in the game that needs no key. The Perfect Path at this ninth stage is the last one worth walking before the Saint's.",
      "Nine pillars hold the vault up. Nobody remembers what the tenth held.",
      [enter("pillars", "nine_pillar_vault"), plain("perfect", "perfection_start", "Begin a Perfect Path at the ninth stage - **/ascend → Perfection → Start**")])
stage(30, "A World of Your Own",
      "A Dao Saint who holds the whole of the Law of Space folds a world of their own. Comprehend Space to Essence/Origin, then stabilize the world with **/innerworld → Personal World → Create**.",
      "Everything you have walked through was somebody's. This is where you make one.",
      [law("space", "space", "Comprehend the Law of Space to Essence/Origin"),
       plain("world", "personal_world", "Stabilize a personal world - **/innerworld → Personal World → Create**")])
stage(31, "The Sovereign",
      "Dao Sovereign is the top of the ladder. World Collapse is the Law of Space's capstone, manifested inside a world you folded yourself; nothing stands above it, and nothing is handed over after it.",
      "There is no realm above this one. What you do here is the end of the road and the only reason it was built.",
      [technique("collapse", "world_collapse", "Manifest World Collapse inside your own world")])

stages[-1]["follow_on"] = ""

existing = {s["quest_key"]: s for s in w["realm_road"]}
if "realm_road_7" not in existing or existing["realm_road_7"]["follow_on"] != "":
    raise SystemExit("realm_road_7 is missing or already chains on; the Mortal road is not the shape this script extends")
existing["realm_road_7"]["follow_on"] = "realm_road_8"
w["realm_road"] = [s for s in w["realm_road"] if int(s["realm_index"]) <= 7] + stages
if [s["realm_index"] for s in w["realm_road"]] != list(range(1, len(realms))):
    raise SystemExit("the road does not cover every realm once in order")
PATH.write_text(json.dumps(w, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {len(stages)} stages, realm_road now {len(w['realm_road'])} long")
