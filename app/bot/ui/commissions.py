"""The commission surface: the offer card, its accept buttons, and the abandon
confirmation (v0.22.0, docs/COMMISSIONS_DESIGN.md).

Two rules shape everything in this module.

The first is that **the buttons come from the block, not from the narration**.
`CommissionOfferView` is built from the `Offer` the pure ladder returned
(app/rules/commissions.py); the Steward's words are attached above it and are
never parsed. If the narrator invents a reward, a deadline, or a second job,
none of it reaches a button and none of it reaches the engine.

The second is that **the card states the cost before the danger button**. The
abandon confirmation says what abandoning does to standing, and says it in the
same terms failing does, because they are the same thing.
"""
from __future__ import annotations

from typing import Any

import discord

from ...ops.game_engine import GameEngineError
from ...rules import commissions as rules
from ...rules import worldtime
from ..runtime import _explain_engine_error, current_world_time, log
from ..services import COMMISSIONS

# Long enough to read the offer and decide; short enough that a stale card in a
# busy channel does not invite a click that will be refused.
OFFER_TIMEOUT_SECONDS = 300


def _deadline_stamp(deadline_game_minute: int) -> str:
    wt = worldtime.from_game_minutes(int(deadline_game_minute))
    return f"day {wt.day} of month {wt.month}, {wt.period}"


def offer_card(offer: Any, *, item_names: dict[str, str] | None = None) -> str:
    """The canonical half of the reply: what the commission is, what it pays,
    when it is due. Everything here is read off the definition.

    When the giver keeps his terms to himself the card says so plainly rather
    than showing a blank or a guess. The player can see they are being asked to
    take work on trust; that is a decision they get to make, not something the
    interface hides from them."""
    definition = dict(offer.definition or {})
    hidden = rules.rewards_are_hidden(definition)
    terms = rules.variant_terms(definition)
    objectives = [str(o.get("label") or o.get("id") or "") for o in definition.get("objectives") or []]
    lines = [f"📜 **Commission: {definition.get('title', '')}**"]
    if str(definition.get("requires_sect") or ""):
        lines.append(f"-# {definition['requires_sect']} · disciples only")
    if objectives:
        lines.append("**Objectives**\n" + "\n".join(f"{i}. {text}" for i, text in enumerate(objectives, 1)))
    if hidden:
        lines.append(f"**Terms offered** — {rules.UNDISCLOSED}")
        if offer.boast:
            lines.append(f"-# What they claim it is worth: “{offer.boast}” — their words, not the Pavilion's ledger.")
        lines.append("-# You will not know what this pays until it is done. It may be worth far more "
                     "than it looks, or far less.")
    else:
        lines.append(
            f"**Terms offered** — {terms[0]['label']}\n"
            + rules.format_rewards(terms[0]["rewards"], item_names=item_names)
        )
    deadline = int(terms[0]["deadline_game_minutes"] or 0)
    due = rules.format_wait_days(deadline) + " from acceptance" if deadline else "no fixed deadline"
    lines.append(f"-# Deadline: {due} · you hold no other commission")
    return "\n".join(lines)


def held_card(held: dict[str, Any], *, game_minute: int) -> str:
    """The progress line under a giver's "how is it going" reply, and the same
    line the quest journal shows."""
    remaining = int(held.get("deadline_game_minute") or 0) - int(game_minute)
    due = (f"deadline in {rules.format_wait_days(remaining)}" if held.get("deadline_game_minute")
           else "no fixed deadline")
    return (f"**Held commission:** {held.get('title', held.get('quest_key'))} — "
            f"{int(held.get('objectives_done', 0))} / {int(held.get('objectives_total', 0))} objectives · {due}")


