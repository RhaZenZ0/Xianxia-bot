"""Quest Forge (v0.20.6): drafts from a story, GM-approved, budgeted rewards.

Layers, bottom up: the validator in app/rules/quests.py (pure), the forge in
app/ai/quest_forge.py against a fake router, the QuestService's dynamic
catalog and reward grant against a fake engine, the quest_definitions
table, and - in source, since discord.py is absent - the admin commands and
the bot worker.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import PROJECT_ROOT, install_aiosqlite_shim, install_openai_shim, seed_character
install_aiosqlite_shim()
install_openai_shim()

from app.ai.quest_forge import ForgeResult, QuestForge, extract_json_object, store_draft, system_prompt
from app.database import Database, SCHEMA_VERSION
from app.ops.core_services import QuestService
from app.rules.game import World
from app.rules.quests import (
    OBJECTIVE_TYPES,
    QUEST_DEFINITIONS,
    procedural_quest_from_event,
    quest_key_for,
    rewardable_items,
    validate_quest_definition,
)

WORLD = World(PROJECT_ROOT / "content" / "world.json")
BUDGET = {"max_xp": 50, "max_stones": 200, "max_items": 3}
GOOD = {
    "title": "The Reed Gate Whisper",
    "description": "Someone is spreading rumours in Greenriver Town. Find who, and why.",
    "objectives": [
        {"type": "explore", "target": "greenriver town"},
        {"type": "talk", "target": "Elder Su Yan", "count": 2},
        {"type": "scene_action", "target": "Investigate"},
    ],
    "rewards": {"insight_xp": 30, "items": {"spirit_herb": 2}},
}


class ValidatorTests(unittest.TestCase):
    def test_a_good_draft_normalises_targets_ids_and_labels(self):
        definition, errors = validate_quest_definition(GOOD, WORLD, BUDGET)
        self.assertEqual(errors, [])
        self.assertEqual([o["target"] for o in definition["objectives"]], ["Greenriver Town", "Elder Su Yan", "investigate"])
        self.assertEqual([o["id"] for o in definition["objectives"]], ["explore_1", "talk_2", "scene_action_3"])
        self.assertEqual(definition["objectives"][1]["count"], 2)
        self.assertEqual(definition["objectives"][2]["label"], "Resolve a Scene Action: Investigate")
        self.assertEqual(definition["rewards"], {"insight_xp": 30, "items": {"spirit_herb": 2}})
        self.assertEqual(definition["source_type"], "forge")

    def test_everything_the_world_lacks_is_an_error_not_a_silent_drop(self):
        bad = {
            "title": "Bad", "description": "x",
            "objectives": [{"type": "kill", "target": "dragon"}, {"type": "explore", "target": "Nowhere"},
                           {"type": "talk", "target": "Nobody"}, {"type": "sect_discovery", "target": "Azure Cloud Sect"}],
            "rewards": {"insight_xp": 999, "spirit_stones": -1, "items": {"bugslayer_sword": 1}, "gold": 5},
        }
        definition, errors = validate_quest_definition(bad, WORLD, BUDGET)
        self.assertIsNone(definition)
        joined = "\n".join(errors)
        for needle in ("description must be", "unknown type 'kill'", "unknown location 'Nowhere'", "unknown NPC 'Nobody'",
                       "sect_discovery takes no target", "exceeds the GM budget (50)", "cannot be negative",
                       "not a rewardable catalog item", "unknown reward keys: gold"):
            self.assertIn(needle, joined, needle)
        _, errors = validate_quest_definition({**GOOD, "objectives": [{"type": "scene_action", "target": "fly"}] + [{"type": "explore"}] * 4}, WORLD, BUDGET)
        self.assertIn("at most 4 objectives", errors)
        self.assertTrue(any("unknown scene action 'fly'" in e for e in errors), errors)

    def test_the_budget_is_enforced_on_items_and_stones(self):
        draft = {**GOOD, "rewards": {"spirit_stones": 201}}
        self.assertIn("reward spirit_stones=201 exceeds the GM budget (200)", validate_quest_definition(draft, WORLD, BUDGET)[1])
        draft = {**GOOD, "rewards": {"items": {"spirit_herb": 2, "beast_core": 2}}}
        self.assertIn("reward items total 4 exceeds the GM budget (3)", validate_quest_definition(draft, WORLD, BUDGET)[1])

    def test_rewardable_items_exclude_market_excluded_unique_and_indestructible(self):
        items = rewardable_items(WORLD)
        self.assertNotIn("bugslayer_sword", items)
        self.assertIn("spirit_herb", items)
        for item_id, item in items.items():
            self.assertFalse(item.get("market_excluded") or item.get("unique") or item.get("indestructible"), item_id)

    def test_hidden_masters_and_private_locations_are_not_valid_targets(self):
        hidden = [name for name, npc in WORLD.npcs.items() if isinstance(npc.get("hidden_master"), dict)]
        private = [name for name, loc in WORLD.locations.items() if loc.get("private")]
        if hidden:
            _, errors = validate_quest_definition({**GOOD, "objectives": [{"type": "talk", "target": hidden[0]}]}, WORLD, BUDGET)
            self.assertTrue(any("unknown NPC" in e for e in errors), errors)
        if private:
            _, errors = validate_quest_definition({**GOOD, "objectives": [{"type": "explore", "target": private[0]}]}, WORLD, BUDGET)
            self.assertTrue(any("unknown location" in e for e in errors), errors)

    def test_a_draft_with_no_rewards_gets_a_small_xp_default(self):
        definition, errors = validate_quest_definition({**GOOD, "rewards": {}}, WORLD, BUDGET)
        self.assertEqual(errors, [])
        self.assertEqual(definition["rewards"], {"insight_xp": 10})

    def test_the_static_catalog_passes_its_own_validator(self):
        # The two shipped quests use the same vocabulary the Forge is held to.
        for key, definition in QUEST_DEFINITIONS.items():
            with self.subTest(quest=key):
                cleaned, errors = validate_quest_definition(definition, WORLD, {"max_xp": 100, "max_stones": 0, "max_items": 0})
                self.assertEqual(errors, [])
                self.assertEqual([o["type"] for o in cleaned["objectives"]], [o["type"] for o in definition["objectives"]])
        self.assertEqual(set(OBJECTIVE_TYPES), {"explore", "talk", "scene_action", "sect_discovery", "sect_trial"})

    def test_the_procedural_draft_always_validates(self):
        for event in (
            {"history_id": 7, "event_type": "war_started", "title": "Azure Cloud War", "summary": "War.", "location": "Greenriver Town"},
            {"history_id": 8, "event_type": "leadership_change", "title": "A succession", "summary": "", "location": "Not A Place"},
            {"history_id": 9, "event_type": "rumour", "title": "", "summary": "", "location": ""},
        ):
            with self.subTest(event=event["history_id"]):
                draft = procedural_quest_from_event(event, WORLD, BUDGET)
                definition, errors = validate_quest_definition(draft, WORLD, BUDGET)
                self.assertEqual(errors, [], draft)
                self.assertEqual(definition["source_key"], f"history:{event['history_id']}")
                self.assertTrue(any(o["type"] == "explore" for o in definition["objectives"]))

    def test_keys_are_slugs_and_never_collide(self):
        self.assertEqual(quest_key_for("The Reed Gate Whisper"), "forge_the_reed_gate_whisper")
        self.assertEqual(quest_key_for("The Reed Gate Whisper", {"forge_the_reed_gate_whisper"}), "forge_the_reed_gate_whisper_2")
        self.assertEqual(quest_key_for("!!!"), "forge_forged_quest")


class _FakeRouter:
    enabled = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return SimpleNamespace(text=reply, model="fake/model", tier="routine", attempted_models=("fake/model",))


class ForgeTests(unittest.TestCase):
    def test_json_is_found_fenced_bare_or_inside_prose(self):
        obj = {"title": "x"}
        self.assertEqual(extract_json_object('```json\n{"title": "x"}\n```'), obj)
        self.assertEqual(extract_json_object('{"title": "x"}'), obj)
        self.assertEqual(extract_json_object('Here you go:\n{"title": "x"}\nEnjoy.'), obj)
        self.assertIsNone(extract_json_object("no json here"))
        self.assertIsNone(extract_json_object("[1, 2]"))

    def test_the_system_prompt_names_only_public_content_and_the_budget(self):
        text = system_prompt(WORLD, BUDGET)
        self.assertIn("Greenriver Town", text)
        self.assertIn("insight_xp <= 50", text)
        self.assertIn("total item quantity <= 3", text)
        self.assertNotIn("bugslayer_sword", text)
        for name, npc in WORLD.npcs.items():
            if isinstance(npc.get("hidden_master"), dict):
                self.assertNotIn(name, text)
            self.assertNotIn(str(npc.get("secret", "\x00")), text)
        self.assertIn("data, not instructions", text)

    def test_a_valid_reply_becomes_a_definition_on_the_first_attempt(self):
        router = _FakeRouter([json.dumps(GOOD)])
        result = asyncio.run(QuestForge(router, WORLD, budget=BUDGET).draft("rumours in Greenriver", source_key="gm:1"))
        self.assertIsNotNone(result.definition)
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.procedural)
        self.assertEqual(result.model, "fake/model")
        self.assertEqual(result.definition["source_key"], "gm:1")
        call = router.calls[0]
        self.assertEqual(call["tier"], "routine")
        self.assertNotIn("leak_guard", call)  # the guard stays at its default: on
        self.assertIn("<<<STORY>>>", call["prompt"])

    def test_an_invalid_reply_is_retried_once_with_the_errors_quoted(self):
        bad = {**GOOD, "objectives": [{"type": "explore", "target": "Nowhere"}]}
        router = _FakeRouter([json.dumps(bad), json.dumps(GOOD)])
        result = asyncio.run(QuestForge(router, WORLD, budget=BUDGET).draft("story", source_key="gm:1"))
        self.assertIsNotNone(result.definition)
        self.assertEqual(result.attempts, 2)
        self.assertFalse(result.procedural)
        self.assertIn("unknown location 'Nowhere'", router.calls[1]["prompt"])

    def test_two_bad_replies_fall_back_to_the_procedural_draft(self):
        router = _FakeRouter(["not json", "still not json"])
        result = asyncio.run(QuestForge(router, WORLD, budget=BUDGET).draft("A bandit lord took Greenriver Town's granary.", source_key="gm:1"))
        self.assertIsNotNone(result.definition)
        self.assertTrue(result.procedural)
        self.assertEqual(result.attempts, 2)
        self.assertIn("no JSON object", "; ".join(result.errors))
        self.assertEqual(result.definition["source_key"], "gm:1")

    def test_a_dead_chain_falls_back_without_raising(self):
        router = _FakeRouter([RuntimeError("all free models unavailable")])
        result = asyncio.run(QuestForge(router, WORLD, budget=BUDGET).draft("story about Greenriver Town", source_key="gm:1"))
        self.assertTrue(result.procedural)
        self.assertIn("model unavailable", result.errors[0])

    def test_no_model_means_procedural_with_zero_calls(self):
        router = _FakeRouter([])
        router.enabled = False
        result = asyncio.run(QuestForge(router, WORLD, budget=BUDGET).draft("story", source_key="gm:1"))
        self.assertTrue(result.procedural)
        self.assertEqual(result.attempts, 0)
        self.assertEqual(router.calls, [])

    def test_a_fallback_event_shapes_the_procedural_draft(self):
        router = _FakeRouter([RuntimeError("down")])
        event = {"history_id": 42, "event_type": "war_started", "title": "Azure Cloud War", "summary": "Azure Cloud Sect declared war.", "location": "Greenriver Town"}
        result = asyncio.run(QuestForge(router, WORLD, budget=BUDGET).draft("x", source_key="history:42", fallback_event=event))
        self.assertEqual(result.definition["title"], "Aftermath: Azure Cloud War")
        self.assertEqual(result.definition["source_key"], "history:42")
        self.assertEqual(result.definition["objectives"][0]["target"], "Greenriver Town")


class _FakeEngine:
    def __init__(self):
        self.calls = []

    async def action(self, operation, user_id, payload):
        self.calls.append((operation, user_id, payload))
        if operation == "quest.progress":
            # Complete when every objective's type matches the reported one.
            done = all(o["type"] == payload["objective_type"] for o in payload["objectives"])
            return {"touched": True, "complete": done, "progress": {}}
        return {"cultivation_awarded": 0}


class ServiceAndStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "forge.sqlite3")
        await self.db.init()
        self.engine = _FakeEngine()
        self.service = QuestService(self.db, QUEST_DEFINITIONS, engine=self.engine)
        attrs = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4}
        for uid in (3, 5, 6, 9):
            self.assertTrue(await seed_character(
                self.db, user_id=uid, discord_name=f"u{uid}", name=f"Tester {uid}", origin="Greenriver Town",
                path="Qi Refiner", spiritual_root="Fire", concept="quest forge test", location="Greenriver Town",
                attributes=attrs, qi_max=20, vitality_max=20, created_game_minute=0,
            ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_28_has_the_table_and_the_dashboard_reviewed_it(self):
        from app.dashboard.contract import DASHBOARD_REVIEWED_SCHEMA_VERSION, DASHBOARD_SYSTEM_TABLES

        self.assertEqual(SCHEMA_VERSION, 28)
        self.assertEqual(DASHBOARD_REVIEWED_SCHEMA_VERSION, SCHEMA_VERSION)
        self.assertIn("quest_definitions", DASHBOARD_SYSTEM_TABLES["exploration"])
        rows = await self.db.list_quest_definitions()
        self.assertEqual(rows, [])

    async def test_a_draft_is_stored_as_draft_and_only_approval_puts_it_in_the_catalog(self):
        definition, _ = validate_quest_definition(GOOD, WORLD, BUDGET)
        row = await store_draft(self.db, ForgeResult(definition=definition, model="fake/model"), story="rumours", origin="gm_prompt", created_by=7)
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["quest_key"], "forge_the_reed_gate_whisper")
        self.assertEqual(row["objectives"][1]["target"], "Elder Su Yan")
        self.assertEqual(row["rewards"], {"insight_xp": 30, "items": {"spirit_herb": 2}})
        self.assertNotIn(row["quest_key"], await self.service.catalog(refresh=True))
        self.assertTrue(await self.db.set_quest_definition_status(row["quest_key"], "approved", reviewed_by=7))
        catalog = await self.service.catalog(refresh=True)
        self.assertIn(row["quest_key"], catalog)
        self.assertIn("first_steps", catalog)  # static quests stay
        # A second draft with the same title gets a distinct key.
        second = await store_draft(self.db, ForgeResult(definition=definition, model="fake/model"), story="again", origin="gm_prompt", created_by=7)
        self.assertEqual(second["quest_key"], "forge_the_reed_gate_whisper_2")
        with self.assertRaises(ValueError):
            await self.db.set_quest_definition_status(row["quest_key"], "published")

    async def test_retiring_hides_it_from_new_takers_but_holders_keep_it(self):
        definition, _ = validate_quest_definition(GOOD, WORLD, BUDGET)
        row = await store_draft(self.db, ForgeResult(definition=definition), story="s", origin="gm_prompt", created_by=1)
        await self.db.set_quest_definition_status(row["quest_key"], "approved")
        await self.service.catalog(refresh=True)
        await self.service.accept(5, row["quest_key"])
        await self.db.set_quest_definition_status(row["quest_key"], "retired")
        await self.service.catalog(refresh=True)
        self.assertNotIn(row["quest_key"], {q["quest_key"] for q in await self.service.available(6)})
        active = await self.db.list_character_quests(5, status="active")
        self.assertEqual([r["quest_key"] for r in active], [row["quest_key"]])
        # ... and progress on the held quest still resolves through the last-known definition.
        self.assertEqual(await self.service.progress(5, "explore", target="Greenriver Town"), [])

    async def test_completion_grants_the_declared_rewards_through_the_engine(self):
        definition, _ = validate_quest_definition({**GOOD, "objectives": [{"type": "explore", "target": "Greenriver Town"}],
                                                   "rewards": {"insight_xp": 30, "spirit_stones": 40, "items": {"spirit_herb": 2}}}, WORLD, BUDGET)
        row = await store_draft(self.db, ForgeResult(definition=definition), story="s", origin="gm_prompt", created_by=1)
        await self.db.set_quest_definition_status(row["quest_key"], "approved")
        await self.service.catalog(refresh=True)
        await self.service.accept(9, row["quest_key"])
        changed = await self.service.progress(9, "explore", target="Greenriver Town")
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0]["just_completed"])
        self.assertEqual(changed[0]["title"], "The Reed Gate Whisper")
        self.assertEqual(changed[0]["rewards_granted"], {"spirit_stones": 40, "insight_xp": 30, "items": {"spirit_herb": 2}})
        ops = [c[0] for c in self.engine.calls]
        self.assertEqual(ops, ["quest.progress", "cultivation.reward"])
        reward_call = self.engine.calls[1]
        self.assertEqual(reward_call[1], 9)
        self.assertEqual(reward_call[2]["event_type"], f"quest_reward:{row['quest_key']}")
        self.assertEqual(reward_call[2]["cultivation"], 0)

    async def test_a_quest_with_nothing_to_grant_makes_no_reward_call(self):
        await self.service.accept(3, "first_steps")
        engine_calls_before = len(self.engine.calls)
        # first_steps needs explore + talk + scene_action; one report touches, does not complete
        changed = await self.service.progress(3, "explore")
        self.assertEqual(len(changed), 1)
        self.assertNotIn("just_completed", changed[0])
        self.assertEqual([c[0] for c in self.engine.calls[engine_calls_before:]], ["quest.progress"])

    async def test_unknown_keys_are_refused_and_static_shadows_forged(self):
        with self.assertRaises(ValueError):
            await self.service.accept(1, "forge_nope")
        definition, _ = validate_quest_definition({**GOOD, "title": "First Steps Beneath Heaven"}, WORLD, BUDGET)
        definition = {**definition, "quest_key": "first_steps"}
        await self.db.save_quest_definition(definition, status="approved")
        catalog = await self.service.catalog(refresh=True)
        self.assertEqual(catalog["first_steps"]["source_type"], "system")


class SurfaceTests(unittest.TestCase):
    """The admin commands and the worker, read in source."""

    def setUp(self):
        self.wo = (PROJECT_ROOT / "app" / "bot" / "admin" / "world_ops.py").read_text(encoding="utf-8")
        self.bot = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.env = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    def test_the_two_commands_exist_are_admin_gated_and_audit(self):
        for name in ("questforge", "quests"):
            self.assertRegex(self.wo, rf'@registered_group_command\(admin_world_group, name="{name}"')
        forge = self.wo[self.wo.index("async def admin_questforge"):self.wo.index("async def admin_quests(")]
        self.assertIn("if not await require_admin(interaction):", forge)
        self.assertIn('await audit_admin(interaction, "quest.forge"', forge)
        self.assertIn("QUEST_FORGE.draft(story, source_key=", forge)
        self.assertIn('store_draft(DB, result, story=story, origin="gm_prompt"', forge)
        review = self.wo[self.wo.index("class QuestDraftReviewView"):self.wo.index("async def admin_questforge")]
        self.assertIn("return await require_admin(interaction)", review)
        self.assertIn('await audit_admin(interaction, f"quest.{status}"', review)
        self.assertIn("await QUESTS.catalog(refresh=True)", review)

    def test_nothing_on_the_discord_side_writes_a_gameplay_table(self):
        # The Forge writes quest_definitions (a GM table) and nothing else; the
        # reward path is the engine's cultivation.reward in QuestService.
        for forbidden in ("consume_item", "add_items(", "restore_resources", "apply_effect(", "add_currency"):
            self.assertNotIn(forbidden, self.wo[self.wo.index("# Quest Forge (v0.20.6)"):])
        service = (PROJECT_ROOT / "app" / "ops" / "core_services.py").read_text(encoding="utf-8")
        body = service[service.index("class QuestService"):service.index("class ExplorationService")]
        self.assertIn('await self.engine.action("cultivation.reward", user_id, payload)', body)
        self.assertNotIn("INSERT", body)

    def test_the_worker_is_opt_in_idempotent_and_announces(self):
        self.assertIn("if SETTINGS.quest_forge_auto:", self.bot)
        self.assertIn('"quest_forge_task"):', self.bot)
        body = self.bot[self.bot.index("async def forge_quests_from_history"):self.bot.index("async def quest_forge_worker")]
        self.assertIn('source_key = f"history:{event.get(\'history_id\')}"', body)
        self.assertIn("if source_key in seen:", body)
        self.assertIn('str(event.get("visibility") or "public") != "public"', body)
        self.assertIn('origin="world_history"', body)
        self.assertIn('"Quest drafts ready"', body)
        self.assertIn("QUEST_FORGE_AUTO=false", self.env)

    def test_sect_trial_is_finally_reported_and_completions_are_announced(self):
        sect = (PROJECT_ROOT / "app" / "bot" / "commands" / "sect.py").read_text(encoding="utf-8")
        self.assertIn('QUESTS.progress(interaction.user.id, "sect_trial"', sect)
        for module in ("commands/exploration.py", "commands/scene.py", "commands/sect.py"):
            source = (PROJECT_ROOT / "app" / "bot" / module).read_text(encoding="utf-8")
            self.assertEqual(source.count("QUESTS.progress("), source.count("announce_quest_progress(interaction, await QUESTS.progress("), module)
        character = (PROJECT_ROOT / "app" / "bot" / "commands" / "character.py").read_text(encoding="utf-8")
        self.assertNotIn("QUEST_DEFINITIONS", character)
        self.assertIn("await QUESTS.catalog()", character)


if __name__ == "__main__":
    unittest.main()
