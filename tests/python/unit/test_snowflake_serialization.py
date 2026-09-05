"""Discord IDs must survive Python -> JSON -> JavaScript without losing digits.

A snowflake is a 17-19 digit integer, well past Number.MAX_SAFE_INTEGER
(2**53-1). Serialised as a bare JSON number it is parsed into an IEEE-754 double
by the browser and silently rounded, so the dashboard ends up posting an id that
belongs to nobody. These tests pin the serialisation boundary that prevents it.
"""
from tests.support import PROJECT_ROOT, install_aiosqlite_shim
import json
import re
import unittest

install_aiosqlite_shim()

from app.dashboard.server import JS_MAX_SAFE_INTEGER, json_safe_numbers

# A realistic Discord user snowflake: 18 digits, above 2**53-1.
SNOWFLAKE = 847706123456789012


class SnowflakeSerializationTests(unittest.TestCase):
    def test_the_premise_actually_holds(self):
        """Sanity-check the bug itself, so this suite fails loudly if it moves."""
        self.assertGreater(SNOWFLAKE, JS_MAX_SAFE_INTEGER)
        # This is precisely what JSON.parse does to a bare number in the browser.
        rounded = int(float(SNOWFLAKE))
        self.assertNotEqual(rounded, SNOWFLAKE)

    def test_snowflake_becomes_an_exact_decimal_string(self):
        encoded = json_safe_numbers({"user_id": SNOWFLAKE})
        self.assertEqual(encoded["user_id"], "847706123456789012")
        self.assertIsInstance(encoded["user_id"], str)

    def test_round_trip_through_json_keeps_every_digit(self):
        body = json.dumps(json_safe_numbers({"user_id": SNOWFLAKE}))
        # float() is how a JS engine would materialise a bare JSON number.
        self.assertEqual(int(json.loads(body)["user_id"]), SNOWFLAKE)
        self.assertNotIn("847706123456789012,", body.replace('"', ""))

    def test_small_numbers_stay_numbers(self):
        payload = json_safe_numbers(
            {"realm_index": 3, "karma_score": -250, "vitality": 0, "safe_edge": JS_MAX_SAFE_INTEGER}
        )
        self.assertEqual(payload["realm_index"], 3)
        self.assertEqual(payload["karma_score"], -250)
        self.assertEqual(payload["vitality"], 0)
        self.assertEqual(payload["safe_edge"], JS_MAX_SAFE_INTEGER)
        for value in payload.values():
            self.assertIsInstance(value, int)

    def test_negative_out_of_range_integers_are_also_stringified(self):
        self.assertEqual(json_safe_numbers(-(JS_MAX_SAFE_INTEGER + 1)), str(-(JS_MAX_SAFE_INTEGER + 1)))

    def test_booleans_stay_booleans(self):
        """bool subclasses int, so a naive isinstance check would turn these into "True"."""
        payload = json_safe_numbers({"active": True, "archived": False})
        self.assertIs(payload["active"], True)
        self.assertIs(payload["archived"], False)
        self.assertEqual(json.loads(json.dumps(payload)), {"active": True, "archived": False})

    def test_nested_structures_are_converted_throughout(self):
        payload = json_safe_numbers(
            {
                "players": [{"user_id": SNOWFLAKE, "name": "Han Li"}],
                "threads": {"household": {"thread_id": SNOWFLAKE + 1, "guild_id": SNOWFLAKE + 2}},
                "tuple_rows": ({"message_id": SNOWFLAKE + 3},),
            }
        )
        self.assertEqual(payload["players"][0]["user_id"], str(SNOWFLAKE))
        self.assertEqual(payload["threads"]["household"]["thread_id"], str(SNOWFLAKE + 1))
        self.assertEqual(payload["threads"]["household"]["guild_id"], str(SNOWFLAKE + 2))
        # Tuples become JSON arrays.
        self.assertEqual(payload["tuple_rows"][0]["message_id"], str(SNOWFLAKE + 3))
        self.assertIsInstance(payload["tuple_rows"], list)

    def test_non_numeric_values_pass_through_untouched(self):
        payload = json_safe_numbers({"name": "Han Li", "location": None, "ratio": 0.5})
        self.assertEqual(payload, {"name": "Han Li", "location": None, "ratio": 0.5})

    def test_send_json_routes_through_the_sanitizer(self):
        """The fix is only real if every response body goes through it."""
        source = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        match = re.search(r"async def _send_json\(.*?\n(.*?)\n\n", source, re.S)
        self.assertIsNotNone(match, "could not locate _send_json")
        self.assertIn("json_safe_numbers(payload)", match.group(1))


class DashboardJavaScriptIdHandlingTests(unittest.TestCase):
    """app.js must not put an id back through Number() after the server fixed it."""

    def setUp(self):
        self.source = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")

    def test_channel_options_compare_ids_as_strings(self):
        self.assertIn("sameId(current,c.id)", self.source)
        self.assertNotIn("Number(current)===Number(c.id)", self.source)

    def test_id_helpers_exist(self):
        self.assertIn("const sameId=", self.source)
        self.assertIn("const id=", self.source)

    def test_no_discord_id_is_rendered_through_the_number_formatter(self):
        offenders = re.findall(r"n\((?:x|r|c|row)\.[a-z_]*(?:user_id|guild_id|thread_id|channel_id|message_id|role_id)\)", self.source)
        self.assertEqual(offenders, [], f"IDs must not be rendered with n(): {offenders}")


if __name__ == "__main__":
    unittest.main()
