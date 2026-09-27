"""Cards: the embed-shaped messages, drawn in the Components V2 layout (v1.9.0).

Why this module exists
----------------------
The seventeen hubs moved to Components V2 in v0.19.5 and the Scene Action
panel followed, but every *card* - the character sheet, a battle, a boss raid,
an auction lot, a stall, an event scene, a discovery - stayed a classic embed.
So one bot drew two looks: a panel with an accent bar and text blocks, and
beside it an embed with a coloured edge and inline fields.

A card here is written exactly as an embed was - :class:`Card` keeps
``discord.Embed``'s constructor and its ``add_field`` / ``set_footer`` /
``set_thumbnail`` / ``set_image`` / ``set_author`` - and draws itself as a
``Container``. That keeps each migration a change of the class name rather
than a rewrite of how the card is composed.

:class:`CardView` is the other half. A Components V2 message may carry no
``content`` and no ``embeds``, and a button in one must sit inside an
``ActionRow``, so a card's buttons have to live in the same ``LayoutView`` as
the card. ``CardView`` accepts a view written the classic way -
``@discord.ui.button`` methods, ``add_item`` with ``row=``, ``clear_items`` -
and lays those controls out in rows under the card, so a view keeps its
callbacks, its ``interaction_check`` and its state and changes only its base
class. The controls are ``view.controls``; ``view.children`` is the layout
(the container), which is what discord.py itself walks.

It imports ``discord`` and nothing from the project, so it sits at the bottom
of the package with ``scene_layout.py``.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import discord

#: Discord caps the text of a Components V2 message at 4,000 characters across
#: all of its text displays. A card leaves a little room under it.
TEXT_LIMIT = 3900
#: A classic view holds five rows; a card keeps the same shape.
CONTROL_ROWS = 5
_BLANK_NAMES = {"", "​", "​​"}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


class Card:
    """An embed's content, drawn as a Components V2 container.

    The constructor and the setters are ``discord.Embed``'s, so a card is
    composed the way an embed was. ``colour`` and ``color`` are one value.
    """

    def __init__(
        self,
        *,
        title: Any = None,
        description: Any = None,
        colour: Any = None,
        color: Any = None,
        **_ignored: Any,
    ) -> None:
        self.title = _text(title)
        self.description = _text(description)
        value = colour if colour is not None else color
        self.colour = int(value) if value is not None else None
        self.fields: list[tuple[str, str, bool]] = []
        self.footer = ""
        self.author = ""
        self.thumbnail: str | None = None
        self.image: str | None = None
        #: Text drawn above the title: what used to be a message's ``content``
        #: beside its embed, which a Components V2 message cannot carry.
        self.lead = ""

    # --- discord.Embed's surface ---------------------------------------
    @property
    def color(self) -> int | None:
        return self.colour

    @color.setter
    def color(self, value: Any) -> None:
        self.colour = int(value) if value is not None else None

    def add_field(self, *, name: Any, value: Any, inline: bool = True) -> "Card":
        self.fields.append((_text(name), _text(value), bool(inline)))
        return self

    def set_footer(self, *, text: Any = None, icon_url: Any = None) -> "Card":
        self.footer = _text(text)
        return self

    def set_author(self, *, name: Any, url: Any = None, icon_url: Any = None) -> "Card":
        self.author = _text(name)
        return self

    def set_thumbnail(self, *, url: Any) -> "Card":
        self.thumbnail = _text(url) or None
        return self

    def set_image(self, *, url: Any) -> "Card":
        self.image = _text(url) or None
        return self

    # --- reading it back --------------------------------------------------
    def text(self) -> str:
        """Every word the card shows, in order: what a reader, a log line or a
        test sees without walking the component tree."""
        return "\n".join(part for part in self._parts() if part)

    def _parts(self) -> list[str]:
        lead, header, fields, footer = self._fitted()
        return [lead, header, fields, footer]

    def _header(self) -> str:
        lines = []
        if self.author:
            lines.append(f"-# {self.author}")
        if self.title:
            lines.append(f"## {self.title}")
        if self.description:
            lines.append(self.description)
        return "\n".join(lines)

    def _fields(self) -> str:
        blocks = []
        for name, value, _inline in self.fields:
            name = name.strip()
            if name in _BLANK_NAMES:
                blocks.append(value)
            else:
                blocks.append(f"**{name}**\n{value}" if value else f"**{name}**")
        return "\n\n".join(block for block in blocks if block)

    def _fitted(self) -> tuple[str, str, str, str]:
        """The four text blocks, cut to fit Discord's 4,000 characters.

        An embed allowed 6,000, so a long sheet can overflow. The fields are
        cut first, then the description, then the lead - the title and the
        footer are short and say what the card is."""
        lead = self.lead.strip()
        header = self._header()
        fields = self._fields()
        footer = f"-# {self.footer}" if self.footer else ""
        budget = TEXT_LIMIT - len(footer)
        for which in ("fields", "header", "lead"):
            total = len(lead) + len(header) + len(fields)
            if total <= budget:
                break
            over = total - budget
            if which == "fields" and fields:
                fields = _cut(fields, len(fields) - over)
            elif which == "header" and header:
                header = _cut(header, len(header) - over)
            elif which == "lead" and lead:
                lead = _cut(lead, len(lead) - over)
        return lead, header, fields, footer

    # --- drawing ------------------------------------------------------------
    def container(self, controls: Sequence[discord.ui.ActionRow] = ()) -> discord.ui.Container:
        lead, header, fields, footer = self._fitted()
        container = discord.ui.Container(accent_colour=self.colour)
        if lead:
            container.add_item(discord.ui.TextDisplay(lead))
            if header:
                container.add_item(discord.ui.Separator())
        if header:
            if self.thumbnail:
                container.add_item(
                    discord.ui.Section(
                        discord.ui.TextDisplay(header),
                        accessory=discord.ui.Thumbnail(self.thumbnail),
                    )
                )
            else:
                container.add_item(discord.ui.TextDisplay(header))
        if self.image:
            container.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem(self.image))
            )
        if fields:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(fields))
        if footer:
            container.add_item(discord.ui.TextDisplay(footer))
        if not (lead or header or fields or footer or self.image):
            container.add_item(discord.ui.TextDisplay("​"))
        if controls:
            container.add_item(discord.ui.Separator())
            for row in controls:
                container.add_item(row)
        return container


def _cut(text: str, length: int) -> str:
    if length <= 1:
        return "…"
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def _is_control(item: discord.ui.Item[Any]) -> bool:
    """A button or a select: what a classic view held, and what must sit in a row."""
    return not item._is_v2()


class CardView(discord.ui.LayoutView):
    """A card with the controls of a classic view laid out under it.

    Subclasses written for ``discord.ui.View`` move over by changing their
    base: decorated buttons and selects, ``add_item(item)`` with a ``row``,
    ``remove_item`` and ``clear_items`` all act on :attr:`controls`, and the
    card is set with :meth:`set_card`. Every change re-lays the view out, so
    the message is right whenever it is sent or edited.
    """

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        # LayoutView gathers only decorators bound to an ActionRow; a view
        # written the classic way declares them bare. Take those too, so they
        # are built (with their callback bound to this view) like any other.
        children = dict(cls.__view_children_items__)
        for base in reversed(cls.__mro__):
            for name, member in base.__dict__.items():
                if hasattr(member, "__discord_ui_model_type__") and not getattr(
                    member, "__discord_ui_parent__", None
                ):
                    children[name] = member
        cls.__view_children_items__ = children

    def __init__(self, *, card: Card | None = None, timeout: float | None = 180.0) -> None:
        self._card_controls: list[discord.ui.Item[Any]] = []
        self._card: Card | None = card
        self._laying_out = False
        super().__init__(timeout=timeout)
        # The decorated controls were built as top-level children; move them
        # into the rows.
        for item in [item for item in list(self._children) if _is_control(item)]:
            super().remove_item(item)
            self._card_controls.append(item)
        self._lay_out()

    # --- the classic view's surface --------------------------------------
    @property
    def controls(self) -> list[discord.ui.Item[Any]]:
        """The buttons and selects, in the order they were added."""
        return list(self._card_controls)

    @property
    def card(self) -> Card | None:
        return self._card

    def set_card(self, card: Card | None) -> "CardView":
        self._card = card
        self._lay_out()
        return self

    def add_item(self, item: discord.ui.Item[Any]) -> "CardView":
        if self._laying_out or not _is_control(item):
            return super().add_item(item)
        self._card_controls.append(item)
        self._lay_out()
        return self

    def remove_item(self, item: discord.ui.Item[Any]) -> "CardView":
        if self._laying_out or item not in self._card_controls:
            return super().remove_item(item)
        self._card_controls.remove(item)
        self._lay_out()
        return self

    def clear_items(self) -> "CardView":
        if self._laying_out:
            return super().clear_items()
        self._card_controls.clear()
        self._lay_out()
        return self

    def disable_controls(self) -> None:
        """What a classic view's ``on_timeout`` did over ``self.children``."""
        for item in self._card_controls:
            if hasattr(item, "disabled"):
                item.disabled = True
        self._lay_out()

    # --- layout -------------------------------------------------------------
    def _rows(self) -> list[discord.ui.ActionRow]:
        """Controls grouped the way a classic view grouped them: an item with a
        ``row`` goes there, the rest fill the first row with room."""
        weights = [0] * CONTROL_ROWS
        placed: list[list[discord.ui.Item[Any]]] = [[] for _ in range(CONTROL_ROWS)]
        extra: list[list[discord.ui.Item[Any]]] = []
        for item in self._card_controls:
            width = getattr(item, "width", 1)
            wanted = getattr(item, "_row", None)
            order = [wanted] if wanted is not None and 0 <= wanted < CONTROL_ROWS else []
            order += [r for r in range(CONTROL_ROWS) if r != wanted]
            for row in order:
                if weights[row] + width <= 5 and len(placed[row]) < 5:
                    placed[row].append(item)
                    weights[row] += width
                    break
            else:
                extra.append([item])
        groups = [row for row in placed if row] + extra
        rows = []
        for group in groups:
            row = discord.ui.ActionRow()
            for item in group:
                item._parent = None
                row.add_item(item)
            rows.append(row)
        return rows

    def _lay_out(self) -> None:
        self._laying_out = True
        try:
            super().clear_items()
            rows = self._rows()
            if self._card is not None:
                super().add_item(self._card.container(rows))
            else:
                for row in rows:
                    super().add_item(row)
        finally:
            self._laying_out = False


