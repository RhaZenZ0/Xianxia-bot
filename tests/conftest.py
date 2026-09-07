"""Shared pytest bootstrap for the Python-owned test surface."""
from __future__ import annotations

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
