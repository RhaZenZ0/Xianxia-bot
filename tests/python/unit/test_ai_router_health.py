import asyncio
import ssl
import unittest
from types import SimpleNamespace

from tests.support import install_openai_shim

install_openai_shim()

from app.ai.ai_router import (
    AITaskRouter,
    DEFAULT_DYNAMIC_FREE_MODEL,
    DEFAULT_EPIC_FALLBACK_MODEL,
    DEFAULT_EPIC_MODEL,
    DEFAULT_ROUTINE_FALLBACK_MODEL,
    DYNAMIC_FREE_MIN_OUTPUT_TOKENS,
    RouteLimiter,
    NarrationTier,
    OpenRouterRequestLimiter,
    _looks_like_scratchpad,
    _salvage_narration,
    _response_provider,
    DEFAULT_ROUTINE_MODEL,
    MAX_FAILURE_COOLDOWN_SECONDS,
    _looks_like_tls_failure,
    _retry_after_seconds,
    _validate_generated_text,
)


class _Message(SimpleNamespace):
    """A chat message the way the SDK hands it over, reasoning field included."""


def _reply(content=None, reasoning=None):
    return _Message(content=content, reasoning=reasoning)


class _RateLimit(Exception):
    def __init__(self, retry_after=None, status_code=429):
        super().__init__("Error code: 429 - rate limited")
        self.status_code = status_code
        headers = {} if retry_after is None else {"retry-after": retry_after}
        self.response = SimpleNamespace(status_code=status_code, headers=headers)


class _FakeCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        if isinstance(payload, _Message):
            return SimpleNamespace(choices=[SimpleNamespace(message=payload)])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=payload))])


class _FakeClient:
    def __init__(self, payloads):
        self.completions = _FakeCompletions(payloads)
        self.chat = SimpleNamespace(completions=self.completions)


def _router(payloads, **kwargs):
    kwargs.setdefault("failure_cooldown_seconds", 1.0)
    # A named second hop, so these fixtures exercise a three-deep chain
    # (primary -> named fallback -> dynamic router) regardless of what the
    # shipped default happens to be. Since v0.26.1 the default fallback slot is
    # empty; the shape of the DEFAULT chain is gated by AITaskRouterTests rather
    # than here, so pinning a slug in the fixture weakens nothing.
    kwargs.setdefault("routine_fallback_model", "fixture-vendor/fixture-model:free")
    kwargs.setdefault("epic_fallback_model", "fixture-vendor/fixture-model:free")
    router = AITaskRouter(api_key="test-key", **kwargs)
    router.client = _FakeClient(payloads)
    return router


def _generate(router, **kwargs):
    params = {
        "tier": NarrationTier.ROUTINE,
        "system_prompt": "sys",
        "prompt": "prompt",
        "max_output_tokens": 64,
    }
    params.update(kwargs)
    return asyncio.run(router.generate(**params))


class LimiterCounterTests(unittest.TestCase):
    def test_grants_and_rejections_are_counted(self):
        limiter = OpenRouterRequestLimiter(max_requests_per_minute=2)
        self.assertTrue(asyncio.run(limiter.try_acquire()))
        self.assertTrue(asyncio.run(limiter.try_acquire()))
        self.assertFalse(asyncio.run(limiter.try_acquire()))
        snapshot = limiter.snapshot()
        self.assertEqual(snapshot["granted"], 2)
        self.assertEqual(snapshot["rejected"], 1)
        self.assertEqual(snapshot["max_requests_per_minute"], 2)


