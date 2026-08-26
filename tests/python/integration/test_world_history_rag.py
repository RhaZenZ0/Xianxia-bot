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


class WorldHistoryRagTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        for uid, name in ((9101, "Zi Dian"), (9102, "Han Yue")):
            ok = await self.db.create_character(
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

    def test_schema_is_v16(self):
        self.assertEqual(SCHEMA_VERSION, 17)

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

    async def test_territory_war_start_writes_history(self):
        await self.db.ensure_territory("greenriver_pass", name="Greenriver Pass", region="Greenriver Town", game_minute=2200)
        await self.db.claim_territory("greenriver_pass", controller_type="sect", controller_key="Iron Mountain Sect", game_minute=2200)
        war_id = await self.db.start_territory_war(
            attacker_key="Azure Cloud Sect", defender_key="Iron Mountain Sect",
            territory_key="greenriver_pass", game_minute=2250,
        )
        rows = await self.db.list_world_history(event_type="war_started", limit=10)
        self.assertTrue(any(r["source_key"] == f"territory_war:{war_id}:started" for r in rows))


if __name__ == "__main__":
    unittest.main()