def accepted_card(accepted: dict[str, Any], *, item_names: dict[str, str] | None = None) -> str:
    """Terms are locked whether or not they are stated. An undisclosed
    commission has exact rewards from this moment on - the engine wrote them
    down - and the card says the terms are fixed without saying what they are."""
    deadline = accepted.get("deadline_game_minute")
    hidden = bool(accepted.get("rewards_hidden"))
    lines = [f"📜 **Commission accepted — {accepted.get('title', accepted.get('quest_key'))}**"]
    if hidden:
        lines.append(f"**Terms locked:** {accepted.get('variant_label', 'standard')} — "
                     "fixed now, told to you when the work is done.")
    else:
        lines.append(
            f"**Terms locked:** {accepted.get('variant_label', 'standard')} — "
            + rules.format_rewards(dict(accepted.get("rewards") or {}), item_names=item_names)
        )
    lines.append(f"**Deadline:** {_deadline_stamp(int(deadline))}" if deadline else "**Deadline:** none")
    lines.append("-# Rewards move only on completion. Track it under **/quests**. "
                 "Abandoning costs the same standing as failing.")
    return "\n".join(lines)


class CommissionAcceptButton(discord.ui.Button):
    """One set of terms. The index, not the label, is what the engine locks."""

    def __init__(self, owner: "CommissionOfferView", *, label: str, variant_index: int, primary: bool) -> None:
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.success if primary else discord.ButtonStyle.secondary,
        )
        self.owner = owner
        self.variant_index = int(variant_index)

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner.user_id:
            await interaction.response.send_message(
                "This commission was offered to another cultivator.", ephemeral=False, delete_after=20)
            return
        try:
            accepted = await COMMISSIONS.accept(
                self.owner.user_id, self.owner.quest_key,
                variant_index=self.variant_index,
                action_id=f"discord:{interaction.id}:commission.accept",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False, delete_after=30)
            return
        except Exception:
            log.exception("Commission accept failed")
            await interaction.response.send_message(
                "❌ The commission could not be recorded. Nothing was taken on.", ephemeral=False, delete_after=30)
            return
        self.owner.stop()
        for child in self.owner.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(view=self.owner)
        except Exception:
            log.debug("Could not disable the commission offer view", exc_info=True)
        await interaction.followup.send(
            accepted_card(accepted, item_names=COMMISSIONS.item_names()), ephemeral=False)


class CommissionDeclineButton(discord.ui.Button):
    def __init__(self, owner: "CommissionOfferView") -> None:
        super().__init__(label="Decline", style=discord.ButtonStyle.secondary)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner.user_id:
            await interaction.response.send_message(
                "This commission was offered to another cultivator.", ephemeral=False, delete_after=20)
            return
        self.owner.stop()
        for child in self.owner.children:
            child.disabled = True
        # Declining costs nothing: it was never taken on. Only an accepted
        # commission can be failed or abandoned.
        await interaction.response.edit_message(view=self.owner)
        await interaction.followup.send("You let the offer pass. Nothing is owed either way.", ephemeral=False)


class CommissionOfferView(discord.ui.View):
    """Accept-per-variant plus Decline. Built from the engine's definition, so
    the number of buttons is the number of sets of terms a GM approved."""

    def __init__(self, user_id: int, offer: Any) -> None:
        super().__init__(timeout=OFFER_TIMEOUT_SECONDS)
        self.user_id = int(user_id)
        definition = dict(offer.definition or {})
        self.quest_key = str(definition.get("quest_key") or "")
        for index, terms in enumerate(rules.variant_terms(definition)[:4]):
            self.add_item(CommissionAcceptButton(
                self, label=("Accept — " if index == 0 else "") + str(terms["label"]),
                variant_index=index, primary=index == 0,
            ))
        self.add_item(CommissionDeclineButton(self))


