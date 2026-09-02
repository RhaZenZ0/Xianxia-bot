#!/usr/bin/env python3
"""Generate and verify RELEASE_MANIFEST.sha256 for a Xianxia RP release tree.

The manifest used to be produced by hand, which meant it drifted the moment any
file was edited afterwards: an audit of the shipped v0.19 archive found 34 wrong
hashes, 14 listed-but-missing files and 18 packaged-but-unlisted ones. A manifest
in that state is worse than none at all - it looks like an integrity check while
silently passing over most of the release - so generation is scripted here, and
the updater verifies it before installing anything.

Usage:
    python3 scripts/release_manifest.py --write    # regenerate the manifest
    python3 scripts/release_manifest.py --verify   # check the tree against it
    python3 scripts/release_manifest.py --verify --root /path/to/tree

--verify exits non-zero and prints every mismatch, missing file and unlisted
file. Both modes cover exactly the same file set, so a regenerate-then-verify
round trip is always clean; if it is not, that is a bug in this script.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

MANIFEST_NAME = "RELEASE_MANIFEST.sha256"

# Directories never shipped, or not content-stable across machines.
EXCLUDED_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "data", "updates", "update_backups", ".venv", "venv",
    ".idea", ".vscode",
})

# Files excluded by exact name anywhere in the tree.
EXCLUDED_NAMES = frozenset({
    MANIFEST_NAME,  # cannot hash itself
    ".env",         # per-deployment secrets, never packaged
    ".DS_Store",
})

# Suffixes excluded anywhere in the tree.
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".log")


def iter_release_files(root: Path):
    """Every file the manifest covers, in deterministic sorted order."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts[:-1]):
            continue
        if relative.name in EXCLUDED_NAMES or relative.name.endswith(EXCLUDED_SUFFIXES):
            continue
        yield relative, path


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> str:
    # Two spaces between hash and path is the sha256sum -c format, so the file
    # stays checkable with `sha256sum -c` on any Linux host as well.
    return "".join(
        f"{file_digest(path)}  {relative.as_posix()}\n" for relative, path in iter_release_files(root)
    )


def read_manifest(root: Path) -> dict[str, str]:
    manifest_path = root / MANIFEST_NAME
    entries: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition("  ")
        if not digest or not name:
            raise ValueError(f"malformed manifest line: {line!r}")
        entries[name] = digest
    return entries


def verify(root: Path) -> int:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"ERROR: {MANIFEST_NAME} not found in {root}", file=sys.stderr)
        return 2
    expected = read_manifest(root)
    actual = {relative.as_posix(): path for relative, path in iter_release_files(root)}

    missing = sorted(set(expected) - set(actual))
    unlisted = sorted(set(actual) - set(expected))
    changed = sorted(
        name for name in sorted(set(expected) & set(actual)) if file_digest(actual[name]) != expected[name]
    )

    for name in missing:
        print(f"MISSING   {name}", file=sys.stderr)
    for name in unlisted:
        print(f"UNLISTED  {name}", file=sys.stderr)
    for name in changed:
        print(f"CHANGED   {name}", file=sys.stderr)

    if missing or unlisted or changed:
        print(
            f"\n{MANIFEST_NAME} does not match this tree: "
            f"{len(changed)} changed, {len(missing)} missing, {len(unlisted)} unlisted "
            f"(of {len(expected)} listed).",
            file=sys.stderr,
        )
        return 1
    print(f"{MANIFEST_NAME} verified: {len(expected)} files match.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the manifest")
    mode.add_argument("--verify", action="store_true", help="check the tree against the manifest")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2
    if args.write:
        content = build_manifest(root)
        (root / MANIFEST_NAME).write_text(content, encoding="utf-8")
        print(f"Wrote {MANIFEST_NAME}: {content.count(chr(10))} files.")
        return 0
    return verify(root)


if __name__ == "__main__":
    raise SystemExit(main())
