import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.game import World
from app.rag import MemoryRAGRetriever, build_fts_query, resolve_retrieval_profile

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 6, "insight": 6, "will": 5, "presence": 4}


class RagV1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rag.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        ok = await self.db.create_character(
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

    def test_schema_is_v16(self):
        self.assertEqual(SCHEMA_VERSION, 17)


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

        await self.db.learn_manual(8101, "blood_sea_scripture")
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


if __name__ == "__main__":
    unittest.main()