def card_view(card: Card, *, timeout: float | None = None) -> CardView:
    """A card with no controls, ready to send as ``view=``."""
    return CardView(card=card, timeout=timeout)


def card_text(view: Any) -> str:
    """Every word on a card view - for logs and tests."""
    card = getattr(view, "card", None)
    return card.text() if isinstance(card, Card) else ""


def is_layout(view: Any) -> bool:
    """Whether ``view`` makes a Components V2 message - one that may carry no
    ``content`` and no ``embeds``."""
    check = getattr(view, "has_components_v2", None)
    return bool(callable(check) and check())


def fold_content(view: Any, content: Any) -> bool:
    """Put text sent beside a card into the card, above its title.

    A Components V2 message cannot carry ``content``, so a caller that passed
    both would be refused by Discord. True when there is nothing left to send
    as content; False when ``view`` is a layout that is not a card and the text
    has nowhere to go."""
    text = "" if content is None else str(content).strip()
    if not text:
        return True
    card = getattr(view, "card", None)
    if not isinstance(view, CardView) or not isinstance(card, Card):
        return False
    if text not in card.lead:
        card.lead = "\n".join(part for part in (text, card.lead) if part)
        view._lay_out()
    return True


# ---------------------------------------------------------------------------
# Notices: a plain-text reply, drawn as a card (v1.9.0)
# ---------------------------------------------------------------------------
# About three hundred replies are a bare line of text - a refusal, a
# confirmation, a result too short to need a card - sent straight through
# ``interaction.response`` and ``interaction.followup``. Rather than touch each
# of them, the one class discord.py builds every interaction from is replaced
# by a subclass whose three reply doors turn a plain-text reply into a notice
# card as it is sent. A reply that carries a view, an embed or a file is left
# exactly as its handler wrote it: its layout is the handler's decision.

