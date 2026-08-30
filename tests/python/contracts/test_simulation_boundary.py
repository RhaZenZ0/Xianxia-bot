import unittest

from tests.support import install_aiosqlite_shim
install_aiosqlite_shim()

from app.simulation import WorldSimulator


class RecordingSimulationEngine:
    def __init__(self):
        self.calls = []

    async def run_due_simulation(self, game_minute, automation):
        self.calls.append(("run_due", game_minute, dict(automation)))
        return [{
            "system": "npc_life",
            "due_steps": 3,
            "applied_steps": 3,
            "summary": "go-owned simulation",
            "events": [{"kind": "test"}],
        }]

    async def force_simulation(self, system, steps, game_minute):
        self.calls.append(("force", system, steps, game_minute))
        return {
            "system": system,
            "due_steps": steps,
            "applied_steps": steps,
            "summary": f"go-owned {system}",
        }


class SimulationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_due_delegates_to_go_engine(self):
        engine = RecordingSimulationEngine()
        sim = WorldSimulator(None, {}, engine=engine)
        runs = await sim.run_due(4321, {"npc_life": True})
        self.assertEqual(engine.calls, [("run_due", 4321, {"npc_life": True})])
        self.assertEqual(runs[0].system, "npc_life")
        self.assertEqual(runs[0].summary, "go-owned simulation")
        self.assertEqual(runs[0].events, ({"kind": "test"},))

    async def test_force_run_delegates_to_go_engine(self):
        engine = RecordingSimulationEngine()
        sim = WorldSimulator(None, {}, engine=engine)
        run = await sim.force_run("dynamic_economy", 2, 5000)
        self.assertEqual(engine.calls, [("force", "dynamic_economy", 2, 5000)])
        self.assertEqual(run.summary, "go-owned dynamic_economy")

    def test_engine_is_required(self):
        with self.assertRaises(TypeError):
            WorldSimulator(None, {})


if __name__ == "__main__":
    unittest.main()
