"""What the first hour is shown, and what waits for a realm (v1.0.9).

Reported from live play: *"it's become complex and overwhelming."* A character
three minutes old met 249 leaves across 67 pages in 16 hubs - every system the
game has, at once. `PROGRESSION_GATES` (v1.0.0-rc.32) already hides a door the
engine **would refuse outright**; what this adds is a door that would have
worked and is simply not what the first hour is about.

That is a different rule from rc.32's, and the three things that keep it honest
are written up in `scripts/author_feature_unlocks.py`. The one that shapes this
module: **gating is advertising, never a bound.** Nothing here is asked on a
press, and the slash command keeps working - a bound that lives in the client
is not a bound (rc.48), and the engine's own rules stay the only refusal.

Pure, and takes its roster injected, because `WORLD` is built in
`app/bot/runtime.py` and `test_app_layout.py` puts `rules` at the bottom - the
reason `describe_era` (v1.0.7) and `narrator.py`'s `npc_resolver` (rc.27) are
shaped the same way.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

# A leaf whose path ends here is a page's own status read, and a status read
# never waits for a realm (v1.0.13). Stated once, here, and imported by the
# authoring script: it used to live in the script, and the menu (v1.2.0) needs
# the same predicate to decide whether a hub still has a lever open on it.
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


def locked_leaves(roster: Mapping[str, object] | None, realm_index: int) -> dict[str, int]:
    """`leaf path -> the realm that opens it`, for what this realm has not reached.

    A roster that is absent, empty or unreadable locks **nothing**. That
    direction is deliberate and is the same call `maintenance.py` makes about
    its flag: a presentation filter that fails towards hiding would leave a
    player looking at an empty game with no way to tell that from a correct
    one, while failing towards showing is the surface this release started from
    and is merely busy.
    """
    if not isinstance(roster, Mapping):
        return {}
    leaves = roster.get("leaves")
    if not isinstance(leaves, Mapping):
        return {}
    realm = int(realm_index)
    out: dict[str, int] = {}
    for path, opens_at in leaves.items():
        try:
            needs = int(opens_at)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if needs > realm:
            out[str(path)] = needs
    return out


def hidden_hubs(
    roster: Mapping[str, object] | None,
    realm_index: int,
    pages: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict[str, int]:
    """`hub -> the realm that first opens a lever on it`, for the hubs a
    player at this realm has nothing to *do* in yet (v1.2.0).

    Player feedback: *"Interface is overwhelming ... I still forget where to
    go what to do."* Sixteen hubs on the menu, and at Body Tempering four of
    them held nothing but status reads and a collapsed line. A hub is hidden
    from the menu only when every leaf on every one of its pages is either a
    status read or locked - a single open lever keeps it on the board, because
    a hub with one thing to do is a hub worth opening. `pages` is the live
    `{hub: {page: [leaf paths]}}` map rather than a copy, so a leaf that moves
    hubs is carried by the move.

    Fails the way `locked_leaves` fails: an absent roster locks nothing, so
    nothing is hidden. And it is advertising, never a bound - the hub's own
    slash command still opens it, and the menu names what it left off.
    """
    locked = locked_leaves(roster, realm_index)
    if not locked:
        return {}
    out: dict[str, int] = {}
    for hub, hub_pages in pages.items():
        opens_at: int | None = None
        any_lever = False
        for leaves in hub_pages.values():
            for leaf in leaves:
                path = str(leaf).lstrip("/")
                if is_status_read(path):
                    continue
                any_lever = True
                needs = locked.get(path)
                if needs is None:
                    opens_at = None
                    break
                opens_at = needs if opens_at is None else min(opens_at, needs)
            else:
                continue
            break
        else:
            if any_lever and opens_at is not None:
                out[str(hub)] = int(opens_at)
    return out


def collapsed_menu_line(labels: Sequence[str], realm_name: "object" = None) -> str:
    """The one line the menu prints for the hubs it left off (v1.2.0).

    The road stays visible (rc.32): the hubs are named, the nearest realm that
    opens one is named, and the line says their slash commands still work,
    because hiding is advertising and never a bound. `/locked` is where the
    doors themselves are listed, and the line names it.
    """
    names = [str(label) for label in labels if str(label)]
    if not names:
        return ""
    where = f" — the nearest at **{realm_name}**" if realm_name else ""
    listed = ", ".join(f"**{name}**" for name in names)
    return (f"-# 🔒 {listed} open as you cultivate{where}. "
            "Their slash commands still work; **/locked** lists every door.")


def next_unlock_realm(locked: Mapping[str, int]) -> int | None:
    """The nearest realm that opens anything, or None when nothing is waiting.

    What the collapsed line names. "6 more open as you advance" says nothing a
    player can act on; "the next at Qi Refining" is a reason to cultivate.
    """
    if not locked:
        return None
    return min(int(v) for v in locked.values())


def unlock_summary(locked: Mapping[str, int], realm_name: "object" = None) -> str:
    """The one line a gated page prints in place of its locked rows.

    Deliberately one line however many doors are held back: the whole finding
    is that a page of rows nobody can use yet is what made this game read as
    overwhelming, and replacing them with an equally long column of padlocks
    would move the noise rather than remove it. `/locked` is where the full
    list lives, and the line names it.
    """
    if not locked:
        return ""
    count = len(locked)
    doors = "door" if count == 1 else "doors"
    where = f" — the next at **{realm_name}**" if realm_name else ""
    return f"-# 🔒 {count} more {doors} here open as you cultivate{where}. **/locked** lists them."
