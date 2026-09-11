"""Roleplay scenes: talk, action (with its panel views), npcinfo, /scene.

Split phase 9e (v0.19.48, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order. `_scene_action_targets` and
`scene_action_panel` are what `EventSceneView` (ui/event_scene.py) reaches
through the `EVENT_HANDLERS` registry; the two bindings moved here with
them from main.py's register_event_handlers() (see the bottom of the file).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...ai.narrator import roll_npc_memory
from ...rules import commissions as commission_rules
from ...rules.npc_memory import classify_memory, exchange_memory_summary, public_mood_hint
from .. import scene_layout
from ..character_state import announce_quest_progress, current_effect_modifiers
from ..formatting import roll_line
from ..locations import _location_is_visible, current_npc_location, local_npc_autocomplete
from ..registry import EVENT_HANDLERS, registered_group_command, registered_root_command
from ..runtime import (
    DB,
    ENGINE,
    WORLD,
    character_location_display,
    current_world_time,
    log,
    reply_long,
    require_character,
    serialized_user_action,
)
from ..services import (
    COMMISSIONS,
    GUILD,
    NARRATOR,
    NARRATOR_CONTEXT,
    NARRATOR_QUEUE,
    NPC_RELATIONSHIPS,
    QUESTS,
    SIM,
)
from ..ui.commissions import commission_reply_extras, offer_for as commission_offer_for
from ..threads import _private_scene_for_thread, active_private_location_thread, ensure_expedition_thread


@registered_root_command(name="talk", description="Speak with a persistent NPC", guild=GUILD)
@app_commands.autocomplete(npc=local_npc_autocomplete)
@serialized_user_action
async def talk(
    interaction: discord.Interaction,
    npc: str,
    message: str,
) -> None:
    c = await require_character(interaction)
    if not c:
        return
    npc_data = await DB.get_npc_definition(npc)
    if not npc_data:
        await interaction.response.send_message("Unknown NPC.", ephemeral=False)
        return
    wt = await current_world_time()
    npc_location = await current_npc_location(npc, wt.period)
    if npc_location and npc_location != c.get("location"):
        await interaction.response.send_message(
            f"**{npc}** is currently at **{npc_location}** during the **{wt.period}**, not **{await character_location_display(c)}**.",
            ephemeral=False,
        )
        return
    channel_id = interaction.channel_id or 0
    await DB.add_history(
        channel_id,
        user_id=interaction.user.id,
        speaker=c["name"],
        content=f"To {npc}: {message}",
    )
    memory = await DB.get_npc_memory(interaction.user.id, npc)
    history = await DB.get_history(channel_id, 24)
    lineage_context = await DB.describe_lineage_context(interaction.user.id)
    social_context = (await NARRATOR_CONTEXT.build(
        c, scene_type="freeform roleplay", lineage_context=lineage_context,
        query_text=message, focus_npc=npc,
    )).text
    relationship = await NPC_RELATIONSHIPS.get(interaction.user.id, npc)
    relationship_context = (
        f"\nPersistent relationship: {NPC_RELATIONSHIPS.public_label(relationship)}; "
        f"trust {relationship.get('trust',0):+d}, respect {relationship.get('respect',0):+d}, "
        f"fear {relationship.get('fear',0):+d}, debt {relationship.get('debt',0):+d}, "
        f"grudge {relationship.get('grudge',0):+d}. "
        "These values are canonical; narrate them but do not change them."
    )
    social_context += relationship_context
    npc_state = await SIM.npc_status(npc) or {}
    salient_memories = await DB.list_npc_player_memories(
        interaction.user.id, npc, 6, mark_recalled=True
    )
    if npc_state:
        visible_mood = public_mood_hint(str(npc_state.get("mood") or ""))
        if visible_mood:
            social_context += f"\nCurrent outward demeanor: {visible_mood}."
        social_context += f"\nCurrent observable activity: {npc_state.get('activity','Following established routine')}."
    # Commissions (v0.22.0): if this NPC gives work, the ladder runs here -
    # before the model call, on stored state only - and its result is handed to
    # the narrator as canon. The player's words influence *whether* he offers
    # only in the sense that they reached him; they never shape the commission.
    commission_offer = None
    commission_block: dict[str, Any] | None = None
    if COMMISSIONS.is_giver(npc):
        try:
            wt_offer = await current_world_time()
            commission_offer = await commission_offer_for(
                interaction.user.id, npc,
                realm_index=int(c.get("realm_index", 0) or 0),
                game_minute=wt_offer.total_minutes,
            )
            commission_block = commission_rules.commission_context(
                commission_offer, item_names=COMMISSIONS.item_names())
        except Exception:
            log.exception("Commission selection failed for %s", npc)
            commission_offer = None
            commission_block = None
    await interaction.response.defer()
    try:
        answer = await NARRATOR_QUEUE.run(
            "talk_to_npc",
            NARRATOR.talk_to_npc(
                character=c,
                npc_name=npc,
                player_dialogue=message,
                memory=memory,
                history=history,
                social_context=social_context,
                scene_context=social_context,
                npc_state=npc_state,
                salient_memories=salient_memories,
                commission_context=commission_block,
            ),
        )
    except Exception:
        log.exception("NPC narration failed")
        answer = f"{npc} studies you in silence; the narrator service failed to answer."

    await DB.add_history(
        channel_id,
        user_id=None,
        speaker=npc,
        content=answer,
    )
    new_memory = roll_npc_memory(memory, message, npc, answer)
    await DB.set_npc_memory(interaction.user.id, npc, new_memory)
    wt_now = await current_world_time()
    memory_kind, salience = classify_memory(message, answer)
    npc_memory_id = await DB.add_npc_player_memory(
        interaction.user.id, npc,
        memory_kind=memory_kind,
        summary=exchange_memory_summary(message, npc, answer),
        salience=salience,
        source="talk",
        game_minute=wt_now.total_minutes,
    )
    await DB.add_rag_memory(
        interaction.user.id, source_key=f"npc:{npc_memory_id}", memory_kind=memory_kind,
        summary=exchange_memory_summary(message, npc, answer), salience=salience,
        location=str(c.get("location") or ""), npc_name=npc,
        faction=str(npc_data.get("sect_affiliation") or ""), source="talk",
        game_minute=wt_now.total_minutes,
    )
    await NPC_RELATIONSHIPS.record_encounter(
        interaction.user.id, npc,
        summary=f"Player: {message[:220]} | {npc}: {answer[:360]}",
        deltas={"trust": 1},
    )
    try:
        await announce_quest_progress(interaction, await QUESTS.progress(interaction.user.id, "talk", target=npc, game_minute=wt_now.total_minutes))
    except Exception:
        log.exception("Quest progress update failed after NPC talk")
    recommendation_hint = ""
    if bool(npc_data.get("can_recommend")) and npc_data.get("sect_affiliation"):
        recommendation_hint = (
            f"\n\n🏯 **Sect connection:** {npc} is affiliated with **{npc_data.get('sect_affiliation')}**. "
            "After speaking with them, you may use **Sect → Recruitment → Recommendation** to ask for formal sponsorship."
        )
    commission_card, commission_view = ("", None)
    if commission_offer is not None:
        try:
            commission_card, commission_view = await commission_reply_extras(interaction.user.id, commission_offer)
        except Exception:
            log.exception("Commission card build failed for %s", npc)
            commission_card, commission_view = ("", None)
    await reply_long(interaction, f"**{npc}**\n{answer}{recommendation_hint}")
    if commission_card:
        # Posted as its own message so the buttons are attached to the
        # canonical card, never to a paragraph the model wrote.
        await interaction.followup.send(commission_card, view=commission_view, ephemeral=False)


SCENE_ACTION_TYPES: dict[str, dict[str, Any]] = {
    "observe": {"label": "Observe", "emoji": "👁️", "attribute": "insight", "tn": 11, "description": "Read the scene, behavior, tracks or obvious details."},
    "investigate": {"label": "Investigate", "emoji": "🔎", "attribute": "insight", "tn": 14, "description": "Search for concealed clues, causes, mechanisms or evidence."},
    "influence": {"label": "Influence", "emoji": "🗣️", "attribute": "presence", "tn": 14, "description": "Persuade, intimidate, negotiate or shape someone's response."},
    "stealth": {"label": "Stealth", "emoji": "🌫️", "attribute": "agility", "tn": 14, "description": "Hide, shadow, infiltrate or move without drawing attention."},
    "physical": {"label": "Physical Feat", "emoji": "💪", "attribute": "body", "tn": 14, "description": "Climb, force, endure, lift, leap or overcome a physical obstacle."},
    "qi": {"label": "Qi Control", "emoji": "✨", "attribute": "spirit", "tn": 14, "description": "Manipulate qi carefully without using a formal combat technique."},
    "resolve": {"label": "Resolve", "emoji": "🧘", "attribute": "will", "tn": 14, "description": "Resist fear, pain, pressure, temptation or spiritual strain."},
    "aid": {"label": "Aid", "emoji": "🤝", "attribute": "presence", "tn": 11, "description": "Help, reassure, coordinate or support someone in the scene."},
}


def _scene_player_target_id(target: str) -> int | None:
    text = str(target or "")
    if not text.startswith("Player ") or ":" not in text:
        return None
    raw = text[7:].split(":", 1)[0].strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


async def _scene_action_targets(character: dict[str, Any]) -> list[str]:
    wt = await current_world_time()
    targets: list[str] = []
    location = str(character.get("location") or "")
    for npc_name in WORLD.npcs:
        if await current_npc_location(npc_name, wt.period) == location:
            targets.append(npc_name)
    player_rows = await DB.get_characters_at_location(
        location, exclude_user_id=int(character.get("user_id") or 0) or None
    )
    for row in player_rows:
        targets.append(f"Player {int(row['user_id'])}: {str(row.get('name') or 'Cultivator')}"[:100])
    return targets[:20]


async def _resolve_scene_action(
    interaction: discord.Interaction, *, action_key: str, target: str, detail: str,
    character: dict[str, Any] | None = None,
) -> None:
    await interaction.response.defer(ephemeral=False)
    c = character or await require_character(interaction)
    if not c:
        return
    profile = SCENE_ACTION_TYPES.get(action_key)
    if not profile:
        await reply_long(interaction, "That scene action is no longer available.", ephemeral=False)
        return
    target = str(target or "Environment")
    player_target_id = _scene_player_target_id(target)
    if target not in {"Environment", "Self"}:
        if player_target_id is not None:
            rows = await DB.get_characters_at_location(
                str(c.get("location") or ""), exclude_user_id=interaction.user.id
            )
            if not any(int(row.get("user_id") or 0) == player_target_id for row in rows):
                await reply_long(interaction, "That cultivator is not present in your current scene.", ephemeral=False)
                return
        else:
            wt = await current_world_time()
            if await current_npc_location(target, wt.period) != c.get("location"):
                await reply_long(interaction, "That NPC is not present in your current scene.", ephemeral=False)
                return
    detail = str(detail).strip()
    if not detail:
        await reply_long(interaction, "Describe how your character attempts the action.", ephemeral=False)
        return

    # A self-directed action completes automatically because the character is
    # acting on their own known state. This is not an information-discovery
    # bypass: hidden canonical facts remain outside the narrator context.
    # Every external/scene target still uses a transparent canonical roll.
    # Settle time-based effects, then let Go derive the canonical attribute,
    # target number, RNG, and degree. Discord only validates presentation/world
    # presence and narrates the committed fixed result.
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "scene.action",
            interaction.user.id,
            {
                "action_key": action_key,
                "target": target,
                "detail": detail,
                
            },
            action_id=f"discord:{interaction.id}:scene.action:{action_key}",
        )
    except GameEngineError as exc:
        await reply_long(interaction, f"Scene action could not be resolved: {exc}", ephemeral=False)
        return
    mechanics = dict(envelope.get("result") or {})
    attribute = str(mechanics.get("attribute") or profile["attribute"])
    tn = int(mechanics.get("tn", profile["tn"]))
    self_action = bool(mechanics.get("automatic", False))
    result = None if self_action else SimpleNamespace(**mechanics)
    if self_action:
        fixed = (
            f"Scene action: {profile['label']}\nTarget: Self\n"
            f"Attribute: {attribute.title()}\n"
            "Outcome: Automatic success — self-directed action.\n"
            "Disclosure boundary: describe only the character's visible, felt, remembered, or otherwise known state. "
            "Hidden canonical information remains concealed."
        )
    else:
        fixed = (
            f"Scene action: {profile['label']}\nTarget: {target}\n"
            f"Attribute: {attribute.title()}\n{roll_line(result)}"
        )
    action_text = f"{profile['label']} — target: {target}. {detail}"
    channel_id = interaction.channel_id or 0
    await DB.add_history(channel_id, user_id=interaction.user.id, speaker=c["name"], content=action_text)
    history = await DB.get_history(channel_id, 20)
    context = await NARRATOR_CONTEXT.build(
        c, scene_type=f"structured_scene_action:{action_key}", query_text=action_text,
        focus_npc=(target if target not in {"Environment", "Self"} and player_target_id is None else ""),
    )
    private_scene = None
    if interaction.guild is not None and isinstance(interaction.channel, discord.Thread):
        private_scene = await _private_scene_for_thread(interaction.guild, interaction.channel.id, interaction.user.id, c)
    keep_in_scene = bool(private_scene)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)
    try:
        narration = await NARRATOR_QUEUE.run(
            "scene_action",
            NARRATOR.narrate_action(
                character=c,
                action=action_text,
                history=history,
                fixed_roll=fixed,
                scene_context=context.text,
            ),
        )
    except Exception:
        log.exception("Structured scene-action narration failed")
        narration = (
            "The self-directed action succeeds, but reveals nothing beyond what the character can perceive or already knows."
            if self_action
            else "The fixed roll stands. No additional mechanical result was invented."
        )
    await DB.add_history(channel_id, user_id=None, speaker="World", content=narration)
    try:
        outcome_memory = "automatic self success" if self_action else ("success" if result.success else ("partial failure" if result.margin >= -3 else "failure"))
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="scene_action",
            salience=42 if (self_action or (result and result.success)) else 34,
            location=str(c.get("location") or ""),
            npc_name=(target if target not in {"Environment", "Self"} and player_target_id is None else ""),
            source="scene_action", game_minute=context.game_minute,
            summary=(
                f"{c.get('name','The player')} attempted {profile['label']} at {await character_location_display(c)} "
                f"targeting {target}: {detail[:300]} | Outcome: {outcome_memory}. "
                f"Observed response: {narration[:520]}"
            ),
        )
        wt_now = await current_world_time()
        await announce_quest_progress(interaction, await QUESTS.progress(interaction.user.id, "scene_action", amount=1, target=action_key, game_minute=wt_now.total_minutes))
    except Exception:
        log.exception("Quest progress update failed after Scene Action")
    if self_action:
        card_colour = 0x57F287
        result_text = (
            "✅ **Automatic success — self-directed action.**\n"
            "This does not reveal hidden canonical information."
        )
        result_footer = "Automatic self-action • Narration remains limited to character-known information"
    elif result.success:
        card_colour = 0x57F287
        result_text = roll_line(result)
        result_footer = "Canonical roll resolved by game rules • AI narration cannot change mechanics"
    elif result.margin >= -3:
        card_colour = 0xF0A33E
        result_text = roll_line(result)
        result_footer = "Canonical roll resolved by game rules • AI narration cannot change mechanics"
    else:
        card_colour = 0xED4245
        result_text = roll_line(result)
        result_footer = "Canonical roll resolved by game rules • AI narration cannot change mechanics"
    embed = discord.Embed(
        title=f"{profile['emoji']} {profile['label']} → {target}"[:256],
        description=narration[:4096],
        color=card_colour,
    )
    embed.add_field(name="🎲 Result", value=result_text[:1024], inline=False)
    embed.add_field(name="🧠 Attribute", value=f"**{attribute.title()}**", inline=True)
    embed.add_field(name="🎯 Target", value=f"**{target[:180]}**", inline=True)
    embed.add_field(name="📝 Attempt", value=detail[:1024], inline=False)
    embed.set_footer(text=result_footer)
    await interaction.followup.send(embed=embed, ephemeral=False)


class SceneActionDetailModal(discord.ui.Modal):
    def __init__(self, *, action_key: str, target: str):
        profile = SCENE_ACTION_TYPES[action_key]
        super().__init__(title=f"{profile['label']} • {target}"[:45])
        self.action_key = action_key
        self.target = target
        self.detail = discord.ui.TextInput(
            label="How do you attempt it?",
            placeholder="Describe your method, words, movement or intent.",
            style=discord.TextStyle.paragraph,
            min_length=2,
            max_length=500,
        )
        self.add_item(self.detail)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _resolve_scene_action(
            interaction,
            action_key=self.action_key,
            target=self.target,
            detail=str(self.detail.value),
        )


class SceneActionTypeSelect(discord.ui.Select):
    def __init__(self, view: "SceneActionView"):
        self.scene_view = view
        super().__init__(
            placeholder="Choose what you are trying to do",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=str(profile["label"]),
                    value=key,
                    description=str(profile["description"])[:100],
                    emoji=str(profile["emoji"]),
                    default=key == view.action_key,
                )
                for key, profile in SCENE_ACTION_TYPES.items()
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.scene_view.action_key = self.values[0]
        self.scene_view.refresh_components()
        await interaction.response.edit_message(embed=self.scene_view.embed(), view=self.scene_view)


class SceneActionTargetSelect(discord.ui.Select):
    def __init__(self, view: "SceneActionView"):
        self.scene_view = view
        opts = [
            discord.SelectOption(label="Environment / Scene", value="Environment", emoji="🌍"),
            discord.SelectOption(label="Self", value="Self", emoji="🧘"),
        ]
        opts.extend(discord.SelectOption(label=name[:100], value=name[:100], emoji="👤") for name in view.npcs[:20])
        super().__init__(placeholder="Choose a target", min_values=1, max_values=1, options=opts[:25])

    async def callback(self, interaction: discord.Interaction) -> None:
        self.scene_view.target = self.values[0]
        self.scene_view.refresh_components()
        await interaction.response.edit_message(embed=self.scene_view.embed(), view=self.scene_view)


class SceneActionView(discord.ui.View):
    def __init__(self, owner_id: int, character: dict[str, Any], npcs: list[str], location_display: str | None = None):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.character = dict(character)
        self.npcs = list(npcs)
        self.action_key = "observe"
        self.target = "Environment"
        # embed() is synchronous and can't itself resolve abode:/sect_abode:/
        # personal_world:/birth_family: location keys into a place name (that needs a
        # DB lookup) - callers precompute it with character_location_display() and pass
        # it in. Falls back to the raw value if a caller doesn't (defensive only).
        self.location_display = location_display if location_display is not None else str(character.get("location") or "Unknown")
        self.refresh_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Scene Action panel belongs to another cultivator.", ephemeral=False)
            return False
        return True

    def refresh_components(self) -> None:
        self.clear_items()
        self.add_item(SceneActionTypeSelect(self))
        self.add_item(SceneActionTargetSelect(self))
        button = discord.ui.Button(label="Describe & Resolve", style=discord.ButtonStyle.primary, emoji="🎭")
        async def run(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(SceneActionDetailModal(action_key=self.action_key, target=self.target))
        button.callback = run
        self.add_item(button)

    def embed(self) -> discord.Embed:
        profile = SCENE_ACTION_TYPES[self.action_key]
        self_target = self.target == "Self"
        embed = discord.Embed(
            title="🎭 Scene Action",
            description=(
                "Choose the kind of action and a target. The game engine selects the stat and difficulty, "
                "then the read-only AI narrator describes the fixed outcome. Self-directed actions succeed automatically but cannot "
                "reveal hidden information; other targets use a canonical roll."
            ),
            color=0x6D78A8,
        )
        embed.add_field(name="Action", value=f"{profile['emoji']} **{profile['label']}**\n{profile['description']}", inline=False)
        embed.add_field(name="Target", value=f"**{self.target}**", inline=True)
        check_text = (
            "**Automatic success**\nHidden information stays concealed"
            if self_target
            else f"**{str(profile['attribute']).title()}** vs **TN {profile['tn']}**"
        )
        embed.add_field(name="Check", value=check_text, inline=True)
        embed.set_footer(text=f"Current location: {self.location_display} • no reward/state change is invented by narration")
        return embed


# ---------------------------------------------------------------------------
# Scene Action panel wiring
# ---------------------------------------------------------------------------
# The panel itself lives in app/bot/scene_layout.py, which imports discord and
# nothing else from the project: that is what lets the whole component tree be
# built and counted in a build sandbox with no discord.py installed, so the
# 40-component budget is checked here rather than by a player hitting it.
# Everything game-specific is injected from this module.
async def _scene_reload_state(owner_id: int) -> dict[str, Any] | None:
    """Re-read one player's scene. Returning None leaves the panel as it was."""
    try:
        character = await DB.get_character(int(owner_id))
        if not character:
            return None
        return {
            "character": dict(character),
            "npcs": await _scene_action_targets(character),
            "location_display": await character_location_display(character),
        }
    except Exception:
        log.warning("Scene Action panel could not reload state", exc_info=True)
        return None


