"""`/admin npc setmissing` (v1.0.0-rc.38): the disappearance a GM can stage
from Discord.

Held here rather than left to the playtest sweep, whose settle can time out
on a busy machine and stamp a leaf "slow" without reading its reply:

- an NPC the catalogue does not know is refused before the engine is asked;
- a lose reaches the engine as `admin.npc.set_missing {npc_name, missing:
  true}` and the reply names where they stand;
- a return sends `missing: false` and the reply names home;
- the engine's own refusal is relayed in its own words, never as the panel's
  failure text.

The engine audits the action, so the handler's audit call is the bot-side
mirror with `database_log=False`.
"""
from __future__ import annotations

import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _module():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.admin.inspect_sim")


def _interaction() -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False))


class SetMissingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = _module()
        self.handler = getattr(self.mod.admin_npc_setmissing, "callback", self.mod.admin_npc_setmissing)

    async def _run(self, engine: AsyncMock, known: bool = True, **kwargs):
        interaction = _interaction()
        with patch.object(self.mod, "require_admin", AsyncMock(return_value=True)), \
             patch.object(self.mod, "audit_admin", AsyncMock()) as audit, \
             patch.object(self.mod.DB, "get_npc_definition", AsyncMock(return_value={"title": "Herbalist"} if known else None)), \
             patch.object(self.mod.ENGINE, "action", engine):
            await self.handler(interaction, "Herbalist Mo", **kwargs)
        return interaction.response.send_message.call_args.args[0], audit

    async def test_an_unknown_npc_is_refused_before_the_engine(self):
        engine = AsyncMock()
        reply, audit = await self._run(engine, known=False)
        self.assertEqual(reply, "Unknown NPC.")
        engine.assert_not_awaited()
        audit.assert_not_awaited()

    async def test_a_lose_reaches_the_engine_and_names_where_they_stand(self):
        engine = AsyncMock(return_value={"status": "missing", "location": "Moonfen Marsh", "home_location": "Greenriver Town"})
        reply, audit = await self._run(engine, missing=True, reason="a story")
        op, _, payload = engine.await_args.args
        self.assertEqual(op, "admin.npc.set_missing")
        self.assertEqual(payload, {"npc_name": "Herbalist Mo", "missing": True, "reason": "a story"})
        self.assertIn("Moonfen Marsh", reply)
        self.assertIn("has not come home", reply)
        self.assertFalse(audit.await_args.kwargs["database_log"], "the engine audits; the bot mirrors")

    async def test_a_return_sends_missing_false_and_names_home(self):
        engine = AsyncMock(return_value={"status": "alive", "location": "Moonfen Marsh", "home_location": "Greenriver Town"})
        reply, _ = await self._run(engine, missing=False)
        self.assertFalse(engine.await_args.args[2]["missing"])
        self.assertIn("Greenriver Town", reply)
        self.assertIn("is home", reply)

    async def test_the_engines_refusal_is_relayed_in_its_own_words(self):
        engine = AsyncMock(side_effect=self.mod.GameEngineError("npc is dead"))
        reply, audit = await self._run(engine, missing=True)
        self.assertIn("npc is dead", reply)
        audit.assert_not_awaited()
        for failure in ("Something went wrong", "❌ The action could not be completed"):
            self.assertNotIn(failure, reply)


if __name__ == "__main__":
    unittest.main()
