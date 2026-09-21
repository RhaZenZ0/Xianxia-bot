#!/usr/bin/env python3
"""Author one cultivation manual per birth household (v1.0.3).

Eight of the thirteen households handed a child **another sect's** canon as the
family's own tradition - a fallen martial clan teaching the Azure Cloud Sect's
sword canon - and the other five handed out a procedurally generated id whose
catalogue index is in its own title ("Starfall Scripture - Sword Cultivator 25").
There was nowhere correct to point them: of 160 manuals only 18 are authored, 12
of those carry a `sect` and the other 6 are path-locked dark arts that
`manualForbidden` refuses at the lesson outright, so **no authored, sect-less,
non-forbidden manual existed in the game at all.**

The split was also a silent, permanent mechanical difference nobody had stated:
the eight sect canons are Mortal grade (x1.03 gathering) and the five generated
ones Spirit (x1.12), so which household a cultivator was born into was worth 9%
of every cultivation session for the whole of their life.

Every manual here is Mortal grade, `path: "Any"`, carries no `sect`, and is
named out of the household's own authored `story`. Run once; it is idempotent.

    python3 scripts/author_household_manuals.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORLD = ROOT / "content" / "world.json"

from app.rules.advanced_catalog import manual_element  # noqa: E402

# Mortal grade for every house, deliberately: rc.31's tutoring band is already
# what varies by a household's wealth, and two ladders varying on one axis would
# price the family's money twice - the reason rc.55 kept the manor array out of
# seclusion's environment term.
GRADE = "Mortal"

# (manual_id, name, element, alignment, blurb, [(suffix, name, mastery, qi, dmg, heal, suppress, tags, text)])
HOUSEHOLDS: dict[str, dict] = {
    "fallen_martial_clan": dict(
        manual_id="eastern_gate_remnant_form", name="Eastern Gate Remnant Form", element="Metal",
        alignment="Orthodox",
        blurb="What the clan still teaches of the form it held the eastern gate with, minus the three movements that needed the sword that was broken.",
        techniques=[
            ("held_stance", "Held Stance", 0, 2, 5, 0, 0, ["sword", "melee"], "The gate is not walked through. Weight back, edge level, and no step surrendered."),
            ("gate_breath", "Gate Breath", 1, 3, 1, 3, 0, ["recovery", "foundation"], "The breath four generations took between one assault and the next."),
            ("remembered_word", "The Remembered Word", 2, 3, 3, 0, 1, ["sword", "control"], "A parry the house teaches angrily, which is why it works."),
        ]),
    "tomb_watch_clan": dict(
        manual_id="grave_watch_vigil_record", name="Grave-Watch Vigil Record", element="Earth",
        alignment="Neutral",
        blurb="Two hundred years of standing still in the dark, written down by people who knew the thieves by name.",
        techniques=[
            ("lantern_low", "Lantern Held Low", 0, 2, 4, 0, 0, ["inscription", "control"], "Light that shows the ground and not the face above it."),
            ("long_vigil", "The Long Vigil", 1, 3, 0, 4, 0, ["recovery", "foundation"], "How to be awake at the fourth hour without being tired at the fifth."),
            ("cousins_name", "A Cousin's Name", 2, 3, 3, 0, 1, ["inscription", "control"], "A seal laid to be recognised rather than to hold, because it was usually family."),
        ]),
    "body_tempering_family": dict(
        manual_id="hungry_season_tempering_record", name="Hungry Season Tempering Record", element="Earth",
        alignment="Orthodox",
        blurb="The tempering that cost a household a winter and a son, written out afterwards so that it need never cost that much again.",
        techniques=[
            ("season_grip", "Season's Grip", 0, 2, 5, 0, 0, ["body", "melee"], "A hold learned from carrying what the house could not afford to drop."),
            ("standing_up", "Standing Up", 1, 3, 1, 4, 0, ["recovery", "body"], "He died standing. The house teaches the standing, and hopes to stop teaching the rest."),
            ("fed_first", "Fed First", 2, 3, 2, 2, 1, ["alchemy", "body"], "Which of two bodies gets the medicine, decided before either is bleeding."),
        ]),
    "border_garrison_family": dict(
        manual_id="stick_in_the_mud_array_primer", name="Stick-in-the-Mud Array Primer", element="Earth",
        alignment="Orthodox",
        blurb="The border array a widow of this house drew with a stick, taught exactly as she drew it because nobody has been allowed to move a line of it since.",
        techniques=[
            ("mud_line", "The Line in the Mud", 0, 2, 4, 0, 0, ["formation", "control"], "One furrow, laid where the ground will hold it, in the time you actually have."),
            ("wall_breath", "Wall Breath", 1, 3, 0, 4, 0, ["recovery", "formation"], "Qi banked the way a garrison banks a fire: slowly, and for the third night."),
            ("second_incursion", "The Second Incursion", 2, 3, 3, 0, 1, ["formation", "control"], "What the family learned the year it lost half its men, which is where to stand instead."),
        ]),
    "martial_household": dict(
        manual_id="unfamous_house_method", name="Unfamous House Method", element="Metal",
        alignment="Orthodox",
        blurb="A complete martial foundation with nothing memorable in it, which was the entire point and is written on the wall in the front room.",
        techniques=[
            ("plain_strike", "Plain Strike", 0, 2, 5, 0, 0, ["melee"], "Nothing anybody would travel to watch. It lands."),
            ("quiet_recovery", "Quiet Recovery", 1, 3, 1, 3, 0, ["recovery", "foundation"], "Mend where you are not being looked at."),
            ("unchallenged", "Unchallenged", 2, 3, 2, 0, 2, ["control"], "The house's oldest rule, as a technique: end it before anyone decides you are worth testing."),
        ]),
    "spear_guard_family": dict(
        manual_id="river_ford_spear_canon", name="River Ford Spear Canon", element="Water",
        alignment="Orthodox",
        blurb="Eleven spears held a ford until the town woke up. Two of them lived long enough to write this, and it is mostly about the waiting.",
        techniques=[
            ("ford_thrust", "Ford Thrust", 0, 2, 6, 0, 0, ["spear", "melee"], "Reach, from a footing that is never quite dry."),
            ("hold_the_shallows", "Hold the Shallows", 1, 3, 0, 4, 0, ["recovery", "foundation"], "Breath taken in the one place the current does the work for you."),
            ("until_morning", "Until Morning", 2, 4, 3, 0, 1, ["spear", "control"], "Nine were buried. This is the part that is about lasting, not winning."),
        ]),
    "sword_hall_family": dict(
        manual_id="left_hand_forge_canon", name="Left-Hand Forge Canon", element="Metal",
        alignment="Orthodox",
        blurb="Founded by a swordsman who lost his right hand and learned to forge with his left rather than stop touching swords. Taught left-handed to everyone, on purpose.",
        techniques=[
            ("wrong_hand", "The Wrong Hand", 0, 2, 5, 0, 0, ["sword", "melee"], "A cut from the side nobody trains against, including you, at first."),
            ("fuller_rest", "Fuller's Rest", 1, 3, 1, 3, 0, ["recovery", "forging"], "The pause he took with a thumb in the groove, which is still nicked into every blade the hall marks."),
            ("hand_you_have", "The Hand You Have", 2, 3, 3, 0, 1, ["sword", "control"], "A guard built on the assumption that something is already missing."),
        ]),
    "hidden_weapon_family": dict(
        manual_id="unaccepted_fan_primer", name="Unaccepted Fan Primer", element="Wind",
        alignment="Neutral",
        blurb="Three generations uncaught, which is not the same as three generations unsuspected. The primer is mostly about the difference.",
        techniques=[
            ("sleeve_measure", "Sleeve Measure", 0, 2, 4, 0, 0, ["inscription", "concealment"], "Knowing to the inch what your sleeve will take before you need it to take anything."),
            ("nothing_shown", "Nothing Shown", 1, 3, 0, 3, 0, ["recovery", "concealment"], "Mending done at a table, in company, without a change of expression."),
            ("went_home_with", "He Went Home With It", 2, 3, 3, 0, 1, ["inscription", "control"], "The sect elder never remembered accepting the fan. That is the technique."),
        ]),
    "weaponsmith_martial_family": dict(
        manual_id="no_crest_blade_record", name="No-Crest Blade Record", element="Metal",
        alignment="Neutral",
        blurb="The founder made swords for both sides of a sect war and was executed by the winners. The house has put nobody's crest on anything since, and teaches why first.",
        techniques=[
            ("plain_blade", "The Plain Blade", 0, 2, 5, 0, 0, ["forging", "melee"], "The best ordinary sword in the province, used the ordinary way."),
            ("both_sides", "Both Sides", 1, 3, 1, 3, 0, ["recovery", "forging"], "Steel cooled evenly, because it does not know who is holding it either."),
            ("no_crest", "No Crest", 2, 3, 2, 0, 2, ["forging", "control"], "Decline the quarrel and keep the customer. The family is still alive; the founder is not."),
        ]),
    "nether_market_house": dict(
        manual_id="back_room_ledger_primer", name="Back Room Ledger Primer", element="Water",
        alignment="Neutral",
        blurb="A respectable ink-seller's teaching with eighty years of a different trade written in the margins, in the same hand.",
        techniques=[
            ("two_ledgers", "Two Ledgers", 0, 2, 4, 0, 0, ["inscription", "concealment"], "Both true. Only one of them adds up in front of a stranger."),
            ("paid_in_kind", "Paid In Kind", 1, 3, 0, 3, 0, ["recovery", "inscription"], "The house's own restorative, invented because a physician asks where the money came from."),
            ("without_receipts", "Without Receipts", 2, 3, 3, 0, 1, ["inscription", "control"], "A seal that holds a thing shut and records nothing about what it was."),
        ]),
    "escort_martial_family": dict(
        manual_id="first_contract_array", name="First Contract Array", element="Earth",
        alignment="Orthodox",
        blurb="The first contract was a coffin, carried three hundred li down a road that was closed. It arrived. Every contract since has been the same contract.",
        techniques=[
            ("closed_road", "The Closed Road", 0, 2, 4, 0, 0, ["formation", "control"], "Lines laid at a halt, by people who expect to be moving again before they are finished."),
            ("three_hundred_li", "Three Hundred Li", 1, 3, 0, 4, 0, ["recovery", "formation"], "Recovery measured in distance rather than in hours, because the cargo does not stop."),
            ("it_arrived", "It Arrived", 2, 4, 2, 0, 2, ["formation", "control"], "The whole of the family's reputation, which is that the answer is always this one."),
        ]),
    "alchemy_family": dict(
        manual_id="one_in_ten_furnace_record", name="One-in-Ten Furnace Record", element="Fire",
        alignment="Orthodox",
        blurb="The house made its fortune on a fever cure and nearly lost it on the same cure. It has thrown away one batch in ten ever since, and teaches the throwing away first.",
        techniques=[
            ("low_flame", "Low Flame", 0, 2, 4, 0, 0, ["alchemy", "control"], "The heat the fever cure actually wanted, which is less than it looks like it wants."),
            ("tested_batch", "The Tested Batch", 1, 3, 0, 4, 0, ["recovery", "alchemy"], "A restorative nobody in this house will hand you untasted."),
            ("one_in_ten", "One in Ten", 2, 3, 2, 2, 1, ["alchemy", "control"], "Recognising the batch to destroy. The family paid for this one at a funeral."),
        ]),
    "noble_martial_clan": dict(
        manual_id="gate_without_asking_canon", name="Gate Without Asking Canon", element="Metal",
        alignment="Orthodox",
        blurb="Given to the clan's founder by an emperor who needed a family that would guard a gate without asking what was behind it. The canon does not say either.",
        techniques=[
            ("qin_draw", "The Qin Draw", 0, 2, 6, 0, 0, ["sword", "melee"], "A formal opening, taught to children of this house before they can lift the sword it was written for."),
            ("standing_post", "Standing Post", 1, 3, 1, 4, 0, ["recovery", "foundation"], "The clan's own restorative stance, held at a door for an entire watch."),
            ("not_asking", "Not Asking", 2, 4, 3, 0, 1, ["sword", "control"], "A guard that faces outward and is never, ever turned round."),
        ]),
}


def build(data: dict) -> tuple[int, int]:
    system = data["technique_system"]
    manuals, techniques, items = system["manuals"], system["techniques"], data["items"]
    lesson = data["birth_family_lesson"]
    added_manuals = added_techniques = 0

    for household, spec in HOUSEHOLDS.items():
        if household not in lesson:
            raise SystemExit(f"{household} is not a birth_family_lesson entry")
        mid = spec["manual_id"]
        item_id = f"{mid}_manual"
        technique_ids = []
        for suffix, name, mastery, qi, dmg, heal, suppress, tags, text in spec["techniques"]:
            tid = f"{mid}_{suffix}"
            if tid not in techniques:
                added_techniques += 1
            techniques[tid] = {
                "name": name, "manual": mid, "min_mastery": mastery, "qi_cost": qi,
                "vitality_cost": 0, "damage": dmg, "heal": heal, "suppress_turns": suppress,
                "karma_cost": 0, "exposure": 1, "tags": tags, "description": text,
            }
            technique_ids.append(tid)

        if mid not in manuals:
            added_manuals += 1
        manuals[mid] = {
            "name": spec["name"], "item_id": item_id, "alignment": spec["alignment"],
            # "Any", because creation chooses a path before the send-off: a
            # path-locked family manual would be wrong for six children in seven.
            # `manual_element` is the tree's one statement of what qi a method
            # draws - keywords in the name first, then a stable spread by id -
            # and `test_rc9_elemental_qi.py` holds every manual to it. Authoring
            # a prettier value beside it would be a second statement free to
            # disagree, which is the fault this release spends its length
            # removing. Where the fit matters, the name is what to change.
            "path": "Any", "grade": GRADE, "element": manual_element(mid, spec["name"]),
            "min_realm_index": 0,
            # No `sect` key at all. That is the finding, stated as content.
            "description": spec["blurb"], "techniques": technique_ids,
        }
        items[item_id] = {
            "name": f"{spec['name']} — Household Slip", "type": "manual", "manual_id": mid,
            "sect_value": 12, "base_price": 60,
            "description": f"The {spec['name']}, copied out in a relative's hand. It leaves the house with you.",
            "legal_status": "clean", "market_excluded": True,
        }
        lesson[household]["manual"] = mid

    return added_manuals, added_techniques


def main() -> int:
    data = json.loads(WORLD.read_text(encoding="utf-8"))
    m, t = build(data)
    WORLD.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Authored {len(HOUSEHOLDS)} household manuals ({m} new), {t} new techniques.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
