#!/usr/bin/env python3
"""Run the whole release playtest at once (v1.14.1).

The two harnesses used to be run one after the other - about eight minutes for
the engine half and twenty-five for the Discord half, twenty of them in the
leaf sweep. Each run binds ports of its own now, so this starts them together:
the engine half, and the Discord half split into parts that each sweep a share
of the hubs (`playtest_discord.py --shard I/N`).

    python3 scripts/playtest_all.py                 # engine + Discord in 3 parts
    python3 scripts/playtest_all.py --shards 2      # fewer parts on a smaller machine
    python3 scripts/playtest_all.py --only discord  # or --only engine
    python3 scripts/playtest_all.py --keep          # keep the logs even when green

The engine is built once and handed to every part. Each part writes its own
log; a progress line is printed every thirty seconds, and at the end each
part's count, every FAIL line, and the **union check**: the parts' `SHARD`
lines must name every registered hub exactly once, so splitting the sweep can
never quietly drop a hub. Any failure, a part that exits non-zero, or a union
that does not close exits non-zero, and the logs are kept.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playtest_common import ROOT, build_engine  # noqa: E402 - beside this file

SHARD_LINE = re.compile(r"^PASS\s+SHARD (\d+)/(\d+) hubs: (.*) \(of (\d+)\)\s*$", re.M)
STEPS_LINE = re.compile(r"^(\d+) steps, (\d+) failed\s*$", re.M)
PROGRESS_SECONDS = 30


def union_problems(logs: dict[str, str], parts: int) -> list[str]:
    """What is wrong with the parts' hub lists, or nothing.

    Every part 1..N must have reported, they must agree on how many hubs there
    are, no hub may be swept twice, and together they must name that many."""
    seen: dict[int, list[str]] = {}
    totals: set[int] = set()
    for text in logs.values():
        for index, total_parts, hubs, total in SHARD_LINE.findall(text):
            if int(total_parts) != parts:
                return [f"a part reported shard {index}/{total_parts} in a run of {parts}"]
            seen[int(index)] = [h for h in hubs.split(",") if h]
            totals.add(int(total))
    problems = [f"part {i}/{parts} never said which hubs it swept" for i in range(1, parts + 1) if i not in seen]
    if len(totals) > 1:
        problems.append(f"the parts disagree on how many hubs there are: {sorted(totals)}")
    every = [hub for hubs in seen.values() for hub in hubs]
    twice = sorted({hub for hub in every if every.count(hub) > 1})
    if twice:
        problems.append(f"swept by more than one part: {twice}")
    if totals and not problems and len(set(every)) != next(iter(totals)):
        problems.append(f"the parts swept {len(set(every))} hubs of {next(iter(totals))}")
    return problems


def counts(text: str) -> tuple[int, int] | None:
    found = STEPS_LINE.findall(text)
    return (int(found[-1][0]), int(found[-1][1])) if found else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shards", type=int, default=3, help="how many parts the Discord sweep is split into (default 3)")
    parser.add_argument("--only", choices=("engine", "discord"), help="run one half only")
    parser.add_argument("--keep", action="store_true", help="keep the run directory and logs even when green")
    args = parser.parse_args()
    if args.shards < 1:
        raise SystemExit("--shards is at least 1")

    run_dir = Path(tempfile.mkdtemp(prefix="xianxia-playtest-all-"))
    print(f"run directory: {run_dir}", flush=True)
    binary = build_engine(run_dir / "xianxia-engine")
    env = {**os.environ, "PLAYTEST_ENGINE_BINARY": str(binary)}

    parts: dict[str, list[str]] = {}
    if args.only != "discord":
        parts["engine"] = [sys.executable, "scripts/playtest_engine.py", "--launch"]
    if args.only != "engine":
        for i in range(1, args.shards + 1):
            parts[f"discord {i}/{args.shards}"] = [sys.executable, "scripts/playtest_discord.py", "--launch",
                                                    "--shard", f"{i}/{args.shards}"]

    started = time.monotonic()
    procs: dict[str, tuple[subprocess.Popen, Path]] = {}
    for name, command in parts.items():
        log = run_dir / (name.replace(" ", "-").replace("/", "-of-") + ".log")
        procs[name] = (subprocess.Popen(command, cwd=ROOT, env=env, stdout=log.open("w"), stderr=subprocess.STDOUT), log)

    def text_of(log: Path) -> str:
        return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""

    while any(proc.poll() is None for proc, _ in procs.values()):
        time.sleep(PROGRESS_SECONDS)
        line = []
        for name, (proc, log) in procs.items():
            text = text_of(log)
            steps = len(re.findall(r"^(?:PASS|FAIL|SKIP) ", text, re.M))
            failed = len(re.findall(r"^FAIL ", text, re.M))
            line.append(f"{name}: {steps} steps{f', {failed} failed' if failed else ''}{'' if proc.poll() is None else ' (done)'}")
        print(f"[{int(time.monotonic() - started)}s] " + " · ".join(line), flush=True)

    logs = {name: text_of(log) for name, (_, log) in procs.items()}
    ok = True
    print(f"\nfinished in {int(time.monotonic() - started)}s")
    for name, (proc, _) in procs.items():
        tally = counts(logs[name])
        summary = f"{tally[0]} steps, {tally[1]} failed" if tally else "no summary line"
        bad = proc.returncode != 0 or tally is None or tally[1] > 0
        ok = ok and not bad
        print(f"  {'FAIL' if bad else 'PASS'}  {name}: {summary} (exit {proc.returncode})")
    for name, text in logs.items():
        for line in re.findall(r"^FAIL .*$", text, re.M):
            print(f"  [{name}] {line}")
    if args.only != "engine":
        problems = union_problems({k: v for k, v in logs.items() if k.startswith("discord")}, args.shards)
        for problem in problems:
            print(f"  FAIL  union: {problem}")
        if not problems:
            print(f"  PASS  union: the {args.shards} parts swept every hub exactly once")
        ok = ok and not problems
    if ok and not args.keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    else:
        print(f"logs kept in {run_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
