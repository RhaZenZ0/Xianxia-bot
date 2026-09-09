"""First-run and container-hardening contracts for the deployment scripts.

Two of these came from an external review of v0.19.7 and are the kind of thing
that only bites a new operator or a security pass, never the person who already
has a working .env.
"""
from tests.support import PROJECT_ROOT
import re
import unittest

STARTUP = (PROJECT_ROOT / "startup.sh").read_text(encoding="utf-8")
ENV_EXAMPLE = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
DOCKERFILE = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
STOP = (PROJECT_ROOT / "stop.sh").read_text(encoding="utf-8")


class FirstRunSetupTests(unittest.TestCase):
    def test_first_run_message_mentions_every_secret_the_run_will_demand(self):
        """Whatever startup.sh hard-fails on must be named before the first run.

        Naming a subset is how a first run gets all the way to a check it was
        never told about. It happened twice: the message named only
        DISCORD_TOKEN, GUILD_ID and OPENROUTER_API_KEY while the dashboard check
        demanded a 20-character DASHBOARD_TOKEN, and then again with
        ENGINE_AUTH_TOKEN, which is checked three lines after the message and
        stops the stack dead. So this reads the failures out of the script
        rather than listing names here, and a new required value fails this
        test until it is announced.
        """
        section = STARTUP[STARTUP.index('if [ ! -f "$ENV_FILE" ]'): STARTUP.index("DISCORD_TOKEN_VALUE=")]
        # Only what the operator is actually shown. A comment in the same block
        # explaining why a name matters would otherwise satisfy this test while
        # the name never reached the terminal.
        block = "\n".join(line for line in section.splitlines() if line.strip().startswith("echo "))
        demanded = set(re.findall(r'fail "([A-Z][A-Z0-9_]*) (?:is empty|must be)', STARTUP))
        self.assertTrue(demanded, "no required-value checks found in startup.sh")
        for name in sorted(demanded):
            self.assertIn(name, block,
                          f"startup.sh fails on {name} but the first-run message never mentions it")

        dashboard_default_on = re.search(r"^DASHBOARD_ENABLED=(\w+)", ENV_EXAMPLE, re.M)
        self.assertIsNotNone(dashboard_default_on)
        if dashboard_default_on.group(1).lower() in {"1", "true", "yes", "on"}:
            self.assertIn("DASHBOARD_ENABLED=false", block,
                          "offer the opt-out alongside the requirement")

    def test_the_required_block_is_the_first_thing_in_the_env_example(self):
        """A value you must supply should not be 200 lines below one you need not.

        OPENROUTER_API_KEY used to sit in the AI section and DASHBOARD_TOKEN at
        the very bottom, so filling in a fresh .env meant hunting for them.
        """
        head = ENV_EXAMPLE[:ENV_EXAMPLE.index("# Discord")]
        for name in ("DISCORD_TOKEN=", "GUILD_ID=", "OPENROUTER_API_KEY=",
                     "ENGINE_AUTH_TOKEN=", "DASHBOARD_TOKEN="):
            self.assertIn(name, head, f"{name} is not in the required block at the top of .env.example")
        # And exactly once each - a key set twice in a .env silently takes its
        # last value, which is the worst way to learn it was duplicated.
        for name in ("OPENROUTER_API_KEY", "DASHBOARD_TOKEN", "ENGINE_AUTH_TOKEN"):
            assignments = re.findall(rf"^{name}=", ENV_EXAMPLE, re.M)
            self.assertEqual(len(assignments), 1, f"{name} is assigned {len(assignments)} times")

    def test_the_token_failure_says_how_to_produce_one(self):
        self.assertIn("secrets.token_urlsafe", STARTUP)


class DashboardFlagTests(unittest.TestCase):
    def test_the_flag_is_normalised_once_and_reused(self):
        """Acceptance and reporting must agree on what "enabled" means.

        Acceptance took 1/true/yes/on while the closing log hint compared against
        the literal "true", so DASHBOARD_ENABLED=yes started the dashboard and
        then withheld the command for reading its logs.
        """
        self.assertIn("DASHBOARD_ON=", STARTUP)
        self.assertNotIn('"${DASHBOARD_ENABLED_VALUE:-false}" = "true"', STARTUP)
        accepted = re.search(r"^\s*(1\|true\|yes\|on)\)", STARTUP, re.M)
        self.assertIsNotNone(accepted, "the accepted spellings should stay in one place")
        self.assertEqual(STARTUP.count("1|true|yes|on"), 1,
                         "only one place should decide what counts as enabled")
        self.assertIn('if [ "$DASHBOARD_ON" = "1" ]', STARTUP,
                      "the log hint must use the same normalised flag")


