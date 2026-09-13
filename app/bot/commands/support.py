"""`/vote`: the server's listing page, and the patron's gift for supporting it.

A vote on Top.gg (or DISBOARD, or whichever list the operator configured) is
the one thing a player does for this world from outside it, and it is the
cheapest way a small server is found at all: the listing ranks on votes, and
every list worth the name resets the vote after twelve hours.

Nothing here verifies the vote. Verification would mean an inbound webhook,
which means publishing an endpoint from a NAS that currently publishes nothing
— a door opened for a thank-you. So the claim is taken on trust and the engine
meters it at exactly the cadence a real vote has (`support.vote_claim`): an
honest player is thanked once per vote, and a dishonest one is thanked no more
often. The gift is deliberately small and arrives in the low-grade currency of
the world the cultivator stands in, so it buys a few things at any tier and
distorts no economy.
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


def _gift_line(result: dict) -> str:
    """The engine's receipt, in the currency's own name rather than its id."""
    amount = int(result.get("amount") or 0)
    currency_id = str(result.get("currency") or "")
    try:
        currency = WORLD.currency_name(currency_id) or currency_id
    except Exception:  # a currency the catalogue does not name is still payable
        log.exception("Could not resolve the vote gift currency %r", currency_id)
        currency = currency_id
    balance = int(result.get("balance") or 0)
    return (
        f"🙏 Thank you. A patron's gift of **{amount} × {currency}** reaches you "
        f"— you now hold **{balance}**."
    )


class VoteClaimView(discord.ui.View):
    """One button under the link: the claim the player says they have earned.

    The button belongs to the cultivator who ran the command, and it is spent
    once — the engine refuses a second claim inside twelve hours anyway, but a
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
    # The link is for everyone. A visitor with no cultivator yet is exactly the
    # person a listing brought in, so they are shown the page and told what
    # making one would get them, rather than being turned away by
    # require_character before they have read the link.
    if await DB.get_character(interaction.user.id) is None:
        lines.append("\nThe gift is for cultivators — **/begin** makes one, and then this pays every **12h**.")
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
        lines.append(
            f"\nThen press the button: a patron's gift of **{amount}** low-grade stones of your "
            f"own world is waiting, and it renews every **12h**."
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