async def _scene_open_detail_modal(
    interaction: discord.Interaction, action_key: str, target: str
) -> None:
    await interaction.response.send_modal(
        SceneActionDetailModal(action_key=action_key, target=target)
    )


def scene_action_panel(
    owner_id: int,
    character: dict[str, Any],
    npcs: list[str],
    location_display: str,
    *,
    action_key: str = "observe",
) -> tuple[discord.ui.View, dict[str, Any]]:
    """Return ``(view, send kwargs)``.

    A Components V2 message cannot carry an embed and the classic fallback needs
    one, so the kwargs travel with the view. Four call sites used to build the
    send arguments themselves; returning both together is what stops the two
    paths drifting apart.
    """
    if scene_layout.LAYOUT_COMPONENTS_AVAILABLE:
        layout = scene_layout.SceneActionLayoutView(
            owner_id=owner_id,
            profiles=SCENE_ACTION_TYPES,
            character=character,
            npcs=npcs,
            location_display=location_display,
            reload_state=_scene_reload_state,
            open_modal=_scene_open_detail_modal,
            action_key=action_key,
        )
        return layout, {"view": layout}
    classic = SceneActionView(owner_id, character, npcs, location_display)
    if action_key in SCENE_ACTION_TYPES:
        classic.action_key = action_key
        classic.target = "Environment"
        classic.refresh_components()
    return classic, {"embed": classic.embed(), "view": classic}


