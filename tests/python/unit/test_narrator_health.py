"""Three faults that look alike in a log and are not alike at all.

The Narrator Health panel (v1.0.0-rc.13) exists because the old AI Routing page
showed every failure the same way, and the three that matter have three
different fixes:

* a **provider cap** is the model's own upstream rate-limiting OpenRouter's
  shared free pool. OpenRouter credits do not raise it; an own-provider key
  does.
* the **account budget** is OpenRouter's own daily free-tier cap. Credits *do*
  raise it, 50 to 1000.
* an **empty reply** is not a limit at all - a reasoning model spending a small
  token budget on its own scratchpad - and neither waiting nor paying fixes it.

Confusing the first two sends an operator to buy credits for a ceiling credits
never touch, which is the specific mistake this classification prevents. So the
classification is asserted here rather than left to the panel to infer from
error text.
"""
from __future__ import annotations

import unittest

from app.ai.ai_router import _fault_summary, _route_fault


def row(**over):
    base = {
        "attempts": 0, "successes": 0, "failures": 0, "empty_responses": 0,
        "rate_limited": 0, "probe_retired": False,
    }
    base.update(over)
    return base


class WhichFaultARouteIsIn(unittest.TestCase):
    def test_a_route_that_is_serving_has_no_fault(self):
        self.assertEqual(_route_fault(row(attempts=2, successes=2)), "")

    def test_an_untried_route_has_no_fault(self):
        # "not yet asked" must not render as a problem.
        self.assertEqual(_route_fault(row()), "")

    def test_a_429_is_a_provider_cap(self):
        self.assertEqual(_route_fault(row(attempts=5, successes=1, failures=4, rate_limited=4)),
                         "provider_cap")

    def test_a_route_that_only_ever_answers_empty_is_an_empty_reply(self):
        self.assertEqual(_route_fault(row(attempts=6, successes=0, failures=6, empty_responses=6)),
                         "empty_reply")

    def test_empty_reply_wins_over_a_rate_limit_on_the_same_route(self):
        # A route can be both, and only one of them is cleared by waiting.
        # Calling it a provider cap would tell the operator to wait or to buy a
        # key for a route that answers fine and just never answers with prose.
        self.assertEqual(
            _route_fault(row(attempts=6, successes=0, failures=6, empty_responses=5, rate_limited=1)),
            "empty_reply",
        )

    def test_a_route_that_has_succeeded_is_not_an_empty_reply(self):
        # It answers with prose sometimes; the empties are noise, not its nature.
        self.assertEqual(
            _route_fault(row(attempts=9, successes=3, failures=6, empty_responses=6)),
            "error",
        )

    def test_a_retired_route_says_so_before_anything_else(self):
        self.assertEqual(
            _route_fault(row(attempts=3, successes=0, failures=3, empty_responses=3,
                             rate_limited=3, probe_retired=True)),
            "retired",
        )

    def test_any_other_failure_is_named_honestly_rather_than_guessed(self):
        self.assertEqual(_route_fault(row(attempts=2, successes=0, failures=2)), "error")


class WhatTheChainAddsUpTo(unittest.TestCase):
    def test_it_counts_each_fault_and_the_routes_still_carrying_traffic(self):
        models = [
            {"model": "a", "successes": 2, "cooling_down": False, "fault": ""},
            {"model": "b", "successes": 0, "cooling_down": True, "fault": "provider_cap"},
            {"model": "c", "successes": 0, "cooling_down": True, "fault": "provider_cap"},
            {"model": "d", "successes": 0, "cooling_down": False, "fault": "empty_reply"},
        ]
        out = _fault_summary(models, {"used_today": 15, "max_requests_per_day": 50})
        self.assertEqual(out["counts"], {"provider_cap": 2, "empty_reply": 1})
        self.assertEqual(out["routes"], 4)
        self.assertEqual(out["serving"], 1)

    def test_a_cooling_route_is_not_counted_as_carrying_traffic(self):
        models = [{"model": "a", "successes": 5, "cooling_down": True, "fault": "provider_cap"}]
        self.assertEqual(_fault_summary(models, {})["serving"], 0)

    def test_the_spent_budget_is_its_own_flag_not_a_route_fault(self):
        # It belongs to the account, not to any model, and it is the one
        # ceiling here that credits raise.
        out = _fault_summary([], {"used_today": 50, "max_requests_per_day": 50})
        self.assertTrue(out["budget_spent"])
        self.assertEqual(out["counts"], {})

    def test_a_budget_that_is_merely_low_is_not_spent(self):
        self.assertFalse(_fault_summary([], {"used_today": 49, "max_requests_per_day": 50})["budget_spent"])

    def test_an_unconfigured_cap_never_reads_as_spent(self):
        # 0/0 is "no cap known", not "exhausted".
        self.assertFalse(_fault_summary([], {"used_today": 0, "max_requests_per_day": 0})["budget_spent"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