class RouterHealthTests(unittest.TestCase):
    def test_success_is_recorded_against_the_model_that_served(self):
        router = _router(["narration"])
        result = _generate(router)
        snapshot = router.health_snapshot()
        row = next(r for r in snapshot["models"] if r["model"] == result.model)
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["successes"], 1)
        self.assertEqual(row["failures"], 0)
        self.assertGreater(row["last_success_at"], 0.0)
        self.assertEqual(snapshot["tiers"]["routine"]["served"], 1)

    def test_a_failed_route_records_the_error_and_the_cooldown(self):
        router = _router([RuntimeError("provider exploded"), "narration"])
        _generate(router)
        snapshot = router.health_snapshot()
        failed = [r for r in snapshot["models"] if r["failures"]]
        self.assertEqual(len(failed), 1)
        self.assertIn("provider exploded", failed[0]["last_error"])
        self.assertTrue(failed[0]["cooling_down"])
        self.assertGreater(failed[0]["cooldown_remaining_seconds"], 0.0)

    def test_a_cooling_route_is_counted_as_skipped_not_attempted(self):
        # Without this distinction a cooling-down route looks healthy in the panel
        # (zero failures) while actually serving nothing.
        router = _router([RuntimeError("boom"), "first", "second"])
        _generate(router)
        _generate(router)
        snapshot = router.health_snapshot()
        cooled = next(r for r in snapshot["models"] if r["failures"])
        self.assertEqual(cooled["attempts"], 1)
        self.assertEqual(cooled["skipped_cooling"], 1)

    def test_exhausted_chain_is_counted_per_tier(self):
        router = _router([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
        with self.assertRaises(RuntimeError):
            _generate(router)
        self.assertEqual(router.health_snapshot()["tiers"]["routine"]["exhausted"], 1)

    def test_rate_limit_refusal_is_counted_per_tier(self):
        router = _router(["a", "b"], max_requests_per_minute=1)
        _generate(router)
        with self.assertRaises(RuntimeError):
            _generate(router)
        snapshot = router.health_snapshot()
        self.assertEqual(snapshot["tiers"]["routine"]["rate_limited"], 1)
        self.assertEqual(snapshot["limiter"]["rejected"], 1)

    def test_snapshot_never_contains_the_api_key_or_a_prompt(self):
        router = _router(["narration"])
        _generate(router)
        blob = repr(router.health_snapshot())
        self.assertNotIn("test-key", blob)
        self.assertNotIn("prompt", blob)

    def test_snapshot_is_readable_before_any_request(self):
        snapshot = AITaskRouter(api_key="test-key").health_snapshot()
        self.assertEqual(snapshot["models"], [])
        self.assertTrue(snapshot["enabled"])
        self.assertIn("routine", snapshot["chains"])


class LeakGuardTests(unittest.TestCase):
    def test_narration_still_rejects_implementation_leaks_by_default(self):
        with self.assertRaises(ValueError):
            _validate_generated_text("The game engine returned an error.")

    def test_generate_defaults_to_the_player_facing_guard(self):
        router = _router(["Your system prompt says otherwise.", "clean narration"])
        result = _generate(router)
        self.assertEqual(result.text, "clean narration")

    def test_leak_guard_can_be_disabled_for_administrator_analysis(self):
        text = _validate_generated_text("The game engine returned an error.", leak_guard=False)
        self.assertIn("game engine", text)

    def test_generate_honours_leak_guard_false(self):
        router = _router(["Bugs — the go engine 500'd."])
        result = _generate(router, leak_guard=False)
        self.assertIn("go engine", result.text)

    def test_empty_output_is_still_rejected_with_the_guard_off(self):
        with self.assertRaises(ValueError):
            _validate_generated_text("   ", leak_guard=False)


class TemperatureTests(unittest.TestCase):
    def test_explicit_temperature_overrides_the_tier_default(self):
        router = _router(["narration"])
        _generate(router, temperature=0.2)
        self.assertEqual(router.client.completions.calls[0]["temperature"], 0.2)

    def test_tier_default_is_used_when_none_is_given(self):
        router = _router(["narration"])
        _generate(router)
        self.assertEqual(router.client.completions.calls[0]["temperature"], 0.78)

    def test_out_of_range_temperature_is_clamped(self):
        router = _router(["narration"])
        _generate(router, temperature=9.0)
        self.assertEqual(router.client.completions.calls[0]["temperature"], 2.0)


class TLSDetectionTests(unittest.TestCase):
    """openai 3.x verifies TLS against the OS trust store, not certifi.

    A broken trust store fails every route, and narration degrades to procedural
    prose without raising anywhere - the operator sees nothing. These make the
    failure nameable so /admin can shout about it.
    """

    def test_a_real_ssl_error_is_detected(self):
        self.assertTrue(_looks_like_tls_failure(ssl.SSLError("handshake failed")))

    def test_a_certificate_verify_failure_is_detected_by_message(self):
        exc = RuntimeError("CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate")
        self.assertTrue(_looks_like_tls_failure(exc))

    def test_a_wrapped_ssl_error_is_found_through_the_cause_chain(self):
        # The openai client wraps transport errors, so the SSLError is never the
        # outermost exception in practice.
        try:
            try:
                raise ssl.SSLCertVerificationError("self-signed certificate")
            except ssl.SSLError as inner:
                raise RuntimeError("Connection error.") from inner
        except RuntimeError as outer:
            self.assertTrue(_looks_like_tls_failure(outer))

    def test_an_ordinary_failure_is_not_mistaken_for_tls(self):
        for message in ("rate limited", "model unavailable", "results were empty", "timeout"):
            self.assertFalse(_looks_like_tls_failure(RuntimeError(message)), message)

    def test_none_is_not_a_tls_failure(self):
        self.assertFalse(_looks_like_tls_failure(None))

    def test_a_timeout_mid_handshake_is_a_timeout_not_a_certificate_problem(self):
        # asyncio.wait_for cancels the in-flight request, and a request cancelled
        # during the handshake leaves the half-finished SSL exception in the
        # context chain. Reporting that as a trust-store failure sends the
        # operator to check ca-certificates for an upstream they simply cannot
        # reach in time.
        try:
            try:
                try:
                    raise ssl.SSLWantReadError("The operation did not complete (read)")
                except ssl.SSLWantReadError:
                    raise asyncio.CancelledError()
            except asyncio.CancelledError:
                raise TimeoutError()
        except TimeoutError as exc:
            self.assertFalse(_looks_like_tls_failure(exc))

    def test_a_cyclic_exception_chain_terminates(self):
        first = RuntimeError("a")
        second = RuntimeError("b")
        first.__cause__ = second
        second.__cause__ = first
        self.assertFalse(_looks_like_tls_failure(first))

    def test_tls_failures_are_counted_and_flagged_in_the_snapshot(self):
        router = _router([ssl.SSLError("certificate verify failed"), "narration"])
        _generate(router)
        snapshot = router.health_snapshot()
        self.assertEqual(snapshot["tls_failures"], 1)
        flagged = [r for r in snapshot["models"] if r["last_error_looks_like_tls"]]
        self.assertEqual(len(flagged), 1)

    def test_an_ordinary_failure_does_not_raise_the_tls_counter(self):
        router = _router([RuntimeError("model unavailable"), "narration"])
        _generate(router)
        snapshot = router.health_snapshot()
        self.assertEqual(snapshot["tls_failures"], 0)
        self.assertFalse(any(r["last_error_looks_like_tls"] for r in snapshot["models"]))

    def test_the_tls_error_is_logged_loudly_exactly_once(self):
        router = _router([ssl.SSLError("cert bad"), ssl.SSLError("cert bad"), "ok"])
        with self.assertLogs("xianxia.ai_router", level="ERROR") as captured:
            _generate(router)
        self.assertEqual(len(captured.records), 1)
        self.assertIn("AI_TLS_FAILURE", captured.output[0])
        self.assertIn("ca-certificates", captured.output[0])


class DailyBudgetTests(unittest.TestCase):
    """OpenRouter free tier: 20 req/min AND 50 req/day under $10 lifetime credits.

    Only the per-minute half was tracked. In production 11 narrations cost 15
    upstream attempts, because every failed route walks to the next one and each
    walk spends a daily slot.
    """

    def test_the_daily_allowance_is_enforced_independently_of_the_minute(self):
        limiter = OpenRouterRequestLimiter(max_requests_per_minute=100, max_requests_per_day=3)
        for _ in range(3):
            self.assertTrue(asyncio.run(limiter.try_acquire()))
        self.assertFalse(asyncio.run(limiter.try_acquire()))
        snapshot = limiter.snapshot()
        self.assertTrue(snapshot["daily_exhausted"])
        self.assertEqual(snapshot["daily_rejected"], 1)
        self.assertEqual(snapshot["remaining_today"], 0)

    def test_remaining_today_counts_down(self):
        limiter = OpenRouterRequestLimiter(max_requests_per_minute=100, max_requests_per_day=10)
        asyncio.run(limiter.try_acquire())
        asyncio.run(limiter.try_acquire())
        self.assertEqual(limiter.snapshot()["used_today"], 2)
        self.assertEqual(limiter.snapshot()["remaining_today"], 8)

    def test_a_spent_budget_stops_the_chain_with_a_named_error(self):
        # Walking all three routes to discover the budget is gone costs three
        # refusals instead of one.
        router = _router(["a", "b", "c", "d"], max_requests_per_day=2)
        _generate(router)
        _generate(router)
        with self.assertRaises(RuntimeError) as caught:
            _generate(router)
        self.assertIn("daily free-tier budget spent", str(caught.exception))
        self.assertIn("2/2", str(caught.exception))

    def test_the_budget_appears_in_the_health_snapshot(self):
        router = _router(["a"], max_requests_per_day=25)
        _generate(router)
        limiter = router.health_snapshot()["limiter"]
        self.assertEqual(limiter["max_requests_per_day"], 25)
        self.assertEqual(limiter["used_today"], 1)
        self.assertFalse(limiter["daily_exhausted"])

    def test_the_default_matches_the_real_free_tier_floor(self):
        # 50/day is what an account under $10 of lifetime credits actually gets.
        self.assertEqual(OpenRouterRequestLimiter().max_requests_per_day, 50)


class RetryAfterTests(unittest.TestCase):
    """A blanket cooldown is a guess; the provider sends the real number."""

    def test_a_numeric_retry_after_header_is_used(self):
        self.assertEqual(_retry_after_seconds(_RateLimit(retry_after="7")), 7.0)

    def test_a_missing_header_falls_back_to_the_default_cooldown(self):
        self.assertIsNone(_retry_after_seconds(_RateLimit()))

    def test_an_http_date_retry_after_is_ignored_rather_than_guessed(self):
        self.assertIsNone(_retry_after_seconds(_RateLimit(retry_after="Wed, 21 Oct 2026 07:28:00 GMT")))

    def test_a_nonsense_retry_after_is_ignored(self):
        for value in ("0", "-5", "", "soon"):
            self.assertIsNone(_retry_after_seconds(_RateLimit(retry_after=value)), value)

    def test_an_absurd_retry_after_is_clamped(self):
        self.assertEqual(_retry_after_seconds(_RateLimit(retry_after="99999")), 300.0)

    def test_an_exception_with_no_response_does_not_raise(self):
        self.assertIsNone(_retry_after_seconds(RuntimeError("plain")))

    def test_the_cooldown_honours_the_hint_instead_of_the_multiplier(self):
        router = _router([_RateLimit(retry_after="3"), "narration"], failure_cooldown_seconds=20.0)
        _generate(router)
        row = next(r for r in router.health_snapshot()["models"] if r["failures"])
        # 20s * 3 for a 429 would be 60; the hint says 3.
        self.assertLessEqual(row["cooldown_remaining_seconds"], 3.0)

    def test_without_a_hint_the_429_multiplier_still_applies(self):
        router = _router([_RateLimit(), "narration"], failure_cooldown_seconds=20.0)
        _generate(router)
        row = next(r for r in router.health_snapshot()["models"] if r["failures"])
        self.assertGreater(row["cooldown_remaining_seconds"], 30.0)


class EmptyResponseTests(unittest.TestCase):
    """openrouter/free returned empty content 6 times out of 6 in production.

    It is a random router over a free pool that is now mostly reasoning models,
    and routine narration asks for ~180 tokens - all of which a reasoning model
    spends before writing any content.
    """

    def test_prose_in_the_reasoning_field_is_salvaged(self):
        router = _router([_reply(content="", reasoning="Mist coils over the marsh water.")])
        result = _generate(router)
        self.assertEqual(result.text, "Mist coils over the marsh water.")
        row = next(r for r in router.health_snapshot()["models"] if r["empty_responses"])
        self.assertEqual(row["empty_responses"], 1)
        self.assertEqual(row["reasoning_salvaged"], 1)

    def test_scratchpad_in_the_reasoning_field_is_refused(self):
        # "Okay, the user wants..." is worse than the procedural fallback.
        router = _router([
            _reply(content="", reasoning="Okay, the user wants a marsh description. Let me set the scene."),
            "clean narration",
        ])
        self.assertEqual(_generate(router).text, "clean narration")

    def test_real_content_is_preferred_over_reasoning(self):
        router = _router([_reply(content="The marsh exhales.", reasoning="thinking")])
        self.assertEqual(_generate(router).text, "The marsh exhales.")
        self.assertEqual(router.health_snapshot()["models"][0]["empty_responses"], 0)

    def test_empty_responses_are_counted_apart_from_rate_limits(self):
        router = _router([_reply(content="", reasoning=""), "narration"])
        _generate(router)
        rows = {r["model"]: r for r in router.health_snapshot()["models"]}
        empty = [r for r in rows.values() if r["empty_responses"]]
        self.assertEqual(len(empty), 1)

    def test_a_route_that_never_worked_is_flagged(self):
        router = _router([RuntimeError("boom"), "narration"])
        _generate(router)
        flagged = [r for r in router.health_snapshot()["models"] if r["never_succeeded"]]
        self.assertEqual(len(flagged), 1)

    def test_a_working_route_is_not_flagged(self):
        router = _router(["narration"])
        result = _generate(router)
        row = next(r for r in router.health_snapshot()["models"] if r["model"] == result.model)
        self.assertFalse(row["never_succeeded"])

    def test_scratchpad_detection(self):
        for text in (
            "Okay, let me think about this.",
            "The user wants a description.",
            "I should describe the marsh.",
            "As an AI, I will narrate.",
        ):
            self.assertTrue(_looks_like_scratchpad(text), text)
        for text in (
            "Mist coils over the black water and the reeds bend without wind.",
            "A heron lifts from the shallows, startled by nothing visible.",
        ):
            self.assertFalse(_looks_like_scratchpad(text), text)


class DynamicRouteTokenTests(unittest.TestCase):
    def test_the_dynamic_free_router_gets_room_to_answer(self):
        router = _router([RuntimeError("a"), RuntimeError("b"), "narration"])
        _generate(router, max_output_tokens=180)
        calls = router.client.completions.calls
        self.assertEqual(calls[0]["max_tokens"], 180)
        self.assertEqual(calls[-1]["model"], router.dynamic_free_model)
        self.assertGreaterEqual(calls[-1]["max_tokens"], DYNAMIC_FREE_MIN_OUTPUT_TOKENS)

    def test_a_named_route_keeps_the_requested_budget(self):
        router = _router(["narration"])
        _generate(router, max_output_tokens=190)
        self.assertEqual(router.client.completions.calls[0]["max_tokens"], 190)

    def test_a_larger_request_is_never_shrunk(self):
        router = _router([RuntimeError("a"), RuntimeError("b"), "narration"])
        _generate(router, max_output_tokens=2000)
        self.assertEqual(router.client.completions.calls[-1]["max_tokens"], 2000)


class RouteLimitTests(unittest.TestCase):
    """Provider caps are PER MODEL; the account-wide limiter cannot express them.

    Google allows Gemma 4 about 15 requests/minute and 1500/day per model. One
    global ceiling of 20/min is at once too high for a single route (15) and too
    low for the fleet (two Gemma routes = 30) - and production showed exactly
    that: both Gemma routes cooling independently while the account limiter had
    refused nothing at all.
    """

    def test_defaults_match_the_documented_gemma_free_tier(self):
        limiter = RouteLimiter("google/gemma-4-31b-it:free")
        self.assertEqual(limiter.per_minute, 15)
        self.assertEqual(limiter.per_day, 1500)

    def test_the_per_minute_window_refuses_past_its_own_ceiling(self):
        limiter = RouteLimiter("m", per_minute=2, per_day=100)
        self.assertTrue(limiter.try_acquire())
        self.assertTrue(limiter.try_acquire())
        self.assertFalse(limiter.try_acquire())
        self.assertEqual(limiter.snapshot()["refused_minute"], 1)

    def test_the_per_day_window_refuses_independently(self):
        limiter = RouteLimiter("m", per_minute=100, per_day=2)
        self.assertTrue(limiter.try_acquire())
        self.assertTrue(limiter.try_acquire())
        self.assertFalse(limiter.try_acquire())
        snapshot = limiter.snapshot()
        self.assertEqual(snapshot["refused_day"], 1)
        self.assertEqual(snapshot["refused_minute"], 0)

    def test_the_snapshot_names_the_ceiling_that_is_closest(self):
        limiter = RouteLimiter("m", per_minute=100, per_day=4)
        for _ in range(3):
            limiter.try_acquire()
        snapshot = limiter.snapshot()
        self.assertEqual(snapshot["binding"], "day")
        self.assertEqual(snapshot["headroom_percent"], 25.0)

    def test_each_route_gets_its_own_window(self):
        # The whole point: one route at its ceiling must not throttle another.
        router = _router(["a", "b"], route_requests_per_minute=1)
        _generate(router)
        _generate(router)
        limiters = {m: l.snapshot() for m, l in router._route_limiters.items()}
        self.assertEqual(len(limiters), 2, "both routes should have separate windows")
        self.assertTrue(all(l["used_minute"] == 1 for l in limiters.values()))

    def test_a_capped_route_is_skipped_not_failed(self):
        # Skipping costs nothing; calling out to be told 429 costs an attempt
        # against both the provider cap and the daily budget.
        router = _router(["first", "second"], route_requests_per_minute=1)
        _generate(router)
        result = _generate(router)
        snapshot = router.health_snapshot()
        first = next(r for r in snapshot["models"] if r["skipped_route_limit"])
        self.assertEqual(first["attempts"], 1, "no second upstream attempt was spent")
        self.assertEqual(first["skipped_route_limit"], 1)
        self.assertNotEqual(result.model, first["model"], "it moved to the next route")

    def test_a_capped_route_is_not_recorded_as_a_failure(self):
        router = _router(["first", "second"], route_requests_per_minute=1)
        _generate(router)
        _generate(router)
        skipped = next(r for r in router.health_snapshot()["models"] if r["skipped_route_limit"])
        self.assertEqual(skipped["failures"], 0)
        self.assertFalse(skipped["never_succeeded"])

    def test_route_limits_appear_in_the_snapshot(self):
        router = _router(["narration"])
        _generate(router)
        row = next(r for r in router.health_snapshot()["models"] if r["attempts"])
        self.assertIsNotNone(row["route_limits"])
        self.assertEqual(row["route_limits"]["per_minute"], 15)
        self.assertEqual(router.health_snapshot()["route_requests_per_day"], 1500)

    def test_the_account_ceiling_and_the_route_ceiling_are_different_numbers(self):
        # 20/min account-wide (OpenRouter) vs 15/min per route (the provider).
        router = _router(["narration"])
        snapshot = router.health_snapshot()
        self.assertEqual(snapshot["limiter"]["max_requests_per_minute"], 20)
        self.assertEqual(snapshot["route_requests_per_minute"], 15)


if __name__ == "__main__":
    unittest.main()


# The narration a player actually received in an expedition thread (v0.19.36).
LEAKED_THINKING = """Here's a thinking process:

1. **Analyze User Input:**
    * **Scene type:** Private expedition / player action resolution/continuation
    * **Canonical context provided:** Detailed world state, location, character stats, recent memories, NPC info, etc.
    * **Player action:** "Discover roads" (untrusted fictional action)
    * **Fixed roll information:** None. No mechanical roll to invent.
    * **Constraints:** Narrate only world/NPC response. Don't echo fixed rolls, add mechanics, choose another action, or reveal hidden simulator facts. Stay in-world.

2. **Identify Key Elements from Context:**
    * Character: Shen Zi, 18, sword cultivator, wind spiritual root
"""

CLEAN_NARRATION = (
    "Shen Zi follows the ridge until the trees thin. Below, a road: two ruts and a cairn, "
    "half-swallowed by ferns, older than any map she has read."
)


class ScratchpadInContentTests(unittest.TestCase):
    """A reasoning model put its thinking in `content`, not the reasoning field.

    The scratchpad guard only ran on the reasoning-salvage path (empty content),
    so this went straight through _validate_generated_text and into a player's
    expedition thread as the narration of "Discover roads". Every check here
    was red against v0.19.35.
    """

    def test_the_leaked_reply_is_recognised_as_scratchpad(self):
        self.assertTrue(_looks_like_scratchpad(LEAKED_THINKING))

    def test_the_leaked_reply_is_rejected_by_the_validator(self):
        with self.assertRaises(ValueError):
            _validate_generated_text(LEAKED_THINKING)

    def test_the_route_falls_through_to_the_next_model_and_counts_it(self):
        router = _router([LEAKED_THINKING, CLEAN_NARRATION])
        result = _generate(router)
        self.assertEqual(result.text, CLEAN_NARRATION)
        rows = {r["model"]: r for r in router.health_snapshot()["models"]}
        rejected = [r for r in rows.values() if r["scratchpad_rejected"]]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["scratchpad_rejected"], 1)
        self.assertNotEqual(rejected[0]["model"], result.model)

    def test_every_route_scratchpadding_ends_in_the_procedural_fallback(self):
        # The narrator catches the exhausted-chain error and uses its fallback;
        # what matters here is that no scratchpad text is ever returned.
        router = _router([LEAKED_THINKING, LEAKED_THINKING, LEAKED_THINKING])
        with self.assertRaises(RuntimeError):
            _generate(router)

    def test_a_reply_that_thinks_then_labels_its_narration_is_salvaged(self):
        text = LEAKED_THINKING + "\n\n**Final narration:**\n" + CLEAN_NARRATION
        self.assertEqual(_validate_generated_text(text), CLEAN_NARRATION)
        self.assertEqual(_salvage_narration(text), CLEAN_NARRATION)

    def test_think_tags_are_stripped_and_the_prose_kept(self):
        text = "<think>\nthe user wants roads. I should keep it short.\n</think>\n" + CLEAN_NARRATION
        self.assertEqual(_validate_generated_text(text), CLEAN_NARRATION)

    def test_an_unclosed_think_tag_is_all_thinking(self):
        # Neutral wording on purpose: nothing here trips the phrase patterns, so
        # only the unclosed-tag rule can reject it.
        with self.assertRaises(ValueError):
            _validate_generated_text("<think>roads bend east through the reeds toward the pass")

    def test_ordinary_narration_is_untouched(self):
        for text in (
            CLEAN_NARRATION,
            "Mist coils over the black water and the reeds bend without wind.",
            # Numbered prose and quoted dialogue must not trip the list-heading pattern.
            "1. The first cairn. 2. The second, older. Neither marked on any map.",
            '"You again," the ferryman says, and does not smile.',
            "The road forks. Left, the reed beds; right, a climb toward the wind-scoured pass.",
        ):
            with self.subTest(text=text):
                self.assertFalse(_looks_like_scratchpad(text))
                self.assertEqual(_validate_generated_text(text), text)

    def test_the_admin_monitor_report_is_not_subject_to_the_narration_guard(self):
        # The GM chat digest legitimately says things like "analyze the user
        # input"; it opts out of the player-facing guards with leak_guard=False.
        text = "Summary: analyze the user input for bug reports. The thinking process was sound."
        self.assertEqual(_validate_generated_text(text, leak_guard=False), text)

    def test_more_shapes_of_thinking_out_loud(self):
        for text in (
            "Here is my reasoning: the scene is a marsh.",
            "Analyzing the request: the player wants to discover roads.",
            "Fixed roll information: none provided.",
            "Scene type: private expedition.",
            # Isolates the "thinking process" pattern - nothing else matches this.
            "The thinking process was: describe the marsh at dusk.",
        ):
            with self.subTest(text=text):
                self.assertTrue(_looks_like_scratchpad(text))


class EscalatingBackoffTests(unittest.TestCase):
    """Two Gemma free routes sat at 0-for-15 all day on a fixed 60s cooldown.

    Every narration re-tried both before reaching the route that worked, and
    each retry spent one of the 50 daily free-tier slots: 23 narrations cost
    50 slots. A route that keeps failing now backs off harder each time.
    """

    def _row(self, router, model):
        return next(r for r in router.health_snapshot()["models"] if r["model"] == model)

    def test_consecutive_failures_double_the_cooldown(self):
        router = _router([], failure_cooldown_seconds=20.0)
        model = DEFAULT_ROUTINE_MODEL
        seen = []
        for _ in range(4):
            router._cooldown_until.pop(model, None)
            router._mark_failure(model, _RateLimit())
            seen.append(self._row(router, model)["cooldown_seconds"])
        # 20s x 3 for a 429 = 60s, then 120, 240, 480.
        self.assertEqual(seen, [60.0, 120.0, 240.0, 480.0])
        self.assertEqual(self._row(router, model)["consecutive_failures"], 4)

    def test_the_backoff_is_capped(self):
        router = _router([], failure_cooldown_seconds=20.0)
        model = DEFAULT_ROUTINE_MODEL
        for _ in range(12):
            router._cooldown_until.pop(model, None)
            router._mark_failure(model, _RateLimit())
        self.assertEqual(self._row(router, model)["cooldown_seconds"], MAX_FAILURE_COOLDOWN_SECONDS)

    def test_a_success_resets_the_streak(self):
        router = _router([_RateLimit(), _RateLimit(), "narration"], failure_cooldown_seconds=1.0)
        _generate(router)  # primary fails, fallback fails, openrouter/free serves
        primary = self._row(router, DEFAULT_ROUTINE_MODEL)
        self.assertEqual(primary["consecutive_failures"], 1)
        # Now the primary recovers on the next call.
        router._cooldown_until.clear()
        router.client = _FakeClient(["narration again"])
        _generate(router)
        primary = self._row(router, DEFAULT_ROUTINE_MODEL)
        self.assertEqual(primary["consecutive_failures"], 0)
        self.assertEqual(primary["cooldown_seconds"], 0.0)

    def test_a_provider_hint_is_still_escalated_on_a_streak(self):
        router = _router([], failure_cooldown_seconds=20.0)
        model = DEFAULT_ROUTINE_MODEL
        router._mark_failure(model, _RateLimit(retry_after="3"))
        self.assertEqual(self._row(router, model)["cooldown_seconds"], 3.0)
        router._cooldown_until.pop(model, None)
        router._mark_failure(model, _RateLimit(retry_after="3"))
        self.assertEqual(self._row(router, model)["cooldown_seconds"], 6.0)


class ProviderAndByokTests(unittest.TestCase):
    """"Google AI never responds and I imported my key into OpenRouter."

    OpenRouter tries an operator's own provider key first and silently falls
    back to its shared pool on any error, so from the bot there was no way to
    tell which one served a request. The completion carries a top-level
    `provider`; GET /generation?id= carries `is_byok`.
    """

    def test_provider_is_read_from_the_completion(self):
        self.assertEqual(_response_provider(SimpleNamespace(provider="Google AI Studio")), "Google AI Studio")
        self.assertEqual(_response_provider(SimpleNamespace(model_extra={"provider": "Google"})), "Google")
        self.assertEqual(_response_provider(SimpleNamespace()), "")

    def test_a_success_records_the_provider(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="narration"))],
            provider="Google AI Studio",
            id="",
        )
        router = _router([])
        router.client.completions.payloads = [completion]
        router.client.completions.create = _return(completion)
        result = _generate(router)
        row = next(r for r in router.health_snapshot()["models"] if r["model"] == result.model)
        self.assertEqual(row["last_provider"], "Google AI Studio")
        self.assertIsNone(row["byok"])  # no generation id, so no lookup

    def test_byok_lookup_records_whether_the_operators_key_was_used(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="narration"))],
            provider="Google AI Studio",
            id="gen-123",
        )
        router = _router([])
        router.client.completions.create = _return(completion)
        calls = []

        async def fake_lookup(model, response):
            calls.append((model, response.id))
            router._model_row(model)["byok"] = False
            router._model_row(model)["byok_checked_at"] = 1.0

        router._maybe_check_byok = fake_lookup
        result = _generate(router)
        self.assertEqual(calls, [(result.model, "gen-123")])
        row = next(r for r in router.health_snapshot()["models"] if r["model"] == result.model)
        self.assertIs(row["byok"], False)

    def test_byok_lookup_failure_never_breaks_narration(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="narration"))],
            id="gen-999",
        )
        router = _router([])
        router.client.completions.create = _return(completion)
        # api_key set, base_url unreachable: the lookup raises inside and is swallowed.
        router.base_url = "http://127.0.0.1:9"
        self.assertEqual(_generate(router).text, "narration")


