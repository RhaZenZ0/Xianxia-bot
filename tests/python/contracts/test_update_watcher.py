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

v1.12.3: an update request could stay open for ever and nothing could clear
it. The conversation is held over several polls now - a report the engine
refuses is retried on the next tick, a world a GM closed stays closed, a
signal ends the process - and the whole file runs under `sh` (dash, where
there is one) and under bash.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT

WATCHER = PROJECT_ROOT / "update_watch.sh"
UPDATER = PROJECT_ROOT / "update.sh"

# Records "<operation> <body>" per call and answers the canned request. A
# file "<operation>.<status>" in $XW_FAILS holding N makes the next N calls
# with that status fail (exit 1), the way the engine's wget does on a refusal
# or an unreachable host.
STUB_ENGINE = r'''#!/bin/sh
printf '%s %s\n' "$1" "$XW_BODY" >> "$XW_CALLS"
st=$(printf '%s' "$XW_BODY" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')
f="$XW_FAILS/$1.$st"
if [ -f "$f" ]; then
    n=$(cat "$f")
    if [ "$n" -gt 0 ]; then echo $((n - 1)) > "$f"; exit 1; fi
fi
case "$1" in
    admin.server.update_request) cat "$XW_REQUEST_ANSWER" ;;
    *) printf '{"result":{"ok":true}}' ;;
esac
'''

FAKE_UPDATERS = {
    "installed": "#!/bin/sh\necho 'Stopping Xianxia RP...'\nprintf '1.4.0\\n' > \"$XW_VERSION_FILE\"\nexit 0\n",
    "slow": "#!/bin/sh\necho 'Stopping Xianxia RP...'\nsleep 2\nprintf '1.4.0\\n' > \"$XW_VERSION_FILE\"\nexit 0\n",
    "preflight": "#!/bin/sh\necho 'ERROR: The installed .env does not satisfy Xianxia RP 1.4.0 (see above).' >&2\nexit 1\n",
    "rolled_back": "#!/bin/sh\necho 'Stopping Xianxia RP...'\necho 'ERROR: New docker-compose.yml is invalid.' >&2\nexit 1\n",
    # A tab and an ESC in the quoted ERROR line: invalid inside a JSON string.
    "control_characters": "#!/bin/sh\necho 'Stopping Xianxia RP...'\nprintf 'ERROR: bad\\tthing \\033[31mred\\033[0m end\\n' >&2\nexit 1\n",
}


