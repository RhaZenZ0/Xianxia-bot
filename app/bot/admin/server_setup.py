"""Server setup and the /admin server commands: complete setup/repair,
permission and configuration reports, realm access roles, the dashboard's
Discord bridge, the chat monitor, and the wipe-and-rebuild of managed
channel messages.

Phase 6 of the main.py split (v0.19.41, docs/MAIN_SPLIT_PLAN.md). Imports
channel_messages and bugs_forum (never the reverse), core, channels,
services, runtime; never main.py. The two dashboard entry points used to be
annotated with main.py's XianxiaBot class; they take the same object and
are annotated with its discord.ext base class here, since the bot class
still lives in main.py until phase 8. Definition order is the order these
had in main.py.

The three /admin server commands not here (maintenance, backup, audit)
live further down main.py with the other admin operations and move in
phase 7.
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ...ai import chat_monitor
from ...database import SCHEMA_VERSION
from ...rules.realm_hubs import REALM_HUBS, realm_hub
from ...version import RELEASE_VERSION
from ..channels import (
    _ensure_realm_access_roles,
    _resolve_text_channel,
    configured_info_channel,
    ensure_realm_hub_channels,
    post_server_log,
)
from ..registry import registered_group_command
from ..runtime import DB, ENGINE, SETTINGS, WORLD, _realm_access_role_name, _sync_realm_access_roles, log, reply_long
from ..services import AI_ROUTER, ALERTS, GUILD, NARRATOR, SIM
from .bugs_forum import (
    BUGS_CHANNEL_NAME,
    BUGS_FORUM_TAGS,
    BUGS_GUIDELINES_KEY,
    DEFAULT_BUGS_GUIDELINES,
    bugs_forum_reports,
    ensure_bugs_forum_channel,
    missing_bugs_forum_tags,
)
from .channel_messages import (
    BASE_CHANNEL_SETUP_CHOICES,
    BASE_CHANNEL_SPECS,
    CHANNEL_MESSAGE_DEFAULT,
    CHANNEL_MESSAGE_DISABLED,
    CHANNEL_MESSAGE_KEYS,
    CHANNEL_MESSAGE_LABELS,
    CHANNEL_WIPE_KEYS,
    DEFAULT_CHANNEL_MESSAGES,
    _base_channel_bindings,
    _resolve_channel_message_target,
    channel_message_state,
    ensure_all_channel_messages,
    ensure_base_xianxia_channels,
    ensure_channel_message,
    ensure_xianxia_info_guide,
    resolve_channel_message_content,
)
from .core import admin_server_group, audit_admin, require_admin

@registered_group_command(admin_server_group, name="bind_channels", description="Manually bind existing Xianxia event, home, log and new-player channels")
async def admin_setup(
    interaction: discord.Interaction,
    announcements: discord.TextChannel,
    scenes: discord.TextChannel,
    homes: discord.TextChannel | None = None,
    logs: discord.TextChannel | None = None,
    begin: discord.TextChannel | None = None,
) -> None:
    if not await require_admin(interaction):
        return
    if interaction.guild is None:
        return
    existing = await DB.get_server_config(interaction.guild.id)
    await DB.set_server_channels(
        interaction.guild.id,
        announcement_channel_id=announcements.id,
        event_scene_channel_id=scenes.id,
        home_scene_channel_id=(homes or scenes).id,
        log_channel_id=(logs.id if logs else existing.get("log_channel_id")),
        begin_channel_id=(begin.id if begin else existing.get("begin_channel_id")),
    )
    await audit_admin(
        interaction, "server.bind_channels", target=f"guild:{interaction.guild.id}",
        after={
            "announcements": announcements.id, "scenes": scenes.id, "homes": (homes or scenes).id,
            "logs": logs.id if logs else existing.get("log_channel_id"),
            "begin": begin.id if begin else existing.get("begin_channel_id"),
        },
    )
    cfg = await DB.get_server_config(interaction.guild.id)
    log_ch = await _resolve_text_channel(interaction.guild, cfg.get("log_channel_id"))
    begin_ch = await _resolve_text_channel(interaction.guild, cfg.get("begin_channel_id"))
    await interaction.response.send_message(
        "✅ **Xianxia server setup saved.**\n"
        f"Announcements: {announcements.mention}\n"
        f"Event scenes/threads: {scenes.mention}\n"
        f"Private player-owned location threads: {(homes or scenes).mention}\n"
        f"Operational logs: {log_ch.mention if log_ch else '**not configured**'}\n"
        f"New-player /begin channel: {begin_ch.mention if begin_ch else '**not configured**'}\n\n"
        "Settings persist in SQLite across restarts.",
        ephemeral=False,
    )


@registered_group_command(admin_server_group, name="status", description="Show the current event-channel setup and permissions")
async def admin_status(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    cfg = await DB.get_server_config(guild.id)
    ann_id = cfg.get("announcement_channel_id")
    scene_id = cfg.get("event_scene_channel_id")
    home_id = cfg.get("home_scene_channel_id") or scene_id
    log_id = cfg.get("log_channel_id")
    begin_id = cfg.get("begin_channel_id")
    ann = await _resolve_text_channel(guild, ann_id)
    scene = await _resolve_text_channel(guild, scene_id)
    home = await _resolve_text_channel(guild, home_id)
    log_ch = await _resolve_text_channel(guild, log_id)
    begin_ch = await _resolve_text_channel(guild, begin_id)

    lines = [
        "⚙️ **Xianxia Server Setup**",
        f"\nAnnouncements: {ann.mention if ann else '**not configured**'}",
        f"\nEvent scenes: {scene.mention if scene else '**not configured**'}",
        f"\nPrivate homes: {home.mention if home else '**not configured**'}",
        f"\nOperational logs: {log_ch.mention if log_ch else '**not configured**'}",
        f"\nNew-player /begin: {begin_ch.mention if begin_ch else '**not configured**'}",
    ]
    if scene and guild.me:
        perms = scene.permissions_for(guild.me)
        checks = {
            "View Channel": perms.view_channel,
            "Send Messages": perms.send_messages,
            "Create Public Threads": perms.create_public_threads,
            "Send in Threads": perms.send_messages_in_threads,
            "Manage Threads": perms.manage_threads,
        }
        lines.append("\n\n**Event-scenes permissions**")
        for label, ok in checks.items():
            lines.append(f"\n{'✅' if ok else '❌'} {label}")
        if not perms.manage_threads:
            lines.append("\n\n⚠️ **Manage Threads** is needed to reliably lock/archive expired secret realms.")
    if home and guild.me:
        perms = home.permissions_for(guild.me)
        lines.append("\n\n**Private-home permissions**")
        for label, ok in {
            "View Channel": perms.view_channel,
            "Send Messages": perms.send_messages,
            "Create Private Threads": perms.create_private_threads,
            "Send in Threads": perms.send_messages_in_threads,
            "Manage Threads": perms.manage_threads,
        }.items():
            lines.append(f"\n{'✅' if ok else '❌'} {label}")
    hub_rows = await DB.get_realm_hub_channels(guild.id)
    lines.append("\n\n**Realm-capital meeting channels**")
    by_world={str(r['world_name']):r for r in hub_rows}
    for world,hub in REALM_HUBS.items():
        row=by_world.get(world); channel=guild.get_channel(int(row['channel_id'])) if row else None
        lines.append(f"\n{'✅' if isinstance(channel,discord.TextChannel) else '❌'} {world}: {channel.mention if isinstance(channel,discord.TextChannel) else 'not provisioned'}")
    await interaction.response.send_message("".join(lines), ephemeral=False)


async def clear_managed_channel_messages(guild: discord.Guild) -> dict[str, Any]:
    """'Fresh look': delete and recreate every Xianxia-managed channel that's safe to
    fully wipe (CHANNEL_WIPE_KEYS, every realm-capital hub, and the #bugs forum), then
    reprovision and repost everything the same way Full Setup/Repair does. Deleting and
    recreating clears all message history instantly and completely, regardless of
    message age - Discord's bulk-delete API only works on messages under 14 days old,
    so purging message-by-message would be slow, heavily rate-limited, and incomplete
    for anything older. The tradeoff: any *custom* permission overwrites a GM added by
    hand to these channels are lost (the bot only reapplies its own baseline
    overwrites), and channels reappear at the bottom of their category rather than
    their old position.
    """
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    cleared: list[str] = []
    skipped: list[str] = []

    for key in CHANNEL_WIPE_KEYS:
        channel = await _resolve_text_channel(guild, bindings.get(key))
        if channel is None:
            continue
        try:
            await channel.delete(reason="Xianxia RP fresh-start channel wipe")
            cleared.append(key)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not delete #%s for fresh-start wipe", key)
            skipped.append(key)

    realm_channel_rows = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    for world, row in realm_channel_rows.items():
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            await channel.delete(reason="Xianxia RP fresh-start channel wipe")
            cleared.append(f"realm:{world}")
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not delete realm hub #%s for fresh-start wipe", channel.name)
            skipped.append(f"realm:{world}")

    bugs_channel_id = cfg.get("bugs_channel_id")
    bugs_channel = guild.get_channel(int(bugs_channel_id)) if bugs_channel_id else None
    if isinstance(bugs_channel, discord.ForumChannel):
        try:
            await bugs_channel.delete(reason="Xianxia RP fresh-start channel wipe")
            cleared.append("bugs")
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not delete #%s for fresh-start wipe", BUGS_CHANNEL_NAME)
            skipped.append("bugs")

    # Recreate everything just deleted (plus anything else still missing) from scratch,
    # exactly like Full Setup/Repair - name-based rebinding means the stale channel ids
    # left behind by the deletes above self-heal here without any extra bookkeeping.
    base_result, _realm_rows, bugs_warning = await _run_complete_server_setup(guild, create_missing=True)
    info_channel = base_result["channels"].get("xianxia-info")
    if info_channel is not None:
        await ensure_xianxia_info_guide(guild, info_channel)
    await ensure_all_channel_messages(guild)
    if bugs_warning:
        skipped.append(f"bugs recreation: {bugs_warning}")

    return {"cleared": cleared, "skipped": skipped, "protected": ["player-homes", "expeditions", "event-scenes"]}


@registered_group_command(admin_server_group, name="basechannels", description="Inspect dashboard-managed base Xianxia Discord channels")
@app_commands.choices(action=BASE_CHANNEL_SETUP_CHOICES)
async def admin_base_channels(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    category_name: str = "📜 Xianxia RP",
) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return

    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    if action.value == "status":
        lines = ["📜 **Base Xianxia Channels**"]
        for name, channel_id in bindings.items():
            channel = await _resolve_text_channel(guild, channel_id)
            lines.append(f"• `#{name}`: {channel.mention if channel else '*not configured*'}")
        if guild.me:
            lines.append(f"\nManage Channels permission: **{'yes' if guild.me.guild_permissions.manage_channels else 'NO'}**")
            lines.append(f"Manage Roles permission: **{'yes' if guild.me.guild_permissions.manage_roles else 'NO'}**")
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return

    await interaction.response.defer(ephemeral=False)
    try:
        result = await ensure_base_xianxia_channels(guild, category_name=category_name)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.followup.send(f"❌ Could not validate the configured base Discord layout: {exc}", ephemeral=False)
        return
    await audit_admin(
        interaction, "server.basechannels", target=f"guild:{guild.id}",
        after={
            "category_id": result["category"].id if result["category"] else None,
            "created": result["created"],
            "repaired": result["repaired"],
        },
    )
    warning_text = "".join(f"\n⚠️ {warning}" for warning in result["warnings"])
    await interaction.followup.send(
        "✅ **Base Xianxia channels are connected.**\n"
        + "\n".join(f"• `#{name}` → {channel.mention}" for name, channel in result["channels"].items())
        + warning_text,
        ephemeral=False,
    )


SERVER_SETUP_CHOICES = [
    app_commands.Choice(name="Setup Server", value="setup"),
    app_commands.Choice(name="Repair Server", value="repair"),
    app_commands.Choice(name="Check Permissions", value="permissions"),
    app_commands.Choice(name="Show Configuration", value="configuration"),
    app_commands.Choice(name="Sync Realm Roles", value="sync_roles"),
    app_commands.Choice(name="Rebuild Info Guide", value="rebuild_info"),
]


SERVER_BASE_CATEGORY = "📜 Xianxia RP"


SERVER_REALM_CATEGORY = "🌌 Realm Capitals"


def _server_permission_report(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """Return human-readable permission diagnostics and actionable warnings."""
    me = guild.me
    if me is None:
        return ["❌ Bot member state is unavailable."], ["Discord did not expose the bot member for this guild."]

    perms = me.guild_permissions
    lines: list[str] = [
        f"{'✅' if perms.manage_channels else 'ℹ️'} **Manage Channels** — optional; channel creation and layout are owned by the admin dashboard."
    ]
    checks = (
        ("Manage Threads", perms.manage_threads, "Private expedition/property threads cannot be reliably recovered or archived."),
        ("Create Private Threads", perms.create_private_threads, "Private expedition and player-location threads cannot be created."),
        ("Send Messages in Threads", perms.send_messages_in_threads, "The bot cannot narrate or update private scene threads."),
        ("Manage Roles", perms.manage_roles, "Realm-role creation and synchronization will not work."),
        ("View Channels", perms.view_channel, "The bot cannot resolve configured game channels."),
        ("Send Messages", perms.send_messages, "The bot cannot post setup guides, events or normal game responses."),
        ("Read Message History", perms.read_message_history, "Persistent scene and guide recovery may be incomplete."),
        ("Embed Links", perms.embed_links, "Some game panels and rich status responses will be degraded."),
    )
    warnings: list[str] = []
    for label, ok, consequence in checks:
        lines.append(f"{'✅' if ok else '❌'} **{label}**" + ("" if ok else f"\n   {consequence}"))
        if not ok:
            warnings.append(f"{label}: {consequence}")

    realm_roles = [
        role for world in REALM_HUBS
        if (role := discord.utils.get(guild.roles, name=_realm_access_role_name(world))) is not None
    ]
    if not perms.manage_roles:
        lines.append("❌ **Realm Role Hierarchy**\n   Cannot validate/manage realm roles until **Manage Roles** is granted.")
    elif not realm_roles:
        lines.append("⚠️ **Realm Role Hierarchy**\n   Realm roles are not provisioned yet; **Setup Server** can create them.")
    else:
        blocked = [role for role in realm_roles if me.top_role.position <= role.position]
        if blocked:
            names = ", ".join(role.name for role in blocked[:4])
            lines.append(
                "❌ **Realm Role Hierarchy**\n"
                f"   Move the bot's highest role above: {names}. Realm-role syncing will fail while those roles are above/equal to the bot."
            )
            warnings.append("Bot role hierarchy is below one or more generated realm-access roles.")
        else:
            lines.append("✅ **Realm Role Hierarchy** — bot role is above all generated realm-access roles.")
    return lines, warnings


async def _server_configuration_report(guild: discord.Guild) -> str:
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    lines = [
        "⚙️ **Xianxia Server Configuration**",
        f"Guild: **{guild.name}** (`{guild.id}`)",
        f"Release: **{RELEASE_VERSION}** • Database schema: **v{SCHEMA_VERSION}**",
        "\n**Base channel bindings**",
    ]
    for name, channel_id in bindings.items():
        channel = await _resolve_text_channel(guild, channel_id)
        if channel is not None:
            lines.append(f"✅ `#{name}` → {channel.mention} (`{channel.id}`)")
        elif channel_id:
            lines.append(f"❌ `#{name}` → stale/missing (`{channel_id}`)")
        else:
            lines.append(f"⚪ `#{name}` → not configured")

    lines.append("\n**Realm-capital bindings**")
    rows = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    for world, hub in REALM_HUBS.items():
        row = rows.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        role = discord.utils.get(guild.roles, name=_realm_access_role_name(world))
        channel_text = channel.mention if isinstance(channel, discord.TextChannel) else "not configured"
        role_text = role.mention if role is not None else "not configured"
        lines.append(f"• **{world}** — channel: {channel_text} • role: {role_text}")

    bugs_channel_id = cfg.get("bugs_channel_id")
    bugs_channel = guild.get_channel(int(bugs_channel_id)) if bugs_channel_id else None
    bugs_text = f"#{bugs_channel.name}" if isinstance(bugs_channel, discord.ForumChannel) else ("stale/missing" if bugs_channel_id else "not configured")
    lines.append(f"\nBug-report forum: {bugs_text}")

    info_id = cfg.get("info_message_id")
    lines.append(f"\nInfo guide message ID: `{info_id}`" if info_id else "\nInfo guide message ID: *not recorded*")
    updated_at = cfg.get("updated_at")
    if updated_at:
        lines.append(f"Configuration updated: <t:{int(float(updated_at))}:R>")
    return "\n".join(lines)


async def _sync_all_realm_access_roles(guild: discord.Guild) -> dict[str, int]:
    """Reconcile generated realm visibility roles for every character still in the guild."""
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        raise PermissionError("Manage Roles is required")

    role_map = await _ensure_realm_access_roles(guild)
    blocked = [role for role in role_map.values() if me.top_role.position <= role.position]
    if blocked:
        raise PermissionError(
            "Move the bot role above the generated realm roles before syncing: "
            + ", ".join(role.name for role in blocked[:4])
        )

    counts = {"characters": 0, "members": 0, "synced": 0, "not_in_guild": 0, "failed": 0}
    for user_id in await DB.list_character_user_ids():
        counts["characters"] += 1
        member = guild.get_member(int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except discord.NotFound:
                counts["not_in_guild"] += 1
                continue
            except (discord.Forbidden, discord.HTTPException):
                counts["failed"] += 1
                continue
        counts["members"] += 1
        character = await DB.get_character(int(user_id))
        if character is None:
            continue
        try:
            await _sync_realm_access_roles(guild, member, character)
            counts["synced"] += 1
        except Exception:
            counts["failed"] += 1
            log.exception("Bulk realm-role sync failed for user %s", user_id)
    return counts


async def _run_complete_server_setup(
    guild: discord.Guild, *, create_missing: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    base_result = await ensure_base_xianxia_channels(guild, category_name=SERVER_BASE_CATEGORY, create_missing=create_missing)
    realm_rows = await ensure_realm_hub_channels(guild, category_name=SERVER_REALM_CATEGORY, create_missing=create_missing)
    _bugs_channel, bugs_warning = await ensure_bugs_forum_channel(guild, category_name=SERVER_BASE_CATEGORY, create_missing=create_missing)
    return base_result, realm_rows, bugs_warning


async def _dashboard_discord_snapshot(client: commands.Bot, guild: discord.Guild) -> dict[str, Any]:
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    base_channels: list[dict[str, Any]] = []
    for name in BASE_CHANNEL_SPECS:
        channel_id = bindings.get(name)
        channel = await _resolve_text_channel(guild, channel_id)
        base_channels.append({
            "key": name,
            "configured_id": int(channel_id) if channel_id else None,
            "channel_id": channel.id if channel else None,
            "name": channel.name if channel else None,
            "status": "ready" if channel else ("stale" if channel_id else "missing"),
        })

    realm_rows = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    realm_hubs: list[dict[str, Any]] = []
    for world, hub in REALM_HUBS.items():
        row = realm_rows.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        role = discord.utils.get(guild.roles, name=_realm_access_role_name(world))
        realm_hubs.append({
            "world": world,
            "display_name": str(hub.get("display_name") or world),
            "channel_id": channel.id if isinstance(channel, discord.TextChannel) else None,
            "channel_name": channel.name if isinstance(channel, discord.TextChannel) else None,
            "role_id": role.id if role else None,
            "role_name": role.name if role else None,
            "ready": isinstance(channel, discord.TextChannel) and role is not None,
        })

    stored_messages = await DB.get_channel_messages(guild.id)
    channel_messages: list[dict[str, Any]] = []
    for key in CHANNEL_MESSAGE_KEYS:
        target = await _resolve_channel_message_target(guild, key)
        row = stored_messages.get(key) or {}
        state = channel_message_state(stored_messages, key)
        channel_messages.append({
            "key": key,
            "label": CHANNEL_MESSAGE_LABELS.get(key, key),
            # The effective text, which is "" for a slot the GM cleared. The
            # dashboard textarea shows exactly this, so a cleared slot must come
            # back empty rather than redisplaying the default it no longer uses.
            "content": resolve_channel_message_content(stored_messages, key),
            "state": state,
            "is_default": state == CHANNEL_MESSAGE_DEFAULT,
            "is_disabled": state == CHANNEL_MESSAGE_DISABLED,
            # What Save would restore if this slot were reset, so the dashboard
            # can offer the built-in text without pretending it is in use.
            "default_content": DEFAULT_CHANNEL_MESSAGES.get(key, ""),
            "message_id": row.get("message_id"),
            "channel_id": target.id if target else None,
            "channel_ready": target is not None,
        })

    bugs_channel_id = cfg.get("bugs_channel_id")
    bugs_channel = guild.get_channel(int(bugs_channel_id)) if bugs_channel_id else None
    if not isinstance(bugs_channel, discord.ForumChannel):
        bugs_channel = None
    bugs_guidelines_row = stored_messages.get(BUGS_GUIDELINES_KEY) or {}
    bugs_reports = await bugs_forum_reports(guild, bugs_channel) if bugs_channel is not None else []
    bugs = {
        "channel_id": bugs_channel.id if bugs_channel else None,
        "channel_name": bugs_channel.name if bugs_channel else None,
        "ready": bugs_channel is not None,
        "guidelines": bugs_guidelines_row.get("content") or DEFAULT_BUGS_GUIDELINES,
        "is_default_guidelines": not bugs_guidelines_row.get("content"),
        # Report what Discord actually offers, not what we would like it to
        # offer. This used to echo BUGS_FORUM_TAGS unconditionally, so a forum
        # adopted from an existing #bugs channel (which never had the tags
        # applied) still told the GM all five were available.
        "available_tags": [
            str(tag.name) for tag in (getattr(bugs_channel, "available_tags", None) or ())
        ] if bugs_channel is not None else [],
        "required_tags": [name for name, _emoji in BUGS_FORUM_TAGS],
        "missing_tags": missing_bugs_forum_tags(bugs_channel) if bugs_channel is not None else [],
        "open_reports": sum(1 for r in bugs_reports if not r["archived"]),
        "reports": bugs_reports,
    }

    me = guild.me
    permission_specs = (
        ("manage_channels", "Manage Channels", "Lets the dashboard's Full Setup/Repair create missing channels and categories automatically; without it, create them manually in Discord and Full Setup/Repair will still bind them by name.", False),
        ("manage_threads", "Manage Threads", "Recover/archive private expedition and property threads.", True),
        ("create_private_threads", "Create Private Threads", "Create private expedition and player-location threads.", True),
        ("send_messages_in_threads", "Send Messages in Threads", "Narrate and update private scenes.", True),
        ("manage_roles", "Manage Roles", "Create/sync realm visibility roles.", True),
        ("view_channel", "View Channels", "Resolve configured game channels.", True),
        ("send_messages", "Send Messages", "Post guides, events and game responses.", True),
        ("read_message_history", "Read Message History", "Recover persistent scene and guide messages.", True),
        ("embed_links", "Embed Links", "Render rich game/status panels.", True),
    )
    permissions: list[dict[str, Any]] = []
    for attr, label, purpose, required in permission_specs:
        ok = bool(me and getattr(me.guild_permissions, attr, False))
        permissions.append({"key": attr, "label": label, "ok": ok, "purpose": purpose, "required": required})

    blocked_roles: list[str] = []
    if me and me.guild_permissions.manage_roles:
        for row in realm_hubs:
            role = guild.get_role(int(row["role_id"])) if row.get("role_id") else None
            if role is not None and me.top_role.position <= role.position:
                blocked_roles.append(role.name)

    all_channels = [
        {
            "id": channel.id,
            "name": channel.name,
            "category": channel.category.name if channel.category else "Uncategorized",
        }
        for channel in sorted(guild.text_channels, key=lambda c: ((c.category.position if c.category else -1), c.position, c.name))
    ]
    ready_base = sum(1 for row in base_channels if row["status"] == "ready")
    ready_realms = sum(1 for row in realm_hubs if row["ready"])
    warnings = [row["label"] for row in permissions if row["required"] and not row["ok"]]
    if blocked_roles:
        warnings.append("Bot role must be moved above: " + ", ".join(blocked_roles))

    return {
        "connected": True,
        "guild": {"id": guild.id, "name": guild.name, "member_count": guild.member_count},
        "bot": {
            "id": client.user.id if client.user else None,
            "name": str(client.user) if client.user else "Xianxia RP",
            "administrator": bool(me and me.guild_permissions.administrator),
            "top_role": me.top_role.name if me else None,
        },
        "permissions": permissions,
        "permission_warnings": warnings,
        "base_channels": base_channels,
        "base_ready": ready_base,
        "base_total": len(base_channels),
        "realm_hubs": realm_hubs,
        "realm_ready": ready_realms,
        "realm_total": len(realm_hubs),
        "text_channels": all_channels,
        "info_message_id": cfg.get("info_message_id"),
        "channel_messages": channel_messages,
        "bugs": bugs,
        "registered_commands": len(client.tree.get_commands(guild=GUILD)),
        "setup_ready": ready_base == len(base_channels) and ready_realms == len(realm_hubs) and not warnings,
    }


async def _audit_dashboard_discord(action: str, guild: discord.Guild, *, after: dict[str, Any] | None = None, reason: str = "GM dashboard") -> None:
    await DB.log_admin_action(
        admin_user_id=0,
        action=f"dashboard.discord.{action}",
        target=f"guild:{guild.id}",
        after=after or {},
        reason=reason[:500],
    )


async def dashboard_discord_control(client: commands.Bot, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Private dashboard -> discord.py control surface.

    This endpoint never performs game mechanics. It validates dashboard-owned
    Discord layout, binds existing channels, and stores Discord IDs through the
    normal Go-owned database boundary.
    """
    action = str(action or "status").strip().lower()
    guild = client.get_guild(SETTINGS.guild_id)
    if guild is None:
        raise RuntimeError(f"Configured Discord guild {SETTINGS.guild_id} is not connected")
    reason = str(payload.get("reason") or "GM dashboard")[:500]

    if action in {"status", "permissions", "configuration"}:
        return {"ok": True, "action": action, "result": await _dashboard_discord_snapshot(client, guild)}

    if action in {"setup", "repair"}:
        # This is the one caller that actually creates missing channels/categories -
        # everything else (including the /admin slash command's own "setup"/"repair"
        # choices) stays validate-and-bind-only, keeping Discord layout dashboard-owned.
        base_result, realm_rows, bugs_warning = await _run_complete_server_setup(guild, create_missing=True)
        info_channel = base_result["channels"].get("xianxia-info")
        info_message = await ensure_xianxia_info_guide(guild, info_channel) if info_channel else None
        channel_messages = await ensure_all_channel_messages(guild)
        role_sync: dict[str, int] | None = None
        if guild.me.guild_permissions.manage_roles:
            role_sync = await _sync_all_realm_access_roles(guild)
        synced = await client.tree.sync(guild=GUILD)
        warnings = list(base_result["warnings"])
        if bugs_warning:
            warnings.append(bugs_warning)
        result = {
            "mode": action,
            "created": list(base_result["created"]),
            "repaired": list(base_result["repaired"]),
            "warnings": warnings,
            "realm_hubs": len(realm_rows),
            "info_message_id": info_message.id if info_message else None,
            "channel_messages_posted": sum(1 for v in channel_messages.values() if v),
            "realm_role_sync": role_sync,
            "synced_commands": len(synced),
        }
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "sync_roles":
        counts = await _sync_all_realm_access_roles(guild)
        await _audit_dashboard_discord(action, guild, after=counts, reason=reason)
        return {"ok": True, "action": action, "result": counts, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "rebuild_info":
        channel = await configured_info_channel(guild)
        if channel is None:
            raise ValueError("#xianxia-info is not configured. Run Full Setup or Repair first.")
        message = await ensure_xianxia_info_guide(guild, channel)
        if message is None:
            raise RuntimeError("Discord rejected the info-guide update")
        result = {"channel_id": channel.id, "message_id": message.id}
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result}

    if action == "set_channel_messages":
        raw = payload.get("messages")
        if not isinstance(raw, dict) or not raw:
            raise ValueError('payload "messages" must be a non-empty object of {channel_key: content}')
        unknown = sorted(set(raw) - set(CHANNEL_MESSAGE_KEYS))
        if unknown:
            raise ValueError(f"Unknown channel message key(s): {', '.join(unknown)}")
        applied: dict[str, int | None] = {}
        skipped: list[str] = []
        for key, content in raw.items():
            message = await ensure_channel_message(guild, key, str(content or ""))
            if message is not None or not str(content or "").strip():
                applied[key] = message.id if message else None
            else:
                skipped.append(key)
        result = {"applied": applied, "skipped": skipped}
        await _audit_dashboard_discord(action, guild, after={"keys": sorted(applied)}, reason=reason)
        return {"ok": True, "action": action, "result": result, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "set_bugs_guidelines":
        text = str(payload.get("guidelines") or "").strip() or DEFAULT_BUGS_GUIDELINES
        await DB.set_channel_message(guild.id, BUGS_GUIDELINES_KEY, content=text, message_id=None)
        channel, warning = await ensure_bugs_forum_channel(guild, category_name=SERVER_BASE_CATEGORY, create_missing=False)
        result = {"channel_id": channel.id if channel else None, "warning": warning}
        await _audit_dashboard_discord(action, guild, after={"channel_id": result["channel_id"]}, reason=reason)
        return {"ok": True, "action": action, "result": result, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "bugs_reports":
        bugs_channel_id = (await DB.get_server_config(guild.id)).get("bugs_channel_id")
        channel = guild.get_channel(int(bugs_channel_id)) if bugs_channel_id else None
        if not isinstance(channel, discord.ForumChannel):
            raise ValueError("#bugs is not configured as a forum channel yet. Run Full Setup or Repair first.")
        reports = await bugs_forum_reports(guild, channel, limit=int(payload.get("limit") or 20))
        return {"ok": True, "action": action, "result": {"channel_id": channel.id, "reports": reports}}

    if action == "sync_commands":
        synced = await client.tree.sync(guild=GUILD)
        result = {"synced_commands": len(synced), "guild_id": guild.id}
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result}

    if action == "bind_channels":
        cfg = await DB.get_server_config(guild.id)
        mapping = {
            "announcement_channel_id": "announcements",
            "event_scene_channel_id": "scenes",
            "home_scene_channel_id": "homes",
            "log_channel_id": "logs",
            "begin_channel_id": "begin",
            "info_channel_id": "info",
            "exploration_channel_id": "exploration",
        }
        values: dict[str, int | None] = {}
        for db_key, payload_key in mapping.items():
            raw = payload.get(payload_key)
            if raw in {None, ""}:
                values[db_key] = cfg.get(db_key)
                continue
            try:
                channel_id = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid channel id for {payload_key}") from exc
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError(f"Selected {payload_key} channel is not a text channel in {guild.name}")
            values[db_key] = channel_id
        if not values.get("announcement_channel_id") or not values.get("event_scene_channel_id"):
            raise ValueError("World-events and event-scenes channels must be selected before saving bindings")
        await DB.set_server_channels(guild.id, **values)
        await _audit_dashboard_discord(action, guild, after=values, reason=reason)
        return {"ok": True, "action": action, "result": values, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "test_announcement":
        cfg = await DB.get_server_config(guild.id)
        channel = await _resolve_text_channel(guild, cfg.get("announcement_channel_id"))
        if channel is None:
            raise ValueError("No announcement channel is configured")
        message = await channel.send("🧪 **Xianxia RP GM Dashboard test** — Discord server integration is working.")
        result = {"channel_id": channel.id, "message_id": message.id}
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result}

    if action == "fresh_start":
        # Cosmetic message wipe, NOT a world/database reset: deletes and recreates
        # every Xianxia-managed channel safe to fully clear (CHANNEL_WIPE_KEYS, the
        # realm-capital hubs, #bugs), leaving player-homes/expeditions/event-scenes
        # untouched so live private/property/event threads survive. See
        # clear_managed_channel_messages for why deletion (not purge) is used and
        # exactly what is and isn't cleared.
        if str(payload.get("confirm") or "").strip().upper() != "CLEAR":
            raise ValueError('confirmation required: payload "confirm" must be exactly "CLEAR"')
        result = await clear_managed_channel_messages(guild)
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "reset_world":
        # Discord-side half of a world reset: delete every thread the bot still
        # has a database row for (they're about to become orphaned pointers to
        # characters/events that no longer exist) and announce the reset. This
        # never touches the SQLite file itself - that part is reset_database.sh's
        # job, since only it can safely stop/restart the whole stack. Called by
        # both reset_database.sh (before it wipes the database) and the
        # dashboard's "Reset World" button.
        if str(payload.get("confirm") or "").strip().upper() != "RESET":
            raise ValueError('confirmation required: payload "confirm" must be exactly "RESET"')
        records = await DB.all_managed_thread_ids()
        deleted = 0
        already_gone = 0
        failed = 0
        by_kind: dict[str, int] = {}
        for record in records:
            thread_id = int(record["thread_id"])
            thread: discord.Thread | None = guild.get_thread(thread_id)
            if thread is None:
                try:
                    fetched = await guild.fetch_channel(thread_id)
                    thread = fetched if isinstance(fetched, discord.Thread) else None
                except discord.NotFound:
                    already_gone += 1
                    continue
                except (discord.Forbidden, discord.HTTPException):
                    failed += 1
                    continue
            if thread is None:
                already_gone += 1
                continue
            try:
                await thread.delete()
            except discord.NotFound:
                already_gone += 1
                continue
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
                continue
            deleted += 1
            by_kind[record["kind"]] = by_kind.get(record["kind"], 0) + 1

        cfg = await DB.get_server_config(guild.id)
        channel = await _resolve_text_channel(guild, cfg.get("announcement_channel_id"))
        message_text = str(payload.get("message") or "").strip() or (
            "🌌 **The world has ended and a new age begins.** Every cultivator, sect, "
            "family and chapter of history has returned to dust — the slate is wiped "
            "clean. Use `/begin` to forge a new legend."
        )
        announcement: dict[str, Any] | None = None
        if channel is not None:
            message = await channel.send(message_text)
            announcement = {"channel_id": channel.id, "message_id": message.id}

        result = {
            "threads_found": len(records),
            "threads_deleted": deleted,
            "threads_already_gone": already_gone,
            "threads_failed": failed,
            "threads_deleted_by_kind": by_kind,
            "announcement": announcement,
        }
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result}

    raise ValueError(f"Unsupported Discord dashboard action: {action}")


@registered_group_command(admin_server_group, name="setup", description="Install, repair and diagnose the complete Xianxia Discord server")
@app_commands.choices(action=SERVER_SETUP_CHOICES)
async def admin_setup_server(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return

    if action.value == "permissions":
        lines, warnings = _server_permission_report(guild)
        summary = "✅ **Ready for automatic setup.**" if not warnings else f"⚠️ **{len(warnings)} setup issue(s) found.**"
        await interaction.response.send_message(
            "🧰 **Xianxia Server Permission Check**\n" + summary + "\n\n" + "\n".join(lines),
            ephemeral=False,
        )
        await audit_admin(interaction, "server.setup.permissions", target=f"guild:{guild.id}", after={"issues": len(warnings)})
        return

    if action.value == "configuration":
        await interaction.response.send_message(await _server_configuration_report(guild), ephemeral=False)
        await audit_admin(interaction, "server.setup.configuration", target=f"guild:{guild.id}")
        return

    if action.value == "sync_roles":
        if not guild.me or not guild.me.guild_permissions.manage_roles:
            lines, _ = _server_permission_report(guild)
            await interaction.response.send_message(
                "❌ **Manage Roles** is required to synchronize cultivation-world visibility roles.\n\n" + "\n".join(lines),
                ephemeral=False,
            )
            return
        await interaction.response.defer(ephemeral=False)
        try:
            counts = await _sync_all_realm_access_roles(guild)
        except PermissionError as exc:
            await interaction.followup.send(f"❌ Realm-role sync could not start: {exc}", ephemeral=False)
            return
        await audit_admin(interaction, "server.setup.sync_roles", target=f"guild:{guild.id}", after=counts)
        await interaction.followup.send(
            "✅ **Realm access roles synchronized.**\n"
            f"Characters checked: **{counts['characters']}**\n"
            f"Guild members found: **{counts['members']}**\n"
            f"Members reconciled: **{counts['synced']}**\n"
            f"Characters no longer in this Discord: **{counts['not_in_guild']}**\n"
            f"Fetch/sync failures: **{counts['failed']}**",
            ephemeral=False,
        )
        return

    if action.value == "rebuild_info":
        info_channel = await configured_info_channel(guild)
        if info_channel is None:
            await interaction.response.send_message(
                "❌ `#xianxia-info` is not configured or was deleted. Run **Server → Setup → Repair Server** first.",
                ephemeral=False,
            )
            return
        await interaction.response.defer(ephemeral=False)
        message = await ensure_xianxia_info_guide(guild, info_channel)
        if message is None:
            await interaction.followup.send(
                "❌ Could not rebuild the information guide. Run **Check Permissions** and verify the bot can send messages in `#xianxia-info`.",
                ephemeral=False,
            )
            return
        await audit_admin(
            interaction, "server.setup.rebuild_info", target=f"guild:{guild.id}",
            after={"channel_id": info_channel.id, "message_id": message.id},
        )
        await interaction.followup.send(
            f"✅ **Xianxia information guide rebuilt.**\nChannel: {info_channel.mention}\nGuide message: `{message.id}`",
            ephemeral=False,
        )
        return

    # Discord channel creation is dashboard-owned. Bot setup only validates bindings
    # and refreshes game-side metadata for channels that already exist.
    await interaction.response.defer(ephemeral=False)
    try:
        base_result, realm_rows, bugs_warning = await _run_complete_server_setup(guild)
    except (discord.Forbidden, discord.HTTPException, PermissionError) as exc:
        await interaction.followup.send(
            f"❌ **Server {'setup' if action.value == 'setup' else 'repair'} stopped.** Discord rejected a required operation: {exc}\n"
            "Run **Server → Setup → Check Permissions** for the exact missing capability.",
            ephemeral=False,
        )
        return

    # Setup/Repair refreshes the persistent guide and, when possible, reconciles
    # existing players so a fresh Discord install does not wait for each player
    # to trigger an interaction before receiving the correct realm visibility roles.
    info_channel = base_result["channels"].get("xianxia-info")
    info_message = await ensure_xianxia_info_guide(guild, info_channel) if info_channel else None
    _, permission_warnings = _server_permission_report(guild)
    warnings = list(base_result["warnings"]) + permission_warnings
    if bugs_warning:
        warnings.append(bugs_warning)
    role_sync_counts: dict[str, int] | None = None
    if guild.me and guild.me.guild_permissions.manage_roles:
        try:
            role_sync_counts = await _sync_all_realm_access_roles(guild)
        except PermissionError as exc:
            warnings.append(str(exc))
    mode = "Setup" if action.value == "setup" else "Repair"
    audit_action = "server.setup.install" if action.value == "setup" else "server.setup.repair"
    await audit_admin(
        interaction, audit_action, target=f"guild:{guild.id}",
        after={
            "base_category_id": base_result["category"].id if base_result["category"] else None,
            "base_created": base_result["created"],
            "base_repaired": base_result["repaired"],
            "realm_hubs": {str(row["world_name"]): int(row["channel_id"]) for row in realm_rows},
            "info_message_id": info_message.id if info_message else None,
            "permission_issues": len(permission_warnings),
            "realm_role_sync": role_sync_counts,
        },
    )
    warning_text = "" if not warnings else "\n\n⚠️ **Follow-up diagnostics**\n" + "\n".join(f"• {warning}" for warning in dict.fromkeys(warnings))
    await interaction.followup.send(
        f"✅ **Xianxia Server {mode} complete.**\n"
        f"Base channels connected: **{len(base_result['channels'])}**\n"
        f"Bot-created base channels: **0 (disabled)**\n"
        f"Dashboard-owned bindings validated: **{len(base_result['channels'])}**\n"
        f"Realm-capital channels connected: **{len(realm_rows)}**\n"
        f"Info guide: **{'ready' if info_message else 'needs attention'}**\n"
        + (f"Realm-role reconciliation: **{role_sync_counts['synced']} member(s)**\n" if role_sync_counts is not None else "Realm-role reconciliation: **skipped**\n")
        + "No database/world reset was performed and unrelated Discord channels were not deleted."
        + warning_text,
        ephemeral=False,
    )


REALM_HUB_SETUP_CHOICES = [
    app_commands.Choice(name="Refresh dashboard bindings", value="refresh"),
    app_commands.Choice(name="Show realm-capital channel status", value="status"),
]


@registered_group_command(admin_server_group, name="realmhubs", description="Inspect dashboard-managed realm-capital channel bindings")
@app_commands.choices(action=REALM_HUB_SETUP_CHOICES)
async def admin_realm_hubs(interaction: discord.Interaction, action: app_commands.Choice[str], category_name: str = "🌌 Realm Capitals") -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    if action.value == "refresh":
        await ensure_realm_hub_channels(guild, category_name=category_name)
    existing = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    lines = [
        "🏙️ **Realm-Capital Meeting Channels**",
        "Discord channel creation is disabled in the bot; configure channel IDs in the admin dashboard.",
    ]
    for world, hub in REALM_HUBS.items():
        row = existing.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        lines.append(
            f"• **{world} — {hub['display_name']}**: "
            f"{channel.mention if isinstance(channel, discord.TextChannel) else '*not bound*'}"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(admin_server_group, name="observability", description="Inspect slow-query telemetry, runtime metrics and external-alert history")
async def admin_observability(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    flushed=await DB.flush_slow_query_log(); obs=await DB.observability_snapshot(); alerts=await DB.get_operational_alerts(limit=8)
    try:
        engine_status = await ENGINE.database_status()
        engine_line = f"Go engine **online** • requests **{int(engine_status.get('requests', 0))}** • SQLite journal **{engine_status.get('pragmas', {}).get('journal_mode', 'unknown')}**"
    except Exception as exc:
        engine_line = f"Go engine **offline** — {type(exc).__name__}: {exc}"
    lines=["📈 **Operational Observability**",f"Schema **v{SCHEMA_VERSION}** • query count **{obs['query_count']}** • slow queries **{obs['slow_query_count']}** • recent 1h **{obs['recent_slow_queries_1h']}**",f"Max observed latency **{obs['max_query_latency_ms']:.3f} ms** • slow threshold **{obs['slow_query_threshold_ms']:.1f} ms** • flushed now **{flushed}**",f"Connections opened **{obs['connections_opened']}** • reused **{obs['connections_reused']}** • writer waits **{obs['writer_wait_count']}** ({obs['writer_wait_ms']:.3f} ms)",engine_line,f"External webhook alerts: **{'enabled' if ALERTS.enabled else 'disabled'}**"]
    if alerts:
        lines.append("\n**Recent Alerts**")
        for a in alerts: lines.append(f"• `{a['alert_key']}` • {a['severity']} • delivered={bool(a['delivered'])} — {a['message']}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


# ---------------------------------------------------------------------------
# Chat monitor - administrator-only channel analysis
# ---------------------------------------------------------------------------
# scene_history cannot answer "what has been happening in this channel":
# Database.add_history hard-deletes everything past the newest 60 rows per channel
# on every insert, because its job is to feed the narrator a rolling context
# window rather than to archive play.  These actions therefore read Discord's own
# message history, and run the transcript through the same free OpenRouter chain
# the narrator uses - never a paid route, so OPENROUTER_REQUIRE_FREE keeps holding.
MONITOR_INTENT_HINT = (
    "⚠️ **Message Content intent is off**, so Discord returns empty text for every "
    "message the bot was not mentioned in. Set `MESSAGE_CONTENT_INTENT=true` in "
    "`.env` **and** enable *Message Content Intent* under Bot → Privileged Gateway "
    "Intents in the Discord Developer Portal, then restart the bot."
)


async def _monitor_collect(
    channels: list[Any],
    *,
    limit: int,
    after: Any,
) -> tuple[list[chat_monitor.TranscriptMessage], list[str]]:
    """Pull recent messages from each channel, newest first, degrading per channel.

    One unreadable channel must not fail the whole report, so a permission error is
    recorded and the sweep continues.
    """
    collected: list[chat_monitor.TranscriptMessage] = []
    skipped: list[str] = []
    per_channel = max(1, int(limit) // max(1, len(channels)))
    for channel in channels:
        name = getattr(channel, "name", str(getattr(channel, "id", "?")))
        try:
            async for message in channel.history(limit=per_channel, after=after, oldest_first=False):
                author = message.author
                collected.append(
                    chat_monitor.TranscriptMessage(
                        channel_id=int(channel.id),
                        channel_name=str(name),
                        author_id=int(author.id),
                        author_name=str(getattr(author, "display_name", None) or author),
                        is_bot=bool(getattr(author, "bot", False)),
                        created_at=message.created_at.timestamp(),
                        content=str(message.content or ""),
                    )
                )
        except discord.Forbidden:
            skipped.append(f"#{name} (missing Read Message History)")
        except Exception as exc:
            log.warning("Monitor could not read channel %s: %s", name, exc)
            skipped.append(f"#{name} ({type(exc).__name__})")
        if len(collected) >= int(limit):
            break
    return collected[: int(limit)], skipped


@registered_group_command(admin_server_group, name="ai_status", description="Show narrator health and OpenRouter free-route counters")
async def admin_ai_status(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    await reply_long(interaction, chat_monitor.render_health(NARRATOR.health_snapshot()), ephemeral=False)


@registered_group_command(admin_server_group, name="chat_digest", description="Read a channel's recent history and report what players are doing")
@app_commands.describe(
    channel="Channel to analyse (defaults to the channel you run this in)",
    hours="How far back to read, in hours",
    include_threads="Also sweep the channel's threads",
)
async def admin_chat_digest(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    hours: int | None = None,
    include_threads: bool = False,
) -> None:
    if not await require_admin(interaction):
        return
    target = channel or interaction.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            "Choose a text channel or thread to analyse.", ephemeral=False
        )
        return

    window = SETTINGS.monitor_lookback_hours if hours is None else max(1, min(720, int(hours)))
    after = discord.utils.utcnow() - timedelta(hours=window)

    targets: list[Any] = [target]
    if include_threads and isinstance(target, discord.TextChannel):
        targets.extend(list(target.threads))
        try:
            async for archived in target.archived_threads(limit=20):
                targets.append(archived)
        except Exception:
            log.warning("Monitor could not enumerate archived threads", exc_info=True)

    await interaction.response.defer(ephemeral=False)
    messages, skipped = await _monitor_collect(
        targets, limit=SETTINGS.monitor_max_messages, after=after
    )

    scope = f"<#{target.id}>"
    if len(targets) > 1:
        scope += f" + {len(targets) - 1} thread(s)"
    scope += f" • last {window}h"

    suffix = ""
    if skipped:
        suffix += "\n⚠️ Skipped: " + ", ".join(skipped)
    if not SETTINGS.message_content_intent:
        suffix += "\n\n" + MONITOR_INTENT_HINT

    if not messages:
        await reply_long(
            interaction,
            f"🔎 **Channel Monitor**\nScope: {scope}\nNo messages found in that window." + suffix,
            ephemeral=False,
        )
        return

    report = await chat_monitor.analyse_transcript(
        AI_ROUTER if NARRATOR.provider == "openrouter" else None,
        messages,
        chunk_chars=SETTINGS.monitor_chunk_chars,
        max_chunks=SETTINGS.monitor_max_chunks,
    )
    await audit_admin(
        interaction,
        "server.chat_digest",
        target=str(target.id),
        after={"hours": window, "messages": len(messages), "channels": len(targets)},
    )
    await reply_long(
        interaction, chat_monitor.render_report(report, scope=scope) + suffix, ephemeral=False
    )


