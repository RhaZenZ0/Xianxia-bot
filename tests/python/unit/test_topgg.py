"""`app/ops/topgg.py`: the listing check, and the ways it is allowed to fail.

`/vote` used to pay on trust because nothing could check a vote. Top.gg's v1
API can, on an outbound call, and this module is that call. What is worth
pinning is not the happy path - it is the failure behaviour, because this
module's whole design rests on one asymmetry:

    a Top.gg that answers "no live vote" refuses a claim;
    a Top.gg that does not answer pays it.

Every network fault, every 5xx, every rate limit and every rotated token has to
land on the paying side of that line. A regression that turned one of them into
a refusal would take gifts away from players for someone else's outage, and it
would do it silently - there is no exception to notice, only a quieter grant
rate. So each one gets a test.
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

import httpx

from app.ops.topgg import (
    NOT_VOTED,
    UNCONFIGURED,
    UNKNOWN,
    VOTED,
    TopggClient,
    VoteCheck,
    _expires_unix,
)


def run(coro):
    return asyncio.run(coro)


def client_answering(handler) -> TopggClient:
    """A client wired to a fake transport, so no test touches the network."""
    transport = httpx.MockTransport(handler)
    session = httpx.AsyncClient(
        transport=transport,
        base_url="https://top.gg/api",
        headers={"Authorization": "Bearer test-token"},
    )
    return TopggClient("test-token", client=session)


def answering(status: int, json_body=None, *, text: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=json_body if json_body is not None else {})
    return handler


def in_hours(hours: float) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


class AVoteIsLiveUntilItsExpiryPasses(unittest.TestCase):
    """`expires_at` is the whole verdict.

    Top.gg expires a vote twelve hours after it is cast, which is the cadence
    the gift already renewed on, so the two need no reconciling - the field is
    read directly rather than compared against a cooldown kept here.
    """

    def test_a_future_expiry_is_a_vote(self):
        check = run(client_answering(answering(200, {"expires_at": in_hours(6), "weight": 1})).vote_state(7))
        self.assertEqual(check.state, VOTED)
        self.assertTrue(check.verified)
        self.assertTrue(check.trusted)
        self.assertGreater(check.expires_unix, 0)

    def test_a_past_expiry_is_a_vote_already_spent(self):
        check = run(client_answering(answering(200, {"expires_at": in_hours(-1)})).vote_state(7))
        self.assertEqual(check.state, NOT_VOTED)
        self.assertFalse(check.verified)
        self.assertFalse(check.trusted, "an expired vote must not pay")

    def test_a_404_is_the_documented_no_vote_on_record(self):
        check = run(client_answering(answering(404)).vote_state(7))
        self.assertEqual(check.state, NOT_VOTED)
        self.assertFalse(check.trusted)

    def test_an_answer_with_no_expiry_at_all_is_no_vote(self):
        check = run(client_answering(answering(200, {"weight": 1})).vote_state(7))
        self.assertEqual(check.state, NOT_VOTED)

    def test_a_wrapped_object_is_read_too(self):
        # The SDK reads the object flat, so flat is what this expects - but an
        # answer that nests it under `data` must not read as "no vote".
        check = run(client_answering(answering(200, {"data": {"expires_at": in_hours(3)}})).vote_state(7))
        self.assertEqual(check.state, VOTED)

    def test_a_weight_that_is_not_a_number_does_not_lose_the_vote(self):
        check = run(client_answering(answering(200, {"expires_at": in_hours(3), "weight": "??"})).vote_state(7))
        self.assertEqual(check.state, VOTED)
        self.assertEqual(check.weight, 1)


class EveryWayOfNotKnowingPays(unittest.TestCase):
    """The asymmetry this module exists to hold.

    Each of these is a Top.gg that could not give a verdict. None of them may
    become a refusal, because the player did nothing wrong in any of them and
    the engine's twelve-hour cooldown still bounds what the outage can cost.
    """

    def assert_pays(self, check: VoteCheck, *, why: str):
        self.assertEqual(check.state, UNKNOWN, why)
        self.assertTrue(check.trusted, f"{why}: must still pay the gift")
        self.assertFalse(check.verified, f"{why}: must not be recorded as verified")

    def test_a_network_failure_pays(self):
        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)
        self.assert_pays(run(client_answering(explode).vote_state(7)), why="connect error")

    def test_a_timeout_pays(self):
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)
        self.assert_pays(run(client_answering(slow).vote_state(7)), why="timeout")

    def test_a_rate_limit_pays(self):
        self.assert_pays(run(client_answering(answering(429)).vote_state(7)), why="429")

    def test_a_server_error_pays(self):
        self.assert_pays(run(client_answering(answering(503)).vote_state(7)), why="503")

    def test_a_refused_token_pays_and_is_logged_as_an_error(self):
        # The one failure an operator must act on, so it is loud - but a
        # rotated token must not cost players their gifts while it is noticed.
        for status in (401, 403):
            with self.subTest(status=status):
                with self.assertLogs("xianxia", level="ERROR") as captured:
                    check = run(client_answering(answering(status)).vote_state(7))
                self.assert_pays(check, why=f"HTTP {status}")
                self.assertIn("TOPGG_TOKEN", "\n".join(captured.output))

    def test_an_answer_that_is_not_json_pays(self):
        self.assert_pays(run(client_answering(answering(200, text="<html>nope</html>")).vote_state(7)), why="not JSON")

    def test_an_answer_that_is_not_an_object_pays(self):
        self.assert_pays(run(client_answering(answering(200, ["surprise"])).vote_state(7)), why="a list")

    def test_an_unreadable_expiry_does_not_raise(self):
        # A shape fromisoformat cannot read is no expiry, which reads as no
        # live vote - never as a traceback out of a button press.
        self.assertEqual(_expires_unix({"expires_at": "the day after tomorrow"}), 0)
        self.assertEqual(_expires_unix({}), 0)


class WithNoTokenNothingIsAsked(unittest.TestCase):
    def test_an_unconfigured_client_answers_without_a_request(self):
        def forbidden(request: httpx.Request) -> httpx.Response:
            raise AssertionError("an unconfigured client must not call Top.gg")
        client = TopggClient("", client=httpx.AsyncClient(transport=httpx.MockTransport(forbidden)))
        check = run(client.vote_state(7))
        self.assertEqual(check.state, UNCONFIGURED)
        self.assertTrue(check.trusted, "no token means the old trust-based claim")
        self.assertFalse(check.verified)
        self.assertFalse(client.enabled)
        self.assertFalse(run(client.post_metrics(server_count=3)))


class TheMetricsPostSendsOnlyWhatThisBotKnows(unittest.TestCase):
    def test_it_patches_the_project_and_carries_the_counts(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            import json
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={})

        self.assertTrue(run(client_answering(handler).post_metrics(server_count=12, shard_count=2)))
        self.assertEqual(seen["method"], "PATCH")
        self.assertEqual(seen["url"], "https://top.gg/api/v1/projects/@me/metrics")
        self.assertEqual(seen["auth"], "Bearer test-token")
        self.assertEqual(seen["body"], {"server_count": 12, "shard_count": 2})

    def test_the_counts_this_bot_does_not_have_are_left_out(self):
        # MetricsPayload also carries member/online/player counts. They belong
        # to server and game listings; sending them as zeros would publish a
        # zero over whatever the listing already shows.
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={})

        run(client_answering(handler).post_metrics(server_count=1))
        self.assertEqual(set(seen), {"server_count"})

    def test_a_failure_is_a_false_rather_than_a_raise(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.assertFalse(run(client_answering(answering(status)).post_metrics(server_count=1)))

        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)
        self.assertFalse(run(client_answering(explode).post_metrics(server_count=1)))


class TheRequestIsTheOneTheSdkMakes(unittest.TestCase):
    """Pinned against top.gg's own Go SDK, which is where these came from.

    The v0 API took a bare token and addressed the bot by id. Both changed in
    v1, and getting either wrong is a 401 that only shows up in production.
    """

    def test_the_vote_check_addresses_the_project_as_me_with_a_bearer_token(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(404)

        run(client_answering(handler).vote_state(1234567890))
        self.assertEqual(seen["url"], "https://top.gg/api/v1/projects/@me/votes/1234567890")
        self.assertEqual(seen["auth"], "Bearer test-token")


if __name__ == "__main__":
    unittest.main()
