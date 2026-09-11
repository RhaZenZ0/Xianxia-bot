"""The /secretrealm hub: entering and exploring temporary hidden realms.

Split phase 9a (v0.19.44, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ..formatting import human_duration, roll_line
from ..hubs import register_hub_option_hint
from ..registry import registered_group_command
from ..runtime import ENGINE, SETTINGS, WORLD, current_world_time, require_character, serialized_user_action

# ---------------- Secret Realm commands ----------------
secret_group = app_commands.Group(name="secretrealm", description="Enter and explore temporary hidden realms")


@registered_group_command(secret_group, name="status", description="Show secret realms available at your location or your active run")
async def secret_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.action("secret_realm.status", interaction.user.id, {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"Secret-realm status could not be read: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if bool(result.get("active")):
        run = dict(result.get("run") or {})
        realm = dict(run.get("realm") or {})
        room = run.get("room")
        room_name = dict(room or {}).get("name") if room else "Inheritance Chamber Complete"
        remaining = max(0, int(float(run.get("expires_at") or 0) - time.time()))
        text = (
            f"🌀 **Inside {realm.get('name', run.get('realm_id', 'Secret Realm'))}**\n"
            f"Room: **{room_name}**\nDanger: **{int(run.get('danger') or 0)}/3**\n"
            f"Closes in: **{human_duration(remaining)}**"
        )
        await interaction.response.send_message(text, ephemeral=False)
        return
    rotation = dict(result.get("rotation") or {})
    rotation_line = _rotation_line(rotation)
    available = list(result.get("available") or [])
    if not available:
        await interaction.response.send_message("No secret-realm entrance is currently open at your location." + rotation_line, ephemeral=False)
        return
    lines = ["🌀 **Open Secret Realms Here**"]
    for entry in available:
        entry = dict(entry or {})
        realm = dict(entry.get("realm") or {})
        thread_ref = f" — scene <#{entry['thread_id']}>" if entry.get("thread_id") else ""
        remaining = max(0, int(float(entry.get("ends_at") or 0) - time.time()))
        lines.append(
            f"\n**{realm.get('name', entry.get('realm_id', 'Secret Realm'))}** — minimum realm: "
            f"{WORLD.realm_name(int(realm.get('min_realm_index') or 0))} — closes in {human_duration(remaining)}{thread_ref}"
        )
    await interaction.response.send_message("".join(lines) + rotation_line, ephemeral=False)


def _rotation_line(rotation: dict) -> str:
    """The rotation the tick keeps (v1.0.0-rc.2): which realm opens next and
    where, so a player can be standing at the ruin when it does."""
    next_id = str(rotation.get("next_realm_id") or "")
    realm = dict(WORLD.secret_realms.get(next_id) or {}) if next_id else {}
    if not realm:
        return ""
    last = str(rotation.get("last_realm_id") or "")
    last_realm = dict(WORLD.secret_realms.get(last) or {}) if last else {}
    line = f"\n\n🔄 **Next on the rotation:** {realm.get('name', next_id)} at **{realm.get('location', 'an unknown place')}**, on the world tick about game day {int(rotation.get('next_game_minute') or 0) // 1440 + 1}."
    if last_realm:
        line += f" Last opened: {last_realm.get('name', last)} at {last_realm.get('location', '?')}."
    return line


@registered_group_command(secret_group, name="enter", description="Enter an open secret realm by name")
@serialized_user_action
async def secret_enter(interaction: discord.Interaction, realm: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "secret_realm.enter", interaction.user.id,
            {"realm_id": realm},
            action_id=f"discord:{interaction.id}:secret_realm.enter",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"You cannot enter that secret realm: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    info = dict(result.get("realm") or {})
    first = dict(result.get("first_room") or {})
    thread_ref = f"\n\n💬 Shared expedition thread: <#{result['thread_id']}>" if result.get("thread_id") else ""
    await interaction.followup.send(
        f"🌀 **{c['name']} enters {info.get('name', result.get('realm_id', 'the secret realm'))}.**\n"
        f"{info.get('description', '')}\n\nFirst area: **{first.get('name', 'Unknown Chamber')}**\n"
        f"{first.get('description', '')}\nUse **/realm → Secret Realms → Explore**.{thread_ref}",
        ephemeral=False,
    )


@secret_enter.autocomplete("realm")
async def open_realm_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The realms open at the player's location right now (v0.33.0), read
    from the same engine query /secretrealm status shows - so the picker
    never offers an entrance the status line would not."""
    try:
        envelope = await ENGINE.action("secret_realm.status", interaction.user.id, {})
    except GameEngineError:
        return []
    result = dict(envelope.get("result") or {})
    needle = current.casefold().strip()
    choices: list[app_commands.Choice[str]] = []
    for entry in list(result.get("available") or []):
        entry = dict(entry or {})
        realm = dict(entry.get("realm") or {})
        realm_id = str(entry.get("realm_id") or realm.get("id") or "")
        name = str(realm.get("name") or realm_id)
        if not realm_id or (needle and needle not in name.casefold() and needle not in realm_id.casefold()):
            continue
        remaining = max(0, int(float(entry.get("ends_at") or 0) - time.time()))
        choices.append(app_commands.Choice(
            name=f"{name} — min. {WORLD.realm_name(int(realm.get('min_realm_index') or 0))}, closes in {human_duration(remaining)}"[:100],
            value=realm_id[:100],
        ))
    return choices[:25]


