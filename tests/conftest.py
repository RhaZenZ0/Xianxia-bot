"""Shared pytest bootstrap for the Python-owned test surface."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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
