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
import re
import unittest

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


if __name__ == "__main__":
    unittest.main()
