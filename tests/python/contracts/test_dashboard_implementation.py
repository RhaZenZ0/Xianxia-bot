from __future__ import annotations

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

from app.dashboard_contract import (
    DASHBOARD_REVIEWED_SCHEMA_VERSION,
    DASHBOARD_SYSTEM_TABLES,
    dashboard_implementation_issues,
)
from app.database import SCHEMA_VERSION


def test_dashboard_implementation_gate_is_clean():
    issues = dashboard_implementation_issues(PROJECT_ROOT, schema_version=SCHEMA_VERSION)
    assert issues == [], "Dashboard implementation gaps:\n - " + "\n - ".join(issues)


def test_dashboard_schema_review_and_newer_system_registry_are_explicit():
    assert DASHBOARD_REVIEWED_SCHEMA_VERSION == SCHEMA_VERSION
    assert {"cultivation", "crafting", "exploration", "economy", "dynasties"} <= set(DASHBOARD_SYSTEM_TABLES)
    assert "expedition_threads" in DASHBOARD_SYSTEM_TABLES["exploration"]


def test_dashboard_gate_detects_unreviewed_schema_bump():
    issues = dashboard_implementation_issues(PROJECT_ROOT, schema_version=SCHEMA_VERSION + 1)
    assert any("schema/dashboard review mismatch" in issue for issue in issues)


def test_dashboard_gate_detects_frontend_backend_drift(tmp_path):
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "app").mkdir()
    for rel in ("dashboard/index.html", "dashboard/app.js", "app/dashboard.py"):
        src = PROJECT_ROOT / rel
        dst = tmp_path / rel
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    js_path = tmp_path / "dashboard" / "app.js"
    js_path.write_text(
        js_path.read_text(encoding="utf-8").replace("/api/economy", "/api/economy_missing", 1),
        encoding="utf-8",
    )
    issues = dashboard_implementation_issues(tmp_path, schema_version=SCHEMA_VERSION)
    assert any("frontend references unregistered GET APIs" in issue for issue in issues)
    assert any("registered dashboard views have no frontend consumer: /api/economy" in issue for issue in issues)
