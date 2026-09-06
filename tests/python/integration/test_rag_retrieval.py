"""Deterministic memory / canon / history retrieval (app/ai/rag.py).

Was test_rag_v1.py + test_world_history_rag.py: same fixture, same retriever,
one file since v0.20.3.
"""
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.database import Database
from app.rules.game import World
from app.ai.rag import MemoryRAGRetriever, build_fts_query, resolve_retrieval_profile

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 6, "insight": 6, "will": 5, "presence": 4}


class MemoryAndCanonRetrievalTests(unittest.IsolatedAsyncioTestCase):
    """Player/NPC memories and location canon through MemoryRAGRetriever."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rag.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        ok = await seed_character(self.db,
            user_id=8101, discord_name="rag", name="Zi Dian",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Lightning",
            concept="remember what the world teaches", location="Greenriver Town",
            attributes=ATTRS, qi_max=25, vitality_max=25, created_game_minute=0,
        )
        self.assertTrue(ok)
        self.world = World(ROOT / "content" / "world.json")
        await self.db.sync_world_catalog(self.world.data)
        await self.db.sync_rag_canon(self.world.data)

    async def asyncTearDown(self):
        self.tmp.cleanup()


    def test_scene_profiles_tighten_routine_context_by_scene_type(self):
        dialogue = resolve_retrieval_profile("persistent NPC dialogue", focus_npc="Elder Su Yan", routine_cap=4000, epic_cap=6500)
        battle = resolve_retrieval_profile("battle resolution", routine_cap=4000, epic_cap=6500)
        roleplay = resolve_retrieval_profile("freeform roleplay", routine_cap=4000, epic_cap=6500)
        exploration = resolve_retrieval_profile("exploration", routine_cap=4000, epic_cap=6500)
        epic = resolve_retrieval_profile("world event", routine_cap=4000, epic_cap=6500)
        self.assertEqual(dialogue.name, "dialogue")
        self.assertEqual(battle.name, "battle")
        self.assertLess(dialogue.context_chars, exploration.context_chars)
        self.assertLess(battle.rag_chars, exploration.rag_chars)
        self.assertLess(roleplay.canon_limit, epic.canon_limit)
        self.assertGreater(epic.context_chars, exploration.context_chars)

    async def test_exact_rag_context_is_short_lived_cached_and_user_scoped(self):
        await self.db.add_rag_memory(
            8101, memory_kind="vow", salience=90, location="Greenriver Town",
            summary="Zi Dian promised to return the river seal before dusk.",
            source="test", game_minute=100,
        )
        retriever = MemoryRAGRetriever(db=self.db, world=self.world, query_cache_seconds=30, canon_cache_seconds=30)
        character = await self.db.get_character(8101)
        profile = resolve_retrieval_profile("freeform roleplay", routine_cap=4000, epic_cap=6500)
        first = await retriever.retrieve(
            character, query_text="river seal promise", game_minute=200, location="Greenriver Town",
            known_manuals=[], profile=profile,
        )
        second = await retriever.retrieve(
            character, query_text="river seal promise", game_minute=201, location="Greenriver Town",
            known_manuals=[], profile=profile,
        )
        self.assertFalse(first.context_cache_hit)
        self.assertTrue(second.context_cache_hit)
        self.assertEqual(first.text, second.text)
        stats = retriever.cache_stats()
        self.assertGreaterEqual(stats["context"]["hits"], 1)

    def test_fts_query_never_passes_operators_through(self):
        query = build_fts_query('elder OR secret NEAR("seal") + qi?', extra_terms=["Greenriver Town"])
        self.assertIn('"elder"', query)
        self.assertIn('"secret"', query)
        self.assertIn('"qi"', query)
        self.assertNotIn('NEAR(', query)
        self.assertNotIn(' + ', query)

    async def test_player_memory_is_full_text_searchable_and_owner_scoped(self):
        await self.db.add_rag_memory(
            8101, memory_kind="vow", salience=90, location="Greenriver Town",
            summary="Zi Dian swore to Elder Su Yan that the bronze river bell would be returned.",
            source="test", game_minute=100,
        )
        rows = await self.db.search_rag_memories(8101, build_fts_query("bronze river bell Elder Su"), limit=10)
        self.assertEqual(len(rows), 1)
        self.assertIn("bronze river bell", rows[0]["summary"])
        other = await self.db.search_rag_memories(9999, build_fts_query("bronze river bell"), limit=10)
        self.assertEqual(other, [])

    async def test_npc_memories_mirror_into_unified_rag_memory(self):
        npc_id = await self.db.add_npc_player_memory(
            8101, "Elder Su Yan", memory_kind="debt",
            summary="Elder Su Yan acknowledged a debt after Zi Dian protected a disciple.",
            salience=76, source="talk", game_minute=120,
        )
        rows = await self.db.search_rag_memories(8101, build_fts_query("debt protected disciple"), limit=10)
        self.assertTrue(any(row["source_key"] == f"npc:{npc_id}" for row in rows))

    async def test_retriever_filters_location_canon_and_unknown_manuals(self):
        # Search terms deliberately hit another location and a forbidden manual.
        retriever = MemoryRAGRetriever(db=self.db, world=self.world)
        character = await self.db.get_character(8101)
        ctx = await retriever.retrieve(
            character, query_text="Cloudspine Blood Sea Scripture blood qi",
            game_minute=200, location="Greenriver Town", known_manuals=[], max_chars=4000,
        )
        self.assertNotIn("Cloudspine Foothills", ctx.text)
        self.assertNotIn("Blood Sea Scripture", ctx.text)

        async with self.db._connect() as db:
            await db.execute(
                "INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(?,?,?,?,?,?)",
                (8101, "blood_sea_scripture", 0, 0, 1.0, 1.0),
            )
            await db.commit()
        known = await self.db.get_manuals(8101)
        ctx2 = await retriever.retrieve(
            character, query_text="Blood Sea Scripture blood qi",
            game_minute=201, location="Greenriver Town", known_manuals=known, max_chars=4000,
        )
        self.assertIn("Blood Sea Scripture", ctx2.text)



    async def test_canon_resync_does_not_leave_stale_or_corrupt_fts_rows(self):
        before = await self.db.rag_stats()
        await self.db.sync_rag_canon(self.world.data)
        after = await self.db.rag_stats()
        self.assertEqual(before["canon_documents"], after["canon_documents"])
        rows = await self.db.search_rag_canon(build_fts_query("Greenriver Jade River"), limit=10)
        self.assertTrue(any(row["title"] == "Greenriver Town" for row in rows))

    async def test_focus_npc_does_not_receive_other_npc_private_memory(self):
        await self.db.add_rag_memory(
            8101, source_key="npc:90001", memory_kind="secret", salience=95,
            location="Greenriver Town", npc_name="Yan Kuo", source="talk", game_minute=140,
            summary="Yan Kuo privately told Zi Dian that a hidden catalyst was purchased.",
        )
        await self.db.add_rag_memory(
            8101, source_key="npc:90002", memory_kind="vow", salience=80,
            location="Greenriver Town", npc_name="Elder Su Yan", source="talk", game_minute=141,
            summary="Elder Su Yan asked Zi Dian to remember the river seal oath.",
        )
        retriever = MemoryRAGRetriever(db=self.db, world=self.world)
        character = await self.db.get_character(8101)
        ctx = await retriever.retrieve(
            character, query_text="hidden catalyst river seal oath", game_minute=160,
            location="Greenriver Town", known_manuals=[], focus_npc="Elder Su Yan", max_chars=4000,
        )
        self.assertIn("river seal oath", ctx.text)
        self.assertNotIn("hidden catalyst", ctx.text)

    async def test_current_location_canon_is_retrievable(self):
        retriever = MemoryRAGRetriever(db=self.db, world=self.world)
        character = await self.db.get_character(8101)
        ctx = await retriever.retrieve(
            character, query_text="Jade River spirit herb town",
            game_minute=50, location="Greenriver Town", known_manuals=[], max_chars=4000,
        )
        self.assertIn("Greenriver Town", ctx.text)
        self.assertIn("Jade River", ctx.text)


class WorldHistoryRetrievalTests(unittest.IsolatedAsyncioTestCase):
    """world_history visibility (public / participant / faction / hidden)
    through the same retriever."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        for uid, name in ((9101, "Zi Dian"), (9102, "Han Yue")):
            ok = await seed_character(self.db,
                user_id=uid, discord_name=name, name=name,
                origin="Greenriver Town", path="Qi Refiner", spiritual_root="Lightning",
                concept="walk the Dao", location="Greenriver Town", attributes=ATTRS,
                qi_max=25, vitality_max=25, created_game_minute=0,
            )
            self.assertTrue(ok)
        self.world = World(ROOT / "content" / "world.json")
        await self.db.sync_world_catalog(self.world.data)
        await self.db.sync_rag_canon(self.world.data)
        self.rag = MemoryRAGRetriever(db=self.db, world=self.world, query_cache_seconds=0, canon_cache_seconds=0)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_public_history_is_fts_searchable(self):
        await self.db.record_world_history_event(
            event_type="war_started", title="Azure Cloud War",
            summary="Azure Cloud Sect declared war on Iron Mountain Sect for Greenriver Pass.",
            significance=90, visibility="public", location="Greenriver Town", faction="Azure Cloud Sect",
            actor_type="sect", actor_key="Azure Cloud Sect", actor_name="Azure Cloud Sect",
            target_type="sect", target_key="Iron Mountain Sect", target_name="Iron Mountain Sect",
            tags=("war", "territory"), game_minute=1000, source_key="test:war:1",
        )
        rows = await self.db.search_world_history(build_fts_query("Azure Cloud war Greenriver Pass"), limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "war_started")

    async def test_structured_local_history_survives_query_wording_mismatch(self):
        await self.db.record_world_history_event(
            event_type="leadership_change", title="New Greenriver patriarch",
            summary="Elder Qiao inherited the river clan seat after the former patriarch died.",
            significance=88, visibility="public", location="Greenriver Town", faction="Qiao Clan",
            tags=("leadership", "succession"), game_minute=1200, source_key="test:leadership:1",
        )
        char = await self.db.get_character(9101)
        profile = resolve_retrieval_profile("freeform roleplay", routine_cap=4000, epic_cap=6500)
        ctx = await self.rag.retrieve(
            char, query_text="I ask what important things happened around here", game_minute=1300,
            location="Greenriver Town", known_manuals=[], known_factions=[], profile=profile,
        )
        self.assertIn("RETRIEVED WORLD HISTORY", ctx.text)
        self.assertIn("New Greenriver patriarch", ctx.text)
        self.assertGreaterEqual(ctx.history_count, 1)

    async def test_player_participant_history_is_owner_scoped(self):
        await self.db.record_world_history_event(
            event_type="inheritance", title="Hidden Moon Inheritance",
            summary="Zi Dian privately obtained the Hidden Moon Inheritance beneath the river shrine.",
            significance=85, visibility="participant", location="Greenriver Town",
            actor_type="player", actor_key="9101", actor_name="Zi Dian", related_user_id=9101,
            tags=("inheritance", "secret"), game_minute=1400, source_key="test:inheritance:private",
        )
        profile = resolve_retrieval_profile("freeform roleplay", routine_cap=4000, epic_cap=6500)
        own = await self.rag.retrieve(
            await self.db.get_character(9101), query_text="Hidden Moon inheritance", game_minute=1500,
            location="Greenriver Town", known_manuals=[], profile=profile,
        )
        other = await self.rag.retrieve(
            await self.db.get_character(9102), query_text="Hidden Moon inheritance", game_minute=1500,
            location="Greenriver Town", known_manuals=[], profile=profile,
        )
        self.assertIn("Hidden Moon Inheritance", own.text)
        self.assertNotIn("Hidden Moon Inheritance", other.text)

    async def test_focused_npc_does_not_learn_player_private_history(self):
        await self.db.record_world_history_event(
            event_type="betrayal", title="Private betrayal",
            summary="Zi Dian discovered a betrayal no elder has been told about.",
            significance=90, visibility="participant", location="Greenriver Town",
            actor_type="player", actor_key="9101", actor_name="Zi Dian", related_user_id=9101,
            game_minute=1600, source_key="test:betrayal:private",
        )
        char = await self.db.get_character(9101)
        ctx = await self.rag.retrieve(
            char, query_text="betrayal", game_minute=1700, location="Greenriver Town",
            known_manuals=[], focus_npc="Elder Su Yan",
            profile=resolve_retrieval_profile("persistent NPC dialogue", focus_npc="Elder Su Yan", routine_cap=4000, epic_cap=6500),
        )
        self.assertNotIn("Private betrayal", ctx.text)

    async def test_faction_history_requires_matching_faction(self):
        await self.db.record_world_history_event(
            event_type="leadership_change", title="Inner Sect succession",
            summary="Azure Cloud Sect quietly replaced the Outer Hall master.",
            significance=70, visibility="faction", location="Greenriver Town", faction="Azure Cloud Sect",
            game_minute=1800, source_key="test:faction:1",
        )
        char = await self.db.get_character(9101)
        yes = await self.rag.retrieve(
            char, query_text="Outer Hall master succession", game_minute=1900, location="Greenriver Town",
            known_manuals=[], known_factions=["Azure Cloud Sect"], max_chars=4000,
        )
        no = await self.rag.retrieve(
            char, query_text="Outer Hall master succession", game_minute=1900, location="Greenriver Town",
            known_manuals=[], known_factions=["Iron Mountain Sect"], max_chars=4000,
        )
        self.assertIn("Inner Sect succession", yes.text)
        self.assertNotIn("Inner Sect succession", no.text)

    async def test_hidden_history_never_reaches_rag(self):
        await self.db.record_world_history_event(
            event_type="betrayal", title="GM-only betrayal",
            summary="A hidden conspiracy exists behind the court.",
            significance=100, visibility="hidden", location="Greenriver Town",
            game_minute=2000, source_key="test:hidden:1",
        )
        char = await self.db.get_character(9101)
        ctx = await self.rag.retrieve(
            char, query_text="hidden conspiracy court betrayal", game_minute=2100,
            location="Greenriver Town", known_manuals=[], max_chars=4000,
        )
        self.assertNotIn("GM-only betrayal", ctx.text)


if __name__ == "__main__":
    unittest.main()