class ContainerUserTests(unittest.TestCase):
    def test_python_containers_do_not_run_as_root(self):
        """bot, db-init and dashboard all build from this Dockerfile."""
        # re.M matters: assertRegex would otherwise anchor ^ to the start of the file.
        self.assertIsNotNone(
            re.search(r"^USER\s+10001", DOCKERFILE, re.M), "Python image still runs as root"
        )
        user_line = DOCKERFILE.index("\nUSER ")
        self.assertLess(DOCKERFILE.index("pip install"), user_line,
                        "pip install has to run before the user drop")
        self.assertLess(DOCKERFILE.index("COPY . ."), user_line,
                        "the source copy has to run before the user drop")

    def test_the_unconditional_mkdir_path_is_writable_by_that_user(self):
        """Database.__init__ calls path.parent.mkdir() before checking the transport.

        The Python services reach SQLite over GAME_ENGINE_URL and never open a
        file, but that mkdir runs regardless - so /app/data must exist and be
        owned by the runtime user or all three containers die at startup on a
        PermissionError for a directory they never use.
        """
        self.assertIn("mkdir -p /app/data", DOCKERFILE)
        self.assertIn("chown -R xianxia:xianxia /app/data", DOCKERFILE)

    def test_only_the_engine_mounts_a_host_volume(self):
        """If a Python service ever gains a bind mount, revisit the user drop."""
        mounts = re.findall(r"^\s+-\s+\./data:/data\s*$", COMPOSE, re.M)
        self.assertEqual(len(mounts), 1,
                         "more than one service mounts ./data; the non-root drop assumes only the engine does")


REQUIREMENTS = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
AI_ROUTER = (PROJECT_ROOT / "app" / "ai" / "ai_router.py").read_text(encoding="utf-8")
NARRATOR = (PROJECT_ROOT / "app" / "ai" / "narrator.py").read_text(encoding="utf-8")


class OpenAISDKMajorBumpTests(unittest.TestCase):
    """Contracts for the openai 2.x -> 3.x bump.

    openai 3.x replaced httpx with HTTPX2. The published migration note says the
    upgrade is a no-op *provided* the client is built without a custom
    ``http_client`` - so that condition is asserted rather than assumed, because
    it is the whole basis for calling this bump safe.
    """

    def test_openai_is_pinned_to_a_3_x_release(self):
        match = re.search(r"^openai==(\d+)\.", REQUIREMENTS, re.M)
        self.assertIsNotNone(match, "openai must stay exactly pinned, not floated")
        self.assertGreaterEqual(int(match.group(1)), 3)

    def test_httpx_is_pinned_directly_and_not_relied_on_transitively(self):
        # openai 3.x installs httpx2 and no longer installs httpx at all, but
        # app/database/remote.py and app/ops/game_engine.py import httpx directly for
        # the Go engine transport - the path every piece of game state uses.
        # A range until v0.29.0; an exact pin since, with the lock carrying the hash.
        self.assertRegex(REQUIREMENTS, r"(?m)^httpx==")
        for module in ("app/database/remote.py", "app/ops/game_engine.py"):
            self.assertIn(
                "import httpx",
                (PROJECT_ROOT / module).read_text(encoding="utf-8"),
                module,
            )

    def test_no_custom_http_client_is_passed_to_the_openai_client(self):
        # This is the exact condition under which the 3.x migration is documented
        # as requiring no code changes.
        for source in (AI_ROUTER, NARRATOR):
            self.assertNotIn("http_client", source)

    def test_the_image_installs_and_verifies_an_os_trust_store(self):
        # HTTPX2 verifies against the OS trust store instead of certifi. A missing
        # bundle would fail every route, and narration degrades silently rather
        # than raising - so the build asserts the bundle exists.
        self.assertIn("ca-certificates", DOCKERFILE)
        self.assertIn("test -s /etc/ssl/certs/ca-certificates.crt", DOCKERFILE)
        self.assertIn("SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt", DOCKERFILE)

    def test_the_trust_store_is_in_place_before_dependencies_are_installed(self):
        self.assertLess(
            DOCKERFILE.index("update-ca-certificates"),
            DOCKERFILE.index("pip install"),
        )


