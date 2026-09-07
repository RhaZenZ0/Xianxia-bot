"""Per-user token bucket for typed play (v0.21.1). Exercised behaviourally."""
from __future__ import annotations

import unittest

from app.ops.user_budget import UserBudget


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class UserBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.budget = UserBudget(burst=3, per_minute=6.0, clock=self.clock)

    def test_burst_then_refusal(self):
        self.assertTrue(all(self.budget.try_acquire(1) for _ in range(3)))
        self.assertFalse(self.budget.try_acquire(1), "the fourth line in the same instant is refused")
        self.assertEqual(self.budget.refused, 1)
        self.assertEqual(self.budget.granted, 3)

    def test_refill_at_the_sustained_rate(self):
        for _ in range(3):
            self.budget.try_acquire(1)
        self.assertFalse(self.budget.try_acquire(1))
        self.clock.now += 10.0  # 6/min = one token per 10s
        self.assertTrue(self.budget.try_acquire(1))
        self.assertFalse(self.budget.try_acquire(1))

    def test_refill_never_exceeds_burst(self):
        self.clock.now += 3600.0
        for _ in range(3):
            self.assertTrue(self.budget.try_acquire(7))
        self.assertFalse(self.budget.try_acquire(7))

    def test_users_are_independent(self):
        for _ in range(3):
            self.budget.try_acquire(1)
        self.assertFalse(self.budget.try_acquire(1))
        self.assertTrue(self.budget.try_acquire(2), "another player's bucket is untouched")

    def test_seconds_until_token(self):
        for _ in range(3):
            self.budget.try_acquire(1)
        self.assertAlmostEqual(self.budget.seconds_until_token(1), 10.0, places=6)
        self.clock.now += 4.0
        self.assertAlmostEqual(self.budget.seconds_until_token(1), 6.0, places=6)
        self.assertEqual(self.budget.seconds_until_token(99), 0.0)

    def test_idle_users_are_evicted(self):
        budget = UserBudget(burst=2, per_minute=6.0, idle_evict_seconds=60.0, clock=self.clock)
        budget.try_acquire(1)
        budget.try_acquire(2)
        self.assertEqual(budget.snapshot()["tracked_users"], 2)
        self.clock.now += 61.0
        budget.try_acquire(3)
        self.assertEqual(budget.snapshot()["tracked_users"], 1, "the two idle buckets are gone")

    def test_invalid_rate_is_rejected(self):
        with self.assertRaises(ValueError):
            UserBudget(burst=1, per_minute=0)


if __name__ == "__main__":
    unittest.main()
