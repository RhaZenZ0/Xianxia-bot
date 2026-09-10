from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import discord
from discord import app_commands

from .registry import ACTIONS

log = logging.getLogger("xianxia.hubs")

_MENTION_ID = re.compile(r"(?:<@!?|<#)?(\d{15,22})>?$")
_RANGE_ANNOTATION = re.compile(r"Range\[(int|float)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")


_HUB_COLOURS = {
    "character": 0x6C7A89, "quest": 0xA87D32, "cultivation": 0x5B8C5A,
    "items": 0x8A6D3B, "npc": 0x6D78A8, "world": 0x3F7F73,
    "travel": 0x557A95, "combat": 0x9A4A4A, "economy": 0xA5863B,
    "craft": 0x8B6F47, "beast": 0x6B875F, "sect": 0x815D73,
    "family": 0x8A694D, "abode": 0x587A63, "innerworld": 0x5D5A91,
    "realm": 0x744F8A, "admin": 0xB23A48,
}

_HUB_ICONS = {
    "character": "🧑", "quest": "☯️", "cultivation": "🧘", "items": "🎒",
    "npc": "👥", "world": "🌍", "travel": "🗺️", "combat": "⚔️",
    "economy": "💰", "craft": "🛠️", "beast": "🐉", "sect": "🏯",
    "family": "🏠", "abode": "🏡", "innerworld": "🌌", "realm": "🌀",
    "admin": "🛡️",
}

_QUICK_ACTION_LIMIT = 3

# --- Components V2 "action list" hub layout ---------------------------------
# discord.py 2.6+ exposes LayoutView/Container/Section, which allow one visible,
# individually tappable row per action instead of hiding every action behind a
# dropdown.  requirements.txt pins 2.7.1, but the layout is feature-detected
# rather than assumed: on an older discord.py these names are simply missing and
# every hub keeps the classic embed panel instead of raising at import time.
_LAYOUT_COMPONENT_NAMES = (
    "LayoutView", "Container", "Section", "TextDisplay", "Separator", "ActionRow",
)
LAYOUT_COMPONENTS_AVAILABLE = all(
    hasattr(discord.ui, name) for name in _LAYOUT_COMPONENT_NAMES
)
# Hubs rendered with the new layout.  Add or remove a name here to migrate a hub
# or roll it back; any hub not listed keeps the classic embed + two-dropdown
# panel, so this set is the whole rollout switch and the whole rollback switch.
#
# Rolled out in stages, easiest shape first, so a problem would surface on the
# simplest hubs before reaching the ones that stress the component budget:
#   1. multi-page, no page over 5 actions - nothing chunks
#   2. single-page hubs - no Prev/Next at all; abode and family also chunk
#   3. multi-page hubs that chunk, or sit right on the 8-action limit
#   4. admin - last, because it is how the server is operated and because it
#      needs the administrator re-check in interaction_check below
LAYOUT_HUB_NAMES: set[str] = {
    # stage 1
    "character", "npc", "world", "travel", "craft", "realm", "items", "combat", "economy",
    # stage 2
    "innerworld", "beast", "abode", "family",
    # stage 3
    "quest", "cultivation", "sect",
    # stage 4
    "admin",
}
# Discord counts every component in a message, nested ones included, against a
# limit of 40.  With no system select row the chrome costs 12 at worst
# (container, header, three separators, page text, and a five-button control
# row) and each action row costs 3 (Section + TextDisplay + accessory Button).
# 9 would technically fit at 39/40; 8 lands the busiest page in the game
# (/sect: 25 actions across three subgroups) at 36 and keeps headroom in case
# Discord ever counts a nested component differently.  Longer pages chunk.
_LAYOUT_ACTION_LIMIT = 8

_DANGER_ACTION_WORDS = frozenset({
    "abandon", "clear", "close", "delete", "destroy", "disband", "kill",
    "leave", "purge", "reject", "remove", "reset", "sever", "withdraw",
})
_SUCCESS_ACTION_WORDS = frozenset({
    "accept", "activate", "begin", "buy", "create", "enter", "equip",
    "join", "open", "repair", "sell", "start", "train", "upgrade",
})

_PAGE_EMOJIS = {
    "sheet": "📜", "gender": "🪪", "lifespan": "⌛", "karma": "☯️", "fate": "🧧",
    "bond": "🤝", "daoheart": "💠", "reputation": "🏷️", "grudges": "🔥", "crime": "⚖️",
    "bounty": "🎯", "inheritances": "📚", "effects": "✨", "specialeffects": "🌀",
    "condition": "🩹", "soul": "🕯️", "afterlife": "☸️", "reincarnate": "🌱",
    "breakthrough": "⬆️", "perfect": "💎", "bodyperfect": "🥋", "tribulation": "⚡",
    "cultivate": "🧘", "seclusion": "🚪", "body": "🥋", "aptitude": "🧬", "law": "📖",
    "manual": "📚", "conceal": "🌫️", "profession": "🛠️", "inventory": "🎒", "storage": "📦",
    "use": "🧪", "equipment": "🛡️", "artifact": "🔮", "provenance": "🔎", "npcinfo": "👤",
    "talk": "💬", "sense": "👁️", "world": "🌍", "explore": "🧭", "hunt": "🐾",
    "worldevents": "🌩️", "civilization": "🏘️", "scene": "🎭", "era": "🌌", "time": "🕰️",
    "rulers": "👑", "worldrules": "📜", "travel": "🗺️", "realmhub": "🏙️", "array": "🌀",
    "battle": "⚔️", "duel": "🤺", "party": "👥", "formation": "🧭", "boss": "🐲", "hunter": "🎯",
    "wallet": "💰", "market": "🏪", "blackmarket": "🌑", "auction": "🔨", "caravan": "🐫",
    "alchemy": "⚗️", "craft": "🛠️", "beast": "🐉", "sect": "🏯", "territory": "🚩", "war": "⚔️",
    "family": "🏠", "abode": "🏡", "innerworld": "🌌", "secretrealm": "🌀", "spatialkey": "🗝️",
    "server": "⚙️", "player": "🧑", "simulation": "🧠", "admin": "🛡️",
}


def _page_emoji(key: str) -> str:
    return _PAGE_EMOJIS.get(str(key), "🔹")


def _hub_icon(name: str) -> str:
    return _HUB_ICONS.get(str(name), "🎮")


def _action_emoji(action: "HubAction") -> str:
    return _page_emoji(str(getattr(action.command, "name", "")))


_STATUS_BAR = re.compile(r"[▰▱]{2,}")
_LAYOUT_BAR_SEGMENTS = 5


def _compact_status_value(value: object) -> str:
    """Flatten a status value onto one line and shorten any progress bar.

    Status values are built for embed fields, where a 10-segment bar sits alone
    in its own box. Inside the layout's header it shares a line with its label
    and numbers, and at that width ten segments render as one solid rule rather
    than a meter - especially at 100%, where every cell is filled. Halving it
    keeps the shape readable without changing what it means.
    """
    text = " ".join(str(value).split())

    def shrink(match: re.Match[str]) -> str:
        bar = match.group(0)
        filled = round(bar.count("▰") / len(bar) * _LAYOUT_BAR_SEGMENTS)
        return "▰" * filled + "▱" * (_LAYOUT_BAR_SEGMENTS - filled)

    return _STATUS_BAR.sub(shrink, text)


def _mapped_action_emoji(action: "HubAction") -> str | None:
    """The action's own emoji, or None when nothing specific is mapped.

    `_action_emoji` falls back to a generic glyph, which is right for a dense
    text listing but wrong for the layout's action rows: leaf command names
    (`propose`, `sever`, `status`) are almost never in `_PAGE_EMOJIS`, so every
    row on a page ended up with the same diamond - five identical glyphs that
    carried no information and competed with the labels. Returning None lets the
    caller simply omit it.
    """
    return _PAGE_EMOJIS.get(str(getattr(action.command, "name", "")))


