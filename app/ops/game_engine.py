from __future__ import annotations

import os
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

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0, auth_token: str | None = None):
        self.base_url = str(base_url).strip().rstrip("/")
        if not self.base_url:
            raise ValueError("Game engine URL is required")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )
        self.auth_token = str(auth_token if auth_token is not None else os.getenv("ENGINE_AUTH_TOKEN", "")).strip()

    def _headers(self) -> dict[str, str] | None:
        return {"X-Xianxia-Engine-Token": self.auth_token} if self.auth_token else None

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(f"{self.base_url}{path}", json=payload, headers=self._headers())
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            if isinstance(detail, dict) and detail.get("message"):
                detail = str(detail["message"])
            raise GameEngineError(str(detail))
        return dict(response.json())

    async def action(
        self, operation: str, actor_id: int, payload: dict[str, Any], *,
        action_id: str | None = None, expected_version: int | None = None,
    ) -> Any:
        request: dict[str, Any] = {
            "api_version": "v1",
            "operation": str(operation),
            "actor_id": int(actor_id),
            "payload": dict(payload),
        }
        if action_id is not None:
            request["action_id"] = str(action_id)
        if expected_version is not None:
            request["expected_version"] = int(expected_version)
        data = await self._post("/v1/game/action", request)
        return data.get("result")

    async def authoritative_action(
        self, operation: str, actor_id: int, payload: dict[str, Any], *,
        action_id: str, expected_version: int | None = None,
    ) -> dict[str, Any]:
        if "game_minute" in payload:
            raise ValueError(
                "authoritative action payloads must not include game_minute; Go owns current world time"
            )
        request: dict[str, Any] = {
            "api_version": "v1",
            "action_id": str(action_id),
            "operation": str(operation),
            "actor_id": int(actor_id),
            "payload": dict(payload),
        }
        if expected_version is not None:
            request["expected_version"] = int(expected_version)
        return await self._post("/v1/game/action", request)

    async def world_clock(self) -> dict[str, Any]:
        """The canonical world clock, as the engine computes it (v1.0.0-rc.39).

        The one door to the time of day. Python used to keep two copies of the
        anchor arithmetic - `Database.get_world_clock`, which also re-anchored
        the row whenever the configured scale disagreed with the stored one,
        and the dashboard's own read - so a rate a GM set on the dashboard was
        undone by the next command that asked what time it was.
        """
        return dict(await self.action("world.clock", 0, {}) or {})

    # None of the three sends a minute (v1.0.0-rc.48). The engine derives the
    # canonical one inside Go for all of them - `RunDue` has since v0.22.2,
    # `Force` and `Bootstrap` since rc.48 - so a minute here would be a number
    # the caller computes, ships over HTTP and watches the engine throw away.
    # The wire still accepts the field, so an older bot mid-upgrade keeps
    # working; nothing in this tree sends it, and
    # `tests/python/contracts/test_simulation_minute.py` is what holds that.

    async def bootstrap_simulation(self) -> dict[str, Any]:
        return await self._post("/v1/simulation/bootstrap", {})

    async def run_due_simulation(self, automation: dict[str, bool]) -> list[dict[str, Any]]:
        data = await self._post(
            "/v1/simulation/run-due",
            {"automation": {str(k): bool(v) for k, v in automation.items()}},
        )
        return [dict(row) for row in data.get("runs", [])]

    async def force_simulation(self, system: str, steps: int) -> dict[str, Any]:
        return await self._post(
            "/v1/simulation/force",
            {"system": str(system), "steps": int(steps)},
        )

    async def live(self) -> dict[str, Any]:
        response = await self._client.get(f"{self.base_url}/livez")
        response.raise_for_status()
        return dict(response.json())

    async def database_status(self) -> dict[str, Any]:
        response = await self._client.get(f"{self.base_url}/v1/db/status", headers=self._headers())
        response.raise_for_status()
        return dict(response.json())
