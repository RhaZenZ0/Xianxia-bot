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
from ..rules.worldtime import from_game_minutes
from .runtime import DB, ENGINE, WORLD, log
from .services import SIM

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
            path=str(character.get("path", "")),
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


async def announce_quest_progress(interaction: Any, changed: list[dict[str, Any]]) -> None:
    """Tell the player what QUESTS.progress() just did - a completed quest used
    to flip to `completed` in silence (v0.20.6). Best effort: never raises."""
    lines = []
    for row in changed or ():
        title = str(row.get("title") or row.get("quest_key"))
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
            else:
                lines.append(f"📜 **Quest complete: {title}**" + (" — " + ", ".join(parts) if parts else ""))
        else:
            lines.append(f"📜 Quest progress: **{title}**")
    if not lines:
        return
    try:
        if interaction.response.is_done():
            await interaction.followup.send("\n".join(lines), ephemeral=False)
        else:
            await interaction.response.send_message("\n".join(lines), ephemeral=False)
    except Exception:
        log.exception("Could not announce quest progress")