def _action_button_style(action: "HubAction", index: int) -> discord.ButtonStyle:
    words = set(re.findall(r"[a-z]+", str(getattr(action.command, "name", "")).casefold()))
    if words & _DANGER_ACTION_WORDS:
        return discord.ButtonStyle.danger
    if words & _SUCCESS_ACTION_WORDS:
        return discord.ButtonStyle.success
    return discord.ButtonStyle.primary if index == 0 else discord.ButtonStyle.secondary


def _action_listing(actions: Sequence["HubAction"], limit: int = 900) -> str:
    lines: list[str] = []
    used = 0
    for action in actions:
        line = f"{_action_emoji(action)} **{action.label}**  ·  {action.description}"
        if used + len(line) + 1 > limit:
            remaining = max(0, len(actions) - len(lines))
            if remaining:
                lines.append(f"＋ **{remaining} more** in the Action menu")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) or "No actions are available here."


def _quick_action_listing(actions: Sequence["HubAction"]) -> str:
    if not actions:
        return "No quick actions available."
    lines = []
    for action in actions[:_QUICK_ACTION_LIMIT]:
        lines.append(f"{_action_emoji(action)} **{action.label}**")
    if len(actions) > _QUICK_ACTION_LIMIT:
        lines.append(f"⌄ {len(actions) - _QUICK_ACTION_LIMIT} more in **Action**")
    return "  •  ".join(lines)


@dataclass(frozen=True)
class HubPage:
    key: str
    label: str
    description: str
    command: Any


@dataclass(frozen=True)
class HubDefinition:
    name: str
    title: str
    description: str
    pages: Sequence[HubPage]


@dataclass(frozen=True)
class HubInput:
    name: str
    label: str
    description: str
    annotation: Any
    required: bool
    default: Any
    choices: tuple[Any, ...]


@dataclass(frozen=True)
class HubDynamicOption:
    """One live option exposed by a hub input provider.

    ``value`` is passed unchanged to the canonical command handler.  The label,
    description and optional emoji are Discord presentation only.
    """

    label: str
    value: Any
    description: str = ""
    emoji: str | None = None


@dataclass(frozen=True)
class HubStatusField:
    """One short, live status value rendered on a hub card."""

    name: str
    value: str
    inline: bool = True


@dataclass(frozen=True)
class HubAction:
    command: Any
    handler: Any
    path: str
    label: str
    description: str


# Every hub the surface built, in order (v0.34.1). surface.py registers them
# once its definitions exist; anything below surface in the package - the
# playtest board, which posts one message per hub page - reads them here
# rather than importing surface.
REGISTERED_HUBS: list[HubDefinition] = []


def register_hubs(*definitions: HubDefinition) -> None:
    for definition in definitions:
        if all(existing.name != definition.name for existing in REGISTERED_HUBS):
            REGISTERED_HUBS.append(definition)


_HUB_OPTION_PROVIDERS: dict[tuple[str, str], Any] = {}

# What to say when a live provider returns nothing.
#
# "Equip has no available equipment id options right now." is true and useless: a
# player holding a Spirit-Iron Sword reads it as a bug. The reason is that Equip
# operates on BOUND equipment and the sword is still a carried item, so the real
# answer is "use Bind first" - which the panel had no way to say.
_HUB_OPTION_HINTS: dict[tuple[str, str], str] = {}


def register_hub_option_hint(command: Any, parameter: str, hint: str) -> None:
    """Explain an empty picker, and say what to do about it."""
    qualified = str(getattr(command, "qualified_name", getattr(command, "name", "")))
    _HUB_OPTION_HINTS[(qualified, str(parameter))] = str(hint)


def _hub_option_hint(action: "HubAction", spec: "HubInput") -> str:
    qualified = str(getattr(action.command, "qualified_name", getattr(action.command, "name", "")))
    return _HUB_OPTION_HINTS.get((qualified, spec.name), "")


def register_hub_option_provider(command: Any, parameter: str, provider: Any) -> None:
    """Register a live dropdown provider for a command parameter.

    Most parameters need no explicit registration: discord.py autocomplete
    callbacks are discovered automatically.  Registration exists for richer
    options (for example active events with location/time descriptions).
    """

    qualified = str(getattr(command, "qualified_name", getattr(command, "name", "")))
    _HUB_OPTION_PROVIDERS[(qualified, str(parameter))] = provider


# --- Ordering actions within a page -----------------------------------------
# Alphabetical is what a filesystem does, not what a player needs. It put
# /family's "Leave" tenth of fourteen - onto the second chunk - while the
# onboarding copy was telling new cultivators to use exactly that action to get
# out of their birth household.
#
# This is a DIFFERENT axis from _DANGER_ACTION_WORDS, which decides button
# colour, and the two must not be conflated. "Leave" is styled red because it
# changes your state and is worth noticing, but it is ordinary movement and
# belongs near the top. "Sever" is red as well, but it is irreversible and rare,
# so it belongs at the bottom. Colour warns; order prioritises. Sorting by the
# colour axis is exactly what would bury the action people need most.
#
# Only frequent, meaningful verbs are listed. There are 166 distinct leaf verbs
# across the game and no per-hub tuning here - anything unlisted lands in the
# middle band and stays alphabetical, which keeps this table small enough to
# stay honest.
_ORIENTING_ACTION_WORDS = frozenset({
    "status", "view", "info", "sheet", "overview", "list", "show", "history",
    "summary", "guide", "inspect", "check",
})
_PRIMARY_ACTION_WORDS = frozenset({
    "accept", "act", "begin", "claim", "create", "cultivate", "enter", "establish",
    "explore", "hunt", "join", "leave", "meditate", "open", "propose", "respond",
    "start", "stop", "talk", "train", "travel", "use", "visit",
})
_IRREVERSIBLE_ACTION_WORDS = frozenset({
    "abandon", "delete", "destroy", "disband", "dissolve", "purge", "reincarnate",
    "reset", "revoke", "sever",
})


def _action_rank(name: str) -> int:
    """Which band an action sorts into: 0 orienting, 1 primary, 2 rest, 3 rare.

    Irreversible is tested first so a rare action is never promoted by also
    matching a common word.
    """
    words = set(re.findall(r"[a-z]+", str(name).casefold()))
    if words & _IRREVERSIBLE_ACTION_WORDS:
        return 3
    if words & _ORIENTING_ACTION_WORDS:
        return 0
    if words & _PRIMARY_ACTION_WORDS:
        return 1
    return 2


def _leaf_actions(page: HubPage) -> list[HubAction]:
    command = page.command
    if command is None:
        return []
    if isinstance(command, app_commands.Group):
        leaves = [item for item in command.walk_commands() if isinstance(item, app_commands.Command)]
    else:
        leaves = [command]
    actions: list[HubAction] = []
    for item in leaves:
        qualified = str(getattr(item, "qualified_name", getattr(item, "name", "action")))
        path = f"/{qualified}"
        actions.append(
            HubAction(
                command=item,
                handler=ACTIONS.handler_for(item),
                path=path,
                label=str(getattr(item, "name", "Action")).replace("_", " ").title()[:100],
                description=str(getattr(item, "description", "Run this action"))[:100],
            )
        )
    # (band, name, path): band puts the useful actions first, name keeps each
    # band alphabetical, path is the tiebreaker so nested subgroups that share a
    # leaf name (/sect has four distinct "status" commands) stay deterministic.
    return sorted(
        actions,
        key=lambda action: (
            _action_rank(getattr(action.command, "name", "")),
            str(getattr(action.command, "name", "")).casefold(),
            action.path,
        ),
    )


def _command_parameter_metadata(command: Any) -> Mapping[str, Any]:
    try:
        params = command.parameters
    except Exception:
        return {}
    if isinstance(params, Mapping):
        return params
    # discord.py 2.x exposes Command.parameters as a list[Parameter].
    try:
        return {str(param.name): param for param in params}
    except (TypeError, AttributeError):
        return {}


