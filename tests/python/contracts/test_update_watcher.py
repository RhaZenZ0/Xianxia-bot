"""The update watcher does what the dashboard's request asks, and reports it (v1.4.0).

`update_watch.sh` is the host half of "update from the dashboard": it reads
the GM's request out of the engine, closes the world, runs `update.sh
--upgrade`, reopens the world and reports the outcome under the request's own
nonce. It cannot run for real here - there is no Docker and no NAS - so the
two things it talks to are stood in for: `XIANXIA_ENGINE_CALL` names a stub
that records every operation it is sent and answers canned JSON, and
`XIANXIA_UPDATE_SH` names a fake updater that exits the way the real one does
in each of its three outcomes. What is held is the conversation: which
operations, in which order, with which status and detail, and that the world
is reopened whatever happened.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT

WATCHER = PROJECT_ROOT / "update_watch.sh"

STUB_ENGINE = r'''#!/bin/sh
# Records "<operation> <body>" per call and answers the canned request.
printf '%s %s\n' "$1" "$XW_BODY" >> "$XW_CALLS"
case "$1" in
    admin.server.update_request) cat "$XW_REQUEST_ANSWER" ;;
    *) printf '{"result":{"ok":true}}' ;;
esac
'''

FAKE_UPDATERS = {
    "installed": "#!/bin/sh\necho 'Stopping Xianxia RP...'\nprintf '1.4.0\\n' > \"$XW_VERSION_FILE\"\nexit 0\n",
    "preflight": "#!/bin/sh\necho 'ERROR: The installed .env does not satisfy Xianxia RP 1.4.0 (see above).' >&2\nexit 1\n",
    "rolled_back": "#!/bin/sh\necho 'Stopping Xianxia RP...'\necho 'ERROR: New docker-compose.yml is invalid.' >&2\nexit 1\n",
}


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class TheWatcherRunsTheRequestAndReportsIt(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "xianxia"
        self.project.mkdir()
        (self.project / "VERSION").write_text("1.3.5\n", encoding="utf-8")
        self.calls = self.tmp / "calls.log"
        self.answer = self.tmp / "answer.json"
        self.engine = _executable(self.tmp / "engine.sh", STUB_ENGINE)

    def _run(self, updater: str, request: dict | None, *, state: str = "") -> list[tuple[str, dict]]:
        self.answer.write_text(json.dumps({"result": {"request": request, "result": None, "heartbeat": None}}), encoding="utf-8")
        state_file = self.tmp / "state"
        if state:
            state_file.write_text(state + "\n", encoding="utf-8")
        elif state_file.exists():
            state_file.unlink()
        self.calls.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "XIANXIA_PROJECT_DIR": str(self.project),
            "XIANXIA_ENGINE_CALL": str(self.engine),
            "XIANXIA_UPDATE_SH": str(_executable(self.tmp / f"update_{updater}.sh", FAKE_UPDATERS[updater])),
            "XIANXIA_WATCH_LOG": str(self.tmp / "watch.log"),
            "XIANXIA_WATCH_STATE": str(state_file),
            "XIANXIA_WATCH_LOCK": str(self.tmp / "lock"),
            "XIANXIA_WATCH_RETRY_SECONDS": "0",
            "XW_CALLS": str(self.calls),
            "XW_REQUEST_ANSWER": str(self.answer),
            "XW_VERSION_FILE": str(self.project / "VERSION"),
        }
        proc = subprocess.run(["sh", str(WATCHER), "--oneshot"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = []
        for line in self.calls.read_text(encoding="utf-8").splitlines():
            op, _, body = line.partition(" ")
            out.append((op, json.loads(body)["payload"]))
        return out

    REQUEST = {"status": "requested", "nonce": "abc123", "channel": "stable", "reason": "please"}

    def test_an_installed_update_is_reported_done_with_the_new_version(self):
        calls = self._run("installed", self.REQUEST)
        ops = [op for op, _ in calls]
        self.assertEqual(ops, [
            "admin.server.update_request",
            "admin.server.update_status",      # acked
            "admin.server.maintenance_mode",   # closed
            "admin.server.update_status",      # fetching
            "admin.server.maintenance_mode",   # reopened
            "admin.server.update_status",      # done
            "admin.server.update_status",      # heartbeat
        ], calls)
        statuses = [p["status"] for op, p in calls if op == "admin.server.update_status"]
        self.assertEqual(statuses, ["acked", "fetching", "done", "heartbeat"])
        done = calls[5][1]
        self.assertEqual(done["nonce"], "abc123")
        self.assertEqual(done["installed_version"], "1.4.0", "the report must carry the version the updater installed")
        closed, reopened = [p for op, p in calls if op == "admin.server.maintenance_mode"]
        self.assertTrue(closed["enabled"])
        self.assertFalse(reopened["enabled"])

    def test_a_refused_preflight_reports_the_env_migration_and_nothing_changed(self):
        calls = self._run("preflight", self.REQUEST)
        failed = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "failed"]
        self.assertEqual(len(failed), 1, calls)
        self.assertIn("migrate_env.sh", failed[0]["detail"])
        self.assertIn("nothing was changed", failed[0]["detail"])
        self.assertEqual((self.project / "VERSION").read_text().strip(), "1.3.5")
        self.assertFalse([p for op, p in calls if op == "admin.server.maintenance_mode"][-1]["enabled"],
                         "a failed update left the world shut")

    def test_a_failure_after_the_stop_is_reported_as_rolled_back(self):
        calls = self._run("rolled_back", self.REQUEST)
        failed = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "failed"]
        self.assertEqual(len(failed), 1, calls)
        self.assertTrue(failed[0]["detail"].startswith("rolled back:"), failed[0]["detail"])
        self.assertIn("docker-compose.yml is invalid", failed[0]["detail"])
        self.assertFalse([p for op, p in calls if op == "admin.server.maintenance_mode"][-1]["enabled"])

    def test_a_request_already_handled_is_not_run_twice(self):
        calls = self._run("installed", self.REQUEST, state="abc123")
        self.assertEqual([op for op, _ in calls], ["admin.server.update_request", "admin.server.update_status"])
        self.assertEqual(calls[1][1]["status"], "heartbeat")

    def test_no_request_means_a_read_and_a_heartbeat_only(self):
        calls = self._run("installed", None)
        self.assertEqual([op for op, _ in calls], ["admin.server.update_request", "admin.server.update_status"])
        self.assertEqual(calls[1][1], {"status": "heartbeat"})

    def test_a_request_already_acked_is_left_to_whoever_acked_it(self):
        calls = self._run("installed", {**self.REQUEST, "status": "acked"})
        self.assertEqual([op for op, _ in calls], ["admin.server.update_request", "admin.server.update_status"])


class TheWatcherIsShippedAndHoldsNoPrivilege(unittest.TestCase):
    def test_it_reaches_the_engine_the_way_update_sh_does(self):
        """One host-to-engine channel: `docker compose exec` into the engine
        container with the token read there. No port is published for it."""
        watcher = WATCHER.read_text(encoding="utf-8")
        self.assertIn("docker compose exec -T", watcher)
        self.assertIn('X-Xianxia-Engine-Token: $ENGINE_AUTH_TOKEN', watcher)
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("docker.sock", compose)
        self.assertNotIn('"8081:8081"', compose)

    def test_it_never_touches_the_env_file(self):
        """A release that adds a key is reported, not migrated: the watcher
        never rewrites the file the tokens live in."""
        watcher = WATCHER.read_text(encoding="utf-8")
        self.assertNotIn("migrate_env.sh --", watcher)
        self.assertNotIn("> \"$PROJECT_DIR/.env\"", watcher)

    def test_it_is_in_the_release_manifest(self):
        manifest = (PROJECT_ROOT / "RELEASE_MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertTrue(" update_watch.sh" in manifest, "update_watch.sh is not in RELEASE_MANIFEST.sha256; run scripts/release_manifest.py --write")
