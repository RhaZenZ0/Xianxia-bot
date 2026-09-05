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


class FirstRunSetupTests(unittest.TestCase):
    def test_first_run_message_mentions_every_secret_the_run_will_demand(self):
        """.env.example enables the dashboard, so DASHBOARD_TOKEN is required too.

        The message used to name only DISCORD_TOKEN, GUILD_ID and
        OPENROUTER_API_KEY. A first run that filled in exactly those three got
        all the way to the dashboard check and failed on a 20-character token it
        had never been told about.
        """
        block = STARTUP[STARTUP.index("if [ ! -f .env ]"): STARTUP.index("DISCORD_TOKEN_VALUE=")]
        dashboard_default_on = re.search(r"^DASHBOARD_ENABLED=(\w+)", ENV_EXAMPLE, re.M)
        self.assertIsNotNone(dashboard_default_on)
        if dashboard_default_on.group(1).lower() in {"1", "true", "yes", "on"}:
            self.assertIn("DASHBOARD_TOKEN", block,
                          "dashboard is on by default but the first-run message omits its token")
            self.assertIn("DASHBOARD_ENABLED=false", block,
                          "offer the opt-out alongside the requirement")

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


if __name__ == "__main__":
    unittest.main()


REQUIREMENTS = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
AI_ROUTER = (PROJECT_ROOT / "app" / "ai_router.py").read_text(encoding="utf-8")
NARRATOR = (PROJECT_ROOT / "app" / "narrator.py").read_text(encoding="utf-8")


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
        # app/database/remote.py and app/game_engine.py import httpx directly for
        # the Go engine transport - the path every piece of game state uses.
        self.assertRegex(REQUIREMENTS, r"(?m)^httpx>=")
        for module in ("app/database/remote.py", "app/game_engine.py"):
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


HEALTH = (PROJECT_ROOT / "app" / "health.py").read_text(encoding="utf-8")
DASHBOARD = (PROJECT_ROOT / "app" / "dashboard.py").read_text(encoding="utf-8")


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
