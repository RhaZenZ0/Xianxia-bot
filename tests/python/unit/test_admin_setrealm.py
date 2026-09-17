"""`/admin player setrealm` (v1.0.0-rc.37): the realm lever on the Discord side.

The Discord admin panel had no realm override at all. This one takes the
qi pair and, optionally, the body pair, and three things about it are
held here rather than left to the playtest sweep, whose settle can time out
on a busy machine and record a leaf as "slow" without reading its reply:

- a member with no character is refused before the engine is asked;
- half a body pair is refused before the engine is asked, in the same
  words the engine would use;
- a whole pair reaches the engine as `body_realm_index`/`body_phase`, the
  reply names both ladders, and the engine's own refusal is relayed rather
  than turned into the panel's failure text.

The engine writes the audit row (`admin.player.set_realm` audits itself),
so the handler's own audit call is the bot-side mirror with
`database_log=False`, like teleport's.
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


MEMBER = SimpleNamespace(id=900001, mention="<@900001>")
SHEET = {"user_id": 900001, "realm_index": 1, "phase": 1, "body_realm_index": 0, "body_phase": 1}


class SetRealmTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = _module()
        self.handler = getattr(self.mod.admin_setrealm, "callback", self.mod.admin_setrealm)

    async def _run(self, engine: AsyncMock, character: dict | None = SHEET, **kwargs):
        interaction = _interaction()
        with patch.object(self.mod, "require_admin", AsyncMock(return_value=True)), \
             patch.object(self.mod, "audit_admin", AsyncMock()) as audit, \
             patch.object(self.mod.DB, "get_character", AsyncMock(return_value=character)), \
             patch.object(self.mod.ENGINE, "action", engine):
            await self.handler(interaction, MEMBER, **kwargs)
        return interaction.response.send_message.call_args.args[0], audit

    async def test_a_member_with_no_character_is_refused_before_the_engine(self):
        engine = AsyncMock()
        reply, audit = await self._run(engine, character=None, realm=3, stage=2)
        self.assertIn("no cultivation character", reply)
        engine.assert_not_awaited()
        audit.assert_not_awaited()

    async def test_half_a_body_pair_is_refused_before_the_engine(self):
        engine = AsyncMock()
        reply, _ = await self._run(engine, realm=3, stage=2, body_stage=9)
        self.assertIn("set together", reply)
        engine.assert_not_awaited()

    async def test_the_qi_pair_alone_leaves_the_body_ladder_out_of_the_payload(self):
        engine = AsyncMock(return_value={"realm_index": 3, "phase": 2})
        reply, audit = await self._run(engine, realm=3, stage=2, reason="a story correction")
        op, actor, payload = engine.await_args.args
        self.assertEqual(op, "admin.player.set_realm")
        self.assertEqual(payload, {"user_id": 900001, "realm_index": 3, "phase": 2, "reason": "a story correction"})
        self.assertIn("realm **3/2**", reply)
        self.assertNotIn("body", reply)
        self.assertFalse(audit.await_args.kwargs["database_log"], "the engine audits; the bot mirrors")

    async def test_a_whole_body_pair_reaches_the_engine_and_the_reply_names_both_ladders(self):
        engine = AsyncMock(return_value={"realm_index": 0, "phase": 9, "body_realm_index": 0, "body_phase": 9})
        reply, audit = await self._run(engine, realm=0, stage=9, body_realm=0, body_stage=9)
        payload = engine.await_args.args[2]
        self.assertEqual((payload["body_realm_index"], payload["body_phase"]), (0, 9))
        self.assertIn("realm **0/9**", reply)
        self.assertIn("body **0/9**", reply)
        self.assertEqual(audit.await_args.kwargs["after"]["body_phase"], 9)

    async def test_the_engines_refusal_is_relayed_in_its_own_words(self):
        engine = AsyncMock(side_effect=self.mod.GameEngineError("invalid body_realm_index (0-31) or body_phase (1-9)"))
        reply, audit = await self._run(engine, realm=0, stage=9, body_realm=40, body_stage=9)
        self.assertIn("invalid body_realm_index", reply)
        audit.assert_not_awaited()
        for failure in ("Something went wrong", "❌ The action could not be completed"):
            self.assertNotIn(failure, reply)


if __name__ == "__main__":
    unittest.main()
