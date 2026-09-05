"""Every Discord interaction handler that performs an authoritative Go
mutation must acknowledge the interaction (defer/send_message/send_modal/
edit_message) before that mutation, not after.

Why this matters: Discord invalidates an interaction token if it receives no
initial response within ~3 seconds. A handler that calls
ENGINE.authoritative_action(...) first and only replies afterwards can commit
a real gameplay mutation, then fail to ever tell the player it happened
(interaction expired) - the player retries, and a second legitimate mutation
occurs from what looks like a single click. v0.19.24's own security/latency
work never touched this; a dedicated review of main.py after that release
found 84 handlers with exactly this shape (cultivate, breakthrough, travel,
check, and nearly everything else that touches the Go engine). All of them
were fixed by deferring as literally the first statement in the handler body
and routing their own replies through interaction.followup.send() (or a
smart is_done()-aware responder, for a handler like require_character() that
is called by both already-deferred and not-yet-deferred callers).

This test is a permanent static guard against the same shape recurring: for
every function under app/bot that calls ENGINE.authoritative_action(, the
first response/defer/send_modal/edit_message call in that function's OWN
source (not a callee's) must appear at or before the first authoritative_action
call, by source line. A function with no interaction-response capability at
all (it takes a bare user_id, not `interaction` - e.g. a background
auto-simulation helper called from many contexts) is exempt, since it cannot
ack anything itself; its callers are responsible for their own ack timing,
and are each checked independently by this same scan.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.support import PROJECT_ROOT

BOT_DIR = PROJECT_ROOT / "app" / "bot"

ACK_MARKERS = (
    "interaction.response.send_message(",
    "interaction.response.defer(",
    "interaction.response.send_modal(",
    "interaction.response.edit_message(",
)
MUTATE_MARKER = "authoritative_action("

# Helpers that call authoritative_action but take a bare user_id rather than
# an `interaction`, so they have no ability to ack anything themselves. Every
# caller of these is independently scanned and must ack before calling them
# if it matters for that caller's own timing.
NOT_AN_INTERACTION_ENTRYPOINT = {
    "_current_birth_family",
    "settle_seclusion_for_user",
}


def _late_ack_functions() -> list[tuple[Path, str, int]]:
    offenders: list[tuple[Path, str, int]] = []
    for path in sorted(BOT_DIR.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        src_lines = src.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name in NOT_AN_INTERACTION_ENTRYPOINT:
                continue
            own_source = ast.get_source_segment(src, node) or ""
            if MUTATE_MARKER not in own_source:
                continue

            skip_ranges = [
                (child.lineno, getattr(child, "end_lineno", child.lineno))
                for child in ast.walk(node)
                if child is not node
                and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
            ]

            def in_nested_def(lineno: int) -> bool:
                return any(lo <= lineno <= hi for lo, hi in skip_ranges)

            first_mutate_line = None
            first_ack_line = None
            for lineno in range(node.lineno, node.end_lineno + 1):
                if in_nested_def(lineno):
                    continue
                text = src_lines[lineno - 1]
                if first_mutate_line is None and MUTATE_MARKER in text:
                    first_mutate_line = lineno
                if first_ack_line is None and any(marker in text for marker in ACK_MARKERS):
                    first_ack_line = lineno

            if first_mutate_line is None:
                continue  # only a nested def (a separate entrypoint) had it
            if first_ack_line is None or first_ack_line > first_mutate_line:
                offenders.append((path.relative_to(PROJECT_ROOT), node.name, first_mutate_line))
    return offenders


def test_no_interaction_handler_mutates_before_acking():
    offenders = _late_ack_functions()
    assert offenders == [], (
        "These handlers call ENGINE.authoritative_action(...) before acking "
        "the interaction (send_message/defer/send_modal/edit_message), risking "
        "a duplicate mutation if the interaction token expires before the "
        "first reply goes out: "
        + ", ".join(f"{path}:{name}() (mutate at line {line})" for path, name, line in offenders)
    )


def test_require_character_acks_before_its_own_slow_lifecycle_call():
    # require_character() is the one shared guard-clause helper nearly every
    # handler calls first, and it can itself trigger a slow authoritative Go
    # call (old-age true-death) before returning. It must defer defensively
    # before that call so it is safe to call from both already-deferred
    # handlers (is_done() short-circuits) and not-yet-deferred ones.
    src = (PROJECT_ROOT / "app" / "bot" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "require_character"
    )
    body = ast.get_source_segment(src, node) or ""
    mutate_idx = body.index("authoritative_action(")
    defer_idx = body.index("interaction.response.defer(")
    assert defer_idx < mutate_idx, (
        "require_character's own defer (before lifecycle.true_death) must "
        "precede the authoritative_action call, not follow it"
    )
    # And its response calls must be ack-state-aware (respond()/is_done()),
    # not a bare unconditional send_message that would raise
    # InteractionResponded when called from an already-deferred handler.
    assert "interaction.response.send_message(" not in body