# ---------------------------------------------------------------------------
# Chains, fallbacks and the reasoning switch (merged from test_ai_router.py, v0.20.3)
# ---------------------------------------------------------------------------

class AITaskRouterTests(unittest.TestCase):
    def test_default_free_fallback_chains(self):
        router = AITaskRouter(api_key=None)
        self.assertEqual(
            router.models_for(NarrationTier.ROUTINE),
            (DEFAULT_ROUTINE_MODEL, DEFAULT_DYNAMIC_FREE_MODEL),
        )
        self.assertEqual(
            router.models_for(NarrationTier.EPIC),
            (DEFAULT_EPIC_MODEL, DEFAULT_DYNAMIC_FREE_MODEL),
        )
        # The fallback slots are empty by default (v0.26.1) and an empty slot is
        # dropped from the chain rather than attempted as a model named "".
        self.assertEqual(DEFAULT_ROUTINE_FALLBACK_MODEL, "")
        self.assertEqual(DEFAULT_EPIC_FALLBACK_MODEL, "")
        self.assertFalse(router.enabled)

    def test_free_only_guard_rejects_paid_model(self):
        with self.assertRaisesRegex(ValueError, "requires a free route"):
            AITaskRouter(api_key=None, routine_model="google/gemma-4-31b-it")

    def test_openrouter_free_is_accepted_by_free_guard(self):
        router = AITaskRouter(api_key=None, dynamic_free_model="openrouter/free")
        self.assertIn("openrouter/free", router.models_for(NarrationTier.ROUTINE))

    def test_routine_uses_known_fallback_after_primary_failure(self):
        router = AITaskRouter(api_key=None)
        fake = _FakeClient([RuntimeError("primary unavailable"), "fallback narration"])
        router.client = fake
        result = asyncio.run(
            router.generate(
                tier=NarrationTier.ROUTINE,
                system_prompt="safe system",
                prompt="scene",
                max_output_tokens=80,
            )
        )
        self.assertEqual(result.model, DEFAULT_DYNAMIC_FREE_MODEL)
        self.assertEqual(result.attempted_models, (DEFAULT_ROUTINE_MODEL, DEFAULT_DYNAMIC_FREE_MODEL))

    def test_dynamic_free_router_is_last_cloud_fallback(self):
        router = AITaskRouter(api_key=None, routine_fallback_model="fixture-vendor/fixture-model:free")
        fake = _FakeClient([
            RuntimeError("primary unavailable"),
            RuntimeError("known fallback unavailable"),
            "dynamic free narration",
        ])
        router.client = fake
        result = asyncio.run(
            router.generate(
                tier=NarrationTier.ROUTINE,
                system_prompt="safe system",
                prompt="scene",
                max_output_tokens=80,
            )
        )
        self.assertEqual(result.model, DEFAULT_DYNAMIC_FREE_MODEL)
        self.assertEqual(fake.completions.calls[-1]["model"], DEFAULT_DYNAMIC_FREE_MODEL)

    def test_epic_chain_starts_with_the_default_epic_model(self):
        router = AITaskRouter(api_key=None)
        router.client = _FakeClient(["epic narration"])
        result = asyncio.run(
            router.generate(
                tier=NarrationTier.EPIC,
                system_prompt="safe system",
                prompt="major scene",
                max_output_tokens=120,
            )
        )
        self.assertEqual(result.model, DEFAULT_EPIC_MODEL)


