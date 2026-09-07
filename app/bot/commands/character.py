"""The character: /begin, sheet, dashboards, quests, inventory, identity roots (lifespan, karma, soul, afterlife, reincarnate, dao heart, reputation, grudges, provenance, era, effects), /fate and /bond.

Split phase 9b (v0.19.45, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands

from ...rules import commissions as commission_rules
from ...rules.advanced_runtime import ERA_CYCLE
from ...rules.birthfamily import family_tier_name, karma_description, karma_label
from ...rules.fate import fate_label
from ...ops.game_engine import GameEngineError
from ...simulation import MINUTES_PER_DAY
from ...rules.worldtime import MINUTES_PER_YEAR
from ..channels import configured_begin_channel
from ..character_state import current_effect_modifiers
from ..formatting import human_duration, player_property_emoji, player_property_facility_lines
from ..registry import registered_group_command, registered_root_command
from ..runtime import (
    _explain_engine_error,
    DB,
    ENGINE,
    GENDER_CHOICES,
    SETTINGS,
    WORLD,
    authoritative_lifespan,
    character_location_display,
    current_world_time,
    log,
    player_property_label,
    reply_long,
    require_character,
    serialized_user_action,
)
from ..ui.commissions import AbandonCommissionView, abandon_warning
from ..ui.creation import BirthFamilyView
from ..services import COMMISSIONS, GUILD, NPC_RELATIONSHIPS, QUESTS, SCENES

@registered_root_command(name="begin", description="Choose a family, cultivation style, and create your cultivator", guild=GUILD)
async def begin(interaction: discord.Interaction) -> None:
    existing = await DB.get_character(interaction.user.id)
    if existing:
        await interaction.response.send_message(
            "You already have a character. Use **/character → Overview**.", ephemeral=True
        )
        return

    begin_ch = await configured_begin_channel(interaction.guild)
    if begin_ch is not None:
        in_begin_channel = interaction.channel_id == begin_ch.id
        if isinstance(interaction.channel, discord.Thread):
            in_begin_channel = in_begin_channel or interaction.channel.parent_id == begin_ch.id
        if not in_begin_channel:
            await interaction.response.send_message(
                f"🌱 Character creation begins in {begin_ch.mention}. Use **/begin** there.", ephemeral=True
            )
            return

    wt = await current_world_time()
    try:
        offer_envelope = await ENGINE.authoritative_action(
            "character.family_options",
            interaction.user.id,
            {"world_name": "Mortal World"},
            action_id=f"discord:{interaction.id}:character.family_options",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ Could not generate canonical birth families: {exc}", ephemeral=True)
        return
    offer_result = dict(offer_envelope.get("result") or {})
    families = [dict(row) for row in offer_result.get("families", [])]
    if not families:
        await interaction.response.send_message("No canonical birth families are available. Try **/begin** again.", ephemeral=True)
        return
    view = BirthFamilyView(interaction.user.id, families, int(offer_envelope.get("state_version", 0)))
    await interaction.response.send_message(
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
    )


@registered_root_command(name="gender", description="Set Male or Female for gendered realm titles and forms of address", guild=GUILD)
@app_commands.choices(gender=GENDER_CHOICES)
async def set_gender(interaction: discord.Interaction, gender: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    # Ack first: the write is authoritative now, and an interaction token that
    # expires before the first reply would have the player click again on a
    # change that already landed.
    await interaction.response.defer(ephemeral=False)
    try:
        await ENGINE.authoritative_action(
            "character.set_gender", interaction.user.id, {"gender": gender.value},
            action_id=f"discord:{interaction.id}:character.set_gender",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Character sex could not be set: {exc}", ephemeral=False)
        return
    main_name = WORLD.realm_name(c["realm_index"], gender.value)
    body_name = WORLD.body_realm_name(c.get("body_realm_index", 0), gender.value)
    await interaction.followup.send(
        f"✅ Character sex set to **{gender.name}**.\n"
        f"Qi realm title: **{main_name}**\nBody realm title: **{body_name}**\n"
        "This changes titles/names only; it never changes stats, rolls, or progression.",
        ephemeral=False,
    )


@registered_root_command(name="sheet", description="View your cultivation character", guild=GUILD)
async def sheet(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    realm = WORLD.realm_name(c["realm_index"], c.get("gender"))
    cost = WORLD.phase_cost(c["realm_index"], c["phase"])
    body_realm = WORLD.body_realm_name(c.get("body_realm_index", 0), c.get("gender"))
    body_cost = WORLD.body_phase_cost(c.get("body_realm_index", 0), c.get("body_phase", 1))
    perfection = await DB.get_perfection(interaction.user.id, c["realm_index"])
    body_perfection = await DB.get_body_perfection(interaction.user.id, c.get("body_realm_index", 0))
    inheritances = await DB.get_inheritances(interaction.user.id)
    membership = await DB.get_sect_membership(interaction.user.id)
    master = await DB.get_master(interaction.user.id)
    wallet = await DB.get_wallet(interaction.user.id)
    birth_family = await DB.get_birth_family(interaction.user.id)
    laws = await DB.get_law_progress(interaction.user.id)
    abode = await DB.get_abode(interaction.user.id)
    soul_legacy = await DB.get_soul_legacy(interaction.user.id)
    aptitudes = await DB.get_aptitudes(interaction.user.id)
    a = c["attributes"]
    wt = await current_world_time()
    life = await authoritative_lifespan(interaction.user.id)
    embed = discord.Embed(title=f"{c['name']} — {realm}", description=c["concept"][:4096])
    embed.add_field(
        name="Qi Cultivation",
        value=f"**{realm} • Stage {c['phase']}**\nEssence {c['cultivation']} / {cost}",
        inline=True,
    )
    embed.add_field(
        name="Body Cultivation",
        value=f"**{body_realm} • Stage {c.get('body_phase', 1)}**\nEssence {c.get('body_cultivation', 0)} / {body_cost}",
        inline=True,
    )
    root_profile = aptitudes.get("root") or {}
    root_elements = "/".join(str(x) for x in root_profile.get("elements", [c["spiritual_root"]]))
    root_mutation = str(root_profile.get("mutation") or "")
    mutation_name = WORLD.spiritual_root_system.get("mutations", {}).get(root_mutation, {}).get("name")
    root_text = (
        f"**{root_profile.get('grade','Common')}** • {root_elements}\n"
        f"Purity {int(root_profile.get('purity',50))}% • Stability {int(root_profile.get('stability',100))}%"
    )
    if mutation_name:
        root_text += f"\nMutation: **{mutation_name}**"
    embed.add_field(name="Spiritual Root", value=root_text, inline=True)
    embed.add_field(name="Path", value=c["path"], inline=True)
    location_display=await character_location_display(c)
    embed.add_field(name="Location", value=location_display, inline=True)
    wallet_lines=[f"{WORLD.currency_name(cid)}: **{int(balance):,}**" for cid,balance in wallet.items() if int(balance)>0]
    embed.add_field(name="Wallet", value="\n".join(wallet_lines[:6]) if wallet_lines else "Empty", inline=True)
    embed.add_field(
        name="Resources",
        value=(
            f"❤️ Vitality **{c['vitality']}/{c['vitality_max']}**\n"
            f"💠 Qi **{c['qi']}/{c['qi_max']}**\n"
            f"✨ Insight XP **{c['insight_xp']}**"
        ),
        inline=True,
    )
    if life.ageless:
        life_text = f"Age **{life.age_years:.1f}** • **Ageless by cultivation**"
    else:
        life_text = (
            f"Age **{life.age_years:.1f}** / **{life.total_years} years**\n"
            f"Natural {life.natural_years} + cultivation {life.cultivation_bonus_years} + medicine {life.extension_years}"
        )
    if getattr(life, "aging_paused", False):
        paused_years = float(getattr(life, "paused_game_minutes", 0) or 0) / float(MINUTES_PER_YEAR)
        life_text += f"\n⏸️ **Inactive aging paused** • {paused_years:,.1f} game-years protected"
    if c.get("life_status") == "deceased":
        life_text += "\n🕯️ **Deceased — lifespan exhausted**"
    embed.add_field(name="Age & Lifespan", value=life_text, inline=False)
    embed.add_field(
        name="Identity & Legacy",
        value=(
            f"Sex **{(c.get('gender') if c.get('gender') in {'male','female'} else 'Not set').title()}**\n"
            f"Inheritances **{len(inheritances)}**\n"
            f"Karma **{karma_label(int(c.get('karma_score',0)))}** ({int(c.get('karma_score',0)):+d})"
        ),
        inline=True,
    )
    if c.get("life_status") == "alive":
        try:
            sense_stats = dict(await ENGINE.action(
                "sense.status", interaction.user.id, {},
            ) or {})
            sense_text = (
                f"Power **{int(sense_stats.get('power', 0))}** • Precision **{int(sense_stats.get('precision', 0))}**\n"
                f"Range **{int(sense_stats.get('range_m', 0)):,} m** • Concealment **{'Active' if sense_stats.get('concealment_active') else 'Off'}**"
            )
        except GameEngineError:
            sense_text = "Spiritual Sense data is temporarily unavailable."
    else:
        sense_text = "Dormant while the soul turns through Samsara."
    embed.add_field(name="Spiritual Sense", value=sense_text, inline=False)
    if WORLD.dual_resonance_active(c):
        embed.add_field(
            name="☯ Dual Cultivation Resonance",
            value="**Active** — Qi and Body cultivation are exactly aligned. +10% training gains and +1 breakthrough/combat checks.",
            inline=False,
        )
    if membership:
        sect_value = (
            f"{membership['sect_name']} • {membership['rank_name']}"
            f"\nContribution: {membership.get('contribution_points', 0)} • Influence: {membership.get('influence', 0)}"
        )
        if master:
            sect_value += f"\nMaster: {master['name']}"
        embed.add_field(name="Sect", value=sect_value, inline=False)
    if birth_family:
        embed.add_field(
            name="Birth Family",
            value=(
                f"{birth_family['family_name']} • **{family_tier_name(int(birth_family.get('tier',1)))}**\n"
                f"Wealth {birth_family.get('wealth',0)} • Influence {birth_family.get('influence',0)} • Stability {birth_family.get('stability',0)}\n"
                f"Family head: {birth_family.get('head_title','Family Head')} {birth_family.get('head_name','Unknown')}"
            ),
            inline=False,
        )
    bloodline = aptitudes.get("bloodline")
    if bloodline:
        techniques = list(bloodline.get("unlocked_techniques") or [])
        embed.add_field(
            name="Bloodline",
            value=(
                f"**{bloodline.get('name')}** • {str(bloodline.get('state','dormant')).title()}\n"
                f"Purity {int(bloodline.get('purity',0))}% • Stage {int(bloodline.get('evolution_stage',0))} • "
                f"Rejection {int(bloodline.get('rejection',0))}%"
                + (f"\nTechniques: {', '.join(str(x) for x in techniques[:3])}" if techniques else "")
            ),
            inline=False,
        )
    physique = aptitudes.get("physique") or {}
    embed.add_field(
        name="Physique",
        value=(
            f"**{physique.get('name','Ordinary Mortal Body')}** • {str(physique.get('state','ordinary')).title()}\n"
            f"Stage {int(physique.get('evolution_stage',0))} • Stability {int(physique.get('stability',100))}% • "
            f"Instability {int(physique.get('instability',0))}%"
        ),
        inline=False,
    )
    if int(soul_legacy.get("incarnation_count",1)) > 1 or int(soul_legacy.get("legacy_points",0)) > 0:
        trait=str(soul_legacy.get("special_trait") or "None")
        embed.add_field(
            name="☸️ Soul Legacy",
            value=(
                f"Incarnation **{int(soul_legacy.get('incarnation_count',1))}** • Legacy **{int(soul_legacy.get('legacy_points',0))}**\n"
                f"Memory {int(soul_legacy.get('awakened_memory',0))}/{int(soul_legacy.get('memory_seed',0))}% • Talent Echo {int(soul_legacy.get('talent_echo',0))}% • Law Echo {int(soul_legacy.get('law_echo',0))}%\n"
                f"Trait: **{trait}**"
            ),
            inline=False,
        )
    if laws:
        top=laws[0]; definition=WORLD.law_definition(str(top['law_id'])) or {}; stage=WORLD.law_stage(int(top['comprehension']))
        embed.add_field(name="Law Comprehension",value=f"{definition.get('name', top['law_id'])} • **{stage['name']}** • {top['comprehension']}%",inline=False)
    if abode:
        embed.add_field(name="Player Property",value=f"{player_property_emoji(abode)} {abode['name']} • {player_property_label(abode)} • Entrance: {abode['base_location']}\n" + " • ".join(player_property_facility_lines(abode)[:6]),inline=False)
    active_effects, _, _effect_wt = await current_effect_modifiers(interaction.user.id)
    if active_effects:
        effect_names = ", ".join(str(e.get("name", e.get("effect_key", "Effect"))) for e in active_effects[:5])
        if len(active_effects) > 5:
            effect_names += f" +{len(active_effects)-5} more"
        embed.add_field(name="Active Effects", value=effect_names, inline=False)
    embed.add_field(name="World Time", value=wt.display, inline=False)
    if c["phase"] == 9:
        if perfection and perfection["completed"]:
            perfect_text = "★ Perfect Realm achieved — your foundation carries permanent bonuses."
        elif perfection and perfection["active"]:
            perfect_text = (
                f"Perfection **{perfection['progress']}%** • Training {perfection['training_progress']}/{WORLD.perfection_training_cap()}\n"
                f"Quests {perfection['completed_quests']}/{WORLD.perfection_quest_count()} • use **/quest → Realm Perfection → Info**"
            )
        else:
            perfect_text = "Stage 9 choice unlocked: **/quest → Realm Perfection → Start** or **/quest → Main Progression → Breakthrough** to skip perfection."
        embed.add_field(name="Realm Perfection", value=perfect_text, inline=False)
    if c.get("body_phase", 1) == 9:
        if body_perfection and body_perfection["completed"]:
            body_perfect_text = "★ Perfect Body Realm achieved — your physical foundation carries permanent bonuses."
        elif body_perfection and body_perfection["active"]:
            body_perfect_text = (
                f"Perfection **{body_perfection['progress']}%** • Training {body_perfection['training_progress']}/{WORLD.body_perfection_training_cap()}\n"
                f"Quests {body_perfection['completed_quests']}/{WORLD.body_perfection_quest_count()} • use **/quest → Body Perfection → Info**"
            )
        else:
            body_perfect_text = "Body Stage 9 choice unlocked: **/quest → Body Perfection → Start** or **/cultivation → Body Cultivation → Breakthrough**."
        embed.add_field(name="Body Perfection", value=body_perfect_text, inline=False)

    embed.add_field(
        name="Attributes",
        value=(
            f"Body {a['body']} • Agility {a['agility']} • Spirit {a['spirit']}\n"
            f"Insight {a['insight']} • Will {a['will']} • Presence {a['presence']}"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Origin: {c['origin']}")
    await interaction.response.send_message(embed=embed)


async def _player_dashboard_embed(user_id: int, *, guild_id: int | None, page: str = "overview") -> discord.Embed:
    c = await DB.get_character(int(user_id))
    if not c:
        return discord.Embed(title="🧑 Cultivator Dashboard", description="No character exists yet. Use `/begin`.")
    scene = await SCENES.current(int(user_id), guild_id=guild_id)
    membership = await DB.get_sect_membership(int(user_id))
    abode = await DB.get_abode(int(user_id))
    sect_abode = await DB.get_sect_abode(int(user_id))
    active_quests = await DB.list_character_quests(int(user_id), status="active")
    realm = WORLD.realm_name(int(c.get("realm_index", 0)), c.get("gender"))
    embed = discord.Embed(title=f"🧑 {c['name']} — Player Dashboard", description=f"**{realm} • Stage {c.get('phase',1)}**")

    if page == "scene":
        if scene:
            embed.add_field(name="Physical Location", value=scene.physical_location, inline=False)
            embed.add_field(name="Active Scene", value=f"**{scene.scene_label}**\nType: `{scene.scene_type}`", inline=False)
            embed.add_field(name="Discord Scene", value=(f"<#{scene.channel_id}>" if scene.channel_id else "No dedicated scene thread"), inline=False)
        embed.set_footer(text="Physical location controls travel/access. Active scene controls local RP routing.")
        return embed

    if page == "relationships":
        rows = await DB.list_npc_relationships(int(user_id), 8)
        if not rows:
            embed.description = "No persistent NPC relationships have developed yet. Talk to named NPCs in the world."
        else:
            for row in rows[:8]:
                label = NPC_RELATIONSHIPS.public_label(row)
                embed.add_field(
                    name=f"🤝 {row['npc_name']} — {label}",
                    value=(f"Trust {int(row['trust']):+d} • Respect {int(row['respect']):+d} • Fear {int(row['fear']):+d}\n"
                           f"Debt {int(row['debt']):+d} • Grudge {int(row['grudge']):+d} • Encounters {int(row['encounter_count'])}"),
                    inline=False,
                )
        return embed

    if page == "quests":
        available = await QUESTS.available(int(user_id))
        catalog = await QUESTS.catalog()
        if active_quests:
            for row in active_quests[:8]:
                definition = catalog.get(str(row["quest_key"]), {})
                progress = row.get("progress") or {}
                parts = []
                for obj in definition.get("objectives", []):
                    cur = int(progress.get(str(obj["id"]), 0)); req = max(1, int(obj.get("count",1)))
                    parts.append(f"{'✅' if cur >= req else '▫️'} {obj.get('label',obj['id'])} **{cur}/{req}**")
                embed.add_field(name=f"📜 {definition.get('title', row['quest_key'])}", value="\n".join(parts) or "In progress", inline=False)
        else:
            embed.add_field(name="Active Quests", value="None", inline=False)
        embed.add_field(name="Available", value="\n".join(f"• {q['title']}" for q in available[:8]) or "None", inline=False)
        return embed

    scene_text = scene.scene_label if scene else str(c.get("location") or "Unknown")
    embed.add_field(name="📍 Current Scene", value=scene_text, inline=True)
    embed.add_field(name="✨ Resources", value=f"Vitality **{c.get('vitality',0)}/{c.get('vitality_max',0)}**\nQi **{c.get('qi',0)}/{c.get('qi_max',0)}**", inline=True)
    embed.add_field(name="🧭 Cultivation Path", value=str(c.get("path") or "Unknown"), inline=True)
    embed.add_field(name="🏯 Sect", value=(f"{membership['sect_name']} • {membership['rank_name']}" if membership else "Unaffiliated"), inline=False)
    home = abode or sect_abode
    embed.add_field(name="🏡 Residence", value=(str(home.get("name")) if home else "None established"), inline=True)
    embed.add_field(name="📜 Active Quests", value=str(len(active_quests)), inline=True)
    embed.set_footer(text="Use the buttons below for Scene, Relationships and Quests.")
    return embed


class PlayerDashboardView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int | None) -> None:
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id) if guild_id is not None else None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This dashboard belongs to another cultivator.", ephemeral=False)
            return False
        return True

    async def _show(self, interaction: discord.Interaction, page: str) -> None:
        await interaction.response.edit_message(embed=await _player_dashboard_embed(self.user_id, guild_id=self.guild_id, page=page), view=self)

    @discord.ui.button(label="Overview", emoji="🧑", style=discord.ButtonStyle.primary)
    async def overview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "overview")

    @discord.ui.button(label="Scene", emoji="📍", style=discord.ButtonStyle.secondary)
    async def scene(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "scene")

    @discord.ui.button(label="Relationships", emoji="🤝", style=discord.ButtonStyle.secondary)
    async def relationships(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "relationships")

    @discord.ui.button(label="Quests", emoji="📜", style=discord.ButtonStyle.secondary)
    async def quests(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "quests")


@registered_root_command(name="me", description="Open your player dashboard", guild=GUILD)
async def player_dashboard(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    view = PlayerDashboardView(interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(
        embed=await _player_dashboard_embed(interaction.user.id, guild_id=interaction.guild_id),
        view=view,
        ephemeral=False,
    )


class QuestAcceptSelect(discord.ui.Select):
    def __init__(self, user_id: int, available: list[dict]) -> None:
        self.user_id = int(user_id)
        super().__init__(
            placeholder="Accept an available quest…", min_values=1, max_values=1,
            options=[discord.SelectOption(label=str(q["title"])[:100], value=str(q["quest_key"]), description=str(q.get("description", ""))[:100]) for q in available[:25]],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This quest panel belongs to another cultivator.", ephemeral=False); return
        try:
            row = await QUESTS.accept(
                self.user_id, self.values[0],
                action_id=f"discord:{interaction.id}:commission.accept",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False); return
        definition = await QUESTS.definition(str(row["quest_key"])) or {}
        await interaction.response.send_message(f"📜 Quest accepted: **{definition.get('title', row['quest_key'])}**", ephemeral=False)


class AbandonCommissionOpenButton(discord.ui.Button):
    """The danger button lives behind a confirmation that states the cost, so
    the journal itself can never abandon anything in one click."""

    def __init__(self, user_id: int, held: dict) -> None:
        super().__init__(label="Abandon commission", style=discord.ButtonStyle.danger, emoji="⚠️")
        self.user_id = int(user_id)
        self.held = dict(held)

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This quest panel belongs to another cultivator.", ephemeral=False, delete_after=20)
            return
        await interaction.response.send_message(
            abandon_warning(self.held),
            view=AbandonCommissionView(self.user_id, str(self.held.get("quest_key") or "")),
            ephemeral=False,
        )


class QuestDashboardView(discord.ui.View):
    def __init__(self, user_id: int, available: list[dict], held: dict | None = None) -> None:
        super().__init__(timeout=300)
        if available:
            self.add_item(QuestAcceptSelect(user_id, available))
        if held:
            self.add_item(AbandonCommissionOpenButton(user_id, held))


@registered_root_command(name="quests", description="View and accept objective-driven quests", guild=GUILD)
async def quests_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    active = await DB.list_character_quests(interaction.user.id, status="active")
    available = await QUESTS.available(interaction.user.id)
    held = await COMMISSIONS.held(interaction.user.id)
    wt = await current_world_time()
    lines = ["📜 **Quest Journal**"]
    catalog = await QUESTS.visible_catalog(interaction.user.id)
    if active:
        for row in active[:10]:
            definition = catalog.get(str(row["quest_key"]), {})
            progress = row.get("progress") or {}
            objectives = []
            for obj in definition.get("objectives", []):
                cur=int(progress.get(str(obj["id"]),0)); req=max(1,int(obj.get("count",1)))
                objectives.append(f"{'✅' if cur >= req else '▫️'} {obj.get('label',obj['id'])} {cur}/{req}")
            title = definition.get("title", row["quest_key"])
            # A commission is marked, and carries its clock: a deadline you
            # cannot see is a deadline you will miss.
            if int(row.get("commission", 0) or 0):
                giver = str(definition.get("giver_npc") or "")
                due = row.get("deadline_game_minute")
                clock = (f" · due in {commission_rules.format_wait_days(int(due) - wt.total_minutes)}" if due else "")
                title = f"📜 {title} — for {giver}{clock}" if giver else f"📜 {title}{clock}"
            lines.append(f"\n**{title}**\n" + "\n".join(objectives))
    else:
        lines.append("\nNo active quests.")
    if available:
        lines.append("\n**Available**\n" + "\n".join(f"• {q['title']}" for q in available[:10]))
    if held:
        lines.append("\n-# Commissions come from the people who give them. "
                     "Abandoning one costs the same standing as failing it.")
    await interaction.response.send_message(
        "\n".join(lines), view=QuestDashboardView(interaction.user.id, available, held), ephemeral=False)


@registered_root_command(name="inventory", description="View your items and materials", guild=GUILD)
async def inventory(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    inv = await DB.get_inventory(interaction.user.id)
    if not inv:
        text = "Your storage pouch is empty."
    else:
        lines = []
        for item_id, qty in inv.items():
            item = WORLD.items.get(item_id, {"name": item_id, "description": ""})
            lines.append(f"**{item['name']}** x{qty} — {item['description']}")
        text = "\n".join(lines)
    await interaction.response.send_message(
        f"**{c['name']}'s Carried Inventory**\nLow Spirit Stones: **{c['spirit_stones']}**\n\n{text}",
        ephemeral=False,
    )


@registered_root_command(name="inheritances", description="View the ancient inheritances your character has obtained", guild=GUILD)
async def inheritances(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    owned = await DB.get_inheritances(interaction.user.id)
    if not owned:
        await interaction.response.send_message("You have not obtained an ancient inheritance yet.", ephemeral=False)
        return
    lines = [f"📜 **{c['name']}'s Inheritances**"]
    for row in owned:
        info = WORLD.inheritances.get(row["inheritance_id"], {})
        lines.append(f"\n**{info.get('name', row['inheritance_id'])}**\n{info.get('description', '')}")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_root_command(name="effects", description="View active buffs, debuffs, curses and other generic effects", guild=GUILD)
async def effects_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    effects, _, wt = await current_effect_modifiers(interaction.user.id)
    if not effects:
        await interaction.response.send_message("You have no active timed or persistent effects.", ephemeral=False)
        return
    lines = [f"✨ **Active Effects — {c['name']}**"]
    for effect in effects:
        ends = effect.get("ends_game_minute")
        remaining = "Permanent" if ends is None else f"{max(0, int(ends)-wt.total_minutes)} game minutes remaining"
        lines.append(f"\n**{effect.get('name', effect.get('effect_key', 'Effect'))}** — {remaining}")
        if effect.get("description"):
            lines.append(str(effect["description"]))
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_root_command(name="reputation", description="View persistent faction and social reputation", guild=GUILD)
async def reputation_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_reputations(interaction.user.id)
    if not rows:
        await interaction.response.send_message("📜 No faction has formed a strong recorded opinion of this incarnation yet.", ephemeral=False)
        return
    lines = [f"📜 **Reputation — {c['name']}**"]
    for row in rows:
        score = int(row.get("score", 0))
        label = "Revered" if score >= 70 else "Trusted" if score >= 30 else "Known" if score > -30 else "Distrusted" if score > -70 else "Hated"
        lines.append(f"\n**{row['faction_key']}**: **{score:+d}** ({label})" + (f"\nLast cause: {row.get('last_reason')}" if row.get('last_reason') else ""))
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_root_command(name="grudges", description="View persistent personal, family and faction grudges against you", guild=GUILD)
async def grudges_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_grudges(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🗡️ No active grudge record is attached to this incarnation.", ephemeral=False)
        return
    lines = [f"🗡️ **Active Grudges — {c['name']}**"]
    for row in rows:
        lines.append(f"\n**{row['holder_key']}** ({row['holder_type']}) • Intensity **{row['intensity']}/10**\n{row.get('reason','')}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_root_command(name="daoheart", description="View Dao-heart stability, public face and sworn internal commitments", guild=GUILD)
async def daoheart_command(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    state=await DB.get_social_state(interaction.user.id)
    await interaction.response.send_message(f"☯️ **Dao Heart — {c['name']}**\nDao heart **{state['dao_heart']}/100** • Stability **{state['dao_stability']}/100** • Face **{state['face']:+d}**\nVow: {state['vow'] or 'None'}\nObsession: {state['obsession'] or 'None'}",ephemeral=False)


@registered_root_command(name="provenance", description="Inspect ownership marks, legality and tracking on one carried item", guild=GUILD)
async def provenance_command(interaction: discord.Interaction, item: str) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_item_provenance(interaction.user.id,item)
    if not rows:
        await interaction.response.send_message("No provenance record exists for that item stack yet.",ephemeral=False);return
    lines=[f"🔎 **Provenance — {WORLD.item_name(item)}**"]
    for r in rows[:10]: lines.append(f"\n`#{r['provenance_id']}` {r['source_type']}:{r['source_key']} • **{r['legal_status']}** • authenticity {r['authenticity']}% • tracking {r['tracking_strength']}%"+(f" • mark `{r['ownership_mark']}`" if r.get('ownership_mark') else ""))
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_root_command(name="era", description="View the active era, automatic cycle timing and recent transition events", guild=GUILD)
async def era_command(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time(); era=await DB.get_current_era()
    if not era:
        await interaction.response.send_message("No active world era is recorded.",ephemeral=False);return
    mods=", ".join(f"{k}={v}" for k,v in (era.get('modifiers') or {}).items()) or "baseline laws"
    template=next((x for x in ERA_CYCLE if x['name']==era['name']),None)
    remaining=max(0,int(template['duration_days'])*MINUTES_PER_DAY-(wt.total_minutes-int(era['started_game_minute']))) if template else 0
    events=await DB.get_world_era_events(limit=5)
    lines=[f"🌌 **{era['name']}**",str(era.get('description','')),f"Started at game minute **{era['started_game_minute']}** • automatic transition in about **{remaining/MINUTES_PER_DAY:.1f} world-days**",f"Modifiers: **{mods}**"]
    if events:
        lines.append("\n**Recent Era Events**")
        for e in events: lines.append(f"• {e['title']} — game minute {e['game_minute']}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@registered_root_command(name="specialeffects",description="Inspect active special, Law, curse, control, and domain effects",guild=GUILD)
async def special_effects_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    effects,_,wt=await current_effect_modifiers(interaction.user.id); special=[e for e in effects if e.get('special') or e.get('category') not in {None,'General'}]
    if not special: await interaction.response.send_message("No special effects are currently affecting you.",ephemeral=False);return
    lines=[f"✨ **Special Effects — {c['name']}**"]
    for e in special:
        remain='Permanent' if e.get('ends_game_minute') is None else f"{max(0,int(e['ends_game_minute'])-wt.total_minutes)} game minutes"
        lines.append(f"\n**{e.get('name')}** • {e.get('category','Special')} • Severity {e.get('severity',0)} • {remain}\n{e.get('description','')}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_root_command(name="lifespan",description="View your age, realm lifespan range and remaining longevity",guild=GUILD)
async def lifespan_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    wt=await current_world_time(); life=await authoritative_lifespan(interaction.user.id)
    realm=WORLD.realm_name(int(c.get('realm_index',0))); phase=int(c.get('phase',1))
    if life.ageless:
        ceiling="**Ageless / no natural-aging death**"
        range_line="True Immortal-tier longevity has transcended ordinary mortal aging."
    else:
        ceiling=f"**{int(life.total_years or 0):,} years**"
        if life.realm_floor_years is not None:
            range_line=f"Realm range: **{life.realm_floor_years:,}–{life.realm_ceiling_years:,} years**; Stage {phase} determines where you sit inside that range."
        else:
            range_line="Realm range: mortal baseline."
    remain=("∞" if life.remaining_years is None else f"{life.remaining_years:,.1f} years")
    pause_line = ""
    if getattr(life, "aging_paused", False):
        paused_years = float(getattr(life, "paused_game_minutes", 0) or 0) / float(MINUTES_PER_YEAR)
        pause_line = (
            f"\n⏸️ Inactivity protection is active. **{paused_years:,.1f} game-years** "
            "of biological aging are currently excluded."
        )
    await interaction.response.send_message(
        f"🕯️ **Lifespan — {c['name']}**\n"
        f"Realm: **{realm}, Stage {phase}**\n"
        f"Age: **{life.age_years:,.1f} years**\n"
        f"Natural mortal lifespan: **{life.natural_years:,} years**\n"
        f"Cultivation/body longevity contribution: **+{life.cultivation_bonus_years:,} years**\n"
        f"Life-extension medicines/herbs: **+{life.extension_years:,} years**\n"
        f"Current lifespan ceiling: {ceiling}\n"
        f"Remaining: **{remain}**{pause_line}\n\n{range_line}\n\n"
        "Natural longevity does not protect against combat death, tribulations, curses, soul destruction, or explicit life-draining techniques.",
        ephemeral=False,
    )


@registered_root_command(name="karma",description="View your Good/Evil alignment and karmic reputation",guild=GUILD)
async def karma_status_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    score=int(c.get('karma_score',0))
    await interaction.response.send_message(
        f"☯️ **Karmic Alignment — {c['name']}**\n"
        f"Path: **{karma_label(score)}**\nScore: **{score:+d} / 1000**\n\n{karma_description(score)}\n\n"
        "Karma is changed by canonical quests, choices, crimes, mercy, betrayals and GM/world events — not by repeatedly self-reporting actions.",
        ephemeral=False,
    )


fate_group = app_commands.Group(name="fate", description="Inspect the providence you can spend to resist a fatal turn")


@registered_group_command(fate_group, name="status", description="View your spendable Fate reserve and lifetime fortune")
async def fate_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    state = await DB.get_fate(interaction.user.id)
    points = int(state.get("points", 0))
    await interaction.response.send_message(
        f"🧧 **Fate — {c['name']}**\n"
        f"Reserve: **{points}/9** • {fate_label(points)}\n"
        f"Lifetime earned: **{int(state.get('lifetime_earned',0))}** • spent: **{int(state.get('lifetime_spent',0))}**\n\n"
        "Fate is distinct from Karma. Rare fortuitous events, meaningful mercy and cleared heavenly tribulations can grant it. "
        "If an ordinary battle would cause **true death**, one Fate is automatically burned to twist the fatal outcome into survival with a severe injury.",
        ephemeral=False,
    )


@registered_group_command(fate_group, name="history", description="Review recent Fate gains and expenditures")
async def fate_history(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    rows = await DB.get_fate_ledger(interaction.user.id, limit=12)
    if not rows:
        await interaction.response.send_message("🧧 No Fate has been gained or spent by this incarnation yet.", ephemeral=False)
        return
    lines = [f"🧧 **Recent Fate — {c['name']}**"]
    for row in rows:
        lines.append(
            f"\n• **{int(row['delta']):+d}** → {int(row['balance_after'])}/9"
            + (f" — {row.get('reason')}" if row.get('reason') else "")
        )
    await reply_long(interaction, "".join(lines), ephemeral=False)


bond_group = app_commands.Group(name="bond", description="Form a consensual Dao partnership and cultivate together")


@registered_group_command(bond_group, name="status", description="View your pending or active Dao partnership")
async def bond_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    bond=await DB.get_dao_partnership(interaction.user.id)
    if not bond:
        await interaction.response.send_message("💞 You have no pending or active Dao partnership.",ephemeral=False);return
    await interaction.response.send_message(
        f"💞 **Dao Partnership — {c['name']}**\nPartner: **{bond['partner_name']}** • Status: **{str(bond['status']).title()}**\n"
        f"Dual-Cultivation Resonance: **{int(bond.get('resonance',0))}/100** • Sessions: **{int(bond.get('dual_sessions',0))}**\n"
        "An active high-resonance bond leaves a small **Partner Echo** in Samsara if this incarnation dies.",ephemeral=False
    )


@registered_group_command(bond_group, name="propose", description="Invite another cultivator into a consensual Dao partnership")
@serialized_user_action
async def bond_propose(interaction:discord.Interaction,partner:discord.Member)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.propose",interaction.user.id,{"partner_user_id":partner.id},action_id=f"discord:{interaction.id}:dao.propose")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"💞 Dao-partnership proposal **#{result.get('partnership_id')}** sent to {partner.mention}.",ephemeral=False)


@registered_group_command(bond_group, name="respond", description="Accept or reject a Dao-partnership proposal addressed to you")
@app_commands.choices(decision=[app_commands.Choice(name="Accept",value="accept"),app_commands.Choice(name="Reject",value="reject")])
@serialized_user_action
async def bond_respond(interaction:discord.Interaction,partnership_id:int,decision:app_commands.Choice[str])->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.respond",interaction.user.id,{"partnership_id":int(partnership_id),"accept":decision.value=='accept'},action_id=f"discord:{interaction.id}:dao.respond")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send("💞 Dao partnership response recorded.",ephemeral=False)


@registered_group_command(bond_group, name="dual_cultivate", description="Cultivate with your accepted Dao partner while both are at the same location")
@serialized_user_action
async def bond_dual_cultivate(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.dual_cultivate",interaction.user.id,{"cooldown_seconds":SETTINGS.cultivate_cooldown_minutes*60},action_id=f"discord:{interaction.id}:dao.dual_cultivate")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"☯️ Paired meridians resonate at **{result.get('location','your shared location')}**.\nBond Resonance: **{int(result.get('resonance',0))}/100**",ephemeral=False)


@registered_group_command(bond_group, name="sever", description="End your active Dao partnership")
@serialized_user_action
async def bond_sever(interaction:discord.Interaction,confirm:bool=False)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.sever",interaction.user.id,{},action_id=f"discord:{interaction.id}:dao.sever")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send("🧵 Your Dao partnership is severed.",ephemeral=False)


@registered_root_command(name="soul",description="View your reincarnation history and persistent Soul Legacy",guild=GUILD)
async def soul_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    legacy=await DB.get_soul_legacy(interaction.user.id)
    trait=str(legacy.get("special_trait") or "None")
    past=list(legacy.get("past_lives") or [])
    awakened=int(legacy.get("awakened_memory",0))
    memory=int(legacy.get("memory_seed",0))
    visible_count=0 if memory<=0 or awakened<=0 else min(len(past), max(1, (awakened * len(past)) // max(1,memory)))
    lines=[
        f"☸️ **Soul Record — {c['name']}**",
        f"Incarnation: **{int(legacy.get('incarnation_count',1))}**",
        f"Soul Legacy Points: **{int(legacy.get('legacy_points',0))}**",
        f"Memory Seed: **{memory}%** • Awakened: **{awakened}%**",
        f"Talent Echo: **{int(legacy.get('talent_echo',0))}%**",
        f"Law Echo: **{int(legacy.get('law_echo',0))}%**",
        f"Karmic Fortune: **{int(legacy.get('karmic_fortune',0)):+d}**",
        f"Samsara Trait: **{trait}**",
        f"Past lives recorded: **{len(past)}**",
    ]
    if visible_count>0:
        lines.append("\n**Awakened past-life echoes**")
        for life in past[-visible_count:]:
            lines.append(f"• **{life.get('name','Unknown')}** — {life.get('path') or 'Unknown Path'}; karma {int(life.get('karma',0)):+d}; death: {life.get('death_reason','unknown')}")
    elif past:
        lines.append("\nYour previous incarnations remain sealed. Breakthroughs and deep Law insights can awaken fragments.")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_root_command(name="afterlife",description="View your Samsara cycle after true death",guild=GUILD)
async def afterlife_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    if c.get("life_status")!="deceased":
        await interaction.response.send_message("You are alive; no Samsara cycle is active.",ephemeral=False);return
    try:
        state=dict(await ENGINE.action(
            "lifecycle.samsara_status",interaction.user.id,{"minutes_per_year":MINUTES_PER_YEAR},
        ) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"No reincarnation path is currently recorded for this soul. ({exc})",ephemeral=False);return
    old_family=await DB.get_birth_family(interaction.user.id) or {}
    progress=min(1.0,float(state.get('samsara_years_elapsed',0))/max(1.0,float(state.get('samsara_years_target',1))))
    passed=int(int(state.get('samsara_lives_count',0))*progress)
    lines=[
        f"☸️ **Samsara — {c['name']}**",
        f"Previous family: **{old_family.get('family_name','Unknown')}** (historical only; not fast-forwarded)",
        f"Private soul-time: **{float(state.get('samsara_years_elapsed',0)):.2f} / {float(state.get('samsara_years_target',0)):.2f} years**",
        f"Lives passed through the wheel: **{passed:,} / {int(state.get('samsara_lives_count',0)):,}**",
        ("Reincarnation: **READY — use /reincarnate**" if state.get("ready") else f"Real time remaining: **{human_duration(int(state.get('seconds_remaining',0)))}**"),
        f"Playable rebirth destination: **{str(state.get('target_world') or 'Mortal World')}**",
        "Shared world time advanced: **0 extra minutes** because of your death.",
        f"Memory Seed: **{int(state.get('memory_retention',0))}%**",
        f"Talent Echo: **{int(state.get('talent_retention',0))}%**",
        f"Partner Echo: **{int(state.get('partner_echo',0))}%**" + (f" from **{state.get('partner_name')}**" if state.get('partner_name') else ""),
        f"Law Echo: **{int(state.get('comprehension_retention',0))}%**",
        f"Soul Legacy Points from this life: **{int(state.get('legacy_points',0))}**",
    ]
    if state.get("special_trait"):
        lines.append(f"Potential Samsara Trait: **{state.get('special_trait')}**")
    echoes=list(state.get("samsara_history") or [])[-5:]
    if echoes:
        lines.append("\n**Notable Samsara echoes**")
        lines.extend(f"• {entry}" for entry in echoes)
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_root_command(name="reincarnate",description="Complete Samsara and be born again in the world chosen by the wheel",guild=GUILD)
@app_commands.choices(gender=GENDER_CHOICES)
@serialized_user_action
async def reincarnate(interaction:discord.Interaction,name:str,path:str,gender:app_commands.Choice[str])->None:
    c=await DB.get_character(interaction.user.id)
    if not c:
        await interaction.response.send_message("Create your first incarnation with **/begin**.",ephemeral=False);return
    if c.get('life_status')!='deceased':
        await interaction.response.send_message("Your current incarnation is still alive.",ephemeral=False);return
    try:
        state=dict(await ENGINE.action(
            "lifecycle.samsara_status",interaction.user.id,{"minutes_per_year":MINUTES_PER_YEAR},
        ) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"No Samsara cycle is recorded for this soul. ({exc})",ephemeral=False);return
    if not state.get("ready"):
        await interaction.response.send_message(
            f"☸️ Your soul is still turning through Samsara.\n"
            f"Private soul-time: **{float(state.get('samsara_years_elapsed',0)):.2f} / {float(state.get('samsara_years_target',0)):.2f} years**\n"
            f"Real time remaining: **{human_duration(int(state.get('seconds_remaining',0)))}**\n"
            "Your old family and the shared world are not being fast-forwarded.",ephemeral=False);return
    normalized_path=WORLD.normalize_path(path)
    if not normalized_path:
        await interaction.response.send_message("Unknown path. Choose: "+", ".join(WORLD.paths.keys()),ephemeral=False);return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "lifecycle.reincarnate",interaction.user.id,
            {"name":name.strip(),"path":normalized_path,"gender":gender.value},
            action_id=f"discord:{interaction.id}:lifecycle.reincarnate",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    partner_echo=max(0,min(25,int(result.get("partner_echo",0))))
    partner_name=str(result.get("partner_name") or "")
    lines=[
        "☸️ **Samsara turns. A new life begins.**",
        f"You awaken as **{name.strip()}**, generation **1** of **{result['family_name']}**.",
        f"World: **{result.get('target_world', state.get('target_world','Mortal World'))}**",
        f"Home: **{result.get('physical_location','Unknown')}** • Opening scene: **family household**",
        f"Age: **12** • Spiritual Root: **{result['spiritual_root']}** • Path: **{normalized_path}**",
        f"Incarnation: **{int(result.get('incarnation_count',2))}**",
        f"Soul Legacy Points: **{int(result.get('legacy_points',0))}**",
        f"Memory Seed: **{int(result.get('memory_retention',0))}%**",
        f"Talent Echo: **{int(result.get('talent_retention',0))}%**" + (f" + **{partner_echo}% Partner Echo** from {partner_name}" if partner_echo and partner_name else ""),
        f"Law Echo: **{int(result.get('comprehension_retention',0))}%**",
        "Your previous realm, Qi/Body cultivation, inventory, money, and direct Law progress are gone.",
        "Past-life memories are sealed and can awaken gradually through breakthroughs and deep comprehension.",
    ]
    if result.get("lineage_status"):
        lines.append(f"Lineage outcome: **{str(result['lineage_status']).replace('_',' ').title()}**")
    if result.get("lineage_summary"):
        lines.append(str(result["lineage_summary"]))
    if result.get("special_trait"):
        lines.append(f"🌌 Samsara Trait: **{result['special_trait']}**")
    try:
        await ENGINE.bootstrap_simulation(wt.total_minutes)
    except Exception:
        log.exception("Could not initialize Go-owned simulation bootstrap after reincarnation")
    await interaction.response.send_message("\n".join(lines))


