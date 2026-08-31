#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dashboard_contract import dashboard_implementation_issues


def schema_version() -> int:
    source = (ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
    match = re.search(r"^SCHEMA_VERSION\s*=\s*(\d+)\s*$", source, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find SCHEMA_VERSION in app/database/core.py")
    return int(match.group(1))


def main() -> int:
    issues = dashboard_implementation_issues(ROOT, schema_version=schema_version())
    if issues:
        print("Dashboard implementation check: FAIL")
        for issue in issues:
            print(f" - {issue}")
        return 1
    print("Dashboard implementation check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
