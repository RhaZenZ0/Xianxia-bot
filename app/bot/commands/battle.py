"""Combat: /battle, its panel views and the bounty command.

Split phase 9d (v0.19.47, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order. `_execute_battle_law_technique` lives here
rather than in law.py because `BattleView` calls it and `/law technique`
already imports `_battle_panel` from here - keeping it on this side is what
makes law → battle a one-way edge.

`_battle_panel` is also what `EventSceneView` (ui/event_scene.py) reaches
through the `EVENT_HANDLERS` registry; the binding moved here with it (it
used to sit in main.py's register_event_handlers()).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands

from ...rules.battle import matchup_label, suppression_label, vitality_band, vitality_bar
from ...ops.game_engine import GameEngineError
from ...rules.worldtime import MINUTES_PER_YEAR
from ..channels import _report_game_ui_error
from ..formatting import human_duration, roll_line
from ..registry import EVENT_HANDLERS, registered_group_command, registered_root_command
from ..runtime import (
    _explain_engine_error,
    DB,
    SETTINGS,
    WORLD,
    _USER_ACTION_LOCKS,
    _record_true_death_history,
    current_world_time,
    reply_long,
    require_character,
    serialized_user_action,
)
from ..services import COMBAT, GUILD, SIM


battle_group = app_commands.Group(name="battle",description="Resolve active danger scenes such as auction-door ambushes")
BATTLE_STYLE_CHOICES=[app_commands.Choice(name="Attack",value="attack"),app_commands.Choice(name="Defend",value="defend"),app_commands.Choice(name="Flee",value="flee")]

async def _battle_reply(
    interaction:discord.Interaction, *, content:str|None=None, embed:discord.Embed|None=None,
    view:discord.ui.View|None=None, edit_panel:bool=False, ephemeral:bool=False,
)->None:
    if edit_panel:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content,embed=embed,view=view)
        else:
            await interaction.response.edit_message(content=content,embed=embed,view=view)
        return
    if interaction.response.is_done():
        await interaction.followup.send(content=content,embed=embed,view=view,ephemeral=False)
    else:
        await interaction.response.send_message(content=content,embed=embed,view=view,ephemeral=False)


async def _battle_available_options(user_id:int,c:dict)->tuple[list[tuple[str,str,str]],list[tuple[str,str,str]]]:
    law_rows={str(r['law_id']):r for r in await DB.get_law_progress(user_id)}
    techniques:list[tuple[str,str,str]]=[]
    for tid,t in WORLD.law_system.get('techniques',{}).items():
        row=law_rows.get(str(t.get('law')))
        if not row: continue
        stage=WORLD.law_stage(int(row.get('comprehension',0)))
        if int(stage.get('index',0))>=int(t.get('requires_stage',99)) and int(c.get('realm_index',0))>=int(t.get('min_realm_index',999)):
            techniques.append((str(tid),str(t.get('name',tid)),str(t.get('description','Law technique'))))
    inv=await DB.get_inventory(user_id); usable:list[tuple[str,str,str]]=[]
    for iid,qty in inv.items():
        idef=WORLD.items.get(iid,{})
        if qty>0 and idef.get('use',{}).get('instant'):
            instant=idef.get('use',{}).get('instant',{})
            recovery=[]
            if int(instant.get('vitality_restore',0)): recovery.append(f"Vitality +{int(instant['vitality_restore'])}")
            if int(instant.get('qi_restore',0)): recovery.append(f"Qi +{int(instant['qi_restore'])}")
            usable.append((str(iid),f"{idef.get('name',iid)} x{qty}"," • ".join(recovery) or "Instant recovery"))
    return techniques[:25],usable[:25]

def _battle_embed(c:dict,b:dict,techniques:list[tuple[str,str,str]],items:list[tuple[str,str,str]], *, result_text:str|None=None)->discord.Embed:
    defeated=int(b.get("npc_hp",0))<=0
    description=result_text or ("Your opponent is defeated. Decide their fate." if defeated else "Choose your next action.")
    player_max=max(1,int(b.get('player_hp_max',0)),int(c.get('vitality_max',0)),int(b.get('player_hp',0)))
    npc_max=max(1,int(b.get('npc_hp_max',0)),int(b.get('npc_hp',0)))
    _,embed_color=vitality_band(int(b.get('player_hp',0)),player_max)
    e=discord.Embed(title=f"⚔️ Battle #{b['battle_id']} — {b['npc_name']}",description=description,color=embed_color)
    e.add_field(name="Your Vitality",value=vitality_bar(int(b['player_hp']),player_max),inline=False)
    e.add_field(name="Opponent Vitality",value=vitality_bar(int(b['npc_hp']),npc_max),inline=False)
    e.add_field(
        name="Cultivation",
        value=(
            f"**You:** {WORLD.realm_name(int(c.get('realm_index',0)))} • Stage {int(c.get('phase',1))}\n"
            f"**Opponent:** {WORLD.realm_name(int(b['npc_realm_index']))} • Stage {int(b['npc_stage'])}"
        ),inline=False,
    )
    e.add_field(
        name="Matchup",
        value=matchup_label(int(c.get('realm_index',0)),int(c.get('phase',1)),int(b['npc_realm_index']),int(b['npc_stage'])),
        inline=True,
    )
    e.add_field(name="Location",value=str(b.get('location') or 'Unknown'),inline=True)
    e.add_field(name="Suppression",value=suppression_label(int(b.get('npc_suppressed_turns',0))),inline=False)
    if defeated:
        e.add_field(name="Final Decision",value="🤝 Spare — end the battle without killing\n☠️ Kill — true NPC death with persistent world consequences",inline=False)
    else:
        e.add_field(name="Core Actions",value="⚔️ Attack • 🛡️ Defend • 🏃 Flee • 🔄 Refresh",inline=False)
        e.add_field(name="Battle Menus",value=f"🌌 Law techniques: **{len(techniques)}**\n🧪 Recovery items: **{len(items)}**",inline=False)
    e.set_footer(text=f"Battle #{int(b['battle_id'])} • Owner locked • Panel updates in place")
    return e


class BattleTechniqueSelect(discord.ui.Select):
    def __init__(self,parent:"BattleView",techniques:list[tuple[str,str,str]]):
        self.parent_view=parent
        available=bool(techniques)
        options=[discord.SelectOption(label=name[:100],value=tid[:100],description=description[:100]) for tid,name,description in techniques]
        if not options: options=[discord.SelectOption(label="No Law techniques available",value="__none__")]
        super().__init__(placeholder="Use a Law technique",min_values=1,max_values=1,options=options,disabled=not available,row=1)
    async def callback(self,interaction:discord.Interaction)->None:
        await self.parent_view._dispatch(interaction,"technique",self.values[0])


class BattleRecoverySelect(discord.ui.Select):
    def __init__(self,parent:"BattleView",items:list[tuple[str,str,str]]):
        self.parent_view=parent
        available=bool(items)
        options=[discord.SelectOption(label=name[:100],value=iid[:100],description=description[:100]) for iid,name,description in items]
        if not options: options=[discord.SelectOption(label="No recovery items carried",value="__none__")]
        super().__init__(placeholder="Use a recovery item",min_values=1,max_values=1,options=options,disabled=not available,row=2)
    async def callback(self,interaction:discord.Interaction)->None:
        await self.parent_view._dispatch(interaction,"item",self.values[0])


class BattleView(discord.ui.View):
    def __init__(self,user_id:int,battle_id:int,techniques:list[tuple[str,str,str]],items:list[tuple[str,str,str]]):
        super().__init__(timeout=300); self.user_id=int(user_id); self.battle_id=int(battle_id)
        self.add_item(BattleTechniqueSelect(self,techniques)); self.add_item(BattleRecoverySelect(self,items))
    async def interaction_check(self,interaction:discord.Interaction)->bool:
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message("This battle panel belongs to another cultivator.",ephemeral=False); return False
        return True
    async def _dispatch(self,interaction:discord.Interaction,kind:str,value:str="")->None:
        if not interaction.response.is_done(): await interaction.response.defer()
        lock=_USER_ACTION_LOCKS.setdefault(interaction.user.id,asyncio.Lock())
        async with lock:
            battle=await DB.get_battle(self.battle_id,user_id=self.user_id,active_only=True)
            active=await DB.get_active_battle(self.user_id)
            if not battle or not active or int(active['battle_id'])!=self.battle_id:
                await _battle_reply(interaction,content="⌛ This battle panel is stale. Use **/combat → Active Battle → Status** for the current battle.",view=None,edit_panel=True);return
            if kind in {"attack","defend","flee"}:
                await _resolve_battle_turn(interaction,kind,expected_battle_id=self.battle_id,edit_panel=True);return
            c=await DB.get_character(self.user_id)
            if not c:
                await _battle_reply(interaction,content="This incarnation no longer exists.",view=None,edit_panel=True);return
            if kind=="technique": result=await _execute_battle_law_technique(interaction,battle,value)
            elif kind=="item": result=await _use_battle_recovery_item(interaction,self.battle_id,value)
            else: result="🔄 Battle panel refreshed."
            updated=await DB.get_battle(self.battle_id,user_id=self.user_id,active_only=True)
            if not updated:
                await _battle_reply(interaction,content="⌛ This battle has already ended.",view=None,edit_panel=True);return
            c=await DB.get_character(self.user_id) or c
            embed,view=await _battle_panel(self.user_id,c,updated,result_text=result)
            await _battle_reply(interaction,embed=embed,view=view,edit_panel=True)
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(interaction, error, where=f"battle:{self.battle_id}:{type(item).__name__}")
    @discord.ui.button(label="Attack",style=discord.ButtonStyle.danger,emoji="⚔️",row=0)
    async def attack(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"attack")
    @discord.ui.button(label="Defend",style=discord.ButtonStyle.primary,emoji="🛡️",row=0)
    async def defend(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"defend")
    @discord.ui.button(label="Flee",style=discord.ButtonStyle.secondary,emoji="🏃",row=0)
    async def flee(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"flee")
    @discord.ui.button(label="Refresh",style=discord.ButtonStyle.secondary,emoji="🔄",row=0)
    async def refresh(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"refresh")

class BattleFinishView(discord.ui.View):
    def __init__(self,user_id:int,battle_id:int):
        super().__init__(timeout=300); self.user_id=int(user_id); self.battle_id=int(battle_id)
    async def interaction_check(self,interaction:discord.Interaction)->bool:
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message("This battle decision belongs to another cultivator.",ephemeral=False); return False
        return True
    async def _finish(self,interaction:discord.Interaction,outcome:str)->None:
        if not interaction.response.is_done(): await interaction.response.defer()
        lock=_USER_ACTION_LOCKS.setdefault(interaction.user.id,asyncio.Lock())
        async with lock: await _finish_battle(interaction,outcome,expected_battle_id=self.battle_id,edit_panel=True)
    async def _refresh(self,interaction:discord.Interaction)->None:
        if not interaction.response.is_done(): await interaction.response.defer()
        lock=_USER_ACTION_LOCKS.setdefault(interaction.user.id,asyncio.Lock())
        async with lock:
            b=await DB.get_battle(self.battle_id,user_id=self.user_id,active_only=True); c=await DB.get_character(self.user_id)
            if not b or not c or int(b.get('npc_hp',1))>0:
                await _battle_reply(interaction,content="⌛ This final-decision panel is stale.",view=None,edit_panel=True);return
            embed,view=await _battle_panel(self.user_id,c,b,result_text="🔄 Final decision refreshed. Choose the opponent's fate.")
            await _battle_reply(interaction,embed=embed,view=view,edit_panel=True)
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(interaction, error, where=f"battle-finish:{self.battle_id}:{type(item).__name__}")
    @discord.ui.button(label="Spare",style=discord.ButtonStyle.success,emoji="🤝",row=0)
    async def spare(self,interaction:discord.Interaction,button:discord.ui.Button): await self._finish(interaction,"spare")
    @discord.ui.button(label="Kill",style=discord.ButtonStyle.danger,emoji="☠️",row=0)
    async def kill(self,interaction:discord.Interaction,button:discord.ui.Button): await self._finish(interaction,"kill")
    @discord.ui.button(label="Refresh",style=discord.ButtonStyle.secondary,emoji="🔄",row=0)
    async def refresh(self,interaction:discord.Interaction,button:discord.ui.Button): await self._refresh(interaction)


async def _battle_panel(user_id:int,c:dict,b:dict,*,result_text:str|None=None)->tuple[discord.Embed,discord.ui.View]:
    techniques,items=await _battle_available_options(user_id,c)
    embed=_battle_embed(c,b,techniques,items,result_text=result_text)
    beasts=await DB.get_spirit_beasts(user_id)
    active_beast=next((x for x in beasts if int(x.get('active',0))==1),None)
    bonds=await DB.get_artifact_bonds(user_id)
    if active_beast:
        embed.add_field(name="Spirit Beast",value=f"🐉 **{active_beast['name']}** • Rank {active_beast['rank']} • Loyalty {active_beast['loyalty']} • Evolution {active_beast['evolution_stage']}",inline=False)
    awakened=[x for x in bonds if int(x.get('awakened',0))==1]
    if awakened:
        embed.add_field(name="Artifact Resonance",value=" • ".join(f"{WORLD.item_name(x['item_id'])} ({x['resonance']}%)" for x in awakened[:3]),inline=False)
    if int(b.get('npc_hp',0))<=0: return embed,BattleFinishView(user_id,int(b['battle_id']))
    return embed,BattleView(user_id,int(b['battle_id']),techniques,items)


async def _use_battle_recovery_item(interaction: discord.Interaction, battle_id: int, item_id: str) -> str:
    wt = await current_world_time()
    try:
        envelope = await COMBAT.recovery_item(
            interaction.user.id,
            battle_id=int(battle_id),
            item_id=item_id,
            game_minute=wt.total_minutes,
            action_id=f"discord:{interaction.id}:combat.recovery_item:{int(battle_id)}:{item_id}",
        )
    except GameEngineError as exc:
        return f"❌ {_explain_engine_error(exc)}"
    state = dict(envelope.get("result") or {})
    lines = [f"🧪 **Used {state.get('item_name', item_id)}**"]
    if int(state.get("vitality_restore", 0)):
        lines.append(f"Vitality: **{int(state.get('vitality', 0))}/{int(state.get('vitality_max', 0))}**")
    if int(state.get("qi_restore", 0)):
        lines.append(f"Qi: **{int(state.get('qi', 0))}/{int(state.get('qi_max', 0))}**")
    return "\n".join(lines)

async def _execute_battle_law_technique(interaction: discord.Interaction, battle: dict, technique: str) -> str:
    wt = await current_world_time()
    try:
        envelope = await COMBAT.technique(
            interaction.user.id,
            battle_id=int(battle["battle_id"]),
            technique=technique,
            game_minute=wt.total_minutes,
            minutes_per_year=MINUTES_PER_YEAR,
            base_samsara_years=SETTINGS.reincarnation_base_samsara_years,
            max_wait_seconds=SETTINGS.reincarnation_max_wait_seconds,
            action_id=f"discord:{interaction.id}:combat.technique:{int(battle['battle_id'])}:{technique}",
        )
    except GameEngineError as exc:
        return f"❌ {_explain_engine_error(exc)}"
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    lines = [f"🌌 **{result.get('technique_name', technique)}**", roll_line(roll)]
    if bool(getattr(roll, "success", False)):
        if technique == "spatial_lockdown":
            lines.append(f"Space freezes around **{battle['npc_name']}**. Their counteractions are suppressed for **{int(result.get('suppressed_turns', 1))} turn(s)**.")
        elif technique == "spatial_strangulation":
            lines.append(f"Space compresses inward for **{int(result.get('damage_dealt', 0))} conceptual damage**. Opponent Vitality: **{int(result.get('npc_hp', 0))}**.")
            if result.get("opponent_defeated"):
                lines.append("🏆 **Opponent defeated.** The battle remains open for your explicit **Spare** or **Kill** decision.")
        else:
            lines.append("Your Law dominates the local rules of the exchange.")
    else:
        lines.append("The opponent resists or tears free of your spatial authority.")
    if result.get("opponent_defeated"):
        return "\n".join(lines)
    if result.get("counter_suppressed"):
        lines.append("🌌 Opponent counter suppressed by spatial control.")
    elif result.get("counter_roll"):
        lines.append(f"**Opponent counter:** {roll_line(SimpleNamespace(**dict(result['counter_roll'])))}")
    if int(result.get("damage_taken", 0)):
        lines.append(f"🩸 You take **{int(result['damage_taken'])}** damage.")
    if str(result.get("status")) == "lost":
        injury = dict(result.get("injury") or {})
        if result.get("fate_rescue"):
            lines.append(
                f"🌠 **FATE DEFIES DEATH.** A thread of providence snaps instead of your soul. You survive at **1 Vitality** with "
                f"**{injury.get('name','a grave injury')}** (severity **{int(injury.get('severity',1))}/5**). Fate remaining: **{int(result.get('fate_remaining',0))}/9**."
            )
        elif result.get("true_death"):
            death = dict(result.get("true_death") or {})
            await _record_true_death_history(interaction.user.id, death, wt.total_minutes)
            years = int(death.get("private_years", SETTINGS.reincarnation_base_samsara_years))
            wait = max(1, int(death.get("real_wait_seconds", SETTINGS.reincarnation_max_wait_seconds)))
            lines.append(
                f"☠️ **TRUE DEATH.** Your body and current incarnation are lost. Your soul enters **Samsara** for roughly **{years:,} private years**, "
                f"compressed into at most **{human_duration(wait)}** of real time. The wheel selected **{str(death.get('target_world') or 'Mortal World')}** as your possible rebirth world. "
                "The shared world and your old family are not fast-forwarded."
            )
        else:
            lines.append(
                f"💀 **Defeated.** You survive but are incapacitated and suffer **{injury.get('name','an injury')}** "
                f"(severity **{int(injury.get('severity',1))}/5**). True death was possible in this battle."
            )
    return "\n".join(lines)

async def _finish_battle(interaction:discord.Interaction,outcome:str,*,expected_battle_id:int|None=None,edit_panel:bool=False)->None:
    c=await DB.get_character(interaction.user.id)
    if not c:
        await _battle_reply(interaction,content="Create a character first.",ephemeral=False,edit_panel=edit_panel);return
    b=(await DB.get_battle(expected_battle_id,user_id=interaction.user.id,active_only=True)) if expected_battle_id is not None else await DB.get_active_battle(interaction.user.id)
    if not b:
        await _battle_reply(interaction,content="You have no defeated opponent awaiting a final decision.",ephemeral=False,edit_panel=edit_panel);return
    if int(b.get("npc_hp",0))>0:
        await _battle_reply(interaction,content="Your opponent is still fighting.",ephemeral=False,edit_panel=edit_panel);return
    outcome=str(outcome).lower()
    if outcome not in {"spare","kill"}:
        await _battle_reply(interaction,content="Choose **Spare** or **Kill**.",ephemeral=False,edit_panel=edit_panel);return
    wt=await current_world_time()
    try:
        envelope=await COMBAT.finalize(
            interaction.user.id,battle_id=int(b["battle_id"]),outcome=outcome,game_minute=wt.total_minutes,
            action_id=f"discord:{interaction.id}:combat.finalize:{int(b['battle_id'])}:{outcome}",
        )
    except GameEngineError as exc:
        await _battle_reply(interaction,content=f"❌ {_explain_engine_error(exc)}",view=None,ephemeral=False,edit_panel=edit_panel);return
    result=dict(envelope.get("result") or {})
    replayed=bool(envelope.get("replayed"))
    if result.get("event_manifestation"):
        verb="disperse" if outcome=="kill" else "drive off"
        await _battle_reply(
            interaction,
            content=(f"⚔️ **You {verb} the hostile manifestation.** It was part of the live event, not a persistent NPC life. "
                     "Your victory has been recorded as canonical event participation and will contribute to the event aftermath."),
            view=None,ephemeral=False,edit_panel=edit_panel,
        )
        return
    impact={"impacts":[str(x) for x in list(result.get("impacts") or [])]}
    lines=[]
    npc_name=str(result.get("npc_name") or b.get("npc_name") or "your opponent")
    if outcome=="kill":
        lines.append(f"☠️ **You kill {npc_name}.** This is a canonical NPC death.")
        if "karma_score" in result:
            lines.append(f"☯️ Karma shifts to **{int(result['karma_score']):+d}**.")
        if int(result.get("grudge_intensity_delta",0)):
            lines.append(f"🗡️ A lineage grudge is now tracked with **+{int(result['grudge_intensity_delta'])}** intensity from this death.")
    else:
        lines.append(f"🤝 **You spare {npc_name}.** The battle ends without a death.")
        if "karma_score" in result:
            lines.append(f"☯️ Karma shifts to **{int(result['karma_score']):+d}**.")
        if "fate_after" in result:
            lines.append(f"🌠 Meaningful mercy draws providence: **Fate {int(result['fate_after'])}/9**.")
    impacts=list(impact.get("impacts") or [])
    if impacts:
        lines.append("🌍 **World consequences:**")
        lines.extend(f"• {x}" for x in impacts[:8])
    elif replayed:
        lines.append("🌍 This final decision was replayed idempotently; its world consequence was not applied twice.")
    else:
        lines.append("🌍 No major faction or family consequence was attached to this opponent, but the action was recorded in world history.")
    await _battle_reply(interaction,content="\n".join(lines),view=None,edit_panel=edit_panel)

async def _resolve_battle_turn(interaction:discord.Interaction,style:str,action:str="",*,expected_battle_id:int|None=None,edit_panel:bool=False)->None:
    c=await DB.get_character(interaction.user.id)
    if not c:
        await _battle_reply(interaction,content="Create a character first.",ephemeral=False,edit_panel=edit_panel);return
    b=(await DB.get_battle(expected_battle_id,user_id=interaction.user.id,active_only=True)) if expected_battle_id is not None else await DB.get_active_battle(interaction.user.id)
    if not b:
        await _battle_reply(interaction,content="You are not in an active battle.",ephemeral=False,edit_panel=edit_panel); return
    if int(b.get("npc_hp",0))<=0:
        embed,view=await _battle_panel(interaction.user.id,c,b)
        await _battle_reply(interaction,embed=embed,view=view,ephemeral=False,edit_panel=edit_panel);return
    wt=await current_world_time()
    try:
        envelope=await COMBAT.turn(
            interaction.user.id,battle_id=int(b["battle_id"]),style=style,action=action,
            game_minute=wt.total_minutes,minutes_per_year=MINUTES_PER_YEAR,
            base_samsara_years=SETTINGS.reincarnation_base_samsara_years,
            max_wait_seconds=SETTINGS.reincarnation_max_wait_seconds,
            action_id=f"discord:{interaction.id}:combat.turn:{int(b['battle_id'])}:{style}",
        )
    except GameEngineError as exc:
        await _battle_reply(interaction,content=f"❌ {_explain_engine_error(exc)}",view=None,ephemeral=False,edit_panel=edit_panel);return
    result=dict(envelope.get("result") or {})
    lines=[]
    if str(result.get("action") or "").strip(): lines.append(f"Action: *{str(result['action'])[:300]}*")
    if int(result.get("companion_bonus",0)): lines.append(f"🐉 Artifact/companion support grants **+{int(result['companion_bonus'])}** to this exchange.")
    if int(result.get("equipment_attack",0)) or int(result.get("equipment_defense",0)):
        lines.append(f"🛡️ Equipment contributes **+{int(result.get('equipment_attack',0))} offense / +{int(result.get('equipment_defense',0))} defense**. Durability is consumed by combat exchanges.")
    if result.get("player_roll"):
        lines.append(roll_line(SimpleNamespace(**dict(result["player_roll"]))))
    if result.get("defending"): lines.append("🛡️ You brace and reinforce your defenses.")
    if int(result.get("damage_dealt",0)): lines.append(f"💥 You deal **{int(result['damage_dealt'])}** damage.")
    if result.get("bugslayer_passive"):
        lines.append(
            f"✨ **{result['bugslayer_passive']}** exposes a flaw: "
            f"**+{int(result.get('bugslayer_bonus_damage', 0))} damage** and the counter is disrupted."
        )
    if result.get("escaped"):
        lines.append("🏃 **Escape successful.**")
        await _battle_reply(interaction,content="\n".join(lines),view=None,edit_panel=edit_panel);return
    # The passive suppresses the counter through the same npc_suppressed_turns
    # mechanism as spatial techniques; only attribute it to "spatial control"
    # when the sword was not the cause.
    if result.get("counter_suppressed") and not result.get("bugslayer_passive"):
        lines.append("🌌 Opponent counter suppressed by spatial control.")
    elif result.get("counter_roll"):
        lines.append(f"**Opponent counter:** {roll_line(SimpleNamespace(**dict(result['counter_roll'])))}")
    if int(result.get("damage_taken",0)): lines.append(f"🩸 You take **{int(result['damage_taken'])}** damage.")
    if result.get("opponent_defeated"):
        updated=await DB.get_battle(int(b["battle_id"]),user_id=interaction.user.id,active_only=True) or {**b,"npc_hp":0,"player_hp":int(result.get("player_hp",b.get("player_hp",1)))}
        c=await DB.get_character(interaction.user.id) or c
        lines.append("🏆 **Opponent defeated.** Decide whether to **Spare** or **Kill** them. Killing named NPCs can permanently change families, sects, regions, alliances and the economy.")
        embed,view=await _battle_panel(interaction.user.id,c,updated,result_text="\n".join(lines))
        await _battle_reply(interaction,embed=embed,view=view,edit_panel=edit_panel);return
    if str(result.get("status"))=="lost":
        injury=dict(result.get("injury") or {})
        if result.get("fate_rescue"):
            lines.append(
                f"🌠 **FATE DEFIES DEATH.** A thread of providence snaps instead of your soul. You survive at **1 Vitality** with "
                f"**{injury.get('name','a grave injury')}** (severity **{int(injury.get('severity',1))}/5**). Fate remaining: **{int(result.get('fate_remaining',0))}/9**."
            )
        elif result.get("true_death"):
            death=dict(result.get("true_death") or {})
            await _record_true_death_history(interaction.user.id,death,wt.total_minutes)
            years=int(death.get("private_years",SETTINGS.reincarnation_base_samsara_years))
            wait=max(1,int(death.get("real_wait_seconds",SETTINGS.reincarnation_max_wait_seconds)))
            lines.append(
                f"☠️ **TRUE DEATH.** Your body and current incarnation are lost. Your soul enters **Samsara** for roughly **{years:,} private years**, "
                f"compressed into at most **{human_duration(wait)}** of real time. The wheel selected **{str(death.get('target_world') or 'Mortal World')}** as your possible rebirth world. "
                "The shared world and your old family are not fast-forwarded."
            )
        else:
            lines.append(
                f"💀 **Defeated.** You survive but are incapacitated and suffer **{injury.get('name','an injury')}** "
                f"(severity **{int(injury.get('severity',1))}/5**). True death was possible in this battle."
            )
        await _battle_reply(interaction,content="\n".join(lines),view=None,edit_panel=edit_panel);return
    updated=await DB.get_active_battle(interaction.user.id) or {**b,'player_hp':int(result.get('player_hp',b.get('player_hp',1))),'npc_hp':int(result.get('npc_hp',b.get('npc_hp',1)))}
    c=await DB.get_character(interaction.user.id) or c
    embed,view=await _battle_panel(interaction.user.id,c,updated,result_text="\n".join(lines))
    await _battle_reply(interaction,embed=embed,view=view,edit_panel=edit_panel)

@registered_group_command(battle_group, name="status",description="View your active battle and all currently available options")
async def battle_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    b=await DB.get_active_battle(interaction.user.id)
    if not b: await interaction.response.send_message("You are not in an active battle.",ephemeral=False); return
    embed,view=await _battle_panel(interaction.user.id,c,b)
    await interaction.response.send_message(embed=embed,view=view,ephemeral=False)

@registered_group_command(battle_group, name="finish",description="Spare or kill an opponent you have already defeated")
@app_commands.choices(outcome=[app_commands.Choice(name="Spare",value="spare"),app_commands.Choice(name="Kill",value="kill")])
@serialized_user_action
async def battle_finish(interaction:discord.Interaction,outcome:app_commands.Choice[str])->None:
    await _finish_battle(interaction,outcome.value)


@registered_group_command(battle_group, name="challenge",description="Challenge a living NPC or martial-family head at your location")
@serialized_user_action
async def battle_challenge(interaction:discord.Interaction,target:str)->None:
    c=await require_character(interaction)
    if not c:return
    if WORLD.location_safe_zone(str(c.get("location",""))):
        await interaction.response.send_message("🛡️ Violence is suppressed in this protected location.",ephemeral=False);return
    if await DB.get_active_battle(interaction.user.id):
        await interaction.response.send_message("Finish your current battle first.",ephemeral=False);return
    info=await SIM.combat_target(str(c.get("location","")),target)
    if not info:
        await interaction.response.send_message("That living NPC/family head is not mechanically present here or cannot be openly challenged.",ephemeral=False);return
    ri=max(0,int(info.get("realm_index",0))); st=max(1,min(9,int(info.get("phase",1))))
    source=f"challenge:{info.get('target_type','npc')}:{info.get('family_id') or info.get('name')}"
    try:
        envelope=await COMBAT.start(
            interaction.user.id,kind="challenge",npc_name=str(info["name"]),npc_realm_index=ri,npc_stage=st,
            source=source,target_key=source,action_id=f"discord:{interaction.id}:combat.start:{source}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"⚔️ {exc}. Wait for that confrontation to end.",ephemeral=False);return
    result=dict(envelope.get("result") or {}); bid=int(result.get("battle_id") or 0)
    battle=await DB.get_active_battle(interaction.user.id)
    embed,view=await _battle_panel(interaction.user.id,c,battle or result)
    await interaction.response.send_message(
        content=f"⚔️ **Challenge accepted.** Battle `#{bid}` begins. If you win, you will explicitly choose whether the defeated NPC lives or dies.",
        embed=embed,view=view,
    )


@battle_challenge.autocomplete("target")
async def battle_challenge_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    c=await DB.get_character(interaction.user.id)
    if not c:return []
    needle=current.casefold().strip(); out=[]
    for row in await SIM.combat_targets(str(c.get("location",""))):
        name=str(row.get("name") or "")
        if name and (not needle or needle in name.casefold()):
            out.append(app_commands.Choice(name=name[:100],value=name[:100]))
    return out[:25]


@registered_group_command(battle_group, name="act",description="Take one action in your active battle")
@app_commands.choices(style=BATTLE_STYLE_CHOICES)
@serialized_user_action
async def battle_act(interaction:discord.Interaction,style:app_commands.Choice[str],action:str="")->None:
    await _resolve_battle_turn(interaction,style.value,action)


@registered_root_command(name="bounty", description="View active capture/death bounties attached to your incarnation", guild=GUILD)
async def bounty_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_bounties(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🎯 No active bounty is recorded against you.", ephemeral=False)
        return
    lines = [f"🎯 **Active Bounties — {c['name']}**"]
    for row in rows:
        lines.append(f"\n**{row['jurisdiction']}** • **{int(row['amount']):,}** local-value bounty\n{row.get('reason','')}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


# Phase 8 (v0.19.43) routed EventSceneView to the panel through the registry;
# phase 9d moved the binding here with the panel.
EVENT_HANDLERS.register("battle_panel", _battle_panel)
