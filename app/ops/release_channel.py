"""The release channel: what "a newer release exists" means, in one place.

Releases are GitHub Releases on the project repository (v0.20.4,
docs/V020_RELEASE_NOTES.md). Each carries `xianxia_rp_v<version>.zip` and a
`.sha256` sidecar, built by the release job in .github/workflows/ci.yml from
the tag. Two
channels:

  stable - full releases (tag `vX.Y.Z`);
  beta   - pre-releases too (tag `vX.Y.Z-beta.N`, `-rc.N`; VERSION inside
           the archive is still `X.Y.Z`, so the on-disk updater compares
           plain numbers).

This module is pure: it parses the API's JSON and decides. Two consumers
share it - the bot's update-check worker (announces in the log channel) and
the dashboard - while `update.sh --check/--fetch` re-implements the same
selection in shell because the updater must work with nothing but wget.
The parity between the two is pinned by tests/python/unit/test_release_channel.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_REPOSITORY = "RhaZenZ0/Xianxia-bot"
CHANNELS = ("stable", "beta")
ASSET_PATTERN = re.compile(r"^xianxia_rp_v(\d+\.\d+\.\d+)\.zip$")
TAG_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?$")


def parse_version(text: str) -> tuple[int, int, int]:
    """'0.20.4', 'v0.20.4' and 'v0.21.0-beta.1' all parse; the suffix is dropped."""
    match = TAG_PATTERN.match(str(text).strip())
    if not match:
        raise ValueError(f"not a release version: {text!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_prerelease_tag(tag: str) -> bool:
    match = TAG_PATTERN.match(str(tag).strip())
    return bool(match and match.group(4))


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple[int, int, int]
    prerelease: bool
    archive_url: str
    checksum_url: str | None
    page_url: str
    notes: str = ""

    @property
    def version_text(self) -> str:
        return ".".join(str(part) for part in self.version)

    @property
    def channel(self) -> str:
        return "beta" if self.prerelease else "stable"


def api_url(repository: str = DEFAULT_REPOSITORY, per_page: int = 20) -> str:
    return f"https://api.github.com/repos/{repository}/releases?per_page={int(per_page)}"


def parse_releases(payload: str | bytes | list[dict[str, Any]]) -> list[Release]:
    """Turn the GitHub releases listing into Release records, newest first.

    Drafts are skipped; so is any release without the archive asset (a tag
    pushed before the workflow finished, or a hand-made release). A
    pre-release is one GitHub marks as such OR whose tag carries a suffix -
    either signal is enough, so a mis-clicked checkbox cannot push a beta
    to the stable channel.
    """
    entries = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    releases: list[Release] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("draft"):
            continue
        tag = str(entry.get("tag_name") or "")
        try:
            version = parse_version(tag)
        except ValueError:
            continue
        archive_url = checksum_url = None
        for asset in entry.get("assets") or ():
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if ASSET_PATTERN.match(name):
                archive_url = url
            elif name.endswith(".zip.sha256"):
                checksum_url = url
        if not archive_url:
            continue
        releases.append(Release(
            tag=tag,
            version=version,
            prerelease=bool(entry.get("prerelease")) or is_prerelease_tag(tag),
            archive_url=archive_url,
            checksum_url=checksum_url,
            page_url=str(entry.get("html_url") or ""),
            notes=str(entry.get("body") or ""),
        ))
    releases.sort(key=lambda r: (r.version, not r.prerelease), reverse=True)
    return releases


def newest_for_channel(releases: Iterable[Release], channel: str) -> Release | None:
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; expected one of {CHANNELS}")
    candidates = [r for r in releases if channel == "beta" or not r.prerelease]
    return max(candidates, key=lambda r: (r.version, not r.prerelease), default=None)


def newer_than_installed(release: Release | None, installed: str) -> Release | None:
    """The release if it is strictly newer than the installed version, else None."""
    if release is None:
        return None
    return release if release.version > parse_version(installed) else None


def announcement(release: Release, installed: str, channel: str) -> str:
    """The Discord log-channel message, bounded so post_server_log never truncates it."""
    first_line = next((line.strip() for line in release.notes.splitlines() if line.strip() and not line.startswith("#")), "")
    summary = (first_line[:280] + "…") if len(first_line) > 280 else first_line
    lines = [
        f"**{release.version_text}** is available on the **{channel}** channel (installed: {installed}).",
        f"Release page: {release.page_url}" if release.page_url else "",
        "Install on the NAS: `./update.sh --fetch` then `./update.sh --install` (or `./update.sh --upgrade`).",
    ]
    if summary:
        lines.insert(1, summary)
    return "\n".join(line for line in lines if line)
