"""Per-character derived state: effect modifiers, pill toxicity, NPC memory.

Phase 4 of the main.py split (v0.19.39, docs/history/MAIN_SPLIT_PLAN.md).
current_effect_modifiers is read by eight command blocks, which is why it
lives here rather than with any one of them. Reads runtime, services and
app.*; never main.py. Definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

from ..rules.aptitudes import aptitude_effects
from ..rules.effects import aggregate_modifiers, normalize_effect_payload
from ..rules.npc_memory import classify_memory, scene_memory_summary
from ..rules.quests import next_objective_label
from ..rules.worldtime import from_game_minutes
from .runtime import DB, ENGINE, WORLD, log
from .services import QUESTS, SIM

async def current_effect_modifiers(user_id: int) -> tuple[list[dict], dict[str, float], object]:
    authority = dict(await ENGINE.action("effects.current", int(user_id), {}))
    wt = from_game_minutes(int(authority.get("game_minute", 0)))
    effects = await DB.get_active_effects(user_id, wt.total_minutes)
    effects = [
        effect for effect in effects
        if not (
            str(effect.get("effect_key", "")) == "pill_toxicity"
            and str(effect.get("source_type", "")) == "alchemy"
            and str(effect.get("source_id", "")) == "pill_toxicity"
        )
    ]
    toxicity_effect = authority.get("pill_toxicity_effect")
    if isinstance(toxicity_effect, dict):
        effects.append(dict(toxicity_effect))
    character = await DB.get_character(user_id)
    if character:
        aptitudes = await DB.get_aptitudes(user_id)
        effects.extend(aptitude_effects(
            aptitudes,
            root_system=WORLD.spiritual_root_system,
            bloodline_definitions=WORLD.bloodlines,
            physique_definitions=WORLD.physiques,
        ))
        deployed = await DB.get_active_location_array(str(character.get("location", "")), wt.total_minutes)
        if deployed:
            payload = normalize_effect_payload({
                "effect_key": f"location_array:{deployed.get('item_id','array')}",
                "name": str(deployed.get("name", "Deployed Formation")),
                "special": True,
                **dict(deployed.get("effect") or {}),
            })
            payload.update({
                "effect_key": f"location_array:{deployed.get('item_id','array')}",
                "source_type": "location_array",
                "source_id": str(deployed.get("location", "")),
                "stacks": 1,
                "starts_game_minute": int(deployed.get("starts_game_minute", 0)),
                "ends_game_minute": int(deployed.get("ends_game_minute", 0)),
            })
            effects.append(payload)
    return effects, aggregate_modifiers(effects), wt


def _npc_name_mentioned(text: str, npc_name: str) -> bool:
    haystack = " ".join(str(text or "").casefold().split())
    words = [w for w in str(npc_name or "").casefold().split() if w]
    if not haystack or not words:
        return False
    aliases = {" ".join(words)}
    if len(words) >= 2:
        aliases.add(" ".join(words[:2]))
        aliases.add(" ".join(words[-2:]))
    return any(len(alias) >= 4 and alias in haystack for alias in aliases)


async def _remember_freeform_npc_scene(
    *, user_id: int, character: dict[str, Any], player_text: str, narration: str, game_minute: int,
) -> None:
    """Store player-specific episodic memories for NPCs explicitly involved in freeform RP.

    This does not alter trust or other mechanical relationship values. It only
    lets a present named NPC remember an exchange later instead of resetting.
    """
    region = await SIM.civilization_status(str(character.get("location") or ""))
    if not region:
        return
    for row in (region.get("npcs") or [])[:8]:
        npc_name = str(row.get("npc_name") or "").strip()
        if not npc_name or not _npc_name_mentioned(player_text, npc_name):
            continue
        memory_kind, salience = classify_memory(player_text, narration)
        memory_id = await DB.add_npc_player_memory(
            int(user_id), npc_name, memory_kind=memory_kind,
            summary=scene_memory_summary(player_text, npc_name, narration),
            salience=salience, source="freeform", game_minute=int(game_minute),
        )
        # Enrich the trigger-mirrored unified RAG row with structured locality.
        await DB.add_rag_memory(
            int(user_id), source_key=f"npc:{memory_id}", memory_kind=memory_kind,
            summary=scene_memory_summary(player_text, npc_name, narration), salience=salience,
            location=str(character.get("location") or ""), npc_name=npc_name,
            source="freeform", game_minute=int(game_minute),
        )


def _item_label(item_id: str) -> str:
    """Name the item rather than printing its key at the player."""
    item = dict(WORLD.items.get(str(item_id)) or {})
    return str(item.get("name") or str(item_id).replace("_", " ").title())


async def record_quest_progress(user_id: int, objective_type: str, **kwargs: Any) -> list[dict[str, Any]]:
    """The record half of reporting an objective, separate from the telling
    (v1.0.5).

    Eight call sites wrote `await announce_quest_progress(interaction, await
    QUESTS.progress(...))` **after** the command's reply, each with a comment
    citing the rule from v1.0.0-rc.28. That rule is real, and it is about the
    *announcement*: `announce_quest_progress` falls back to
    `interaction.response.send_message` when the interaction has not been
    answered yet, so a reporter placed ahead of a command's only reply spends it
    on the quest line and the player never sees their craft roll.

    It says nothing about the record - and nesting the two made the record
    inherit the announcement's position. When `/craft`'s reply raised on a
    missing key (v1.0.3), the craft had already committed in the engine and the
    quest never advanced: six pills made, and an errand still reading zero.

    So the record goes first and the telling after. Splitting them is what makes
    the order visible; `test_a_quest_is_recorded_before_it_is_told.py` is what
    keeps it.

    This never raises. A quest that could not be advanced must not take the
    command's reply down with it - which is the same promise
    `announce_quest_progress` already makes about the other half.
    """
    try:
        return await QUESTS.progress(int(user_id), objective_type, **kwargs)
    except Exception:
        log.exception("Quest progress could not be recorded (%s)", objective_type)
        return []


async def announce_quest_progress(interaction: Any, changed: list[dict[str, Any]]) -> None:
    """Tell the player what QUESTS.progress() just did - a completed quest used
    to flip to `completed` in silence (v0.20.6). Best effort: never raises."""
    lines = []
    for row in changed or ():
        title = str(row.get("title") or row.get("quest_key"))
        if row.get("caught_up"):
            # A tutorial stage handed over by the catch-up (v1.2.0), which
            # used to be written into the result and read by nothing.
            lines.append(f"📜 **New quest: {title}**")
            first = next_objective_label(row.get("objectives"), {})
            if first:
                lines.append(f"-# Next: {first}")
            continue
        if row.get("just_completed"):
            rewards = row.get("rewards_granted") or {}
            parts = []
            if rewards.get("insight_xp"):
                parts.append(f"✨ {int(rewards['insight_xp'])} Insight XP")
            if rewards.get("spirit_stones"):
                parts.append(f"🪙 {int(rewards['spirit_stones'])} spirit stones")
            for item_id, qty in dict(rewards.get("items") or {}).items():
                parts.append(f"🎁 {_item_label(item_id)} ×{int(qty)}")
            commission = dict(row.get("commission") or {})
            if commission:
                # The payout line is the whole reveal for an undisclosed
                # commission, so it is stated in full even when it is a
                # disappointment - especially when it is a disappointment.
                giver = str(commission.get("giver_npc") or "")
                paid = ", ".join(parts) if parts else "nothing at all"
                lines.append(f"📜 **Commission complete: {title}**"
                             + (f" — {giver} pays: {paid}" if giver else f" — paid: {paid}"))
                standing = dict(commission.get("standing") or {})
                if standing:
                    lines.append(f"-# Standing with {giver or 'them'} rises.")
                # An outsider's work for a sect's gate (v1.1.0) pays standing
                # with the sect itself - the number its entrance trial and a
                # sponsor's roll both read. Stated from what the engine paid.
                earned = dict(commission.get("sect_standing") or {})
                if int(earned.get("delta") or 0) > 0:
                    lines.append(
                        f"🏯 Standing with the **{earned.get('sect')}** +{int(earned['delta'])} "
                        f"(now {int(earned.get('score') or 0)}) — its entrance trial comes easier: "
                        "**/sect → Recruitment → Trial** at its gate.")
            else:
                lines.append(f"📜 **Quest complete: {title}**" + (" — " + ", ".join(parts) if parts else ""))
            # A chained quest handed the next one over in the same engine
            # transaction (v1.0.0-rc.26). Saying so here is the point of the
            # chain: the player finishes something and is immediately told
            # what the next thing is, rather than being returned to sixteen
            # equally-weighted hubs.
            handed = dict(row.get("follow_on") or {})
            if handed:
                lines.append(f"📜 **New quest: {handed.get('title') or handed.get('quest_key')}**")
                first = next_objective_label(handed.get("objectives"), {})
                if first:
                    lines.append(f"-# Next: {first}")
        else:
            lines.append(f"📜 Quest progress: **{title}**")
            # What is still outstanding, which this used to leave the player to
            # work out. The labels are written as hub paths, so this names a
            # real command - and inside a hub panel `suggested_actions` turns
            # it into the button.
            terms = dict(row.get("terms") or {})
            outstanding = next_objective_label(terms.get("objectives"), row.get("progress"))
            if outstanding:
                lines.append(f"-# Next: {outstanding}")
    if not lines:
        return
    try:
        if interaction.response.is_done():
            await interaction.followup.send("\n".join(lines), ephemeral=False)
        else:
            await interaction.response.send_message("\n".join(lines), ephemeral=False)
    except Exception:
        log.exception("Could not announce quest progress")