HEALTH = (PROJECT_ROOT / "app" / "ops" / "health.py").read_text(encoding="utf-8")
DASHBOARD = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")


class RequestHeadLimitTests(unittest.TestCase):
    """Slow-header resource exhaustion in the two hand-rolled HTTP parsers.

    Both servers read headers with a loop of readline() calls. There was a
    timeout per line and nothing else - no request-line cap, no header count,
    no cumulative byte budget, no absolute deadline - so a client sending one
    header just before each timeout held a connection open indefinitely, and a
    client sending them quickly grew the header dict without bound. Everything
    below runs BEFORE authentication on both servers.
    """

    def test_neither_server_still_reads_headers_in_an_unbounded_loop(self):
        for name, source in (("health.py", HEALTH), ("dashboard.py", DASHBOARD)):
            for match in re.finditer(r"while True:(.{0,200})", source, re.S):
                self.assertNotIn("readline()", match.group(1), name)

    def test_both_servers_read_the_head_through_the_shared_limiter(self):
        for name, source in (("health.py", HEALTH), ("dashboard.py", DASHBOARD)):
            self.assertIn("read_request_head(", source, name)
            self.assertIn("limits=self.header_limits", source, name)

    def test_both_servers_state_the_stream_limit_instead_of_inheriting_64k(self):
        for name, source in (("health.py", HEALTH), ("dashboard.py", DASHBOARD)):
            self.assertIn("limit=STREAM_LIMIT", source, name)

    def test_both_servers_cap_concurrent_connections_and_release_the_slot(self):
        for name, source in (("health.py", HEALTH), ("dashboard.py", DASHBOARD)):
            self.assertIn("self.connections.try_acquire()", source, name)
            self.assertIn("self.connections.release()", source, name)
            self.assertIn("too_many_connections", source, name)

    def test_the_slot_is_released_in_a_finally(self):
        # A handler that raises before release leaks a slot, and enough leaks
        # wedge the listener permanently - a worse outcome than the DoS.
        for name, source in (("health.py", HEALTH), ("dashboard.py", DASHBOARD)):
            index = source.index("self.connections.release()")
            preceding = source[:index]
            self.assertIn("finally:", preceding[-400:], name)

    def test_the_head_is_bounded_before_authentication_runs(self):
        # The dashboard is the network-facing listener; every byte below is read
        # from an anonymous peer.
        self.assertLess(
            DASHBOARD.index("read_request_head("),
            DASHBOARD.index("if not self._authorized(headers):"),
        )

    def test_rejections_answer_a_real_status_line(self):
        # Before this, a rejected slow-header request went out as
        # "HTTP/1.1 408 OK" because the reason map had no entry and defaulted.
        for name, source in (("health.py", HEALTH), ("dashboard.py", DASHBOARD)):
            for status, phrase in (
                (408, "Request Timeout"),
                (414, "URI Too Long"),
                (431, "Request Header Fields Too Large"),
            ):
                self.assertRegex(source, rf'{status}\s*:\s*"{phrase}"', f"{name} {status}")

    def test_the_limits_are_configurable_and_documented(self):
        for key in (
            "HTTP_MAX_REQUEST_LINE_BYTES",
            "HTTP_MAX_HEADER_LINES",
            "HTTP_MAX_HEADER_BYTES",
            "HTTP_HEADER_DEADLINE_SECONDS",
            "HTTP_MAX_CONNECTIONS",
        ):
            self.assertIn(key, ENV_EXAMPLE, key)

class ContainerBootstrapTests(unittest.TestCase):
    """The compose stack's shape (merged from test_container_bootstrap.py in
    v0.20.3; its retired-service check is the finished removal that
    test_config's provider whitelist already pins)."""

    def test_database_bootstrap_is_a_required_one_shot_gate(self):
        self.assertIn("xianxia-db-init:", COMPOSE)
        self.assertIn('command: ["python", "-m", "app.database.bootstrap"]', COMPOSE)
        self.assertIn("xianxia-db-init:\n        condition: service_completed_successfully", COMPOSE)

    def test_qnap_scripts_manage_engine_bot_and_optional_dashboard(self):
        self.assertIn("docker compose --profile dashboard up", STARTUP)
        self.assertIn("OPENROUTER_API_KEY", STARTUP)
        self.assertIn("DASHBOARD_TOKEN", STARTUP)
        self.assertIn('BOT_CONTROL_URL: "http://xianxia-bot:8080"', COMPOSE)
        self.assertIn('test: ["CMD", "python", "-m", "app.ops.healthcheck"]', COMPOSE)
        self.assertIn("docker compose --profile dashboard down", STOP)


