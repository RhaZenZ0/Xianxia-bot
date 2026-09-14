"""`/cooldowns`: what you are waiting on, and what you can do right now.

The engine meters two dozen waits and half a dozen more that live outside the
cooldown table entirely — a road journey, closed-door seclusion, the sect
trial's retry, the Samsara wait. Until this command there was no way to see any
of them: a player found a cooldown by trying the action and reading the
refusal, and the cultivation sheet showed exactly one.

The reading is the engine's (`cooldown.status`), which normalises three clocks
onto one axis so this module never does arithmetic on a wait. What is Python's
here is only presentation: the words for each family, the names behind the ids
(a manual, a law, a beast), and the one piece of filtering that is
configuration rather than rule — a listing site nobody has set means there is
no patron's gift to be ready for.

Every ready line names its action as a hub path, the way the rest of the bot
does, so `tests/python/unit/test_hint_paths.py` holds them to the live surface:
a page that moves takes this card's wording with it.
"""

from __future__ import annotations

import discord

from ...ops.game_engine import GameEngineError
from ..registry import registered_root_command
from ..runtime import (
    _explain_engine_error,
    DB,
    ENGINE,
    SETTINGS,
    WORLD,
    format_wait,
    log,
    require_character,
)
from ..services import GUILD

# family -> (mark, what it is, where it is done). The path is the repo's way of
# naming a reachable action; a bare root like **/tribute** is a command instead.
# `tests/python/unit/test_cooldowns_command.py` holds this table to the engine
# roster in go_core/internal/game/cooldown_status.go, in both directions.
FAMILY_LABELS: dict[str, tuple[str, str, str]] = {
    "cultivate": ("🧘", "Cultivate", "**/cultivation → Cultivate → Cultivate**"),
    "body_cultivate": ("🥋", "Temper the body", "**/cultivation → Body → Cultivate**"),
    "explore": ("🧭", "Explore", "**/world → Act → Explore**"),
    "hunt": ("🗡️", "Hunt", "**/world → Act → Hunt**"),
    "secret_realm": ("🌀", "Enter a secret realm", "**/realm → Secret Realms → Explore**"),
    "perfect_quest": ("✨", "Perfection quest", "**/ascend → Perfection → Quest**"),
    "perfect_trial": ("⚔️", "Perfection trial", "**/ascend → Perfection → Trial**"),
    "body_perfect_quest": ("✨", "Body perfection quest", "**/ascend → Perfection → Quest**"),
    "body_perfect_trial": ("⚔️", "Body perfection trial", "**/ascend → Perfection → Trial**"),
    "support_vote": ("🎁", "The patron's tribute", "**/tribute**"),
    "alchemy_purge": ("🧪", "Purge pill toxicity", "**/craft → Alchemy → Purge**"),
    "alchemy_forage": ("🌿", "Forage for herbs and craft makings", "**/craft → Alchemy → Forage**"),
    "beast_tame": ("🐾", "Tame a beast", "**/beast → Companions → Tame**"),
    "meridian_heal": ("🩹", "Heal a meridian", "**/cultivation → Qi Body → Heal**"),
    "qi_refine": ("💠", "Refine your qi", "**/cultivation → Qi Body → Refine**"),
    "ghost_harvest": ("👻", "Harvest death qi", "**/cultivation → Ghost → Harvest**"),
    "ghost_appease": ("🕯️", "Appease the dead", "**/cultivation → Ghost → Appease**"),
    "dao_dual_cultivation": ("❤️", "Dual cultivation", "**/character → Dao Partnership → Dual Cultivate**"),
    # Composite families: only ever drawn while a row exists, so the subject
    # carries the meaning and the path is where that subject is worked.
    "beast_feed": ("🍖", "Feed", "**/beast → Companions → Feed**"),
    "beast_train": ("🐉", "Train", "**/beast → Companions → Train**"),
    "artifact_bond": ("🔮", "Artifact bond", "**/items → Artifacts → Bond**"),
    "aptitude_temper": ("🌱", "Temper", "**/cultivation → Path → Temper**"),
    "aptitude_harmonize": ("🎼", "Harmonize", "**/cultivation → Path → Harmonize**"),
    "aptitude_awaken": ("🌟", "Awaken", "**/cultivation → Path → Awaken**"),
    "aptitude_evolve": ("🦋", "Evolve", "**/cultivation → Path → Evolve**"),
    "law": ("☯️", "Law", "**/cultivation → Laws → Comprehend**"),
    "manual": ("📖", "Manual", "**/cultivation → Arts → Study**"),
    "war_action": ("🏴", "War", "**/sect → War → Act**"),
    # Waits that are not cooldown rows at all.
    "road_transit": ("🛣️", "On the road", "**/travel**"),
    "seclusion": ("🚪", "Closed-door seclusion", "**/cultivation → Cultivate → Status**"),
    "sect_trial_retry": ("🏯", "Sect entrance trial", "**/sect → Recruitment → Trial**"),
    "secret_realm_run": ("🌀", "Secret realm seal", "**/realm → Secret Realms → Status**"),
    "reincarnation": ("🪷", "Samsara", "**/character → Samsara → Reincarnate**"),
    "muted": ("🤐", "Muted by a GM", ""),
    "frozen": ("🧊", "Frozen by a GM", ""),
}

