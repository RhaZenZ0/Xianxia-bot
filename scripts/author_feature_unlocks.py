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
#   0 Body Tempering      the first hour: cultivate, talk, walk, make, fight
#   1 Qi Refining         a companion, a rival, a sect worth asking about
#   2 Foundation Establishment  a sect's rooms, a party, the auction floor
#   3 Core Formation      the underworld, the roads, a home of your own
#   4 Nascent Soul        holding ground: territory, war, a house, a partner
#   5 Soul Formation      what a life leaves behind

# Page -> the realm it opens at. A page absent from here opens at 0.
PAGES: dict[str, int] = {
    "character / Dao Partnership": 4,
    "character / Consequences": 3,
    "character / Fate": 4,
    "character / Samsara": 5,
    "ascend / Perfection": 5,
    "items / Artifacts": 2,
    "items / Provenance": 1,
    "combat / Duels": 1,
    "combat / Party": 2,
    "combat / Formations": 2,
    "combat / Boss Raids": 3,
    "combat / Bounty Hunter": 3,
    "economy / Black Market": 3,
    "economy / Auction House": 2,
    "economy / Merchants": 2,
    "economy / Caravans": 3,
    # Absent, and deliberately: `cultivation / Path`, `cultivation / Qi Body`
    # and `beast / Companions` opened at 2/2/1 until v1.0.13 and are reported
    # from live play - "I can view only like half the menu". Path is what you
    # were born with, which this file already called identity rather than a
    # system; Qi Body is named on the cultivation card a realm-0 player reads
    # every session ("20/108 meridians"), so hiding its levers advertised a
    # thing and then hid the door to it; and a companion is the one thing the
    # first hour had to be *told about* rather than shown.
    "sect / Sect": 2,
    "sect / Recruitment": 1,
    "sect / Discipleship": 2,
    "sect / Holdings": 2,
    "sect / Territory": 4,
    "sect / War": 4,
    "family / House": 4,
    "family / Legacy": 5,
    "abode / Property": 3,
    "abode / Access": 3,
    "innerworld / Personal World": 5,
    "realm / Secret Realms": 1,
    "realm / Spatial Keys": 1,
    "travel / Realm Capitals": 1,
    "travel / Teleportation Arrays": 2,
}

# Leaf -> the realm it opens at, overriding its page. Each says why.
LEAVES: dict[str, int] = {
    # `character / Overview` is the sheet and six readings of a life that has
    # not happened yet. The sheet is the first thing anybody opens.
    "sheet": 0,
    "lifespan": 0,
    "karma": 1,
    "reputation": 1,
    "daoheart": 2,
    "inheritances": 5,
    "soul": 5,
    # `character / Samsara` is gated at 5, but a reset is for the player who
    # has just decided this game is too much. It is never held back.
    "reset": 0,
    # The hidden sect's door is its own karma gate, not a realm one, and the
    # recruitment page it sits on opens at 1.
    "sect shadow": 2,
    # Reads that are a page's own front door without being named `status`.
    # Each is the thing that tells a player the system is there at all.
    "sect recruitment info": 0,
    "auction browse": 1,
    "boss list": 2,
    "perfect info": 3,
    "bounty": 1,
    "provenance": 1,
}

# A leaf whose path ends here is a status read, and a status read is never
# held back (`STATUS_READS_ALWAYS_OPEN`, below). It is a rule rather than a
# list because the list is what drifted: until v1.0.13 `LEAVES` carried six
# status reads at 0 under a comment reading *"Every page's own status stays"*
# and eleven more at 1 or 2 **in the same block, under that same comment**,
# while eight others were never listed at all and inherited a page floor of 2.
# Nineteen of them were held back from a realm-0 player, which is the opposite
# of what all three statements of this rule say - CLAUDE.md's v1.0.9 section,
# this file's own docstring, and the gate's docstring.
STATUS_LEAF = "status"


def is_status_read(leaf: str) -> bool:
    """Whether this leaf is a page's own status read, which never waits.

    rc.32 states the limit the whole curriculum inherits: *"never a status
    read or the door into the system, because a road nobody can see is a road
    nobody learns exists."* A lever can wait for a realm; the readout that
    says the system exists cannot, or a player meets an empty page rather than
    a locked one.
    """
    return str(leaf).split()[-1:] == [STATUS_LEAF]


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
