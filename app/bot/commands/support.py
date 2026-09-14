"""`/vote`: the server's listing page, and the patron's gift for supporting it.

A vote on Top.gg (or DISBOARD, or whichever list the operator configured) is
the one thing a player does for this world from outside it, and it is the
cheapest way a small server is found at all: the listing ranks on votes, and
every list worth the name resets the vote after twelve hours.

This used to say the vote could not be verified, and for a webhook that is
still true — publishing an endpoint from a NAS that publishes nothing is a door
opened for a thank-you. But Top.gg's v1 API answers the same question the other
way round, on an *outbound* call this deployment can already make, so when
`TOPGG_TOKEN` is set the claim is checked before it is paid (`app/ops/topgg.py`).

Without that token, and whenever Top.gg will not answer, the old bargain still
holds: the claim is taken on trust and the engine meters it at exactly the
cadence a real vote has (`support.vote_claim`), so an honest player is thanked
once per vote and a dishonest one is thanked no more often. Only a Top.gg that
answers and says *no live vote* refuses a claim — an outage never does.

The gift itself is the cultivator's: the engine sizes it by the realms they
have climbed inside the world they stand in, pays it in that world's low-grade
currency, and adds one material their craft or their path actually uses. On the
operator's local Friday-to-Sunday it doubles. Every number in this module is
read back off the engine's receipt - nothing here computes any part of a gift.
"""

from __future__ import annotations

import discord

from ...ops.game_engine import GameEngineError
from ...ops.topgg import NOT_VOTED, UNCONFIGURED as VOTE_UNCONFIGURED, VoteCheck
from ..registry import registered_root_command
from ..runtime import (
    _explain_engine_error,
    budget_refusal_line,
    DB,
    ENGINE,
    SETTINGS,
    TOPGG,
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


def _verification_is_live() -> bool:
    """Whether a claim will actually be checked against the listing's API."""
    return bool(SETTINGS.topgg_verify_votes and TOPGG.enabled)


async def _vote_check(user_id: int) -> VoteCheck:
    """Ask Top.gg whether this cultivator has a live vote.

    Skipped entirely when the operator has turned verification off, which is
    the same answer an absent token gives: UNCONFIGURED, which pays. The client
    never raises, so there is nothing to catch here - the whole point of the
    tri-state is that "we could not tell" is a value rather than an exception.
    """
    if not SETTINGS.topgg_verify_votes:
        return VoteCheck(VOTE_UNCONFIGURED, detail="TOPGG_VERIFY_VOTES is off")
    return await TOPGG.vote_state(user_id)


def _not_voted_line(site: str) -> str:
    """The only refusal in this module, and it is written to be survivable.

    A player reaching it has almost always done nothing wrong: they pressed
    before voting, or within the moment it takes Top.gg to record one. So it
    says what to do and that the button is still there, rather than accusing
    anyone of anything.
    """
    return (
        f"🗳️ **{site}** has no vote recorded for you yet, so the gift is still waiting.\n"
        "If you have just voted, give it a moment and press the button again — a vote can "
        "take a few seconds to register. If you have not, the link above is the place.\n"
        "-# Votes renew every 12h, and each one counts once."
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
        # Deferred before the check, not after: asking Top.gg is a round trip to
        # someone else's website and Discord wants an acknowledgement inside
        # three seconds. A component defer leaves the message as it is, so the
        # button can still be disabled - or deliberately left alone - once the
        # verdict is in.
        await interaction.response.defer()
        check = await _vote_check(interaction.user.id)
        if check.state == NOT_VOTED:
            # The one refusal, and the button is left live for it. A player who
            # pressed too early, or before Top.gg had recorded the vote, needs
            # to press again - taking the button away would make an honest
            # mistake cost them the gift.
            await interaction.followup.send(_not_voted_line(self.site), ephemeral=False)
            return
        button.disabled = True
        button.label = "Claimed"
        self.stop()
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException:
            # The message is gone or uneditable; the claim itself still stands.
            log.debug("Could not disable the vote claim button", exc_info=True)
        try:
            envelope = await ENGINE.authoritative_action(
                "support.vote_claim",
                interaction.user.id,
                # `verified` is the receipt's, not the gift's: the engine pays
                # the same either way and records which it was, so the ledger
                # can tell a checked claim from one paid through an outage.
                {"site": self.site, "verified": check.verified},
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
        currency = _catalogue_name(WORLD.currency_name, str(status.get("currency") or ""), "currency")
        waiting = f"**{amount} × {currency}**"
        item_id = str(status.get("item_id") or "")
        quantity = int(status.get("item_quantity") or 0)
        if item_id and quantity > 0:
            waiting += f" and **{quantity} × {_catalogue_name(WORLD.item_name, item_id, 'item')}**"
        lines.append(
            f"\nThen press the button: a patron's gift of {waiting} is waiting — the gift is sized "
            f"to your realm, and it renews every **12h**."
        )
        # Said out loud when it is true, because it changes what the button
        # means: vote first, then press. Silent otherwise rather than promising
        # a check this deployment is not making.
        if _verification_is_live():
            lines.append(f"-# Your vote is confirmed with {site} before the gift is paid.")
        view = VoteClaimView(owner_id=interaction.user.id, site=site)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=False)
        return
    remaining = int(status.get("remaining_seconds") or 0)
    lines.append(
        f"\nYou have already claimed this round — the next gift is ready in "
        f"**{format_wait(remaining)}**. The vote itself is never wasted."
    )
    await interaction.response.send_message("\n".join(lines), ephemeral=False)