def engine_answer(request: dict | None, *, world_closed: bool = False) -> str:
    """The engine's read, as Go writes it: a map marshalled with sorted keys,
    the request object beside flat scalars for the watcher to read."""
    req = request or {}
    return json.dumps({"result": {
        "request": request, "result": None, "heartbeat": None,
        "request_nonce": req.get("nonce", ""), "request_status": req.get("status", ""),
        "request_channel": req.get("channel", ""), "maintenance_enabled": world_closed,
    }}, sort_keys=True)


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class TheWatcherRunsTheRequestAndReportsIt(unittest.TestCase):
    SHELL = "sh"

    def setUp(self):
        if shutil.which(self.SHELL) is None:
            self.skipTest(f"no {self.SHELL} on this machine")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "xianxia"
        self.project.mkdir()
        (self.project / "VERSION").write_text("1.3.5\n", encoding="utf-8")
        self.calls = self.tmp / "calls.log"
        self.answer = self.tmp / "answer.json"
        self.fails = self.tmp / "fails"
        self.fails.mkdir()
        self.state_file = self.tmp / "state"
        self.engine = _executable(self.tmp / "engine.sh", STUB_ENGINE)

    def _env(self, updater: str, extra: dict | None = None) -> dict:
        return {
            **os.environ,
            "XIANXIA_PROJECT_DIR": str(self.project),
            "XIANXIA_ENGINE_CALL": str(self.engine),
            "XIANXIA_UPDATE_SH": str(_executable(self.tmp / f"update_{updater}.sh", FAKE_UPDATERS[updater])),
            "XIANXIA_WATCH_LOG": str(self.tmp / "watch.log"),
            "XIANXIA_WATCH_STATE": str(self.state_file),
            "XIANXIA_WATCH_LOCK": str(self.tmp / "lock"),
            "XIANXIA_WATCH_RETRY_SECONDS": "0",
            "XW_CALLS": str(self.calls),
            "XW_FAILS": str(self.fails),
            "XW_REQUEST_ANSWER": str(self.answer),
            "XW_VERSION_FILE": str(self.project / "VERSION"),
            **(extra or {}),
        }

    def _parse_calls(self) -> list[tuple[str, dict]]:
        out = []
        for line in self.calls.read_text(encoding="utf-8").splitlines():
            op, _, body = line.partition(" ")
            # An invalid body is the finding: the engine would answer 400.
            out.append((op, json.loads(body)["payload"]))
        return out

    def _run(self, updater: str, request: dict | None, *, state: str = "", keep: bool = False,
             world_closed: bool = False, fails: dict | None = None, extra_env: dict | None = None,
             ) -> list[tuple[str, dict]]:
        """One poll. `keep` carries the state file, the unsent report and the
        closed-world marker over from the poll before, the way a second tick of
        a real watcher finds them."""
        self.answer.write_text(engine_answer(request, world_closed=world_closed), encoding="utf-8")
        if not keep:
            for leftover in (self.state_file, Path(f"{self.state_file}.pending"), Path(f"{self.state_file}.closed")):
                leftover.unlink(missing_ok=True)
            if state:
                self.state_file.write_text(state + "\n", encoding="utf-8")
        for name, count in (fails or {}).items():
            (self.fails / name).write_text(f"{count}\n", encoding="utf-8")
        self.calls.write_text("", encoding="utf-8")
        proc = subprocess.run([self.SHELL, str(WATCHER), "--oneshot"], env=self._env(updater, extra_env),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return self._parse_calls()

    @staticmethod
    def _statuses(calls):
        return [p["status"] for op, p in calls if op == "admin.server.update_status"]

    @staticmethod
    def _world(calls):
        return [p for op, p in calls if op == "admin.server.maintenance_mode"]

    REQUEST = {"status": "requested", "nonce": "abc123", "channel": "stable", "reason": "please"}

    def test_an_installed_update_is_reported_done_with_the_new_version(self):
        calls = self._run("installed", self.REQUEST)
        ops = [op for op, _ in calls]
        self.assertEqual(ops, [
            "admin.server.update_request",
            "admin.server.update_status",      # acked
            "admin.server.update_request",     # is the world already closed?
            "admin.server.maintenance_mode",   # closed
            "admin.server.update_status",      # fetching
            "admin.server.maintenance_mode",   # reopened
            "admin.server.update_status",      # done
            "admin.server.update_status",      # heartbeat
        ], calls)
        self.assertEqual(self._statuses(calls), ["acked", "fetching", "done", "heartbeat"])
        done = calls[6][1]
        self.assertEqual(done["nonce"], "abc123")
        self.assertEqual(done["installed_version"], "1.4.0", "the report must carry the version the updater installed")
        closed, reopened = self._world(calls)
        self.assertTrue(closed["enabled"])
        self.assertFalse(reopened["enabled"])
        self.assertFalse(Path(f"{self.state_file}.pending").exists(), "a delivered report was left on disk")
        self.assertFalse(Path(f"{self.state_file}.closed").exists())

    def test_a_refused_preflight_reports_the_env_migration_and_nothing_changed(self):
        calls = self._run("preflight", self.REQUEST)
        failed = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "failed"]
        self.assertEqual(len(failed), 1, calls)
        self.assertIn("migrate_env.sh", failed[0]["detail"])
        self.assertIn("nothing was changed", failed[0]["detail"])
        self.assertEqual((self.project / "VERSION").read_text().strip(), "1.3.5")
        self.assertFalse(self._world(calls)[-1]["enabled"], "a failed update left the world shut")

    def test_a_failure_after_the_stop_is_reported_as_rolled_back(self):
        calls = self._run("rolled_back", self.REQUEST)
        failed = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "failed"]
        self.assertEqual(len(failed), 1, calls)
        self.assertTrue(failed[0]["detail"].startswith("rolled back:"), failed[0]["detail"])
        self.assertIn("docker-compose.yml is invalid", failed[0]["detail"])
        self.assertFalse(self._world(calls)[-1]["enabled"])

    def test_a_request_already_handled_is_not_run_twice(self):
        calls = self._run("installed", self.REQUEST, state="abc123")
        self.assertEqual([op for op, _ in calls], ["admin.server.update_request", "admin.server.update_status"])
        self.assertEqual(calls[1][1]["status"], "heartbeat")

    def test_no_request_means_a_read_and_a_heartbeat_only(self):
        calls = self._run("installed", None)
        self.assertEqual([op for op, _ in calls], ["admin.server.update_request", "admin.server.update_status"])
        self.assertEqual(calls[1][1], {"status": "heartbeat"})

    # -- v1.12.3: what used to leave a request open for ever ---------------------

    def test_a_brace_in_the_reason_still_runs_the_request(self):
        """The watcher cut the request object out of the read at its first `}`;
        the engine writes keys alphabetically, so a reason like "fix {bug}"
        lost `status` and the watcher only ever heartbeated."""
        request = {**self.REQUEST, "reason": 'fix {bug} and "quote" }{'}
        calls = self._run("installed", request)
        self.assertEqual(self._statuses(calls), ["acked", "fetching", "done", "heartbeat"], calls)

    def test_an_ack_that_fails_once_is_retried_on_the_next_tick(self):
        """The nonce was remembered before the ack was sent, so one failed
        report and every later tick skipped the request as handled."""
        calls = self._run("installed", self.REQUEST, fails={"admin.server.update_status.acked": 1})
        self.assertEqual(self._statuses(calls), ["acked", "heartbeat"], "a failed ack must stop the run there")
        self.assertEqual(self._world(calls), [], "the world was closed for a request that was never acknowledged")
        self.assertFalse(self.state_file.exists() and "abc123" in self.state_file.read_text(),
                         "the nonce was remembered before the ack was taken")
        calls = self._run("installed", self.REQUEST, keep=True)
        self.assertEqual(self._statuses(calls), ["acked", "fetching", "done", "heartbeat"], calls)
        self.assertIn("abc123", self.state_file.read_text())

    def test_an_ack_the_engine_took_but_never_answered_is_run_not_left(self):
        """The ack landed and its answer was lost: the request reads `acked`
        and this watcher never remembered it, so nobody else will run it."""
        calls = self._run("installed", {**self.REQUEST, "status": "acked"})
        self.assertEqual(self._statuses(calls), ["acked", "fetching", "done", "heartbeat"], calls)

    def test_a_final_report_the_engine_refuses_twice_lands_on_the_third_poll(self):
        """The closing report was tried twenty times and abandoned, so an
        engine slower than a minute and a half to come back left the request
        at `fetching` for ever. It is written to disk first and sent again at
        the start of every poll."""
        one_try = {"XIANXIA_WATCH_REOPEN_TRIES": "1"}
        calls = self._run("installed", self.REQUEST, fails={"admin.server.update_status.done": 2}, extra_env=one_try)
        self.assertEqual(self._statuses(calls).count("done"), 1)
        self.assertTrue(Path(f"{self.state_file}.pending").exists(), "the unsent report was not kept")
        self.assertFalse(self._world(calls)[-1]["enabled"], "the world stayed shut because the report was refused")
        fetching = {**self.REQUEST, "status": "fetching"}
        calls = self._run("installed", fetching, keep=True, extra_env=one_try)
        self.assertEqual(self._statuses(calls).count("done"), 1, "the second poll must try again, and only send it")
        self.assertNotIn("failed", self._statuses(calls))
        self.assertTrue(Path(f"{self.state_file}.pending").exists())
        calls = self._run("installed", fetching, keep=True, extra_env=one_try)
        done = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "done"]
        self.assertEqual(len(done), 1, calls)
        self.assertEqual(done[0]["nonce"], "abc123")
        self.assertEqual(done[0]["installed_version"], "1.4.0", "the kept report lost the version it carried")
        self.assertFalse(Path(f"{self.state_file}.pending").exists(), "a delivered report stayed on disk")

    def test_a_report_for_a_request_that_is_no_longer_open_is_dropped_not_retried_for_ever(self):
        self._run("installed", self.REQUEST, fails={"admin.server.update_status.done": 99},
                  extra_env={"XIANXIA_WATCH_REOPEN_TRIES": "1"})
        self.assertTrue(Path(f"{self.state_file}.pending").exists())
        calls = self._run("installed", {**self.REQUEST, "status": "cancelled"}, keep=True)
        self.assertNotIn("done", self._statuses(calls))
        self.assertFalse(Path(f"{self.state_file}.pending").exists())

    def test_a_tab_and_an_escape_in_the_error_line_still_make_valid_json(self):
        """`json_escape` dropped CR and LF only, so a tab or an ESC in the
        quoted `ERROR:` line made the body invalid JSON and the engine refused
        every retry. `_parse_calls` fails on an invalid body."""
        calls = self._run("control_characters", self.REQUEST)
        failed = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "failed"]
        self.assertEqual(len(failed), 1, calls)
        self.assertIn("rolled back:", failed[0]["detail"])
        self.assertIn("bad thing", failed[0]["detail"], "a tab should have become a space")
        self.assertFalse([c for c in failed[0]["detail"] if ord(c) < 32], failed[0]["detail"])

    def test_a_world_the_gm_closed_stays_closed(self):
        """The watcher always reopened the world at the end, wiping a closure
        (and its reason) the GM had made before asking for the update."""
        calls = self._run("installed", self.REQUEST, world_closed=True)
        self.assertEqual(self._world(calls), [], "the watcher touched a world a GM had closed")
        self.assertEqual(self._statuses(calls), ["acked", "fetching", "done", "heartbeat"])

    def test_a_world_the_watcher_could_not_reopen_is_reopened_on_a_later_poll(self):
        marker = Path(f"{self.state_file}.closed")
        self.state_file.write_text("older\n", encoding="utf-8")
        marker.write_text("", encoding="utf-8")
        calls = self._run("installed", None, keep=True)
        self.assertEqual([p["enabled"] for p in self._world(calls)], [False])
        self.assertFalse(marker.exists())

    def test_the_scratch_file_is_made_before_the_world_is_closed(self):
        """`mktemp` ran after the world was closed, so a failure left it shut."""
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        _executable(bin_dir / "mktemp", "#!/bin/sh\nexit 1\n")
        calls = self._run("installed", self.REQUEST, extra_env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        self.assertEqual(self._world(calls), [], "the world was closed before the scratch file existed")
        failed = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "failed"]
        self.assertEqual(len(failed), 1, calls)
        self.assertIn("nothing was changed", failed[0]["detail"])

    def test_a_request_left_open_by_a_watcher_that_died_is_reported_failed(self):
        """Something past `requested`, remembered here, with nothing running
        and no report waiting: the process that ran it is gone. Left alone the
        Request button never came back."""
        for status in ("acked", "fetching", "installing"):
            with self.subTest(status=status):
                calls = self._run("installed", {**self.REQUEST, "status": status}, state="abc123")
                failed = [p for op, p in calls if op == "admin.server.update_status" and p["status"] == "failed"]
                self.assertEqual(len(failed), 1, calls)
                self.assertIn("stopped", failed[0]["detail"])
                self.assertEqual(self._world(calls), [], "an interrupted request must not run the update again")