register_hub_option_hint(
    secret_enter,
    "realm",
    "No secret-realm entrance is open where you stand. They open as world events — "
    "**/world → Events** lists the ones running, with their locations.",
)


@registered_group_command(secret_group, name="explore", description="Attempt the next area of your active secret realm")
@serialized_user_action
async def secret_explore(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "secret_realm.explore", interaction.user.id,
            {
                
                "cooldown_seconds": SETTINGS.secret_realm_cooldown_minutes * 60,
            },
            action_id=f"discord:{interaction.id}:secret_realm.explore",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"The secret realm resists your attempt: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    room = dict(result.get("room") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    text = (
        f"🌀 **{result.get('realm_name', 'Secret Realm')} — {room.get('name', 'Unknown Area')}**\n"
        f"{room.get('description', '')}\n\n{roll_line(roll)}"
    )
    if bool(result.get("success")):
        cultivation = int(result.get("cultivation_awarded") or 0)
        stones = int(result.get("spirit_stones") or 0)
        insight = int(result.get("insight_xp") or 0)
        items = dict(result.get("items") or {})
        text += f"\n✨ Area cleared. **+{cultivation} cultivation, +{stones} spirit stones, +{insight} insight**"
        if items:
            text += f", {WORLD.item_names(items)}"
        if bool(result.get("final_room")):
            inheritance = dict(result.get("inheritance") or {})
            if bool(inheritance.get("gained")):
                bonuses = dict(inheritance.get("bonuses") or {})
                text += (
                    f"\n\n📜 **INHERITANCE OBTAINED — {inheritance.get('name', 'Ancient Legacy')}**\n"
                    f"{inheritance.get('description', '')}\nPermanent gains: "
                    f"+{int(bonuses.get('qi_max') or 0)} Max Qi, "
                    f"+{int(bonuses.get('vitality_max') or 0)} Max Vitality, "
                    f"+{int(bonuses.get('insight_xp') or 0)} Insight XP."
                )
            else:
                text += "\n\nThe inheritance recognizes that you already carry this legacy and grants no duplicate permanent bonus."
        else:
            next_room = dict(result.get("next_room") or {})
            if next_room:
                text += f"\n\nNext: **{next_room.get('name', 'Unknown Area')}**"
    else:
        danger = int(result.get("danger") or 0)
        text += f"\n⚠️ The realm rejects your attempt. Danger rises to **{danger}/3**."
        if bool(result.get("ejected")):
            text += "\n💥 Space collapses around you and forcibly ejects you from the secret realm. You keep rewards already earned."
    await interaction.followup.send(text, ephemeral=False)


@registered_group_command(secret_group, name="leave", description="Leave your active secret realm voluntarily")
@serialized_user_action
async def secret_leave(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "secret_realm.leave", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:secret_realm.leave",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"You cannot leave the secret realm cleanly: {exc}", ephemeral=False)
        return
    left = bool(dict(envelope.get("result") or {}).get("left"))
    message = (
        "You withdraw before the secret realm seals. Rewards already obtained are kept."
        if left else "You are not currently inside an active secret realm."
    )
    await interaction.followup.send(message, ephemeral=False)


