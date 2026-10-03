"""What tempers the body besides a body session (v1.20.0).

The engine decides it: a successful hunt, a successful dig and a battle won
each add a share of one body session (`temperBodyByUseTx`), and the result
carries `body_tempered`. This module only says so, and says nothing when the
engine added nothing - a full stage, or a share the content does not author.
"""
from __future__ import annotations

from collections.abc import Mapping


def tempered_line(result: Mapping, deed: str) -> str:
    """The reply line for a deed that tempered the body, or "" when it did not."""
    try:
        gain = int(result.get("body_tempered") or 0)
    except (TypeError, ValueError):
        return ""
    if gain <= 0:
        return ""
    return f"💪 The {deed} tempers your body: **+{gain}** body essence."
