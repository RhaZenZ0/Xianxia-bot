"""The economy: wallet, use, /storage, /auction, /market, /blackmarket and /civilization.

Split phase 9b (v0.19.45, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

import time
from typing import Any

import discord
from discord import app_commands

from ...rules.black_market import access_reason as black_market_access_reason
from ...ops.game_engine import GameEngineError
from ..locations import _known_locations, _location_is_visible, _world_is_unlocked, location_autocomplete
from ...rules.trade_receipt import format_trade_receipt
from ..formatting import human_duration
from ..pickers import auction_currency_autocomplete, usable_item_autocomplete
from ..registry import registered_group_command, registered_root_command
from ..runtime import _explain_engine_error, DB, ENGINE, WORLD, carried_item_autocomplete, character_location_display, current_world_time, log, reply_long, require_character, respond, serialized_user_action
from ..services import GUILD, SIM

@registered_root_command(name="wallet", description="View all cultivation currencies you currently hold", guild=GUILD)
async def wallet_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wallet = await DB.get_wallet(interaction.user.id)
    if not wallet:
        await interaction.response.send_message("Your wallet is empty.", ephemeral=False)
        return
    grouped: dict[str, list[str]] = {}
    for currency_id, balance in wallet.items():
        info = WORLD.currencies.get(currency_id, {})
        world = str(info.get("world", "Other"))
        grouped.setdefault(world, []).append(f"• {WORLD.currency_name(currency_id)}: **{balance:,}**")
    lines = [f"💎 **{c['name']}'s Wallet**"]
    for world in ("Mortal World", "Spiritual World", "Immortal World", "Celestial World", "Other"):
        if world in grouped:
            lines.append(f"\n**{world}**\n" + "\n".join(grouped[world]))
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_root_command(name="use", description="Use a consumable, pill, or spatial-storage treasure", guild=GUILD)
@app_commands.autocomplete(item=usable_item_autocomplete)
@serialized_user_action
async def use_item_command(interaction: discord.Interaction, item: str) -> None:
    # Ack before the engine call: the guard branches above return early,
    # so an ack inside one of them never runs on the path that mutates.
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    item_def = WORLD.items.get(item)
    if not item_def:
        await respond(interaction, "Unknown item.", ephemeral=False)
        return
    inv = await DB.get_inventory(interaction.user.id)
    if inv.get(item, 0) <= 0:
        await respond(interaction, "You do not carry that item.", ephemeral=False)
        return

    storage_upgrade = item_def.get("storage_upgrade")
    use = item_def.get("use", {})
    array_key = str(item_def.get("array_deploy") or "")
    if not storage_upgrade and not use and not array_key:
        await respond(interaction, "That item has no implemented active use yet.", ephemeral=False)
        return

    if array_key:
        wt=await current_world_time()
        try:
            e=await ENGINE.authoritative_action("array.deploy",interaction.user.id,{"item_id":item},action_id=f"discord:{interaction.id}:array.deploy")
            deployed=dict(e.get("result") or {})
        except GameEngineError as exc:
            await respond(interaction, f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
        deployed_location = deployed.get('location') or await character_location_display(c)
        await respond(interaction, f"🧿 **{deployed.get('name',item_def.get('name',item))} deployed at {deployed_location}.**",ephemeral=False)
        return

    if storage_upgrade:
        wt=await current_world_time()
        try:
            e=await ENGINE.authoritative_action("storage.upgrade",interaction.user.id,{"item_id":item},action_id=f"discord:{interaction.id}:storage.upgrade")
            upgraded=dict(e.get("result") or {})
        except GameEngineError as exc:
            await respond(interaction, f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
        await respond(interaction, f"✨ Spatial storage upgraded to **{item_def.get('name',item)}** — **{upgraded.get('slot_capacity',storage_upgrade.get('slot_capacity',24))} item stacks**.",ephemeral=False)
        return

    # v0.21.0 (roadmap "Authority I"): consume -> restore -> life extension ->
    # effect -> toxicity is one engine transaction, `item.use`, in and out of
    # battle (the restore keeps battles.player_hp in lockstep the same way
    # combat.recovery_item does for the battle panel). Python formats.
    try:
        envelope = await ENGINE.authoritative_action(
            "item.use", interaction.user.id, {"item_id": item}, action_id=f"discord:{interaction.id}:item.use:{item}",
        )
    except GameEngineError as exc:
        await respond(interaction, f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    state = dict(envelope.get("result") or {})
    lines = [f"✨ **Used {state.get('item_name', item_def.get('name', item))}**"]
    if int(state.get("qi_restore", 0)):
        lines.append(f"Qi restored to **{int(state.get('qi', 0))}/{int(state.get('qi_max', 0))}**.")
    if int(state.get("vitality_restore", 0)):
        lines.append(f"Vitality restored to **{int(state.get('vitality', 0))}/{int(state.get('vitality_max', 0))}**.")
    if int(state.get("life_extension_years", 0)):
        lines.append(
            f"🌿 Lifespan permanently extended by **{int(state['life_extension_years'])} years** "
            f"(medicine/herb extension total: **{int(state.get('life_extension_total', 0))} years**)."
        )
    if state.get("effect_name"):
        lines.append(f"Effect applied: **{state['effect_name']}**.")
    if int(state.get("toxicity_gain", 0)):
        lines.append(
            f"⚗️ Medicinal residue **+{int(state['toxicity_gain'])}** → pill toxicity "
            f"**{int(state.get('pill_toxicity', 0))}/100 ({state.get('toxicity_band', '')})**."
        )
    await respond(interaction, "\n".join(lines))


storage_group = app_commands.Group(name="storage", description="Manage your spatial pouch, ring, or inner-space treasure")


async def stored_item_autocomplete(interaction: discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    storage=await DB.get_storage(interaction.user.id) or {}; needle=current.casefold().strip(); out=[]
    for item_id,qty in storage.get("items",{}).items():
        name=WORLD.item_name(item_id)
        if not needle or needle in name.casefold() or needle in item_id.casefold():
            out.append(app_commands.Choice(name=f"{name} x{qty}"[:100],value=item_id[:100]))
    return out[:25]


@registered_group_command(storage_group, name="status",description="Inspect your current spatial storage")
async def storage_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    st=await DB.get_storage(interaction.user.id)
    if not st:
        await interaction.response.send_message("You do not possess spatial storage.",ephemeral=False);return
    lines=[f"💍 **{st['name']}** — {st['grade']}",f"Item stacks: **{st['used_slots']}/{st['slot_capacity']}**",f"Living space: **{'Yes' if st['living_space'] else 'No'}**"]
    for item_id,qty in st.get("items",{}).items(): lines.append(f"• {WORLD.item_name(item_id)} x{qty}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(storage_group, name="deposit",description="Move carried items into spatial storage")
@app_commands.autocomplete(item=carried_item_autocomplete)
@serialized_user_action
async def storage_deposit(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("storage.deposit",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:storage.deposit")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"📦 Stored **{WORLD.item_name(item)} x{quantity}**.",ephemeral=False)


@registered_group_command(storage_group, name="withdraw",description="Take items out of spatial storage")
@app_commands.autocomplete(item=stored_item_autocomplete)
@serialized_user_action
async def storage_withdraw(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("storage.withdraw",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:storage.withdraw")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🎒 Withdrew **{WORLD.item_name(item)} x{quantity}**.",ephemeral=False)


auction_group = app_commands.Group(name="auction", description="Use protected Xianxia auction houses and competitive bidding")


def _house_for_character(c:dict):
    return WORLD.auction_house_at(c.get("location",""))


@registered_group_command(auction_group, name="enter",description="Enter the local protected auction hall")
@serialized_user_action
async def auction_enter(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.enter",interaction.user.id,{},action_id=f"discord:{interaction.id}:auction.enter")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🏮 You enter **{result.get('name','the auction hall')}**. Hidden experts and formations suppress violence inside.\n🛡️ **Protection applies only inside the hall. The moment you leave through the doors, it ends.**",ephemeral=False)


@registered_group_command(auction_group, name="leave",description="Leave the auction hall; its protection ends at the door")
@serialized_user_action
async def auction_leave(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:auction.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    lines=[f"🚪 You step out of **{result.get('name','the auction hall')}** into **{result.get('outside','outside')}**.","The Pavilion's protection ends at the door."]
    incident=dict(result.get('incident') or {})
    if incident.get('triggered'):
        if incident.get('battle_id'): lines.append(f"⚔️ A stronger pursuer ambushed you. Battle **#{incident['battle_id']}** has begun.")
        else: lines.append("🌑 Someone took an interest in your auction purchase after you left the Pavilion.")
    await interaction.followup.send("\n".join(lines),ephemeral=False)


@registered_group_command(auction_group, name="browse",description="Browse active lots in the current auction house")
async def auction_browse(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    found=_house_for_character(c)
    if not found:
        await interaction.response.send_message("Enter an auction house first with **/economy → Auction House → Enter**.",ephemeral=False);return
    house_id,house=found; lots=await DB.list_active_auctions(house_id)
    if not lots:
        await interaction.response.send_message("The auction board currently has no active player lots.",ephemeral=False);return
    now=time.time(); lines=[f"🏮 **{house['name']} — Active Lots**"]
    for lot in lots[:25]:
        item_name=WORLD.item_name(str(lot['item_id'])); bid=int(lot.get('current_bid') or 0); minimum=max(int(lot['starting_bid']),bid+1)
        bidder="Anonymous" if lot.get('anonymous') and lot.get('current_bidder_user_id') else "None"
        if lot.get('current_bidder_user_id') and not lot.get('anonymous'):
            bidder_c=await DB.get_character(int(lot['current_bidder_user_id'])); bidder=bidder_c['name'] if bidder_c else 'Unknown'
        lines.append(
            f"\n`#{lot['auction_id']}` **{item_name} x{lot['quantity']}**\n"
            f"Current: **{bid or 'No bids'} {WORLD.currency_name(str(lot['currency_id']))}** • next minimum **{minimum}**\n"
            f"High bidder: **{bidder}** • closes in **{human_duration(int(float(lot['ends_at'])-now))}**"
        )
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(auction_group, name="sell",description="List a carried item for protected auction")
@app_commands.autocomplete(item=carried_item_autocomplete,currency=auction_currency_autocomplete)
@serialized_user_action
async def auction_sell(
    interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999],
    starting_bid:app_commands.Range[int,1,2000000000],duration_minutes:app_commands.Range[int,5,1440]=60,
    anonymous:bool=False,currency:str="low_spirit_stone"
)->None:
    c=await require_character(interaction)
    if not c:return
    found=_house_for_character(c)
    if not found:
        await interaction.response.send_message("You must be inside an auction house to list a lot.",ephemeral=False);return
    house_id,_=found
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.sell",interaction.user.id,{"house_id":house_id,"item_id":item,"quantity":int(quantity),"currency_id":currency,"starting_bid":int(starting_bid),"anonymous":anonymous,"ends_at":time.time()+int(duration_minutes)*60},action_id=f"discord:{interaction.id}:auction.sell")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.response.send_message(f"🏮 Lot `#{result.get('auction_id')}` listed: **{WORLD.item_name(item)} x{quantity}** starting at **{starting_bid} {WORLD.currency_name(currency)}**.",ephemeral=False)


@registered_group_command(auction_group, name="bid",description="Place an escrowed bid on an active auction lot")
@serialized_user_action
async def auction_bid(interaction:discord.Interaction,auction_id:int,amount:app_commands.Range[int,1,2000000000])->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.bid",interaction.user.id,{"auction_id":int(auction_id),"amount":int(amount)},action_id=f"discord:{interaction.id}:auction.bid")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🔨 Bid accepted on lot `#{auction_id}`: **{amount} {WORLD.currency_name(str(result.get('currency_id','low_spirit_stone')))}**.",ephemeral=False)


civilization_group=app_commands.Group(name="civilization",description="Inspect the living population, security and activity of world regions")


market_group=app_commands.Group(name="market",description="Use the dynamic local cultivation economy")


blackmarket_group=app_commands.Group(name="blackmarket",description="Find and trade with rotating underworld cultivation posts")


async def _black_market_access(user_id:int, character:dict[str,Any]) -> tuple[str|None,int,str]:
    reps = await DB.get_reputations(user_id)
    underworld = next((int(r.get("score",0)) for r in reps if str(r.get("faction_key","")).casefold()=="underworld contacts"), 0)
    membership = await DB.get_sect_membership(user_id)
    alignment = ""
    if membership:
        alignment = str((WORLD.sects.get(str(membership.get("sect_name"))) or {}).get("alignment", ""))
    reason = black_market_access_reason(
        karma=int(character.get("karma_score",0)), underworld_reputation=underworld, sect_alignment=alignment
    )
    return reason, underworld, alignment


@registered_group_command(civilization_group, name="status",description="View population, prosperity, security and recent incidents in a region")
async def civilization_status_command(interaction:discord.Interaction,location:str|None=None)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    target=(location or str(c.get('location') or '')).strip()
    if not await _location_is_visible(interaction.user.id, c, target):
        await interaction.response.send_message("That region has not been discovered by your character.", ephemeral=False); return
    data=await SIM.civilization_status(target)
    if not data:
        await interaction.response.send_message("That location has no civilization simulation record.",ephemeral=False);return
    lines=[
        f"🏙️ **Civilization — {target}**",
        f"World: **{data.get('world_name','Unknown')}**",
        f"Population: **{int(data.get('population',0)):,}**",
        f"Prosperity **{data.get('prosperity',0)}/100** • Security **{data.get('security',0)}/100** • Unrest **{data.get('unrest',0)}/100**",
        f"Spirit resources **{data.get('spirit_resources',0)}/100** • Food supply **{data.get('food_supply',0)}/100** • Migration **{int(data.get('migration_pressure',0)):+d}**",
    ]
    if data.get('npcs'):
        lines.append("\n**Notable active NPCs**")
        for npc in data['npcs'][:8]:
            public=WORLD.npcs.get(str(npc['npc_name']),{})
            public_power=str(public.get('realm','Unknown'))
            if public.get('stage'): public_power+=f" Stage {public.get('stage')}"
            lines.append(f"• **{npc['npc_name']}** — {npc.get('activity','active')} • {public_power}")
    if data.get('events'):
        lines.append("\n**Recent regional incidents**")
        lines.extend(f"• {e['event_text']}" for e in data['events'][:5])
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@civilization_status_command.autocomplete("location")
async def civilization_location_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await location_autocomplete(interaction, current)


@registered_group_command(civilization_group, name="npcs",description="View the named NPCs currently active in a region")
async def civilization_npcs_command(interaction:discord.Interaction,location:str|None=None)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    target=(location or str(c.get('location') or '')).strip()
    if not await _location_is_visible(interaction.user.id, c, target):
        await interaction.response.send_message("That region has not been discovered by your character.", ephemeral=False); return
    data=await SIM.civilization_status(target)
    if not data:
        await interaction.response.send_message("That location has no civilization simulation record.",ephemeral=False);return
    npcs=list(data.get('npcs') or [])
    if not npcs:
        await interaction.response.send_message(f"No named simulated NPCs are currently recorded in **{target}**.",ephemeral=False);return
    lines=[f"🧑‍🤝‍🧑 **Named NPC activity — {target}**"]
    for npc in npcs:
        public=WORLD.npcs.get(str(npc['npc_name']),{})
        public_power=str(public.get('realm','Unknown'))
        if public.get('stage'): public_power+=f" Stage {public.get('stage')}"
        lines.append(f"• **{npc['npc_name']}** — {npc.get('profession','Unknown role')}\n  {npc.get('activity','Following routine')} • {public_power}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@civilization_npcs_command.autocomplete("location")
async def civilization_npcs_location_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await civilization_location_autocomplete(interaction,current)


async def _player_market_item_matches(
    interaction: discord.Interaction,
    current: str,
    *,
    selling: bool = False,
) -> list[app_commands.Choice[str]]:
    character = await DB.get_character(interaction.user.id)
    if not character:
        return []
    location = str(character.get("location") or "")
    rows = await SIM.market_rows(location, 100)
    market_rows = {str(row.get("item_id")): row for row in rows}
    inventory = await DB.get_inventory(interaction.user.id) if selling else {}
    query = current.casefold().strip()
    matches: list[app_commands.Choice[str]] = []
    for item_id, row in market_rows.items():
        if selling and int(inventory.get(item_id, 0) or 0) <= 0:
            continue
        if not selling and int(row.get("supply") or 0) <= 0:
            continue
        name = WORLD.item_name(item_id)
        if query and query not in item_id.casefold() and query not in name.casefold():
            continue
        suffix = f" ({int(inventory[item_id])} carried)" if selling else f" ({int(row.get('supply') or 0)} in stock)"
        matches.append(app_commands.Choice(name=f"{name}{suffix}"[:100], value=item_id[:100]))
    return matches[:25]


@registered_group_command(blackmarket_group, name="rumors", description="Use underworld contacts to locate the current hidden posts in each realm world")
async def blackmarket_rumors(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    reason, rep, _ = await _black_market_access(interaction.user.id, c)
    if not reason:
        await interaction.response.send_message(
            f"🌑 The underworld does not trust you yet. Access requires dark Karma, a demonic-sect introduction, "
            f"or **Underworld Contacts {15}+** (yours: **{rep:+d}**).", ephemeral=False
        )
        return
    wt = await current_world_time()
    known = await _known_locations(interaction.user.id, c)
    posts = [
        post for post in await DB.list_active_black_markets(wt.total_minutes)
        if str(post.get("location")) in known and _world_is_unlocked(c, str(post.get("world_name")))
    ]
    if not posts:
        await interaction.response.send_message("The underworld routes are quiet right now.", ephemeral=False); return
    lines=[f"🌑 **Black-Market Rumors** — Access: {reason}."]
    for post in posts:
        lines.append(
            f"• **{post['world_name']}** — **{post['location']}** "
            f"• heat **{post['heat']}/100** • closes in **{max(0,int(post['closes_game_minute'])-wt.total_minutes)} game min**"
        )
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(blackmarket_group, name="status", description="Inspect an accessible black-market post at your current location")
async def blackmarket_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    reason,rep,_=await _black_market_access(interaction.user.id,c)
    if not reason:
        await interaction.response.send_message(f"🌑 No broker will deal with you yet. Underworld Contacts: **{rep:+d}**.",ephemeral=False);return
    wt=await current_world_time(); post=await DB.get_active_black_market(str(c.get('location','')),wt.total_minutes)
    if not post:
        await interaction.response.send_message("No active underworld post is hidden at your current location. Use **Economy → Black Market → Rumors**.",ephemeral=False);return
    lines=[f"🌑 **Hidden Trading Post — {post['location']}**",f"Heat: **{post['heat']}/100** • closes in **{max(0,int(post['closes_game_minute'])-wt.total_minutes)} game min**"]
    for row in post.get('stock',[]): lines.append(f"• **{WORLD.item_name(str(row['item_id']))}** x{row['quantity']} — **{row['unit_price']:,} {WORLD.currency_name(str(row['currency_id']))}** • {row['legal_status']}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


async def _black_market_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    c=await DB.get_character(interaction.user.id)
    if not c:return []
    reason,_,_=await _black_market_access(interaction.user.id,c)
    if not reason:return []
    wt=await current_world_time(); post=await DB.get_active_black_market(str(c.get('location','')),wt.total_minutes)
    if not post:return []
    q=current.casefold().strip(); out=[]
    for row in post.get('stock',[]):
        iid=str(row['item_id']); name=WORLD.item_name(iid)
        if q and q not in iid.casefold() and q not in name.casefold():continue
        out.append(app_commands.Choice(name=f"{name} ({row['quantity']} left)"[:100],value=iid[:100]))
    return out[:25]


@registered_group_command(blackmarket_group, name="buy", description="Buy forbidden or scarce goods from the hidden post at your location")
@serialized_user_action
async def blackmarket_buy(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,20]=1)->None:
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("black_market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":True},action_id=f"discord:{interaction.id}:black_market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(format_trade_receipt(icon="🌑",verb="Bought",item_label=WORLD.item_name(item),quantity=int(quantity),result=result,currency_name=WORLD.currency_name),ephemeral=False)


@blackmarket_buy.autocomplete("item")
async def blackmarket_buy_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _black_market_item_autocomplete(interaction,current)


@registered_group_command(blackmarket_group, name="sell", description="Fence a carried item through the hidden post at your location")
@serialized_user_action
async def blackmarket_sell(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,20]=1)->None:
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("black_market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":False},action_id=f"discord:{interaction.id}:black_market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(format_trade_receipt(icon="🌑",verb="Sold",item_label=WORLD.item_name(item),quantity=int(quantity),result=result,currency_name=WORLD.currency_name),ephemeral=False)


@blackmarket_sell.autocomplete("item")
async def blackmarket_sell_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _black_market_item_autocomplete(interaction,current)


@registered_group_command(market_group, name="prices",description="View dynamic local prices, supply and demand")
async def market_prices_command(interaction:discord.Interaction,item:str|None=None)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    location=str(c.get('location') or '')
    if item:
        quote=await SIM.market_quote(location,item)
        if not quote:
            await interaction.response.send_message("That item is not traded in your current local market.",ephemeral=False);return
        await interaction.response.send_message(
            f"💹 **{WORLD.item_name(item)} — {location}**\n"
            f"Buy: **{quote['buy_price']:,} {WORLD.currency_name(str(quote['currency_id']))}** • Sell: **{quote['sell_price']:,}**\n"
            f"Supply **{quote['supply']}** • Demand **{quote['demand']}** • Price index **x{float(quote['price_index']):.2f}**",
            ephemeral=False,
        );return
    rows=await SIM.market_rows(location,18)
    if not rows:
        await interaction.response.send_message("No public market is simulated at your current location.",ephemeral=False);return
    lines=[f"💹 **Dynamic Market — {location}**"]
    for row in rows:
        price=max(1,int(round(int(row['base_price'])*float(row['price_index']))))
        lines.append(f"• **{WORLD.item_name(str(row['item_id']))}** — {price:,} {WORLD.currency_name(str(row['currency_id']))} • supply {row['supply']} / demand {row['demand']} • x{float(row['price_index']):.2f}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@market_prices_command.autocomplete("item")
async def market_prices_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _player_market_item_matches(interaction, current)


@registered_group_command(market_group, name="buy",description="Buy an item from the current dynamic market")
@serialized_user_action
async def market_buy_command(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,100]=1)->None:
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":True},action_id=f"discord:{interaction.id}:market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(format_trade_receipt(icon="🪙",verb="Bought",item_label=WORLD.item_name(item),quantity=int(quantity),result=result,currency_name=WORLD.currency_name),ephemeral=False)


@market_buy_command.autocomplete("item")
async def market_buy_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _player_market_item_matches(interaction, current)


@registered_group_command(market_group, name="sell",description="Sell carried items into the current dynamic market")
@serialized_user_action
async def market_sell_command(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,100]=1)->None:
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":False},action_id=f"discord:{interaction.id}:market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(format_trade_receipt(icon="🪙",verb="Sold",item_label=WORLD.item_name(item),quantity=int(quantity),result=result,currency_name=WORLD.currency_name),ephemeral=False)


@market_sell_command.autocomplete("item")
async def market_sell_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _player_market_item_matches(interaction, current, selling=True)


