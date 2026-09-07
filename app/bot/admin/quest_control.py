"""Quest authoring over the dashboard -> bot control channel (v0.24.0).

The Forge has always lived in the bot process, because drafting a quest is an
OpenRouter call and `QUEST_FORGE` is wired there with the world catalog and the
narration budget. That made `/admin world questforge` the only way to author
one, and left the dashboard - which is where a GM does everything else - with a
read-only table.

This is the other half of the existing control channel. `dashboard_discord_control`
carries Discord layout work and says in its own docstring that it never performs
game mechanics; that is still true and this is deliberately not routed through
it. Forging writes a `quest_definitions` row at `status='draft'`, which no
player can see or accept until it is approved, so it is content authoring, not a
gameplay mutation - the same class of write as the commission pool seed.
"""
from __future__ import annotations

from typing import Any

from ...ai.quest_forge import store_draft
from ..runtime import DB, log
from ..services import QUEST_FORGE

# A story shorter than this is not a brief, it is a title. The Forge will
# happily draft from three words and produce something nobody wants.
MIN_STORY_CHARACTERS = 12
MAX_STORY_CHARACTERS = 2000

QUEST_CONTROL_ACTIONS = ("quest.forge",)


def owns(action: str) -> bool:
    """True when this module, rather than the Discord one, handles the action."""
    return str(action or "").strip().lower() in QUEST_CONTROL_ACTIONS


async def dashboard_quest_control(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action != "quest.forge":
        raise ValueError(f"Unsupported quest control action: {action}")

    story = str(payload.get("story") or "").strip()
    if len(story) < MIN_STORY_CHARACTERS:
        raise ValueError(
            f"Give the Forge a little more story to work with "
            f"(at least {MIN_STORY_CHARACTERS} characters; a sentence or two)."
        )
    if len(story) > MAX_STORY_CHARACTERS:
        raise ValueError(f"That story is longer than the Forge accepts ({MAX_STORY_CHARACTERS} characters).")

    admin_user_id = int(payload.get("admin_user_id") or 0)
    result = await QUEST_FORGE.draft(story, source_key=f"dashboard:{admin_user_id}")
    if result.definition is None:
        # Not an exception: the GM asked for a draft and the answer is "the
        # Forge could not make one from this", which is information, not a
        # failure of the control channel.
        return {
            "ok": False,
            "action": action,
            "result": {"errors": list(result.errors[:8]), "model": result.model},
        }

    row = await store_draft(
        DB, result, story=story, origin="gm_dashboard", created_by=admin_user_id,
    )
    # Rule 6: an Admin Console action writes to admin_audit_log. The engine
    # audits its own actions; this one is Python-side, so it says so here.
    try:
        await DB.log_admin_action(
            admin_user_id=admin_user_id,
            action="quest.forge",
            target=str(row.get("quest_key") or ""),
            before=None,
            after={"model": result.model, "procedural": result.procedural, "origin": "gm_dashboard"},
            reason="GM dashboard",
        )
    except Exception:
        log.exception("Could not write the audit row for a dashboard quest forge")

    return {
        "ok": True,
        "action": action,
        "result": {
            "quest_key": row.get("quest_key"),
            "title": row.get("title"),
            "status": row.get("status"),
            "objectives": list(row.get("objectives") or []),
            "rewards": dict(row.get("rewards") or {}),
            "model": result.model,
            # True when every model attempt failed or kept producing an invalid
            # draft. The draft is still real and still reviewable; the GM should
            # simply know no model wrote it.
            "procedural": bool(result.procedural),
            "errors": list(result.errors[:8]),
        },
    }
