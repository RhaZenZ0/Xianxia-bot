"""What a GM can see of an account's restart allowance (v1.0.13).

`character.reset` has reported `resets_used` and `resets_remaining` in its own
reply since v1.0.1, and that reply was the **only** place either number ever
appeared. A player learned how many chances were left by spending one - the
confirm step is the generic red "Are you sure?" and names no count - and a GM
could not look it up at all: `event_log` had no Python reader anywhere in the
tree, no dashboard view, no `/admin` panel, no API field.

Two GM surfaces read it now, and this gate is about **where the numbers come
from**, not about the two cards.

The count is rows of `event_log` filtered on `characterResetEvent`, and the
bound is `characterResetAllowance` - a Go string and a Go constant. A surface
that queried the one or restated the other would read correctly on the day it
was written and tell a GM "one left" on the day the engine refuses, which is
rc.46's rule seen from behind the counter and exactly what v1.0.11 took the
spiritual-root ladder out of the browser to stop. So `character.reset_status`
is one door, both surfaces ask it, and neither is allowed to know how the
answer is made.

The other half is the v1.0.8 footer lesson: an engine that cannot be reached
must say so. A card that fell back to zero would read as "never reset", and a
GM cannot tell that from "nobody answered".
"""
from __future__ import annotations

import importlib
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from tests.support import PROJECT_ROOT, code_only

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

OPERATION = "character.reset_status"
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
MEMBER = SimpleNamespace(id=900001, mention="<@900001>")

