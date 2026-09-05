"""The shared event-scene panel (EventSceneView and its selects/modals) and
the two thread spawners that post it.

Phase 8 of the main.py split (v0.19.43, docs/MAIN_SPLIT_PLAN.md). The panel
launches commands that live in many modules; it has always reached them
through the EVENT_HANDLERS registry rather than by import, and main.py fills
that registry at import time. The three helpers it used to call directly
(_scene_action_targets, scene_action_panel, _battle_panel - the plan's
"five back-edges") now go through the same registry, so this module reads
nothing from main.py. Definition order is the order these had in main.py.
"""
from __future__ import annotations

import time
from typing import Any

import discord

from ...ops.game_engine import GameEngineError
from ..channels import _event_archive_minutes, _report_game_ui_error, _resolve_text_channel, event_channels
from ..registry import EVENT_HANDLERS
from ..runtime import DB, ENGINE, SETTINGS, WORLD, character_location_display, current_world_time, log, reply_long
from ..services import COMBAT, SIM

EVENT_ACTION_RULES: dict[str, dict[str, Any]] = {
    "observe": {"label":"Observe", "emoji":"👁️", "attribute":"insight", "tn":10, "contribution":1},
    "investigate": {"label":"Investigate", "emoji":"🔎", "attribute":"insight", "tn":12, "contribution":2, "investigation":2},
    "aid": {"label":"Aid Locals", "emoji":"🤲", "attribute":"heart", "tn":12, "contribution":2, "support":2},
    "support": {"label":"Support Response", "emoji":"🛡️", "attribute":"heart", "tn":13, "contribution":3, "support":3},
    "interfere": {"label":"Interfere", "emoji":"🕸️", "attribute":"insight", "tn":14, "contribution":-1, "interference":3},
    "stabilize": {"label":"Stabilize", "emoji":"☯️", "attribute":"spirit", "tn":14, "contribution":3, "support":2},
    "evacuate": {"label":"Evacuate", "emoji":"🚶", "attribute":"heart", "tn":12, "contribution":2, "support":3},
    "defend": {"label":"Defend", "emoji":"⚔️", "attribute":"body", "tn":14, "contribution":3, "support":2},
    "gather": {"label":"Gather Resources", "emoji":"🌿", "attribute":"insight", "tn":12, "contribution":2},
    "compete": {"label":"Compete", "emoji":"🏆", "attribute":"body", "tn":14, "contribution":2},
    "negotiate": {"label":"Negotiate", "emoji":"🤝", "attribute":"heart", "tn":13, "contribution":2, "support":1},
    "infiltrate": {"label":"Infiltrate", "emoji":"🥷", "attribute":"insight", "tn":15, "contribution":2, "interference":2},
    "exploit": {"label":"Exploit Opportunity", "emoji":"💰", "attribute":"insight", "tn":15, "contribution":1, "interference":1},
    "endure": {"label":"Endure", "emoji":"🗿", "attribute":"body", "tn":13, "contribution":2},
    "withdraw": {"label":"Withdraw", "emoji":"↩️", "attribute":"heart", "tn":0, "contribution":0},
}


def _event_action_keys(category: str, event_type: str) -> tuple[str, ...]:
    text = f"{category} {event_type}".casefold()
    if any(x in text for x in ("beast", "demon", "invasion", "tide", "attack", "war", "siege")):
        return ("observe","investigate","defend","aid","evacuate","interfere","withdraw")
    if any(x in text for x in ("auction", "festival", "market", "merchant", "treasure")):
        return ("observe","investigate","negotiate","compete","exploit","aid","withdraw")
    if any(x in text for x in ("tribulation", "calamity", "storm", "lightning", "spatial", "phenomenon")):
        return ("observe","investigate","stabilize","endure","aid","interfere","withdraw")
    if any(x in text for x in ("secret", "ruin", "inheritance", "realm", "discovery")):
        return ("observe","investigate","gather","compete","infiltrate","aid","withdraw")
    if any(x in text for x in ("sect", "recruit", "politic", "alliance", "diplom")):
        return ("observe","investigate","negotiate","support","interfere","withdraw")
    return ("observe","investigate","aid","support","interfere","withdraw")


