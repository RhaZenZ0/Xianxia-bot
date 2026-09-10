"""Exploration: explore, hunt, craft, /alchemy, /realmhub and /travel.

Split phase 9c (v0.19.46, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order (one contiguous block); reads only modules
below main.py. `_run_crafting` stays here because `craft` and
`/alchemy refine` are its only two callers and both live in this file.
"""
from __future__ import annotations

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
from ..channels import send_long_to_thread
from ..character_state import announce_quest_progress
from ..discovery import (
    LOCATION_DISCOVERY_IMAGES,
    send_location_discovery_image,
    travel_first_discovers_location,
)
from ..formatting import human_duration, roll_line
from ..locations import location_autocomplete
from ..registry import registered_group_command, registered_root_command
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
)
from ..threads import ensure_expedition_thread
from ..ui.event_scene import spawn_event_thread
from .sect import _sect_recruitment_at_location


class ExplorationEventView(discord.ui.View):
    """Personal exploration-event controls backed entirely by Go authority."""

    def __init__(self, owner_user_id: int, event: dict[str, Any]) -> None:
        self.owner_user_id = int(owner_user_id)
        self.event = dict(event or {})
        expires_at = float(self.event.get("expires_at") or time.time() + 7200)
        super().__init__(timeout=max(300, min(21600, int(expires_at - time.time()))))
        self._sync_buttons()

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
            custom_id = str(item.custom_id or "")
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
                
                "cooldown_seconds": SETTINGS.explore_cooldown_minutes * 60,
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
                surprise_text += f"\nThis is now a **server-wide event** for about {int(surprise.get('duration_hours') or 2)}h. Use **/world → Events**."
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
                    f"That entrance is already open at **{surprise.get('location') or c['location']}**. Use **/world → Events**."
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
    discovered_location = str(outcome.get("discovered_location") or "")
    if discovered_location:
        discovery_text = (
            f"\n\n🧭 **New route discovered — {discovered_location}**\n"
            f"{WORLD.locations.get(discovered_location, {}).get('description', 'A newly charted route opens before you.')}"
        )
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
                await announce_quest_progress(interaction, await QUESTS.progress(interaction.user.id, "sect_discovery", amount=1, game_minute=wt_discovery.total_minutes))
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
        await announce_quest_progress(interaction, await QUESTS.progress(interaction.user.id, "explore", amount=1, target=str(c.get("location") or ""), game_minute=wt_discovery.total_minutes))
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
            interaction, full_exploration + "\n\n⚠️ No private expedition thread is configured. Ask an admin to bind the existing channel in the admin dashboard, then run **/admin → Server → Base Channels → Validate / bind**.",
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
            {"cooldown_seconds": SETTINGS.hunt_cooldown_minutes * 60},
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
    await reply_long(interaction, text)
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
    names = await DB.search_catalog("recipe", current, 25)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names]


async def alchemy_recipe_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    names = [
        name for name, recipe in WORLD.recipes.items()
        if str(recipe.get("profession", "")).casefold() == "alchemy"
        and (not needle or needle in name.casefold())
    ]
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in sorted(names)[:25]]


