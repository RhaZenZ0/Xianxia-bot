"""Exploration: explore, hunt, craft, /alchemy, /realmhub and /travel.

Split phase 9c (v0.19.46, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order (one contiguous block); reads only modules
below main.py. `_run_crafting` stays here because `craft` is its only caller
and lives in this file.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands

from ...rules.alchemy import alchemy_purge_refusal, toxicity_band
from ...rules.birthfamily import family_profession_bonus
from ...ops.game_engine import GameEngineError
from ...rules.progression_systems import profession_rank, profession_xp_needed
from ...rules.realm_hubs import REALM_HUBS, realm_hub, realm_hub_by_location
from ...rules.sect_manor import manor_craft_bonus
from ...rules.sect_recruitment import recruitment_definition
from ..channels import send_long_to_thread, world_of_location
from ..character_state import record_quest_progress, announce_quest_progress
from ..discovery import (
    LOCATION_DISCOVERY_IMAGES,
    send_location_discovery_image,
    travel_first_discovers_location,
)
from ..formatting import human_duration, roll_line
from ..hubs import register_hub_option_hint, HubDynamicOption, register_hub_option_provider
from ..locations import _known_locations, access_realm_index, destination_groups, location_autocomplete, npcs_present
from ..registry import VIEW_RESTORERS, registered_group_command, registered_root_command
from ..runtime import (
    DB,
    _sync_realm_presence_roles,
    ENGINE,
    SETTINGS,
    WORLD,
    _explain_engine_error,
    budget_refusal_line,
    character_location_display,
    current_world_time,
    log,
    reply_long,
    require_character,
    serialized_user_action,
)
from ..services import (
    EXPLORATION,
    GUILD,
    NARRATOR,
    NARRATOR_CONTEXT,
    NARRATOR_QUEUE,
    QUESTS,
    SIM,
)
from ..threads import ensure_expedition_thread
from ..ui.event_scene import spawn_event_thread
from .sect import _sect_recruitment_at_location


# Panels that outlive the process (v1.0.0-rc.22). An exploration encounter is
# open for up to six hours; the view used to be open only as long as the bot
# was. A digest keeps the id inside Discord's 100-character ceiling however
# long an event id and a user id get, and includes the owner so two players in
# the same encounter do not share controls.

def _exploration_event_token(event_id: str, owner_user_id: int) -> str:
    return hashlib.blake2s(f"{event_id}:{int(owner_user_id)}".encode("utf-8"), digest_size=8).hexdigest()


class ExplorationEventView(discord.ui.View):
    """Personal exploration-event controls backed entirely by Go authority."""

    def __init__(self, owner_user_id: int, event: dict[str, Any]) -> None:
        self.owner_user_id = int(owner_user_id)
        self.event = dict(event or {})
        expires_at = float(self.event.get("expires_at") or time.time() + 7200)
        # No timeout (v1.0.0-rc.22): the encounter is open for up to six hours
        # and the panel used to die with the process, so a reboot left the
        # player staring at an interrupted exploration they could not act on.
        # The engine still refuses an event that has ended, and `_sync_buttons`
        # disables what is no longer available, so the closing time governs
        # play whether or not the view is still counting.
        super().__init__(timeout=None)
        # The decorated ids are the same on every instance, which is fine for
        # one live panel and wrong for two: namespaced by event and owner, a
        # restored panel answers its own clicks and nobody else's.
        self._stamp_custom_ids()
        self._sync_buttons()

    def _stamp_custom_ids(self) -> None:
        token = _exploration_event_token(str(self.event.get("event_id") or ""), self.owner_user_id)
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            action = str(item.custom_id or "").split(":")[-1] or "button"
            item.custom_id = f"expl:{token}:{action}"[:100]

    def _available_keys(self) -> set[str]:
        return {
            str(row.get("key") or "")
            for row in list(self.event.get("available_actions") or [])
            if isinstance(row, dict)
        }

    def _sync_buttons(self) -> None:
        available = self._available_keys()
        active = bool(self.event.get("active"))
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            custom_id = "exploration_event:" + str(item.custom_id or "").split(":")[-1]
            if custom_id == "exploration_event:refresh":
                item.disabled = False
                continue
            key = custom_id.rsplit(":", 1)[-1]
            item.disabled = (not active) or key not in available

    def embed(self) -> discord.Embed:
        active = bool(self.event.get("active"))
        title = str(self.event.get("title") or "Unexpected Event")
        description = str(self.event.get("description") or "Something unexpected interrupts your exploration.")
        location = str(self.event.get("location") or "Unknown")
        expires_at = int(float(self.event.get("expires_at") or time.time()))
        embed = discord.Embed(
            title=f"🌌 {title}",
            description=(
                f"**{self.event.get('category','Fate Encounter')}** • {'🟢 Active' if active else '⚫ Resolved'}\n"
                f"📍 **{location}** • ⏳ <t:{expires_at}:R>\n\n{description}"
            ),
            color=0x9B59B6 if active else 0x5C6370,
        )
        labels = [
            str(row.get("label") or row.get("key") or "Action")
            for row in list(self.event.get("available_actions") or [])
            if isinstance(row, dict)
        ]
        embed.add_field(name="Available actions", value=(" • ".join(labels) if labels else "This encounter has ended."), inline=False)
        embed.set_footer(text="Exploration is paused until this event is resolved or left • Go owns rolls, rewards, and state")
        return embed

    async def _owned(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_user_id:
            return True
        await interaction.response.send_message("This is another cultivator's personal exploration event.", ephemeral=False)
        return False

    @staticmethod
    def _outcome_lines(outcome: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        if int(outcome.get("cultivation_awarded", 0)):
            lines.append(f"Cultivation +{int(outcome['cultivation_awarded'])}")
        if int(outcome.get("spirit_stones", 0)):
            lines.append(f"Spirit Stones +{int(outcome['spirit_stones'])}")
        if int(outcome.get("insight_xp", 0)):
            lines.append(f"Insight +{int(outcome['insight_xp'])}")
        if outcome.get("items"):
            lines.append(WORLD.item_names({str(k): int(v) for k, v in dict(outcome["items"]).items()}))
        if outcome.get("effect"):
            lines.append(f"Effect: {dict(outcome['effect']).get('name','Special effect')}")
        if outcome.get("karma_delta"):
            lines.append(f"Karma {int(outcome['karma_delta']):+d} → {int(outcome.get('karma_score',0)):+d}")
        if outcome.get("fate_delta"):
            lines.append(f"Fate {int(outcome['fate_delta']):+d} → {int(outcome.get('fate',0))}/9")
        return lines

    async def run_action(self, interaction: discord.Interaction, action: str) -> None:
        if not await self._owned(interaction):
            return
        await interaction.response.defer(ephemeral=False)
        wt = await current_world_time()
        operation = "exploration.event.leave" if action == "leave" else "exploration.event.act"
        try:
            envelope = await ENGINE.authoritative_action(
                operation, interaction.user.id,
                {"event_id": str(self.event.get("event_id") or ""), "action": action},
                action_id=f"discord:{interaction.id}:{operation}:{action}",
            )
        except GameEngineError as exc:
            await interaction.followup.send(f"That event action could not proceed: {exc}", ephemeral=False)
            return
        result = dict(envelope.get("result") or {})
        self.event = dict(result.get("event") or self.event)
        self._sync_buttons()
        try:
            await interaction.edit_original_response(embed=self.embed(), view=self)
        except discord.HTTPException:
            pass
        lines = [f"**{str(result.get('label') or action.replace('_',' ').title())} — {'SUCCESS' if result.get('success') else 'FAILURE'}**"]
        roll = dict(result.get("roll") or {})
        if roll:
            lines.append(roll_line(SimpleNamespace(**roll)))
        outcome_lines = self._outcome_lines(dict(result.get("outcome") or {}))
        if outcome_lines:
            lines.append("**Outcome:** " + " • ".join(outcome_lines))
        if result.get("resolved"):
            lines.append("The encounter is resolved. Normal exploration is available again.")
        await interaction.followup.send("\n".join(lines), ephemeral=False)

    @discord.ui.button(label="Observe", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="exploration_event:observe", row=0)
    async def observe(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "observe")

    @discord.ui.button(label="Approach", emoji="🚶", style=discord.ButtonStyle.primary, custom_id="exploration_event:approach", row=0)
    async def approach(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "approach")

    @discord.ui.button(label="Help", emoji="🤲", style=discord.ButtonStyle.success, custom_id="exploration_event:help", row=0)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "help")

    @discord.ui.button(label="Rob Them", emoji="🗡️", style=discord.ButtonStyle.danger, custom_id="exploration_event:rob", row=0)
    async def rob(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "rob")

    @discord.ui.button(label="Leave", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="exploration_event:leave", row=1)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "leave")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="exploration_event:refresh", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._owned(interaction):
            return
        try:
            status = await ENGINE.action(
                "exploration.event.status", interaction.user.id,
                {"event_id": str(self.event.get("event_id") or "")},
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Could not refresh the event: {exc}", ephemeral=False)
            return
        self.event = dict(status or self.event)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


@registered_root_command(name="explore", description="Explore your current location for events and discoveries", guild=GUILD)
@serialized_user_action
async def explore(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt_discovery = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "exploration.explore", interaction.user.id,
            {
                
                "unexpected_event_chance_percent": SETTINGS.unexpected_event_chance_percent,
                "event_key": f"discord:{interaction.id}:exploration:event",
            },
            action_id=f"discord:{interaction.id}:exploration.explore",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Exploration could not proceed: {_explain_engine_error(exc)}", ephemeral=False)
        return
    outcome = dict(envelope.get("result") or {})
    if str(outcome.get("kind") or "") == "event_active":
        event = dict(outcome.get("event") or {})
        expedition_thread = await ensure_expedition_thread(interaction, c)
        view = ExplorationEventView(interaction.user.id, event)
        if expedition_thread is not None:
            await EXPLORATION.enter_expedition(
                interaction.user.id,
                physical_location=str(c.get("location") or "Unknown"),
                channel_id=expedition_thread.id,
                guild_id=interaction.guild_id,
            )
            try:
                await expedition_thread.send(
                    content=f"{interaction.user.mention}, your previous exploration is still interrupted by this encounter.",
                    embed=view.embed(),
                    view=view,
                )
                await interaction.followup.send(
                    f"🌌 You are still dealing with **{event.get('title','an unexpected event')}**. Controls reopened in {expedition_thread.mention}.",
                    ephemeral=False,
                )
            except discord.HTTPException:
                await interaction.followup.send(embed=view.embed(), view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=view.embed(), view=view, ephemeral=False)
        return
    encounter = str(outcome.get("encounter") or "The region is strangely quiet.")
    cultivation = int(outcome.get("cultivation_awarded", 0))
    stones = int(outcome.get("spirit_stones", 0))
    items = {str(k): int(v) for k, v in dict(outcome.get("items") or {}).items()}

    expedition_thread = await ensure_expedition_thread(interaction, c)
    if expedition_thread is not None:
        await EXPLORATION.enter_expedition(
            interaction.user.id,
            physical_location=str(c.get("location") or "Unknown"),
            channel_id=expedition_thread.id,
            guild_id=interaction.guild_id,
        )
    else:
        await EXPLORATION.reset_to_world(interaction.user.id, guild_id=interaction.guild_id)
    history_channel_id = expedition_thread.id if expedition_thread is not None else (interaction.channel_id or 0)
    if expedition_thread is not None:
        try:
            await expedition_thread.send(f"\n📍 **Exploration — {c['location']}**")
        except discord.HTTPException:
            pass

    shared_claims = [str(row.get("title") or "world event") for row in list(outcome.get("shared_claims") or [])]
    surprise = dict(outcome.get("surprise") or {})
    surprise_text = ""
    event_thread: discord.Thread | None = None
    personal_event_view: ExplorationEventView | None = None
    if surprise:
        kind = str(surprise.get("kind") or "")
        surprise_text = f"\n\n🌌 **UNEXPECTED EVENT — {surprise.get('title','Unknown Event')}**\n{surprise.get('description','')}"
        if kind == "personal":
            personal_event_view = ExplorationEventView(interaction.user.id, surprise)
            surprise_text += "\n**Your exploration is paused here.** Resolve the encounter or choose **Leave** before exploring, hunting, or travelling again."
        elif kind == "world_event":
            if not bool(surprise.get("activated")):
                surprise_text = (
                    f"\n\n🌌 **EVENT ECHO — {surprise.get('title','World Event')}**\n"
                    f"This event is already active at **{c['location']}**; its consequences do not stack."
                )
            else:
                impacts=[str(x) for x in list(surprise.get("impacts") or [])]
                consequence=str(surprise.get("consequence_text") or "").strip()
                if consequence:
                    surprise_text += f"\n**Persistent consequence:** {consequence}"
                if impacts:
                    surprise_text += f"\n**Systems changed:** {'; '.join(impacts)}."
                surprise_text += f"\nThis is now a **server-wide event** for about {int(surprise.get('duration_hours') or 2)}h. Use **/world → Almanac → Worldevents**."
                event_thread = await spawn_event_thread(
                    interaction,title=str(surprise.get("title") or "World Event"),event_type="random_event",
                    event_key=str(surprise.get("event_key") or ""),expires_at=float(surprise.get("expires_at") or time.time()+7200),
                    announcement=(
                        f"⚡ **SERVER-WIDE EVENT — {surprise.get('title','World Event')}**\n"
                        f"Category: **{surprise.get('category','Phenomenon')}** • Severity **{int(surprise.get('severity',1))}/10**\n"
                        f"📍 **{c['location']}**\n{surprise.get('description','')}"
                        + (f"\n\n**Persistent consequence:** {consequence}" if consequence else "")
                        + f"\n\nActive for about **{int(surprise.get('duration_hours') or 2)}h**. Use the thread below for the live scene."
                    ),
                )
        elif kind == "secret_realm":
            if not bool(surprise.get("activated")):
                surprise_text = (
                    f"\n\n🌀 **SPATIAL ECHO — {surprise.get('realm_name') or surprise.get('title','Secret Realm')}**\n"
                    f"That entrance is already open at **{surprise.get('location') or c['location']}**. Use **/world → Almanac → Worldevents**."
                )
            else:
                await DB.record_world_history_event(
                    event_type="discovery", title=f"Secret realm opened: {surprise.get('realm_name') or surprise.get('title','Secret Realm')}",
                    summary=f"The entrance to {surprise.get('realm_name') or surprise.get('title','Secret Realm')} opened at {surprise.get('location')}. {surprise.get('realm_description','')}",
                    significance=76, visibility="public", location=str(surprise.get("location") or c["location"]),
                    actor_type="world", actor_key=str(surprise.get("secret_realm_id") or ""), actor_name=str(surprise.get("realm_name") or "Secret Realm"),
                    target_type="secret_realm", target_key=str(surprise.get("secret_realm_id") or ""), target_name=str(surprise.get("realm_name") or "Secret Realm"),
                    tags=("discovery","secret realm","opening"), game_minute=wt_discovery.total_minutes,
                    metadata={"event_key": surprise.get("event_key"), "open_hours": int(surprise.get("open_hours") or 8)},
                    source_key=f"secret_realm_open:{surprise.get('event_key')}",
                )
                surprise_text += (
                    f"\n🌀 **Secret Realm Opened: {surprise.get('realm_name') or surprise.get('title','Secret Realm')}**\n"
                    f"{surprise.get('realm_description','')}\nThe entrance remains unstable for about **{int(surprise.get('open_hours') or 8)}h**. "
                    "Use **/realm → Secret Realms → Enter**."
                )
                event_thread = await spawn_event_thread(
                    interaction,title=str(surprise.get("realm_name") or surprise.get("title") or "Secret Realm"),event_type="secret_realm",
                    event_key=str(surprise.get("event_key") or ""),expires_at=float(surprise.get("expires_at") or time.time()+28800),
                    announcement=(
                        f"🌀 **{str(surprise.get('category') or 'SECRET REALM').upper()} — {surprise.get('realm_name') or surprise.get('title','Secret Realm')}**\n"
                        f"📍 Entrance: **{surprise.get('location') or c['location']}**\n{surprise.get('description','')}\n{surprise.get('realm_description','')}\n\n"
                        f"The entrance remains open for about **{int(surprise.get('open_hours') or 8)}h**. "
                        "Travel there, then use **/realm → Secret Realms → Enter**. The thread below is the shared expedition scene."
                    ),
                )
        if event_thread is not None:
            surprise_text += f"\n💬 **Live event thread:** {event_thread.mention}"

    discovery_text = ""
    here_data = WORLD.locations.get(str(c.get("location") or "")) or {}
    if here_data.get("gate"):
        try:
            mood = int(dict(await SIM.civilization_status(str(here_data.get("outside_location") or "")) or {}).get("prosperity") or 50)
        except Exception:
            mood = 50
        discovery_text += ("\n🚪 The gate queue is long today and the carts are full - the city is thriving." if mood >= 70 else
                           ("\n🚪 The gate is quiet and the guards bored - the city is struggling." if mood <= 30 else "\n🚪 The usual traffic through the gate; the city is getting by."))
    discovered_shop = outcome.get("discovered_shop")
    if isinstance(discovered_shop, dict):
        discovery_text += (
            f"\n\n🏪 **You find {discovered_shop.get('name')}** — a tier {int(discovered_shop.get('tier') or 1)} {str(discovered_shop.get('kind') or 'shop').replace('_', ' ')} kept by {discovered_shop.get('keeper')}.\n"
            f"{discovered_shop.get('description') or ''}\nEnter it with **/travel**, then **/economy → City Shops → Browse**."
        )
    site_kind = str(outcome.get("site_kind") or "")
    if site_kind == "ruin" and dict(outcome.get("items") or {}):
        discovery_text += "\n🪨 The ruin gives up twice what open ground would."
    elif site_kind == "shrine" and int(outcome.get("insight_xp") or 0):
        discovery_text += f"\n🔔 The bell and the stillness: +{int(outcome.get('insight_xp') or 0)} insight."
    discovered_location = str(outcome.get("discovered_location") or "")
    discovered_site = outcome.get("discovered_site")
    if isinstance(discovered_site, dict):
        leg = list(discovered_site.get("leg") or [])
        discovery_text += (
            f"\n\n🛤️ **You find {discovered_site.get('name')}** — {ROAD_SITE_LABEL.get(str(discovered_site.get('kind')), 'a place')} on the {' – '.join(leg)} road.\n"
            f"{discovered_site.get('description') or ''}\nReach it with **/travel** from either end of the road."
        )
    elif discovered_location:
        discovery_text = (
            f"\n\n🧭 **New route discovered — {discovered_location}**\n"
            f"{WORLD.locations.get(discovered_location, {}).get('description', 'A newly charted route opens before you.')}"
        )
    if discovered_location:
        discovered_sects = _sect_recruitment_at_location(discovered_location)
        if discovered_sects:
            try:
                await ENGINE.action("sect.discover", interaction.user.id, {
                    "sects": discovered_sects,
                    "discovery_kind": "exploration",
                    "source_key": discovered_location,
                    "game_minute": wt_discovery.total_minutes,
                })
            except GameEngineError:
                log.exception("Sect discovery could not be recorded for %s", discovered_location)
            discovery_text += f"\n🏯 **Sect route discovered:** {', '.join(discovered_sects)}. Open **Sect → Recruitment** to learn about the gate."
            try:
                progressed = await record_quest_progress(interaction.user.id, "sect_discovery", amount=1, game_minute=wt_discovery.total_minutes)
                await announce_quest_progress(interaction, progressed)
            except Exception:
                log.exception("Quest progress update failed after sect discovery")
        try:
            await DB.record_world_history_event(
                event_type="discovery", title=f"{c.get('name','A cultivator')} discovered {discovered_location}",
                summary=(
                    f"While exploring from {c.get('location','Unknown')}, {c.get('name','the cultivator')} charted a route to "
                    f"{discovered_location}." + (f" Sect access revealed: {', '.join(discovered_sects)}." if discovered_sects else "")
                ),
                significance=58 if discovered_sects else 48, visibility="participant", location=str(discovered_location),
                actor_type="player", actor_key=str(interaction.user.id), actor_name=str(c.get('name') or ''),
                target_type="location", target_key=str(discovered_location), target_name=str(discovered_location), related_user_id=interaction.user.id,
                tags=("discovery","route",*tuple(discovered_sects or [])), game_minute=wt_discovery.total_minutes,
                metadata={"origin": str(c.get('location') or ''), "sects": list(discovered_sects or [])},
                source_key=f"location_discovery:{interaction.user.id}:{discovered_location}",
            )
        except Exception:
            log.exception("Could not persist structured world-history discovery")

    try:
        progressed = await record_quest_progress(interaction.user.id, "explore", amount=1, target=str(c.get("location") or ""), game_minute=wt_discovery.total_minutes)
        await announce_quest_progress(interaction, progressed)
    except Exception:
        log.exception("Quest progress update failed after exploration")
    await DB.add_history(history_channel_id, user_id=interaction.user.id, speaker=c["name"], content=f"Explores {c['location']}")
    history = await DB.get_history(history_channel_id, 20)
    exploration_context = await NARRATOR_CONTEXT.build(c, scene_type="exploration", query_text=encounter)
    # v0.31.0: the encounter is the engine's; the pool describes it at once.
    # The model is asked only on the GM flag (here) or the player's button (below).
    narrate_with_model = await _routine_narration_by_default()

    def _ask_model():
        return NARRATOR.narrate_exploration(c, encounter, history, scene_context=exploration_context.text, upgrade=True)

    try:
        if narrate_with_model:
            narration = await NARRATOR_QUEUE.run("exploration", _ask_model())
        else:
            narration = await NARRATOR.narrate_exploration(c, encounter, history, scene_context=exploration_context.text)
    except Exception:
        log.exception("Exploration narration failed")
        narration = encounter
    try:
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="exploration", salience=38, location=str(c.get("location") or ""),
            source="exploration", game_minute=exploration_context.game_minute,
            summary=(
                f"While exploring {c.get('location','Unknown')}, {c.get('name','the player')} encountered: "
                f"{encounter[:360]} | Observed scene: {narration[:500]}"
            ),
        )
    except Exception:
        log.exception("Could not persist exploration RAG memory")
    reward_text = f"\n\n**Exploration gains:** +{cultivation} cultivation, +{stones} spirit stones" + (f", {WORLD.item_names(items)}" if items else "")
    if shared_claims:
        reward_text += "\n**Active event participation:** " + ", ".join(shared_claims)
    await DB.add_history(history_channel_id, user_id=None, speaker="World", content=narration + surprise_text + discovery_text)
    full_exploration = narration + reward_text + surprise_text + discovery_text

    async def _deliver_prose(text: str) -> None:
        await DB.add_history(history_channel_id, user_id=None, speaker="World", content=text)
        if expedition_thread is not None:
            await send_long_to_thread(expedition_thread, f"📜 {text}")
        else:
            await interaction.followup.send(f"📜 {text}", ephemeral=False)

    narrate_view = None if narrate_with_model else NarrateItView(
        owner_id=interaction.user.id, narrate=_ask_model, deliver=_deliver_prose, label="exploration",
    )
    if expedition_thread is not None:
        try:
            await send_long_to_thread(expedition_thread, full_exploration)
            if discovered_location:
                await send_location_discovery_image(
                    interaction, discovered_location, thread=expedition_thread
                )
            if personal_event_view is not None:
                await expedition_thread.send(embed=personal_event_view.embed(), view=personal_event_view)
            await interaction.followup.send(
                f"🧭 Exploration recorded in your private expedition journal: {expedition_thread.mention}",
                ephemeral=False, view=narrate_view,
            )
        except discord.HTTPException:
            log.exception("Could not write exploration result to private expedition thread")
            await reply_long(interaction, full_exploration, ephemeral=False)
            if personal_event_view is not None:
                await interaction.followup.send(embed=personal_event_view.embed(), view=personal_event_view, ephemeral=False)
            if narrate_view is not None:
                await interaction.followup.send("The account above is the world's plain record.", view=narrate_view, ephemeral=False)
    else:
        await reply_long(
            interaction, full_exploration + "\n\n⚠️ No private expedition thread is configured. Ask an admin to bind the existing channel in the admin dashboard, then run **/admin → Server → Basechannels**.",
            ephemeral=False,
        )
        if discovered_location:
            await send_location_discovery_image(interaction, discovered_location)
        if personal_event_view is not None:
            await interaction.followup.send(embed=personal_event_view.embed(), view=personal_event_view, ephemeral=False)
        if narrate_view is not None:
            await interaction.followup.send("The account above is the world's plain record.", view=narrate_view, ephemeral=False)


@registered_root_command(name="hunt", description="Hunt a spirit beast for materials", guild=GUILD)
@serialized_user_action
async def hunt(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "exploration.hunt", interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:exploration.hunt",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"The hunt could not proceed: {_explain_engine_error(exc)}", ephemeral=False)
        return
    outcome = dict(envelope.get("result") or {})
    beast = dict(outcome.get("beast") or {})
    roll = SimpleNamespace(**dict(outcome.get("roll") or {}))
    success = bool(outcome.get("success"))
    cultivation_awarded = int(outcome.get("cultivation_awarded", 0))

    hunt_context = await NARRATOR_CONTEXT.build(
        c, scene_type="spirit beast hunt", query_text=f"hunt {beast.get('name','spirit beast')} {beast.get('element','')}"
    )
    narrate_with_model = await _routine_narration_by_default()

    def _ask_model():
        return NARRATOR.narrate_hunt_result(c, beast, roll_line(roll), success, scene_context=hunt_context.text, upgrade=True)

    try:
        if narrate_with_model:
            narration = await NARRATOR_QUEUE.run("hunt", _ask_model())
        else:
            narration = await NARRATOR.narrate_hunt_result(c, beast, roll_line(roll), success, scene_context=hunt_context.text)
    except Exception:
        log.exception("Hunt narration failed")
        narration = f"You encounter a {beast.get('name','spirit beast')}."
    try:
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="hunt", salience=48 if success else 32,
            location=str(c.get("location") or ""), source="hunt", game_minute=hunt_context.game_minute,
            summary=(
                f"{c.get('name','The player')} hunted {beast.get('name','a spirit beast')} at {await character_location_display(c)}. "
                f"Outcome: {'success' if success else 'the beast escaped'}. Observed: {narration[:520]}"
            ),
        )
    except Exception:
        log.exception("Could not persist hunt RAG memory")

    text = f"**Hunt: {beast.get('name','Spirit Beast')}**\n{roll_line(roll)}\n\n{narration}"
    if str(outcome.get("site_kind") or "") == "hunting_ground":
        text += f"\n🏹 A hunting ground: **+{int(outcome.get('site_bonus') or 0)}** to the roll, and the spoils half again."
    if success:
        loot = {str(k): int(v) for k, v in dict(beast.get("loot") or {}).items()}
        text += (
            f"\n\n**Loot:** +{cultivation_awarded} cultivation, +{int(beast.get('stones',0))} spirit stones, "
            f"{WORLD.item_names(loot)}"
        )
        bonded = dict(outcome.get("bonded_beast") or {})
        wild = dict(outcome.get("wild_encounter") or {})
        if bonded:
            text += (
                f"\n\n🐉 **Beast bond:** your overwhelming Beast-Binder success earns the respect of **{bonded.get('name', beast.get('name','the beast'))}**. "
                f"An **equality contract** is formed at loyalty **{int(bonded.get('loyalty',25))}**"
                + (" and it becomes your active companion." if bonded.get("active") else ".")
            )
        elif wild:
            text += (
                f"\n\n🪢 **Subdued beast opportunity:** **{beast.get('name','The beast')}** survives the clash and watches you warily. "
                f"Encounter `#{int(wild.get('encounter_id',0))}` can be approached through **/beast → Tame** before it leaves."
            )
    else:
        text += "\n\nThe beast escapes. No permanent injury or item loss is applied."
    # A hunt won is a fight come out of standing (v1.0.13). `combat_win` had
    # exactly one reporter, `/battle`'s finalize - while the one objective in
    # the game that asks for it, the beginner path's `beginner_road`, labels
    # itself with this very leaf. So the stage named the command that could
    # not advance it and a player hunted all day at 0/1.
    #
    # The report is written after the engine has already decided the hunt
    # landed (rc.28) and before the command answers, while the announcement
    # waits for the reply (v1.0.5). It is untargeted like every `combat_win`,
    # and carries the quarry's name for the day there is a roster.
    progressed = []
    if success:
        progressed = await record_quest_progress(
            interaction.user.id, "combat_win", amount=1,
            target=str(beast.get("name") or ""), game_minute=wt.total_minutes)
    await reply_long(interaction, text)
    await announce_quest_progress(interaction, progressed)
    if not narrate_with_model:
        async def _deliver_prose(prose: str) -> None:
            await interaction.followup.send(f"📜 {prose}", ephemeral=False)
        await interaction.followup.send(
            "The clash above is the world's plain record.",
            view=NarrateItView(owner_id=interaction.user.id, narrate=_ask_model, deliver=_deliver_prose, label="hunt"),
            ephemeral=False,
        )


async def recipe_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """The methods this cultivator actually knows (v1.0.5).

    It was `DB.search_catalog("recipe", current, 25)` - the whole 33-recipe
    catalogue, capped at Discord's 25 - while `craft.resolve` refuses any method
    the player has not learned. So a fresh character who knows about three was
    offered twenty-five, and the hub renders this same callback as a drop-down
    (`_autocomplete_provider` reuses it rather than duplicating game lookups),
    which is how "why is crafting a drop-down menu" came to mean "a menu of
    things I cannot make".

    rc.46's rule, one surface over: a surface must not offer what the engine
    will refuse. The journal stopped listing quests no roster would hand over
    for exactly this reason.

    Knowing a method is not the same as being equal to it - a rank too low is a
    second, different refusal - so those stay on the list. `/craft → Profession
    → Profession Status` is where the ranks and the materials are spelled out.
    """
    try:
        known = await DB.get_known_recipes(interaction.user.id)
    except Exception:
        log.exception("Known recipes could not be read for the craft picker")
        return []
    needle = current.casefold().strip()
    names = sorted({str(row.get("recipe") or "") for row in known if row.get("recipe")})
    return [
        app_commands.Choice(name=name[:100], value=name[:100])
        for name in names
        if not needle or needle in name.casefold()
    ][:25]


def where_it_is_sold(item_id: str, location: str) -> str:
    """Where a material is sold, measured from where the cultivator stands (v1.0.15).

    The craft refusal used to say "buy them at a hall of the trade" whatever the
    material, and an Apprentice alchemist standing in the Jadewood Apothecary was
    told that about a fruit no hall in the Mortal World had ever sold. The shelves
    are content, so this reads them: the halls of this city first, then the rest
    of this world, and when this world sells it nowhere it says so and names the
    worlds that do, rather than sending somebody to a counter that has none.

    A private place (a household, an inner world) belongs to no world, so there
    it names the worlds instead of guessing one - the rc.52 reason
    `world_of_location` answers None rather than the Mortal World.
    """
    halls = [
        shop for shop in WORLD.shops.values()
        if any(str(line.get("item_id")) == item_id for line in shop.get("sells") or [])
    ]
    if not halls:
        rooms = sorted({
            str(realm.get("name") or key)
            for key, realm in WORLD.secret_realms.items()
            for room in realm.get("rooms") or []
            if item_id in (room.get("items") or {})
        })
        if rooms:
            return f"no hall sells it; it is found in {', '.join(rooms)}"
        return "no hall sells it"
    worlds = [name for name in REALM_HUBS if any(shop.get("world") == name for shop in halls)]
    world = world_of_location(location)
    if world is None:
        return f"sold in halls of the {', '.join(worlds)}"
    local = [shop for shop in halls if shop.get("world") == world]
    if not local:
        return f"no hall in the {world} sells it; halls in the {', '.join(worlds)} do"
    city = _city_of(location)
    here = sorted(str(shop.get("name")) for shop in local if shop.get("city") == city)
    if here:
        return f"sold here, at {', '.join(here)}"
    cities = sorted({str(shop.get("city")) for shop in local})
    more = f" and {len(cities) - 3} more" if len(cities) > 3 else ""
    return f"sold in the {world} at {', '.join(cities[:3])}{more}"


async def _where_to_find_what_is_short(user_id: int, cost: dict[str, Any], location: str) -> list[str]:
    """One line per material the cultivator is short of, or none if unreadable.

    The engine's refusal already names the shortfall; this only says where each
    one can be had. It never raises, because it runs inside the reply to a
    refused craft and a lookup that threw would cost the player the refusal
    itself - the v1.0.10 rule for any line drawn beside something that matters.
    """
    try:
        carried = await DB.get_inventory(user_id)
        return [
            f"• **{WORLD.item_name(item)}**: {where_it_is_sold(item, location)}"
            for item, qty in sorted(cost.items())
            if int(carried.get(item, 0)) < int(qty)
        ]
    except Exception:
        log.exception("Where-to-buy lines could not be drawn for a refused craft")
        return []


async def _run_crafting(interaction: discord.Interaction, recipe: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    r = await DB.get_recipe_definition(recipe)
    if not r:
        await interaction.response.send_message(
            "Unknown recipe. Start typing a recipe name and choose it from autocomplete.",
            ephemeral=False,
        )
        return
    # The recipe's profession is printed on every result line below and decides
    # the alchemy-only batch wording. It was removed with the `required_profession`
    # gate in the same edit, which left three uses of a name nothing defined.
    profession = str(r["profession"])
    try:
        envelope = await ENGINE.authoritative_action(
            "craft.resolve",
            interaction.user.id,
            {"recipe": recipe},
            action_id=f"discord:{interaction.id}:craft.resolve",
        )
    except GameEngineError as exc:
        # The engine names exactly which materials are short and by how much
        # (v1.0.1); it used to refuse with the bare words "missing materials"
        # and this handler then replaced even those with a sentence of its own.
        # Two layers each discarding the one fact the player needed.
        message = str(exc)
        if "missing materials" in message.casefold():
            where = await _where_to_find_what_is_short(
                interaction.user.id, dict(r.get("cost") or {}), str(c.get("location") or ""))
            message = "\n".join([
                f"🧰 {message}",
                *where,
                "Buy them at a hall (**/economy → City Shops → Here**) or gather them "
                "(**/craft → Alchemy → Forage**; beast cores come from **/world → Act → Hunt**). "
                "**/craft → Profession → Profession Status** lists every method you know and what each one needs.",
            ])
        await interaction.response.send_message(message, ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    result = SimpleNamespace(**resolved)
    # The engine's roll, whole (die1, die2, modifier, tn, total, degree,
    # probability) - the same read every other caller of `roll_line` makes.
    # Passing `result` here raised `AttributeError: no attribute 'die1'` on
    # every craft that got past the materials check, *after* the engine had
    # committed: the materials were spent, the output granted, and the player
    # was shown a wiring failure and told nothing had happened (v1.0.3).
    roll = SimpleNamespace(**dict(resolved.get("roll") or {}))
    facility_bonus = int(resolved.get("facility_bonus", 0))
    manor_facility_bonus = int(resolved.get("manor_facility_bonus", 0))
    inherited_family_bonus = int(resolved.get("family_bonus", 0))
    craft_echo = int(resolved.get("craft_echo", 0))
    profession_bonus = int(resolved.get("profession_bonus", 0))
    output = {str(k): int(v) for k, v in dict(resolved.get("output") or {}).items()}
    success = bool(resolved.get("success"))
    outcome = f"Created **{WORLD.item_names(output)}**." if success else "The refinement fails and the ingredients are consumed."
    profession_row = dict(resolved.get("profession_progress") or {})
    level = int(profession_row.get("level", 0))
    mastery_line = (
        f"\n🛠️ Profession: **{profession_rank(level)}** (Level {level}) • "
        f"XP {profession_row.get('xp',0)}/{profession_xp_needed(level)}"
    )
    quality_label = str(resolved.get("quality_label") or "")
    quality_line = ""
    if quality_label:
        prefix = "⚗️ Batch quality" if profession == "Alchemy" else "✨ Craft quality"
        quality_line = f"\n{prefix}: **{quality_label}**"
        mult = int(resolved.get("output_multiplier", 1))
        if profession == "Alchemy" and success and mult > 1:
            quality_line += f" • output ×{mult}"

    # Built outside the f-string: a backslash inside an f-string expression is
    # only legal from Python 3.12 (PEP 701), and `python -m compileall app` is
    # one of this repo's checks. Same output, one line per bonus that applies.
    bonus_lines = [
        f"{label}: **+{value}**\n"
        for label, value in (
            ("Player-property facility bonus", facility_bonus),
            ("Sect-manor facility bonus", manor_facility_bonus),
            (f"Birth-family {resolved.get('family_trade') or 'trade'} tradition", inherited_family_bonus),
            (f"Past-life {profession} memory ({resolved.get('craft_echo_life') or 'a life before'})", craft_echo),
            ("Profession mastery bonus", profession_bonus),
        )
        if value
    ]
    # A rank reached is a hall opening its roll (v1.0.0-rc.45). Said here
    # because the quest is handed over silently otherwise, and a crafter who is
    # not told has no reason to look in /quests.
    exam_line = ""
    if resolved.get("exam_offered"):
        exam_line = (f"\n🎓 The {profession} halls will examine you at this rank: "
                     f"**/craft → Profession → Exam**, at a hall of the trade. See **/quests**.")
    # Only a craft that produced something counts. A failed refinement spends
    # the ingredients and is a real part of the trade, but "craft a Recovery
    # Pill" is not satisfied by not crafting one.
    #
    # Recorded *before* the reply and told after (v1.0.5). The telling must come
    # after: this command never defers, so the interaction has exactly one
    # `response`, and a reporter with something to say would spend it on the
    # quest line. The record has no such constraint, and nesting the two inside
    # one statement is what made it inherit the announcement's position - so
    # when this reply raised in v1.0.3, the craft had committed and the quest
    # never advanced.
    progressed: list[dict] = []
    if success:
        wt_craft = await current_world_time()
        progressed = await record_quest_progress(
            interaction.user.id, "craft", amount=1, target=recipe,
            game_minute=wt_craft.total_minutes)
    await interaction.response.send_message(
        f"**{profession}: {recipe}**\n{roll_line(roll)}\n"
        + "".join(bonus_lines)
        + f"{outcome}{quality_line}{mastery_line}{exam_line}"
    )
    await announce_quest_progress(interaction, progressed)


@registered_root_command(name="craft", description="Practice alchemy, forging, formation or talisman inscription from a method you know", guild=GUILD)
@app_commands.autocomplete(recipe=recipe_autocomplete)
@serialized_user_action
async def craft(interaction: discord.Interaction, recipe: str) -> None:
    await _run_crafting(interaction, recipe)


async def method_slip_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The slips actually in the bags, so a player is never offered one they lack."""
    try:
        held = await DB.get_inventory(interaction.user.id)
    except Exception:
        return []
    needle = current.casefold().strip()
    out: list[app_commands.Choice[str]] = []
    for item_id, quantity in sorted(held.items()):
        if int(quantity) <= 0:
            continue
        definition = WORLD.items.get(item_id) or {}
        if not definition.get("teaches_recipe"):
            continue
        label = str(definition.get("name") or item_id)
        if needle and needle not in label.casefold() and needle not in item_id.casefold():
            continue
        out.append(app_commands.Choice(name=label[:100], value=item_id))
        if len(out) >= 25:
            break
    return out


