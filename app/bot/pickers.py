"""Shared option providers that need the live services.

Phase 7 of the main.py split (v0.19.42, docs/history/MAIN_SPLIT_PLAN.md). Autocomplete
callbacks are named as bare decorator arguments, so anything two modules both
decorate with must be importable from below both of them - never behind a
deferred import. carried_item_autocomplete already lives in runtime.py for
that reason; these two read SIM as well as WORLD, and runtime.py must stay
below services.py, so they live here. Later phases add to this file rather
than duplicating a picker into a command module.
"""
from __future__ import annotations

import discord
from discord import app_commands

from .runtime import DB, WORLD
from .services import SIM

async def auction_currency_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); out=[]
    for cid,info in WORLD.currencies.items():
        name=str(info.get('name',cid))
        if not needle or needle in name.casefold() or needle in cid.casefold(): out.append(app_commands.Choice(name=name[:100],value=cid[:100]))
    return out[:25]


async def auction_house_currency_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    """The coin of the floor the seller stands on, and no other (v1.34.0).

    `/auction sell` lists in the money of the house's own world and the engine
    refuses any other, so a picker that offered the sixteen currencies offered
    fifteen the sale would be refused with. Outside a house there is nothing to
    pick: the command says so itself. `auction_currency_autocomplete` stays for
    the GM's grant, which does take any coin."""
    c=await DB.get_character(interaction.user.id)
    found=WORLD.auction_house_at(str((c or {}).get("location","")))
    if not found: return []
    cid=str(found[1].get("default_currency") or "")
    name=str(WORLD.currencies.get(cid,{}).get("name",cid))
    needle=current.casefold().strip()
    if not cid or (needle and needle not in name.casefold() and needle not in cid.casefold()): return []
    return [app_commands.Choice(name=name[:100],value=cid[:100])]


async def _market_item_matches(current:str)->list[app_commands.Choice[str]]:
    # Which items an ordinary market may stock is the engine's rule
    # (market.catalog, v0.30.0); WORLD supplies only the display names.
    q=current.lower().strip(); out=[]
    for iid in await SIM.market_item_ids():
        item=WORLD.item_definition(iid)
        name=str(item.get('name',iid))
        if q and q not in name.lower() and q not in iid.lower(): continue
        out.append(app_commands.Choice(name=name[:100],value=iid[:100]))
        if len(out)>=25:break
    return out




# Split phase 9b (v0.19.45): carried-item picker used by /use (economy) and
# /spatialkey (abode); reads DB as well as WORLD.
async def usable_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    inv = await DB.get_inventory(interaction.user.id)
    needle = current.casefold().strip()
    choices=[]
    for item_id in inv:
        item=WORLD.item_definition(item_id)
        if not item.get("use") and not item.get("storage_upgrade") and not item.get("array_deploy"):
            continue
        label=str(item.get("name",item_id))
        if not needle or needle in label.casefold() or needle in item_id.casefold():
            choices.append(app_commands.Choice(name=label[:100], value=item_id[:100]))
    return choices[:25]