class EnvPreflightTests(unittest.TestCase):
    """v0.20.5: `startup.sh --check-env [FILE]` validates a .env against the
    release's requirements without Docker and without starting anything, and
    update.sh runs the STAGED release's copy against the installed .env
    before it stops the stack. A 0.19.20 install updating to 0.20.4 failed at
    startup on a missing ENGINE_AUTH_TOKEN and rolled back - correctly, but
    a full stop/rollback/restart cycle for a one-line .env edit."""

    def _check(self, env_text):
        import subprocess
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("startup.sh", ".env.example", "VERSION"):
                (root / name).write_text((PROJECT_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
            (root / "candidate.env").write_text(env_text, encoding="utf-8")
            result = subprocess.run(
                ["sh", "./startup.sh", "--check-env", "candidate.env"], cwd=root,
                capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
            )
            return result.returncode, result.stdout + result.stderr, (root / ".env").exists()

    BASE = "DISCORD_TOKEN=x\nGUILD_ID=1\nOPENROUTER_API_KEY=k\n"

    def test_a_pre_0_19_env_is_refused_with_the_token_hint_and_nothing_is_created(self):
        rc, out, created = self._check(self.BASE + "DASHBOARD_ENABLED=false\n")
        self.assertEqual(rc, 1)
        self.assertIn("ENGINE_AUTH_TOKEN must be at least 20 characters", out)
        self.assertFalse(created)

    def test_the_dashboard_token_is_checked_in_check_mode_too(self):
        rc, out, _ = self._check(self.BASE + "ENGINE_AUTH_TOKEN=abcdefghijklmnopqrstuvwxyz\nDASHBOARD_ENABLED=true\n")
        self.assertEqual(rc, 1)
        self.assertIn("DASHBOARD_TOKEN must be at least 20 characters", out)

    def test_a_complete_env_passes_without_docker(self):
        rc, out, _ = self._check(self.BASE + "ENGINE_AUTH_TOKEN=abcdefghijklmnopqrstuvwxyz\nDASHBOARD_ENABLED=false\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("satisfies the requirements of Xianxia RP", out)
        self.assertNotIn("Docker", out)

    def test_update_sh_preflights_before_it_stops_anything(self):
        update = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        preflight = update.index('sh ./startup.sh --check-env "$PROJECT_DIR/.env"')
        self.assertLess(update.index("verify_release_manifest\n"), preflight)
        self.assertLess(preflight, update.index("ROLLBACK_ARMED=1"))
        self.assertLess(preflight, update.index('"$PROJECT_DIR/stop.sh"; else'))
        # The new release's copy is what is asked - an older package without
        # the flag would start the stack, so it is only run when it knows it.
        self.assertIn("grep -q -- '--check-env' \"$NEW_ROOT/startup.sh\"", update)
        self.assertIn('(cd "$NEW_ROOT" && sh ./startup.sh --check-env', update)
        self.assertIn("Nothing was stopped or changed.", update)

    def test_update_sh_authenticates_its_backup_call_to_the_engine(self):
        # Every /v1/ route has required X-Xianxia-Engine-Token since 0.20.0
        # (go_core/internal/server/server.go, authorized()). The pre-update
        # backup is a /v1/db/backups POST; without the header a tokened engine
        # answers 401 and the updater stops at "Could not create a safe SQLite
        # backup" - which is what every update *from* a 0.20 engine did until
        # v0.20.9. The token lives in the engine container's environment, so
        # the request must be assembled inside it, not on the host.
        update = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        server = (PROJECT_ROOT / "go_core" / "internal" / "server" / "server.go").read_text(encoding="utf-8")
        self.assertIn('r.Header.Get("X-Xianxia-Engine-Token")', server)
        start = update.index("create_database_backup()")
        body = update[start:update.index("DB_BACKUP_PATH=", start)]
        self.assertIn("docker compose exec -T xianxia-engine sh -c", body)
        self.assertIn('--header="X-Xianxia-Engine-Token: $ENGINE_AUTH_TOKEN"', body)
        self.assertIn("/v1/db/backups", body)
        # The whole request is one single-quoted string, so $ENGINE_AUTH_TOKEN
        # expands in the container, not on the host where it may be unset.
        request = body[body.index("sh -c"):body.index("2>/dev/null")]
        self.assertRegex(request, r"sh -c \\\n\s*'wget [^']*\$ENGINE_AUTH_TOKEN[^']*/v1/db/backups'\s*$")

    def test_update_sh_refuses_to_start_a_tree_that_does_not_match_the_manifest(self):
        # A NAS install once ended with 0.20.8's VERSION, manifest and docs on
        # pre-Quest-Forge code (app/ai/quest_forge.py absent), and the updater
        # then reported "package 0.20.8 is not newer than installed 0.20.8".
        # Deletes and copies now abort explicitly, and the installed tree is
        # checked against the release manifest before startup.sh is called.
        update = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        install = update.index('echo "Installing Xianxia RP $TARGET_VERSION..."')
        start = update.index('echo "Starting Xianxia RP $TARGET_VERSION..."')
        commit = update[install:start]
        self.assertIn('rm -rf "$old_item" || {', commit)
        self.assertIn('cp -a "$item" "$PROJECT_DIR/" || {', commit)
        # The check must happen; how it is spelled belongs to the shared helper.
        # This used to assert the literal `sha256sum -c --quiet ...`, which is
        # how the suite came to hold the BusyBox bug in place: the command was
        # unrunnable on the target NAS and the test insisted on it verbatim.
        self.assertIn('verify_manifest_tree "$PROJECT_DIR"', commit)
        self.assertIn("Post-install tree does not match RELEASE_MANIFEST.sha256", commit)
        self.assertLess(commit.index("Post-install VERSION check failed"),
                        commit.index('verify_manifest_tree "$PROJECT_DIR"'))
        self.assertLess(update.index("ROLLBACK_ARMED=1"), install)


if __name__ == "__main__":
    unittest.main()


class UpdaterManifestPortabilityTests(unittest.TestCase):
    """v0.23.2: the updater must run on BusyBox, which is what a QNAP provides.

    v0.23.0 shipped an updater that verified the downloaded archive correctly
    and then failed the *identical* check against the installed tree, because
    the check was written twice and only one copy had been made portable. The
    second still passed `--quiet`, a GNU coreutils extension; BusyBox answers
    "unrecognized option", exits non-zero, and the updater rolls the install
    back reporting a corrupt tree that is in fact byte-perfect.
    """

    UPDATE = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")

    def test_no_gnu_only_checksum_flags_are_passed(self):
        for flag in ("--quiet", "--strict", "--warn", "--ignore-missing"):
            for line in self.UPDATE.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # the comment explaining why is allowed to name them
                if flag in stripped and ("sha256sum" in stripped or "shasum" in stripped):
                    self.fail(f"update.sh passes {flag} to a checksum tool: {stripped}")

    def test_there_is_exactly_one_manifest_verification(self):
        """Two copies of a check is two places to fix a bug in.

        This is the actual root cause: the BusyBox fix was applied where the
        failure was noticed and the other copy was never touched. One helper,
        two call sites.
        """
        self.assertEqual(
            self.UPDATE.count("verify_manifest_tree() {"), 1,
            "the manifest check should be defined once",
        )
        invocations = [
            line for line in self.UPDATE.splitlines()
            if "verify_manifest_tree " in line and not line.strip().startswith("#")
        ]
        self.assertGreaterEqual(len(invocations), 2,
                                "both the pre-install and post-install checks should call the helper")
        for tool in ("sha256sum -c", "shasum -a 256 -c"):
            self.assertEqual(
                self.UPDATE.count(tool), 1,
                f"{tool} appears more than once; the check has been duplicated again",
            )

    def test_both_the_package_and_the_installed_tree_are_verified(self):
        self.assertIn('verify_manifest_tree "$NEW_ROOT"', self.UPDATE)
        self.assertIn('verify_manifest_tree "$PROJECT_DIR"', self.UPDATE)