@registered_root_command(name="learn", description="Read a method slip - the method is yours for good and the slip is spent", guild=GUILD)
@app_commands.autocomplete(slip=method_slip_autocomplete)
@serialized_user_action
async def learn(interaction: discord.Interaction, slip: str) -> None:
    """The other half of the learning step (v1.0.0-rc.20).

    A household teaches its own craft and nothing else, so everything else is a
    jade slip bought from the trade that uses it. The method is permanent and
    the slip is not: one impression, one reading. Re-reading a method already
    carried spends nothing, so nobody burns a spare for no gain.
    """
    if not await require_character(interaction):
        return
    await interaction.response.defer(ephemeral=False)
    try:
        envelope = await ENGINE.authoritative_action(
            "recipe.learn",
            interaction.user.id,
            {"item_id": slip},
            action_id=f"discord:{interaction.id}:recipe.learn",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    recipe = str(result.get("recipe") or slip)
    profession = str(result.get("profession") or "")
    min_level = int(result.get("min_level") or 0)
    level = int(result.get("level") or 0)
    if result.get("already_known"):
        head = (f"📜 You already carry the method for **{recipe}**, "
                "so the slip stays in your bags unread.")
    else:
        head = (f"📜 You read the slip through. The method for **{recipe}** is yours, "
                "and the jade is blank.")
    if result.get("ready"):
        tail = f"\n{profession} {level} — your hands are equal to it. **/craft** it when you have the materials."
    else:
        tail = f"\n{profession} {level}, and the work asks for **{min_level}**. The method keeps; practise until you reach it."
    await interaction.followup.send(head + tail, ephemeral=False)


class NarrateItView(discord.ui.View):
    """One button under a procedural result: ask the model for prose (v0.31.0).

    An exploration opening or a hunt result is decided by the engine and
    read from the procedural pool. The model is asked only when the player
    presses this - an explicit ask, metered on its own door of the per-player
    budget - or when the GM's `ai_routine_narration` flag is on, in which case
    the handler narrates up front and this view is never shown.
    """

    def __init__(
        self, *, owner_id: int, narrate: Callable[[], Awaitable[str]],
        deliver: Callable[[str], Awaitable[None]], label: str = "exploration",
    ) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.narrate = narrate
        self.deliver = deliver
        self.label = label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("That scene belongs to the cultivator who played it.", ephemeral=False, delete_after=15)
            return False
        return True

    @discord.ui.button(label="Narrate it", style=discord.ButtonStyle.secondary, emoji="📜")
    async def narrate_it(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        refusal = budget_refusal_line(interaction.user.id, "narrate_it")
        if refusal:
            await interaction.response.send_message(refusal, ephemeral=False, delete_after=20)
            return
        button.disabled = True
        button.label = "Narrated"
        self.stop()
        await interaction.response.edit_message(view=self)
        try:
            text = await NARRATOR_QUEUE.run(f"narrate_it:{self.label}", self.narrate())
        except Exception:
            log.exception("Narrate-it narration failed (%s)", self.label)
            text = "The spiritual currents are unstable; the narrator could not be reached. The procedural account above stands."
        try:
            await self.deliver(text)
        except discord.HTTPException:
            log.exception("Could not deliver Narrate-it prose (%s)", self.label)


async def _routine_narration_by_default() -> bool:
    """The GM scene flag: narrate explore and hunt with the model unasked."""
    try:
        return bool((await DB.get_automation_settings()).get("ai_routine_narration"))
    except Exception:
        log.exception("Could not read automation settings; treating routine narration as procedural")
        return False


alchemy_group = app_commands.Group(name="alchemy", description="Refine pills, gather medicinal herbs and manage pill toxicity")


@registered_group_command(alchemy_group, name="status", description="Inspect Alchemy mastery, recent refinements and medicinal toxicity")
async def alchemy_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    state = await DB.get_alchemy_state(interaction.user.id)
    # The decayed figure is the engine's (v0.30.0): effects.current settles
    # natural decay against the canonical clock without writing anything.
    settled = dict(await ENGINE.action("effects.current", interaction.user.id, {}) or {})
    pill_toxicity = int(settled.get("pill_toxicity", state.get("pill_toxicity", 0)))
    profession = await DB.get_profession_progress(interaction.user.id, "Alchemy") or {}
    batches = await DB.get_alchemy_batches(interaction.user.id, limit=5)
    band, band_text = toxicity_band(pill_toxicity)
    member_manor = await DB.get_member_sect_manor(interaction.user.id)
    birth_family = await DB.get_birth_family(interaction.user.id)
    inherited_family_bonus = family_profession_bonus(birth_family, "Alchemy", WORLD.data.get("birth_family_sendoff"))
    manor_bonus = 0
    if member_manor and str(member_manor.get("base_location", "")) == str(c.get("location", "")):
        manor_bonus = manor_craft_bonus(member_manor, "Alchemy")
    abode = await DB.get_abode_by_location(str(c.get("location", "")))
    abode_bonus = 0
    if abode and await DB.can_access_abode(int(abode['user_id']), interaction.user.id):
        abode_bonus = int(abode.get("alchemy_level", 0)) * 2
    lines = [
        f"⚗️ **Alchemy — {c['name']}**",
        f"Mastery: **{profession_rank(int(profession.get('level', 0)))}** • Level **{int(profession.get('level', 0))}** • XP **{int(profession.get('xp', 0))}**",
        f"Refinements: **{int(state.get('successful_refinements',0))}/{int(state.get('total_refinements',0))}** successful • Flawless **{int(state.get('flawless_refinements',0))}**",
        f"Pill toxicity: **{pill_toxicity}/100 — {band}**\n{band_text}",
        f"Local facilities: player property **+{abode_bonus}** • sect manor **+{manor_bonus}**",
        f"Birth-family tradition: **+{inherited_family_bonus} Alchemy**" if inherited_family_bonus else "Birth-family tradition: **none**",
    ]
    if batches:
        lines.append("\n**Recent batches**")
        for batch in batches:
            result_text = "success" if int(batch.get("success", 0)) else "failure"
            lines.append(
                f"`#{batch['batch_id']}` **{batch['recipe_name']}** • {str(batch['quality']).title()} • margin {int(batch['margin']):+d} • {result_text}"
            )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(alchemy_group, name="forage", description="Gather herbs and craft makings using the current region's simulated spirit resources")
@serialized_user_action
async def alchemy_forage(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    remaining = await DB.cooldown_remaining(interaction.user.id, "alchemy_forage")
    if remaining:
        await interaction.response.send_message(
            f"The nearby beds and seams need time to recover. Forage again in **{human_duration(remaining)}**.", ephemeral=False,
        )
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "forage.resolve",
            interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:forage.resolve",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Foraging could not resolve: {exc}", ephemeral=False)
        return
    resolved = dict(envelope.get("result") or {})
    result = SimpleNamespace(**resolved)
    # The engine's roll, whole (die1, die2, modifier, tn, total, degree,
    # probability): the flat d1/d2 beside it are not what `roll_line` reads,
    # and printing them here would be presentation deciding a degree (rc.35).
    roll = SimpleNamespace(**dict(resolved.get("roll") or {}))
    success = bool(resolved.get("success"))
    forage_progress = dict(resolved.get("profession_progress") or {})
    level = int(forage_progress.get("level", 0))
    forage_location = str(resolved.get("location") or c.get("location", ""))
    inherited_forage_bonus = int(resolved.get("family_bonus", 0))
    garden_bonus = int(resolved.get("garden_bonus", 0))
    craft_echo = int(resolved.get("craft_echo", 0))
    bonus_bits = (
        f"{(' • Household herb lore **+'+str(inherited_forage_bonus)+'**') if inherited_forage_bonus else ''}"
        f"{(' • Property herb garden **+'+str(garden_bonus)+'**') if garden_bonus else ''}"
        f"{(' • A past life’s hands **+'+str(craft_echo)+'**') if craft_echo else ''}"
    )
    if not success:
        await interaction.response.send_message(
            f"🌿 **Forage — {forage_location}**\n{roll_line(roll)}\n"
            f"Regional spirit resources: **{int(resolved.get('spirit_resources',0))}/100**.{bonus_bits} "
            "You find no usable harvest this time."
            f"\n🧺 Foraging: **{profession_rank(level)}** Lv.{level} "
            f"• XP {int(forage_progress.get('xp',0))}/{profession_xp_needed(level)}"
        )
        return
    awarded = {str(k): int(v) for k, v in dict(resolved.get("loot") or {}).items()}
    rare = str(resolved.get("rare_found") or "")
    rare_line = f"\n✨ Rare find: **{WORLD.item_name(rare)}**." if rare else ""
    # The makings of the other three crafts (v1.0.0-rc.21). They are already in
    # `Harvested`, but naming them is the point: a forager who does not know
    # that ink and paper come out of the hills has no reason to look.
    makings = {str(k): int(v) for k, v in dict(resolved.get("materials_found") or {}).items()}
    makings_line = f"\n📜 Craft makings: **{WORLD.item_names(makings)}**." if makings else ""
    # One report per distinct material that actually came out of the hills,
    # herbs and craft makings alike, so a targeted objective can name the thing
    # it wants. The failed-forage branch above returns before this and reports
    # nothing, which is the same rule crafting keeps.
    #
    # Recorded before the harvest is on screen and told after (v1.0.5), for the
    # reason `_run_crafting` gives: this command does not defer, so the one
    # `response` belongs to the result and the telling gets the followup - while
    # the record must not be able to be lost to a failure in drawing the reply.
    wt_forage = await current_world_time()
    progressed: list[dict] = []
    for material in sorted({**awarded, **makings}):
        progressed += await record_quest_progress(
            interaction.user.id, "gather", amount=1, target=str(material),
            game_minute=wt_forage.total_minutes)
    await interaction.response.send_message(
        f"🌿 **Forage — {forage_location}**\n{roll_line(roll)}\n"
        f"Regional spirit resources: **{int(resolved.get('spirit_resources',0))}/100**.{bonus_bits}\n"
        f"Harvested: **{WORLD.item_names(awarded)}**.{rare_line}{makings_line}\n"
        f"🧺 Foraging: **{profession_rank(level)}** Lv.{level} "
        f"• XP {int(forage_progress.get('xp',0))}/{profession_xp_needed(level)}"
    )
    await announce_quest_progress(interaction, progressed)


@registered_group_command(alchemy_group, name="purge", description="Slowly purge medicinal residue by spending Qi in controlled circulation")
@serialized_user_action
async def alchemy_purge(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    # Ack first, unconditionally: the purge is an authoritative mutation, and
    # an interaction token that expires before the first reply would have the
    # player click again on a cycle that already ran.
    await interaction.response.defer(ephemeral=False)
    # The whole cycle - cooldown, Qi, toxicity, the shared effect row - is one
    # engine transaction as of v0.23.0. The cooldown is still read here so the
    # refusal can name the wait in words; every other check the command used to
    # make read state the engine re-reads anyway, and passing them was never a
    # guarantee that all four writes would land.
    remaining = await DB.cooldown_remaining(interaction.user.id, "alchemy_purge")
    if remaining:
        await interaction.followup.send(
            f"Your meridians need time before another purge cycle: **{human_duration(remaining)}**.", ephemeral=False,
        )
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "alchemy.purge", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:alchemy.purge",
        )
    except GameEngineError as exc:
        await interaction.followup.send(alchemy_purge_refusal(str(exc)), ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = [
        f"🫧 You circulate **{int(result.get('qi_cost', 0))} Qi** through the meridians and purge "
        f"**{int(result.get('purged', 0))}** toxicity. "
        f"Pill toxicity is now **{int(result.get('pill_toxicity', 0))}/100**."
    ]
    if int(result.get("detox_power", 0)) > 0:
        lines.append(f"💊 Purging medicine carried you **+{int(result.get('detox_power', 0))} detox power**.")
    # The flame the Purging Phoenix Pill has always warned about (v1.0.0-rc.58).
    # It is only rolled above saturation, so a light purge prints nothing extra
    # and stays the action it has always been. Nothing is decided here - the
    # engine has already applied the condition if there is one.
    scorch = result.get("scorch_roll")
    if isinstance(scorch, dict):
        lines.append(f"🔥 Holding the medicinal flame: {roll_line(SimpleNamespace(**scorch))}")
        scorched = result.get("scorched")
        if isinstance(scorched, dict):
            lines.append(
                f"🩸 The flame scorches your meridians — **{scorched.get('name', 'Meridian Damage')}** "
                f"(severity {int(scorched.get('severity', 1))}). `/condition treat` with a **Jade Life Herb** mends it."
            )
        else:
            lines.append("🌿 The flame stays where you put it.")
    await interaction.followup.send("\n".join(lines), ephemeral=False)


realmhub_group = app_commands.Group(name="realmhub", description="Meet other cultivators in the central city of each realm world")
async def realmhub_world_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    needle = current.casefold().strip()
    worlds = [
        world for world, hub in REALM_HUBS.items()
        if int(c.get("realm_index", 0)) >= int(hub.get("min_realm_index", 0))
        and (not needle or needle in world.casefold() or needle in str(hub.get("display_name", "")).casefold())
    ]
    return [app_commands.Choice(name=f"{world} — {REALM_HUBS[world]['display_name']}"[:100], value=world) for world in worlds[:25]]


@registered_group_command(realmhub_group, name="status", description="Show realm capitals your cultivation can currently perceive")
async def realmhub_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    mappings = {str(r['world_name']): r for r in (await DB.get_realm_hub_channels(interaction.guild.id) if interaction.guild else [])}
    lines=["🏙️ **Known Realm Capitals**"]
    for world,hub in REALM_HUBS.items():
        if int(c.get('realm_index',0)) < int(hub['min_realm_index']):
            continue
        row=mappings.get(world); channel=f"<#{int(row['channel_id'])}>" if row else "*Discord channel not provisioned yet*"
        lines.append(f"• **{world} — {hub['display_name']}** • {channel}")
    lines.append("\nHigher worlds remain beyond perception until your cultivation can enter them.")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(realmhub_group, name="go", description="Travel directly to the central meeting city of a known realm world")
@app_commands.autocomplete(world=realmhub_world_autocomplete)
@serialized_user_action
async def realmhub_go(interaction:discord.Interaction,world:str)->None:
    c=await require_character(interaction)
    if not c:return
    hub=realm_hub(world)
    if not hub:
        await interaction.response.send_message("Unknown realm capital.",ephemeral=False);return
    discovery_location = str(hub["location"])
    previously_discovered = await DB.has_discovered_location(interaction.user.id, discovery_location)
    try:
        envelope=await ENGINE.authoritative_action(
            "exploration.travel",interaction.user.id,
            {"destination":str(hub["location"]),"mode":"hub"},
            action_id=f"discord:{interaction.id}:exploration.travel.hub",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Realm-capital travel failed: {exc}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    # Hub travel is instant: put the capital's presence role on now so the
    # channel appears before the reply does (v0.21.6).
    await _sync_realm_presence_roles(interaction.guild, interaction.user, {"location": str(hub["location"])})
    channel_line=""
    if interaction.guild:
        rows=await DB.get_realm_hub_channels(interaction.guild.id); row=next((r for r in rows if str(r['world_name'])==world),None)
        if row: channel_line=f"\n💬 Meet other cultivators in <#{int(row['channel_id'])}>."
    await interaction.response.send_message(f"🏙️ **{c['name']} arrives at {hub['display_name']} — {result.get('world') or world}.**{channel_line}")
    if travel_first_discovers_location(
        result, discovery_location, previously_discovered=previously_discovered
    ):
        await send_location_discovery_image(interaction, discovery_location)












city_group = app_commands.Group(name="city", description="The city around you: its gates, districts and who is about")


def _city_of(location: str) -> str:
    data = WORLD.locations.get(location) or {}
    if data.get("outside_location") and (data.get("district") or data.get("shop") or data.get("auction_house")):
        return str(data["outside_location"])
    return location


def _city_parts(city: str) -> list[str]:
    return sorted(name for name, data in WORLD.locations.items() if data.get("district") and str(data.get("outside_location")) == city)


def _city_inn(city: str) -> str:
    return next((name for name in _city_parts(city) if WORLD.locations[name].get("district") == "inn"), "")


def _city_board(city: str) -> list[dict[str, Any]]:
    """The commissions whose givers live in this city or its parts."""
    givers = WORLD.data.get("commission_givers", {}) or {}
    rows = []
    for c in WORLD.data.get("commissions", []) or []:
        giver = str(c.get("giver_npc") or "")
        where = str((givers.get(giver) or {}).get("location") or (WORLD.npcs.get(giver) or {}).get("location") or "")
        if where and _city_of(where) == city:
            rows.append(dict(c))
    return sorted(rows, key=lambda c: (int(c.get("tier") or 1), str(c.get("title"))))


def _prosperity_line(data: dict[str, Any]) -> str:
    if not data or not data.get("found", True):
        return ""
    prosperity = int(data.get("prosperity") or 0)
    mood = "thriving" if prosperity >= 70 else ("struggling" if prosperity <= 30 else "getting by")
    return f"The city is **{mood}** — prosperity {prosperity}/100, security {int(data.get('security') or 0)}/100."


@registered_group_command(city_group, name="look", description="See the gates and districts of this city, where you stand, and who is here")
async def city_look(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    here = str(c.get("location") or "")
    city = _city_of(here)
    parts = sorted(name for name, data in WORLD.locations.items() if data.get("district") and str(data.get("outside_location")) == city)
    here_data = WORLD.locations.get(here) or {}
    if here_data.get("road_site"):
        leg = [str(x) for x in list(here_data.get("road_leg") or [])]
        people = await npcs_present(here)
        lines = [f"🛤️ **{here}** — {ROAD_SITE_LABEL.get(str(here_data.get('road_site')), 'a place')} on the {' – '.join(leg)} road.", str(here_data.get("description") or "")]
        lines.append(f"**Here:** {', '.join(people[:12]) if people else 'nobody of note at the moment'}.")
        lines.append(_road_site_line(str(here_data.get("road_site")), here, leg).strip())
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return
    if not parts and city == here and not WORLD.locations.get(city, {}).get("gates"):
        await interaction.response.send_message(f"🏙️ **{here}** has no walls and no districts - it is all one place.", ephemeral=False)
        return
    lines = [f"🏙️ **{city}** — you are at **{here}**." if here != city else f"🏙️ **{city}** — you are in the centre."]
    gates = [p for p in parts if WORLD.locations[p].get("gate")]
    districts = [p for p in parts if not WORLD.locations[p].get("gate")]
    if gates:
        faces = WORLD.locations.get(city, {}).get("gates") or {}
        lines.append("**Gates:** " + "; ".join(f"{g} → {', '.join(faces.get(str(WORLD.locations[g].get('gate')), []))}" for g in gates))
    if districts:
        lines.append("**Districts:** " + ", ".join(districts))
    people = await npcs_present(here)
    if people:
        lines.append(f"**Here:** {', '.join(people[:12])}" + (" …" if len(people) > 12 else ""))
    else:
        lines.append("**Here:** nobody of note at the moment.")
    try:
        mood = _prosperity_line(dict(await SIM.civilization_status(city) or {}))
    except Exception:
        mood = ""
    if mood:
        lines.append(mood)
    lines.append(f"{WORLD.locations.get(here, {}).get('description', '')}")
    lines.append("Walk to any gate or district with **/travel**; walk the streets with **/world → Act → Explore** to find the shops. **/world → City → Board** for work, **Inn** for company, **Rumours** for news.")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(city_group, name="board", description="The city's commission board: a quest pavilion in a capital, the gate notice elsewhere")
async def city_board(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    city = _city_of(str(c.get("location") or ""))
    board = _city_board(city)
    if not board:
        await interaction.response.send_message(f"🪧 No board in {city} - find one in a city.", ephemeral=False)
        return
    capital = bool(WORLD.locations.get(city, {}).get("realm_hub"))
    held = {str(r.get("quest_key")): str(r.get("status")) for r in await DB.list_character_quests(interaction.user.id)}
    title = f"🏯 **The Quest Pavilion of {city}**" if capital else f"🪧 **The notice board of {city}**"
    lines = [title]
    for q in board[:20]:
        key = str(q.get("quest_key"))
        state = held.get(key)
        mark = {"active": "📌 held", "completed": "✅ done", "failed": "❌ failed", "abandoned": "↩️ abandoned"}.get(state or "", "open")
        reward = dict(q.get("rewards") or {})
        lines.append(f"• **{q.get('title')}** (tier {int(q.get('tier') or 1)}) — from {q.get('giver_npc')}; {int(reward.get('spirit_stones') or 0)} stones, {int(reward.get('insight_xp') or 0)} insight — {mark}")
    if capital:
        wanted = await DB.list_active_bounties(limit=8)
        if wanted:
            lines.append("")
            lines.append("**Wanted** — bounties posted across the realm:")
            for b in wanted:
                lines.append(f"• {b.get('target_name')} — {int(b.get('amount') or 0)} stones, {b.get('jurisdiction')}: {str(b.get('reason') or '')[:80]}")
    lines.append("")
    lines.append("Take one with **/world → City → Accept** while you are in the city; the giver is found in their own district.")
    await reply_long(interaction, "\n".join(lines))


@registered_group_command(city_group, name="accept", description="Take a commission from this city's board")
@serialized_user_action
async def city_accept(interaction: discord.Interaction, quest: str, terms: app_commands.Range[int, 0, 2] = 0) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    city = _city_of(str(c.get("location") or ""))
    board = {str(q.get("quest_key")): q for q in _city_board(city)}
    if quest not in board:
        await interaction.followup.send(f"❌ That is not on {city}'s board. Read it with **/world → City → Board**.", ephemeral=False)
        return
    try:
        envelope = await ENGINE.authoritative_action("commission.accept", interaction.user.id, {"quest_key": quest, "variant_index": int(terms)}, action_id=f"discord:{interaction.id}:commission.accept")
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    q = board[quest]
    await interaction.followup.send(
        f"📜 **{q.get('title')}** taken from {q.get('giver_npc')}'s board. {q.get('description')}\n"
        f"Deadline in **{human_duration(int(result.get('deadline_game_minutes') or q.get('deadline_game_minutes') or 0))}**. Progress shows under **/quests**.",
        ephemeral=False,
    )


@city_accept.autocomplete("quest")
async def city_accept_quest_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    city = _city_of(str(c.get("location") or ""))
    held = {str(r.get("quest_key")) for r in await DB.list_character_quests(interaction.user.id)}
    needle = current.casefold().strip()
    choices = []
    for q in _city_board(city):
        key = str(q.get("quest_key"))
        if key in held:
            continue
        title = str(q.get("title"))
        if needle and needle not in title.casefold():
            continue
        choices.append(app_commands.Choice(name=f"{title} — {q.get('giver_npc')}"[:100], value=key))
    return choices[:25]


@registered_group_command(city_group, name="envoys", description="The sect envoys' hall of a capital: every sect's gate in this world, and the way there")
async def city_envoys(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    here = str(c.get("location") or "")
    city = _city_of(here)
    data = WORLD.locations.get(here) or {}
    if not WORLD.locations.get(city, {}).get("realm_hub"):
        await interaction.response.send_message("The sect envoys keep their halls in the realm capitals, in the temple quarter.", ephemeral=False)
        return
    hall = next((name for name in _city_parts(city) if WORLD.locations[name].get("district") == "temple"), "")
    if data.get("district") != "temple":
        await interaction.response.send_message(f"The envoys' hall is in **{hall or 'the temple quarter'}** - walk there with **/travel**.", ephemeral=False)
        return
    world = str(WORLD.locations.get(city, {}).get("world") or "")
    sects = []
    for sect_name in WORLD.sects:
        rec = recruitment_definition(WORLD.sects, sect_name)
        if rec and str(WORLD.locations.get(str(rec.get("location")), {}).get("world") or "") == world and rec.get("public_route", True):
            sects.append((sect_name, rec))
    if not sects:
        await interaction.response.send_message("No sect keeps an envoy here.", ephemeral=False)
        return
    wt = await current_world_time()
    try:
        await ENGINE.action("sect.discover", interaction.user.id, {"sects": [name for name, _ in sects], "discovery_kind": "envoys_hall", "source_key": hall, "game_minute": wt.total_minutes})
    except GameEngineError:
        log.exception("Sect discovery could not be recorded at the envoys' hall")
    lines = [f"🏯 **The Sect Envoys' Hall — {hall}**", "Each envoy names their gate and the trial that opens it:"]
    for name, rec in sects:
        lines.append(f"• **{name}** — the {rec.get('trial_name')} at **{rec.get('location')}**, before {rec.get('examiner')}.")
    lines.append("Their routes are on your map now. Open **Sect → Recruitment** to learn about a gate, and **/travel** to reach it.")
    await reply_long(interaction, "\n".join(lines))


RUMOUR_LIMIT = 8


def rumours_a_city_has_heard(rows: "list[dict[str, Any]]") -> "list[dict[str, Any]]":
    """What a teller in this city may repeat, out of everything gathered.

    Reported from live play as one rumour printed five times (v1.0.13). The
    page asked `get_structured_world_history(location=place, user_id=...)`
    once per place, and that function's relevance clause is an **OR** -
    `location=? OR related_user_id=? OR actor_key=? OR target_key=?` - so
    every row about the asking player came back for *every* place queried.
    Ashenwall City and its parts are eight queries, so one discovery was
    eight rows. The caller has stopped asking about the player at all, which
    is the whole of why that duplication existed; this dedupes by
    `history_id` anyway, because the union is only distinct while a row has
    one location and that is the query's property rather than this page's.

    **Public only, and that is the point rather than a tidy-up.** The
    function's own docstring says it *"deliberately returns a superset. The
    RAG retriever performs the final viewpoint/visibility check so one code
    path owns knowledge safety"* - and this page was a second consumer that
    performed neither. So the landlady could repeat a `participant` row the
    player alone was party to, in a city where it did not happen, and a
    `hidden` one: `npc_deeds` writes an unwitnessed robbery or a contraband
    drop at an NPC's own location, which is a city, and the tree's rule for
    those is that the world genuinely does not know. A rumour is what the
    city has *heard*, so it is public news or it is not a rumour.
    """
    seen: dict[int, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("visibility") or "public").strip().lower() != "public":
            continue
        seen.setdefault(int(row.get("history_id") or 0), row)
    ordered = sorted(seen.values(), key=lambda e: int(e.get("game_minute") or 0), reverse=True)
    return ordered[:RUMOUR_LIMIT]


@registered_group_command(city_group, name="rumours", description="What the city has heard lately, told by the people who hear everything first")
async def city_rumours(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    city = _city_of(str(c.get("location") or ""))
    parts = _city_parts(city)
    if not parts and not WORLD.locations.get(city, {}).get("gates"):
        await interaction.response.send_message("Rumours are traded in cities - at the gate, or in the lower town.", ephemeral=False)
        return
    # Rumours are kept where people drink, and this looked in the wrong room
    # for both reasons at once (v1.0.0-rc.24). It wanted a `lower` district,
    # which exists on exactly four locations in the whole world, and inside it
    # a name beginning "Innkeeper" or "Beggar King", which no NPC in the
    # catalogue has - Greenriver Town's are *Landlady* Yu Lian and *Old
    # Beggar* Chen. So forty-four of the forty-eight cities fell through to
    # the gate captain and the lower town never spoke. The `inn` district is
    # in all forty-eight and every one of them has its landlady standing in
    # it, so ask there first and match what an NPC *does*, not what they are
    # called.
    teller = ""
    for district in ("inn", "lower"):
        part = next((name for name in parts if WORLD.locations[name].get("district") == district), "")
        if not part:
            continue
        teller = next((n for n, npc in WORLD.npcs.items()
                       if str(npc.get("location")) == part
                       and any(word in str(npc.get("role") or "").casefold()
                               for word in ("innkeep", "beggar", "tavern", "teahouse", "landlad", "landlord"))), "")
        if teller:
            break
    if not teller:
        gate = next((name for name in parts if WORLD.locations[name].get("gate")), "")
        teller = next((n for n, npc in WORLD.npcs.items() if str(npc.get("location")) == gate and n.startswith("Gate Captain")), "") if gate else ""
    gathered = []
    for place in [city, *parts]:
        gathered.extend(await DB.get_structured_world_history(location=place, min_significance=20, limit=6))
    events = rumours_a_city_has_heard(gathered)
    lines = [f"🗣️ **Rumours in {city}**" + (f" — as {teller} tells them" if teller else "")]
    if not events:
        lines.append("Nothing worth repeating has happened here lately. Make something happen.")
    for e in events:
        lines.append(f"• **{e.get('title')}** — {str(e.get('summary') or '')[:200]}")
    # The realms (v1.0.0-rc.2): an opening is public news across the world
    # it happened in, and the rotation's next turn is what the tellers bet on.
    world = str(WORLD.locations.get(city, {}).get("world") or "")
    for row in await DB.get_active_world_events():
        if str(row.get("event_type")) != "secret_realm":
            continue
        where = str(row.get("location") or "")
        if str(WORLD.locations.get(where, {}).get("world") or "") != world:
            continue
        lines.append(f"• 🌀 **{row.get('title')}** stands open at **{where}** — the road there is the road to a realm.")
    try:
        rotation = dict((await ENGINE.action("secret_realm.status", interaction.user.id, {}) or {}).get("rotation") or {})
    except GameEngineError:
        rotation = {}
    next_realm = dict(WORLD.secret_realms.get(str(rotation.get("next_realm_id") or "")) or {})
    if next_realm and str(WORLD.locations.get(str(next_realm.get("location") or ""), {}).get("world") or "") == world:
        lines.append(f"• 🔄 The tellers say **{next_realm.get('name')}** at **{next_realm.get('location')}** is next to open, on the world tick.")
    await reply_long(interaction, "\n".join(lines))


@registered_group_command(city_group, name="inn", description="The city's inn: who is in town, the merchants at the corner table, and the common room thread")
async def city_inn(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    here = str(c.get("location") or "")
    city = _city_of(here)
    inn = _city_inn(city)
    if not inn:
        await interaction.response.send_message("There is no inn here - every city keeps one.", ephemeral=False)
        return
    await interaction.response.defer(ephemeral=False)
    present = []
    for place in [city, *_city_parts(city)]:
        for row in await DB.get_characters_at_location(place, exclude_user_id=interaction.user.id):
            present.append(f"{row.get('name')} ({place})")
    merchants = []
    try:
        for row in list((await ENGINE.action("merchant.status", interaction.user.id, {}) or {}).get("merchants") or []):
            if not bool(row.get("on_the_road")) and str(row.get("location")) == city:
                merchants.append(str(row.get("name")))
    except GameEngineError:
        pass
    keeper = next((n for n, npc in WORLD.npcs.items() if str(npc.get("location")) == inn), "the landlord")
    lines = [f"🍶 **{inn}** — {keeper} keeps the long table.", str(WORLD.locations.get(inn, {}).get("description") or "")]
    lines.append(f"**In town:** {', '.join(present) if present else 'no other cultivators tonight'}.")
    lines.append(f"**At the corner table:** {', '.join(merchants) if merchants else 'no merchant in town - see /economy → Merchants for who is on the road'}.")
    lines.append("**By the door:** the caravan master's notice - guards wanted for the next road out; see **/economy → Caravans**.")
    lines.append("**At the long table:** trades are struck here, cultivator to cultivator - **/economy → Trade → Offer** names who gets what, and they accept or decline.")
    thread = await _inn_thread(interaction, city, inn)
    if thread is not None:
        lines.append(f"**Common room:** {thread.mention} - whoever is in {city} talks here.")
    await interaction.followup.send("\n".join(lines), ephemeral=False)


async def _inn_thread(interaction: discord.Interaction, city: str, inn: str) -> discord.Thread | None:
    """One public thread per city inn, in the world's realm-hub channel."""
    guild = interaction.guild
    if guild is None:
        return None
    world = str(WORLD.locations.get(city, {}).get("world") or "")
    rows = await DB.get_realm_hub_channels(guild.id)
    row = next((r for r in rows if str(r.get("world_name")) == world), None)
    if not row:
        return None
    channel = guild.get_channel(int(row["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return None
    name = f"🍶 {inn}"[:100]
    for thread in channel.threads:
        if thread.name == name:
            try:
                await thread.add_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            return thread
    try:
        thread = await channel.create_thread(name=name, type=discord.ChannelType.public_thread, reason=f"The common room of {inn}")
        await thread.send(f"🍶 **{inn}** — the common room of {city}. Whoever is in the city passes through here; speak freely, the landlord hears everything anyway.")
        await thread.add_user(interaction.user)
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not open the inn thread for %s", inn)
        return None


travel_group = app_commands.Group(name="travel", description="Travel to another known location")


ROAD_SITE_LABEL = {"waystation": "a waystation", "hunting_ground": "a hunting ground", "ruin": "a ruin", "shrine": "a wayside shrine"}


def _road_site_line(kind: str, site: str, leg: list) -> str:
    """What a road-side site (v0.39.0) offers, told on arrival."""
    if not kind:
        return ""
    back = " – ".join(str(x) for x in leg) if leg else "either city"
    if kind == "waystation":
        what = f"a walled yard on the {back} road. The stall under the eaves sells what the road takes out of you: **/economy → City Shops → Browse**. Merchants walking this road stop here: **/economy → Merchants → Status**."
    elif kind == "hunting_ground":
        what = f"the best hunting on the {back} road, and the least safe: **/world → Act → Hunt** here rolls easier and the spoils are half again."
    elif kind == "ruin":
        what = f"old stones by the {back} road. **/world → Act → Explore** turns up twice what it would elsewhere, and this is where the secret realms of this road open."
    else:
        what = f"a stone and a bell on the {back} road. Nobody hunts within sound of it; **/world → Act → Explore** here steadies the mind."
    return f"\n🛤️ **{site}** is {ROAD_SITE_LABEL.get(kind, 'a place')}: {what} The road leads back to **{leg[0] if leg else '…'}** or on to **{leg[1] if len(leg) > 1 else '…'}** with **/travel**."


def _travel_mode_line(result: dict) -> str:
    """How the journey was crossed, in the words of the genre.

    The engine decides it (realm, or the best flying artifact in the bags)
    and hands back ``travel_mode``/``travel_mount``; this only says it. The
    walking line carries the pointer to a flying artifact deliberately - it
    is shown to exactly the cultivators who cannot yet leave the ground,
    and stops showing itself the moment they can.
    """
    mode = str(result.get("travel_mode") or "")
    mount = str(result.get("travel_mount") or "")
    if mode == "folding space":
        return "\n🌌 You do not cross the distance so much as fold it; the road passes beneath you in moments."
    if mode == "flying":
        if mount:
            return f"\n🗡️ You ride **{mount}** above the road — a third of the walking hours, and little on the ground can reach you."
        return "\n☁️ You leave the ground and fly it — a third of the walking hours, and little on the ground can reach you."
    return "\n🚶 You walk it. A flying artifact would cut the road to a third: **/economy → City Shops → Browse**."


def _discord_arrival_display(result: dict) -> str:
    """Render a road journey's arrival as a live, self-updating Discord
    timestamp when the Go engine could resolve one, falling back to the raw
    game-minute figure (e.g. when the world clock is frozen at scale=0, or
    the world_state row can't be read) so the message never goes blank.
    """
    arrival_ts = result.get("arrival_unix_ts")
    if isinstance(arrival_ts, (int, float)) and arrival_ts > 0:
        ts = int(arrival_ts)
        return f"<t:{ts}:R> (<t:{ts}:t>)"
    return f"game minute {int(result.get('arrival_game_minute') or 0)}"


@registered_group_command(travel_group, name="go", description="Travel to another known location")
@app_commands.autocomplete(destination=location_autocomplete)
@serialized_user_action
async def travel(interaction: discord.Interaction, destination: str) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    undiscovered_image_locations = {
        location
        for location in LOCATION_DISCOVERY_IMAGES
        if not await DB.has_discovered_location(interaction.user.id, location)
    }
    try:
        envelope=await ENGINE.authoritative_action(
            "exploration.travel",interaction.user.id,
            {"destination":destination,"mode":"known"},
            action_id=f"discord:{interaction.id}:exploration.travel",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Travel failed: {exc}",ephemeral=False)
        return
    result=dict(envelope.get("result") or {})
    desc=str(result.get("description") or "")
    safe="\n🛡️ This location is protected by laws or formations." if bool(result.get("safe_zone")) else ""
    road=""
    if bool(result.get("road_connection")):
        danger=int(result.get("road_danger") or 0)
        chance=int(result.get("road_encounter_chance_percent") or 0)
        arrival_display=_discord_arrival_display(result)
        road=_travel_mode_line(result)+(
            f"\n⏱️ Arrival **{arrival_display}** • Danger **{danger}/45** • "
            f"Encounter risk **{chance}%**."
            "\n🚶 You remain in transit and cannot take authoritative actions until arrival."
        )
        encounter=result.get("road_encounter")
        if isinstance(encounter,dict):
            road+=f"\n⚠️ {str(encounter.get('detail') or 'A road encounter delays the journey')}"
            delay=int(encounter.get("delay_minutes") or 0)
            damage=int(encounter.get("vitality_damage") or 0)
            if delay or damage:
                road+=f" (**+{delay} min**, **-{damage} Vitality**)"
    envoy_line=""
    dest_data=WORLD.locations.get(str(result.get("destination") or destination)) or {}
    if dest_data.get("district")=="temple" and WORLD.locations.get(str(dest_data.get("outside_location") or ""),{}).get("realm_hub"):
        envoy_line="\n🏯 The sect envoys keep their hall here - **/world → City → Envoys** names every gate in this world and puts it on your map."
    gate_line=""
    arrived_at=str(result.get("arrived_at") or "")
    if arrived_at and arrived_at!=str(result.get("destination") or destination):
        parts=[p for p in list(result.get("city_parts") or []) if p!=arrived_at]
        inside=", ".join(f"**{p}**" for p in parts) if parts else "the streets"
        gate_line=(f"\n🏯 You arrive at the **{arrived_at}** — the road behind you, the city ahead. Inside the walls: {inside}. "
                   "Step in with **/travel**; **/world → City → Look** shows who is about.")
    if result.get("left_by_gate"):
        gate_line=f"\n🚪 You leave by the **{result.get('left_by_gate')} Gate**."+gate_line
    shop_line=""
    shop_key=str((WORLD.locations.get(str(result.get("destination") or destination)) or {}).get("shop") or "")
    if shop_key:
        shop=dict(WORLD.shops.get(shop_key) or {})
        shop_line=f"\n🏪 You step inside; **{shop.get('keeper')}** looks up from the counter. Browse with **/economy → City Shops → Browse**; the door opens back onto {shop.get('city')} with **/travel**."
    site_line=_road_site_line(str(result.get("site_kind") or ""),str(result.get("destination") or destination),list(result.get("site_leg") or []))
    sites_found=""
    for row in list(result.get("road_sites_found") or []):
        if not isinstance(row,dict):continue
        leg=list(row.get("leg") or [])
        sites_found+=(f"\n🛤️ On the way you find **{row.get('name')}** — {ROAD_SITE_LABEL.get(str(row.get('kind')),'a place')} on the {' – '.join(leg)} road. "
                      "It is on your map now: reach it with **/travel** from either end.")
    merchants=""
    for row in list(result.get("merchant_encounters") or []):
        if not isinstance(row,dict):continue
        if str(row.get("met"))=="road":
            merchants+=f"\n🧳 **{row.get('name')}** is on this road ({row.get('location')} → {row.get('destination')}) — trade by the roadside with **/economy → Merchants → Buy**."
        else:
            merchants+=f"\n🧳 **{row.get('name')}** is trading at **{row.get('location')}** along the way."
    meeting=""
    hub_match=realm_hub_by_location(str(result.get("destination") or destination))
    if hub_match and interaction.guild:
        world_name,_hub=hub_match
        rows=await DB.get_realm_hub_channels(interaction.guild.id)
        row=next((r for r in rows if str(r.get("world_name"))==world_name),None)
        if row: meeting=f"\n💬 Public meeting channel: <#{int(row['channel_id'])}>."
    # Where they ended up, not where they asked for: a road journey that stops
    # at the gate arrives at `arrived_at`, and an objective naming the city
    # would never be satisfied by somebody standing in it.
    try:
        wt_travel = await current_world_time()
        progressed = await record_quest_progress(
            interaction.user.id, "travel", amount=1,
            target=str(result.get("arrived_at") or result.get("destination") or destination),
            game_minute=wt_travel.total_minutes)
        await announce_quest_progress(interaction, progressed)
    except Exception:
        log.exception("Quest progress update failed after travel")
    await interaction.followup.send(
        f"🗺️ **{c['name']} travels to {result.get('destination') or destination}.**\n{desc}{gate_line}{envoy_line}{shop_line}{road}{merchants}{safe}{meeting}{site_line}{sites_found}"
    )
    for location in sorted(undiscovered_image_locations):
        if travel_first_discovers_location(
            result, location, previously_discovered=False
        ):
            await send_location_discovery_image(interaction, location)


async def travel_destination_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """The travel picker with its places grouped and labelled (v0.40.0):
    this city's parts and shops, road-side sites, cities by road hops, the
    capitals - the same known places the slash autocomplete offers."""
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    known = await _known_locations(interaction.user.id, c)
    needle = str(current or "").casefold().strip()
    rows = destination_groups(str(c.get("location") or ""), known, access_realm_index(c))
    out = []
    for name, emoji, description, _order in rows:
        if needle and needle not in name.casefold():
            continue
        out.append(HubDynamicOption(label=name[:100], value=name[:100], description=description[:100], emoji=emoji))
    return out[:25]


register_hub_option_provider(travel, "destination", travel_destination_hub_options)


@registered_group_command(travel_group, name="status", description="Show your destination and a live countdown while you are traveling")
async def travel_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    try:
        # ENGINE.action() already unwraps the HTTP envelope's "result" field
        # (unlike the authoritative-mutation client method, which hands back
        # the full envelope) - the query's own fields are the top level here.
        result = dict(await ENGINE.action("exploration.travel_status", interaction.user.id, {}) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"Travel status could not be read: {exc}", ephemeral=False)
        return
    if not bool(result.get("traveling")):
        await interaction.response.send_message(
            f"🗺️ **{c['name']}** is not currently traveling — at **{await character_location_display(c)}**.",
            ephemeral=False,
        )
        return
    arrival_display = _discord_arrival_display(result)
    remaining = int(result.get("remaining_game_minutes") or 0)
    text = (
        f"🚶 **{c['name']}** is en route to **{result.get('destination', 'an unknown destination')}**.\n"
        f"⏱️ Arrival **{arrival_display}** • {remaining} game-minutes remaining."
        "\nYou cannot take authoritative actions until you arrive."
    )
    await interaction.response.send_message(text, ephemeral=False)


async def restore_exploration_event_views(bot: discord.Client) -> int:
    """Re-register the panel of every exploration encounter still running.

    The encounter is the engine's row; the buttons were the process's. After a
    restart a player whose exploration was interrupted had a panel that
    answered nothing and no command that would redraw it - the only way out
    was to wait the event out. The engine's own status call is what rebuilds
    each panel, so a restored one carries the actions that are still available
    rather than the ones that were available when it was posted.
    """
    restored = 0
    try:
        rows = await DB.get_live_exploration_events()
    except Exception:
        log.exception("Could not read the live exploration encounters to restore")
        return 0
    for row in rows:
        user_id = int(row.get("user_id") or 0)
        event_id = str(row.get("event_id") or "")
        if not user_id or not event_id:
            continue
        try:
            status = await ENGINE.action("exploration.event.status", user_id, {"event_id": event_id})
            bot.add_view(ExplorationEventView(user_id, dict(status or row)))
        except Exception:
            log.exception("Could not restore the exploration event panel for %s", event_id)
            continue
        restored += 1
    return restored


VIEW_RESTORERS.register("exploration_event", restore_exploration_event_views)


register_hub_option_hint(
    craft,
    "recipe",
    "You have not learned a method yet. Buy a jade slip at a hall of the trade "
    "(**/economy → City Shops → Here**) and read it with **/craft → Profession → Learn**; "
    "**Profession Status** lists everything you know and what each one needs.",
)
