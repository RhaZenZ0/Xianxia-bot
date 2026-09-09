from __future__ import annotations

import asyncio
import json
import hmac
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .http_limits import (
    STREAM_LIMIT,
    ConnectionLimiter,
    EmptyRequest,
    HeaderLimits,
    RequestHeadRejected,
    read_request_head,
)
from ..version import RELEASE_VERSION

log = logging.getLogger("xianxia.health")

STARTUP_PHASES = (
    "DATABASE_READY",
    "CATALOG_READY",
    "SIMULATION_READY",
    "DISCORD_READY",
)


@dataclass
class PhaseState:
    ready: bool = False
    changed_at: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


class HealthState:
    """In-memory operational state served by the health HTTP endpoint.

    The state intentionally contains no Discord-specific objects so it can be
    tested and queried even while the gateway is still connecting.
    """

    def __init__(self, *, supported_schema_version: int) -> None:
        self.boot_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.supported_schema_version = int(supported_schema_version)
        self.schema_version: int | None = None
        self.phases = {phase: PhaseState() for phase in STARTUP_PHASES}
        self.checks: dict[str, dict[str, Any]] = {}
        self.runtime_metrics: dict[str, float] = {}
        self.last_error: dict[str, Any] | None = None

    def set_schema_version(self, version: int) -> None:
        self.schema_version = int(version)

    def mark_phase(self, phase: str, *, detail: dict[str, Any] | None = None) -> bool:
        if phase not in self.phases:
            raise ValueError(f"Unknown startup phase: {phase}")
        state = self.phases[phase]
        first_ready = not state.ready
        state.ready = True
        state.changed_at = time.time()
        state.detail = dict(detail or {})
        if first_ready:
            log.info("%s boot_id=%s detail=%s", phase, self.boot_id, state.detail)
        return first_ready

    def clear_phase(self, phase: str, *, reason: str = "") -> None:
        if phase not in self.phases:
            raise ValueError(f"Unknown startup phase: {phase}")
        state = self.phases[phase]
        state.ready = False
        state.changed_at = time.time()
        state.detail = {"reason": reason} if reason else {}

    def set_check(self, name: str, ok: bool, **detail: Any) -> None:
        self.checks[str(name)] = {
            "ok": bool(ok),
            "checked_at": time.time(),
            **detail,
        }

    def set_metric(self, name: str, value: int | float) -> None:
        self.runtime_metrics[str(name)] = float(value)

    def fail(self, phase: str, exc: BaseException) -> None:
        self.last_error = {
            "phase": str(phase),
            "type": type(exc).__name__,
            "message": str(exc),
            "at": time.time(),
        }
        log.exception("STARTUP_FAILED phase=%s boot_id=%s", phase, self.boot_id, exc_info=exc)

    @property
    def startup_ready(self) -> bool:
        return all(state.ready for state in self.phases.values())

    @property
    def runtime_healthy(self) -> bool:
        return all(bool(check.get("ok")) for check in self.checks.values()) if self.checks else True

    @property
    def ready(self) -> bool:
        schema_compatible = self.schema_version == self.supported_schema_version
        return self.startup_ready and self.runtime_healthy and schema_compatible and self.last_error is None

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "status": "ready" if self.ready else ("degraded" if self.startup_ready else "starting"),
            "version": RELEASE_VERSION,
            "ready": self.ready,
            "boot_id": self.boot_id,
            "uptime_seconds": round(max(0.0, now - self.started_at), 3),
            "schema": {
                "current": self.schema_version,
                "supported": self.supported_schema_version,
                "compatible": self.schema_version == self.supported_schema_version,
            },
            "phases": {
                phase: {
                    "ready": state.ready,
                    "changed_at": state.changed_at or None,
                    "detail": state.detail,
                }
                for phase, state in self.phases.items()
            },
            "checks": self.checks,
            "metrics": dict(self.runtime_metrics),
            "last_error": self.last_error,
        }

    def prometheus_metrics(self) -> str:
        lines = [
            "# HELP xianxia_build_info Static release metadata for this running bot.",
            "# TYPE xianxia_build_info gauge",
            f'xianxia_build_info{{version="{RELEASE_VERSION}"}} 1',
            "# HELP xianxia_startup_ready Whether every startup phase and runtime check is ready.",
            "# TYPE xianxia_startup_ready gauge",
            f"xianxia_startup_ready {1 if self.ready else 0}",
            "# HELP xianxia_uptime_seconds Process uptime in seconds.",
            "# TYPE xianxia_uptime_seconds gauge",
            f"xianxia_uptime_seconds {max(0.0, time.time() - self.started_at):.3f}",
            "# HELP xianxia_schema_version Current SQLite schema version.",
            "# TYPE xianxia_schema_version gauge",
            f"xianxia_schema_version {self.schema_version if self.schema_version is not None else -1}",
            "# HELP xianxia_startup_phase_ready Startup phase readiness.",
            "# TYPE xianxia_startup_phase_ready gauge",
        ]
        for phase, state in self.phases.items():
            lines.append(f'xianxia_startup_phase_ready{{phase="{phase}"}} {1 if state.ready else 0}')
        lines.extend([
            "# HELP xianxia_runtime_check_ok Runtime dependency check state.",
            "# TYPE xianxia_runtime_check_ok gauge",
        ])
        for name, check in sorted(self.checks.items()):
            safe = name.replace('"', "'")
            lines.append(f'xianxia_runtime_check_ok{{check="{safe}"}} {1 if check.get("ok") else 0}')
        lines.extend([
            "# HELP xianxia_runtime_metric Numeric runtime operational metrics.",
            "# TYPE xianxia_runtime_metric gauge",
        ])
        for name, value in sorted(self.runtime_metrics.items()):
            safe = name.replace('"', "'")
            lines.append(f'xianxia_runtime_metric{{metric="{safe}"}} {float(value):.6f}')
        return "\n".join(lines) + "\n"