@registered_root_command(name="action", description="Open the guided Scene Action panel", guild=GUILD)
async def scene_action_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    npcs = await _scene_action_targets(c)
    _view, panel_kwargs = scene_action_panel(
        interaction.user.id, c, npcs, await character_location_display(c)
    )

    target_thread: discord.Thread | None = None
    if interaction.guild is not None and isinstance(interaction.channel, discord.Thread):
        scene = await _private_scene_for_thread(interaction.guild, interaction.channel.id, interaction.user.id, c)
        if scene:
            target_thread = interaction.channel
    if target_thread is None:
        target_thread = await active_private_location_thread(interaction, c)
    if target_thread is None and not str(c.get("location") or "").startswith(
        ("personal_world:", "birth_family:", "sect_abode:", "abode:")
    ):
        # If active_private_location_thread() above couldn't resolve/create the
        # right private thread for one of these prefixes (a missing DB row, a
        # deleted Discord thread, a lost permission), the correct fallback is
        # the public panel below - never the expedition journal. A character
        # standing in a private residence isn't "exploring the world", and
        # routing them there anyway just reproduces the world-catalog lookup
        # failure a moment later when they try to actually use it.
        target_thread = await ensure_expedition_thread(interaction, c)

    if target_thread is not None:
        try:
            panel = await target_thread.send(**panel_kwargs)
            await interaction.response.send_message(
                f"🎭 Your Scene Action panel is open in {target_thread.mention}: {panel.jump_url}", ephemeral=False
            )
            return
        except discord.HTTPException:
            log.exception("Could not post Scene Action panel to private scene thread")
    await interaction.response.send_message(ephemeral=False, **panel_kwargs)


