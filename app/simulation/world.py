"""Python's side of the world simulation: orchestration and reads, no rules.

Since v0.30.0 (Authority II) this module runs no SQL. Every simulation
mutation was already delegated to the Go engine; the reads that stayed here
as raw SQL through a Go-hosted session - market rows and quotes, challenge
targets, simulation state, the recent-action ledger, the NPC/sect/clan/
region status panels - are now engine queries, so the rules those reads
carried (the sell price, the hidden-master filter, the lag against the
clock, which items a market may stock) live in Go and nowhere else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..database import Database

MINUTES_PER_DAY = 24 * 60

# The native batches the engine runs, in the order `force_run("all")` walks
# them. Their intervals are engine state (`admin.simulation.interval` sets
# them, `simulation.status` reports them); Python keeps only the names.
SIMULATION_SYSTEMS: tuple[str, ...] = (
    "npc_civilization",
    "npc_life",
    "dynamic_economy",
    "black_markets",
    "sect_politics",
    "clan_dynamics",
    "autonomous_world_events",
)


@dataclass
class SimulationRun:
    system: str
    due_steps: int
    applied_steps: int
    summary: str
    events: tuple[dict[str, Any], ...] = ()


class WorldSimulator:
    """Persistent, bounded world simulation driven by canonical world time.

    The engine stores its own last-simulated anchors so bot restarts are safe.
    Large time gaps are summarized in bounded aggregate updates rather than
    replayed as millions of tiny ticks.
    """

    def __init__(self, db: Database, world_data: dict[str, Any], *, engine: Any):
        self.db = db
        self.engine = engine
        self.world = world_data

    def _require_engine(self) -> Any:
        if self.engine is None:
            raise RuntimeError("WorldSimulator requires the Go engine for simulation mutations")
        return self.engine

    async def _query(self, operation: str, payload: dict[str, Any]) -> Any:
        return await self._require_engine().action(operation, 0, payload)

    # ------------------------------------------------------------------
    # Mutations: delegated to the engine.
    # ------------------------------------------------------------------
    async def initialize(self, game_minute: int) -> None:
        """Bootstrap simulation state through the authoritative Go engine."""
        engine = self._require_engine()
        await engine.bootstrap_simulation(int(game_minute))
        if not await self.db.list_active_black_markets(int(game_minute)):
            await engine.force_simulation("black_markets", 1, int(game_minute))

    async def run_due(self, game_minute: int, automation: dict[str, bool]) -> list[SimulationRun]:
        """Delegate scheduled simulation exclusively to the authoritative Go engine."""
        if self.engine is None:
            raise RuntimeError("WorldSimulator requires the Go engine for simulation mutations")
        rows = await self.engine.run_due_simulation(int(game_minute), automation)
        return [
            SimulationRun(
                str(row.get("system", "")),
                int(row.get("due_steps", 0)),
                int(row.get("applied_steps", 0)),
                str(row.get("summary", "")),
                tuple(dict(event) for event in list(row.get("events") or [])),
            )
            for row in rows
        ]

    async def force_run(self, system: str, steps: int, game_minute: int) -> SimulationRun:
        """Delegate forced simulation exclusively to the authoritative Go engine."""
        if self.engine is None:
            raise RuntimeError("WorldSimulator requires the Go engine for simulation mutations")
        applied = max(1, min(120, int(steps)))
        if system == "all":
            summaries: list[str] = []
            for key in SIMULATION_SYSTEMS:
                row = await self.engine.force_simulation(key, applied, int(game_minute))
                summaries.append(f"{key}: {row.get('summary', '')}")
            return SimulationRun("all", applied, applied, " | ".join(summaries))
        row = await self.engine.force_simulation(str(system), applied, int(game_minute))
        return SimulationRun(
            str(row.get("system", system)),
            int(row.get("due_steps", steps)),
            int(row.get("applied_steps", steps)),
            str(row.get("summary", "")),
        )

    async def set_interval_days(self, system: str, days: int) -> dict[str, Any]:
        """Delegate simulation interval mutation to the authoritative Go engine."""
        engine = self._require_engine()
        if system not in SIMULATION_SYSTEMS:
            raise ValueError("Unknown simulation system")
        bounded_days = max(1, min(365, int(days)))
        result = await engine.action(
            "admin.simulation.interval",
            0,
            {"system": str(system), "days": bounded_days, "reason": "discord admin"},
        )
        return dict(result or {})

    # ------------------------------------------------------------------
    # Reads: engine queries. Each keeps the shape its Discord caller reads.
    # ------------------------------------------------------------------
    async def get_system_state(self, system: str) -> dict[str, Any] | None:
        row = await self._query("simulation.state", {"system": str(system)})
        if not row or not row.get("found"):
            return None
        out = dict(row)
        out.pop("found", None)
        return out

    async def simulation_status(self) -> list[dict[str, Any]]:
        """Every system with its lag, measured by the engine against its own clock."""
        result = await self._query("simulation.status", {})
        return [dict(row) for row in list((result or {}).get("systems") or [])]

    async def recent_player_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        result = await self._query("world.recent_actions", {"limit": max(1, min(100, int(limit)))})
        return [dict(row) for row in list((result or {}).get("actions") or [])]

    async def combat_targets(self, location: str) -> list[dict[str, Any]]:
        """Living NPC/family-head targets mechanically present in a location.

        The engine omits real hidden masters, so this cannot become a
        hidden-power detector; they still enter battles through explicit
        events or GM actions.
        """
        result = await self._query("combat.targets", {"location": str(location)})
        return [dict(row) for row in list((result or {}).get("targets") or [])]

    async def combat_target(self, location: str, target_name: str) -> dict[str, Any] | None:
        for row in await self.combat_targets(location):
            if str(row.get("name", "")).casefold() == str(target_name).casefold():
                return row
        return None

    async def civilization_status(self, location: str) -> dict[str, Any] | None:
        return self._found(await self._query("civilization.status", {"location": str(location)}))

    async def npc_status(self, npc_name: str) -> dict[str, Any] | None:
        return self._found(await self._query("npc.status", {"npc_name": str(npc_name)}))

    async def sect_status(self, sect_name: str) -> dict[str, Any] | None:
        return self._found(await self._query("sect.status", {"sect_name": str(sect_name)}))

    async def clan_status(self, family_id: int) -> dict[str, Any]:
        result = await self._query("clan.status", {"family_id": int(family_id)})
        out = dict(result or {})
        return {
            "branches": [dict(r) for r in list(out.get("branches") or [])],
            "retainers": [dict(r) for r in list(out.get("retainers") or [])],
            "relations": [dict(r) for r in list(out.get("relations") or [])],
        }

    async def market_rows(self, location: str, limit: int = 25) -> list[dict[str, Any]]:
        result = await self._query("market.rows", {"location": str(location), "limit": max(1, min(100, int(limit)))})
        return [dict(row) for row in list((result or {}).get("rows") or [])]

    async def market_quote(self, location: str, item_id: str) -> dict[str, Any] | None:
        """The engine's buy and sell price for one item, or None when not traded there."""
        result = await self._query("market.quote", {"location": str(location), "item_id": str(item_id)})
        if not result or not result.get("traded"):
            return None
        return dict(result)

    async def market_item_ids(self) -> list[str]:
        """Catalogue items an ordinary regional market may stock, by the engine's rule."""
        result = await self._query("market.catalog", {})
        return [str(item_id) for item_id in list((result or {}).get("item_ids") or [])]

    @staticmethod
    def _found(result: Any) -> dict[str, Any] | None:
        if not result or not result.get("found"):
            return None
        out = dict(result)
        out.pop("found", None)
        return out
