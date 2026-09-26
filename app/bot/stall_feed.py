"""Live stall cards (v1.7.0): one message per open stall in its world's
market-stalls channel, kept current while the stall is open and removed when
it closes.

Presentation only, in `auction_feed.py`'s shape. The stall is the engine's
`player_stalls` row and its listings; this module reads them and keeps one
Discord message in step. The only thing written here is the message id
(`stall_card_messages`), so a lost or deleted message is a card that is not
there, never a stall that is not - the next refresh posts it again.

A card is refreshed by the command that changed the stall (open, list,
withdraw, a buy, close) and by `sync_stalls` after every simulation tick,
which is what catches the town's own purchases (`npc_stalls.go` runs inside
the tick and tells Python only a count).
"""
from __future__ import annotations

from typing import Any

import discord

from .channels import _resolve_text_channel, stall_channel, world_of_location
from .runtime import DB, WORLD, log


# What each card last said, per (guild, keeper), in this process. The tick
# refreshes every stall, and editing every card every tick whether or not it
# changed would spend Discord's rate limit on nothing; a card whose text is the
# same as the last one posted is left alone. A restart forgets this, so the first
# tick after one edits each card once.
_LAST_SAID: dict[tuple[int, int], dict[str, Any]] = {}


def _distance_percent() -> int:
    return int((WORLD.data.get("stall_system") or {}).get("distance_percent_per_hop") or 0)


def stall_embed(stall: dict[str, Any]) -> discord.Embed:
    """The card: the stall, its keeper and city, and everything laid on it."""
    coin = WORLD.currency_name(str(stall.get("currency_id") or "low_spirit_stone"))
    listings = list(stall.get("listings") or [])
    embed = discord.Embed(
        title=f"\U0001f9fa {stall.get('name') or 'A stall'} — {stall.get('city') or 'somewhere'}",
        colour=0xA5863B,
        description=f"Kept by **{stall.get('owner_name') or 'a cultivator'}** · priced in {coin}",
    )
    if listings:
        lines = [
            f"#{int(row.get('listing_id') or 0)} **{WORLD.item_name(str(row.get('item_id') or ''))}** "
            f"×{int(row.get('quantity') or 0)} — {int(row.get('unit_price') or 0)} each"
            for row in listings
        ]
        embed.add_field(name="On the stall", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="On the stall", value="Nothing laid out right now.", inline=False)
    pct = _distance_percent()
    far = f" — {pct}% more a road from farther off" if pct else ""
    embed.set_footer(text=f"Buy from anywhere: /economy → Market Stalls → Buy{far}.")
    return embed


async def _delete_card(guild: discord.Guild, record: dict[str, Any]) -> None:
    _LAST_SAID.pop((guild.id, int(record["user_id"])), None)
    channel = await _resolve_text_channel(guild, record.get("channel_id"))
    if channel is not None:
        try:
            message = await channel.fetch_message(int(record["message_id"]))
            await message.delete()
        except discord.HTTPException:
            pass
    await DB.forget_stall_card(guild.id, int(record["user_id"]))


async def _keep_card(guild: discord.Guild, stall: dict[str, Any], record: dict[str, Any] | None, *, force: bool = False) -> None:
    """Edit the card in place, or post it when there is none (or it was
    deleted by hand, or the stall's world has a new channel). Unless
    ``force``, a card that would say what it already says is left alone."""
    channel = await stall_channel(guild, world_of_location(str(stall.get("city") or "")))
    if channel is None:
        if record is not None:
            await _delete_card(guild, record)
        return
    embed = stall_embed(stall)
    key = (guild.id, int(stall["user_id"]))
    said = embed.to_dict()
    if record is not None and int(record.get("channel_id") or 0) == channel.id:
        if not force and _LAST_SAID.get(key) == said:
            return
        try:
            message = await channel.fetch_message(int(record["message_id"]))
            await message.edit(embed=embed)
            _LAST_SAID[key] = said
            return
        except discord.HTTPException:
            pass
    elif record is not None:
        await _delete_card(guild, record)
    try:
        message = await channel.send(embed=embed)
    except discord.HTTPException:
        log.exception("Could not post the stall card for %s", stall.get("user_id"))
        return
    _LAST_SAID[key] = said
    await DB.remember_stall_card(guild_id=guild.id, user_id=int(stall["user_id"]), channel_id=channel.id, message_id=message.id)


async def refresh_stall(guild: discord.Guild | None, user_id: int) -> None:
    """Bring one keeper's card up to date after a command changed their stall:
    post it, edit it, or take it down when the stall is gone. Never raises -
    it runs beside a reply the engine has already committed."""
    if guild is None:
        return
    try:
        stall = next((row for row in await DB.list_player_stalls() if int(row["user_id"]) == int(user_id)), None)
        record = next((row for row in await DB.list_stall_cards(guild.id) if int(row["user_id"]) == int(user_id)), None)
        if stall is None:
            if record is not None:
                await _delete_card(guild, record)
            return
        await _keep_card(guild, stall, record, force=True)
    except Exception:
        log.exception("Could not refresh the stall card for %s", user_id)


async def sync_stalls(guild: discord.Guild | None) -> int:
    """Every card in step with every stall. Called after each simulation tick;
    returns how many cards were taken down."""
    if guild is None:
        return 0
    stalls = {int(row["user_id"]): row for row in await DB.list_player_stalls()}
    records = {int(row["user_id"]): row for row in await DB.list_stall_cards(guild.id)}
    removed = 0
    for user_id, record in records.items():
        if user_id not in stalls:
            await _delete_card(guild, record)
            removed += 1
    for user_id, stall in stalls.items():
        await _keep_card(guild, stall, records.get(user_id))
    return removed


async def take_down_card(guild: discord.Guild | None, record: dict[str, Any] | None) -> None:
    """Remove a card whose record was read before an erasure or a reset swept
    it (the v1.0.8 rule: read the id before the engine call, act after)."""
    if guild is None or record is None:
        return
    try:
        await _delete_card(guild, record)
    except Exception:
        log.exception("Could not take down the stall card for %s", record.get("user_id"))


async def card_record(guild: discord.Guild | None, user_id: int) -> dict[str, Any] | None:
    if guild is None:
        return None
    try:
        return next((row for row in await DB.list_stall_cards(guild.id) if int(row["user_id"]) == int(user_id)), None)
    except Exception:
        log.exception("Could not read the stall card for %s", user_id)
        return None
