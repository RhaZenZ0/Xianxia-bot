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

    def qualified(self, name: str) -> app_commands.Command[Any, ..., Any]:
        """A bound command by its qualified name ("travel go"), root or leaf.

        Typed play (v0.33.0) reaches group leaves this way; roots keep the
        cheaper dictionary above."""
        if name in self._roots:
            return self._roots[name]
        for binding in self._bindings.values():
            if str(getattr(binding.command, "qualified_name", "")) == name:
                return binding.command
        raise KeyError(f"Unregistered action command: {name!r}")

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


ACTIONS = ActionRegistry()
EVENT_HANDLERS = HandlerRegistry()


def registered_root_command(**kwargs: Any):
    return ACTIONS.root_command(**kwargs)


def registered_group_command(group: app_commands.Group, **kwargs: Any):
    return ACTIONS.group_command(group, **kwargs)
