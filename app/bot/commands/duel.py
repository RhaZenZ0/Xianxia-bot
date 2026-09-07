"""The /duel hub: consent-gated nonlethal player-versus-player combat."""

from __future__ import annotations

from types import SimpleNamespace

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ..formatting import roll_line
from ..registry import registered_group_command
from ..runtime import DB, ENGINE, WORLD, require_character, reply_long, serialized_user_action


duel_group = app_commands.Group(
    name="duel",
    description="Consent-gated nonlethal player-versus-player duels",
)



@registered_group_command(duel_group, name="challenge", description="Offer a nonlethal PvP duel; combat cannot start without acceptance")
@serialized_user_action
async def duel_challenge(interaction: discord.Interaction, member: discord.Member, stakes: str = "honor") -> None:
    character = await require_character(interaction)
    if not character:
        return
    target = await DB.get_character(member.id)
    if not target or str(target.get("life_status")) != "alive":
        await interaction.response.send_message("That member has no living cultivator to challenge.", ephemeral=False)
        return
    if str(target.get("location")) != str(character.get("location")):
        await interaction.response.send_message("Consent PvP requires both cultivators to be mechanically present at the same location.", ephemeral=False)
        return
    if WORLD.location_safe_zone(str(character.get("location"))):
        await interaction.response.send_message("Local formations suppress PvP here.", ephemeral=False)
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "pvp.challenge",
            interaction.user.id,
            {"target_user_id": member.id, "stakes": stakes, "ttl_seconds": 300},
            action_id=f"discord:{interaction.id}:pvp.challenge",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc), ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    challenge_id = int(result.get("challenge_id", 0))
    await interaction.response.send_message(
        f"⚔️ <@{member.id}> receives consent duel challenge `#{challenge_id}` from <@{interaction.user.id}>. "
        f"Stakes: **{stakes[:200]}**. It expires in **5 minutes**; use **/combat → Duels → Respond**."
    )


@registered_group_command(duel_group, name="respond", description="Accept or reject a pending consent duel")
@app_commands.choices(decision=[app_commands.Choice(name="Accept", value="accept"), app_commands.Choice(name="Reject", value="reject")])
@serialized_user_action
async def duel_respond(interaction: discord.Interaction, challenge_id: int, decision: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "pvp.respond",
            interaction.user.id,
            {"challenge_id": int(challenge_id), "accept": decision.value == "accept"},
            action_id=f"discord:{interaction.id}:pvp.respond",
        )
    except GameEngineError as exc:
        await interaction.followup.send(str(exc), ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if str(result.get("status")) == "void":
        # v0.22.3: the engine re-checks the duel's preconditions at accept, not
        # only when the challenge was made. Five minutes is long enough to walk
        # into a city, and a duel between two places is not a duel.
        await interaction.followup.send(
            f"⚔️ The challenge lapses: {result.get('reason', 'its terms no longer hold')}. No duel begins."
        )
    elif decision.value == "accept":
        await interaction.followup.send(
            f"⚔️ Duel accepted. Nonlethal PvP match **#{result['match_id']}** begins; challenger acts first. Use **/combat → Duels → Act**."
        )
    else:
        await interaction.followup.send("🤝 Duel rejected. No combat state was created.")


@registered_group_command(duel_group, name="status", description="View your active consent PvP match or pending challenges")
async def duel_status(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    match = await DB.get_pvp_match(interaction.user.id)
    if match:
        await interaction.response.send_message(
            f"⚔️ **Duel #{match['match_id']}**\n"
            f"<@{match['player1_user_id']}>: **{match['player1_hp']} Vitality**\n"
            f"<@{match['player2_user_id']}>: **{match['player2_hp']} Vitality**\n"
            f"Turn: <@{match['turn_user_id']}> • Status **{match['status']}**",
            ephemeral=False,
        )
        return
    rows = await DB.get_pvp_challenges(interaction.user.id, pending_only=True)
    if not rows:
        await interaction.response.send_message("No active duel or pending consent challenge.", ephemeral=False)
        return
    await reply_long(
        interaction,
        "⚔️ **Pending Duel Challenges**\n" + "\n".join(
            f"`#{row['challenge_id']}` <@{row['challenger_user_id']}> → <@{row['target_user_id']}> • {row['stakes']}"
            for row in rows
        ),
        ephemeral=False,
    )


@registered_group_command(duel_group, name="act", description="Take a turn in an accepted nonlethal PvP duel")
@app_commands.choices(style=[app_commands.Choice(name="Attack", value="attack"), app_commands.Choice(name="Defend", value="defend"), app_commands.Choice(name="Surrender", value="surrender")])
@serialized_user_action
async def duel_act(interaction: discord.Interaction, style: app_commands.Choice[str]) -> None:
    if not await require_character(interaction):
        return
    match = await DB.get_pvp_match(interaction.user.id)
    if not match:
        await interaction.response.send_message("You have no active accepted duel.", ephemeral=False)
        return
    if int(match["turn_user_id"]) != interaction.user.id:
        await interaction.response.send_message("It is your opponent's turn.", ephemeral=False)
        return
    opponent_id = int(match["player2_user_id"]) if int(match["player1_user_id"]) == interaction.user.id else int(match["player1_user_id"])
    opponent = await DB.get_character(opponent_id)
    if not opponent:
        await interaction.response.send_message("Opponent state is unavailable.", ephemeral=False)
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "pvp.act",
            interaction.user.id,
            {"match_id": int(match["match_id"]), "style": style.value, "match_version": int(match["version"])},
            action_id=f"discord:{interaction.id}:pvp.act",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc), ephemeral=False)
        return
    resolved = dict(envelope.get("result") or {})
    if resolved.get("breach"):
        # The duel's preconditions stopped holding - someone left, someone
        # died, or the ground became a safe zone. The engine ends it rather
        # than refusing forever; say which way it went.
        if resolved.get("voided"):
            await interaction.response.send_message(
                f"⚔️ Duel **#{match['match_id']}** is void: {resolved['breach']}. No winner, no reputation."
            )
        else:
            winner_id = int(resolved.get("winner_user_id", 0))
            await interaction.response.send_message(
                f"⚔️ Duel **#{match['match_id']}** ends: {resolved['breach']}. "
                f"<@{winner_id}> takes it by forfeit; no reputation is recorded for a duel that was not fought out."
            )
        return
    if style.value == "surrender":
        await interaction.response.send_message(
            f"🏳️ You surrender duel **#{match['match_id']}**. <@{opponent_id}> wins; no true-death or injury roll occurs. Martial Society reputation records the honorable result."
        )
        return
    if style.value == "defend":
        await interaction.response.send_message(f"🛡️ You take a guarded stance. Turn passes to <@{opponent_id}>.")
        return
    roll = SimpleNamespace(**resolved)
    damage = int(resolved.get("damage", 0))
    result = f"\n💥 Damage **{damage}**." if damage else "\nThe attack fails to land cleanly."
    if bool(resolved.get("finished")):
        winner_id = int(resolved.get("winner_user_id", 0))
        result += f"\n🏆 <@{winner_id}> wins the **nonlethal** duel. Martial Society reputation records the result."
    await interaction.response.send_message(f"⚔️ **Duel #{match['match_id']}**\n{roll_line(roll)}{result}")