class ReasoningOffTests(unittest.TestCase):
    """v0.19.38: every narration request tells OpenRouter not to think.

    Both production failures of the free chain were reasoning - empty content
    after the budget went to thinking, and thinking returned as the content.
    """

    def test_reasoning_is_disabled_on_every_request_by_default(self):
        router = AITaskRouter(api_key=None)
        router.client = _FakeClient(["narration"])
        asyncio.run(router.generate(tier=NarrationTier.ROUTINE, system_prompt="s", prompt="p", max_output_tokens=50))
        call = router.client.completions.calls[0]
        self.assertEqual(call["extra_body"], {"reasoning": {"enabled": False, "exclude": True}})

    def test_the_operator_can_let_models_think(self):
        router = AITaskRouter(api_key=None, disable_reasoning=False)
        router.client = _FakeClient(["narration"])
        asyncio.run(router.generate(tier=NarrationTier.EPIC, system_prompt="s", prompt="p", max_output_tokens=50))
        self.assertIsNone(router.client.completions.calls[0]["extra_body"])

    def test_the_bot_actually_passes_the_setting_through(self):
        # The constructor default is True, so dropping the kwarg in services.py
        # would leave OPENROUTER_DISABLE_REASONING silently ignored.
        from tests.support import PROJECT_ROOT
        services = (PROJECT_ROOT / "app" / "bot" / "services.py").read_text(encoding="utf-8")
        self.assertIn("disable_reasoning=SETTINGS.openrouter_disable_reasoning", services)

    def test_the_default_chains_drop_nemotron_super_and_end_at_the_dynamic_router(self):
        # Nemotron 3 Super narrated its own instructions 3 of 3 times and is out.
        # Gemma stays primary (it is only served by Google AI Studio, so it needs
        # the operator's own key).
        #
        # v0.19.38 also required a named non-Google route before openrouter/free,
        # so a Google-side problem could not take both tiers procedural. That is
        # no longer expressible: the two models that held the slot were dropped
        # for scratchpadding (MiniMax M3, v0.25.3) and for leaving the free
        # catalogue (GLM 5.2, v0.26.1), and a slug pinned here is only as good as
        # OpenRouter's catalogue on the day it is written - a dead hop fails every
        # narration AND spends a daily free-tier slot doing it, which is worse
        # than no hop. The guarantee now rests on openrouter/free, which is itself
        # a dynamic router across free models from many providers rather than a
        # single upstream. What is still gated is that the chain ENDS there.
        router = AITaskRouter(api_key=None)
        for tier, chain in router.chains.items():
            self.assertFalse(any("nemotron-3-super" in m for m in chain), tier)
            self.assertEqual(chain[-1], DEFAULT_DYNAMIC_FREE_MODEL, tier)
        self.assertEqual(router.chains[NarrationTier.ROUTINE], (DEFAULT_ROUTINE_MODEL, DEFAULT_DYNAMIC_FREE_MODEL))
        self.assertEqual(router.chains[NarrationTier.EPIC], (DEFAULT_EPIC_MODEL, DEFAULT_DYNAMIC_FREE_MODEL))
        self.assertEqual(DEFAULT_ROUTINE_MODEL, "google/gemma-4-31b-it:free")  # the one literal; test_config pins the rest

    def test_an_operator_can_still_name_a_second_hop(self):
        # Dropping the default must not remove the capability: the slot is empty,
        # not gone, so an operator who finds a free route that works can put it
        # back without a code change.
        router = AITaskRouter(
            api_key=None,
            routine_fallback_model="some-vendor/some-model:free",
            epic_fallback_model="some-vendor/some-model:free",
        )
        for tier, chain in router.chains.items():
            self.assertIn("some-vendor/some-model:free", chain, tier)
            self.assertEqual(chain[-1], DEFAULT_DYNAMIC_FREE_MODEL, tier)

    def test_no_default_route_is_a_model_already_dropped_in_production(self):
        # Each of these was a shipped default that failed 100% of the time while
        # still spending a daily free-tier slot on every attempt:
        #   - Nemotron 3 Super (v0.19.38) and MiniMax M3 (v0.25.3) are
        #     reasoning-native, ignore `reasoning.enabled=false` and answer with
        #     their own analysis, which _validate_generated_text rejects
        #     ("ScratchpadResponse: AI response was reasoning scratchpad, not
        #     narration").
        #   - GLM 5.2 (v0.26.1) left OpenRouter's free catalogue, which now 404s
        #     the `:free` slug and points at the paid one that
        #     OPENROUTER_REQUIRE_FREE rejects.
        # None of them may come back into the defaults.
        dropped = ("minimax-m3", "nemotron-3-super", "glm-5.2")
        router = AITaskRouter(api_key=None)
        for tier, chain in router.chains.items():
            for model in chain:
                for name in dropped:
                    self.assertNotIn(name, model, f"{tier}: {model}")
        for text in (DEFAULT_ROUTINE_MODEL, DEFAULT_ROUTINE_FALLBACK_MODEL,
                     DEFAULT_EPIC_MODEL, DEFAULT_EPIC_FALLBACK_MODEL):
            for name in dropped:
                self.assertNotIn(name, text)

    def test_the_documented_default_chain_matches_the_code(self):
        # README and .env.example both spell the chain out. When only the code
        # moved, an operator copying .env.example pinned the dead route back in
        # by hand - so the docs are gated here rather than trusted.
        from tests.support import PROJECT_ROOT
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for name, value in (
            ("OPENROUTER_ROUTINE_MODEL", DEFAULT_ROUTINE_MODEL),
            ("OPENROUTER_ROUTINE_FALLBACK_MODEL", DEFAULT_ROUTINE_FALLBACK_MODEL),
            ("OPENROUTER_EPIC_MODEL", DEFAULT_EPIC_MODEL),
            ("OPENROUTER_EPIC_FALLBACK_MODEL", DEFAULT_EPIC_FALLBACK_MODEL),
            ("OPENROUTER_DYNAMIC_FREE_FALLBACK", DEFAULT_DYNAMIC_FREE_MODEL),
        ):
            self.assertIn(f"{name}={value}", env_example, name)
            self.assertIn(f"{name}={value}", readme, name)
        # The dropped route must not survive anywhere an operator could copy it.
        self.assertNotIn("minimax/minimax-m3:free", env_example)
        self.assertNotIn("minimax/minimax-m3:free", readme)
        # v0.25.4: the `NAME=value` lines were corrected in v0.25.3 but the
        # prose chain summary right above them still read "Gemma 4 31B Free ->
        # MiniMax M3 Free -> ...", which is the line an operator actually reads.
        # Every arrow summary is gated, not just the assignments.
        for line in env_example.splitlines():
            if line.startswith("#") and line.count("->") >= 2:
                for dead in ("MiniMax", "Nemotron"):
                    self.assertNotIn(dead, line, line)

    def test_the_env_example_says_where_the_ai_studio_key_comes_from(self):
        # The BYOK instructions said where to PASTE the key and never where to
        # get it, which is the step an operator is actually missing.
        from tests.support import PROJECT_ROOT
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for text in (env_example, readme):
            self.assertIn("aistudio.google.com/api-keys", text)
            self.assertIn("openrouter.ai/settings/integrations", text)


def _return(value):
    async def create(**kwargs):
        return value
    return create
