"""`/tribute`: the world's own gift to a cultivator, once a cooldown.

This began life as `/vote`, a thank-you for voting the server up a listing
site, and was shaped around one for a while. The server lists nowhere, so
nothing outside this deployment is involved any more: no listing, no API, no
inbound endpoint, and so nothing to verify and nothing taken on trust. A
tribute is simply a claim a cultivator may make once every twelve hours, and
the engine is the whole authority on it.

The engine operations are still called `support.vote_*` and the cooldown key is
still `support_vote`, deliberately. Those strings are written into
`domain_events` and `cooldowns` the moment anybody plays, and renaming them
would orphan the rows or hand every player a free claim. The name a player
types is presentation; the name the ledger keeps is data.

The gift itself is the cultivator's: the engine sizes it by the realms they
have climbed inside the world they stand in, pays it in that world's low-grade
currency, and adds one material their craft or their path actually uses. On the
operator's local Friday-to-Sunday it doubles. Every number in this module is
read back off the engine's receipt - nothing here computes any part of a gift.
"""

from __future__ import annotations

import discord

from ...ops.game_engine import GameEngineError
from ..hubs import panel_timeout
from ..registry import registered_root_command
from ..runtime import (
    _explain_engine_error,
    budget_refusal_line,
    DB,
    ENGINE,
    WORLD,
    format_wait,
    log,
)
from ..services import GUILD


def _addressed(user: discord.abc.User) -> str:
    """What to call the cultivator: their name on this server.

    `display_name` is the server nickname when there is one and the account's
    name otherwise, which is the name everyone in the channel already knows
    them by. It is user-supplied text going into a message, so the markdown in
    it is escaped - a nickname of "**" would otherwise reach in and bold the
    rest of the line. Mentions cannot fire either way: the bot is built with
    allowed_mentions disabled for users, roles and everyone.
    """
    return discord.utils.escape_markdown(getattr(user, "display_name", "") or "Cultivator")


def _catalogue_name(resolve, identifier: str, what: str) -> str:
    """A catalogue name, or the raw id when the catalogue cannot name it.

    Never raises: the gift has already been paid by the time this runs, so a
    catalogue miss must cost the player a pretty word, not their receipt.
    """
    try:
        return resolve(identifier) or identifier
    except Exception:
        log.exception("Could not resolve the tribute %s %r", what, identifier)
        return identifier


def _gift_line(result: dict, *, name: str = "") -> str:
    """The engine's receipt, read back in names rather than ids.

    Every number here is the engine's. The command computes no part of the
    gift - it only spells out what `support.vote_claim` says it paid.
    """
    amount = int(result.get("amount") or 0)
    currency = _catalogue_name(WORLD.currency_name, str(result.get("currency") or ""), "currency")
    balance = int(result.get("balance") or 0)
    opening = f"🙏 Good work, **{name}**." if name else "🙏 Thank you."
    line = f"{opening} A patron's gift of **{amount} × {currency}**"
    item_id = str(result.get("item_id") or "")
    quantity = int(result.get("item_quantity") or 0)
    # No item is a real outcome, not an error: a tier material the catalogue
    # does not carry is dropped by the engine rather than granted as a phantom.
    if item_id and quantity > 0:
        item = _catalogue_name(WORLD.item_name, item_id, "item")
        line += f" and **{quantity} × {item}**"
    line += f" reaches you — you now hold **{balance}**."
    if result.get("weekend"):
        line += f"\n-# Doubled: the weekend bonus is running (×{int(result.get('multiplier') or 2)})."
    return line


class TributeClaimView(discord.ui.View):
    """One button: the tribute this cultivator is owed.

    The button belongs to the cultivator who ran the command, and it is spent
    once — the engine refuses a second claim inside the cooldown anyway, but a
    disabled button says so before the round trip does.
    """

    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=panel_timeout())
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message(
                "That tribute belongs to the cultivator who opened it — run **/tribute** yourself.",
                ephemeral=False,
                delete_after=15,
            )
            return False
        return True

    @discord.ui.button(label="Claim your tribute", style=discord.ButtonStyle.success, emoji="🎁")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        refusal = budget_refusal_line(interaction.user.id, "vote_claim")
        if refusal:
            await interaction.response.send_message(refusal, ephemeral=False, delete_after=20)
            return
        button.disabled = True
        button.label = "Claimed"
        self.stop()
        await interaction.response.edit_message(view=self)
        try:
            envelope = await ENGINE.authoritative_action(
                "support.vote_claim",
                interaction.user.id,
                {},
                action_id=f"discord:{interaction.id}:support.vote_claim",
            )
            result = dict(envelope.get("result") or {})
        except GameEngineError as exc:
            await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
            return
        await interaction.followup.send(
            _gift_line(result, name=_addressed(interaction.user)), ephemeral=False
        )


@registered_root_command(
    name="tribute",
    description="Claim the world's tribute to you — a patron's gift, once every 12h",
    guild=GUILD,
)
async def tribute(interaction: discord.Interaction) -> None:
    name = _addressed(interaction.user)
    lines = [f"🎁 **A tribute for {name}**"]
    # The weekend is the server's, not a cultivator's, so it is read before the
    # character check and shown to everyone - a visitor who has not begun yet
    # is exactly the person a doubled weekend should reach. `support.weekend`
    # touches no database and needs no actor, which is why it can be asked here.
    try:
        weekend = dict(await ENGINE.action("support.weekend", interaction.user.id, {}) or {})
    except GameEngineError:
        log.exception("Could not read the weekend window; drawing /tribute without it")
        weekend = {}
    if weekend.get("weekend"):
        closes = int(weekend.get("closes_unix") or 0)
        until = f" until <t:{closes}:R>" if closes else ""
        lines.append(f"🎉 **Weekend bonus — the gift is doubled{until}.**")
    # A visitor with no cultivator yet is told what one would get them rather
    # than being turned away by require_character: the tribute is a reason to
    # begin, so the person who has not begun is exactly who should read it.
    if await DB.get_character(interaction.user.id) is None:
        lines.append("\nThe tribute is for cultivators — **/begin** makes one, and then this pays every **12h**.")
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return
    try:
        status = dict(await ENGINE.action("support.vote_status", interaction.user.id, {}) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    claimable = bool(status.get("claimable"))
    amount = int(status.get("amount") or 0)
    if claimable:
        currency = _catalogue_name(WORLD.currency_name, str(status.get("currency") or ""), "currency")
        waiting = f"**{amount} × {currency}**"
        item_id = str(status.get("item_id") or "")
        quantity = int(status.get("item_quantity") or 0)
        if item_id and quantity > 0:
            waiting += f" and **{quantity} × {_catalogue_name(WORLD.item_name, item_id, 'item')}**"
        lines.append(
            f"\nA patron's gift of {waiting} is waiting — sized to your realm, "
            f"and renewed every **12h**."
        )
        view = TributeClaimView(owner_id=interaction.user.id)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=False)
        return
    remaining = int(status.get("remaining_seconds") or 0)
    lines.append(
        f"\nYou have already taken this tribute — the next is ready in "
        f"**{format_wait(remaining)}**."
    )
    await interaction.response.send_message("\n".join(lines), ephemeral=False)
