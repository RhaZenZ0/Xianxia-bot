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
import time
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
    static_quest_seed_rows,
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
        # v0.33.1: forty-eight auction floors and their stewards would fill the
        # capped lists before the town a story is set in; they stay off the
        # prompt (validation still accepts them).
        self.assertNotIn("Auction", text.split("NPCs:")[0].split("Locations:")[1])
        self.assertNotIn("Exchange Warden", text)
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
    """Stands in for the Go engine. `commission.accept` (v0.22.0) writes the
    character_quests row the real action writes, so the service tests still
    exercise a real database - they just do not run Go to get there."""

    def __init__(self, db=None):
        self.calls = []
        self.db = db

    async def _accepted_terms(self, user_id, quest_key):
        """The terms the real engine reads (v0.24.0): the ones pinned on the
        player's row at accept, and only failing that the definition as it
        stands now. Nothing in the caller's payload is consulted, because the
        engine no longer accepts them from there."""
        for row in await self.db.list_character_quests(int(user_id), status="active"):
            if str(row["quest_key"]) == str(quest_key):
                pinned = dict(row.get("terms") or {})
                if pinned.get("objectives") or pinned.get("rewards"):
                    return pinned
        definition = await self.db.get_quest_definition(str(quest_key)) or {}
        return {"objectives": list(definition.get("objectives") or []),
                "rewards": dict(definition.get("rewards") or {})}

    async def action(self, operation, user_id, payload):
        self.calls.append((operation, user_id, payload))
        if operation == "quest.progress":
            # Complete when every objective's type matches the reported one.
            # The engine pays inside this call (v0.22.2) and reports what it
            # paid, so the fake does the same.
            terms = await self._accepted_terms(user_id, payload["quest_key"])
            objectives = list(terms.get("objectives") or [])
            done = bool(objectives) and all(o["type"] == payload["objective_type"] for o in objectives)
            transition = {"touched": bool(objectives), "complete": done, "progress": {}}
            if done:
                transition["rewards_granted"] = dict(terms.get("rewards") or {})
            return transition
        return {"cultivation_awarded": 0}

    async def authoritative_action(self, operation, user_id, payload, *, action_id, expected_version=None):
        self.calls.append((operation, user_id, payload))
        if operation == "commission.accept" and self.db is not None:
            now = time.time()
            # The real action pins the terms onto the row in this same
            # transaction (v0.24.0), so the fake does too - otherwise these
            # tests would exercise a boundary the engine no longer has.
            definition = await self.db.get_quest_definition(str(payload["quest_key"])) or {}
            terms = json.dumps({
                "objectives": list(definition.get("objectives") or []),
                "rewards": dict(definition.get("rewards") or {}),
            })
            async with self.db._connect() as conn:
                await conn.execute(
                    """INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,
                           commission,variant_index,terms_json,created_at,updated_at)
                       VALUES(?,?,'active','{}',0,0,?,?,?,?)
                       ON CONFLICT(user_id,quest_key) DO NOTHING""",
                    (int(user_id), str(payload["quest_key"]), int(payload.get("variant_index", 0)), terms, now, now),
                )
                await conn.commit()
        return {"result": {"quest_key": payload.get("quest_key"), "status": "active"}}


class ServiceAndStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "forge.sqlite3")
        await self.db.init()
        self.engine = _FakeEngine(self.db)
        # The bot seeds the static quests into quest_definitions at startup
        # (app/bot/bot.py). Since v0.24.0 that row is where the engine reads a
        # quest's terms from, so a test database without it is not the database
        # production runs on.
        await self.db.sync_commission_pool(static_quest_seed_rows(QUEST_DEFINITIONS))
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

    async def test_the_forge_table_exists_and_the_dashboard_reviewed_the_schema(self):
        from app.dashboard.contract import DASHBOARD_REVIEWED_SCHEMA_VERSION, DASHBOARD_SYSTEM_TABLES

        self.assertEqual(SCHEMA_VERSION, 40)
        self.assertEqual(DASHBOARD_REVIEWED_SCHEMA_VERSION, SCHEMA_VERSION)
        # v0.24.0: the definition table belongs to the Quests workbench, which
        # can act on every row in it. Exploration showed forged ones read-only.
        self.assertIn("quest_definitions", DASHBOARD_SYSTEM_TABLES["quests"])
        self.assertNotIn("quest_definitions", DASHBOARD_SYSTEM_TABLES["exploration"])
        # Only the static quests seeded at startup; nothing has been forged yet.
        rows = await self.db.list_quest_definitions()
        self.assertEqual({r["quest_key"] for r in rows}, set(QUEST_DEFINITIONS))
        self.assertTrue(all(not r["giver_npc"] for r in rows), "a static quest is not a commission")

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
        await self.service.accept(5, row["quest_key"], action_id="test:accept:5")
        await self.db.set_quest_definition_status(row["quest_key"], "retired")
        await self.service.catalog(refresh=True)
        self.assertNotIn(row["quest_key"], {q["quest_key"] for q in await self.service.available(6)})
        active = await self.db.list_character_quests(5, status="active")
        self.assertEqual([r["quest_key"] for r in active], [row["quest_key"]])
        # ... and the holder can still finish it. Until v0.24.0 this returned
        # nothing: Python only sent progress for quests in the approved
        # catalog, so retiring a definition quietly froze everyone carrying it
        # - unable to complete it, unable to be paid for it, holding it for
        # good. The terms are pinned to their row, so retirement now means
        # only what it says: nobody new may take it.
        changed = await self.service.progress(5, "explore", target="Greenriver Town")
        self.assertEqual([r["quest_key"] for r in changed], [row["quest_key"]])
        self.assertEqual(changed[0]["title"], "The Reed Gate Whisper")

    async def test_completion_is_paid_by_the_transaction_that_completes_it(self):
        definition, _ = validate_quest_definition({**GOOD, "objectives": [{"type": "explore", "target": "Greenriver Town"}],
                                                   "rewards": {"insight_xp": 30, "spirit_stones": 40, "items": {"spirit_herb": 2}}}, WORLD, BUDGET)
        row = await store_draft(self.db, ForgeResult(definition=definition), story="s", origin="gm_prompt", created_by=1)
        await self.db.set_quest_definition_status(row["quest_key"], "approved")
        await self.service.catalog(refresh=True)
        await self.service.accept(9, row["quest_key"], action_id="test:accept:9")
        changed = await self.service.progress(9, "explore", target="Greenriver Town")
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0]["just_completed"])
        self.assertEqual(changed[0]["title"], "The Reed Gate Whisper")
        # What was paid comes back from the completing call itself.
        self.assertEqual(changed[0]["rewards_granted"], {"spirit_stones": 40, "insight_xp": 30, "items": {"spirit_herb": 2}})
        ops = [c[0] for c in self.engine.calls]
        self.assertEqual(ops, ["commission.accept", "quest.progress"],
                         "completion and payment are one commit; a second reward call can be lost")
        progress_call = self.engine.calls[1]
        self.assertEqual(progress_call[1], 9)
        # Nothing about the terms travels with the report any more (v0.24.0).
        # The engine reads what this player accepted off their own row, so a
        # caller cannot state what the work asks for or what it is worth.
        self.assertNotIn("rewards", progress_call[2])
        self.assertNotIn("objectives", progress_call[2])
        self.assertEqual(set(progress_call[2]), {"quest_key", "objective_type", "amount", "target"})

    async def test_a_quest_with_nothing_to_grant_makes_no_reward_call(self):
        await self.service.accept(3, "first_steps", action_id="test:accept:3")
        engine_calls_before = len(self.engine.calls)
        # first_steps needs explore + talk + scene_action; one report touches, does not complete
        changed = await self.service.progress(3, "explore")
        self.assertEqual(len(changed), 1)
        self.assertNotIn("just_completed", changed[0])
        self.assertEqual([c[0] for c in self.engine.calls[engine_calls_before:]], ["quest.progress"])

    async def test_unknown_keys_are_refused_and_static_shadows_forged(self):
        with self.assertRaises(ValueError):
            await self.service.accept(1, "forge_nope", action_id="test:accept:1")
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
        # v0.22.2: rewards are granted by the transaction that completes the
        # quest (quest.progress in Go), not by a second call from here. A
        # separate reward call is the defect, not the design.
        self.assertNotIn("cultivation.reward", body)
        # v0.24.0: and it no longer states the terms either. The engine reads
        # what the player accepted off their own row, so the progress report
        # says neither what the quest asks for nor what it pays. (The catalog
        # above it still carries both - that is what an offer is made of.)
        report = body[body.index("async def progress"):body.index("def visible_to")]
        self.assertNotIn('"rewards"', report)
        self.assertNotIn('"objectives"', report)
        self.assertNotIn("INSERT", body)

    def test_the_worker_is_opt_in_idempotent_and_announces(self):
        self.assertIn("if SETTINGS.quest_forge_auto:", self.bot)
        self.assertIn('"quest_forge_task"', self.bot)
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


class StaticQuestSeedTests(unittest.TestCase):
    """v0.23.1: the static quests are seeded so the engine can find them.

    `commission.accept` decides "does this quest exist?" by looking for a
    `quest_definitions` row. Before this, a static quest had none, so the
    engine could not tell `first_steps` from a key nobody defined and accepted
    both. These assert the seed rows keep the shape that makes them ordinary
    quests rather than commissions.
    """

    def test_every_static_quest_gets_a_row(self):
        rows = static_quest_seed_rows()
        self.assertEqual(
            {row["quest_key"] for row in rows},
            set(QUEST_DEFINITIONS),
            "a static quest with no seed row is invisible to the engine",
        )

    def test_seed_rows_carry_no_giver_so_they_stay_ordinary_quests(self):
        for row in static_quest_seed_rows():
            self.assertEqual(row["giver_npc"], "", row["quest_key"])
            self.assertEqual(row["deadline_game_minutes"], 0, row["quest_key"])
            self.assertEqual(row["variants"], [], row["quest_key"])

    def test_seed_rows_preserve_the_definition(self):
        rows = {row["quest_key"]: row for row in static_quest_seed_rows()}
        for key, definition in QUEST_DEFINITIONS.items():
            self.assertEqual(rows[key]["title"], definition["title"])
            self.assertEqual(rows[key]["objectives"], definition["objectives"])
            self.assertEqual(rows[key]["rewards"], definition["rewards"])
