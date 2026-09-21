"""`/locked`: the roads you have not reached yet, and the realm each opens at.

This is the half that keeps v1.0.9 honest. The curriculum holds back 139 of the
game's 249 leaves from a fresh cultivator, and `PROGRESSION_GATES` (rc.32) sets
the limit that makes such hiding allowable at all: *"a road nobody can see is a
road nobody learns exists."* A gated page prints one collapsed line rather than
a column of padlocks - which is the whole point, the wall of rows was the
complaint - so the full list has to live somewhere, and this is where.

It reads the same roster the panels read, through the same pure helper, so the
card and the panels cannot disagree about what is shut. Grouped by the realm
that opens each door, because "what do I get next" is the question a player
actually has, and the nearest band is printed first and in full.
"""

from __future__ import annotations

import discord

from ..hubs import REGISTERED_HUBS, _leaf_actions
from ..registry import registered_root_command
from ..runtime import DB, WORLD, require_character
from ..services import GUILD
from ...rules import feature_unlocks as unlocks

# Discord's message limit is 2000; a realm-0 cultivator has 139 doors waiting,
# so the card names every band and prints the doors of the nearest few.
BANDS_PRINTED_IN_FULL = 2


def _labels() -> dict[str, str]:
    """`leaf path -> "Hub — Label"`, off the live hub definitions.

    Read rather than restated: a leaf that is renamed or moved to another page
    takes this card's wording with it, which is the rule `test_hint_paths.py`
    already holds for every printed path in the tree.
    """
    out: dict[str, str] = {}
    for definition in REGISTERED_HUBS:
        if definition.name == "admin":
            continue
        for page in definition.pages:
            for action in _leaf_actions(page):
                out[action.path.lstrip("/")] = f"{page.label} — {action.label}"
    return out


@registered_root_command(
    name="locked",
    description="What opens as you cultivate, and the realm each one needs",
    guild=GUILD,
)
async def locked(interaction: discord.Interaction) -> None:
    character = await require_character(interaction)
    if not character:
        return
    realm = int(character.get("realm_index") or 0)
    roster = WORLD.data.get("feature_unlocks") or {}
    waiting = unlocks.locked_leaves(roster, realm)
    labels = _labels()

    if not waiting:
        await interaction.response.send_message(
            f"🗝️ **Everything is open to you.** You stand at **{WORLD.realm_name(realm)}**, "
            "and nothing in the world is waiting on your cultivation any more.",
            ephemeral=False,
        )
        return

    bands: dict[int, list[str]] = {}
    for path, opens_at in waiting.items():
        bands.setdefault(int(opens_at), []).append(labels.get(path, path))

    lines = [
        f"🗝️ **What opens as you cultivate** — you stand at **{WORLD.realm_name(realm)}**.",
        f"-# {len(waiting)} doors are waiting. They are not refusals: a road you can see is a road "
        "you can walk toward.",
    ]
    for index, opens_at in enumerate(sorted(bands)):
        doors = sorted(bands[opens_at])
        heading = f"**{WORLD.realm_name(opens_at)}** — {len(doors)} door{'' if len(doors) == 1 else 's'}"
        if index < BANDS_PRINTED_IN_FULL:
            lines.append(heading)
            lines.append("-# " + " · ".join(doors))
        else:
            lines.append(f"{heading} -# (reach {WORLD.realm_name(opens_at - 1)} to see them listed)")
    await interaction.response.send_message("\n".join(lines)[:1990], ephemeral=False)
