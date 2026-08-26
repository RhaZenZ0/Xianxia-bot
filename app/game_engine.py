from __future__ import annotations

from typing import Any

import httpx


class GameEngineError(RuntimeError):
    pass


class GameEngineClient:
    """Authoritative Go game-engine client.

    The Discord process does presentation, AI/RAG, and orchestration. Mechanical
    mutations and heavy simulation are submitted to this service, which is the
    production owner of SQLite.
    """

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0):
        self.base_url = str(base_url).strip().rstrip("/")
        if not self.base_url:
            raise ValueError("Game engine URL is required")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(f"{self.base_url}{path}", json=payload)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise GameEngineError(f"Go engine request failed ({response.status_code}): {detail}")
        return dict(response.json())

    async def action(self, operation: str, actor_id: int, payload: dict[str, Any]) -> Any:
        data = await self._post(
            "/v1/game/action",
            {"operation": str(operation), "actor_id": int(actor_id), "payload": dict(payload)},
        )
        return data.get("result")

    async def run_due_simulation(self, game_minute: int, automation: dict[str, bool]) -> list[dict[str, Any]]:
        data = await self._post(
            "/v1/simulation/run-due",
            {"game_minute": int(game_minute), "automation": {str(k): bool(v) for k, v in automation.items()}},
        )
        return [dict(row) for row in data.get("runs", [])]

    async def force_simulation(self, system: str, steps: int, game_minute: int) -> dict[str, Any]:
        return await self._post(
            "/v1/simulation/force",
            {"system": str(system), "steps": int(steps), "game_minute": int(game_minute)},
        )

    async def live(self) -> dict[str, Any]:
        response = await self._client.get(f"{self.base_url}/livez")
        response.raise_for_status()
        return dict(response.json())

    async def database_status(self) -> dict[str, Any]:
        response = await self._client.get(f"{self.base_url}/v1/db/status")
        response.raise_for_status()
        return dict(response.json())
