"""Small dependency shims for artifact-only tests.

Production and Docker install requirements.txt. The source archive's tests can
also run in a minimal Python environment where aiosqlite is unavailable.
"""

import importlib.util
import importlib.machinery
import sqlite3
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def install_aiosqlite_shim() -> None:
    if "aiosqlite" in sys.modules:
        return
    if importlib.util.find_spec("aiosqlite") is not None:
        return

    class _Cursor:
        def __init__(self, cursor):
            self._cursor = cursor

        @property
        def lastrowid(self):
            return self._cursor.lastrowid

        @property
        def rowcount(self):
            return self._cursor.rowcount

        async def fetchone(self):
            return self._cursor.fetchone()

        async def fetchall(self):
            return self._cursor.fetchall()

    class _Connection:
        def __init__(self, path, timeout=5):
            self._db = sqlite3.connect(path, timeout=timeout)

        @property
        def row_factory(self):
            return self._db.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._db.row_factory = value

        async def execute(self, sql, params=()):
            return _Cursor(self._db.execute(sql, params))

        async def executescript(self, sql):
            self._db.executescript(sql)

        async def commit(self):
            self._db.commit()

        async def rollback(self):
            self._db.rollback()

        async def close(self):
            if self._db is not None:
                self._db.close()
                self._db = None

        def __del__(self):
            db = getattr(self, "_db", None)
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
                self._db = None

    class _ConnectContext:
        def __init__(self, path, timeout=5):
            self.path = path
            self.timeout = timeout
            self.connection = None

        def __await__(self):
            async def _get():
                if self.connection is None:
                    self.connection = _Connection(self.path, self.timeout)
                return self.connection

            return _get().__await__()

        async def __aenter__(self):
            if self.connection is None:
                self.connection = _Connection(self.path, self.timeout)
            return self.connection

        async def __aexit__(self, *_):
            if self.connection is not None:
                await self.connection.close()
                self.connection = None

    shim = types.ModuleType("aiosqlite")
    shim.__spec__ = importlib.machinery.ModuleSpec("aiosqlite", loader=None)
    shim.connect = lambda path, timeout=5: _ConnectContext(path, timeout)
    shim.Row = sqlite3.Row
    shim.Connection = _Connection
    shim.IntegrityError = sqlite3.IntegrityError
    sys.modules["aiosqlite"] = shim


def install_dotenv_shim() -> None:
    if importlib.util.find_spec("dotenv") is not None or "dotenv" in sys.modules:
        return
    shim = types.ModuleType("dotenv")
    shim.__spec__ = importlib.machinery.ModuleSpec("dotenv", loader=None)
    shim.load_dotenv = lambda *args, **kwargs: False
    sys.modules["dotenv"] = shim


def install_openai_shim() -> None:
    """Provide the tiny AsyncOpenAI surface needed by narrator unit tests."""
    try:
        from openai import AsyncOpenAI  # noqa: F401
        return
    except (ImportError, ModuleNotFoundError):
        pass

    class _Responses:
        async def create(self, **kwargs):
            raise RuntimeError("OpenAI shim cannot perform network requests")

    class _AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.responses = _Responses()

    shim = types.ModuleType("openai")
    shim.__spec__ = importlib.machinery.ModuleSpec("openai", loader=None)
    shim.AsyncOpenAI = _AsyncOpenAI
    sys.modules["openai"] = shim
