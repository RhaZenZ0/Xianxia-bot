import unittest

from app.trade_receipt import (
    DEFAULT_CURRENCY_LABEL,
    ENGINE_TOTAL_KEY,
    format_trade_receipt,
)


def _receipt(result, *, verb="Bought", quantity=1, currency_name=None):
    return format_trade_receipt(
        icon="🪙",
        verb=verb,
        item_label="Spirit-Iron Sword",
        quantity=quantity,
        result=result,
        currency_name=currency_name,
    )


CURRENCIES = {"spirit_stone": "Spirit Stones", "contribution": "Contribution"}


class EngineKeyTests(unittest.TestCase):
    """The reported bug, pinned down.

    The Go engine returns the charge under ``total``. Every Python trade handler
    asked for ``total_price`` with a default of 0, so a real purchase reported
    "for 0 stones" while the wallet was correctly debited.
    """

    def test_the_engine_key_is_total_not_total_price(self):
        self.assertEqual(ENGINE_TOTAL_KEY, "total")

    def test_the_engine_payload_renders_the_real_price(self):
        # Exactly the shape marketTradeAction returns.
        result = {
            "item_id": "spirit_iron_sword",
            "quantity": 1,
            "unit_price": 120,
            "total": 120,
            "currency_id": "spirit_stone",
            "buy": True,
            "balance": 880,
        }
        text = _receipt(result, currency_name=CURRENCIES.get)
        self.assertIn("for **120 Spirit Stones**", text)
        self.assertNotIn("0 stones", text)

    def test_a_total_price_key_is_not_consulted(self):
        # A payload carrying only the old, wrong key must NOT render as a price.
        text = _receipt({"total_price": 999, "currency_id": "spirit_stone"})
        self.assertNotIn("999", text)


class MissingPriceTests(unittest.TestCase):
    """A price that is absent is reported as absent, never as zero.

    This is the whole lesson of the bug: `.get(key, 0)` turned a missing key into
    a confident, specific, wrong number about someone's money.
    """

    def test_a_missing_total_never_renders_as_zero(self):
        text = _receipt({"currency_id": "spirit_stone"})
        self.assertNotIn("0", text.split("Check your balance")[0].replace("x1", ""))
        self.assertIn("did not report a price", text)

    def test_a_missing_total_tells_the_player_what_to_do(self):
        text = _receipt({})
        self.assertIn("Check your balance", text)

    def test_a_non_numeric_total_is_treated_as_missing(self):
        self.assertIn("did not report a price", _receipt({"total": "free"}))

    def test_a_boolean_total_is_treated_as_missing(self):
        # bool is an int subclass; True must not print as a price of 1.
        self.assertIn("did not report a price", _receipt({"total": True}))

    def test_an_empty_string_total_is_treated_as_missing(self):
        self.assertIn("did not report a price", _receipt({"total": ""}))

    def test_a_real_zero_total_is_still_shown_as_zero(self):
        # A genuinely free trade is a fact, not a missing value.
        text = _receipt({"total": 0, "currency_id": "spirit_stone"}, currency_name=CURRENCIES.get)
        self.assertIn("for **0 Spirit Stones**", text)
        self.assertNotIn("did not report", text)

    def test_a_numeric_string_total_is_accepted(self):
        self.assertIn("for **250", _receipt({"total": "250"}))


class PresentationTests(unittest.TestCase):
    def test_currency_id_is_resolved_to_a_display_name(self):
        text = _receipt({"total": 5, "currency_id": "contribution"}, currency_name=CURRENCIES.get)
        self.assertIn("Contribution", text)

    def test_an_unknown_currency_falls_back_to_the_generic_label(self):
        text = _receipt({"total": 5, "currency_id": "mystery"}, currency_name=CURRENCIES.get)
        self.assertIn(DEFAULT_CURRENCY_LABEL, text)
        self.assertNotIn("mystery", text)

    def test_a_raising_currency_lookup_does_not_break_the_receipt(self):
        def boom(_):
            raise KeyError("nope")

        text = _receipt({"total": 5, "currency_id": "x"}, currency_name=boom)
        self.assertIn("for **5 stones**", text)

    def test_balance_is_reported_when_the_engine_returns_it(self):
        self.assertIn("Balance now **880 stones**", _receipt({"total": 120, "balance": 880}))

    def test_balance_is_omitted_when_absent(self):
        self.assertNotIn("Balance", _receipt({"total": 120}))

    def test_a_zero_balance_is_still_reported(self):
        self.assertIn("Balance now **0", _receipt({"total": 120, "balance": 0}))

    def test_unit_price_is_shown_only_for_multi_buys(self):
        many = _receipt({"total": 360, "unit_price": 120}, quantity=3)
        one = _receipt({"total": 120, "unit_price": 120}, quantity=1)
        self.assertIn("Unit price **120", many)
        self.assertNotIn("Unit price", one)

    def test_large_numbers_are_grouped(self):
        self.assertIn("1,234,567", _receipt({"total": 1234567}))

    def test_black_market_detection_is_surfaced(self):
        # The engine already decided this and it changes the player's standing.
        self.assertIn("detected", _receipt({"total": 50, "detected": True}))

    def test_undetected_trades_say_nothing_about_detection(self):
        self.assertNotIn("detected", _receipt({"total": 50, "detected": False}))

    def test_the_verb_is_used_verbatim(self):
        self.assertTrue(_receipt({"total": 5}, verb="Sold").startswith("🪙 Sold **"))

    def test_quantity_is_floored_at_one(self):
        self.assertIn("x1", _receipt({"total": 5}, quantity=0))


if __name__ == "__main__":
    unittest.main()
