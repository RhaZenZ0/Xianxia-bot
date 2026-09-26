"""The changelog as data: what an entry is, how versions order, where a page lives.

Two readers turn `VERSIONS.md` into something a person sees. The bot posts an
entry's opening sentence into `#updates` (`app/bot/admin/release_notes.py`),
and `scripts/build_release_pages.py` publishes every entry as a page on the
repository's GitHub Pages site. The post links the page, so the two must agree
on three things - which lines start an entry, what each entry is called, and
which path its page is written to - and each is stated here once (v1.8.2).

Pure: it reads text it is handed and imports nothing above `rules`.
"""
from __future__ import annotations

import re

# A line opening with a bold version stamp starts an entry, optionally followed
# by an `(rc.N)` for the release candidates of 1.0.0. The whole file is read,
# not only the Changelog section: that is how the bot has always read it.
ENTRY = re.compile(r"^\*\*(?P<version>\d+\.\d+(?:\.\d+)?)\*\*\s*(?:\((?P<rc>rc\.\d+)\))?", re.M)


def release_tag(version: str) -> tuple[str, str]:
    """`1.0.0-rc.59` -> `("1.0.0", "rc.59")`; `1.0.0` -> `("1.0.0", "")`."""
    base, _, suffix = version.partition("-")
    return base, suffix


def version_key(version: str) -> tuple[int, ...]:
    """Sortable, and `1.0.10` is newer than `1.0.9` (v1.0.11).

    Versions sort as integers, never as text - the rule `playtest_checklist.py`
    learned in v1.0.1, where `v1.0.10` sorted before `v1.0.9` and the generator
    inherited the wrong checklist's ticks.

    A release candidate sorts **below** the release it is a candidate for, so
    `1.0.0-rc.59 < 1.0.0 < 1.0.1`. That is what the trailing sentinel is: an
    entry with no rc suffix is the final one of its base, so it takes a number
    no candidate can reach.
    """
    base, suffix = release_tag(version)
    parts = tuple(int(piece) for piece in re.findall(r"\d+", base))
    rc = re.search(r"rc\.(\d+)", suffix)
    return parts + (int(rc.group(1)) if rc else 1 << 30,)


def changelog_entries(text: str) -> list[tuple[str, str]]:
    """Every entry in `text` as `(label, entry)`, in the order the file has them.

    `label` is spelled the way the entry stamps itself - `1.0.0-rc.59` for a
    candidate, `1.0.1` for a release. An entry runs from its header to the next
    one, several paragraphs, which is how they are written and how they read.
    An empty entry is left out.
    """
    matches = list(ENTRY.finditer(text))
    out: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        rc = match.group("rc") or ""
        label = f"{match.group('version')}-{rc}" if rc else match.group("version")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entry = text[match.start():end].strip()
        if entry:
            out.append((label, entry))
    return out


def page_path(label: str) -> str:
    """The path of one release's page on the site, relative to the site root."""
    return f"v{label}/"


def pages_url(repository: str, label: str | None = None) -> str:
    """The GitHub Pages address of the site, or of one release's page on it.

    A project site lives at `https://<owner>.github.io/<repo>/`; GitHub
    lowercases the owner in the host name and keeps the repository name as it
    is spelled in the path.
    """
    owner, _, repo = repository.strip().strip("/").partition("/")
    base = f"https://{owner.lower()}.github.io/{repo}/"
    return base + page_path(label) if label else base
