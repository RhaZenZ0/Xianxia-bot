"""Live war cards (v1.24.0): one message per sect war in its world's war-front
channel, kept current while the war is fought and left standing, with the
verdict, once it ends.

Presentation only, in `stall_feed.py`'s shape. The war is the engine's
`territory_wars` row with its operation row and blow-by-blow; this module reads
them and keeps one Discord message in step. The only thing written here is the
message id (`war_card_messages`), so a lost or deleted message is a card that
is not there, never a war that is not - the next refresh posts it again.

A card is refreshed by the command that moved the war (`/war act`, and a
`/territory claim` that opened one) and by `sync_wars` after every simulation
tick, which is when the world's own sieges are fought and its own wars
declared. A war that has ended is drawn once more with its verdict and then
forgotten: the message stays in the channel as the record of it, and no later
tick edits it again.
"""
from __future__ import annotations

import asyncio
from typing import Any

import discord

from .cards import Card, card_view
from .channels import _resolve_text_channel, war_channel, world_of_location
from .runtime import DB, log


# What each card last said, per (guild, war), in this process: the tick
# refreshes every war, and an unchanged card is left alone.
_WAR_LAST_SAID: dict[tuple[int, int], str] = {}

# One lock per war's card: a command's refresh and the tick's sync must not both
# read "no card yet" and both post (v1.12.3's stall-card finding).
_WAR_CARD_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}

MINUTES_PER_DAY = 1440


def _bar(percent: int, width: int = 10) -> str:
    filled = max(0, min(width, round(max(0, min(100, percent)) * width / 100)))
    return "█" * filled + "░" * (width - filled)


