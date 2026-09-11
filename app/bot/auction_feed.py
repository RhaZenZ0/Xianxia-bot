"""Live auction cards (v0.33.1): one message per open lot in the house's
channel, kept current while the lot is open and struck when it settles.

Presentation only. The lot is the `auctions` row the engine owns; this module
reads it and keeps a Discord message in step with it - listed on
`auction.sell`, refreshed on `auction.bid`, closed after the simulation tick
that settles it (finalizeAuctions in Go). The only thing written here is the
message id (`auction_lot_messages`), so a lost or deleted message is a card
that is not there, never a lot that is not.
"""
from __future__ import annotations

import time
from typing import Any

import discord

from .channels import _resolve_text_channel
from .runtime import DB, WORLD, log


def _currency(lot: dict[str, Any]) -> str:
    return WORLD.currency_name(str(lot.get("currency_id") or "low_spirit_stone"))


async def _name(user_id: Any) -> str:
    if not user_id:
        return "None"
    character = await DB.get_character(int(user_id))
    return str(character["name"]) if character else "Unknown"


async def lot_embed(house_id: str, lot: dict[str, Any], *, state: str = "open") -> discord.Embed:
    """The card. ``state`` is open, sold or unsold."""
    house = dict(WORLD.auction_houses.get(house_id) or {})
    item = WORLD.item_name(str(lot.get("item_id") or ""))
    quantity = int(lot.get("quantity") or 1)
    bid = int(lot.get("current_bid") or 0)
    bidder_id = lot.get("current_bidder_user_id")
    anonymous = bool(lot.get("anonymous"))
    bidder = "Anonymous" if anonymous and bidder_id else await _name(bidder_id)
    if not bidder_id and str(lot.get("merchant_bidder") or ""):
        # v0.37.0: a travelling merchant holds the high bid; its purse is
        # the escrow, and a player who outbids it sees it refunded.
        holder = dict(WORLD.merchants.get(str(lot.get("merchant_bidder"))) or {})
        bidder = f"{holder.get('name') or lot.get('merchant_bidder')} (travelling merchant)"
    merchant_key = str(lot.get("merchant_buyer") or "")
    if merchant_key:
        # v0.34.1: no bidder reached the reserve, but a travelling merchant
        # took the lot at its starting bid; the seller is paid all the same.
        merchant = dict(WORLD.merchants.get(merchant_key) or {})
        bidder = f"{merchant.get('name') or merchant_key} (travelling merchant)"
    seller = await _name(lot.get("seller_user_id"))
    minimum = max(int(lot.get("starting_bid") or 0), bid + 1)
    ends_at = int(float(lot.get("ends_at") or time.time()))
    if state == "sold":
        title = f"🔨 SOLD — {item} ×{quantity}"
        colour = 0x5B8C5A
    elif state == "unsold":
        title = f"🏮 Unsold — {item} ×{quantity}"
        colour = 0x6C7A89
    else:
        title = f"🏮 Lot #{lot.get('auction_id')} — {item} ×{quantity}"
        colour = 0xA5863B
    embed = discord.Embed(title=title, colour=colour, description=str(house.get("name") or house_id))
    embed.add_field(name="Seller", value=seller, inline=True)
    if state == "sold":
        embed.add_field(name="Struck to", value=bidder, inline=True)
        embed.add_field(name="Hammer price", value=f"{bid} {_currency(lot)}", inline=True)
    elif state == "unsold":
        embed.add_field(name="Result", value="No bids reached the reserve; the lot returns to its seller.", inline=False)
    else:
        embed.add_field(name="Current bid", value=(f"{bid} {_currency(lot)}" if bid else "No bids yet"), inline=True)
        embed.add_field(name="High bidder", value=bidder, inline=True)
        embed.add_field(name="Next minimum", value=f"{minimum} {_currency(lot)}", inline=True)
        embed.add_field(name="Closes", value=f"<t:{ends_at}:R> (<t:{ends_at}:t>)", inline=False)
        embed.set_footer(text=f"Bid with /economy → Auction House → Bid, lot {lot.get('auction_id')}. Protection ends at the doors.")
    return embed


async def _channel_for(guild: discord.Guild, house_id: str) -> discord.TextChannel | None:
    rows = {str(row["house_id"]): row for row in await DB.get_auction_house_channels(guild.id)}
    row = rows.get(house_id)
    if not row:
        return None
    return await _resolve_text_channel(guild, row.get("channel_id"))


async def announce_lot(guild: discord.Guild | None, house_id: str, auction_id: int) -> discord.TextChannel | None:
    """Post the card for a lot just listed. Returns the channel it landed in,
    or None when the house has no channel here (nothing is lost: the lot is
    on the board regardless)."""
    if guild is None:
        return None
    channel = await _channel_for(guild, house_id)
    lot = await DB.get_auction(int(auction_id))
    if channel is None or lot is None:
        return None
    try:
        message = await channel.send(embed=await lot_embed(house_id, lot))
    except discord.HTTPException:
        log.exception("Could not post the live card for lot %s", auction_id)
        return None
    await DB.remember_auction_lot_message(
        auction_id=int(auction_id), guild_id=guild.id, house_id=house_id, channel_id=channel.id, message_id=message.id,
    )
    return channel


async def _edit_card(guild: discord.Guild, record: dict[str, Any], lot: dict[str, Any], *, state: str) -> bool:
    channel = await _resolve_text_channel(guild, record.get("channel_id"))
    if channel is None:
        return False
    try:
        message = await channel.fetch_message(int(record["message_id"]))
        await message.edit(embed=await lot_embed(str(record["house_id"]), lot, state=state))
        return True
    except discord.HTTPException:
        return False


async def refresh_lot(guild: discord.Guild | None, auction_id: int) -> None:
    """Bring the card up to date after a bid."""
    if guild is None:
        return
    records = {int(row["auction_id"]): row for row in await DB.list_auction_lot_messages(guild.id)}
    record = records.get(int(auction_id))
    lot = await DB.get_auction(int(auction_id))
    if record is None or lot is None:
        return
    await _edit_card(guild, record, lot, state="open" if int(lot.get("active") or 0) else "sold")


async def settle_lots(guild: discord.Guild | None) -> int:
    """Strike every card whose lot the engine has settled. Called after each
    simulation tick; returns how many cards were closed."""
    if guild is None:
        return 0
    closed = 0
    for record in await DB.list_auction_lot_messages(guild.id):
        lot = await DB.get_auction(int(record["auction_id"]))
        if lot is None:
            await DB.forget_auction_lot_message(int(record["auction_id"]))
            continue
        if int(lot.get("active") or 0):
            if str(lot.get("merchant_bidder") or ""):
                # A merchant bid on the tick, not through a command, so the
                # card learns of it here.
                await _edit_card(guild, record, lot, state="open")
            continue
        struck = (lot.get("current_bidder_user_id") or lot.get("merchant_buyer")) and int(lot.get("current_bid") or 0) > 0
        state = "sold" if struck else "unsold"
        await _edit_card(guild, record, lot, state=state)
        await DB.forget_auction_lot_message(int(record["auction_id"]))
        closed += 1
    return closed
