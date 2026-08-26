import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.birthfamily import generate_family_options
from app.database import Database
from app.game import World
from app.simulation import WorldSimulator

ROOT=PROJECT_ROOT

class ForbiddenArtsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/'forbidden.sqlite3')
        await self.db.init()
        self.world=World(ROOT/'content'/'world.json')
        self.sim=WorldSimulator(self.db,self.world.data)
        await self.sim.initialize(0)
        profile=generate_family_options()[0]
        self.assertTrue(await self.db.create_character(
            user_id=99,discord_name='evil-test',name='Mo Test',origin='Greenriver Town',
            path='Soul Cultivator',spiritual_root='Fire',concept='forbidden art test',
            location='Greenriver Town',attributes={'body':2,'agility':2,'spirit':3,'insight':3,'will':2,'presence':2},
            qi_max=20,vitality_max=20,created_game_minute=0,birth_family_profile=profile,
        ))

    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_content_contains_demonic_manuals_sects_and_world_rules(self):
        self.assertGreaterEqual(len(self.world.manuals),6)
        self.assertIn('blood_sea_palm',self.world.techniques)
        self.assertEqual(self.world.sects['Blood River Sect']['alignment'],'Demonic')
        self.assertTrue(self.world.world_rules['npc_principles'])

    async def test_manual_learning_and_mastery_persist(self):
        learned=await self.db.learn_manual(99,'blood_sea_scripture')
        self.assertEqual(learned['mastery'],0)
        for _ in range(3): state=await self.db.practice_manual(99,'blood_sea_scripture',1)
        self.assertEqual(state['mastery'],1)
        rows=await self.db.get_manuals(99)
        self.assertEqual(rows[0]['practice'],3)

    async def test_witnessed_forbidden_art_hurts_region_and_family(self):
        fam_before=await self.db.get_birth_family(99)
        region_before=await self.sim.civilization_status('Greenriver Town')
        result=await self.sim.apply_forbidden_art_use(
            user_id=99,technique_id='soul_rend',technique_name='Soul Rend',location='Greenriver Town',
            game_minute=60,exposure=7,karma_cost=4,witnessed=True,
        )
        fam_after=await self.db.get_birth_family(99)
        region_after=await self.sim.civilization_status('Greenriver Town')
        self.assertLess(int(fam_after['stability']),int(fam_before['stability']))
        self.assertGreater(int(region_after['unrest']),int(region_before['unrest']))
        self.assertTrue(result['impacts'])

if __name__=='__main__': unittest.main()
