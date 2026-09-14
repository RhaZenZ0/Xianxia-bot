from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from discord import app_commands

CommandHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ActionBinding:
    command: app_commands.Command[Any, ..., Any]
    handler: CommandHandler


class ActionRegistry:
    """Explicit mapping between Discord command metadata and gameplay handlers.

    Gameplay commands used by the interactive hubs are metadata objects only; they
    are not temporarily added to the Discord tree and removed later.  The mapping
    also lets GUI routing invoke the canonical handler without reaching through
    discord.py's ``Command.callback`` implementation detail.
    """

    def __init__(self) -> None:
        self._bindings: dict[int, ActionBinding] = {}
        self._roots: dict[str, app_commands.Command[Any, ..., Any]] = {}

    def bind(
        self,
        command: app_commands.Command[Any, ..., Any],
        handler: CommandHandler,
        *,
        root: bool = False,
    ) -> app_commands.Command[Any, ..., Any]:
        key = id(command)
        if key in self._bindings:
            raise RuntimeError(f"Command already registered: {command.qualified_name}")
        self._bindings[key] = ActionBinding(command=command, handler=handler)
        if root:
            name = str(command.name)
            if name in self._roots:
                raise RuntimeError(f"Duplicate root action registration: {name}")
            self._roots[name] = command
        return command

    def root_command(self, **kwargs: Any):
        # ``guild`` belongs to CommandTree registration rather than Command
        # construction.  Keep it accepted at call sites so command metadata stays
        # close to the handler while registration remains centralized.
        kwargs.pop("guild", None)

        def decorator(handler: CommandHandler):
            command = app_commands.command(**kwargs)(handler)
            return self.bind(command, handler, root=True)

        return decorator

    def group_command(self, group: app_commands.Group, **kwargs: Any):
        def decorator(handler: CommandHandler):
            command = group.command(**kwargs)(handler)
            return self.bind(command, handler)

        return decorator

    def handler_for(self, command: Any) -> CommandHandler:
        binding = self._bindings.get(id(command))
        if binding is None:
            raise KeyError(f"Unregistered action command: {getattr(command, 'qualified_name', command)!r}")
        return binding.handler

    def root(self, name: str) -> app_commands.Command[Any, ..., Any]:
        return self._roots[name]

    def all_commands(self) -> dict[str, app_commands.Command[Any, ..., Any]]:
        """Every bound command by qualified name, roots and group leaves alike.

        The shorthand (v1.0.0) resolves a typed word against this whole table,
        not only the roots.
        """
        commands: dict[str, app_commands.Command[Any, ..., Any]] = {}
        for binding in self._bindings.values():
            name = str(getattr(binding.command, "qualified_name", ""))
            if name:
                commands.setdefault(name, binding.command)
        return commands

    def qualified(self, name: str) -> app_commands.Command[Any, ..., Any]:
        """A bound command by its qualified name ("travel go"), root or leaf.

        Typed play (v0.33.0) reaches group leaves this way; roots keep the
        cheaper dictionary above."""
        if name in self._roots:
            return self._roots[name]
        command = self.all_commands().get(name)
        if command is None:
            raise KeyError(f"Unregistered action command: {name!r}")
        return command

    def roots(self) -> dict[str, app_commands.Command[Any, ..., Any]]:
        return dict(self._roots)


class HandlerRegistry:
    """Small explicit registry for non-slash UI routes such as event buttons."""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler) -> None:
        key = str(name)
        if key in self._handlers:
            raise RuntimeError(f"Duplicate UI handler registration: {key}")
        self._handlers[key] = handler

    async def invoke(self, name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            handler = self._handlers[str(name)]
        except KeyError as exc:
            raise RuntimeError(f"UI handler is not registered: {name}") from exc
        return await handler(*args, **kwargs)


class RestoreRegistry:
    """Panels that have to be re-registered after a restart (v1.0.0-rc.22).

    Discord keeps a message forever; a `discord.ui.View` lives only as long as
    the process that sent it. A panel anchored to a message players come back
    to - an event scene open for two days, an exploration encounter open for
    six hours - therefore comes back from a reboot with dead controls unless
    something registers it again. That is what this collects.

    A panel only belongs here if it outlives a process. The hub pages, the
    pickers and the confirms all time out inside fifteen minutes and are
    re-opened by running the command again; registering those would leave
    live-looking buttons on messages whose moment has passed, which is the
    opposite of the fix.

    Each restorer is `async (bot) -> int`, answering how many it restored. One
    failing restorer never costs the others, and none of them can stop the bot
    starting: a world that comes up with one stale panel is better than a world
    that does not come up.
    """

    def __init__(self) -> None:
        self._restorers: dict[str, Any] = {}

    def register(self, name: str, restorer: Any) -> None:
        key = str(name)
        if key in self._restorers:
            raise RuntimeError(f"Duplicate view restorer registration: {key}")
        self._restorers[key] = restorer

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._restorers))

    async def restore_all(self, bot: Any) -> dict[str, int]:
        """Every registered panel family, in name order. Never raises."""
        import logging

        restored: dict[str, int] = {}
        for name in sorted(self._restorers):
            try:
                restored[name] = int(await self._restorers[name](bot) or 0)
            except Exception:
                logging.getLogger("xianxia").exception("Could not restore the %s panels", name)
                restored[name] = 0
        return restored


ACTIONS = ActionRegistry()
EVENT_HANDLERS = HandlerRegistry()
VIEW_RESTORERS = RestoreRegistry()


def registered_root_command(**kwargs: Any):
    return ACTIONS.root_command(**kwargs)


def registered_group_command(group: app_commands.Group, **kwargs: Any):
    return ACTIONS.group_command(group, **kwargs)
