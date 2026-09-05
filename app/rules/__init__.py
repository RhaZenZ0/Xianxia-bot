"""Gameplay rules and content helpers: pure functions and tables over world
content, with no Discord, HTTP or database dependency. Everything above
(bot, ai, dashboard, simulation, database) may import from here; nothing
here imports from any of them (tests/python/unit/test_app_layout.py)."""
