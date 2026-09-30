"""The breakthrough is recorded the moment the engine decides it (v1.12.3).

`/breakthrough` reports the `breakthrough` objective after the engine has
committed - and the report sat below `NARRATOR_CONTEXT.build`, the narration,
the RAG memory write and the whole reply assembly, every one of which runs
*after* the commit and can raise. A raise there cost the player the stage's
quest step for good: the breakthrough had happened and nothing would ever say
so (v1.0.5's finding about `/craft`, in the command that was written next).

Driven with a context builder that raises, because the source reads correctly
in both orders - the position of one statement is the whole fault.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

CULTIVATION = PROJECT_ROOT / "app" / "bot" / "commands" / "cultivation.py"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _module():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.cultivation")


def _handler(module):
    callback = module.breakthrough.callback
    return getattr(callback, "__wrapped__", callback)


class _Interaction:
    id = 99

    def __init__(self):
        self.user = SimpleNamespace(id=7)
        self.sent = []
        done = {"v": False}

        async def defer(**_kw):
            done["v"] = True

        async def send_message(*a, **kw):
            self.sent.append(("response", a, kw))
            done["v"] = True

        async def followup_send(*a, **kw):
            self.sent.append(("followup", a, kw))

        self.response = SimpleNamespace(defer=defer, send_message=send_message, is_done=lambda: done["v"])
        self.followup = SimpleNamespace(send=followup_send)


def _drive(module, *, success, context_raises):
    recorded = []

    async def require_character(_interaction):
        return {"user_id": 7, "name": "Lin", "location": "Greenriver Town", "realm_index": 0}

    async def current_world_time():
        return SimpleNamespace(total_minutes=1234)

    async def authoritative_action(*_a, **_k):
        return {"result": {"success": success, "to_realm": "Qi Refining", "to_stage": 1, "roll": {"die1": 5, "die2": 5, "modifier": 2, "total": 12, "tn": 10, "degree": "success"}}}

    async def build(*_a, **_k):
        if context_raises:
            raise RuntimeError("the narrator context failed after the engine committed")
        return SimpleNamespace(text="", game_minute=1234)

    async def record(user_id, objective, **kwargs):
        recorded.append((user_id, objective, kwargs))
        return []

    async def nothing(*_a, **_k):
        return None

    async def reply_long(_interaction, _text):
        return None

    interaction = _Interaction()
    with patch.object(module, "require_character", require_character), \
            patch.object(module, "current_world_time", current_world_time), \
            patch.object(module.ENGINE, "authoritative_action", authoritative_action), \
            patch.object(module.NARRATOR_CONTEXT, "build", build), \
            patch.object(module, "record_quest_progress", record), \
            patch.object(module, "announce_quest_progress", nothing), \
            patch.object(module, "reply_long", reply_long), \
            patch.object(module.NARRATOR, "narrate_breakthrough", nothing), \
            patch.object(module.DB, "add_rag_memory", nothing):
        error = None
        try:
            asyncio.run(_handler(module)(interaction, False, False))
        except Exception as exc:  # the handler is allowed to raise here; the record is what is held
            error = exc
    return recorded, error


class ABreakthroughIsRecordedWhateverComesAfter(unittest.TestCase):
    def test_a_success_is_recorded_even_when_the_narrators_context_raises(self):
        module = _module()
        recorded, error = _drive(module, success=True, context_raises=True)
        self.assertIsNotNone(error, "the fake context builder did not reach the handler; the drive is broken, not the tree")
        self.assertEqual([(u, o) for u, o, _k in recorded], [(7, "breakthrough")],
                         "the stage's quest report was lost behind code that can raise")

    def test_a_failure_reports_nothing(self):
        module = _module()
        recorded, _error = _drive(module, success=False, context_raises=False)
        self.assertEqual(recorded, [], "only a breakthrough that landed is a gate crossed")

    def test_a_success_that_nothing_raised_on_is_recorded_once(self):
        module = _module()
        recorded, error = _drive(module, success=True, context_raises=False)
        self.assertIsNone(error)
        self.assertEqual(len(recorded), 1)


class TheReportPrecedesEverythingAfterTheCommit(unittest.TestCase):
    def test_the_record_comes_before_the_context_the_narration_the_memory_and_the_reply(self):
        tree = ast.parse(CULTIVATION.read_text(encoding="utf-8"))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "breakthrough")
        lines: dict[str, int] = {}
        for call in ast.walk(node):
            if isinstance(call, ast.Call):
                lines.setdefault(ast.unparse(call.func), call.lineno)
        self.assertIn("record_quest_progress", lines)
        record = lines["record_quest_progress"]
        for later in ("NARRATOR_CONTEXT.build", "NARRATOR.narrate_breakthrough", "DB.add_rag_memory", "reply_long"):
            self.assertIn(later, lines, f"{later} is not in /breakthrough; the reader is broken, not the tree")
            self.assertLess(record, lines[later], f"the record is written after {later}, which can raise")
        self.assertLess(record, lines["announce_quest_progress"], "the telling must come after the record (v1.0.5)")


if __name__ == "__main__":
    unittest.main()
