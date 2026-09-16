"""Asking who is standing somewhere costs a bounded number of queries.

Until v1.0.0-rc.28 every caller walked the whole NPC catalogue calling
`npc.status` per name - 574 iterations with an engine round trip inside each,
serially - and `/action`'s target picker and `/scene status` both did it on
every open. The content of the answer is a Go-owned rule; what this asserts is
the Python boundary: that nobody has gone back to the per-NPC loop, and that
the one resolver keeps the same order of precedence `current_npc_location`
does, so a picker cannot offer somebody `/talk` then refuse them.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
GO = PROJECT_ROOT / "go_core" / "internal"
LOCATIONS = (APP / "bot" / "locations.py").read_text(encoding="utf-8")

# Every module that draws "who is here". The loop this forbids is the exact
# shape they all used.
SURFACES = ("bot/commands/scene.py", "bot/commands/sense.py", "bot/commands/exploration.py", "bot/locations.py")
PER_NPC_LOOP = re.compile(
    r"for\s+\w+\s+in\s+WORLD\.npcs\b[^\n]*:\s*\n\s*if\s+await\s+current_npc_location", re.MULTILINE
)


def _node(source: str, name: str):
    tree = ast.parse(source)
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )


def body(source: str, name: str) -> str:
    return ast.get_source_segment(source, _node(source, name)) or ""


def code(source: str, name: str) -> str:
    """The function without its docstring.

    These docstrings quote the shape they replaced, so a search over the whole
    body finds the old code in the prose describing it - which is how the first
    version of the ordering check below failed against a correct function.
    """
    node = _node(source, name)
    statements = node.body
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant):
        statements = statements[1:]
    return "\n".join(ast.get_source_segment(source, statement) or "" for statement in statements)


class NobodyAsksOneNPCAtATime(unittest.TestCase):
    def test_no_surface_walks_the_catalogue_with_an_engine_call_inside(self):
        offenders = []
        for relative in SURFACES:
            source = (APP / relative).read_text(encoding="utf-8")
            # `npcs_present` itself is allowed the fallback loop - it is the
            # one place that resolves what the engine could not answer for,
            # and it is bounded by the catalogue rather than by the engine.
            if relative == "bot/locations.py":
                source = source.replace(body(source, "npcs_present"), "")
            if PER_NPC_LOOP.search(source):
                offenders.append(relative)
        self.assertEqual(
            offenders, [],
            "these draw 'who is here' one NPC at a time, which is 574 engine round trips per open: "
            + ", ".join(offenders),
        )

    def test_the_surfaces_use_the_one_resolver(self):
        for relative in ("bot/commands/scene.py", "bot/commands/sense.py", "bot/commands/exploration.py"):
            source = (APP / relative).read_text(encoding="utf-8")
            with self.subTest(module=relative):
                self.assertIn("npcs_present(", source)


class TheResolverAgreesWithTheSingleLookup(unittest.TestCase):
    """A picker that offers somebody and a command that then refuses them is
    worse than either being wrong on its own, so the two must rank the same
    sources in the same order: circuit, then the simulation, then the schedule."""

    def test_a_circuit_walker_is_resolved_against_the_clock_not_the_engine(self):
        present = code(LOCATIONS, "npcs_present")
        self.assertIn("circuit", present)
        # The engine's answer for a circuit-walker is explicitly skipped, which
        # is what `current_npc_location` does by checking the circuit first.
        self.assertLess(present.index("circuit"), present.index("current_npc_location"))

    def test_it_asks_the_engine_once(self):
        present = code(LOCATIONS, "npcs_present")
        self.assertEqual(present.count("SIM.npcs_at_location"), 1)
        self.assertNotIn("SIM.npc_status", present)

    def test_it_rules_people_out_from_content_before_asking(self):
        """The text check above is not enough on its own, and shipped a
        function that still cost 558 round trips while passing it: the calls
        were one level down, inside `current_npc_location`. What actually
        bounds the cost is the catalogue check that comes *before* the
        fallback resolves anybody - `WhoIsHereCostsOneQuery` measures it."""
        present = code(LOCATIONS, "npcs_present")
        self.assertIn('if (WORLD.npc_location_at(name, period) or "") != where:', present)

    def test_an_engine_failure_degrades_rather_than_raises(self):
        """Drawing a scene must not become impossible because one query failed."""
        present = code(LOCATIONS, "npcs_present")
        self.assertIn("except Exception", present)


class GoOwnsTheAnswer(unittest.TestCase):
    def test_the_query_is_registered_in_both_registries(self):
        for path in ("game/world_status_queries.go", "game/authoritative.go"):
            source = (GO / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn('"npc.at_location"', source)

    def test_it_reads_both_populations(self):
        source = (GO / "game" / "world_status_queries.go").read_text(encoding="utf-8")
        query = source[source.index("func npcsAtLocationGo("):]
        query = query[:query.index("\nfunc ")]
        self.assertIn("npc_civilization_state", query)
        self.assertIn("npc_registry", query)
        # A matured descendant is in both; the simulation row is the
        # authoritative answer about where somebody is.
        self.assertIn("NOT EXISTS", query)

    def test_it_does_not_load_a_whole_life_per_person(self):
        """`npc.status` carries relationships, disciple bonds and the life row.
        Loading all of that for everybody in a city to decide whether to list
        them is what made the old shape slow twice over."""
        source = (GO / "game" / "world_status_queries.go").read_text(encoding="utf-8")
        query = source[source.index("func npcsAtLocationGo("):]
        query = query[:query.index("\nfunc ")]
        for table in ("npc_social_relations", "npc_disciple_bonds"):
            with self.subTest(table=table):
                self.assertNotIn(table, query)


class BootDoesNotSpendTwoThousandRoundTrips(unittest.TestCase):
    def test_the_catalogue_sync_goes_in_one_batch(self):
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        sync = body(core, "sync_world_catalog")
        self.assertIn("_go_transport.batch(statements, transaction=True)", sync)
        # And the local-SQLite path still works, because tests and a
        # non-engine deployment both take it.
        self.assertIn("BEGIN IMMEDIATE", sync)

    def test_the_statements_stay_in_the_method_the_authority_gate_reads(self):
        """`test_authority_boundary` reads the write allowlists off the method
        that contains the SQL. Extracting the statements into a helper would
        have meant widening an authority gate to accommodate a refactor that
        changes no authority, so they stay here."""
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        sync = body(core, "sync_world_catalog")
        for table in ("catalog_npcs", "catalog_locations", "territory_state", "world_eras"):
            with self.subTest(table=table):
                self.assertIn(table, sync)

    def test_the_era_seed_is_not_batched(self):
        """It is a read-then-write: batching it would mean sending an INSERT
        that must not run."""
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        sync = code(core, "sync_world_catalog")
        self.assertLess(sync.index("batch(statements"), sync.index("world_eras"))


class TheCatalogueIsParsedOncePerVersion(unittest.TestCase):
    def test_load_is_memoised_on_the_file_rather_than_the_path(self):
        source = (GO / "worlddata" / "catalog.go").read_text(encoding="utf-8")
        load = source[source.index("func Load(path string)"):]
        load = load[:load.index("\nfunc ")]
        self.assertIn("os.Stat", load)
        self.assertIn("ModTime", load)
        self.assertIn("Size()", load)
        # Keyed on the file's stat, so an operator editing content on a live
        # NAS does not need a restart.
        self.assertIn("catalogCache", load)


def _locations_module():
    """`app.bot.locations` with the engine and the registry replaced.

    Importing it pulls in the whole bot runtime, which reads the environment,
    so this mirrors the env other `app.bot` tests use.
    """
    env = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
           "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
           "DATABASE_PATH": "data/test.sqlite3"}
    with patch.dict(os.environ, env):
        return importlib.import_module("app.bot.locations")


class _CountingSim:
    """One shared table of simulation rows, and a count of what it was asked.

    Everybody sits at home, which is how a freshly bootstrapped world starts
    and the only state in which the daily schedule applies at all - so it is
    also the state in which the schedule and the simulation can disagree.
    """

    def __init__(self, catalogue):
        self.rows = {
            name: {"npc_name": name, "home_location": str(d.get("location")),
                   "current_location": str(d.get("location")), "status": "alive"}
            for name, d in catalogue.items() if d.get("location")
        }
        self.at_location_calls = 0
        self.status_calls = 0

    async def npcs_at_location(self, location):
        self.at_location_calls += 1
        return [dict(r) for r in self.rows.values() if r["current_location"] == str(location)]

    async def npc_status(self, npc_name):
        self.status_calls += 1
        row = self.rows.get(str(npc_name))
        return dict(row) if row else None


class _NoRegistry:
    async def get_registered_npc(self, name):
        return None


@contextlib.contextmanager
def _wired(module):
    sim = _CountingSim(module.WORLD.npcs)

    async def _clock():
        return SimpleNamespace(total_minutes=0, period="Morning")

    saved = (module.SIM, module.DB, module.current_world_time)
    module.SIM, module.DB, module.current_world_time = sim, _NoRegistry(), _clock
    try:
        yield sim
    finally:
        module.SIM, module.DB, module.current_world_time = saved


PERIODS = ("Dawn", "Morning", "Afternoon", "Evening", "Night")


class WhoIsHereCostsOneQuery(unittest.TestCase):
    """The cost is measured, not read off the source.

    The first version of `npcs_present` passed every source check in this file
    and still cost 558 engine round trips per open: it asked the engine for
    everybody at one location, then fell through to `current_npc_location` -
    one `npc.status` each - for all five hundred and seventy the query had not
    returned, which is everybody in the world who is somewhere else. The saving
    was fifteen calls out of 574. Nothing here could see that, because the
    calls were one level down.
    """

    def test_one_open_does_not_scale_with_the_catalogue(self):
        module = _locations_module()
        with _wired(module) as sim:
            where = str(next(iter(module.WORLD.npcs.values())).get("location"))
            asyncio.run(module.npcs_present(where, "Afternoon"))
        self.assertEqual(sim.at_location_calls, 1)
        # The engine is asked about somebody only when content puts them here
        # and the answer is therefore in doubt. That is a handful of people at
        # any one place - never a number that tracks the catalogue.
        self.assertLessEqual(
            sim.status_calls, 12,
            f"drawing one scene cost {sim.status_calls} npc.status round trips against a "
            f"catalogue of {len(module.WORLD.npcs)}; the per-NPC loop is back",
        )

    def test_it_agrees_with_the_single_lookup_wherever_a_schedule_moves_somebody(self):
        """The disagreement this catches is not hypothetical: with everybody
        sitting at home, fourteen catalogue NPCs keep a schedule that takes
        them elsewhere, and the first version offered all of them in the room
        their simulation row named - while `/talk` sent them to the room their
        schedule named. Twenty such pairs across the five periods."""
        module = _locations_module()
        world = module.WORLD
        movers = {
            name for name, d in world.npcs.items()
            if any(str((d.get("schedule") or {}).get(p) or "") not in ("", str(d.get("location") or ""))
                   for p in PERIODS)
        }
        self.assertTrue(movers, "no catalogue NPC keeps a schedule, so this proves nothing")
        places = sorted(
            {str(world.npcs[n].get("location") or "") for n in movers}
            | {str((world.npcs[n].get("schedule") or {}).get(p) or "") for n in movers for p in PERIODS}
        ) 
        places = [p for p in places if p]

        async def sweep():
            wrong = []
            for period in PERIODS:
                for where in places:
                    listed = set(await module.npcs_present(where, period))
                    for name in sorted(movers):
                        actually = await module.current_npc_location(name, period)
                        if (actually == where) != (name in listed):
                            wrong.append(f"{period} {where}: npcs_present says "
                                         f"{'yes' if name in listed else 'no'} to {name!r}, "
                                         f"current_npc_location says {actually!r}")
            return wrong

        module_wired = _wired(module)
        with module_wired:
            wrong = asyncio.run(sweep())
        self.assertEqual(
            wrong, [],
            "the picker and the command disagree about where somebody is, so a player is "
            "offered an NPC and then refused:\n  " + "\n  ".join(wrong[:10]),
        )


if __name__ == "__main__":
    unittest.main()