def war_card(war: dict[str, Any], *, game_minute: int | None = None) -> Card:
    """The card: who besieges what, the walls, the siege, both sides' morale and
    force, the last blows struck, and - once it is over - who won."""
    op = dict(war.get("operations") or {})
    attacker, defender = str(war.get("attacker_key") or "?"), str(war.get("defender_key") or "?")
    ground = str(war.get("territory_name") or war.get("territory_key") or "somewhere")
    active = str(war.get("status") or "") == "active"
    siege = int(op.get("siege_progress") or 0)
    card = Card(
        title=f"⚔️ War #{int(war.get('war_id') or 0)} — {attacker} against {defender}",
        colour=0xB0413E if active else 0x6B6B6B,
        description=f"For **{ground}** · walls **{int(war.get('territory_defense') or 0)}**",
    )
    card.add_field(name="Siege", value=f"`{_bar(siege)}` **{siege}%**", inline=False)
    card.add_field(name="Morale", value=f"{attacker} **{int(op.get('attacker_morale') or 0)}** · "
                                        f"{defender} **{int(op.get('defender_morale') or 0)}**", inline=False)
    card.add_field(name="Force committed", value=f"{attacker} **{int(op.get('attacker_force') or 0)}** · "
                                                 f"{defender} **{int(op.get('defender_force') or 0)}**", inline=False)
    allies = list(war.get("allies") or [])
    if allies:
        beside = {"attacker": attacker, "defender": defender}
        card.add_field(name="Allies", value="\n".join(
            f"🤝 **{a.get('sect_name')}** beside {beside.get(str(a.get('side')), '?')}" for a in allies)[:1024], inline=False)
    blows = []
    for row in list(war.get("recent_actions") or [])[:3]:
        who = f"<@{row['user_id']}>" if row.get("user_id") else (str(row.get("sect_name") or "") or "the field")
        side = attacker if str(row.get("side")) == "attacker" else defender
        blows.append(f"{who} — **{str(row.get('tactic') or '').title()}** for {side} "
                     f"(siege {int(row.get('siege_delta') or 0):+d})")
    if blows:
        card.add_field(name="Lately", value="\n".join(blows)[:1024], inline=False)
    if active:
        card.set_footer(text="Fight from /sect → War → Act; a sect allied to either side may fight beside it. Peace from War → Peace.")
    else:
        winner = str(op.get("winner_key") or "")
        verdict = str(op.get("resolution") or "")
        if verdict == "attacker_occupation":
            line = f"🏯 **{winner}** has taken {ground}."
            until = int(op.get("occupation_until_game_minute") or 0)
            if game_minute is not None and until > game_minute:
                days = -(-(until - int(game_minute)) // MINUTES_PER_DAY)
                line += f" Occupied for {days} more day(s): {defender} may yet come back for it."
        elif verdict == "occupation_lost":
            line = f"🏯 **{winner}** took {ground}, and has since lost it again."
        elif verdict == "peace":
            line = f"🕊️ Peace: **{defender}** keeps {ground}, and {attacker} may not move on it again for a while."
        elif verdict == "ceded":
            line = f"🕊️ Peace: **{defender}** ceded {ground} to **{attacker}** at the table."
        elif verdict == "set_aside":
            line = (f"🕊️ The war over {ground} is set aside: it is part of a city, and a sect holds a place, "
                    "not one of its streets. Nobody wins it.")
        elif verdict == "defender_holds":
            line = f"🛡️ **{winner}** holds {ground}. {attacker} may not move on it again for a while."
        else:
            line = f"The war is over{f': **{winner}** won' if winner else ''}."
        card.add_field(name="Ended", value=line, inline=False)
    return card


async def _delete_war_card(guild: discord.Guild, record: dict[str, Any]) -> None:
    _WAR_LAST_SAID.pop((guild.id, int(record["war_id"])), None)
    channel = await _resolve_text_channel(guild, record.get("channel_id"))
    if channel is not None:
        try:
            message = await channel.fetch_message(int(record["message_id"]))
            await message.delete()
        except discord.HTTPException:
            pass
    await DB.forget_war_card(guild.id, int(record["war_id"]))


async def _keep_war_card(guild: discord.Guild, war: dict[str, Any], record: dict[str, Any] | None, *,
                     force: bool = False, game_minute: int | None = None) -> None:
    key = (guild.id, int(war["war_id"]))
    lock = _WAR_CARD_LOCKS.setdefault(key, asyncio.Lock())
    waited = lock.locked()
    async with lock:
        if record is None or waited:
            record = next((row for row in await DB.list_war_cards(guild.id) if int(row["war_id"]) == key[1]), None)
        await _keep_war_card_locked(guild, war, record, force=force, game_minute=game_minute)


async def _keep_war_card_locked(guild: discord.Guild, war: dict[str, Any], record: dict[str, Any] | None, *,
                            force: bool, game_minute: int | None) -> None:
    channel = await war_channel(guild, world_of_location(str(war.get("territory_key") or "")))
    if channel is None:
        if record is not None:
            await _delete_war_card(guild, record)
        return
    card = war_card(war, game_minute=game_minute)
    key = (guild.id, int(war["war_id"]))
    said = card.text()
    if record is not None and int(record.get("channel_id") or 0) == channel.id:
        if not force and _WAR_LAST_SAID.get(key) == said:
            return
        try:
            message = await channel.fetch_message(int(record["message_id"]))
            await message.edit(content=None, embed=None, view=card_view(card))
            _WAR_LAST_SAID[key] = said
            return
        except discord.HTTPException:
            pass  # deleted by hand: post it again below
    elif record is not None:
        await _delete_war_card(guild, record)
    try:
        message = await channel.send(view=card_view(card), allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        log.exception("Could not post the war card for war %s", war.get("war_id"))
        return
    _WAR_LAST_SAID[key] = said
    await DB.remember_war_card(guild_id=guild.id, war_id=int(war["war_id"]), channel_id=channel.id, message_id=message.id)


async def _finish(guild: discord.Guild, war: dict[str, Any], record: dict[str, Any] | None, game_minute: int | None) -> None:
    """Draw an ended war's verdict once and forget the card, leaving the
    message where it is as the record of the war."""
    if record is None:
        return
    await _keep_war_card(guild, war, record, force=True, game_minute=game_minute)
    _WAR_LAST_SAID.pop((guild.id, int(war["war_id"])), None)
    await DB.forget_war_card(guild.id, int(war["war_id"]))


async def refresh_war(guild: discord.Guild | None, war_id: int, *, game_minute: int | None = None) -> None:
    """Bring one war's card up to date after a command moved it. A war that
    has just ended gets its verdict and is let go. Never raises - it runs
    beside a reply the engine has already committed."""
    if guild is None or not war_id:
        return
    try:
        war = next((row for row in await DB.get_territory_wars(active_only=False) if int(row["war_id"]) == int(war_id)), None)
        if war is None:
            return
        record = next((row for row in await DB.list_war_cards(guild.id) if int(row["war_id"]) == int(war_id)), None)
        if str(war.get("status") or "") == "active":
            await _keep_war_card(guild, war, record, force=True, game_minute=game_minute)
        elif record is None:
            # Won with the very act that refreshed it: post the verdict once.
            await _keep_war_card(guild, war, None, force=True, game_minute=game_minute)
            await DB.forget_war_card(guild.id, int(war_id))
        else:
            await _finish(guild, war, record, game_minute)
    except Exception:
        log.exception("Could not refresh the war card for war %s", war_id)


async def sync_wars(guild: discord.Guild | None, *, game_minute: int | None = None) -> int:
    """Every card in step with every war. Called after each simulation tick;
    returns how many ended wars had their verdict drawn."""
    if guild is None:
        return 0
    wars = {int(row["war_id"]): row for row in await DB.get_territory_wars(active_only=False)}
    records = {int(row["war_id"]): row for row in await DB.list_war_cards(guild.id)}
    finished = 0
    for war_id, record in records.items():
        war = wars.get(war_id)
        if war is None:
            await _delete_war_card(guild, record)
        elif str(war.get("status") or "") != "active":
            await _finish(guild, war, record, game_minute)
            finished += 1
    for war_id, war in wars.items():
        if str(war.get("status") or "") == "active":
            await _keep_war_card(guild, war, records.get(war_id), game_minute=game_minute)
    return finished