def _inputs_for(action: HubAction) -> list[HubInput]:
    signature = inspect.signature(action.handler)
    metadata = _command_parameter_metadata(action.command)
    inputs: list[HubInput] = []
    for index, (name, parameter) in enumerate(signature.parameters.items()):
        if index == 0 and name == "interaction":
            continue
        meta = metadata.get(name)
        description = str(getattr(meta, "description", "") or name.replace("_", " ").title())
        choices = tuple(getattr(meta, "choices", ()) or ())
        required = parameter.default is inspect.Parameter.empty
        default = None if required else parameter.default
        inputs.append(
            HubInput(
                name=name,
                label=name.replace("_", " ").title()[:45],
                description=description[:100],
                annotation=parameter.annotation,
                required=required,
                default=default,
                choices=choices,
            )
        )
    return inputs


def _autocomplete_provider(action: HubAction, spec: HubInput) -> Any | None:
    qualified = str(getattr(action.command, "qualified_name", getattr(action.command, "name", "")))
    provider = _HUB_OPTION_PROVIDERS.get((qualified, spec.name))
    if provider is not None:
        return provider

    # discord.py stores autocomplete callbacks on its internal command parameter
    # objects.  Using this as a fallback lets the GUI reuse the same option source
    # as slash-command autocomplete without duplicating game lookup logic.
    params = getattr(action.command, "_params", None)
    if isinstance(params, Mapping):
        param = params.get(spec.name)
        candidate = getattr(param, "autocomplete", None) if param is not None else None
        if callable(candidate):
            return candidate
    return None


def _is_bool_input(spec: HubInput) -> bool:
    annotation = _annotation_text(spec.annotation)
    lowered = annotation.replace(" ", "")
    return annotation is bool or lowered in {"bool", "<class'bool'>"} or "bool|None" in lowered


def _is_member_input(spec: HubInput) -> bool:
    annotation = _annotation_text(spec.annotation)
    return "discord.Member" in annotation or "discord.User" in annotation or annotation.endswith("Member")


def _is_channel_input(spec: HubInput) -> bool:
    annotation = _annotation_text(spec.annotation)
    return "discord.TextChannel" in annotation or "discord.abc.GuildChannel" in annotation


def _is_guided_input(action: HubAction, spec: HubInput) -> bool:
    return bool(
        (spec.choices and len(spec.choices) <= 25)
        or _is_bool_input(spec)
        or _is_member_input(spec)
        or _is_channel_input(spec)
        or _autocomplete_provider(action, spec) is not None
    )


async def _live_options(
    interaction: discord.Interaction, action: HubAction, spec: HubInput
) -> list[HubDynamicOption]:
    provider = _autocomplete_provider(action, spec)
    if provider is None:
        return []
    raw_options = await provider(interaction, "")
    normalized: list[HubDynamicOption] = []
    seen: set[str] = set()
    for raw in list(raw_options or [])[:25]:
        if isinstance(raw, HubDynamicOption):
            option = raw
        else:
            label = str(getattr(raw, "name", getattr(raw, "label", getattr(raw, "value", raw))))
            value = getattr(raw, "value", raw)
            description = str(getattr(raw, "description", "") or "")
            emoji = getattr(raw, "emoji", None)
            option = HubDynamicOption(label=label, value=value, description=description, emoji=emoji)
        marker = str(option.value)
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(option)
    return normalized[:25]