NOTICE_SUCCESS = 0x57F287
NOTICE_FAILURE = 0xED4245
NOTICE_WAIT = 0xF0A33E
NOTICE_NEUTRAL = 0x6D78A8
_FAILURE_MARKS = ("❌", "⛔", "🚫", "⚠️", "🛑")
_WAIT_MARKS = ("⌛", "⏳", "🕒", "🔒")
_SUCCESS_MARKS = ("✅", "🎉", "✨")
#: Keywords that make a message something other than plain text.
_NOT_PLAIN = ("view", "embed", "embeds", "file", "files", "attachments", "poll", "stickers")
#: Keywords a plain message may carry that a Components V2 one may not.
_TEXT_ONLY = ("tts", "suppress_embeds", "content")


def notice_colour(text: str) -> int:
    """The accent a notice wears, read off how its text opens."""
    head = str(text or "").lstrip()
    if head.startswith(_FAILURE_MARKS):
        return NOTICE_FAILURE
    if head.startswith(_WAIT_MARKS):
        return NOTICE_WAIT
    if head.startswith(_SUCCESS_MARKS):
        return NOTICE_SUCCESS
    return NOTICE_NEUTRAL


def notice_card(text: Any) -> Card:
    body = "" if text is None else str(text)
    return Card(description=body, colour=notice_colour(body))


