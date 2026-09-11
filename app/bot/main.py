"""Composition root for the Discord bot.

Everything the bot is made of lives in the modules below (see
docs/history/MAIN_SPLIT_PLAN.md for the map). This file configures logging, wires
the command surface and the event-handler bindings onto the bot instance,
and provides `run()`. The four names app/bot/__init__.py re-exports -
XianxiaBot, bot, register_command_surface, run - are imported here for
that purpose; nothing else is.
"""
from __future__ import annotations

import logging

from .bot import XianxiaBot, bot
from .runtime import SETTINGS
from .surface import register_command_surface, register_event_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

register_event_handlers()
register_command_surface(bot)


def run() -> None:
    try:
        bot.run(SETTINGS.discord_token, log_handler=None)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