# The three targets an aptitude cooldown can name; a closed set, so no lookup.
_APTITUDE_TARGETS = {"root": "spiritual root", "bloodline": "bloodline", "physique": "physique"}
_BEAST_FAMILIES = ("beast_feed", "beast_train")


def _subject_name(family: str, subject: str, beasts: dict[str, str]) -> str:
    """The id in a composite key, in words. Falls back to the id itself: a
    catalogue that cannot name something must not cost the player the row."""
    if not subject:
        return ""
    if family == "manual":
        definition = WORLD.manual_definition(subject) or {}
        return str(definition.get("name") or subject)
    if family == "law":
        definition = WORLD.law_definition(subject) or {}
        return str(definition.get("name") or subject)
    if family == "artifact_bond":
        return WORLD.item_name(subject)
    if family.startswith("aptitude_"):
        return _APTITUDE_TARGETS.get(subject, subject)
    if family in _BEAST_FAMILIES:
        return beasts.get(subject, f"beast #{subject}")
    if family == "war_action":
        return f"war #{subject}"
    return subject


def _wait_line(row: dict, beasts: dict[str, str]) -> str:
    family = str(row.get("family") or "")
    mark, label, _ = FAMILY_LABELS.get(family, ("⏳", family.replace("_", " ").title(), ""))
    subject = _subject_name(family, str(row.get("subject") or ""), beasts)
    name = f"{label} — **{subject}**" if subject else f"**{label}**"
    # A wait on the world clock has no real moment when the GM has stopped the
    # world. Say that, rather than printing a countdown to 1970.
    if not row.get("scheduled"):
        minutes = int(row.get("remaining_game_minutes") or 0)
        return f"• {mark} {name} — **{minutes}** world-minutes out, but the world clock is stopped"
    available = int(row.get("available_at_unix") or 0)
    when = f"<t:{available}:R>" if available else format_wait(int(row.get("remaining_seconds") or 0))
    verb = "arrives" if family == "road_transit" else "ready"
    return f"• {mark} {name} — {verb} {when}"


def _ready_line(row: dict) -> str:
    family = str(row.get("family") or "")
    mark, label, path = FAMILY_LABELS.get(family, ("✅", family.replace("_", " ").title(), ""))
    return f"• {mark} {label} — {path}" if path else f"• {mark} {label}"


async def _beast_names(user_id: int, rows: list[dict]) -> dict[str, str]:
    """Beast ids to names, and only when a beast is actually on the card: the
    rest of the subjects resolve against content already in this process."""
    if not any(str(row.get("family")) in _BEAST_FAMILIES for row in rows):
        return {}
    try:
        return {str(beast.get("beast_id")): str(beast.get("name") or f"beast #{beast.get('beast_id')}")
                for beast in await DB.get_spirit_beasts(user_id)}
    except Exception:
        log.exception("Could not name the spirit beasts on the cooldown card")
        return {}


@registered_root_command(
    name="cooldowns",
    description="What you are waiting on, and what you can do right now",
    guild=GUILD,
)
async def cooldowns(interaction: discord.Interaction) -> None:
    character = await require_character(interaction)
    if not character:
        return
    await interaction.response.defer(ephemeral=False)
    try:
        status = dict(await ENGINE.action("cooldown.status", interaction.user.id, {}) or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    waits = [dict(row) for row in (status.get("waits") or [])]
    ready = [dict(row) for row in (status.get("ready") or [])]
    beasts = await _beast_names(interaction.user.id, waits)
    lines = [f"⏳ **Cooldowns — {character['name']}**"]
    if waits:
        lines.append("\n**Waiting**")
        lines.extend(_wait_line(row, beasts) for row in waits)
    else:
        lines.append("\n**Waiting** — nothing. Everything you can do, you can do now.")
    if ready:
        lines.append("\n**Ready now**")
        lines.extend(_ready_line(row) for row in ready)
    lines.append("\n-# The times count themselves down. Run **/cooldowns** again for a fresh reading.")
    await interaction.followup.send("\n".join(lines), ephemeral=False)