def is_plain(content: Any, kwargs: dict[str, Any]) -> bool:
    """A reply that is text and nothing else - the only kind a notice replaces."""
    if content is None or not str(content).strip():
        return False
    return not any(kwargs.get(key) is not None for key in _NOT_PLAIN)


def as_notice(content: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """The keywords that send ``content`` as a notice card instead."""
    out = {key: value for key, value in kwargs.items() if key not in _TEXT_ONLY and key not in _NOT_PLAIN}
    out["view"] = card_view(notice_card(content))
    return out


# Which interactions' original responses are notices or a slash command's
# "thinking" placeholder, so a later text-only edit of that response is drawn
# as a notice too (a V2 message cannot take content). Keyed by interaction id,
# because discord.Interaction has __slots__ and no room for a flag; bounded.
_NOTICED: dict[int, bool] = {}
_NOTICED_LIMIT = 4096


def _mark(interaction_id: int) -> None:
    _NOTICED[int(interaction_id)] = True
    while len(_NOTICED) > _NOTICED_LIMIT:
        _NOTICED.pop(next(iter(_NOTICED)))


def _edits_a_layout(interaction: Any) -> bool:
    """Whether the message a text-only edit would land on is Components V2."""
    if _NOTICED.get(int(getattr(interaction, "id", 0) or 0)):
        return True
    message = getattr(interaction, "message", None)
    flags = getattr(message, "flags", None)
    return bool(getattr(flags, "components_v2", False))


class NoticeResponse(discord.InteractionResponse):
    """``interaction.response`` whose plain-text replies are notice cards."""

    __slots__ = ()

    async def send_message(self, content: Any = None, **kwargs: Any) -> Any:
        if is_plain(content, kwargs):
            _mark(self._parent.id)
            return await super().send_message(**as_notice(content, kwargs))
        return await super().send_message(content, **kwargs)

    async def defer(self, **kwargs: Any) -> Any:
        # A slash command's defer is a "thinking" message that its handler
        # later edits into the reply; that edit is drawn as a notice.
        if self._parent.type == discord.InteractionType.application_command or kwargs.get("thinking"):
            _mark(self._parent.id)
        return await super().defer(**kwargs)

    async def edit_message(self, **kwargs: Any) -> Any:
        content = kwargs.get("content")
        if is_plain(content, kwargs) and _edits_a_layout(self._parent):
            return await super().edit_message(content=None, embed=None, **as_notice(content, kwargs))
        return await super().edit_message(**kwargs)


class _NoticeFollowup:
    """``interaction.followup``, whose plain-text sends are notice cards."""

    def __init__(self, webhook: Any) -> None:
        self._webhook = webhook

    async def send(self, content: Any = None, **kwargs: Any) -> Any:
        if is_plain(content, kwargs):
            return await self._webhook.send(**as_notice(content, kwargs))
        return await self._webhook.send(content, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._webhook, name)


class NoticeInteraction(discord.Interaction):
    """Every interaction the bot receives (see :func:`install_notices`)."""

    __slots__ = ()

    @property
    def response(self) -> NoticeResponse:  # type: ignore[override]
        response = discord.Interaction.response.__get__(self, type(self))
        if type(response) is discord.InteractionResponse:
            response.__class__ = NoticeResponse
        return response  # type: ignore[return-value]

    @property
    def followup(self) -> Any:  # type: ignore[override]
        return _NoticeFollowup(discord.Interaction.followup.__get__(self, type(self)))

    async def edit_original_response(self, **kwargs: Any) -> Any:
        content = kwargs.get("content")
        if is_plain(content, kwargs) and _edits_a_layout(self):
            _mark(self.id)
            return await super().edit_original_response(content=None, embed=None, **as_notice(content, kwargs))
        return await super().edit_original_response(**kwargs)


def install_notices() -> None:
    """Build every interaction as a :class:`NoticeInteraction`.

    ``ConnectionState.parse_interaction_create`` constructs the one
    ``Interaction`` every command, button, select and modal receives, by the
    name ``Interaction`` in ``discord.state``. Pointing that name at the
    subclass is the single place a reply's shape can be decided for the
    whole bot. Idempotent."""
    import discord.state as state

    if getattr(state, "Interaction", None) is not NoticeInteraction:
        state.Interaction = NoticeInteraction  # type: ignore[attr-defined]
