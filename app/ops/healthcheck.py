from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def main() -> int:
    host = os.getenv("HEALTHCHECK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.getenv("HEALTH_PORT", "8082").strip() or "8082"
    url = os.getenv("HEALTHCHECK_URL", f"http://{host}:{port}/healthz").strip()
    try:
        with urllib.request.urlopen(url, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return 0 if response.status == 200 and bool(payload.get("ready")) else 1
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
