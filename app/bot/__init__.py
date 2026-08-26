"""Discord bot package and explicit command-registration surface."""

from .main import XianxiaBot, bot, register_command_surface, run

__all__ = ["XianxiaBot", "bot", "register_command_surface", "run"]