# What the engine answers. Spelled once here, as the engine spells it.
# How an engine fails to answer. The first two were all this gate ever tried,
# and the one a real outage raises is the third: the engine client calls httpx
# directly and wraps nothing, so a refused connection escaped both surfaces
# while the test that says it cannot stayed green (v1.0.14).
def _failures(game_engine_error):
    return (game_engine_error("down"), OSError("refused"),
            httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out"))


BLOCK = {
    "user_id": 900001, "resets_used": 2, "resets_remaining": 1, "reset_allowance": 3,
    "resets": [
        {"reset_number": 2, "name": "Mo Secondthoughts", "path": "Sword Cultivator",
         "spiritual_root": "Common", "realm_index": 1, "phase": 2, "created_at": 1700000000.0},
        {"reset_number": 1, "name": "Mo Firstthoughts", "path": "Body Refiner",
         "spiritual_root": "Mortal", "realm_index": 0, "phase": 1, "created_at": 1690000000.0},
    ],
}


def _inspect_module():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.admin.inspect_sim")


class TheDiscordPanelReportsWhatTheEngineSays(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = _inspect_module()
        self.line = self.mod.restart_allowance_line

    async def test_the_numbers_are_the_engines_and_the_lives_are_named(self):
        engine = AsyncMock(return_value=dict(BLOCK))
        with patch.object(self.mod.ENGINE, "action", engine):
            text = await self.line(MEMBER.id)
        self.assertIn("**2** of **3** used", text)
        self.assertIn("Mo Secondthoughts", text, "the newest abandoned life is what a GM is looking for")
        operation, actor, payload = engine.call_args.args
        self.assertEqual(operation, OPERATION)
        self.assertEqual(int(payload["user_id"]), MEMBER.id,
                         "the subject rides the payload: the actor is the GM who typed the command")

    async def test_an_allowance_of_five_reads_as_five(self):
        """The card cannot have an opinion about the bound.

        Asserting "2 of 3" against the shipped constant would pass just as well
        for a line that printed a 3 of its own, which is the whole thing this
        gate exists to forbid - so the engine is made to answer something the
        tree does not contain.
        """
        engine = AsyncMock(return_value={**BLOCK, "reset_allowance": 5, "resets_remaining": 3})
        with patch.object(self.mod.ENGINE, "action", engine):
            text = await self.line(MEMBER.id)
        self.assertIn("**2** of **5** used", text, (
            "the panel did not report the allowance the engine gave it, so it is carrying a copy - "
            "and a copy reads 'one left' on the day the engine refuses"))

    async def test_an_engine_that_does_not_answer_says_unknown(self):
        for failure in _failures(self.mod.GameEngineError):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(self.mod.ENGINE, "action", AsyncMock(side_effect=failure)), \
                     patch.object(self.mod, "log", SimpleNamespace(exception=lambda *a, **k: None)):
                    text = await self.line(MEMBER.id)
                self.assertIn("unknown", text)
                self.assertNotIn("0", text, (
                    "an unreachable engine produced a number. A zero here reads as 'never reset' and "
                    "a GM cannot tell it from 'nobody answered' - the `engine -` footer of v1.0.8"))

    async def test_a_member_with_no_character_is_still_reported(self):
        """The branch that makes the lever worth having.

        An account that reset and has not begun again has no `characters` row,
        which is exactly the state a GM asks about. Stopping at "no cultivation
        character" would hide the only fact left to know about them.
        """
        interaction = SimpleNamespace(user=SimpleNamespace(id=1),
                                      response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False))
        handler = getattr(self.mod.admin_inspect, "callback", self.mod.admin_inspect)
        with patch.object(self.mod, "require_admin", AsyncMock(return_value=True)), \
             patch.object(self.mod.DB, "get_character", AsyncMock(return_value=None)), \
             patch.object(self.mod.ENGINE, "action", AsyncMock(return_value=dict(BLOCK))):
            await handler(interaction, MEMBER)
        reply = interaction.response.send_message.call_args.args[0]
        self.assertIn("**2** of **3** used", reply, (
            "a GM inspecting somebody who reset and has not begun again is told only that they have "
            "no character, which is the one case this read exists for"))


class TheDashboardAsksTheSameDoor(unittest.IsolatedAsyncioTestCase):
    def _store(self, engine):
        from app.dashboard.server import ReadOnlyDashboardStore

        store = ReadOnlyDashboardStore.__new__(ReadOnlyDashboardStore)
        store._engine = engine
        return store

    async def test_the_block_is_the_engines_answer(self):
        engine = SimpleNamespace(action=AsyncMock(return_value=dict(BLOCK)))
        got = await self._store(engine).restart_allowance(MEMBER.id)
        self.assertEqual(int(got["reset_allowance"]), 3)
        operation, actor, payload = engine.action.call_args.args
        self.assertEqual(operation, OPERATION)
        self.assertEqual(actor, 0, "the dashboard is not a cultivator; it asks as nobody")
        self.assertEqual(int(payload["user_id"]), MEMBER.id)

    async def test_no_engine_and_a_broken_engine_both_answer_nothing(self):
        self.assertEqual(await self._store(None).restart_allowance(MEMBER.id), {})
        from app.dashboard.server import GameEngineError

        for failure in _failures(GameEngineError):
            with self.subTest(failure=type(failure).__name__):
                engine = SimpleNamespace(action=AsyncMock(side_effect=failure))
                self.assertEqual(await self._store(engine).restart_allowance(MEMBER.id), {}, (
                    "an unreachable engine must leave the block empty so the card draws an em dash; a "
                    "zeroed block would be a placeholder that looks like a value, and an escaped error "
                    "costs the whole Player Editor"))


class NeitherSurfaceKnowsHowTheAnswerIsMade(unittest.TestCase):
    """The rule, held over the source rather than over the two cards.

    Read with comments and docstrings blanked, because the prose here and in
    `server.py` names `event_log` and `character_reset` in order to explain why
    nothing may use them - a gate that cannot tell prose from code is
    decoration (rc.52), and this one's first run flagged the file it was
    written for.
    """

    SURFACES = ("app/bot/admin/inspect_sim.py", "app/dashboard/server.py")
    # core.py creates the table; it is the one file allowed to name it.
    TABLE_OWNER = "app/database/core.py"

    def setUp(self):
        self.sources = {rel: code_only((PROJECT_ROOT / rel).read_text(encoding="utf-8"))
                        for rel in self.SURFACES}
        # Asserted before it is trusted (rc.57): a blanker that returned its
        # input empty would make every scan below pass by finding nothing.
        self.assertIn("async def restart_allowance_line", self.sources["app/bot/admin/inspect_sim.py"],
                      "the code-only reader lost the function it is scanning; the gate is broken, not the tree")
        self.assertNotIn("A gate that cannot tell prose", self.sources["app/dashboard/server.py"],
                         "the reader did not blank the docstrings; the gate is broken, not the tree")

    def test_both_surfaces_ask_the_one_door(self):
        for rel, source in self.sources.items():
            with self.subTest(rel=rel):
                self.assertIn(OPERATION, source, f"{rel} does not ask the engine what the account has spent")
        js = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("p.resets", js, "the Player Editor never reads the block the API sends it")

    def test_no_surface_counts_the_rows_itself(self):
        offenders = []
        for rel, source in self.sources.items():
            if "event_log" in source:
                offenders.append(f"{rel} reads event_log")
            if "character_reset" in source and OPERATION not in ("character_reset",):
                # The dotted operation names are fine; the underscored
                # event_type is the engine's private spelling.
                if re.search(r"['\"]character_reset['\"]", source):
                    offenders.append(f"{rel} names the character_reset event type")
        self.assertFalse(offenders, (
            "a GM surface counts the resets itself. The count is rows of a Go-owned table filtered on "
            "a Go constant, and two raw readers of it is how a count and the limit it is counted "
            f"against part company: {offenders}"))

    def test_the_table_has_one_owner_outside_the_engine(self):
        """`event_log` is created by Python's migration and read by nobody."""
        named = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel == self.TABLE_OWNER:
                continue
            if "event_log" in code_only(path.read_text(encoding="utf-8")):
                named.append(rel)
        self.assertFalse(named, (
            "event_log has a Python reader outside the migration that creates it. Its rows are the "
            f"engine's record of what an account spent, and the engine is what reports them: {named}"))

    def test_no_surface_defaults_the_allowance_to_a_number(self):
        """A fallback that looks like a value is not a sentinel."""
        fallback = re.compile(r"reset_allowance[\"']?\s*(?:\|\||\?\?|,|:)\s*\d")
        offenders = [rel for rel, source in self.sources.items() if fallback.search(source)]
        js = code_only_js((PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8"))
        if fallback.search(js):
            offenders.append("dashboard/app.js")
        self.assertFalse(offenders, (
            "a surface supplies its own number when the engine did not answer, so a GM reads a bound "
            f"nothing enforces instead of being told nobody replied: {offenders}"))
        self.assertIn("reset_allowance===undefined?'—'", js.replace(" ", ""), (
            "the Player Editor's tile does not draw an em dash for an unanswered block, so an "
            "unreachable engine renders as a number"))


class TheEngineDoorIsWhereTheRuleLives(unittest.TestCase):
    def setUp(self):
        self.authoritative = (GO / "authoritative.go").read_text(encoding="utf-8")
        self.reset = (GO / "character_reset.go").read_text(encoding="utf-8")

    def test_the_operation_is_allowlisted_as_a_read(self):
        self.assertIn(f'"{OPERATION}"', self.authoritative)
        queries = self.authoritative.split("var authoritativeQueries")[1].split("}")[0]
        self.assertIn(f'"{OPERATION}"', queries, (
            "character.reset_status is not on the read allowlist, so it is either unreachable or a "
            "mutation - and a GM read that can write is not a read"))
        self.assertIn(f'case "{OPERATION}":', self.authoritative)

    def test_the_allowance_is_spelled_once_and_in_go(self):
        self.assertIn("const characterResetAllowance = 3", self.reset,
                      "the allowance moved; the gate is broken, not the tree")
        self.assertIn("int64(characterResetAllowance)", self.reset, (
            "the status reports something other than the constant the action enforces, so the card a "
            "GM reads and the bound the engine applies are free to differ"))


def code_only_js(text: str) -> str:
    """JavaScript with its block and line comments blanked.

    Same rule as the Python reader for the same reason: the comment above the
    Player Editor's `resets` binding explains the fallback it forbids.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(?<![:\w])//.*$", "", line) for line in text.splitlines())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
