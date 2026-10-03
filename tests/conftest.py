"""Shared pytest bootstrap for the Python-owned test surface."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# httpx is constructed at import time by app/database/remote.py and
# app/ops/game_engine.py, so without it `from app.database import Database`
# raises during collection and takes about twenty files - every integration
# test of the layers above the transport - out of the run. The shim is
# installed here rather than per-file because it has to be in place before
# collection imports anything. It refuses to send a request, so a test that
# actually reaches for the network still fails.
from tests.support import install_httpx_shim  # noqa: E402

install_httpx_shim()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tag tests by ownership layer from their directory."""
    for item in items:
        parts = set(Path(str(item.fspath)).parts)
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        elif "contracts" in parts:
            item.add_marker(pytest.mark.contract)
        elif "integration" in parts:
            item.add_marker(pytest.mark.integration)


# PYTEST_SHARD="<i>/<n>" runs one shard of the suite, which is how CI runs the
# python job as several jobs at once. A file outside the shard is never
# collected, so a shard does not pay to import what it does not run.
from tests.support import parse_shard, shard_assignment, suite_test_files  # noqa: E402

_SHARD = parse_shard(os.environ.get("PYTEST_SHARD", ""))
_SHARD_OF = shard_assignment(suite_test_files(), _SHARD[1]) if _SHARD else {}


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if not _SHARD:
        return None
    try:
        name = collection_path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return None
    shard = _SHARD_OF.get(name)
    if shard is None:
        return None
    return shard != _SHARD[0] or None
