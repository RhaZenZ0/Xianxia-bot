from __future__ import annotations

from pathlib import Path

RELEASE_VERSION = "1.0.0"
__version__ = RELEASE_VERSION


def _installed_version() -> str:
    """`RELEASE_VERSION`, refined by the tag the release job stamped, if there is one.

    `VERSION` carries the numeric release and never the `-rc.N` suffix, so on
    its own it cannot tell one rc of a version from another. The release job
    writes the tag it built into `RELEASE_TAG` at the tree root; when that file
    is there and agrees with `VERSION`, it is the more precise answer and the
    update check uses it. A development checkout has no such file, and neither
    does any release built before v1.0.0-rc.10.
    """
    try:
        text = (Path(__file__).resolve().parent.parent / "RELEASE_TAG").read_text(encoding="utf-8")
    except OSError:
        return RELEASE_VERSION
    tag = text.strip().lstrip("vV").strip()
    if tag == RELEASE_VERSION or tag.startswith(f"{RELEASE_VERSION}-"):
        return tag
    return RELEASE_VERSION


INSTALLED_VERSION = _installed_version()
