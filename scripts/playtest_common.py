"""What the two playtest harnesses share: the report, the step runner, and a
scratch engine to run against.

`scripts/playtest_engine.py` drives the roadmap's loops through the Go engine's
HTTP API; `scripts/playtest_discord.py` drives the bot itself through a
simulated Discord. Both print one line per step - PASS, FAIL or SKIP with the
reason - and exit non-zero if anything failed, and both can build, start and
bootstrap a scratch engine in a temporary directory. Neither is ever pointed at
the production database.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENGINE_ADDR = "127.0.0.1:18089"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, step: str, note: str = "") -> None:
        self.rows.append((status, step, note))
        print(f"{status:<5} {step}" + (f" — {note}" if note else ""), flush=True)

    @property
    def failed(self) -> int:
        return sum(1 for status, _, _ in self.rows if status == "FAIL")


async def step(report: Report, name: str, coro, *, expect_error: str | None = None) -> Any:
    """Run one step. With expect_error, the step passes only if the engine
    refuses with a message containing that text - the refusal is the feature."""
    from app.ops.game_engine import GameEngineError

    try:
        result = await coro
    except GameEngineError as exc:
        if expect_error and expect_error in str(exc):
            report.add("PASS", name, f"refused as designed: {exc}")
            return None
        report.add("FAIL", name, str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 - a playtest reports, it does not crash
        report.add("FAIL", name, f"{type(exc).__name__}: {exc}")
        return None
    if expect_error:
        report.add("FAIL", name, f"expected a refusal mentioning {expect_error!r}, got {str(result)[:160]}")
        return None
    report.add("PASS", name)
    return result


def launch_engine(tmp: Path) -> tuple[subprocess.Popen, str, str]:
    binary = tmp / "xianxia-engine"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/xianxia-core"], cwd=ROOT / "go_core", check=True,
                   env={**os.environ, "CGO_ENABLED": "1"})
    token = "playtest-engine-token-" + str(int(time.time()))
    env = {**os.environ, "ENGINE_ADDR": ENGINE_ADDR, "DATABASE_PATH": str(tmp / "playtest.sqlite3"),
           "WORLD_DATA_PATH": str(ROOT / "content" / "world.json"), "ENGINE_AUTH_TOKEN": token}
    proc = subprocess.Popen([str(binary)], env=env, stdout=(tmp / "engine.log").open("w"), stderr=subprocess.STDOUT)
    url = "http://" + ENGINE_ADDR
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + "/readyz", timeout=1) as response:
                if response.status == 200:
                    break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    else:
        proc.kill()
        raise SystemExit("engine did not become ready; see " + str(tmp / "engine.log"))
    return proc, url, token


def bootstrap(url: str, token: str, db_path: str) -> None:
    env = {**os.environ, "GAME_ENGINE_URL": url, "ENGINE_AUTH_TOKEN": token, "DATABASE_PATH": db_path,
           "DISCORD_TOKEN": os.environ.get("DISCORD_TOKEN", "playtest"), "GUILD_ID": os.environ.get("GUILD_ID", "1"),
           "NARRATOR_PROVIDER": os.environ.get("NARRATOR_PROVIDER", "procedural")}
    subprocess.run([sys.executable, "-m", "app.database.bootstrap"], cwd=ROOT, check=True, env=env)


def stop_engine(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
