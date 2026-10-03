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

**Each player is shown what is theirs** (v1.20.2, asked for the same day). Discord
cannot hide a command per member, so the card does what `/menu` does, by
asking the panels' own providers rather than a rule of its own:
`menu_shape` for the hubs the menu leaves off, `not_yet_unlocked` for the
leaves the curriculum holds back, and `hidden_actions` for another path's
doors (`NOT_YOUR_PATH` only - a padlock for where you stand is not this card's
business, which is a reference rather than a room). A group's subcommand *is*
the hub leaf (`_tree_command` registers the very group the pages are built
from), so `/` + its qualified name is the leaf path. `/begin` heads the card
for somebody with no character and is never shown to somebody with one. What
the curriculum held back is named in one line, so nothing vanishes silently
(rc.32); another path's doors are not, because nobody can walk to them
(v1.13.0). Every provider already fails towards showing everything, which is
the card's safe side too. Typed, every command still works: the engine stays
the one place that refuses.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import discord

from ..cards import Card, card_view
from ..hubs import NOT_YOUR_PATH, REGISTERED_HUBS, hidden_actions, menu_shape, not_yet_unlocked
from ..registry import registered_root_command
from ..runtime import DB, log
from ..services import GUILD

_DAILY: tuple[str, ...] = ()

ADMIN_COMMANDS = frozenset({"admin"})
#: The one command somebody with no character can use, and the one somebody
#: with a character never can (an account holds one cultivator).
BEGIN_COMMAND = "begin"


def register_daily_actions(names: Sequence[str]) -> None:
    """Called by `surface` with `DAILY_ACTIONS`, so the card heads its list
    with the five a day is made of, in the menu's own order."""
    global _DAILY
    _DAILY = tuple(names)


def _is_group(command: Any) -> bool:
    return callable(getattr(command, "walk_commands", None))


def _subcommand_paths(group: Any) -> list[tuple[str, str]]:
    """A group's leaves as `(leaf path, what a player types after the group's
    name)`: `("/stall board", "board")`, or `manor upgrade` for a nested group."""
    out: list[tuple[str, str]] = []
    for sub in group.walk_commands():
        if _is_group(sub):
            continue
        qualified = str(getattr(sub, "qualified_name", "") or sub.name)
        out.append(("/" + qualified, qualified.split(" ", 1)[1] if " " in qualified else qualified))
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
    has_character: bool = True,
    hidden_hubs: Iterable[str] = (),
    held_back: Iterable[str] = (),
    not_your_path: Iterable[str] = (),
) -> Card:
    """The overview, from whatever the tree holds, as this player sees it.

    Headings, each in the tree's own order: `/begin` alone for somebody with no
    character, the daily five, the hub panels, the other single commands, and
    the command groups with their leaves. A heading with nothing under it is
    left off. `hidden_hubs` are the hubs the menu leaves off; `held_back` and
    `not_your_path` are leaf paths (`/stall open`). What was held back is named
    in one line; another path's commands are simply absent."""
    hub_names = set(hubs)
    leave_off_hubs = set(hidden_hubs)
    held = set(held_back)
    foreign = set(not_your_path)

    def shown(path: str) -> bool:
        return path not in held and path not in foreign

    listed = [c for c in commands if is_admin or c.name not in ADMIN_COMMANDS]
    begin = next((c for c in listed if c.name == BEGIN_COMMAND), None)
    listed = [c for c in listed if c.name != BEGIN_COMMAND]

    left_off: list[str] = []
    held_count = 0
    kept: list[Any] = []
    leaves: dict[str, list[str]] = {}
    for command in listed:
        path = "/" + command.name
        if command.name in hub_names and command.name in leave_off_hubs:
            left_off.append(path)
            continue
        if _is_group(command):
            subs = _subcommand_paths(command)
            open_subs = [typed for leaf, typed in subs if shown(leaf)]
            held_count += sum(1 for leaf, _ in subs if leaf in held and leaf not in foreign)
            if subs and not open_subs:
                if any(leaf in held for leaf, _ in subs):
                    left_off.append(path)
                continue
            leaves[command.name] = open_subs
        elif not shown(path):
            if path in held and path not in foreign:
                left_off.append(path)
            continue
        kept.append(command)

    by_name = {c.name: c for c in kept}
    every_day = [by_name[name] for name in daily if name in by_name and not _is_group(by_name[name])]
    taken = {c.name for c in every_day}
    panels = [c for c in kept if c.name in hub_names and c.name not in taken]
    taken |= {c.name for c in panels}
    groups = [c for c in kept if _is_group(c) and c.name not in taken]
    taken |= {c.name for c in groups}
    singles = [c for c in kept if c.name not in taken]

    card = Card(
        title="⌨️ Commands",
        description=(
            "Every slash command in this server. Most of the game lives in the **panels**: "
            "`/menu` opens all of them, and a panel's buttons do what the commands below do."
        ),
        colour=0x5B8DEF,
    )
    if begin is not None and not has_character:
        card.add_field(name="🌱 Start here", value=_line(begin), inline=False)
    if every_day:
        card.add_field(name="⚡ Every day", value="\n".join(_line(c) for c in every_day), inline=False)
    if panels:
        card.add_field(name="🧭 Panels", value="\n".join(_line(c) for c in panels), inline=False)
    if singles:
        card.add_field(name="✳️ Commands", value="\n".join(_line(c) for c in singles), inline=False)
    if groups:
        blocks = []
        for group in groups:
            open_subs = leaves.get(group.name) or []
            blocks.append(_line(group) + (f"\n-# {' · '.join(open_subs)}" if open_subs else ""))
        card.add_field(name="📂 Command groups", value="\n".join(blocks), inline=False)
    if left_off or held_count:
        parts = []
        if left_off:
            parts.append(", ".join(left_off))
        if held_count:
            parts.append(f"{held_count} subcommand{'' if held_count == 1 else 's'}")
        card.add_field(
            name="🔒 Not yet",
            value=f"Left off until you cultivate further: {' and '.join(parts)}. `/locked` lists them.",
            inline=False,
        )
    card.set_footer(text="/locked lists what opens as you cultivate · /cooldowns says what is ready now")
    return card


async def _has_character(user_id: int) -> bool:
    """Fails towards having one: a card that offered `/begin` to a cultivator
    because a read broke would send them to a command that refuses."""
    try:
        return bool(await DB.get_character(user_id))
    except Exception:
        log.exception("Character unavailable for /commands")
        return True


@registered_root_command(
    name="commands",
    description="Every slash command in this server, and what each one does",
    guild=GUILD,
)
async def command_overview(interaction: discord.Interaction) -> None:
    member = interaction.user
    permissions = getattr(member, "guild_permissions", None)
    has_character = await _has_character(interaction.user.id)
    shape = await menu_shape(interaction) if has_character else {}
    held = await not_yet_unlocked(interaction) if has_character else {}
    hidden = await hidden_actions(interaction) if has_character else {}
    card = overview_card(
        interaction.client.tree.get_commands(guild=GUILD),
        hubs=(definition.name for definition in REGISTERED_HUBS),
        daily=_DAILY,
        is_admin=bool(getattr(permissions, "administrator", False)),
        has_character=has_character,
        hidden_hubs=dict(shape.get("hidden_hubs") or {}),
        held_back=held,
        not_your_path={path for path, reason in hidden.items() if reason == NOT_YOUR_PATH},
    )
    await interaction.response.send_message(view=card_view(card))