class EventActionSelect(discord.ui.Select):
    def __init__(self, owner: "EventSceneView") -> None:
        self.owner = owner
        options=[]
        for key in _event_action_keys(owner.category, owner.event_type):
            rule=EVENT_ACTION_RULES[key]
            options.append(discord.SelectOption(label=rule["label"], value=key, emoji=rule["emoji"]))
        super().__init__(placeholder="Choose an event action…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner.run_event_action(interaction, self.values[0])


class EventNpcTalkModal(discord.ui.Modal):
    message = discord.ui.TextInput(label="What do you say?", style=discord.TextStyle.paragraph, max_length=1200)

    def __init__(self, npc_name: str) -> None:
        super().__init__(title=f"Speak with {npc_name}"[:45])
        self.npc_name=npc_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await EVENT_HANDLERS.invoke("talk", interaction, self.npc_name, str(self.message.value))


class EventNpcSelect(discord.ui.Select):
    def __init__(self, names: list[str]) -> None:
        self.names=names[:25]
        super().__init__(
            placeholder="Choose an NPC to speak with…", min_values=1, max_values=1,
            options=[discord.SelectOption(label=n[:100], value=n[:100], emoji="💬") for n in self.names],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EventNpcTalkModal(self.values[0]))


class EventNpcSelectView(discord.ui.View):
    def __init__(self, names: list[str]) -> None:
        super().__init__(timeout=300)
        self.add_item(EventNpcSelect(names))


class EventSystemsSelect(discord.ui.Select):
    def __init__(self, systems: list[tuple[str,str,str]]) -> None:
        self.systems={key:(label,emoji) for key,label,emoji in systems}
        super().__init__(
            placeholder="Open a connected game system…", min_values=1, max_values=1,
            options=[discord.SelectOption(label=label[:100],value=key,emoji=emoji) for key,label,emoji in systems[:25]],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key=self.values[0]
        if key=="battle": await EVENT_HANDLERS.invoke("battle", interaction)
        elif key=="secret": await EVENT_HANDLERS.invoke("secret", interaction)
        elif key=="war": await EVENT_HANDLERS.invoke("war", interaction)
        elif key=="auction": await EVENT_HANDLERS.invoke("auction", interaction)
        elif key=="party": await EVENT_HANDLERS.invoke("party", interaction)
        elif key=="boss": await EVENT_HANDLERS.invoke("boss", interaction)
        elif key=="formation": await EVENT_HANDLERS.invoke("formation", interaction)
        elif key=="hunter": await EVENT_HANDLERS.invoke("hunter", interaction)
        elif key=="blackmarket": await EVENT_HANDLERS.invoke("blackmarket", interaction)
        elif key=="civilization": await EVENT_HANDLERS.invoke("civilization", interaction, None)
        elif key=="scene": await EVENT_HANDLERS.invoke("scene", interaction)
        else: await interaction.response.send_message("That system is no longer available here.",ephemeral=False)


class EventSystemsView(discord.ui.View):
    def __init__(self, systems: list[tuple[str,str,str]]) -> None:
        super().__init__(timeout=300)
        self.add_item(EventSystemsSelect(systems))


class EventSceneView(discord.ui.View):
    """Persistent, event-specific play surface backed by canonical game state."""

    def __init__(
        self, *, title: str, event_type: str, expires_at: float, location: str | None = None,
        event_key: str | None = None, category: str = "Event", severity: int = 1,
    ) -> None:
        # The ceiling used to be 21600 (6h), which silently killed the controls
        # of any longer-lived event scene at the halfway mark - content/world.json
        # has secret realms open for up to 12h. 172800 (48h) comfortably covers
        # today's longest realm with headroom for future content, while still
        # bounding the timer instead of leaving it unbounded.
        remaining=max(300,min(172800,int(expires_at-time.time())))
        super().__init__(timeout=remaining)
        self.title=str(title)[:160]; self.event_type=str(event_type or "event")[:80]
        self.expires_at=float(expires_at); self.location=str(location).strip()[:180] if location else None
        self.event_key=str(event_key or "")[:240]; self.category=str(category or "Event")[:80]
        self.severity=max(1,min(10,int(severity or 1)))
        self.add_item(EventActionSelect(self))

    def embed(self) -> discord.Embed:
        active=self.expires_at>time.time(); colour=0x9B59B6 if active else 0x5C6370
        mode=("Type normal RP messages here — the narrator reacts automatically." if SETTINGS.auto_narrate_event_threads
              else "Mention the bot for free-form RP, or use the controls below.")
        embed=discord.Embed(
            title=f"🌌 {self.title}",
            description=(f"**{self.category}** • Severity **{self.severity}/10** • {'🟢 Active' if active else '⚫ Closed'}\n"
                         +(f"📍 **{self.location}**\n" if self.location else "")+f"⏳ Closes <t:{int(self.expires_at)}:R>"),
            color=colour,
        )
        embed.add_field(name="🎮 Event Actions",value="Choose a context-specific action from the menu. Results use character attributes, event severity, and persistent participation state.",inline=False)
        embed.add_field(name="🧭 Connected Systems",value="**Systems** exposes real mechanics available here: combat, secret realms, territory wars, auctions, parties, bosses, formations, bounty hunters, black markets and regional state.",inline=False)
        embed.add_field(name="👥 Living Scene",value="**Participants** shows players and mechanically present NPCs. **Talk** opens direct NPC dialogue without remembering a slash command.",inline=False)
        embed.add_field(name="💬 Free-form RP",value=mode,inline=False)
        embed.set_footer(text="Go/SQLite remains authoritative • Event choices cannot bypass location, rolls, cooldowns or permissions")
        return embed

    async def _character_here(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        character=await DB.get_character(interaction.user.id)
        if character is None:
            await interaction.response.send_message("Create a cultivator with **/begin** first.",ephemeral=False); return None
        if self.expires_at<=time.time():
            await interaction.response.send_message("This event scene has already closed.",ephemeral=False); return None
        if self.location and str(character.get("location") or "")!=self.location:
            await interaction.response.send_message(f"You are at **{await character_location_display(character)}**. Travel to **{self.location}** before acting in this event.",ephemeral=False); return None
        return character

    async def _event_record(self) -> dict[str, Any]:
        if self.event_key:
            row=await DB.get_world_event(self.event_key)
            if row: return row
        return {"event_key":self.event_key,"event_type":self.event_type,"title":self.title,"location":self.location or "","payload":{"category":self.category,"severity":self.severity}}

    async def run_event_action(self, interaction: discord.Interaction, action_key: str) -> None:
        c=await self._character_here(interaction)
        if not c:return
        rule=EVENT_ACTION_RULES.get(action_key,EVENT_ACTION_RULES["observe"])
        wt=await current_world_time()
        if not self.event_key:
            await interaction.response.send_message("This event scene is missing its canonical event key.",ephemeral=False); return
        try:
            envelope=await ENGINE.authoritative_action(
                "world_event.act",interaction.user.id,{"event_key":self.event_key,"action_key":action_key},
                action_id=f"discord:{interaction.id}:world_event.act:{self.event_key}:{action_key}",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Event action failed: {exc}",ephemeral=False); return
        outcome=dict(envelope.get("result") or {})
        if action_key=="withdraw":
            await interaction.response.send_message("↩️ You withdraw from active involvement. The event continues without forcing another action from you.",ephemeral=False); return
        success=bool(outcome.get("success")); state=dict(outcome.get("state") or {}); roll=dict(outcome.get("roll") or {})
        sign="+" if int(roll.get("modifier",0))>=0 else ""
        roll_text=(f"2d10 ({int(roll.get('die1',0))}+{int(roll.get('die2',0))}) {sign}{int(roll.get('modifier',0))} = "
                   f"**{int(roll.get('total',0))}** vs TN **{int(roll.get('tn',0))}** — **{roll.get('degree','Result')}**")
        lines=[f"{rule['emoji']} **{rule['label']} — {'SUCCESS' if success else 'FAILURE'}**",roll_text]
        if state:
            lines.append(f"Event contribution **{int(state.get('contribution',0)):+d}** • investigation **{int(state.get('investigation',0))}** • support **{int(state.get('support',0))}** • interference **{int(state.get('interference',0))}**")
        first=dict(outcome.get("first_participation") or {})
        reward_details=[]
        if int(first.get("cultivation_awarded",0)): reward_details.append(f"Cultivation +{int(first['cultivation_awarded'])}")
        if int(first.get("spirit_stones",0)): reward_details.append(f"Spirit Stones +{int(first['spirit_stones'])}")
        if int(first.get("insight_xp",0)): reward_details.append(f"Insight +{int(first['insight_xp'])}")
        items=dict(first.get("items") or {})
        if items: reward_details.append(WORLD.item_names(items))
        effect=dict(first.get("effect") or {})
        if effect: reward_details.append(f"Effect: {effect.get('name','Event effect')}")
        if int(first.get("karma_delta",0)): reward_details.append(f"Karma {int(first['karma_delta']):+d} → {int(first.get('karma_score',0)):+d}")
        if int(first.get("fate_delta",0)): reward_details.append(f"Fate {int(first['fate_delta']):+d} → {int(first.get('fate',0))}/9")
        if reward_details: lines.append("First-participation outcome: "+" • ".join(reward_details))
        await interaction.response.send_message("\n".join(lines),ephemeral=False)

    async def _open_scene_actions(self, interaction: discord.Interaction, *, default_action: str) -> None:
        character=await self._character_here(interaction)
        if character is None:return
        # Phase 8 (v0.19.43): the scene-action and battle panels are still
        # defined in main.py (they move with their commands in phase 9), so
        # this view reaches them the way it already reaches every command it
        # launches - through the EVENT_HANDLERS registry main.py fills at
        # import - rather than importing main.py.
        npcs = await EVENT_HANDLERS.invoke("scene_action_targets", character)
        _view, kwargs = await EVENT_HANDLERS.invoke(
            "scene_action_panel",
            interaction.user.id, character, npcs,
            await character_location_display(character), action_key=default_action,
        )
        await interaction.response.send_message(ephemeral=False, **kwargs)

    @discord.ui.button(label="Systems",emoji="🧭",style=discord.ButtonStyle.primary,row=1)
    async def systems(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        location=str(c.get("location") or ""); systems:list[tuple[str,str,str]]=[("scene","Scene Status","🎭"),("civilization","Regional State","🏙️")]
        if await DB.get_active_battle(interaction.user.id): systems.append(("battle","Active Battle","⚔️"))
        if any(e.get("event_type")=="secret_realm" for e in await DB.get_active_world_events(location)) or (await DB.get_secret_realm_run(interaction.user.id) or {}).get("active"): systems.append(("secret","Secret Realm","🌀"))
        wars=[w for w in await DB.get_territory_wars(active_only=True) if str(w.get("territory_key") or "")==location]
        if wars: systems.append(("war","Territory War","🏯"))
        if WORLD.auction_house_at(location): systems.append(("auction","Auction House","🏮"))
        party=await DB.get_party(interaction.user.id)
        if party:
            systems.append(("party","Cultivation Party","👥")); systems.append(("formation","Party Formation","☯️"))
        if await DB.get_boss_encounter(user_id=interaction.user.id): systems.append(("boss","Boss Raid","👹"))
        if await DB.get_bounty_hunter_pursuit(user_id=interaction.user.id): systems.append(("hunter","Bounty Pursuit","🎯"))
        wt=await current_world_time()
        if await DB.get_active_black_market(location,wt.total_minutes): systems.append(("blackmarket","Black Market","🌑"))
        text="\n".join(f"{emoji} **{label}**" for _,label,emoji in systems)
        await interaction.response.send_message(f"🧭 **Connected systems at {location}**\n{text}",view=EventSystemsView(systems),ephemeral=False)

    @discord.ui.button(label="Participants",emoji="👥",style=discord.ButtonStyle.secondary,row=1)
    async def participants(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        lines=[f"👥 **Participants — {self.title}**"]
        if self.event_key:
            rows=await DB.list_world_event_participants(self.event_key,limit=15)
            if rows:
                lines.append("\n**Cultivators**")
                for r in rows: lines.append(f"• **{r.get('character_name','Cultivator')}** — contribution {int(r.get('contribution',0)):+d} • {int(r.get('actions_taken',0))} actions")
        regional=await SIM.civilization_status(str(c.get("location") or ""))
        npcs=list((regional or {}).get("npcs") or [])
        if npcs:
            lines.append("\n**Mechanically present NPCs**")
            for npc in npcs[:8]:
                life=await DB.get_npc_life_state(str(npc.get("npc_name") or ""))
                injury=str((life or {}).get("injury") or "").strip(); rank=str((life or {}).get("sect_rank") or npc.get("profession") or "")
                suffix=f" • {rank}" if rank else ""
                if injury:suffix+=f" • injured: {injury}"
                lines.append(f"• **{npc.get('npc_name')}**{suffix}")
        if len(lines)==1: lines.append("No persistent participants are recorded yet.")
        await reply_long(interaction,"\n".join(lines),ephemeral=False)

    @discord.ui.button(label="Consequences",emoji="📜",style=discord.ButtonStyle.secondary,row=1)
    async def consequences(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        event=await self._event_record(); payload=dict(event.get("payload") or {})
        lines=[f"📜 **Consequences — {self.title}**",str(payload.get("consequence_text") or "The outcome depends on the world's mechanical state and participant actions.")]
        if self.event_key:
            rows=await DB.list_world_event_participants(self.event_key,limit=100)
            if rows:
                lines.append(f"\nRecorded cultivators: **{len(rows)}** • net contribution **{sum(int(x.get('contribution',0)) for x in rows):+d}** • support **{sum(int(x.get('support',0)) for x in rows)}** • interference **{sum(int(x.get('interference',0)) for x in rows)}**")
            actions=await DB.get_world_event_actions(self.event_key,limit=5)
            if actions:
                lines.append("\n**Recent actions**")
                lines.extend(f"• {a.get('character_name','Cultivator')}: {str(a.get('action_key','')).replace('_',' ').title()} — {'success' if int(a.get('success',0)) else 'failure'}" for a in actions)
        await reply_long(interaction,"\n".join(lines),ephemeral=False)

    @discord.ui.button(label="Talk",emoji="💬",style=discord.ButtonStyle.success,row=1)
    async def talk_to_npc(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        regional=await SIM.civilization_status(str(c.get("location") or "")); names=[str(x.get("npc_name") or "") for x in (regional or {}).get("npcs",[]) if x.get("npc_name")]
        if not names:
            await interaction.response.send_message("No named persistent NPC is mechanically present here right now.",ephemeral=False);return
        await interaction.response.send_message("💬 Choose someone present in this event scene.",view=EventNpcSelectView(names),ephemeral=False)

    @discord.ui.button(label="Investigate",emoji="🔎",style=discord.ButtonStyle.primary,row=2)
    async def investigate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_event_action(interaction,"investigate")

    @discord.ui.button(label="Battle",emoji="⚔️",style=discord.ButtonStyle.danger,row=2)
    async def battle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        if await DB.get_active_battle(interaction.user.id):
            await EVENT_HANDLERS.invoke("battle", interaction); return
        text=f"{self.category} {self.event_type}".casefold()
        if not any(x in text for x in ("beast","demon","invasion","tide","attack","war","calamity")):
            await interaction.response.send_message("No event-specific hostile manifestation is currently forcing a battle here. Use **Systems** to inspect other combat mechanics.",ephemeral=False);return
        source=f"event:{self.event_key or self.title}"
        try:
            envelope=await COMBAT.start(
                interaction.user.id,kind="event",npc_name=f"{self.title} — hostile manifestation",
                severity=int(self.severity),source=source,target_key=f"{source}:{interaction.user.id}",
                action_id=f"discord:{interaction.id}:combat.start:{source}",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
        result=dict(envelope.get("result") or {}); bid=int(result.get("battle_id") or 0)
        battle=await DB.get_active_battle(interaction.user.id); embed,view=await EVENT_HANDLERS.invoke("battle_panel", interaction.user.id,c,battle or result)
        await interaction.response.send_message(content=f"⚔️ **Event confrontation #{bid} begins.** Defeating this manifestation contributes to the event; it is not a persistent NPC life.",embed=embed,view=view)

    @discord.ui.button(label="Scene Action",emoji="🎭",style=discord.ButtonStyle.success,row=2)
    async def scene_action(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_scene_actions(interaction,default_action="observe")

    @discord.ui.button(label="Refresh",emoji="🔄",style=discord.ButtonStyle.secondary,row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.embed(),view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(interaction,error,where=f"event-scene:{type(item).__name__}")

    async def on_timeout(self) -> None:
        for item in self.children:item.disabled=True


async def _event_scene_location(event_key: str | None, fallback_user_id: int | None = None) -> str | None:
    if event_key:
        try:
            for event in await DB.get_active_world_events():
                if str(event.get("event_key") or "") == str(event_key):
                    location = str(event.get("location") or "").strip()
                    if location:
                        return location
        except Exception:
            log.exception("Could not resolve location for event scene %s", event_key)
    if fallback_user_id is not None:
        character = await DB.get_character(int(fallback_user_id))
        if character:
            location = str(character.get("location") or "").strip()
            if location:
                return location
    return None


async def _event_scene_profile(event_key: str | None, event_type: str) -> tuple[str, int]:
    if event_key:
        try:
            row = await DB.get_world_event(str(event_key))
            if row:
                payload = dict(row.get("payload") or {})
                return (str(payload.get("category") or event_type or "Event"), max(1, min(10, int(payload.get("severity") or 1))))
        except Exception:
            log.exception("Could not resolve event profile for %s", event_key)
    return (str(event_type or "Event").replace("_", " ").title(), 1)


async def spawn_event_thread(
    interaction: discord.Interaction, *, title: str, announcement: str, event_type: str,
    expires_at: float, event_key: str | None = None
) -> discord.Thread | None:
    announcement_channel, scene_channel = await event_channels(interaction)
    if scene_channel is None:
        log.warning("No usable event-scenes channel found for event: %s", title)
        return None

    # Create the scene in the dedicated scene channel first so the announcement can link to it.
    try:
        scene_message = await scene_channel.send(
            f"🌌 **EVENT SCENE — {title}**\n"
            f"Triggered/discovered by {interaction.user.mention}. A dedicated thread is opening here."
        )
        thread = await scene_message.create_thread(
            name=(f"🌌 {title}")[:100],
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Xianxia event scene: {title}",
        )
    except discord.Forbidden:
        log.exception("Missing permission to create event thread for %s", title)
        return None
    except discord.HTTPException:
        log.exception("Discord failed to create event thread for %s", title)
        return None

    announcement_message: discord.Message | None = None
    if announcement_channel is not None:
        try:
            announcement_message = await announcement_channel.send(
                f"{announcement}\n\n💬 **Event scene:** {thread.mention}"
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not post event announcement for %s", title)

    try:
        await DB.register_event_thread(
            thread_id=thread.id,
            event_key=event_key,
            event_type=event_type,
            title=title,
            channel_id=scene_channel.id,
            message_id=scene_message.id,
            announcement_channel_id=announcement_channel.id if announcement_channel else None,
            announcement_message_id=announcement_message.id if announcement_message else None,
            triggered_by=interaction.user.id,
            expires_at=expires_at,
        )
    except Exception:
        log.exception("Could not persist event thread metadata for %s; retrying once", title)
        try:
            await DB.register_event_thread(
                thread_id=thread.id,
                event_key=event_key,
                event_type=event_type,
                title=title,
                channel_id=scene_channel.id,
                message_id=scene_message.id,
                announcement_channel_id=announcement_channel.id if announcement_channel else None,
                announcement_message_id=announcement_message.id if announcement_message else None,
                triggered_by=interaction.user.id,
                expires_at=expires_at,
            )
        except Exception:
            # An unregistered thread is invisible to DB.get_expired_event_threads(),
            # so the expiry worker could never discover or close it - a permanent
            # orphan scene. Rather than hand the caller a Discord thread that no
            # part of this bot can find again, archive/lock it (preserving it for
            # manual recovery) and report the failure instead of the thread.
            log.exception("Event thread metadata still not persisted for %s after retry; archiving orphan thread", title)
            try:
                await thread.send(
                    "⚠️ This event scene could not be registered and will not be tracked "
                    "or automatically closed. An administrator should investigate; the "
                    "thread is being archived."
                )
                await thread.edit(archived=True, locked=True, reason="event registration failed - orphan prevention")
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not archive orphaned event thread for %s", title)
            return None

    try:
        event_location = await _event_scene_location(event_key, fallback_user_id=interaction.user.id)
        event_category, event_severity = await _event_scene_profile(event_key, event_type)
        event_view = EventSceneView(
            title=title, event_type=event_type, expires_at=expires_at, location=event_location,
            event_key=event_key, category=event_category, severity=event_severity,
        )
        await thread.send(
            content=f"{interaction.user.mention} opened this live event scene.",
            embed=event_view.embed(),
            view=event_view,
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not send the event-thread starter panel for %s", title)

    return thread


async def spawn_system_event_thread(
    guild: discord.Guild, *, title: str, announcement: str, event_type: str, expires_at: float, event_key: str
) -> discord.Thread | None:
    config = await DB.get_server_config(guild.id)
    announcement_channel = await _resolve_text_channel(guild, config.get("announcement_channel_id"))
    scene_channel = await _resolve_text_channel(guild, config.get("event_scene_channel_id"))
    if scene_channel is None:
        return None
    try:
        scene_message = await scene_channel.send(f"🌌 **AUTONOMOUS WORLD EVENT — {title}**\nThe living world produced this event without a player trigger.")
        thread = await scene_message.create_thread(
            name=(f"🌌 {title}")[:100], auto_archive_duration=_event_archive_minutes(), reason=f"Autonomous Xianxia event: {title}"
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create autonomous event thread for %s", title); return None
    announcement_message=None
    if announcement_channel:
        try: announcement_message=await announcement_channel.send(f"{announcement}\n\n💬 **Event scene:** {thread.mention}")
        except (discord.Forbidden, discord.HTTPException): log.exception("Could not announce autonomous event %s",title)
    await DB.register_event_thread(
        thread_id=thread.id,event_key=event_key,event_type=event_type,title=title,channel_id=scene_channel.id,
        message_id=scene_message.id,announcement_channel_id=announcement_channel.id if announcement_channel else None,
        announcement_message_id=announcement_message.id if announcement_message else None,triggered_by=None,expires_at=expires_at,
    )
    try:
        event_location = await _event_scene_location(event_key)
        event_category, event_severity = await _event_scene_profile(event_key, event_type)
        event_view = EventSceneView(
            title=title, event_type=event_type, expires_at=expires_at, location=event_location,
            event_key=event_key, category=event_category, severity=event_severity,
        )
        await thread.send(
            content="**The world moves on its own.** Travel to the event location to participate.",
            embed=event_view.embed(),
            view=event_view,
        )
    except (discord.Forbidden,discord.HTTPException):
        log.exception("Could not send autonomous event starter panel for %s", title)
    return thread