def _annotation_text(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    return str(annotation)


def _choice_match(spec: HubInput, raw: str) -> Any | None:
    target = raw.strip().casefold()
    for choice in spec.choices:
        if str(getattr(choice, "name", "")).casefold() == target:
            return choice
        if str(getattr(choice, "value", "")).casefold() == target:
            return choice
    return None


async def _resolve_input(interaction: discord.Interaction, spec: HubInput, raw: str) -> Any:
    value = raw.strip()
    if not value and not spec.required:
        return spec.default
    annotation = _annotation_text(spec.annotation)

    if "Choice[" in annotation or "app_commands.Choice" in annotation:
        match = _choice_match(spec, value)
        if match is not None:
            return match
        inner: Any = value
        if "Choice[int" in annotation:
            inner = int(value)
        elif "Choice[float" in annotation:
            inner = float(value)
        return app_commands.Choice(name=value[:100], value=inner)

    if "discord.Member" in annotation or annotation.endswith("Member") or "discord.User" in annotation:
        if not interaction.guild:
            raise ValueError("A guild member can only be selected inside a server.")
        match = _MENTION_ID.fullmatch(value)
        if not match:
            raise ValueError(f"{spec.label}: enter a member mention or numeric Discord ID.")
        member_id = int(match.group(1))
        member = interaction.guild.get_member(member_id)
        if member is None:
            member = await interaction.guild.fetch_member(member_id)
        return member

    if "discord.TextChannel" in annotation or "discord.Thread" in annotation or "discord.abc.GuildChannel" in annotation:
        if not interaction.guild:
            raise ValueError("A channel can only be selected inside a server.")
        match = _MENTION_ID.fullmatch(value)
        if not match:
            raise ValueError(f"{spec.label}: enter a channel mention or numeric Discord ID.")
        channel_id = int(match.group(1))
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            channel = await interaction.guild.fetch_channel(channel_id)
        return channel

    range_match = _RANGE_ANNOTATION.search(annotation)
    if range_match:
        kind, raw_min, raw_max = range_match.groups()
        converted: int | float = int(value) if kind == "int" else float(value)
        minimum: int | float = int(float(raw_min)) if kind == "int" else float(raw_min)
        maximum: int | float = int(float(raw_max)) if kind == "int" else float(raw_max)
        if converted < minimum or converted > maximum:
            raise ValueError(f"{spec.label}: enter a value from {minimum} to {maximum}.")
        return converted

    lowered = annotation.replace(" ", "")
    if annotation is int or lowered in {"int", "<class'int'>"}:
        return int(value)
    if annotation is float or lowered in {"float", "<class'float'>"}:
        return float(value)
    if annotation is bool or lowered in {"bool", "<class'bool'>"} or "bool|None" in lowered:
        normalized = value.casefold()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise ValueError(f"{spec.label}: use true/false or yes/no.")
    return value


def _safe_edit_kwargs(kwargs: dict[str, Any], *, fallback_view: discord.ui.View | None) -> dict[str, Any]:
    clean = dict(kwargs)
    clean.pop("ephemeral", None)
    clean.pop("silent", None)
    if "content" in clean and "embed" not in clean and "embeds" not in clean:
        clean["embed"] = None
    if ("embed" in clean or "embeds" in clean) and "content" not in clean:
        clean["content"] = None
    if "view" not in clean:
        clean["view"] = fallback_view
    return clean


def _hub_content_chunks(content: Any, limit: int = 1950) -> list[Any]:
    """Split long text before it reaches Discord's 2,000-character limit."""
    if not isinstance(content, str) or len(content) <= limit:
        return [content]
    text = content.strip()
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split = text.rfind("\n", 0, limit)
        if split < limit // 2:
            split = text.rfind(" ", 0, limit)
        if split <= 0:
            split = limit
        chunks.append(text[:split].strip())
        text = text[split:].strip()
    return chunks or [""]


def _response_is_ephemeral(
    kwargs: Mapping[str, Any], *, default: bool = False
) -> bool:
    """Return the handler's requested visibility without mutating its arguments."""
    return bool(kwargs.get("ephemeral", default))


async def _send_ephemeral_followup(
    source: discord.Interaction,
    content: Any,
    kwargs: Mapping[str, Any],
) -> Any:
    """Send private hub feedback without replacing the shared hub surface."""
    followup_kwargs = dict(kwargs)
    followup_kwargs.pop("content", None)
    followup_kwargs["ephemeral"] = True
    if not source.response.is_done():
        return await source.response.send_message(content, **followup_kwargs)
    return await source.followup.send(content, **followup_kwargs)


async def _fallback_followup(source: discord.Interaction, content: Any, kwargs: Mapping[str, Any]) -> Any:
    """Send a fresh response when the original hub message is unavailable."""
    followup_kwargs = dict(kwargs)
    ephemeral = bool(followup_kwargs.pop("ephemeral", False))
    # A public fallback should not duplicate the command hub view.
    if not ephemeral:
        followup_kwargs.pop("view", None)
    return await source.followup.send(content, ephemeral=ephemeral, **followup_kwargs)


def _layout_targets_panel(source: discord.Interaction, hub_view: Any) -> bool:
    """True when this interaction's original response *is* a Components V2 panel.

    A Components V2 message may never carry ``content`` or ``embeds``, and the
    flag cannot be removed by editing, so the classic behaviour - write the
    action's output over the hub card - is impossible there.  When this returns
    True the output is sent as a followup instead, which also leaves the panel
    intact rather than replacing it with result text.

    Component interactions raised by an *input step* message (a choice or member
    picker the hub opened) are not the panel, so those keep editing themselves
    in place exactly as before.  Modal submits have no source message at all and
    resolve against their own deferred "thinking" response.
    """
    if not getattr(hub_view, "is_layout_hub", False):
        return False
    message = getattr(source, "message", None)
    if message is None:
        return False
    panel = getattr(hub_view, "message", None)
    if panel is None:
        return True
    return int(getattr(message, "id", 0)) == int(getattr(panel, "id", -1))


async def _layout_result_send(
    source: discord.Interaction, content: Any, kwargs: Mapping[str, Any]
) -> Any:
    """Deliver hub action output beside a Components V2 panel instead of over it."""
    if _response_is_ephemeral(kwargs):
        return await _send_ephemeral_followup(source, content, kwargs)

    if not source.response.is_done():
        await source.response.defer()
    followup_kwargs = dict(kwargs)
    for key in ("ephemeral", "silent", "view", "content"):
        followup_kwargs.pop(key, None)
    if content is None and not any(key in followup_kwargs for key in ("embed", "embeds")):
        content = "✅ Done."
    return await source.followup.send(content, ephemeral=False, **followup_kwargs)


class _HubFollowupProxy:
    def __init__(self, owner: "HubInteractionProxy") -> None:
        self.owner = owner

    async def send(self, content: Any = None, **kwargs: Any) -> Any:
        chunks = _hub_content_chunks(content)
        first = chunks[0]
        requested_ephemeral = _response_is_ephemeral(
            kwargs, default=self.owner.deferred_ephemeral
        )
        if requested_ephemeral:
            private_kwargs = dict(kwargs)
            private_kwargs["ephemeral"] = True
            result = await _send_ephemeral_followup(
                self.owner.source, first, private_kwargs
            )
            for chunk in chunks[1:]:
                await self.owner.source.followup.send(chunk, ephemeral=True)
            return result

        result: Any = None
        if _layout_targets_panel(self.owner.source, self.owner.hub_view):
            self.owner.output_written = True
            result = await _layout_result_send(self.owner.source, first, kwargs)
        elif not self.owner.output_written:
            self.owner.output_written = True
            edit_kwargs = dict(kwargs)
            if first is not None:
                edit_kwargs["content"] = first
            edit_kwargs = _safe_edit_kwargs(
                edit_kwargs, fallback_view=self.owner.hub_view
            )
            try:
                result = await self.owner.source.edit_original_response(**edit_kwargs)
            except (discord.NotFound, discord.HTTPException):
                result = await _fallback_followup(
                    self.owner.source, first, kwargs
                )
        else:
            result = await _fallback_followup(self.owner.source, first, kwargs)
        for chunk in chunks[1:]:
            await self.owner.source.followup.send(chunk, ephemeral=False)
        return result


class _HubResponseProxy:
    def __init__(self, owner: "HubInteractionProxy") -> None:
        self.owner = owner

    def is_done(self) -> bool:
        return self.owner.source.response.is_done()

    async def send_message(self, content: Any = None, **kwargs: Any) -> Any:
        chunks = _hub_content_chunks(content)
        first = chunks[0]
        if _response_is_ephemeral(
            kwargs, default=self.owner.deferred_ephemeral
        ):
            private_kwargs = dict(kwargs)
            private_kwargs["ephemeral"] = True
            result = await _send_ephemeral_followup(
                self.owner.source, first, private_kwargs
            )
            for chunk in chunks[1:]:
                await self.owner.source.followup.send(chunk, ephemeral=True)
            return result

        self.owner.output_written = True
        if _layout_targets_panel(self.owner.source, self.owner.hub_view):
            result = await _layout_result_send(self.owner.source, first, kwargs)
            for chunk in chunks[1:]:
                await self.owner.source.followup.send(chunk, ephemeral=False)
            return result
        edit_kwargs = dict(kwargs)
        if first is not None:
            edit_kwargs["content"] = first
        edit_kwargs = _safe_edit_kwargs(
            edit_kwargs, fallback_view=self.owner.hub_view
        )
        source_message = getattr(self.owner.source, "message", None)
        hub_message = self.owner.hub_view.message
        result: Any = None
        try:
            if (
                not self.owner.source.response.is_done()
                and source_message is not None
                and source_message == hub_message
            ):
                result = await self.owner.source.response.edit_message(**edit_kwargs)
            else:
                if not self.owner.source.response.is_done():
                    await self.owner.source.response.defer()
                result = await self.owner.source.edit_original_response(**edit_kwargs)
        except (discord.NotFound, discord.HTTPException):
            result = await _fallback_followup(
                self.owner.source, first, kwargs
            )
        for chunk in chunks[1:]:
            await self.owner.source.followup.send(chunk, ephemeral=False)
        return result

    async def defer(self, **kwargs: Any) -> Any:
        if "ephemeral" in kwargs:
            self.owner.deferred_ephemeral = bool(kwargs["ephemeral"])
        if self.owner.source.response.is_done():
            return None
        return await self.owner.source.response.defer(**kwargs)

    async def edit_message(self, **kwargs: Any) -> Any:
        self.owner.output_written = True
        if _layout_targets_panel(self.owner.source, self.owner.hub_view):
            return await _layout_result_send(
                self.owner.source, kwargs.get("content"), kwargs
            )
        edit_kwargs = _safe_edit_kwargs(kwargs, fallback_view=self.owner.hub_view)
        source_message = getattr(self.owner.source, "message", None)
        hub_message = self.owner.hub_view.message
        try:
            if not self.owner.source.response.is_done() and source_message is not None and source_message == hub_message:
                return await self.owner.source.response.edit_message(**edit_kwargs)
            if not self.owner.source.response.is_done():
                await self.owner.source.response.defer()
            return await self.owner.source.edit_original_response(**edit_kwargs)
        except (discord.NotFound, discord.HTTPException):
            content = edit_kwargs.get("content") or "The command completed, but the original hub message is no longer available."
            return await _fallback_followup(self.owner.source, content, kwargs)

    async def send_modal(self, modal: discord.ui.Modal) -> Any:
        return await self.owner.source.response.send_modal(modal)


async def _acknowledge_hub_action(interaction: discord.Interaction) -> None:
    """Acknowledge a hub action before any potentially slow game/Narrator work.

    Discord component/modal interactions must be acknowledged within a few seconds.
    A narration call walking the fallback chain can take tens of seconds, so waiting
    for the registered handler before deferring causes 10062/10015 interaction-token
    failures.
    """
    if interaction.response.is_done():
        return
    if interaction.type == discord.InteractionType.modal_submit:
        # Modal submits do not have a message-update response to defer; create an
        # public deferred response that the proxy can edit once work finishes.
        await interaction.response.defer(ephemeral=False, thinking=True)
    else:
        # Component interactions should acknowledge as a deferred message update so
        # the existing hub card remains the canonical surface.
        await interaction.response.defer()


class HubInteractionProxy:
    """Delegate an interaction while redirecting action responses into the hub message.

    ``command_override`` preserves the registered handler's qualified command identity
    when it is invoked through a GUI.  This matters for administrator audit logs:
    selecting ``Server → Backup`` from ``/admin`` is recorded as
    ``/admin server backup`` rather than merely ``/admin``.
    """

    def __init__(
        self,
        source: discord.Interaction,
        hub_view: "CommandHubView",
        *,
        command_override: Any | None = None,
        supplied_options: Mapping[str, Any] | None = None,
    ) -> None:
        self.source = source
        self.hub_view = hub_view
        self.command_override = command_override
        self.hub_supplied_options = dict(supplied_options or {})
        self.output_written = False
        self.deferred_ephemeral = False
        self.response = _HubResponseProxy(self)
        self.followup = _HubFollowupProxy(self)

    @property
    def message(self) -> discord.Message | None:
        return getattr(self.source, "message", None) or self.hub_view.message

    @property
    def command(self) -> Any:
        return self.command_override or getattr(self.source, "command", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.source, name)


async def _invoke_action(
    interaction: discord.Interaction,
    hub_view: "CommandHubView",
    action: HubAction,
    supplied: Mapping[str, Any],
) -> None:
    proxy = HubInteractionProxy(
        interaction, hub_view, command_override=action.command, supplied_options=supplied
    )
    try:
        await _acknowledge_hub_action(interaction)
        await action.handler(proxy, **dict(supplied))
    except Exception as exc:
        log.exception("Hub action failed: %s", action.path)
        reporter = getattr(interaction.client, "hub_error_reporter", None)
        if callable(reporter):
            try:
                await reporter(
                    interaction.guild,
                    "Hub action failed",
                    f"{action.path} — {type(exc).__name__}: {str(exc)[:900]}",
                )
            except Exception:
                log.exception("Could not mirror hub failure to the configured Discord log channel")
        text = "❌ That action could not be completed. The game state was rechecked and no additional hub-side rule was applied."
        try:
            if _layout_targets_panel(interaction, hub_view):
                await _layout_result_send(interaction, text, {})
            elif not interaction.response.is_done():
                await interaction.response.edit_message(content=text, embed=None, view=hub_view)
            else:
                await interaction.edit_original_response(content=text, embed=None, view=hub_view)
        except (discord.NotFound, discord.HTTPException):
            try:
                await interaction.followup.send(text, ephemeral=False)
            except discord.HTTPException:
                log.exception("Could not deliver hub failure response")


async def _report_hub_ui_error(
    interaction: discord.Interaction, error: Exception, *, where: str
) -> None:
    log.error(
        "Hub UI callback failed: %s", where,
        exc_info=(type(error), error, error.__traceback__),
    )
    reporter = getattr(interaction.client, "hub_error_reporter", None)
    if callable(reporter):
        try:
            await reporter(
                interaction.guild,
                "Hub UI callback failed",
                f"{where} — {type(error).__name__}: {str(error)[:900]}",
            )
        except Exception:
            log.exception("Could not mirror hub UI failure to the configured Discord log channel")
    text = "❌ This interface hit an unexpected error. No extra hub-side game rule was applied."
    try:
        await _send_ephemeral_followup(
            interaction, text, {"ephemeral": True}
        )
    except discord.HTTPException:
        log.exception("Could not deliver hub UI failure response")


class HubActionModal(discord.ui.Modal):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        inputs: Sequence[HubInput],
        *,
        remaining: Sequence[HubInput] | None = None,
        collected: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(title=action.label[:45], timeout=300)
        self.hub_view = hub_view
        self.action = action
        self.inputs = list(inputs[:5])
        self.remaining = list(remaining if remaining is not None else inputs[5:])
        self.collected = dict(collected or {})
        self.fields: dict[str, discord.ui.TextInput] = {}
        for spec in self.inputs:
            default = None
            if spec.default is not None and spec.default is not inspect.Parameter.empty:
                default = str(spec.default)
            placeholder = spec.description
            field = discord.ui.TextInput(
                label=spec.label,
                placeholder=placeholder[:100],
                required=spec.required,
                default=default,
                max_length=400,
            )
            self.fields[spec.name] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values: dict[str, Any] = dict(self.collected)
        try:
            for spec in self.inputs:
                values[spec.name] = await _resolve_input(interaction, spec, str(self.fields[spec.name].value))
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        if self.remaining:
            await interaction.response.send_message(
                f"**{self.action.label}** has additional guided options. Continue to finish the action.",
                view=HubContinueInputView(self.hub_view, self.action, self.remaining, values),
                ephemeral=True,
            )
            return
        await _invoke_action(interaction, self.hub_view, self.action, values)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _report_hub_ui_error(interaction, error, where=f"modal:{self.action.path}")


class HubContinueInputButton(discord.ui.Button):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        super().__init__(label="Continue", style=discord.ButtonStyle.primary)
        self.hub_view = hub_view
        self.action = action
        self.remaining = list(remaining)
        self.collected = dict(collected)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _present_input_step(interaction, self.hub_view, self.action, self.remaining, self.collected)


class HubContinueInputView(discord.ui.View):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        super().__init__(timeout=180)
        self.hub_view = hub_view
        self.add_item(HubContinueInputButton(hub_view, action, remaining, collected))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.hub_view.interaction_check(interaction)


async def _continue_guided_value(
    interaction: discord.Interaction,
    hub_view: "CommandHubView",
    action: HubAction,
    spec: HubInput,
    value: Any,
    remaining: Sequence[HubInput],
    collected: Mapping[str, Any],
) -> None:
    values = dict(collected)
    values[spec.name] = value
    if remaining:
        await _present_input_step(interaction, hub_view, action, remaining, values)
        return
    await _invoke_action(interaction, hub_view, action, values)


class HubDefaultInputButton(discord.ui.Button):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
        *,
        row: int = 1,
    ) -> None:
        label = "Use default"
        annotation = _annotation_text(spec.annotation)
        if "discord.Member" in annotation or "discord.User" in annotation:
            label = "Use self / default"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self.hub_view = hub_view
        self.action = action
        self.spec = spec
        self.remaining = list(remaining)
        self.collected = dict(collected)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _continue_guided_value(
            interaction, self.hub_view, self.action, self.spec, self.spec.default,
            self.remaining, self.collected,
        )


