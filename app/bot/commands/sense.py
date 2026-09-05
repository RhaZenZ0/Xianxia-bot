"""Perception and world information: sense, conceal, check, world,
worldevents, time, rulers, worldrules.

Split phase 9e (v0.19.48, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...rules.sense import hidden_npc_names
from ...rules.worldtime import cultivation_cycle_summary
from ..character_state import current_effect_modifiers
from ..formatting import human_duration, roll_line
from ..locations import _known_locations, _world_is_unlocked, current_npc_location, local_npc_autocomplete
from ..registry import registered_root_command
from ..runtime import (
    DB,
    ENGINE,
    SETTINGS,
    WORLD,
    authoritative_lifespan,
    character_location_display,
    current_world_time,
    log,
    reply_long,
    require_character,
    serialized_user_action,
)
from ..services import GUILD, NARRATOR, NARRATOR_CONTEXT


ATTRIBUTE_CHOICES = [
    app_commands.Choice(name="Body", value="body"),
    app_commands.Choice(name="Agility", value="agility"),
    app_commands.Choice(name="Spirit", value="spirit"),
    app_commands.Choice(name="Insight", value="insight"),
    app_commands.Choice(name="Will", value="will"),
    app_commands.Choice(name="Presence", value="presence"),
]
DIFFICULTY_CHOICES = [
    app_commands.Choice(name="Easy (11)", value=11),
    app_commands.Choice(name="Standard (14)", value=14),
    app_commands.Choice(name="Hard (17)", value=17),
    app_commands.Choice(name="Severe (20)", value=20),
]



@registered_root_command(
    name="sense",
    description="Use Spiritual Sense on yourself, another cultivator, an NPC, or the surrounding area",
    guild=GUILD,
)
@app_commands.autocomplete(npc=local_npc_autocomplete)
async def sense_command(
    interaction: discord.Interaction,
    target: discord.Member | None = None,
    npc: str | None = None,
    area: bool = False,
) -> None:
    c = await require_character(interaction)
    if not c:
        return

    selected = int(target is not None) + int(bool(npc)) + int(area)
    if selected > 1:
        await interaction.response.send_message(
            "Choose only one sense target: another player, an NPC, or `area:true`.",
            ephemeral=False,
        )
        return

    wt = await current_world_time()

    # No target = personal Spiritual Sense status. Derived sense/concealment
    # values are calculated by Go from canonical state.
    if selected == 0 or (target is not None and target.id == interaction.user.id):
        try:
            status = dict(await ENGINE.action(
                "sense.status", interaction.user.id, {},
            ) or {})
        except GameEngineError as exc:
            await interaction.response.send_message(f"Spiritual Sense status could not resolve: {exc}", ephemeral=False)
            return
        await interaction.response.send_message(
            "🔍 **Spiritual Sense**\n"
            f"Power: **{int(status.get('power', 0))}**\n"
            f"Precision: **{int(status.get('precision', 0))}**\n"
            f"Range: **{int(status.get('range_m', 0)):,} m**\n"
            f"Aura concealment: **{'Active' if status.get('concealment_active') else 'Off'}**\n"
            f"Current concealment strength: **{int(status.get('concealment_strength', 0))}**\n\n"
            "Power penetrates concealment, Precision controls how much detail you can identify, and Range controls area scans.",
            ephemeral=False,
        )
        return

    if target is not None:
        tc = await DB.get_character(target.id)
        if not tc:
            await interaction.response.send_message(
                f"{target.mention} does not have a cultivation character yet.", ephemeral=False
            )
            return
        try:
            envelope = await ENGINE.authoritative_action(
                "sense.inspect", interaction.user.id,
                {"mode": "player", "target_user_id": target.id},
                action_id=f"discord:{interaction.id}:sense.inspect:player:{target.id}",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Spiritual Sense could not resolve: {exc}", ephemeral=False)
            return
        sensed = dict(envelope.get("result") or {})
        roll = SimpleNamespace(**dict(sensed.get("roll") or {}))
        precision = dict(sensed.get("precision") or {})
        precision_tier = str(precision.get("tier", "failure"))
        detection_tier = str(sensed.get("detection_tier", "failure"))
        reveal = str(sensed.get("reveal", "none"))
        if reveal == "none":
            reading = (
                "Their aura slips away from your probing sense; you cannot obtain a reliable cultivation reading."
                if sensed.get("target_concealed")
                else "You detect spiritual activity, but its depth remains unclear."
            )
        elif reveal == "world":
            reading = f"You estimate that this cultivator belongs to **{WORLD.realm_world(int(sensed.get('target_realm_index', 0)))}** power."
        elif reveal == "realm":
            reading = f"Main cultivation appears to be **{WORLD.realm_name(int(sensed.get('target_realm_index', 0)), tc.get('gender'))}**."
        elif reveal == "approx":
            reading = (
                "Main cultivation: **"
                + WORLD.approximate_realm(
                    int(sensed.get("target_realm_index", 0)), int(sensed.get("target_phase", 1)),
                    precision_tier=precision_tier, gender=tc.get("gender"),
                )
                + "**."
            )
        else:
            body_name = WORLD.body_realm_name(int(tc.get("body_realm_index", 0)), tc.get("gender"))
            reading = (
                f"Main cultivation: **{WORLD.realm_name(int(sensed.get('target_realm_index', 0)), tc.get('gender'))} — Stage {int(sensed.get('target_phase', 1))}**.\n"
                f"Body cultivation: **{body_name} — Stage {tc.get('body_phase', 1)}**."
            )
        await interaction.response.send_message(
            f"🔍 **Spiritual Sense — {tc['name']}**\n{roll_line(roll)}\n"
            f"Precision: **{int(precision.get('total', 0))}** vs detail TN **{int(precision.get('tn', 0))}** — **{precision_tier.replace('_', ' ').title()}**\n\n"
            f"{reading}",
            ephemeral=False,
        )
        return

    if npc:
        if npc not in WORLD.npcs:
            await interaction.response.send_message("Unknown NPC.", ephemeral=False)
            return
        npc_location = await current_npc_location(npc, wt.period)
        if npc_location and npc_location != c.get("location"):
            await interaction.response.send_message(
                f"**{npc}** is not currently present at **{await character_location_display(c)}**.", ephemeral=False
            )
            return
        try:
            envelope = await ENGINE.authoritative_action(
                "sense.inspect", interaction.user.id,
                {"mode": "npc", "npc_name": npc},
                action_id=f"discord:{interaction.id}:sense.inspect:npc:{npc}",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Spiritual Sense could not resolve: {exc}", ephemeral=False)
            return
        sensed = dict(envelope.get("result") or {})
        roll = SimpleNamespace(**dict(sensed.get("roll") or {}))
        precision = dict(sensed.get("precision") or {})
        lines = [f"🔍 **Spiritual Sense — {npc}**", roll_line(roll)]
        if precision:
            precision_tier = str(precision.get("tier", "failure"))
            lines.append(
                f"Precision: **{int(precision.get('total', 0))}** vs detail TN **{int(precision.get('tn', 0))}** — "
                f"**{precision_tier.replace('_', ' ').title()}**"
            )
        lines.append("")
        lines.append(str(sensed.get("reading") or "You cannot obtain a stable reading from their aura."))
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return

    # Area sweep. Python supplies only which hidden NPCs are physically present;
    # the still-unmigrated autonomous world simulation owns that movement. Go
    # owns perception RNG, thresholds, detail, hints, and secret-safe signals.
    present_hidden: list[str] = []
    for hidden_name in hidden_npc_names(WORLD.hidden_masters):
        if await current_npc_location(hidden_name, wt.period) == c["location"]:
            present_hidden.append(hidden_name)
    try:
        envelope = await ENGINE.authoritative_action(
            "sense.inspect", interaction.user.id,
            {
                "mode": "area",
                "present_hidden_npcs": present_hidden,
                
            },
            action_id=f"discord:{interaction.id}:sense.inspect:area",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Spiritual Sense sweep could not resolve: {exc}", ephemeral=False)
        return
    sensed = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(sensed.get("roll") or {}))
    area_precision = dict(sensed.get("precision") or {})
    precision_tier = str(area_precision.get("tier", "failure"))
    lines = [
        f"🌌 **Spiritual Sense Sweep — {sensed.get('location') or await character_location_display(c)}**",
        roll_line(roll),
        f"Precision: **{int(area_precision.get('total', 0))}** vs detail TN **{int(area_precision.get('tn', 0))}** — "
        f"**{precision_tier.replace('_', ' ').title()}**",
        f"Effective range: **{int(sensed.get('range_m', 0)):,} m**",
    ]
    hints = list(sensed.get("hints") or [])
    if hints:
        lines.extend(f"• {hint}" for hint in hints)
    elif not bool(getattr(roll, "success", False)):
        lines.append("• Environmental interference prevents a clean sweep.")

    if bool(sensed.get("event_visibility")):
        active_events = await DB.get_active_world_events(c["location"])
        for event in active_events[:3]:
            if event["event_type"] == "secret_realm":
                lines.append(f"• A strong **spatial distortion** is present: {event['title']}.")
            else:
                lines.append(f"• Abnormal spiritual currents match the active phenomenon **{event['title']}**.")

    for signal in sensed.get("hidden_signals", []):
        if signal == "artificial":
            lines.append("• One nearby aura feels **artificially projected or inconsistent**.")
        elif signal == "concealed":
            lines.append("• One nearby presence is **far too perfectly concealed to feel ordinary**.")

    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_root_command(name="conceal", description="Turn your cultivation-aura concealment on or off", guild=GUILD)
@serialized_user_action
async def conceal_command(interaction: discord.Interaction, active: bool) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "sense.conceal", interaction.user.id,
            {"active": active},
            action_id=f"discord:{interaction.id}:sense.conceal",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Aura concealment could not change: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    strength = int(result.get("concealment_strength", 0))
    await interaction.followup.send(
        f"{'🌑' if active else '✨'} Aura concealment **{'enabled' if active else 'disabled'}**.\n"
        f"Current concealment strength: **{strength}**.\n"
        "Concealment suppresses your readable aura; it does not make you physically invisible.",
        ephemeral=False,
    )


@registered_root_command(name="check", description="Make a transparent RP skill check with no automatic reward", guild=GUILD)
@app_commands.choices(attribute=ATTRIBUTE_CHOICES, difficulty=DIFFICULTY_CHOICES)
async def check(
    interaction: discord.Interaction,
    attribute: app_commands.Choice[str],
    difficulty: app_commands.Choice[int],
    action: str,
) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    # Effect settlement remains adapter orchestration for now; Go owns the
    # effective attribute calculation, d10 rolls, target comparison, and result.
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "check.resolve",
            interaction.user.id,
            {
                "attribute": attribute.value,
                "tn": difficulty.value,
                "label": action,
                
            },
            action_id=f"discord:{interaction.id}:check.resolve",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Check could not be resolved: {exc}", ephemeral=False)
        return
    result = SimpleNamespace(**dict(envelope.get("result") or {}))
    fixed = f"Action: {action}\n{roll_line(result)}"
    channel_id = interaction.channel_id or 0
    await DB.add_history(
        channel_id,
        user_id=interaction.user.id,
        speaker=c["name"],
        content=action,
    )
    history = await DB.get_history(channel_id, 24)
    lineage_context = await DB.describe_lineage_context(interaction.user.id)
    social_context = (await NARRATOR_CONTEXT.build(
        c, scene_type="freeform roleplay", lineage_context=lineage_context,
    )).text
    try:
        narration = await NARRATOR.narrate_action(
            character=c,
            action=action,
            history=history,
            fixed_roll=fixed,
            social_context=social_context,
            scene_context=social_context,
        )
    except Exception:
        log.exception("Check narration failed")
        narration = "The roll stands as shown; no mechanical rewards or losses are applied."
    await DB.add_history(channel_id, user_id=None, speaker="World", content=narration)
    await reply_long(interaction, f"{roll_line(result)}\n\n{narration}")


@registered_root_command(name="world", description="Show the current world and locations you have actually discovered", guild=GUILD)
async def world(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    known = await _known_locations(interaction.user.id, c)
    current_world = WORLD.realm_world(int(c.get("realm_index", 0)))
    lines = [f"🌍 **Known World — {current_world}**", f"Current location: **{await character_location_display(c)}**"]
    visible = []
    for name in sorted(known):
        data = WORLD.locations.get(name)
        if not data or not _world_is_unlocked(c, str(data.get("world") or current_world)):
            continue
        visible.append((name, data))
    for name, data in visible:
        marker = "📍" if name == c.get("location") else "🧭"
        lines.append(f"\n{marker} **{name}** — {data['description']}")
    wt = await current_world_time()
    nearby = []
    for npc_name in WORLD.npcs:
        if await current_npc_location(npc_name, wt.period) == c.get("location"):
            nearby.append(npc_name)
    if nearby:
        lines.append("\n**NPCs currently here:** " + ", ".join(nearby[:20]))
    lines.append("\n\nExplore known regions to discover additional routes. Higher worlds and their inhabitants remain hidden until your cultivation reaches them.")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_root_command(name="worldevents", description="Show categorized active phenomena, consequences, and secret realms", guild=GUILD)
async def worldevents(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    known = await _known_locations(interaction.user.id, c)
    events = [
        event for event in await DB.get_active_world_events()
        if str(event.get("location")) in known
        and _world_is_unlocked(c, str((WORLD.locations.get(str(event.get("location"))) or {}).get("world") or WORLD.realm_world(int(c.get("realm_index",0)))))
    ]
    if not events:
        await interaction.response.send_message("The world is unusually quiet. No major phenomena are active.", ephemeral=False)
        return
    now = time.time()
    lines = ["🌌 **Active World Events**"]
    for event in events:
        remain = human_duration(int(event["ends_at"] - now))
        thread_ref = f" — scene <#{event['thread_id']}>" if event.get("thread_id") else ""
        if event["event_type"] == "secret_realm":
            realm_id = event["payload"]["realm_id"]
            realm = WORLD.secret_realms[realm_id]
            category=str(event["payload"].get("category") or "Secret Realm")
            lines.append(
                f"\n🌀 **{realm['name']}** — {event['location']} — **{category}** — closes in **{remain}**{thread_ref}\n"
                f"{realm['description']}"
            )
        else:
            payload=dict(event.get("payload") or {})
            category=str(payload.get("category") or "Phenomenon")
            severity=max(1,min(10,int(payload.get("severity",1))))
            consequence=str(payload.get("consequence_text") or "").strip()
            lines.append(
                f"\n⚡ **{event['title']}** — {event['location']} — **{category}**, severity **{severity}/10** — ends in **{remain}**{thread_ref}"
                + (f"\n{consequence}" if consequence else "")
            )
    await reply_long(interaction, "".join(lines), ephemeral=False)



















@registered_root_command(name="time", description="Show the canonical in-world cultivation calendar", guild=GUILD)
async def world_time_command(interaction: discord.Interaction) -> None:
    wt = await current_world_time()
    c = await DB.get_character(interaction.user.id)
    cycle = cultivation_cycle_summary(wt, c.get("spiritual_root") if c else None)
    age_line=""
    if c:
        life=await authoritative_lifespan(interaction.user.id)
        age_line = (f"\nYour age: **{life.age_years:.1f} years** • lifespan: **Ageless**" if life.ageless else f"\nYour age: **{life.age_years:.1f} years** • lifespan ceiling: **{life.total_years} years**")
    await interaction.response.send_message(
        f"🕰️ **World Time**\n{wt.display}\n"
        f"Automatic rate: **{SETTINGS.world_time_scale} game minutes per real minute**.{age_line}\n\n"
        f"**Current Cultivation Flow**\n{cycle}",
        ephemeral=False,
    )


@registered_root_command(name="rulers", description="Show rulers of realm worlds your cultivation can currently perceive", guild=GUILD)
async def rulers_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    lines = ["👑 **Recognized Rulers of Known Worlds**"]
    for world, npc_name in WORLD.world_rulers.items():
        if not _world_is_unlocked(c, world):
            continue
        npc = WORLD.npcs.get(npc_name, {})
        lines.append(
            f"\n**{world}** — {npc_name}"
            f"\n{npc.get('title', npc.get('role', 'Ruler'))} • {npc.get('realm', 'Unknown')} Stage {npc.get('stage', '?')}"
            f"\nSeat: {npc.get('location', 'Unknown')}"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_root_command(name="worldrules",description="Show the laws governing NPC, sect, family and forbidden-art reactions",guild=GUILD)
async def world_rules_command(interaction:discord.Interaction)->None:
    rules=WORLD.world_rules
    lines=["⚖️ **Living World Rules**",str(rules.get('forbidden_arts',{}).get('description','Forbidden arts carry social and karmic consequences.'))]
    for title,key in (("NPC rules","npc_principles"),("Sect rules","sect_principles"),("Family rules","family_principles")):
        lines.append(f"\n**{title}**")
        lines.extend(f"• {x}" for x in rules.get(key,[]))
    await reply_long(interaction,"\n".join(lines),ephemeral=False)