@registered_root_command(name="npcinfo", description="Show public NPC information, current schedule location, and known family notes", guild=GUILD)
@app_commands.autocomplete(npc=local_npc_autocomplete)
async def npc_info_command(interaction: discord.Interaction, npc: str) -> None:
    data = await DB.get_npc_definition(npc)
    if not data:
        await interaction.response.send_message("Unknown NPC.", ephemeral=False)
        return
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    wt = await current_world_time()
    current_location = await current_npc_location(npc, wt.period) or data.get("location", "Unknown")
    if not await _location_is_visible(interaction.user.id, c, str(current_location)):
        await interaction.response.send_message("You have no reliable knowledge of that cultivator yet.", ephemeral=False)
        return
    lines = [
        f"👤 **{npc}**",
        f"Role: **{data.get('role', 'Unknown')}**",
        f"Public cultivation: **{data.get('realm', 'Unknown')}**" + (f" Stage {data.get('stage')}" if data.get('stage') else ""),
        f"Current location ({wt.period}): **{current_location}**",
    ]
    sim_state = await SIM.npc_status(npc) or {}
    if sim_state.get("activity"):
        lines.append(f"Current activity: **{sim_state.get('activity')}**")
    mood = public_mood_hint(str(sim_state.get("mood") or ""))
    if mood:
        lines.append(f"Outward demeanor: **{mood.title()}**")
    relationship = await NPC_RELATIONSHIPS.get(interaction.user.id, npc)
    if int(relationship.get("encounter_count", 0)) > 0:
        lines.append(
            f"Your relationship: **{NPC_RELATIONSHIPS.public_label(relationship)}** "
            f"({int(relationship.get('encounter_count',0))} remembered encounters)"
        )
    if data.get("title"):
        lines.append(f"Title: **{data['title']}**")
    if data.get("public_family"):
        lines.append(f"Public family/court notes: {data['public_family']}")
    if data.get("hidden_master"):
        lines.append("Hidden state: **Not publicly verifiable. Spiritual Sense may or may not reveal more.**")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


