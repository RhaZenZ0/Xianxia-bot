"""Admin tools: /admin npc setmissing, the audited maintenance branches, the dashboard's name links and the quest editor's recipes.

Merged from:

test_admin_npc_setmissing.py — `/admin npc setmissing` (v1.0.0-rc.38): the disappearance a GM can stage
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

test_a_vacuum_refused_is_a_wait.py — A refused VACUUM is told as a wait, not a wiring failure (v1.2.3).

The engine refuses a VACUUM at once when a database session is between its
execute and its commit, rather than sitting out busy_timeout and failing. The
Discord handler must pass that refusal on in words - the generic wrapper would
print one of the three wiring-failure strings over a condition that is neither
wiring nor failure - and must write no audit row for it, since nothing changed.

test_maintenance_is_audited.py — `/admin server maintenance` writes an audit row for every branch that
changes state (design rule 6).

Vacuum and Cleanup were the two that did not (v1.2.1): the content resync
was audited by the engine, and the other two ran and told nobody.

test_dashboard_names_open_what_they_name.py — A name in a dashboard table opens what it names (v1.27.0).

The player drawer and the NPC drawer existed and six views linked their names;
nineteen others printed a player or an NPC as plain text, so a GM reading who
bid on a lot or who holds a grudge had to go and search for them. `table()`
now renders every column keyed on a known name field through one helper,
`linkedCell`, and the click is handled once, on the document.

test_the_quest_editor_knows_recipes.py — The dashboard's stand-in world carries recipes (v1.2.1).

`validate_quest_definition` reads `world.recipes` to resolve a `craft`
objective, and `_QuestWorld` handed it locations, npcs and items - so every
craft objective the Quest Editor saved or the coverage report checked was "an
unknown recipe", whatever was typed.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.support import bot_function_source, install_aiosqlite_shim

install_aiosqlite_shim()

from app.dashboard.server import _QuestWorld  # noqa: E402
from app.rules.quests import validate_quest_definition  # noqa: E402

pytestmark = pytest.mark.unit

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


# --- from test_admin_npc_setmissing.py ---

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


# --- from test_a_vacuum_refused_is_a_wait.py ---

class ARefusedVacuumIsAWait(unittest.TestCase):
    def _vacuum_branch(self) -> ast.If:
        tree = ast.parse(bot_function_source("admin_maintenance"))
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and 'action.value == "vacuum"' in ast.unparse(node.test).replace("'", '"'):
                return node
        self.fail("the vacuum branch could not be found; the reader is broken, not the tree")

    def test_the_busy_refusal_is_caught_and_said(self):
        branch = self._vacuum_branch()
        handlers = [h for n in ast.walk(branch) if isinstance(n, ast.Try) for h in n.handlers]
        names = {ast.unparse(h.type) for h in handlers if h.type is not None}
        self.assertIn("RemoteDatabaseError", names, "DB.vacuum()'s refusal is not caught, so a busy session prints a wiring failure")
        text = "\n".join(ast.unparse(h) for h in handlers)
        self.assertIn("sessions_busy", text, "the handler does not tell the busy refusal from a real failure")
        self.assertNotIn("audit_admin", text, "a refused vacuum changed nothing and must not be audited")


# --- from test_maintenance_is_audited.py ---

class MaintenanceIsAudited(unittest.TestCase):
    def test_vacuum_and_cleanup_each_write_their_row(self):
        tree = ast.parse(bot_function_source("admin_maintenance"))
        audited = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "audit_admin":
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    audited.add(str(node.args[1].value))
        self.assertTrue(audited, "the handler calls audit_admin nowhere; the reader is broken, not the tree")
        for action in ("admin.server.vacuum", "admin.server.cleanup"):
            self.assertIn(action, audited, f"{action} changes the database and writes no admin_audit_log row")


# --- from test_dashboard_names_open_what_they_name.py ---

APP = (Path(__file__).resolve().parents[3] / "dashboard" / "app.js").read_text(encoding="utf-8")


def _body(name: str) -> str:
    start = APP.index(f"function {name}(")
    depth, i = 0, APP.index("){", start) + 1
    for j in range(i, len(APP)):
        depth += {"{": 1, "}": -1}.get(APP[j], 0)
        if depth == 0:
            return APP[i:j + 1]
    raise AssertionError(f"{name} has no balanced body")


class DashboardNamesOpenWhatTheyName(unittest.TestCase):
    def test_the_table_renders_a_bare_key_through_the_link_rule(self) -> None:
        body = _body("table")
        self.assertIn("linkedCell(r,h[1])", body)
        self.assertNotIn("esc(r[h[1]])", body, "a bare key is printed past the link rule")

    def test_the_link_rule_knows_the_player_and_npc_name_fields(self) -> None:
        body = _body("linkedCell")
        self.assertIn("playerLink(", body)
        self.assertIn("npcLink(", body)
        for key in ("player_name", "seller_name", "bidder_name", "owner_name"):
            self.assertRegex(APP, rf"PLAYER_NAME_IDS=\{{[^}}]*\b{key}:")
        self.assertRegex(APP, r"NPC_NAME_KEYS=new Set\(\[[^\]]*'npc_name'")

    def test_one_handler_opens_every_link(self) -> None:
        self.assertTrue(re.search(r"document\.addEventListener\('click'", APP))
        self.assertIn("showPlayer(player.dataset.player)", APP)
        self.assertIn("showNpc(npc.dataset.npc)", APP)


# --- from test_the_quest_editor_knows_recipes.py ---

DRAFT = {
    "title": "Forge Something",
    "description": "Make a Recovery Pill for the hall.",
    "objectives": [{"type": "craft", "target": "Recovery Pill", "count": 1}],
    "rewards": {"insight_xp": 5},
}
BUDGET = {"max_xp": 100, "max_stones": 100, "max_items": 5}


class TheQuestEditorKnowsRecipes(unittest.TestCase):
    def test_a_craft_objective_resolves_against_the_recipes_it_names(self):
        world = _QuestWorld({"locations": {}, "npcs": {}, "items": {}, "recipes": {"Recovery Pill": {"profession": "Alchemy"}}})
        definition, errors = validate_quest_definition(DRAFT, world, BUDGET)
        self.assertEqual(errors, [], "a recipe the world carries was refused")
        self.assertIsNotNone(definition)

    def test_a_recipe_the_world_lacks_is_still_refused(self):
        world = _QuestWorld({"locations": {}, "npcs": {}, "items": {}, "recipes": {}})
        _, errors = validate_quest_definition(DRAFT, world, BUDGET)
        self.assertTrue(errors, "a recipe nothing carries was accepted")


if __name__ == "__main__":
    unittest.main()
