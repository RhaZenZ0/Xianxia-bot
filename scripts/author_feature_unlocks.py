#!/usr/bin/env python3
"""Author `feature_unlocks` into content/world.json (v1.0.9).

Reported from live play: *"it's become complex and overwhelming."* A character
three minutes old is shown **249 leaves across 67 pages in 16 hubs** - every
system the game has, all at once, with nothing saying which of them are for
them yet. Sect politics, territory war, caravan dispatch, boss raids and the
auction floor sit beside `cultivate` and `talk`, and none of them is *refused*:
they work, they are simply not what the first hour is about.

**This is a new rule, not an extension of the old one, and it is worth saying
so plainly.** `PROGRESSION_GATES` (v1.0.0-rc.32) hides a door **the engine
would refuse outright** - a Law before the realm that can hold one, a sect's
rooms to somebody in no sect - and CLAUDE.md states the limit: *"never a status
read or the door into the system, because a road nobody can see is a road
nobody learns exists."* A pacing curriculum hides doors that would have worked.
Three things keep that honest:

- **Nothing vanishes.** A gated page prints one collapsed line saying how many
  doors it is holding back and when the next opens; `/locked` lists every one
  with the realm that opens it. The rc.32 rule is kept in substance - the road
  is visible, it is just not in your way yet.
- **The slash command still works.** Gating is *advertising*, never a bound:
  `/auction` typed directly still runs, and the engine's own rules remain the
  only thing that refuses. A bound that lives in the client is not a bound
  (rc.48), and this release must not become the fifth instance.
- **The roster is content**, so a GM retunes it without a code change - the
  rule this tree already follows for `event_sites`, `forage_materials`,
  `beginner_path` and `world_era_cycles`.

**Gated by page, with per-leaf overrides**, because a page is the natural unit
of "a system" and a 67-entry roster is reviewable where a 249-entry one is not.
The overrides exist because several pages mix the first hour with the deep end:
`character / Overview` holds `sheet` beside `soul` and `inheritances`, and
`cultivation / Path` holds `aptitude root` - what you were born as - beside
`aptitude evolve`.

**Two floors are absolute.** Anything the beginner path or a household errand
needs stays at realm 0, or the curriculum would gate the very quests it exists
to make legible; and `reset` stays at realm 0, because the player most likely
to want it is the one who just found the game overwhelming.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "world.json"

# realm index -> what a page opened here is about. Names come from the ladder
# in `realms`; the gate holds these against it rather than trusting the comment.
#   0 Body Tempering      the first hour: cultivate, break through, explore, buy,
#                         craft, forge, gather, hunt, mine, quest - the owner's
#                         list (v1.2.0) - plus a companion and the body path
#   2 Foundation Establishment  everything else the game has: a sect's rooms,
#                         a party, the auction floor, the markets, the roads
#   3 Core Formation      the underworld, the caravans, a home of your own
#   4 Nascent Soul        holding ground: territory, war, a house, a partner
#   5 Soul Formation      what a life leaves behind
#   8 Spirit Body Transformation  the first realm of the Spiritual World: the
#                         qi body's levers (and the Laws, by their own floor)

# Page -> the realm it opens at. A page absent from here opens at 0.
#
# v1.2.0, on the owner's call: *"Cultivation, Breakthrough, Explore, Shop,
# Craft, Forge, Gather, Hunt, Mine, Quest until Foundation Establishment -
# these things are enough."* So everything outside that list waits for realm
# 2 at the earliest; the floors already above 2 stay where they were. Two
# things stay at 0 against the list, on the same call: `beast / Companions`
# (a companion is the one system the first hour has to be told about) and
# `cultivation / Body` (the body path is a way of cultivating, not a system
# beside it). `cultivation / Qi Body` waits for the Spiritual World, because
# meridians and purity are that world's game; the Laws wait for it too, by the
# content floor `law_system.normal_min_realm_index` rather than by an entry
# here (a second statement of one rule is the rc.39 fault).
PAGES: dict[str, int] = {
    "character / Afflictions": 2,
    "character / Consequences": 3,
    "character / Dao Partnership": 4,
    "character / Fate": 4,
    "character / Samsara": 5,
    "ascend / Perfection": 5,
    "cultivation / Qi Body": 8,
    "cultivation / Ghost": 2,
    "cultivation / Path": 2,
    "cultivation / Arts": 2,
    "items / Storage": 2,
    "items / Artifacts": 2,
    "items / Provenance": 2,
    "world / City": 2,
    "travel / Realm Capitals": 2,
    "travel / Teleportation Arrays": 2,
    "combat / Active Battle": 2,
    "combat / Duels": 2,
    "combat / Party": 2,
    "combat / Formations": 2,
    "combat / Boss Raids": 3,
    "combat / Bounty Hunter": 3,
    "economy / Local Market": 2,
    "economy / Auction House": 2,
    "economy / Merchants": 2,
    "economy / Trade": 2,
    "economy / Black Market": 3,
    "economy / Caravans": 3,
    "craft / Alchemy": 2,
    "craft / Profession": 2,
    "sect / Sect": 2,
    # `sect / Recruitment` stays at 0 (v1.1.0): Recommendation and Trial take
    # an argument, so a hub press is the only way to reach them, and
    # `road_to_a_sect` - which the path hands everybody - asks exactly that.
    "sect / Discipleship": 2,
    "sect / Holdings": 2,
    "sect / Territory": 4,
    "sect / War": 4,
    "family / Family": 2,
    "family / House": 4,
    "family / Legacy": 5,
    "abode / Property": 3,
    "abode / Access": 3,
    "innerworld / Personal World": 5,
    "realm / Secret Realms": 2,
    "realm / Spatial Keys": 2,
}

# Leaf -> the realm it opens at, overriding its page. Each says why.
LEAVES: dict[str, int] = {
    # `character / Overview` is the sheet and six readings of a life that has
    # not happened yet. The sheet is the first thing anybody opens.
    "sheet": 0,
    "lifespan": 0,
    "karma": 2,
    "reputation": 2,
    "daoheart": 2,
    "inheritances": 5,
    "soul": 5,
    # `character / Samsara` is gated at 5, but a reset is for the player who
    # has just decided this game is too much. It is never held back.
    "reset": 0,
    # Nothing on `cultivation / Cultivate` waits: the page opens at 0 and a
    # closed-door retreat is a way of cultivating, which the owner's list
    # names first. The banked insight stays for a second reason - every
    # qi-ladder crossing from 0 to 1 onward needs one or a Perfection, so
    # hiding it would leave a Body Tempering 9 with no visible way through.
    # `cultivation / Path` waits for realm 2, but what you were born as is
    # identity rather than a system (v1.0.13): the three readings stay.
    "aptitude root": 0,
    "aptitude bloodline": 0,
    "aptitude physique": 0,
    # The manual the household's lesson hands over is read on `Arts`.
    "manual list": 0,
    # Alchemy's page waits, but the first hour gathers, treats what a fight
    # left, purges what a pill left, reads a slip and sits an examination.
    "condition treat": 0,
    "alchemy forage": 0,
    "alchemy purge": 0,
    "learn": 0,
    "profession exam": 0,
    # The city's page waits, but its reads and its board are the first
    # hour's: the owner's list says Quest, and the board is where they are.
    "city look": 0,
    "city inn": 0,
    "city rumours": 0,
    "city board": 0,
    "city accept": 0,
    # The household's doors and its view: the path starts inside, steps out
    # through Leave, and comes back in through Enter.
    "family view": 0,
    "family enter": 0,
    "family leave": 0,
    # Reads that are a page's own front door without being named `status`.
    "sect recruitment info": 0,
    "bounty": 0,
    "provenance": 0,
    # The hidden sect's door is its own karma gate, not a realm one; the
    # recruitment page it sits on opens at 0 since v1.1.0, and this does not.
    "sect shadow": 2,
    "boss list": 2,
    "perfect info": 3,
}

# `is_status_read` is the rule rather than a list, because the list is what
# drifted: until v1.0.13 `LEAVES` carried six status reads at 0 under a comment
# reading *"Every page's own status stays"* and eleven more at 1 or 2 **in the
# same block, under that same comment**. It lives in `app/rules/feature_unlocks`
# since v1.2.0, where the menu reads it too, and is imported here so the
# generator and the menu cannot hold two ideas of what a status read is.
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.rules.feature_unlocks import STATUS_LEAF, is_status_read  # noqa: E402

__all__ = ["STATUS_LEAF", "is_status_read", "build", "PAGES", "LEAVES"]


def build(pages: dict[str, list[str]]) -> dict[str, object]:
    """`{leaf path: realm}` for every leaf the curriculum holds back.

    Takes the live page->leaves map rather than a second copy of it, so a leaf
    that moves between pages is carried by the move and a page that no longer
    exists is visible to the gate as a roster entry nothing matches.
    """
    unlocks: dict[str, int] = {}
    for page, leaves in pages.items():
        floor = int(PAGES.get(page, 0))
        for leaf in leaves:
            realm = 0 if is_status_read(leaf) else int(LEAVES.get(leaf, floor))
            if realm > 0:
                unlocks[leaf] = realm
    return {
        "leaves": dict(sorted(unlocks.items())),
        "pages": dict(sorted((p, r) for p, r in PAGES.items() if r > 0)),
    }


def live_pages() -> dict[str, list[str]]:
    """The hubs as they are registered, imported the way the harnesses do."""
    import os
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DISCORD_TOKEN", "authoring")
    os.environ.setdefault("GUILD_ID", "123456789012345678")
    os.environ.setdefault("ENGINE_AUTH_TOKEN", "authoring-token-1234567890")
    os.environ.setdefault("DATABASE_PATH", "data/authoring.sqlite3")
    os.environ.setdefault("HEALTH_PORT", "18099")
    import app.bot.surface  # noqa: F401  (registers the hubs)
    from app.bot.hubs import REGISTERED_HUBS, _leaf_actions

    out: dict[str, list[str]] = {}
    for definition in REGISTERED_HUBS:
        if definition.name == "admin":
            continue
        for page in definition.pages:
            key = f"{definition.name} / {page.label}"
            out[key] = sorted(a.path.lstrip("/") for a in _leaf_actions(page))
    return out


def main() -> int:
    pages = live_pages()
    built = build(pages)
    raw = json.loads(CONTENT.read_text(encoding="utf-8"))
    raw["feature_unlocks"] = built
    CONTENT.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    leaves = built["leaves"]
    total = sum(len(v) for v in pages.values())
    by_realm: dict[int, int] = {}
    for realm in leaves.values():
        by_realm[realm] = by_realm.get(realm, 0) + 1
    print(f"wrote feature_unlocks: {len(leaves)} of {total} leaves gated")
    print("  open at realm 0: ", total - len(leaves))
    for realm in sorted(by_realm):
        print(f"  opens at realm {realm}: {by_realm[realm]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
