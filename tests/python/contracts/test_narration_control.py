"""The narration panel's ownership boundary.

The chain is chosen in a browser, stored by the engine, and applied by the bot.
Each of those belongs to a different process, and the ORDER matters: the engine
write is what makes the choice durable and audited, so it must happen before -
and independently of - the poke that makes it live.
"""
from __future__ import annotations

import asyncio
import unittest

from tests.support import install_aiosqlite_shim, install_openai_shim

install_aiosqlite_shim()
install_openai_shim()

from app.dashboard.server import NarrationDashboardController


class _Control:
    """Stands in for the bot process behind the control channel."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def run_readonly(self, action, payload):
        self.calls.append((action, dict(payload)))
        if self.fail:
            raise RuntimeError("the bot is not answering")
        return {"ok": True, "action": action, "result": {"applied": True}}


class _Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict]] = []

    async def action(self, name, actor_id, payload):
        self.calls.append((name, actor_id, dict(payload)))
        return {"slots": payload["slots"]}


def _controller(*, enabled=True, fail_control=False):
    control = _Control(fail=fail_control)
    controller = NarrationDashboardController(control, "http://engine", enabled, actor_id=7)
    controller.engine = _Engine()
    return controller, control


class NarrationWriteOrderingTests(unittest.TestCase):
    def test_the_choice_is_stored_by_the_engine_before_the_bot_is_poked(self):
        controller, control = _controller()
        result = asyncio.run(
            controller.run("narration.set_chain", {"slots": {"routine_model": "v/m:free"}})
        )
        self.assertEqual(controller.engine.calls[0][0], "admin.narration.set_chain")
        self.assertEqual(control.calls[0][0], "narration.apply")
        self.assertTrue(result["applied"])

    def test_the_write_carries_the_dashboard_actor_so_the_audit_row_has_a_name(self):
        controller, _ = _controller()
        asyncio.run(
            controller.run("narration.set_chain", {"slots": {"routine_model": "v/m:free"}})
        )
        self.assertEqual(controller.engine.calls[0][1], 7)

    def test_an_unreachable_bot_does_not_lose_the_stored_choice(self):
        # It is already durable and will apply at the next restart. Reporting
        # this as a failure would tell a GM their change did not happen when it
        # did - and invite them to make it twice.
        controller, _ = _controller(fail_control=True)
        result = asyncio.run(
            controller.run("narration.set_chain", {"slots": {"routine_model": "v/m:free"}})
        )
        self.assertTrue(controller.engine.calls)
        self.assertTrue(result["ok"])
        self.assertFalse(result["applied"])
        self.assertIn("not answering", result["apply_detail"]["reason"])

    def test_an_empty_selection_is_refused_before_anything_is_written(self):
        controller, _ = _controller()
        with self.assertRaises(ValueError):
            asyncio.run(controller.run("narration.set_chain", {"slots": {}}))
        self.assertEqual(controller.engine.calls, [])

    def test_writes_are_refused_when_the_dashboard_is_read_only(self):
        controller, _ = _controller(enabled=False)
        with self.assertRaises(PermissionError):
            asyncio.run(controller.run("narration.set_chain", {"slots": {"routine_model": "v/m:free"}}))
        self.assertEqual(controller.engine.calls, [])

    def test_an_unsupported_action_never_reaches_the_engine(self):
        controller, _ = _controller()
        with self.assertRaises(ValueError):
            asyncio.run(controller.run("narration.delete_everything", {}))
        self.assertEqual(controller.engine.calls, [])


class NarrationReadTests(unittest.TestCase):
    def test_reading_the_panel_needs_no_admin_write_permission(self):
        # A read-only GM should still see which routes are live and why one is
        # retired; gating the read would blank the panel and teach nobody
        # anything.
        controller, control = _controller(enabled=False)
        snapshot = asyncio.run(controller.snapshot())
        self.assertTrue(snapshot["connected"])
        self.assertEqual([call[0] for call in control.calls], ["narration.status", "narration.catalogue"])
        self.assertFalse(snapshot["admin_writes"])

    def test_an_unreachable_bot_renders_a_message_rather_than_raising(self):
        controller, _ = _controller(fail_control=True)
        snapshot = asyncio.run(controller.snapshot())
        self.assertFalse(snapshot["connected"])
        self.assertIn("not answering", snapshot["message"])


if __name__ == "__main__":
    unittest.main()