class HubChoiceSelect(discord.ui.Select):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        self.hub_view = hub_view
        self.action = action
        self.spec = spec
        self.remaining = list(remaining)
        self.collected = dict(collected)
        options = [
            discord.SelectOption(
                label=str(getattr(choice, "name", getattr(choice, "value", "Choice")))[:100],
                value=str(index),
                description=str(getattr(choice, "value", ""))[:100] or None,
            )
            for index, choice in enumerate(spec.choices[:25])
        ]
        super().__init__(placeholder=f"Choose {spec.label.lower()}", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.spec.choices[int(self.values[0])]
        annotation = _annotation_text(self.spec.annotation)
        value = choice if ("Choice[" in annotation or "app_commands.Choice" in annotation) else getattr(choice, "value", choice)
        await _continue_guided_value(
            interaction, self.hub_view, self.action, self.spec, value,
            self.remaining, self.collected,
        )


class HubChoiceView(discord.ui.View):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        super().__init__(timeout=180)
        self.hub_view = hub_view
        self.add_item(HubChoiceSelect(hub_view, action, spec, remaining, collected))
        if not spec.required:
            self.add_item(HubDefaultInputButton(hub_view, action, spec, remaining, collected, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.hub_view.interaction_check(interaction)


class HubBoolSelect(discord.ui.Select):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        self.hub_view = hub_view
        self.action = action
        self.spec = spec
        self.remaining = list(remaining)
        self.collected = dict(collected)
        options = [
            discord.SelectOption(label="Yes", value="true", description="Enable / confirm this option"),
            discord.SelectOption(label="No", value="false", description="Disable / decline this option"),
        ]
        super().__init__(placeholder=f"Choose {spec.label.lower()}", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _continue_guided_value(
            interaction, self.hub_view, self.action, self.spec, self.values[0] == "true",
            self.remaining, self.collected,
        )


class HubBoolView(discord.ui.View):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        super().__init__(timeout=180)
        self.hub_view = hub_view
        self.add_item(HubBoolSelect(hub_view, action, spec, remaining, collected))
        if not spec.required:
            self.add_item(HubDefaultInputButton(hub_view, action, spec, remaining, collected, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.hub_view.interaction_check(interaction)


class HubMemberSelect(discord.ui.UserSelect):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        self.hub_view = hub_view
        self.action = action
        self.spec = spec
        self.remaining = list(remaining)
        self.collected = dict(collected)
        super().__init__(placeholder=f"Choose {spec.label.lower()}", min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        if interaction.guild and not isinstance(selected, discord.Member):
            selected = interaction.guild.get_member(selected.id) or selected
        await _continue_guided_value(
            interaction, self.hub_view, self.action, self.spec, selected,
            self.remaining, self.collected,
        )


class HubMemberView(discord.ui.View):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        super().__init__(timeout=180)
        self.hub_view = hub_view
        self.add_item(HubMemberSelect(hub_view, action, spec, remaining, collected))
        if not spec.required:
            self.add_item(HubDefaultInputButton(hub_view, action, spec, remaining, collected, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.hub_view.interaction_check(interaction)


class HubChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        self.hub_view = hub_view
        self.action = action
        self.spec = spec
        self.remaining = list(remaining)
        self.collected = dict(collected)
        super().__init__(
            placeholder=f"Choose {spec.label.lower()}", min_values=1, max_values=1, row=0,
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _continue_guided_value(
            interaction, self.hub_view, self.action, self.spec, self.values[0],
            self.remaining, self.collected,
        )


class HubChannelView(discord.ui.View):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        super().__init__(timeout=180)
        self.hub_view = hub_view
        self.add_item(HubChannelSelect(hub_view, action, spec, remaining, collected))
        if not spec.required:
            self.add_item(HubDefaultInputButton(hub_view, action, spec, remaining, collected, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.hub_view.interaction_check(interaction)


class HubDynamicSelect(discord.ui.Select):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        options: Sequence[HubDynamicOption],
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        self.hub_view = hub_view
        self.action = action
        self.spec = spec
        self.dynamic_options = {str(index): option for index, option in enumerate(options[:25])}
        self.remaining = list(remaining)
        self.collected = dict(collected)
        discord_options = [
            discord.SelectOption(
                label=option.label[:100],
                value=str(index),
                description=option.description[:100] or None,
                emoji=option.emoji,
            )
            for index, option in enumerate(options[:25])
        ]
        super().__init__(
            placeholder=f"Choose {spec.label.lower()} • live options"[:150],
            min_values=1,
            max_values=1,
            options=discord_options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        option = self.dynamic_options[self.values[0]]
        await _continue_guided_value(
            interaction, self.hub_view, self.action, self.spec, option.value,
            self.remaining, self.collected,
        )


class HubDynamicView(discord.ui.View):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        spec: HubInput,
        options: Sequence[HubDynamicOption],
        remaining: Sequence[HubInput],
        collected: Mapping[str, Any],
    ) -> None:
        super().__init__(timeout=180)
        self.hub_view = hub_view
        self.add_item(HubDynamicSelect(hub_view, action, spec, options, remaining, collected))
        if not spec.required:
            self.add_item(HubDefaultInputButton(hub_view, action, spec, remaining, collected, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.hub_view.interaction_check(interaction)


async def _present_input_step(
    interaction: discord.Interaction,
    hub_view: "CommandHubView",
    action: HubAction,
    inputs: Sequence[HubInput],
    collected: Mapping[str, Any] | None = None,
) -> None:
    remaining_inputs = list(inputs)
    values = dict(collected or {})
    if not remaining_inputs:
        await _invoke_action(interaction, hub_view, action, values)
        return

    spec = remaining_inputs[0]
    tail = remaining_inputs[1:]

    if spec.choices and len(spec.choices) <= 25:
        await interaction.response.send_message(
            f"**{action.label}** — select {spec.label.lower()}.",
            view=HubChoiceView(hub_view, action, spec, tail, values),
            ephemeral=True,
        )
        return
    if _is_bool_input(spec):
        await interaction.response.send_message(
            f"**{action.label}** — choose {spec.label.lower()}.",
            view=HubBoolView(hub_view, action, spec, tail, values),
            ephemeral=True,
        )
        return
    if _is_member_input(spec):
        await interaction.response.send_message(
            f"**{action.label}** — select {spec.label.lower()}.",
            view=HubMemberView(hub_view, action, spec, tail, values),
            ephemeral=True,
        )
        return
    if _is_channel_input(spec):
        await interaction.response.send_message(
            f"**{action.label}** — select {spec.label.lower()}.",
            view=HubChannelView(hub_view, action, spec, tail, values),
            ephemeral=True,
        )
        return

    provider = _autocomplete_provider(action, spec)
    if provider is not None:
        options = await _live_options(interaction, action, spec)
        if options:
            await interaction.response.send_message(
                f"**{action.label}** — choose {spec.label.lower()} from the current live options.",
                view=HubDynamicView(hub_view, action, spec, options, tail, values),
                ephemeral=True,
            )
        else:
            hint = _hub_option_hint(action, spec)
            message = f"ℹ️ **{action.label}** — nothing to choose from right now."
            if hint:
                message += f"\n{hint}"
            await interaction.response.send_message(message, ephemeral=True)
        return

    # Collect a compact run of genuinely free-form values in one modal, stopping
    # before the next parameter that can be represented by a Discord selector.
    modal_inputs: list[HubInput] = []
    index = 0
    for candidate in remaining_inputs:
        if modal_inputs and _is_guided_input(action, candidate):
            break
        if not modal_inputs and _is_guided_input(action, candidate):
            break
        modal_inputs.append(candidate)
        index += 1
        if len(modal_inputs) >= 5:
            break
    if not modal_inputs:
        # Defensive fallback: this should only happen if a new guided input type is
        # added without a presentation branch above.
        modal_inputs = [spec]
        index = 1
    await interaction.response.send_modal(
        HubActionModal(
            hub_view,
            action,
            modal_inputs,
            remaining=remaining_inputs[index:],
            collected=values,
        )
    )


class HubPageSelect(discord.ui.Select):
    def __init__(self, hub_view: "CommandHubView", row: int = 0) -> None:
        self.hub_view = hub_view
        options = [
            discord.SelectOption(
                label=page.label[:100],
                value=page.key,
                description=page.description[:100],
                emoji=_page_emoji(page.key),
                default=page.key == hub_view.page_key,
            )
            for page in hub_view.definition.pages[:25]
        ]
        super().__init__(placeholder=f"Page • {hub_view.page.label if hub_view.page else 'Choose system'}", min_values=1, max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.hub_view.page_key = self.values[0]
        self.hub_view.rebuild()
        await interaction.response.edit_message(content=None, embed=self.hub_view.build_embed(), view=self.hub_view)


class HubActionSelect(discord.ui.Select):
    def __init__(self, hub_view: "CommandHubView", actions: Sequence[HubAction], row: int = 1) -> None:
        self.hub_view = hub_view
        self.actions = {str(index): action for index, action in enumerate(actions[:25])}
        options = [
            discord.SelectOption(
                label=action.label[:100], value=str(index), description=action.description[:100],
                emoji=_page_emoji(str(getattr(action.command, "name", ""))),
            )
            for index, action in enumerate(actions[:25])
        ]
        page_label = hub_view.page.label if hub_view.page else "system"
        super().__init__(placeholder=f"Action • {page_label}"[:150], min_values=1, max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        action = self.actions[self.values[0]]
        await _start_hub_action(interaction, self.hub_view, action)


async def _start_hub_action(
    interaction: discord.Interaction,
    hub_view: "CommandHubView",
    action: HubAction,
) -> None:
    """Open guided inputs or immediately run one canonical hub action."""
    inputs = _inputs_for(action)
    if not inputs:
        await _invoke_action(interaction, hub_view, action, {})
        return
    await _present_input_step(interaction, hub_view, action, inputs, {})


class HubQuickActionButton(discord.ui.Button):
    def __init__(
        self,
        hub_view: "CommandHubView",
        action: HubAction,
        *,
        index: int,
        row: int,
    ) -> None:
        self.hub_view = hub_view
        self.action = action
        super().__init__(
            label=action.label[:34],
            style=_action_button_style(action, index),
            emoji=_action_emoji(action),
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _start_hub_action(interaction, self.hub_view, self.action)


class HubPageButton(discord.ui.Button):
    def __init__(self, hub_view: "CommandHubView", *, direction: int, row: int) -> None:
        self.hub_view = hub_view
        self.direction = -1 if direction < 0 else 1
        super().__init__(
            label="Previous" if self.direction < 0 else "Next",
            style=discord.ButtonStyle.secondary,
            emoji="◀️" if self.direction < 0 else "▶️",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        pages = list(self.hub_view.definition.pages)
        if not pages:
            await interaction.response.defer()
            return
        current = next(
            (index for index, page in enumerate(pages) if page.key == self.hub_view.page_key),
            0,
        )
        self.hub_view.page_key = pages[(current + self.direction) % len(pages)].key
        self.hub_view.rebuild()
        await interaction.response.edit_message(
            content=None,
            embed=self.hub_view.build_embed(),
            view=self.hub_view,
        )


class HubRefreshButton(discord.ui.Button):
    def __init__(self, hub_view: "CommandHubView", row: int = 2) -> None:
        self.hub_view = hub_view
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            emoji="🔄",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.hub_view.refresh_status(interaction)
        self.hub_view.rebuild()
        await interaction.response.edit_message(content=None, embed=self.hub_view.build_embed(), view=self.hub_view)


# ---------------------------------------------------------------------------
# Components V2 hub layout
# ---------------------------------------------------------------------------
# Subclassing LayoutView only when discord.py actually provides it keeps this
# module importable on older versions; the class is then simply never selected.
_LayoutHubBase = discord.ui.LayoutView if LAYOUT_COMPONENTS_AVAILABLE else discord.ui.View


# The layout deliberately has NO system dropdown. It carried one at first, but
# with Prev/Next present the two did the same job, and the select cost a full row
# to display only the system already named in the page heading above it. Stepping
# is the only navigation now; the arrows wrap, so the far end of a long hub is one
# Prev away rather than seventeen Nexts.


class HubLayoutActionButton(discord.ui.Button):
    """Accessory button that runs the action shown on its own row."""

    def __init__(self, hub_view: "LayoutHubView", action: HubAction, *, index: int) -> None:
        self.hub_view = hub_view
        self.action = action
        style = _action_button_style(action, index)
        # Destructive actions name themselves. A red button reading "Open" says
        # nothing at the moment it matters most - the colour warns, the label
        # should say what it is about to do ("Sever", "Disband", "Abandon").
        # Everything else keeps one consistent label so the column stays calm.
        label = action.label[:20] if style is discord.ButtonStyle.danger else "Open"
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _start_hub_action(interaction, self.hub_view, self.action)


class HubLayoutSystemStepButton(discord.ui.Button):
    def __init__(self, hub_view: "LayoutHubView", *, direction: int) -> None:
        self.hub_view = hub_view
        self.direction = -1 if direction < 0 else 1
        super().__init__(
            label="Prev system" if self.direction < 0 else "Next system",
            style=discord.ButtonStyle.secondary,
            emoji="◀️" if self.direction < 0 else "▶️",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        pages = list(self.hub_view.definition.pages)
        if not pages:
            await interaction.response.defer()
            return
        current = next(
            (index for index, page in enumerate(pages) if page.key == self.hub_view.page_key),
            0,
        )
        self.hub_view.page_key = pages[(current + self.direction) % len(pages)].key
        self.hub_view.action_offset = 0
        self.hub_view.rebuild()
        await interaction.response.edit_message(view=self.hub_view)


class HubLayoutActionPageButton(discord.ui.Button):
    """Step through action chunks on systems with more actions than fit at once."""

    def __init__(self, hub_view: "LayoutHubView", *, direction: int) -> None:
        self.hub_view = hub_view
        self.direction = -1 if direction < 0 else 1
        super().__init__(
            label="Fewer" if self.direction < 0 else "More actions",
            style=discord.ButtonStyle.secondary,
            emoji="🔼" if self.direction < 0 else "🔽",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        page = self.hub_view.page
        total = len(_leaf_actions(page)) if page is not None else 0
        if total <= _LAYOUT_ACTION_LIMIT:
            await interaction.response.defer()
            return
        step = _LAYOUT_ACTION_LIMIT * self.direction
        offset = self.hub_view.action_offset + step
        if offset >= total:
            offset = 0
        elif offset < 0:
            offset = ((total - 1) // _LAYOUT_ACTION_LIMIT) * _LAYOUT_ACTION_LIMIT
        self.hub_view.action_offset = offset
        self.hub_view.rebuild()
        await interaction.response.edit_message(view=self.hub_view)


class HubLayoutRefreshButton(discord.ui.Button):
    def __init__(self, hub_view: "LayoutHubView") -> None:
        self.hub_view = hub_view
        super().__init__(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.hub_view.refresh_status(interaction)
        self.hub_view.rebuild()
        await interaction.response.edit_message(view=self.hub_view)


class LayoutHubView(_LayoutHubBase):
    """Components V2 hub panel: one visible, tappable row per action.

    The classic panel hides every action behind a page dropdown and an action
    dropdown, so a player cannot see what a system offers without opening a menu
    and cannot reach anything in fewer than two interactions.  Here each action
    renders as its own Section - emoji, label, description and its own button -
    and the only surviving dropdown jumps between systems.

    A Components V2 message cannot carry ``content`` or ``embeds``, so unlike the
    classic panel this one is never overwritten with action output; results are
    routed to followup messages by ``_layout_targets_panel`` and the card stays
    on screen, live and reusable, underneath them.
    """

    is_layout_hub = True

    def __init__(
        self,
        owner_id: int,
        definition: HubDefinition,
        *,
        owner_name: str = "Cultivator",
        status_provider: Any | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.owner_name = str(owner_name)[:80]
        self.definition = definition
        self.status_provider = status_provider
        self.status_fields: list[HubStatusField] = []
        self.page_key = definition.pages[0].key if definition.pages else "empty"
        self.action_offset = 0
        self.expired = False
        self.message: discord.Message | None = None
        self.rebuild()

    @property
    def page(self) -> HubPage | None:
        for page in self.definition.pages:
            if page.key == self.page_key:
                return page
        return self.definition.pages[0] if self.definition.pages else None

    async def refresh_status(self, interaction: discord.Interaction) -> None:
        if not callable(self.status_provider):
            return
        try:
            result = self.status_provider(interaction)
            if inspect.isawaitable(result):
                result = await result
            self.status_fields = [
                item for item in list(result or [])[:6] if isinstance(item, HubStatusField)
            ]
        except Exception:
            log.exception("Could not refresh live hub status for %s", self.definition.name)

    def _header_text(self) -> str:
        icon = _hub_icon(self.definition.name)
        title = (
            self.definition.title
            if self.definition.title.startswith(icon)
            else f"{icon} {self.definition.title}"
        )
        lines = [f"## {title}", f"-# Xianxia RP  ·  {self.owner_name}"]
        # One status per line. These values were written for embed fields, where
        # each sits in its own labelled box; joining them into a single run-on
        # sentence made the card unreadable - a resource's label ended one line
        # while its bar started the next, and the whole block read as a wall.
        for status in self.status_fields:
            value = _compact_status_value(status.value)
            lines.append(f"{status.name} {value}".strip()[:200])
        return "\n".join(lines)[:900]

    def _page_text(self, page: HubPage, total: int, shown: int) -> str:
        page_index = next(
            (index for index, item in enumerate(self.definition.pages, 1) if item.key == page.key),
            1,
        )
        total_pages = max(1, len(self.definition.pages))
        if total > shown:
            first = self.action_offset + 1
            meta = (
                f"-# System {page_index}/{total_pages}  ·  "
                f"actions {first}-{self.action_offset + shown} of {total}"
            )
        else:
            meta = (
                f"-# System {page_index}/{total_pages}  ·  "
                f"{total} action{'s' if total != 1 else ''}"
            )
        body = page.description or "System actions"
        return f"### {_page_emoji(page.key)} {page.label}\n{body}\n{meta}"[:600]

    def _action_text(self, action: HubAction) -> str:
        description = " ".join(str(action.description).split())[:150] or "Run this action."
        emoji = _mapped_action_emoji(action)
        prefix = f"{emoji} " if emoji else ""
        return f"{prefix}**{action.label}**\n-# {description}"[:180]

    def rebuild(self) -> None:
        self.clear_items()
        container = discord.ui.Container(
            accent_colour=_HUB_COLOURS.get(self.definition.name, 0x5865F2)
        )
        container.add_item(discord.ui.TextDisplay(self._header_text()))

        page = self.page
        if self.expired:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "-# This panel expired. Run the command again for a live one."
                )
            )
            self.add_item(container)
            return
        if page is None:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay("No migrated actions are configured for this hub.")
            )
            self.add_item(container)
            return

        actions = _leaf_actions(page)
        total = len(actions)
        if self.action_offset >= total:
            self.action_offset = 0
        visible = actions[self.action_offset : self.action_offset + _LAYOUT_ACTION_LIMIT]

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(self._page_text(page, total, len(visible))))
        if visible:
            container.add_item(discord.ui.Separator())
        for offset, action in enumerate(visible):
            container.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(self._action_text(action)),
                    accessory=HubLayoutActionButton(
                        self, action, index=self.action_offset + offset
                    ),
                )
            )
        container.add_item(discord.ui.Separator())

        multi_page = len(self.definition.pages) > 1
        controls = discord.ui.ActionRow()
        if multi_page:
            controls.add_item(HubLayoutSystemStepButton(self, direction=-1))
        controls.add_item(HubLayoutRefreshButton(self))
        if multi_page:
            controls.add_item(HubLayoutSystemStepButton(self, direction=1))
        if total > _LAYOUT_ACTION_LIMIT:
            controls.add_item(HubLayoutActionPageButton(self, direction=-1))
            controls.add_item(HubLayoutActionPageButton(self, direction=1))
        container.add_item(controls)

        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            if not interaction.response.is_done():
                message = (
                    "This admin panel belongs to another administrator."
                    if self.definition.name == "admin"
                    else "This system panel belongs to another player."
                )
                await interaction.response.send_message(message, ephemeral=True)
            return False
        if self.definition.name == "admin":
            # Re-check the permission on every interaction, exactly as the classic
            # panel does. /admin is already gated at invoke time by
            # default_permissions and require_admin, but a panel lives for 15
            # minutes: without this, an administrator whose role is removed while
            # their panel is open keeps a working GM console until it times out.
            member = interaction.user
            if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "This panel requires the **Administrator** permission.", ephemeral=True
                    )
                return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        await _report_hub_ui_error(
            interaction, error, where=f"hub:{self.definition.name}:{type(item).__name__}"
        )

    async def on_timeout(self) -> None:
        self.expired = True
        self.rebuild()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class CommandHubView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        definition: HubDefinition,
        *,
        owner_name: str = "Cultivator",
        status_provider: Any | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.owner_name = str(owner_name)[:80]
        self.definition = definition
        self.status_provider = status_provider
        self.status_fields: list[HubStatusField] = []
        self.page_key = definition.pages[0].key if definition.pages else "empty"
        self.message: discord.Message | None = None
        self.rebuild()

    @property
    def page(self) -> HubPage | None:
        for page in self.definition.pages:
            if page.key == self.page_key:
                return page
        return self.definition.pages[0] if self.definition.pages else None

    async def refresh_status(self, interaction: discord.Interaction) -> None:
        if not callable(self.status_provider):
            return
        try:
            result = self.status_provider(interaction)
            if inspect.isawaitable(result):
                result = await result
            fields = []
            for item in list(result or [])[:6]:
                if isinstance(item, HubStatusField):
                    fields.append(item)
            self.status_fields = fields
        except Exception:
            log.exception("Could not refresh live hub status for %s", self.definition.name)

    def build_embed(self) -> discord.Embed:
        page = self.page
        icon = _hub_icon(self.definition.name)
        panel_title = self.definition.title if self.definition.title.startswith(icon) else f"{icon} {self.definition.title}"
        embed = discord.Embed(
            title=panel_title,
            color=_HUB_COLOURS.get(self.definition.name, 0x5865F2),
        )
        embed.set_author(name="Xianxia RP • Interactive Game Panel")
        if page is None:
            embed.description = f"*{self.definition.description}*"
            embed.add_field(name="No actions", value="No migrated actions are configured for this hub.", inline=False)
            return embed

        actions = _leaf_actions(page)
        page_index = next((idx for idx, item in enumerate(self.definition.pages, 1) if item.key == page.key), 1)
        total_pages = max(1, len(self.definition.pages))
        embed.description = (
            f"### {_page_emoji(page.key)} {page.label}\n"
            f"{page.description or 'System actions'}\n"
            f"`{page_index}/{total_pages}`  •  {len(actions)} action{'s' if len(actions) != 1 else ''}"
        )

        for status in self.status_fields:
            embed.add_field(
                name=status.name[:256],
                value=status.value[:1024] or "—",
                inline=status.inline,
            )

        embed.add_field(
            name="⚡ Quick Actions",
            value=_quick_action_listing(actions),
            inline=False,
        )
        embed.add_field(
            name="📖 What can I do here?",
            value=_action_listing(actions, limit=760),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"{self.owner_name} • Select a section, choose an action, or tap a quick button • "
                "Refresh updates live game state • Panel expires after 15 minutes"
            )
        )
        return embed

    def rebuild(self) -> None:
        self.clear_items()
        if len(self.definition.pages) > 1:
            self.add_item(HubPageSelect(self, row=0))
            action_row = 1
        else:
            action_row = 0
        page = self.page
        actions = _leaf_actions(page) if page is not None else []
        if actions:
            self.add_item(HubActionSelect(self, actions, row=action_row))
        quick_row = action_row + 1
        for index, action in enumerate(actions[:_QUICK_ACTION_LIMIT]):
            self.add_item(HubQuickActionButton(self, action, index=index, row=quick_row))
        controls_row = quick_row + 1
        if len(self.definition.pages) > 1:
            self.add_item(HubPageButton(self, direction=-1, row=controls_row))
        self.add_item(HubRefreshButton(self, row=controls_row))
        if len(self.definition.pages) > 1:
            self.add_item(HubPageButton(self, direction=1, row=controls_row))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            if not interaction.response.is_done():
                message = (
                    "This admin panel belongs to another administrator."
                    if self.definition.name == "admin"
                    else "This system panel belongs to another player."
                )
                await interaction.response.send_message(message, ephemeral=True)
            return False
        if self.definition.name == "admin":
            member = interaction.user
            if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "This panel requires the **Administrator** permission.", ephemeral=True
                    )
                return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_hub_ui_error(
            interaction, error,
            where=f"hub:{self.definition.name}:{type(item).__name__}",
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def _use_layout_hub(definition: HubDefinition) -> bool:
    return LAYOUT_COMPONENTS_AVAILABLE and definition.name in LAYOUT_HUB_NAMES


async def _send_classic_hub(
    interaction: discord.Interaction,
    definition: HubDefinition,
    status_provider: Any | None,
) -> "CommandHubView":
    view = CommandHubView(
        interaction.user.id,
        definition,
        owner_name=getattr(interaction.user, "display_name", str(interaction.user)),
        status_provider=status_provider,
    )
    await view.refresh_status(interaction)
    if interaction.response.is_done():
        await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=False)
    else:
        await interaction.response.send_message(
            embed=view.build_embed(), view=view, ephemeral=False
        )
    return view


async def send_hub(
    interaction: discord.Interaction,
    definition: HubDefinition,
    *,
    status_provider: Any | None = None,
) -> None:
    view: Any = None
    if _use_layout_hub(definition):
        try:
            view = LayoutHubView(
                interaction.user.id,
                definition,
                owner_name=getattr(interaction.user, "display_name", str(interaction.user)),
                status_provider=status_provider,
            )
            await view.refresh_status(interaction)
            # Layout components are built in rebuild(), so live status has to be
            # folded in before sending rather than at render time like the embed.
            view.rebuild()
            await interaction.response.send_message(view=view, ephemeral=False)
        except Exception:
            # Any discord.py/API disagreement about Components V2 degrades to the
            # classic panel rather than failing the player's command outright.
            log.exception(
                "Components V2 hub panel unavailable for %s; using the classic panel",
                definition.name,
            )
            view = None
    if view is None:
        view = await _send_classic_hub(interaction, definition, status_provider)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        view.message = None