scene_group = app_commands.Group(name="scene", description="Inspect the current roleplay scene and in-world time")


@registered_group_command(scene_group, name="status", description="Show scene time, location rules and NPCs currently present")
async def scene_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt=await current_world_time()
    loc=await DB.get_location_definition(c["location"]) or WORLD.locations.get(c["location"],{})
    local=[]
    for name in WORLD.npcs:
        if await current_npc_location(name,wt.period)==c["location"]:
            local.append(name)
    lines=[
        f"🎭 **Scene — {await character_location_display(c)}**",
        f"Time: **{wt.display}**",
        f"World: **{loc.get('world','Mortal World')}**",
        f"Protection: **{'Protected / no violence' if loc.get('safe_zone') else 'No absolute protection'}**",
        f"NPCs present: {', '.join(local) if local else 'none currently visible'}",
    ]
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


# Phase 8 (v0.19.43) routed EventSceneView to these through the registry;
# phase 9e moved the bindings here with the panel helpers.
EVENT_HANDLERS.register("scene_action_targets", _scene_action_targets)
# Typed play (v0.21.1) resolves "> I sneak past the guards" through the same
# function the /action panel's detail modal submits to - by name, so bot.py
# and typed_play.py stay below this module in the import graph.
EVENT_HANDLERS.register("scene_action_resolve", _resolve_scene_action)


async def _scene_action_panel_handler(*args: Any, **kwargs: Any):
    return scene_action_panel(*args, **kwargs)


EVENT_HANDLERS.register("scene_action_panel", _scene_action_panel_handler)
