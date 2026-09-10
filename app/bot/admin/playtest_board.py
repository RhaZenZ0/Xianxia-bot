"""The playtest board (v0.34.1): one post per hub page in #playtest, which
testers answer with a reaction - ✅ it works, ❌ it fails, 💡 change it -
and a reply saying what. The GM reads the board with `report`, which tallies
the reactions and names who left them, and follows the ❌ and 💡 rows to the
replies underneath. Nothing here decides anything: the board is a message
per page and a table of message ids, and the verdicts are the testers'.
"""
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ..channels import _resolve_text_channel
from ..hubs import REGISTERED_HUBS, _leaf_actions
from ..registry import registered_group_command
from ..runtime import DB, log, reply_long
from .core import admin_server_group, audit_admin, require_admin

BOARD_REACTIONS = ("✅", "❌", "💡")
BOARD_ACTIONS = [
    app_commands.Choice(name="Post the board (one message per hub page)", value="post"),
    app_commands.Choice(name="Report the reactions", value="report"),
    app_commands.Choice(name="Clear the board", value="clear"),
]


def board_items() -> list[tuple[str, str, str]]:
    """(item_key, title, body) for every hub page, in hub order."""
    items: list[tuple[str, str, str]] = []
    for definition in REGISTERED_HUBS:
        for page in definition.pages:
            actions = _leaf_actions(page)
            lines = [f"**/{definition.name} → {page.label}** — {page.description}"]
            for action in actions:
                lines.append(f"• `{action.path}` — {action.description}")
            lines.append("\nReact ✅ works · ❌ fails · 💡 change wanted — and reply with what you saw.")
            items.append((f"{definition.name}/{page.key}", f"/{definition.name} → {page.label}", "\n".join(lines)[:1900]))
    return items


async def _board_channel(guild: discord.Guild) -> discord.TextChannel | None:
    cfg = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, cfg.get("playtest_channel_id"))


async def post_board(guild: discord.Guild) -> tuple[int, int]:
    """Post every page that has no message yet; returns (posted, already there)."""
    channel = await _board_channel(guild)
    if channel is None:
        raise RuntimeError("no #playtest channel is bound; run Setup or Repair from the dashboard first")
    existing = {str(row["item_key"]): row for row in await DB.get_playtest_items(guild.id)}
    posted = kept = 0
    for key, _title, body in board_items():
        row = existing.get(key)
        if row:
            try:
                await channel.fetch_message(int(row["message_id"]))
                kept += 1
                continue
            except discord.HTTPException:
                pass  # the message is gone; post it again
        try:
            message = await channel.send(body)
            for emoji in BOARD_REACTIONS:
                await message.add_reaction(emoji)
        except discord.HTTPException:
            log.exception("Could not post playtest item %s", key)
            continue
        await DB.set_playtest_item(guild_id=guild.id, item_key=key, channel_id=channel.id, message_id=message.id)
        posted += 1
    return posted, kept


async def board_report(guild: discord.Guild) -> list[dict[str, Any]]:
    """Per page: counts per reaction and who reacted, the bot's own reactions left out."""
    channel = await _board_channel(guild)
    rows: list[dict[str, Any]] = []
    titles = {key: title for key, title, _ in board_items()}
    me = guild.me.id if guild.me else 0
    for row in await DB.get_playtest_items(guild.id):
        entry: dict[str, Any] = {"key": row["item_key"], "title": titles.get(str(row["item_key"]), str(row["item_key"])),
                                 "message_id": int(row["message_id"]), "channel_id": int(row["channel_id"]),
                                 "counts": {e: 0 for e in BOARD_REACTIONS}, "who": {e: [] for e in BOARD_REACTIONS}, "missing": False}
        target = channel if channel is not None and channel.id == int(row["channel_id"]) else await _resolve_text_channel(guild, row["channel_id"])
        if target is None:
            entry["missing"] = True
            rows.append(entry)
            continue
        try:
            message = await target.fetch_message(int(row["message_id"]))
        except discord.HTTPException:
            entry["missing"] = True
            rows.append(entry)
            continue
        for reaction in message.reactions:
            emoji = str(reaction.emoji)
            if emoji not in BOARD_REACTIONS:
                continue
            try:
                users = [user async for user in reaction.users() if user.id != me]
            except discord.HTTPException:
                users = []
            entry["counts"][emoji] = len(users)
            entry["who"][emoji] = [getattr(user, "display_name", str(user)) for user in users]
        rows.append(entry)
    return rows


def format_report(rows: list[dict[str, Any]], guild_id: int) -> str:
    if not rows:
        return "🧪 The playtest board has not been posted yet. Post it with **/admin → Server → Playtest**."
    flagged = [r for r in rows if r["counts"]["❌"] or r["counts"]["💡"]]
    clean = [r for r in rows if r["counts"]["✅"] and not (r["counts"]["❌"] or r["counts"]["💡"])]
    silent = [r for r in rows if not any(r["counts"].values())]
    lines = [f"🧪 **Playtest board** — {len(rows)} pages · {len(flagged)} flagged · {len(clean)} confirmed · {len(silent)} untouched"]
    for r in flagged:
        link = f"https://discord.com/channels/{guild_id}/{r['channel_id']}/{r['message_id']}"
        parts = []
        for emoji in ("❌", "💡", "✅"):
            if r["counts"][emoji]:
                parts.append(f"{emoji} {r['counts'][emoji]} ({', '.join(r['who'][emoji][:6])})")
        lines.append(f"\n**{r['title']}** — {' · '.join(parts)} — [open]({link})")
    if clean:
        lines.append("\n\n✅ Confirmed: " + ", ".join(r["title"] for r in clean))
    if silent:
        lines.append(f"\n\n⬜ No reaction yet on {len(silent)} page{'s' if len(silent) != 1 else ''}.")
    return "".join(lines)


@registered_group_command(admin_server_group, name="playtest", description="Post, read or clear the playtest board in #playtest")
@app_commands.choices(action=BOARD_ACTIONS)
async def admin_playtest(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    await interaction.response.defer(ephemeral=False)
    if action.value == "post":
        try:
            posted, kept = await post_board(guild)
        except RuntimeError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=False)
            return
        await audit_admin(interaction, "server.playtest.post", target=f"guild:{guild.id}", after={"posted": posted, "kept": kept})
        await interaction.followup.send(f"🧪 Playtest board: **{posted}** page{'s' if posted != 1 else ''} posted, **{kept}** already there.", ephemeral=False)
        return
    if action.value == "clear":
        channel = await _board_channel(guild)
        removed = 0
        for row in await DB.get_playtest_items(guild.id):
            target = channel if channel is not None and channel.id == int(row["channel_id"]) else await _resolve_text_channel(guild, row["channel_id"])
            if target is None:
                continue
            try:
                message = await target.fetch_message(int(row["message_id"]))
                await message.delete()
                removed += 1
            except discord.HTTPException:
                continue
        forgotten = await DB.clear_playtest_items(guild.id)
        await audit_admin(interaction, "server.playtest.clear", target=f"guild:{guild.id}", after={"deleted": removed, "forgotten": forgotten})
        await interaction.followup.send(f"🧪 Playtest board cleared: **{removed}** message{'s' if removed != 1 else ''} deleted.", ephemeral=False)
        return
    rows = await board_report(guild)
    await audit_admin(interaction, "server.playtest.report", target=f"guild:{guild.id}", after={"pages": len(rows)})
    await reply_long(interaction, format_report(rows, guild.id), ephemeral=False)