class TheWatcherUnderBash(TheWatcherRunsTheRequestAndReportsIt):
    """The same conversation under bash, which is what a NAS shell may be."""
    SHELL = "bash"


class TheWatcherStopsWhenItIsToldTo(unittest.TestCase):
    """`kill <watcher>` removed the lock and never exited, so the process went
    on running unlocked and a second watcher could start and take the same
    nonce."""
    SHELL = "sh"

    def setUp(self):
        if shutil.which(self.SHELL) is None:
            self.skipTest(f"no {self.SHELL} on this machine")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "xianxia"
        self.project.mkdir()
        (self.project / "VERSION").write_text("1.3.5\n", encoding="utf-8")
        self.calls = self.tmp / "calls.log"
        self.calls.write_text("", encoding="utf-8")
        (self.tmp / "fails").mkdir()
        self.answer = self.tmp / "answer.json"
        self.answer.write_text(engine_answer(None), encoding="utf-8")
        engine = _executable(self.tmp / "engine.sh", STUB_ENGINE)
        self.lock = self.tmp / "lock"
        self.env = {
            **os.environ,
            "XIANXIA_PROJECT_DIR": str(self.project), "XIANXIA_ENGINE_CALL": str(engine),
            "XIANXIA_WATCH_LOG": str(self.tmp / "watch.log"), "XIANXIA_WATCH_STATE": str(self.tmp / "state"),
            "XIANXIA_WATCH_LOCK": str(self.lock), "XIANXIA_WATCH_RETRY_SECONDS": "0",
            "XW_CALLS": str(self.calls), "XW_FAILS": str(self.tmp / "fails"),
            "XW_REQUEST_ANSWER": str(self.answer), "XW_VERSION_FILE": str(self.project / "VERSION"),
            "UPDATE_WATCH_INTERVAL_SECONDS": "5",
        }

    def _start(self, updater: Path | None = None) -> subprocess.Popen:
        env = dict(self.env)
        if updater is not None:
            env["XIANXIA_UPDATE_SH"] = str(updater)
        proc = subprocess.Popen([self.SHELL, str(WATCHER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def _wait_for(self, needle: str, timeout: float = 15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self.calls.read_text(encoding="utf-8"):
                return
            time.sleep(0.05)
        self.fail(f"the watcher never made a call naming {needle!r}")

    def test_sigterm_ends_the_process_and_removes_the_lock(self):
        proc = self._start()
        self._wait_for("heartbeat")
        time.sleep(0.3)
        self.assertTrue(self.lock.exists(), "the watcher did not take its lock")
        started = time.time()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=4)
        self.assertLess(time.time() - started, 3, "the signal waited out the poll interval")
        self.assertFalse(self.lock.exists(), "a stopped watcher left its lock behind")
        self.assertEqual(proc.returncode, 143)

    def test_an_update_in_progress_is_finished_and_reported_before_the_watcher_stops(self):
        request = {"status": "requested", "nonce": "abc123", "channel": "stable", "reason": "please"}
        self.answer.write_text(engine_answer(request), encoding="utf-8")
        proc = self._start(_executable(self.tmp / "update_slow.sh", FAKE_UPDATERS["slow"]))
        self._wait_for('"status":"fetching"')
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
        self.assertEqual(proc.returncode, 143)
        self.assertFalse(self.lock.exists())
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn('"status":"done"', calls, "a signal abandoned the update before it was reported")
        self.assertEqual((self.project / "VERSION").read_text().strip(), "1.4.0")


class TheWatcherUnderBashStops(TheWatcherStopsWhenItIsToldTo):
    SHELL = "bash"


class TheUpdaterLeavesTheWatchersFilesAlone(unittest.TestCase):
    """`update.sh` deletes everything in the project folder it does not keep.
    The watcher's log (the default LOG_FILE) and its state were not kept, so
    every update erased them."""

    def _skip_lines(self):
        lines = [line for line in UPDATER.read_text(encoding="utf-8").splitlines() if 'case "$name" in .env' in line]
        self.assertEqual(len(lines), 5, "the updater's delete and copy loops changed shape; read this test again")
        return lines

    def test_every_loop_keeps_the_log_and_the_watchers_state(self):
        for line in self._skip_lines():
            for name in ("update_watch.log", ".xianxia-watcher-state", ".xianxia-watcher-state.pending",
                         ".xianxia-watcher-state.closed", ".xianxia-watcher.lock"):
                proc = subprocess.run(
                    ["sh", "-c", f'for name in {name}; do {line.strip()}; echo "removed:$name"; done'],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.stdout.strip(), "", f"{name} would be deleted by: {line.strip()}")

    def test_the_skip_lists_still_let_ordinary_files_through(self):
        """A skip list that keeps everything is not a list."""
        for line in self._skip_lines():
            proc = subprocess.run(
                ["sh", "-c", f'for name in app update_watch.sh; do {line.strip()}; echo "removed:$name"; done'],
                capture_output=True, text=True, timeout=10)
            self.assertIn("removed:app", proc.stdout, line.strip())

    def test_the_watchers_log_and_state_really_live_where_those_names_say(self):
        """The exclusions name what the watcher writes; if it moved them, the
        names would protect nothing."""
        watcher = WATCHER.read_text(encoding="utf-8")
        self.assertIn("/update_watch.log", watcher)
        self.assertIn(".xianxia-watcher-state", watcher)
        self.assertIn(".xianxia-watcher.lock", watcher)


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

    def test_it_cuts_no_json_by_hand(self):
        """The request object was cut out of the read at its first `}`. The
        engine answers the three values as flat strings now."""
        watcher = WATCHER.read_text(encoding="utf-8")
        self.assertNotIn("request_object", watcher)
        for key in ("request_status", "request_nonce", "request_channel"):
            self.assertIn(key, watcher)

    def test_it_is_in_the_release_manifest(self):
        manifest = (PROJECT_ROOT / "RELEASE_MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertTrue(" update_watch.sh" in manifest, "update_watch.sh is not in RELEASE_MANIFEST.sha256; run scripts/release_manifest.py --write")
