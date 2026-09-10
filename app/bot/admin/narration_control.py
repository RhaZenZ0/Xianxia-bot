"""Narration chain control over the dashboard -> bot control channel.

The GM dashboard runs as its own process, so it cannot reach into the running
router's chains; and the router is wired in the bot process from `.env` at
import time, so before this the only way to change a narration route was to
edit `.env` and restart the container. That is a poor fit for the thing it
controls: OpenRouter's free catalogue moves without notice, and two shipped
defaults (`z-ai/glm-5.2:free`, `minimax/minimax-m3:free`) had already died in
place by the time anyone noticed.

Three actions, deliberately split by who owns what:

- `narration.status`   read the live router - chains, per-route probe verdicts,
                       budget - so a GM can see WHY a route is retired before
                       replacing it.
- `narration.catalogue` the free half of OpenRouter's live catalogue, fetched
                       by this process because this is where the key lives.
- `narration.apply`    re-read the engine-persisted chain and apply it to the
                       running router.

The WRITE is not here. The dashboard writes the chain through the engine
(`admin.narration.set_chain`), which is what puts it in `world_state` and in
`admin_audit_log`; this module only applies what the engine already accepted.
That ordering means a GM's choice survives a restart even if this poke fails,
and that nothing can change the live chain without an audit row.
"""
from __future__ import annotations

from typing import Any

from ...ai.ai_router import CREDITS_SWITCH_SLOT, NARRATION_SLOTS
from ..runtime import DB, log
from ..services import AI_ROUTER

NARRATION_CONTROL_ACTIONS = (
    "narration.status",
    "narration.catalogue",
    "narration.apply",
)


def owns_narration_action(action: str) -> bool:
    """True when this module, rather than the Discord one, handles the action."""
    return str(action or "").strip().lower() in NARRATION_CONTROL_ACTIONS


async def apply_stored_chain() -> dict[str, Any]:
    """Apply the engine-persisted chain to the running router.

    Called at startup and whenever the dashboard pokes after a write. Slots the
    GM has never set are left alone, so `.env` remains the baseline rather than
    being overwritten with blanks. A stored slug that no longer validates - the
    free guard was turned on after it was chosen, say - is reported rather than
    raised: narration must survive a bad stored setting the same way it
    survives a dead route.
    """
    stored = await DB.get_narration_chain()
    slots = {name: stored[name] for name in (*NARRATION_SLOTS, CREDITS_SWITCH_SLOT) if name in stored}
    if not slots:
        return {"applied": False, "reason": "no stored chain", "slots": AI_ROUTER.slots_snapshot()}
    try:
        result = AI_ROUTER.set_slots(slots)
    except ValueError as exc:
        log.warning("AI_CHAIN_STORED_REJECTED: %s", exc)
        return {
            "applied": False,
            "reason": str(exc)[:300],
            "slots": AI_ROUTER.slots_snapshot(),
        }
    return {"applied": True, **result}


async def dashboard_narration_control(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(action or "").strip().lower()

    if action == "narration.status":
        return {
            "ok": True,
            "action": action,
            "result": {
                "slots": AI_ROUTER.slots_snapshot(),
                "status": AI_ROUTER.health_snapshot(),
            },
        }

    if action == "narration.catalogue":
        catalogue = await AI_ROUTER.fetch_free_catalogue(
            force=bool(payload.get("refresh"))
        )
        return {"ok": True, "action": action, "result": catalogue}

    if action == "narration.apply":
        result = await apply_stored_chain()
        # Not an exception when it did not apply: "the engine has no chain
        # stored" and "the stored chain no longer validates" are both answers
        # the GM needs to see, not control-channel failures.
        return {"ok": bool(result.get("applied")), "action": action, "result": result}

    raise ValueError(f"Unsupported narration control action: {action}")
