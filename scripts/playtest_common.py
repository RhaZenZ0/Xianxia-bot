"""What the two playtest harnesses share: the report, the step runner, and a
scratch engine to run against.

`scripts/playtest_engine.py` drives the roadmap's loops through the Go engine's
HTTP API; `scripts/playtest_discord.py` drives the bot itself through a
simulated Discord. Both print one line per step - PASS, FAIL or SKIP with the
reason - and exit non-zero if anything failed, and both can build, start and
bootstrap a scratch engine in a temporary directory. Neither is ever pointed at
the production database.

Every run binds ports of its own (v1.14.1). Both harnesses used to bind
`127.0.0.1:18089`, so they could only be run one after the other, and the
Discord half spent twenty of its twenty-five minutes on a sweep that could be
split. `scripts/playtest_all.py` runs the engine half and the Discord half in
parts at once, and `shard_hubs` is the one statement of how the sweep splits.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



def free_port() -> int:
    """A port nothing is bound to right now, for one run's engine or bot."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def shard_hubs(hubs: list[tuple[str, int]], parts: int) -> list[list[str]]:
    """Split the sweep's hubs into `parts` lists of roughly equal weight.

    `hubs` is (name, leaf count). Heaviest first, each to the lightest part so
    far, ties broken by name, so the split is the same on every run and every
    hub lands in exactly one part. The leaf count is the weight because the
    sweep's time is its presses.
    """
    if parts < 1:
        raise ValueError("a sweep is split into at least one part")
    out: list[list[str]] = [[] for _ in range(parts)]
    load = [0] * parts
    for name, weight in sorted(hubs, key=lambda h: (-int(h[1]), h[0])):
        lightest = min(range(parts), key=lambda i: (load[i], i))
        out[lightest].append(name)
        load[lightest] += int(weight)
    return out


def parse_shard(text: str) -> tuple[int, int]:
    """`I/N` -> (I, N), 1-based, refusing anything outside 1..N."""
    index, _, total = str(text).partition("/")
    i, n = int(index), int(total)
    if not 1 <= i <= n:
        raise ValueError(f"shard {text!r} is not I/N with 1 <= I <= N")
    return i, n


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


def build_engine(binary: Path) -> Path:
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/xianxia-core"], cwd=ROOT / "go_core", check=True,
                   env={**os.environ, "CGO_ENABLED": "1"})
    return binary


def launch_engine(tmp: Path) -> tuple[subprocess.Popen, str, str]:
    # `PLAYTEST_ENGINE_BINARY` is an engine somebody already built - the runner
    # builds once for every part rather than once per part.
    prebuilt = os.environ.get("PLAYTEST_ENGINE_BINARY", "").strip()
    binary = Path(prebuilt) if prebuilt else build_engine(tmp / "xianxia-engine")
    token = "playtest-engine-token-" + str(int(time.time()))
    addr = f"127.0.0.1:{free_port()}"
    env = {**os.environ, "ENGINE_ADDR": addr, "DATABASE_PATH": str(tmp / "playtest.sqlite3"),
           "WORLD_DATA_PATH": str(ROOT / "content" / "world.json"), "ENGINE_AUTH_TOKEN": token}
    proc = subprocess.Popen([str(binary)], env=env, stdout=(tmp / "engine.log").open("w"), stderr=subprocess.STDOUT)
    url = "http://" + addr
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
