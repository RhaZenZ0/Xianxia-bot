"""The admin core: the permission gate, the two audit trails, and the
/admin command groups every admin module registers into.

Phase 5 of the main.py split (v0.19.40, docs/MAIN_SPLIT_PLAN.md).
require_admin has 44 call sites and audit_admin 27, all in the /admin
handlers that phases 6-7 move into this package - 85 of the edges between
those handlers and the rest of main.py were just these names. Reads
runtime and channels; never main.py. Definition order is the order these
had in main.py.

The eight Group objects are defined here and registered with Discord by
main.py's register_command_surface (only the single /admin root is ever
added to the tree; the sub-groups are hub metadata).
"""
from __future__ import annotations

import discord
from discord import app_commands

from ..channels import post_server_log
from ..runtime import DB, log

def _admin_command_option_summary(data: object) -> str:
    """Return a compact, non-secret summary of slash-command options for Discord audit logs."""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []

    def walk(options: object) -> None:
        if not isinstance(options, list):
            return
        for option in options:
            if not isinstance(option, dict):
                continue
            nested = option.get("options")
            if isinstance(nested, list):
                walk(nested)
                continue
            name = str(option.get("name") or "option")
            if "value" not in option:
                continue
            value = str(option.get("value"))
            if len(value) > 120:
                value = value[:117] + "..."
            parts.append(f"`{name}`=`{value}`")

    walk(data.get("options"))
    return ", ".join(parts[:12])


async def log_admin_command_invocation(interaction: discord.Interaction) -> None:
    """Mirror every accepted /admin invocation to the configured private log channel."""
    try:
        command_name = interaction.command.qualified_name if interaction.command else "admin"
        options = _admin_command_option_summary(interaction.data)
        supplied = getattr(interaction, "hub_supplied_options", None)
        if isinstance(supplied, dict) and supplied:
            rendered: list[str] = []
            for key, value in list(supplied.items())[:12]:
                if isinstance(value, discord.Member):
                    display = f"{value} ({value.id})"
                elif isinstance(value, discord.abc.GuildChannel):
                    display = f"#{value.name} ({value.id})"
                elif isinstance(value, app_commands.Choice):
                    display = str(value.value)
                else:
                    display = str(value)
                if len(display) > 120:
                    display = display[:117] + "..."
                rendered.append(f"`{key}`=`{display}`")
            options = ", ".join(rendered)
        detail = (
            f"**{interaction.user}** (`{interaction.user.id}`) ran `/{command_name}`\n"
            f"Channel: <#{interaction.channel_id}>"
        )
        if options:
            detail += f"\nOptions: {options}"
        await post_server_log(interaction.guild, "Admin command", detail)
    except Exception:
        # Logging must never block an otherwise valid administrator command.
        log.exception("Could not mirror /admin invocation to Discord")


async def require_admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
        await interaction.response.send_message(
            "This command requires the **Administrator** permission.", ephemeral=False
        )
        return False
    await log_admin_command_invocation(interaction)
    return True


async def audit_admin(
    interaction: discord.Interaction,
    action: str,
    *,
    target: str = "",
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    database_log: bool = True,
) -> None:
    if database_log:
        try:
            await DB.log_admin_action(
                admin_user_id=interaction.user.id, action=action, target=target,
                before=before, after=after, reason=reason,
            )
        except Exception:
            log.exception("Could not write admin audit entry for %s", action)
    try:
        await post_server_log(
            interaction.guild,
            "Admin action",
            f"**{interaction.user}** (`{interaction.user.id}`) ran `{action}`"
            + (f" on `{target}`" if target else "")
            + (f" — {reason}" if reason else ""),
        )
    except Exception:
        log.exception("Could not mirror admin audit entry to Discord for %s", action)


admin_group = app_commands.Group(
    name="admin",
    description="Configure and control the cultivation world",
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),
)


admin_server_group = app_commands.Group(
    name="server",
    description="Server setup, health, maintenance, backups and audit",
    parent=admin_group,
)


admin_world_group = app_commands.Group(
    name="world",
    description="World events, time and GM event controls",
    parent=admin_group,
)


admin_player_group = app_commands.Group(
    name="player",
    description="Inspect, restore and modify player state",
    parent=admin_group,
)


admin_sect_group = app_commands.Group(
    name="sect",
    description="Sect membership, ranks and master relationships",
    parent=admin_group,
)


admin_family_group = app_commands.Group(
    name="family",
    description="Inspect hidden birth-family state",
    parent=admin_group,
)


admin_npc_group = app_commands.Group(
    name="npc",
    description="Inspect hidden NPC state",
    parent=admin_group,
)


admin_sim_group = app_commands.Group(
    name="simulation",
    description="World simulation, economy, sect politics and clan automation",
    parent=admin_group,
)


