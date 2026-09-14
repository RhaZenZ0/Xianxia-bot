"""`/vote`: the server's listing page, and the patron's gift for supporting it.

A vote on Top.gg (or DISBOARD, or whichever list the operator configured) is
the one thing a player does for this world from outside it, and it is the
cheapest way a small server is found at all: the listing ranks on votes, and
Discadia, the listing this server votes on, resets the vote after a day.

Nothing here verifies the vote. Verification would mean an inbound webhook,
which means publishing an endpoint from a NAS that currently publishes nothing
— a door opened for a thank-you. So the claim is taken on trust and the engine
meters it at exactly the cadence a real vote has (`support.vote_claim`): an
honest player is thanked once per vote, and a dishonest one is thanked no more
often.

The gift itself is the cultivator's: the engine sizes it by the realms they
have climbed inside the world they stand in, pays it in that world's low-grade
currency, and adds one material their craft or their path actually uses. On the
operator's local Friday-to-Sunday it doubles. Every number in this module is
read back off the engine's receipt - nothing here computes any part of a gift.
"""

from __future__ import annotations

import discord

from ...ops.game_engine import GameEngineError
from ..registry import registered_root_command
from ..runtime import (
    _explain_engine_error,
    budget_refusal_line,
    DB,
    ENGINE,
    SETTINGS,
    WORLD,
    format_wait,
    log,
)
from ..services import GUILD

UNCONFIGURED = (
    "🗳️ No server listing is configured yet, so there is nothing to vote on. "
    "An administrator sets `VOTE_SITE_URL` (and `VOTE_SITE_NAME`) in `.env` — "
    "see `docs/CONFIGURATION.md`."
)


def _catalogue_name(resolve, identifier: str, what: str) -> str:
    """A catalogue name, or the raw id when the catalogue cannot name it.

    Never raises: the gift has already been paid by the time this runs, so a
    catalogue miss must cost the player a pretty word, not their receipt.
    """
    try:
        return resolve(identifier) or identifier
    except Exception:
        log.exception("Could not resolve the vote gift %s %r", what, identifier)
        return identifier


def _gift_line(result: dict) -> str:
    """The engine's receipt, read back in names rather than ids.

    Every number here is the engine's. The command computes no part of the
    gift - it only spells out what `support.vote_claim` says it paid.
    """
    amount = int(result.get("amount") or 0)
    currency = _catalogue_name(WORLD.currency_name, str(result.get("currency") or ""), "currency")
    balance = int(result.get("balance") or 0)
    line = f"🙏 Thank you. A patron's gift of **{amount} × {currency}**"
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


class VoteClaimView(discord.ui.View):
    """One button under the link: the claim the player says they have earned.

    The button belongs to the cultivator who ran the command, and it is spent
    once — the engine refuses a second claim inside the cooldown anyway, but a
    disabled button says so before the round trip does.
    """

    def __init__(self, *, owner_id: int, site: str) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.site = str(site)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message(
                "That claim belongs to the cultivator who opened it — run **/vote** yourself.",
                ephemeral=False,
                delete_after=15,
            )
            return False
        return True

    @discord.ui.button(label="I voted — claim", style=discord.ButtonStyle.success, emoji="🗳️")
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
                {"site": self.site},
                action_id=f"discord:{interaction.id}:support.vote_claim",
            )
            result = dict(envelope.get("result") or {})
        except GameEngineError as exc:
            await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
            return
        await interaction.followup.send(_gift_line(result), ephemeral=False)


@registered_root_command(
    name="vote",
    description="Vote for this server on its listing page and claim a patron's gift",
    guild=GUILD,
)
async def vote(interaction: discord.Interaction) -> None:
    url = SETTINGS.vote_site_url
    if not url:
        await interaction.response.send_message(UNCONFIGURED, ephemeral=False, delete_after=60)
        return
    site = SETTINGS.vote_site_name
    lines = [
        f"🗳️ **Support this world — {site}**",
        "Every vote lifts the server up its listing, which is how the next cultivator finds us.",
        f"Vote here: {url}",
    ]
    # The weekend is the server's, not a cultivator's, so it is read before the
    # character check and shown to everyone - a visitor who has not begun yet
    # is exactly the person a doubled weekend should reach. `support.weekend`
    # touches no database and needs no actor, which is why it can be asked here.
    try:
        weekend = dict(await ENGINE.action("support.weekend", interaction.user.id, {}) or {})
    except GameEngineError:
        log.exception("Could not read the weekend window; drawing /vote without it")
        weekend = {}
    if weekend.get("weekend"):
        closes = int(weekend.get("closes_unix") or 0)
        until = f" until <t:{closes}:R>" if closes else ""
        lines.append(f"🎉 **Weekend bonus — the gift is doubled{until}.**")
    # The link is for everyone. A visitor with no cultivator yet is exactly the
    # person a listing brought in, so they are shown the page and told what
    # making one would get them, rather than being turned away by
    # require_character before they have read the link.
    if await DB.get_character(interaction.user.id) is None:
        lines.append("\nThe gift is for cultivators — **/begin** makes one, and then this pays every **24h**.")
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
            f"\nThen press the button: a patron's gift of {waiting} is waiting — the gift is sized "
            f"to your realm, and it renews every **24h**."
        )
        view = VoteClaimView(owner_id=interaction.user.id, site=site)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=False)
        return
    remaining = int(status.get("remaining_seconds") or 0)
    lines.append(
        f"\nYou have already claimed this round — the next gift is ready in "
        f"**{format_wait(remaining)}**. The vote itself is never wasted."
    )
    await interaction.response.send_message("\n".join(lines), ephemeral=False)
