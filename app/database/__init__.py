"""Persistent database package.

The public API remains ``app.database`` while implementation and migrations live
inside the package.  Migration definitions are intentionally kept in ``core``
without rewriting historical migration SQL.
"""

from .core import (
    Database,
    OPERATIONAL_REQUIRED_TABLES,
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION,
)

__all__ = [
    "Database",
    "OPERATIONAL_REQUIRED_TABLES",
    "SCHEMA_MIGRATIONS",
    "SCHEMA_VERSION",
]
