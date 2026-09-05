from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx


class RemoteDatabaseError(RuntimeError):
    pass


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__blob_b64"}:
        return base64.b64decode(str(value["__blob_b64"]).encode("ascii"))
    return value


def _encode_param(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__blob_b64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, tuple):
        return [_encode_param(v) for v in value]
    if isinstance(value, list):
        return [_encode_param(v) for v in value]
    return value


class RemoteRow:
    """sqlite3.Row-compatible subset used by the existing repository layer."""

    __slots__ = ("_columns", "_values", "_index")

    def __init__(self, columns: list[str], values: list[Any]):
        self._columns = tuple(columns)
        self._values = tuple(_decode_value(v) for v in values)
        self._index = {name: idx for idx, name in enumerate(self._columns)}

    def keys(self):
        return list(self._columns)

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self):
        return iter(self._values)

    def __getitem__(self, key: int | str):
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]


@dataclass(slots=True)
class RemoteCursor:
    columns: list[str]
    rows: list[list[Any]]
    lastrowid: int
    rowcount: int
    row_mode: bool
    _offset: int = 0

    def _convert(self, values: list[Any]):
        decoded = [_decode_value(v) for v in values]
        return RemoteRow(self.columns, decoded) if self.row_mode else tuple(decoded)

    async def fetchone(self):
        if self._offset >= len(self.rows):
            return None
        row = self._convert(self.rows[self._offset])
        self._offset += 1
        return row

    async def fetchall(self):
        if self._offset >= len(self.rows):
            return []
        result = [self._convert(row) for row in self.rows[self._offset :]]
        self._offset = len(self.rows)
        return result


class RemoteSQLiteConnection:
    """Transaction-capable SQLite session hosted by the authoritative Go engine."""

    def __init__(self, client: httpx.AsyncClient, engine_url: str, session_id: str):
        self._client = client
        self._engine_url = engine_url.rstrip("/")
        self._session_id = session_id
        self._row_factory: Any = None
        self._closed = False

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._row_factory = value

    async def _post(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._engine_url}/v1/db/session/{self._session_id}/{action}",
            json=payload or {},
        )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise RemoteDatabaseError(f"Go SQLite {action} failed ({response.status_code}): {detail}")
        return response.json()

    async def execute(self, sql: str, params: Any = ()) -> RemoteCursor:
        data = await self._post(
            "execute",
            {"sql": str(sql), "params": [_encode_param(v) for v in tuple(params or ())]},
        )
        return RemoteCursor(
            columns=[str(v) for v in data.get("columns", [])],
            rows=[list(v) for v in data.get("rows", [])],
            lastrowid=int(data.get("lastrowid", 0) or 0),
            rowcount=int(data.get("rowcount", 0) or 0),
            row_mode=self._row_factory is not None,
        )

    async def executescript(self, sql: str):
        await self._post("script", {"sql": str(sql)})

    async def commit(self):
        await self._post("commit")

    async def rollback(self):
        await self._post("rollback")

    async def close(self):
        if self._closed:
            return
        self._closed = True
        response = await self._client.delete(f"{self._engine_url}/v1/db/session/{self._session_id}")
        if response.status_code >= 400 and response.status_code != 404:
            raise RemoteDatabaseError(f"Go SQLite session close failed ({response.status_code}): {response.text}")


class GoDatabaseTransport:
    def __init__(self, engine_url: str, *, timeout_seconds: float = 30.0):
        self.engine_url = str(engine_url).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )

    async def open(self) -> RemoteSQLiteConnection:
        response = await self._client.post(f"{self.engine_url}/v1/db/session", json={})
        if response.status_code >= 400:
            raise RemoteDatabaseError(f"Could not open Go SQLite session ({response.status_code}): {response.text}")
        session_id = str(response.json().get("session_id") or "")
        if not session_id:
            raise RemoteDatabaseError("Go engine returned an empty database session ID")
        return RemoteSQLiteConnection(self._client, self.engine_url, session_id)

    async def batch(self, statements: list[dict[str, Any]], *, transaction: bool = True) -> list[dict[str, Any]]:
        payload = {
            "transaction": bool(transaction),
            "statements": [
                {"sql": str(item["sql"]), "params": [_encode_param(v) for v in item.get("params", ())]}
                for item in statements
            ],
        }
        response = await self._client.post(f"{self.engine_url}/v1/db/batch", json=payload)
        if response.status_code >= 400:
            raise RemoteDatabaseError(f"Go SQLite batch failed ({response.status_code}): {response.text}")
        return list(response.json().get("results", []))

    async def status(self) -> dict[str, Any]:
        response = await self._client.get(f"{self.engine_url}/v1/db/status")
        response.raise_for_status()
        return dict(response.json())

    async def maintenance(self, action: str) -> dict[str, Any]:
        response = await self._client.post(f"{self.engine_url}/v1/db/maintenance", json={"action": str(action)})
        if response.status_code >= 400:
            raise RemoteDatabaseError(f"Go SQLite maintenance failed ({response.status_code}): {response.text}")
        return dict(response.json())

    async def create_backup(self) -> dict[str, Any]:
        response = await self._client.post(f"{self.engine_url}/v1/db/backups", json={})
        if response.status_code >= 400:
            raise RemoteDatabaseError(f"Go SQLite backup failed ({response.status_code}): {response.text}")
        return dict(response.json())

    async def list_backups(self) -> list[dict[str, Any]]:
        response = await self._client.get(f"{self.engine_url}/v1/db/backups")
        if response.status_code >= 400:
            raise RemoteDatabaseError(f"Go SQLite backup listing failed ({response.status_code}): {response.text}")
        return [dict(row) for row in response.json().get("backups", [])]

    async def restore_backup(self, name: str) -> dict[str, Any]:
        response = await self._client.post(f"{self.engine_url}/v1/db/restore", json={"name": str(name)})
        if response.status_code >= 400:
            raise RemoteDatabaseError(f"Go SQLite restore failed ({response.status_code}): {response.text}")
        return dict(response.json())
