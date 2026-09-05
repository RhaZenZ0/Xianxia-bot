from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from . import Database


ROOT = Path(__file__).resolve().parents[2]


def configured_database_path() -> Path:
    configured = Path(os.getenv("DATABASE_PATH", "data/xianxia.sqlite3"))
    return configured if configured.is_absolute() else ROOT / configured


async def bootstrap() -> dict[str, object]:
    """Create or migrate the configured database without deleting existing data."""
    database = Database(configured_database_path())
    await database.init()
    schema = await database.get_schema_status()
    probe = await database.operational_health()
    if not schema.get("compatible") or not probe.get("ok"):
        raise RuntimeError(
            "SQLite bootstrap did not produce an operational schema: "
            + json.dumps({"schema": schema, "probe": probe}, sort_keys=True)
        )
    return {
        "path": str(database.path),
        "schema_version": int(schema["current"]),
        "migrations": int(schema["migrations"]),
        "journal_mode": str(probe["journal_mode"]),
    }


def main() -> int:
    result = asyncio.run(bootstrap())
    print("DATABASE_BOOTSTRAP_READY " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
