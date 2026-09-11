"""Typed play: the Discord side (v0.21.1).

``typed_play_router`` decides what a typed line means; this module runs it.
Three pieces:

* :class:`MessageInteraction` - a ``discord.Interaction``-shaped view of a
  ``discord.Message``, so the registered slash handlers (``explore``, ``talk``,
  the scene-action resolver) can be invoked from a typed line exactly as the
  hubs invoke them from a button. Same idea as ``HubInteractionProxy`` in
  ``hubs.py``: the handler does not know or care where the call came from.
* :func:`dispatch` - the ONLY place typed play calls a handler. Root commands
  go through ``registry.ACTIONS`` (the same table the hubs use); scene actions
  go to the function the ``/action`` panel's modal submits to; dialogue goes
  to ``/talk``. Typed play defines no handler of its own and makes no engine
  call and no database write - ``tests/python/contracts/test_typed_play_surface.py``
  checks that by reading this file.
* :class:`TypedPlayPicker` - the view shown when a line is ambiguous or matches
  nothing. A click is a real interaction, so from there dispatch is ordinary.

Every path that can reach the engine or the narrator spends a token from
``runtime.TYPED_PLAY_BUDGET`` first. Speech costs nothing and never comes here.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Awaitable, Callable

import discord

from .registry import ACTIONS, EVENT_HANDLERS
from .runtime import SETTINGS, TYPED_PLAY_BUDGET, log
from .typed_play_router import Candidate, Route, VerbTable

VERB_TABLE = VerbTable.load()

NarrateFn = Callable[[Any], Awaitable[None]]


class TypedPlayUnsupported(RuntimeError):
    """The handler needed something a typed line cannot provide (a modal)."""


# --------------------------------------------------------------------------
# A Message wearing an Interaction's clothes
# --------------------------------------------------------------------------

class _MessageResponse:
    def __init__(self, owner: "MessageInteraction") -> None:
        self._owner = owner
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._done = True
        try:
            await self._owner.channel.typing()
        except Exception:  # typing is cosmetic; never let it fail a dispatch
            pass

    async def send_message(self, content: Any = None, **kwargs: Any) -> None:
        self._done = True
        await self._owner._send(content, **kwargs)

    async def edit_message(self, **kwargs: Any) -> None:
        self._done = True
        await self._owner.edit_original_response(**kwargs)

    async def send_modal(self, modal: Any) -> None:
        raise TypedPlayUnsupported("this action needs a form; use its hub or slash command")


class _MessageFollowup:
    def __init__(self, owner: "MessageInteraction") -> None:
        self._owner = owner

    async def send(self, content: Any = None, **kwargs: Any) -> discord.Message | None:
        return await self._owner._send(content, **kwargs)


_SEND_KWARGS = {"embed", "embeds", "view", "file", "files", "allowed_mentions", "suppress_embeds", "silent"}


class MessageInteraction:
    """Enough of ``discord.Interaction`` for the handlers typed play dispatches to.

    Replies go to the channel the line was typed in: the first as a reply to
    the player's message (so the thread of cause and effect is visible), the
    rest as ordinary sends. ``ephemeral`` cannot be honoured from a message and
    is dropped - nothing the dispatched handlers say ephemerally is secret,
    they use it for tidiness.
    """

    def __init__(self, message: discord.Message, client: Any, *, command: Any = None) -> None:
        self.source_message = message
        self.user = message.author
        self.channel = message.channel
        self.channel_id = message.channel.id
        self.guild = message.guild
        self.guild_id = message.guild.id if message.guild is not None else None
        self.id = message.id
        self.client = client
        self.command = command
        self.type = None
        self.data: dict[str, Any] = {}
        self.response = _MessageResponse(self)
        self.followup = _MessageFollowup(self)
        self.sent: list[discord.Message] = []
        self.typed_play = True

    async def _send(self, content: Any = None, **kwargs: Any) -> discord.Message | None:
        clean = {k: v for k, v in kwargs.items() if k in _SEND_KWARGS and v is not None}
        try:
            if not self.sent:
                msg = await self.source_message.reply(content, mention_author=False, **clean)
            else:
                msg = await self.channel.send(content, **clean)
        except discord.HTTPException:
            log.exception("Typed play could not deliver a reply")
            return None
        self.sent.append(msg)
        return msg

    async def edit_original_response(self, **kwargs: Any) -> discord.Message | None:
        if self.sent:
            clean = {k: v for k, v in kwargs.items() if k in _SEND_KWARGS | {"content"}}
            try:
                return await self.sent[0].edit(**clean)
            except discord.HTTPException:
                log.exception("Typed play could not edit its reply")
                return None
        return await self._send(kwargs.get("content"), **kwargs)

    async def original_response(self) -> discord.Message | None:
        return self.sent[0] if self.sent else None

    async def delete_original_response(self) -> None:
        if self.sent:
            try:
                await self.sent[0].delete()
            except discord.HTTPException:
                pass


# --------------------------------------------------------------------------
# Dispatch: the one door to the handlers
# --------------------------------------------------------------------------

async def _guild_member(interaction: Any, user_id: str) -> Any:
    guild = getattr(interaction, "guild", None)
    if guild is None or not user_id.isdigit():
        return None
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except (discord.HTTPException, AttributeError):
            return None
    return member


async def dispatch(interaction: Any, candidate: Candidate) -> None:
    """Run one candidate through the handler that already owns it.

    ``interaction`` is a real ``discord.Interaction`` (from a picker click) or a
    :class:`MessageInteraction` (from a typed line). Either way the handler is
    the registered one; typed play adds nothing in between.
    """
    if candidate.kind == "root":
        name = str(candidate.payload["command"])
        # A root by name; a group leaf ("travel go", v0.33.0) by its qualified name.
        command = ACTIONS.root(name) if " " not in name else ACTIONS.qualified(name)
        handler = ACTIONS.handler_for(command)
        if isinstance(interaction, MessageInteraction):
            interaction.command = command
        arguments = {str(k): v for k, v in dict(candidate.payload.get("arguments") or {}).items()}
        # A player argument (v0.39.0) is resolved by id against the guild:
        # the handler takes the member the slash command would have been
        # given. Nobody by that id here is a refusal, not a guess.
        for parameter, source in dict(candidate.payload.get("sources") or {}).items():
            if source != "player":
                continue
            member = await _guild_member(interaction, str(arguments.get(parameter) or ""))
            if member is None:
                await interaction.response.send_message("They are not here any more.")
                return
            arguments[parameter] = member
        await handler(interaction, **arguments)
        return
    if candidate.kind == "talk":
        command = ACTIONS.root("talk")
        handler = ACTIONS.handler_for(command)
        if isinstance(interaction, MessageInteraction):
            interaction.command = command
        await handler(interaction, str(candidate.payload["npc"]), str(candidate.payload["message"]))
        return
    if candidate.kind == "scene":
        # The same function the /action panel's detail modal submits to,
        # reached by name through the registry (commands/scene.py binds it).
        await EVENT_HANDLERS.invoke(
            "scene_action_resolve",
            interaction,
            action_key=str(candidate.payload["scene_action"]),
            target=str(candidate.payload["target"]),
            detail=str(candidate.payload["detail"]),
        )
        return
    raise TypedPlayUnsupported(f"unknown candidate kind {candidate.kind!r}")


def budget_refusal(user_id: int, door: str = "typed") -> str | None:
    """None if the player may spend a token now; otherwise the line to reply with."""
    if TYPED_PLAY_BUDGET.try_acquire(int(user_id), door=door):
        return None
    wait = TYPED_PLAY_BUDGET.seconds_until_token(int(user_id))
    return (
        f"⏳ Typed actions are limited to about {SETTINGS.typed_play_per_minute:g} a minute. "
        f"Try again in {max(1, int(round(wait)))}s — or just say it in character, that's free."
    )


# --------------------------------------------------------------------------
# The picker
# --------------------------------------------------------------------------

class TypedPlayPicker(discord.ui.View):
    """Shown when a typed line is ambiguous or matches nothing. Owner-only."""

    def __init__(self, *, owner_id: int, route: Route, narrate: NarrateFn | None) -> None:
        super().__init__(timeout=120)
        self.owner_id = int(owner_id)
        self.route = route
        self.narrate = narrate
        self.message: discord.Message | None = None
        for candidate in route.candidates:
            self.add_item(_CandidateButton(candidate))
        if narrate is not None:
            self.add_item(_NarrateButton())
        self.add_item(_SayItButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("That choice belongs to the cultivator who typed the line.", ephemeral=False, delete_after=15)
            return False
        return True

    async def on_timeout(self) -> None:
        await self._close("(no choice made)")

    async def _close(self, note: str) -> None:
        self.stop()
        if self.message is None:
            return
        try:
            await self.message.edit(content=f"{self.message.content}\n{note}", view=None)
        except discord.HTTPException:
            pass


class _CandidateButton(discord.ui.Button):
    def __init__(self, candidate: Candidate) -> None:
        super().__init__(label=candidate.label[:80], style=discord.ButtonStyle.primary)
        self.candidate = candidate

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TypedPlayPicker = self.view  # type: ignore[assignment]
        refusal = budget_refusal(interaction.user.id)
        if refusal:
            await interaction.response.send_message(refusal, ephemeral=False, delete_after=20)
            return
        await view._close(f"→ {self.candidate.label}")
        try:
            await dispatch(interaction, self.candidate)
        except TypedPlayUnsupported as exc:
            await _say(interaction, f"That can't be done from a typed line: {exc}")
        except Exception:
            log.exception("Typed play dispatch failed from picker: %s", self.candidate.id)
            await _say(interaction, "❌ That action could not be completed. The game state was rechecked and nothing was applied.")


class _NarrateButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Narrate it", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TypedPlayPicker = self.view  # type: ignore[assignment]
        refusal = budget_refusal(interaction.user.id, door="narrate_it")
        if refusal:
            await interaction.response.send_message(refusal, ephemeral=False, delete_after=20)
            return
        await view._close("→ narrated")
        if not interaction.response.is_done():
            await interaction.response.defer()
        if view.narrate is not None:
            await view.narrate(interaction)


class _SayItButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Just say it in character", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TypedPlayPicker = self.view  # type: ignore[assignment]
        await view._close("→ said in character")
        if not interaction.response.is_done():
            await interaction.response.defer()


async def _say(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=False)
        else:
            await interaction.response.send_message(text, ephemeral=False)
    except discord.HTTPException:
        log.exception("Typed play could not deliver a message")


def picker_prompt(route: Route) -> str:
    if route.candidates:
        return f"“{route.text[:120]}” — which do you mean?"
    if route.message:
        return f"“{route.text[:120]}” — {route.message} Narrate it, or just say it in character."
    return f"“{route.text[:120]}” — nothing I can resolve. Narrate it, or just say it in character."


# --------------------------------------------------------------------------
# The once-a-day hint
# --------------------------------------------------------------------------

_HINTED: dict[int, str] = {}


def hint_due(user_id: int, *, today: str | None = None) -> bool:
    """True once per player per UTC day; records the hint as shown."""
    day = today or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    if _HINTED.get(int(user_id)) == day:
        return False
    if len(_HINTED) > 4096:
        _HINTED.clear()
    _HINTED[int(user_id)] = day
    return True


def hint_text() -> str:
    prefix = SETTINGS.typed_play_prefix
    return (
        f"💡 That reads like an action. Start a line with `{prefix}` to have the world resolve it "
        f"(`{prefix} I explore the ravine`). Lines without it are just your character speaking, "
        "and NPCs who are present answer when you address them by name."
    )
