"""`/commands`: every slash command the server has, on one card (v1.20.1).

Asked for as *"A command overview"*. The commands were written down in three
places and none of them was a list: `#xianxia-info` names five, `/menu` draws
the hubs as buttons, and the rest - the daily five, `/stall`, `/boss`,
`/profession`, `/flame`, `/breakthrough` - were each announced once in a
release note and then left for a player to find by typing a slash.

**The card reads the command tree, never a list of its own.** What Discord
offers a player is exactly what `client.tree` holds, so the overview is built
from `tree.get_commands(...)` and each command's own description. A list kept
here would be the fifth reader of the tree tuple v1.0.12 retired, and a
command added tomorrow would be missing from it until somebody noticed.

Two things are injected rather than imported, because `surface` imports this
package and not the other way round: which roots are the daily five (the
order the menu draws them in), through :func:`register_daily_actions`. An
unregistered list loses only the "every day" heading - those commands still
appear, under the rest.

`/admin` is shown only to an administrator: the command is registered for the
whole guild, and listing a door to somebody it will refuse is the thing rc.46
forbids a surface to do.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import discord

from ..cards import Card, card_view
from ..hubs import REGISTERED_HUBS
from ..registry import registered_root_command
from ..services import GUILD

_DAILY: tuple[str, ...] = ()

ADMIN_COMMANDS = frozenset({"admin"})


def register_daily_actions(names: Sequence[str]) -> None:
    """Called by `surface` with `DAILY_ACTIONS`, so the card heads its list
    with the five a day is made of, in the menu's own order."""
    global _DAILY
    _DAILY = tuple(names)


def _is_group(command: Any) -> bool:
    return callable(getattr(command, "walk_commands", None))


def _subcommand_names(group: Any) -> list[str]:
    """A group's leaves as a player types them after the group's name:
    `board`, `buy`, or `manor upgrade` for a nested group."""
    out: list[str] = []
    for sub in group.walk_commands():
        if _is_group(sub):
            continue
        qualified = str(getattr(sub, "qualified_name", "") or sub.name)
        out.append(qualified.split(" ", 1)[1] if " " in qualified else qualified)
    return out


#: Discord cuts a slash command's description at this many characters, and a
#: hub's is built from its longer panel description, so it arrives mid-word.
DESCRIPTION_LIMIT = 100


def _description(command: Any) -> str:
    """The command's own description, with a mid-word cut tidied to a word."""
    text = str(getattr(command, "description", "") or "").strip()
    if len(text) >= DESCRIPTION_LIMIT and not text.endswith((".", "!", "?")):
        text = text[: text.rfind(" ")].rstrip(" ,;:—-") + "…" if " " in text else text
    return text


def _line(command: Any) -> str:
    description = _description(command)
    return f"`/{command.name}` — {description}" if description else f"`/{command.name}`"


def overview_card(
    commands: Iterable[Any],
    *,
    hubs: Iterable[str],
    daily: Sequence[str] = (),
    is_admin: bool = False,
) -> Card:
    """The overview, from whatever the tree holds.

    Four headings, each in the tree's own order: the daily five, the hub
    panels, the other single commands, and the command groups with their
    leaves. A heading with nothing under it is left off."""
    hub_names = set(hubs)
    listed = [c for c in commands if is_admin or c.name not in ADMIN_COMMANDS]
    by_name = {c.name: c for c in listed}

    every_day = [by_name[name] for name in daily if name in by_name and not _is_group(by_name[name])]
    taken = {c.name for c in every_day}
    panels = [c for c in listed if c.name in hub_names and c.name not in taken]
    taken |= {c.name for c in panels}
    groups = [c for c in listed if _is_group(c) and c.name not in taken]
    taken |= {c.name for c in groups}
    singles = [c for c in listed if c.name not in taken]

    card = Card(
        title="⌨️ Commands",
        description=(
            "Every slash command in this server. Most of the game lives in the **panels**: "
            "`/menu` opens all of them, and a panel's buttons do what the commands below do."
        ),
        colour=0x5B8DEF,
    )
    if every_day:
        card.add_field(name="⚡ Every day", value="\n".join(_line(c) for c in every_day), inline=False)
    if panels:
        card.add_field(name="🧭 Panels", value="\n".join(_line(c) for c in panels), inline=False)
    if singles:
        card.add_field(name="✳️ Commands", value="\n".join(_line(c) for c in singles), inline=False)
    if groups:
        blocks = []
        for group in groups:
            leaves = _subcommand_names(group)
            blocks.append(_line(group) + (f"\n-# {' · '.join(leaves)}" if leaves else ""))
        card.add_field(name="📂 Command groups", value="\n".join(blocks), inline=False)
    card.set_footer(text="/locked lists what opens as you cultivate · /cooldowns says what is ready now")
    return card


@registered_root_command(
    name="commands",
    description="Every slash command in this server, and what each one does",
    guild=GUILD,
)
async def command_overview(interaction: discord.Interaction) -> None:
    member = interaction.user
    permissions = getattr(member, "guild_permissions", None)
    card = overview_card(
        interaction.client.tree.get_commands(guild=GUILD),
        hubs=(definition.name for definition in REGISTERED_HUBS),
        daily=_DAILY,
        is_admin=bool(getattr(permissions, "administrator", False)),
    )
    await interaction.response.send_message(view=card_view(card))