class HealthServer:
    def __init__(
        self,
        state: HealthState,
        *,
        host: str = "0.0.0.0",
        port: int = 8082,
        control_handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        control_token: str = "",
        header_limits: HeaderLimits | None = None,
        max_connections: int = 64,
    ) -> None:
        self.state = state
        self.host = str(host)
        self.port = int(port)
        self.control_handler = control_handler
        self.control_token = str(control_token or "")
        # The header loop below runs before any authentication, so its bounds are
        # the only thing standing between an unauthenticated peer and this
        # process's memory and connection table. A 2s per-line timeout that reset
        # on every line was not one.
        self.header_limits = (header_limits or HeaderLimits()).validated()
        self.connections = ConnectionLimiter(max_connections)
        self.rejected_heads = 0
        self._server: asyncio.AbstractServer | None = None

    @property
    def bound_port(self) -> int | None:
        if not self._server or not self._server.sockets:
            return None
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port, limit=STREAM_LIMIT
        )
        log.info("HEALTH_SERVER_READY host=%s port=%s boot_id=%s", self.host, self.bound_port, self.state.boot_id)

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()
        await server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self.connections.try_acquire():
            # Refuse loudly and immediately rather than queueing: a queued slow
            # connection still costs a task and a buffer.
            try:
                await self._respond(writer, 503, {"error": "too_many_connections"})
            except Exception:
                pass
            try:
                writer.close()
            except Exception:
                pass
            return
        try:
            try:
                head = await read_request_head(reader, limits=self.header_limits)
            except EmptyRequest:
                return
            except RequestHeadRejected as rejected:
                self.rejected_heads += 1
                self.state.set_metric("health_head_rejected", self.rejected_heads)
                self.state.set_metric("health_connections_refused", self.connections.refused)
                log.warning(
                    "HEALTH_HEAD_REJECTED status=%s error=%s detail=%s",
                    rejected.status, rejected.error, rejected.detail,
                )
                await self._respond(writer, rejected.status, {"error": rejected.error})
                return
            request = head.request_line.split()
            headers = head.headers
            method = request[0].upper() if request else "GET"
            path = request[1].split("?", 1)[0] if len(request) >= 2 else "/"

            if path == "/livez":
                await self._respond(writer, 200, {"status": "alive", "boot_id": self.state.boot_id})
            elif path in {"/healthz", "/readyz"}:
                snapshot = self.state.snapshot()
                await self._respond(writer, 200 if self.state.ready else 503, snapshot)
            elif path == "/metrics":
                await self._respond_text(writer, 200, self.state.prometheus_metrics(), "text/plain; version=0.0.4")
            elif path == "/control/discord":
                if method != "POST":
                    await self._respond(writer, 405, {"error": "method_not_allowed"})
                elif self.control_handler is None or not self.control_token:
                    await self._respond(writer, 404, {"error": "not_found"})
                elif not hmac.compare_digest(headers.get("x-xianxia-control", ""), self.control_token):
                    await self._respond(writer, 403, {"error": "forbidden"})
                else:
                    try:
                        length = int(headers.get("content-length", "0") or 0)
                    except ValueError:
                        length = -1
                    if length < 2 or length > 65536:
                        await self._respond(writer, 400, {"error": "invalid_body_size"})
                    else:
                        body = await asyncio.wait_for(reader.readexactly(length), timeout=3.0)
                        try:
                            payload = json.loads(body.decode("utf-8"))
                            action = str(payload.get("action") or "status")
                            args = dict(payload.get("payload") or {})
                            result = await self.control_handler(action, args)
                        except (ValueError, TypeError, json.JSONDecodeError) as exc:
                            await self._respond(writer, 400, {"error": "bad_request", "message": str(exc)[:300]})
                        except PermissionError as exc:
                            await self._respond(writer, 403, {"error": "forbidden", "message": str(exc)[:300]})
                        except Exception as exc:
                            log.exception("Discord control request failed")
                            await self._respond(writer, 500, {"error": "control_failed", "message": str(exc)[:300]})
                        else:
                            await self._respond(writer, 200, result)
            else:
                await self._respond(writer, 404, {"error": "not_found"})
        except (asyncio.TimeoutError, ConnectionError):
            pass
        except Exception:
            log.exception("Health HTTP request failed")
        finally:
            self.connections.release()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(self, writer: asyncio.StreamWriter, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
        await self._respond_text(writer, status, body, "application/json")

    @staticmethod
    async def _respond_text(writer: asyncio.StreamWriter, status: int, body: str, content_type: str) -> None:
        # 408/414/431 are answered by the request-head limiter; without them a
        # rejected slow-header request went out as "HTTP/1.1 408 OK".
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 408: "Request Timeout", 414: "URI Too Long", 431: "Request Header Fields Too Large", 500: "Internal Server Error", 503: "Service Unavailable"}.get(status, "OK")
        encoded = body.encode("utf-8")
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(encoded)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(headers + encoded)
        await writer.drain()