async def _run_crafting(
    interaction: discord.Interaction, recipe: str, *, required_profession: str | None = None,
) -> None:
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
    profession = str(r["profession"])
    if required_profession and profession.casefold() != str(required_profession).casefold():
        await interaction.response.send_message(
            f"**{recipe}** is a **{profession}** recipe, not {required_profession}.", ephemeral=False,
        )
        return

    try:
        envelope = await ENGINE.authoritative_action(
            "craft.resolve",
            interaction.user.id,
            {"recipe": recipe},
            action_id=f"discord:{interaction.id}:craft.resolve",
        )
    except GameEngineError as exc:
        message = str(exc)
        if "missing materials" in message.casefold():
            message = "Missing materials for that recipe."
        await interaction.response.send_message(message, ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    result = SimpleNamespace(**resolved)
    facility_bonus = int(resolved.get("facility_bonus", 0))
    manor_facility_bonus = int(resolved.get("manor_facility_bonus", 0))
    inherited_family_bonus = int(resolved.get("family_bonus", 0))
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

    await interaction.response.send_message(
        f"**{profession}: {recipe}**\n{roll_line(result)}\n"
        f"{('Player-property facility bonus: **+'+str(facility_bonus)+'**\n') if facility_bonus else ''}"
        f"{('Sect-manor facility bonus: **+'+str(manor_facility_bonus)+'**\n') if manor_facility_bonus else ''}"
        f"{('Birth-family Alchemy tradition: **+'+str(inherited_family_bonus)+'**\n') if inherited_family_bonus else ''}"
        f"{('Profession mastery bonus: **+'+str(profession_bonus)+'**\n') if profession_bonus else ''}"
        f"{outcome}{quality_line}{mastery_line}"
    )


@registered_root_command(name="craft", description="Practice alchemy, forging, or formation inscription from a known recipe", guild=GUILD)
@app_commands.autocomplete(recipe=recipe_autocomplete)
@serialized_user_action
async def craft(interaction: discord.Interaction, recipe: str) -> None:
    await _run_crafting(interaction, recipe)


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
    inherited_family_bonus = family_profession_bonus(birth_family, "Alchemy")
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


@registered_group_command(alchemy_group, name="refine", description="Refine a pill recipe using the connected Alchemy crafting system")
@app_commands.autocomplete(recipe=alchemy_recipe_autocomplete)
@serialized_user_action
async def alchemy_refine(interaction: discord.Interaction, recipe: str) -> None:
    await _run_crafting(interaction, recipe, required_profession="Alchemy")


@registered_group_command(alchemy_group, name="forage", description="Gather medicinal herbs using the current region's simulated spirit resources")
@serialized_user_action
async def alchemy_forage(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    remaining = await DB.cooldown_remaining(interaction.user.id, "alchemy_forage")
    if remaining:
        await interaction.response.send_message(
            f"The nearby herb beds need time to recover. Forage again in **{human_duration(remaining)}**.", ephemeral=False,
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
    success = bool(resolved.get("success"))
    forage_progress = dict(resolved.get("profession_progress") or {})
    level = int(forage_progress.get("level", 0))
    forage_location = str(resolved.get("location") or c.get("location", ""))
    inherited_forage_bonus = int(resolved.get("family_bonus", 0))
    garden_bonus = int(resolved.get("garden_bonus", 0))
    bonus_bits = (
        f"{(' • Alchemy-family herb lore **+'+str(inherited_forage_bonus)+'**') if inherited_forage_bonus else ''}"
        f"{(' • Property herb garden **+'+str(garden_bonus)+'**') if garden_bonus else ''}"
    )
    if not success:
        await interaction.response.send_message(
            f"🌿 **Medicinal Forage — {forage_location}**\n{roll_line(result)}\n"
            f"Regional spirit resources: **{int(resolved.get('spirit_resources',0))}/100**.{bonus_bits} "
            "You find no usable harvest this time."
            f"\n🧺 Foraging: **{profession_rank(level)}** Lv.{level} "
            f"• XP {int(forage_progress.get('xp',0))}/{profession_xp_needed(level)}"
        )
        return
    awarded = {str(k): int(v) for k, v in dict(resolved.get("loot") or {}).items()}
    rare = str(resolved.get("rare_found") or "")
    rare_line = f"\n✨ Rare find: **{WORLD.item_name(rare)}**." if rare else ""
    await interaction.response.send_message(
        f"🌿 **Medicinal Forage — {forage_location}**\n{roll_line(result)}\n"
        f"Regional spirit resources: **{int(resolved.get('spirit_resources',0))}/100**.{bonus_bits}\n"
        f"Harvested: **{WORLD.item_names(awarded)}**.{rare_line}\n"
        f"🧺 Foraging: **{profession_rank(level)}** Lv.{level} "
        f"• XP {int(forage_progress.get('xp',0))}/{profession_xp_needed(level)}"
    )


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
    await interaction.followup.send(
        f"🫧 You circulate **{int(result.get('qi_cost', 0))} Qi** through the meridians and purge "
        f"**{int(result.get('purged', 0))}** toxicity. "
        f"Pill toxicity is now **{int(result.get('pill_toxicity', 0))}/100**.", ephemeral=False,
    )


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












travel_group = app_commands.Group(name="travel", description="Travel to another known location")


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
        road=(
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
    await interaction.followup.send(
        f"🗺️ **{c['name']} travels to {result.get('destination') or destination}.**\n{desc}{road}{merchants}{safe}{meeting}"
    )
    for location in sorted(undiscovered_image_locations):
        if travel_first_discovers_location(
            result, location, previously_discovered=False
        ):
            await send_location_discovery_image(interaction, location)


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
