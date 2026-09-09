"""The doors fail closed (v0.29.0, the roadmap's Hardened I milestone).

Five things a deployment must not be able to get wrong by default: the engine
refuses to run or answer without a token, the dashboard locks an address that
keeps guessing and refuses a mutation from another origin, both listeners bind
loopback unless a container asks for more, what ships is hash-locked and
digest-pinned, and the Go suite runs under the race detector in CI. Each was
open at v0.28.0; each is held here so it cannot quietly reopen.
"""
from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

from app.ops.config import Settings
from app.ops.http_limits import LoginThrottle

GO_SERVER = (PROJECT_ROOT / "go_core" / "internal" / "server" / "server.go").read_text(encoding="utf-8")
GO_AUTH_TEST = (PROJECT_ROOT / "go_core" / "internal" / "server" / "auth_test.go").read_text(encoding="utf-8")
DASHBOARD = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
COMPOSE = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
CONFIGURATION = (PROJECT_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
DOCKERFILE = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
GO_DOCKERFILE = (PROJECT_ROOT / "go_core" / "Dockerfile").read_text(encoding="utf-8")
REQUIREMENTS = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
LOCK_PATH = PROJECT_ROOT / "requirements.lock"
CI = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

TOKEN = "test-engine-token-1234567890"


def bot_env(**overrides: str) -> dict[str, str]:
    env = {
        "DISCORD_TOKEN": "test-token",
        "GUILD_ID": "123456789012345678",
        "DATABASE_PATH": "data/test.sqlite3",
        "ENGINE_AUTH_TOKEN": TOKEN,
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


class EngineTokenTests(unittest.TestCase):
    def test_the_bot_refuses_to_start_without_an_engine_token(self):
        for token in ("", "   ", "nineteen-characters"):
            with self.subTest(token=token):
                with patch.dict(os.environ, bot_env(ENGINE_AUTH_TOKEN=token), clear=True):
                    with self.assertRaises(RuntimeError) as caught:
                        Settings.from_env()
                self.assertIn("ENGINE_AUTH_TOKEN", str(caught.exception))
                self.assertIn("20", str(caught.exception))

    def test_a_twenty_character_token_is_the_floor(self):
        with patch.dict(os.environ, bot_env(ENGINE_AUTH_TOKEN="exactly-twenty-chars"), clear=True):
            self.assertEqual(Settings.from_env().game_engine_auth_token, "exactly-twenty-chars")

    def test_the_dashboard_checks_the_same_token_when_it_talks_to_the_engine(self):
        from app.dashboard.server import DashboardSettings

        base = {
            "DASHBOARD_TOKEN": "a-very-long-private-dashboard-token",
            "GAME_ENGINE_URL": "http://127.0.0.1:8081",
            "DATABASE_PATH": "data/test.sqlite3",
        }
        with patch.dict(os.environ, base, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                DashboardSettings.from_env()
        self.assertIn("ENGINE_AUTH_TOKEN", str(caught.exception))
        with patch.dict(os.environ, {**base, "ENGINE_AUTH_TOKEN": TOKEN}, clear=True):
            DashboardSettings.from_env()

    def test_the_go_engine_no_longer_admits_everyone_when_the_token_is_blank(self):
        # The exact shape of the old hole: an empty token short-circuited to
        # true. The constructor must now refuse, and the check must deny.
        self.assertNotRegex(GO_SERVER, r'authToken == ""\s*\{\s*return true')
        self.assertRegex(GO_SERVER, r'authToken == ""\s*\{\s*return false')
        self.assertIn("minEngineTokenLength", GO_SERVER)
        self.assertIn("ENGINE_AUTH_TOKEN must be set to at least", GO_SERVER)
        self.assertIn("func TestNewRefusesAnEngineWithoutAUsableToken", GO_AUTH_TEST)


class LoginLockoutTests(unittest.TestCase):
    def make(self, **kwargs):
        self.now = 1000.0
        return LoginThrottle(max_failures=3, window_seconds=60.0, lockout_seconds=120.0, clock=lambda: self.now, **kwargs)

    def test_n_failures_lock_the_address_and_only_that_address(self):
        throttle = self.make()
        self.assertFalse(throttle.failure("10.0.0.5"))
        self.assertFalse(throttle.failure("10.0.0.5"))
        self.assertEqual(throttle.locked_for("10.0.0.5"), 0.0)
        self.assertTrue(throttle.failure("10.0.0.5"))
        self.assertGreater(throttle.locked_for("10.0.0.5"), 0.0)
        self.assertEqual(throttle.locked_for("10.0.0.6"), 0.0)
        self.assertEqual(throttle.lockouts, 1)

    def test_the_lock_expires_and_the_window_slides(self):
        throttle = self.make()
        for _ in range(3):
            throttle.failure("a")
        self.now += 119.0
        self.assertGreater(throttle.locked_for("a"), 0.0)
        self.now += 2.0
        self.assertEqual(throttle.locked_for("a"), 0.0)
        # Two failures 61 seconds apart are not two failures in one window.
        throttle.failure("b")
        self.now += 61.0
        throttle.failure("b")
        self.assertFalse(throttle.failure("b"))

    def test_a_successful_login_clears_the_slate(self):
        throttle = self.make()
        throttle.failure("a")
        throttle.failure("a")
        throttle.success("a")
        self.assertFalse(throttle.failure("a"))
        self.assertFalse(throttle.failure("a"))

    def test_the_table_is_bounded(self):
        throttle = self.make(max_tracked=10)
        for i in range(50):
            self.now += 0.01
            throttle.failure(f"10.0.{i // 256}.{i % 256}")
        self.assertLessEqual(throttle.snapshot()["tracked"], 10)

    def test_the_handler_consults_the_lock_before_reading_credentials(self):
        locked = DASHBOARD.index("self.logins.locked_for(")
        auth = DASHBOARD.index("if not self._authorized(headers):")
        failed = DASHBOARD.index("self.logins.failure(")
        cleared = DASHBOARD.index("self.logins.success(")
        self.assertLess(locked, auth)
        self.assertLess(auth, failed)
        self.assertLess(failed, cleared)
        self.assertIn('"Retry-After"', DASHBOARD)
        # Keyed by the socket peer, never by a header the peer wrote.
        self.assertIn('get_extra_info("peername")', DASHBOARD)
        self.assertNotIn("x-forwarded-for", DASHBOARD.lower())

    def test_the_knobs_are_documented(self):
        for key in ("DASHBOARD_LOGIN_MAX_FAILURES", "DASHBOARD_LOGIN_WINDOW_SECONDS", "DASHBOARD_LOGIN_LOCKOUT_SECONDS", "DASHBOARD_ALLOWED_ORIGINS"):
            self.assertIn(key, ENV_EXAMPLE, key)
            self.assertIn(key, DASHBOARD, key)


class OriginCheckTests(unittest.TestCase):
    def setUp(self):
        from app.dashboard.server import origin_allowed

        self.allowed = origin_allowed

    def test_same_origin_posts_pass_and_cross_origin_posts_do_not(self):
        host = {"host": "127.0.0.1:8090"}
        self.assertTrue(self.allowed({**host, "origin": "http://127.0.0.1:8090"}))
        self.assertTrue(self.allowed({**host, "origin": "HTTP://127.0.0.1:8090"}))
        self.assertFalse(self.allowed({**host, "origin": "http://evil.example"}))
        self.assertFalse(self.allowed({**host, "origin": "http://127.0.0.1:8091"}))
        self.assertFalse(self.allowed({**host, "origin": "null"}))

    def test_a_request_without_origin_is_not_a_browser_page(self):
        self.assertTrue(self.allowed({"host": "127.0.0.1:8090"}))
        self.assertTrue(self.allowed({"host": "127.0.0.1:8090", "sec-fetch-site": "none"}))
        self.assertFalse(self.allowed({"host": "127.0.0.1:8090", "sec-fetch-site": "cross-site"}))

    def test_an_operator_listed_origin_survives_a_proxy_that_rewrites_host(self):
        headers = {"host": "127.0.0.1:8090", "origin": "https://gm.example.lan"}
        self.assertFalse(self.allowed(headers))
        self.assertTrue(self.allowed(headers, ("https://gm.example.lan",)))

    def test_every_mutation_path_is_behind_the_check(self):
        origin = DASHBOARD.index("if not origin_allowed(headers, self.settings.allowed_origins):")
        self.assertLess(DASHBOARD.index('if method == "POST":'), origin)
        self.assertLess(origin, DASHBOARD.index("await self.admin.run(action, payload)"))


class BindDefaultTests(unittest.TestCase):
    def test_bare_metal_defaults_are_loopback(self):
        with patch.dict(os.environ, bot_env(), clear=True):
            self.assertEqual(Settings.from_env().health_host, "127.0.0.1")
        from app.dashboard.server import DashboardSettings

        env = {
            "DASHBOARD_TOKEN": "a-very-long-private-dashboard-token",
            "GAME_ENGINE_URL": "http://127.0.0.1:8081",
            "ENGINE_AUTH_TOKEN": TOKEN,
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(DashboardSettings.from_env().host, "127.0.0.1")

    def test_compose_asks_for_all_interfaces_explicitly_where_a_neighbour_needs_it(self):
        self.assertIn('HEALTH_HOST: "0.0.0.0"', COMPOSE)
        self.assertIn('DASHBOARD_HOST: "0.0.0.0"', COMPOSE)
        # The bot's listener is still never published on the host, and it is
        # not on 8080, which a QNAP's QTS admin already owns.
        self.assertNotRegex(COMPOSE, r'ports:\s*\n\s*-\s*"[^"]*808[02]')
        live_compose = "\n".join(line for line in COMPOSE.splitlines() if not line.strip().startswith("#"))
        self.assertNotIn("8080", live_compose)
        with patch.dict(os.environ, bot_env(), clear=True):
            self.assertEqual(Settings.from_env().health_port, 8082)

    def test_the_env_example_binds_all_interfaces_as_a_stated_choice(self):
        # startup.sh copies .env.example to .env, so this file is the operator's
        # effective default. The operator chose all interfaces (a headless NAS
        # must answer a PC on the LAN); the test holds that the choice is
        # explained beside its narrowing alternative in the configuration
        # reference, and that the application's own default for an unset
        # variable stays loopback (the test above).
        for key in ("DASHBOARD_BIND_ADDRESS", "DASHBOARD_HOST", "HEALTH_HOST"):
            self.assertIn(f"{key}=0.0.0.0", ENV_EXAMPLE, key)
            at = CONFIGURATION.index(f"`{key}`")
            window = CONFIGURATION[at:at + 1200]
            self.assertIn("127.0.0.1", window, f"{key}: the loopback alternative is not documented beside it")

    def test_the_env_example_is_keys_and_separators_only(self):
        # The operator's rule (v0.29.0): the file you copy to .env carries the
        # keys, their defaults and the section separators, nothing else. The
        # explanations live in docs/CONFIGURATION.md, which the header names.
        separator = re.compile(r"^# [=-]{3,}|^# --- .+ -{3,}$")
        assignment = re.compile(r"^#? ?[A-Z][A-Z0-9_]*=")
        inside_title = False
        for number, line in enumerate(ENV_EXAMPLE.splitlines(), start=1):
            if not line.strip():
                continue
            if re.match(r"^# ={3,}", line):
                inside_title = not inside_title
                continue
            if separator.match(line) or assignment.match(line):
                continue
            if line.startswith("#") and inside_title:
                continue  # the section's name, between its two rules
            self.fail(f".env.example line {number} is prose, not a key or a separator: {line!r}")
        self.assertIn("docs/CONFIGURATION.md", ENV_EXAMPLE)
        self.assertTrue((PROJECT_ROOT / "docs" / "CONFIGURATION.md").exists())


class SupplyChainTests(unittest.TestCase):
    PINNED = ("discord.py", "openai", "aiosqlite", "python-dotenv", "httpx")

    @staticmethod
    def canonical(name: str) -> str:
        # PEP 503: the lock spells discord.py as discord-py.
        return re.sub(r"[-_.]+", "-", name).lower()

    def requirement_pins(self) -> dict[str, str]:
        pins = {}
        for line in REQUIREMENTS.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)", line)
            if m:
                pins[self.canonical(m.group(1))] = m.group(2)
        return pins

    def test_the_load_bearing_packages_are_pinned_exactly(self):
        pins = self.requirement_pins()
        for name in self.PINNED:
            self.assertIn(self.canonical(name), pins, f"{name} is a range, not a pin")

    def test_the_lock_pins_every_requirement_with_hashes(self):
        self.assertTrue(LOCK_PATH.exists(), "requirements.lock is missing; run make lock")
        lock = LOCK_PATH.read_text(encoding="utf-8")
        locked = {self.canonical(n): v for n, v in re.findall(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+) \\$", lock, re.M)}
        for name, version in self.requirement_pins().items():
            self.assertEqual(locked.get(name), version, f"{name}: requirements.txt says {version}, lock says {locked.get(name)}")
        self.assertIn("google-genai", locked, "the unbounded dependency is still pinned by the lock")
        # Every pinned entry carries at least one hash, and nothing is unhashed.
        entries = re.findall(r"^([A-Za-z0-9_.\-]+==[^\s\\]+)((?:\s*\\\n\s*--hash=sha256:[0-9a-f]{64})*)", lock, re.M)
        self.assertGreater(len(entries), 10)
        for entry, hashes in entries:
            self.assertIn("--hash=sha256:", hashes, f"{entry} has no hash")

    def test_the_image_installs_the_lock_under_require_hashes(self):
        self.assertIn("COPY requirements.txt requirements.lock", DOCKERFILE)
        self.assertRegex(DOCKERFILE, r"pip install [^\n]*--require-hashes[^\n]*-r requirements\.lock")
        self.assertNotRegex(DOCKERFILE, r"pip install [^\n]*-r requirements\.txt")

    def test_every_base_image_is_pinned_by_digest(self):
        for name, text in (("Dockerfile", DOCKERFILE), ("go_core/Dockerfile", GO_DOCKERFILE)):
            froms = re.findall(r"^FROM (\S+)", text, re.M)
            self.assertTrue(froms, name)
            for image in froms:
                self.assertRegex(image, r"@sha256:[0-9a-f]{64}$", f"{name}: {image} is not digest-pinned")


class CITests(unittest.TestCase):
    def test_the_go_suite_runs_under_the_race_detector(self):
        self.assertRegex(CI, r"go test -race \./\.\.\.")


if __name__ == "__main__":
    unittest.main()