class AbandonCommissionButton(discord.ui.Button):
    def __init__(self, owner: "AbandonCommissionView") -> None:
        super().__init__(label="Abandon commission", style=discord.ButtonStyle.danger)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner.user_id:
            await interaction.response.send_message("This is not your commission.", ephemeral=False, delete_after=20)
            return
        try:
            resolved = await COMMISSIONS.resolve(
                self.owner.user_id, "abandoned", quest_key=self.owner.quest_key,
                action_id=f"discord:{interaction.id}:commission.resolve",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False, delete_after=30)
            return
        except Exception:
            log.exception("Commission abandon failed")
            await interaction.response.send_message(
                "❌ The commission could not be closed. It is still yours.", ephemeral=False, delete_after=30)
            return
        self.owner.stop()
        for child in self.owner.children:
            child.disabled = True
        await interaction.response.edit_message(view=self.owner)
        standing = dict(resolved.get("standing") or {})
        wait = rules.format_wait_days(int(standing.get("cooldown_game_minutes", 0) or 0))
        await interaction.followup.send(
            f"📜 **Abandoned — {resolved.get('title', self.owner.quest_key)}**\n"
            f"{resolved.get('giver_npc', 'The giver')} strikes it from the board. "
            f"Standing falls as if you had failed it; they will deal with you again in **{wait}**.",
            ephemeral=False,
        )


class KeepCommissionButton(discord.ui.Button):
    def __init__(self, owner: "AbandonCommissionView") -> None:
        super().__init__(label="Keep it", style=discord.ButtonStyle.secondary)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner.user_id:
            await interaction.response.send_message("This is not your commission.", ephemeral=False, delete_after=20)
            return
        self.owner.stop()
        for child in self.owner.children:
            child.disabled = True
        await interaction.response.edit_message(view=self.owner)


class AbandonCommissionView(discord.ui.View):
    def __init__(self, user_id: int, quest_key: str) -> None:
        super().__init__(timeout=120)
        self.user_id = int(user_id)
        self.quest_key = str(quest_key)
        self.add_item(AbandonCommissionButton(self))
        self.add_item(KeepCommissionButton(self))


def abandon_warning(held: dict[str, Any]) -> str:
    """States the cost before the danger button, in the words the outcome table
    makes true: abandoning is failing, chosen rather than suffered."""
    giver = str(held.get("giver_npc") or "The giver")
    return (
        f"**Abandon this commission?**\n{held.get('title', held.get('quest_key'))}\n\n"
        f"**{giver} will not forget this.**\n"
        f"Standing with them falls as if you had failed it. They will deal with you again after "
        f"**{rules.format_wait_days(rules.MINUTES_PER_DAY * 2)}**, and the next work they offer will be lesser.\n"
        "No reward, no partial credit."
    )


async def offer_for(user_id: int, giver: str, *, realm_index: int, game_minute: int) -> Any:
    """Read the facts, run the ladder. Deliberately split across two layers:
    CommissionService reads, app/rules/commissions.py decides, and the decision
    is a pure function of what was read - so the whole ladder is testable with
    a dict and no database."""
    inputs = await COMMISSIONS.offer_inputs(int(user_id), str(giver),
                                            realm_index=int(realm_index), game_minute=int(game_minute))
    return rules.choose_offer(**inputs)


async def commission_reply_extras(user_id: int, offer: Any) -> tuple[str, discord.ui.View | None]:
    """What the bot adds under a giver's narrated reply: the canonical card and,
    for an offer only, the buttons. Every other branch is words plus one line of
    truth - there is nothing to click when there is nothing on the table."""
    if offer.kind == "offer" and offer.definition:
        return offer_card(offer, item_names=COMMISSIONS.item_names()), CommissionOfferView(int(user_id), offer)
    if offer.kind == "progress" and offer.held:
        wt = await current_world_time()
        return (held_card(offer.held, game_minute=wt.total_minutes)
                + "\n-# One commission at a time. They will not offer another until this one resolves.", None)
    if offer.kind == "cooldown":
        outcome = offer.last_outcome or "previous"
        return (f"**No commission offered.** Last outcome: *{outcome}* · "
                f"they will deal with you again in **{rules.format_wait_days(offer.cooldown_game_minutes)}**.", None)
    return "", None
