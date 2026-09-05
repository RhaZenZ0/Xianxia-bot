"""Cross-language contract: every engine result key Python reads must exist in Go.

The bug that produced this file
-------------------------------
A player bought a Spirit-Iron Sword. The bot said "for **0** stones". The wallet
had been debited correctly.

The Go engine returns the charge as ``total``. All four Python trade handlers
read ``result.get("total_price", 0)`` - a key the engine has never returned. The
default turned a missing key into a specific, confident, wrong number, on both
buy and sell, in both the open market and the black market. Nothing caught it:
Go compiles fine, Python runs fine, the key mismatch lives only in the JSON
between them.

This test walks that boundary. It reads the Go dispatch switches to map each
action to its handler function, collects every string that function (and the
helpers it calls) can use as a result-map key, and compares that against every
``result.get("...")`` in the matching Python handler.

It is deliberately CONSERVATIVE: the Go key set is over-collected (transitively,
including helper functions), so it under-reports rather than crying wolf. A
finding here is therefore worth taking seriously.
"""

import collections
import re
import unittest

from tests.support import PROJECT_ROOT

GO_ROOT = PROJECT_ROOT / "go_core"
BOT = PROJECT_ROOT / "app" / "bot" / "main.py"
# Engine calls follow the handlers out of main.py as the decomposition proceeds
# (/family moved to app/bot/commands/family.py in split stage 2), so the boundary
# scan reads the whole package. Anchoring it to main.py would have quietly
# stopped checking every handler that moved.
BOT_DIR = PROJECT_ROOT / "app" / "bot"


def _bot_sources() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(BOT_DIR.rglob("*.py")))

# Keys read from a payload the engine passes through verbatim rather than
# constructing, or read defensively where absence is a designed state. Add here
# only with a reason; an empty allowlist is the goal.
ALLOWED_MISSING: dict[tuple[str, str], str] = {}


def _go_sources():
    return {p: p.read_text(encoding="utf-8") for p in GO_ROOT.rglob("*.go")}


def _action_to_functions(sources):
    """Parse `case "a", "b":` followed by `return fn(` or `mutation, err = fn(`."""
    mapping: dict[str, set[str]] = {}
    for text in sources.values():
        pending: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("case ") and '"' in stripped:
                pending = re.findall(r'"([a-z_0-9.]+)"', stripped)
                if not stripped.endswith(":"):
                    continue
            elif pending and stripped.startswith('"'):
                pending += re.findall(r'"([a-z_0-9.]+)"', stripped)
                continue
            call = re.search(r"(?:return|=)\s+(\w+)\(", stripped)
            if pending and call:
                for action in pending:
                    mapping.setdefault(action, set()).add(call.group(1))
                pending = []
    return mapping


def _function_keys(sources):
    keys: dict[str, set[str]] = collections.defaultdict(set)
    for text in sources.values():
        for match in re.finditer(r"^func\s+(\w+)\(", text, re.M):
            name = match.group(1)
            start = match.end()
            nxt = re.search(r"^func\s+\w+\(", text[start:], re.M)
            body = text[start : start + (nxt.start() if nxt else len(text) - start)]
            keys[name] |= set(re.findall(r'"([a-z_0-9]+)"\s*:', body))
            keys[name] |= set(re.findall(r'\w+\["([a-z_0-9]+)"\]\s*=', body))
            for callee in set(re.findall(r"\b(\w+(?:Action|Go|Result|Payload))\(", body)):
                keys[name].add("\x00" + callee)
    return keys


def _keys_for(action, action_fn, fn_keys):
    found: set[str] = set()
    seen: set[str] = set()
    stack = list(action_fn.get(action, ()))
    while stack:
        fn = stack.pop()
        if fn in seen:
            continue
        seen.add(fn)
        for key in fn_keys.get(fn, ()):
            if key.startswith("\x00"):
                stack.append(key[1:])
            else:
                found.add(key)
    return found, seen


def _python_handlers():
    text = _bot_sources()
    out = []
    for match in re.finditer(r"^async def (\w+)\(", text, re.M):
        name = match.group(1)
        start = match.end()
        nxt = re.search(r"^(?:async def |def |class |@registered_)", text[start:], re.M)
        body = text[start : start + (nxt.start() if nxt else len(text) - start)]
        actions = set(re.findall(r"authoritative_action\(\s*['\"]([a-z_0-9.]+)['\"]", body))
        if not actions:
            continue
        gets = set(re.findall(r"result\.get\(\s*['\"]([a-z_0-9]+)['\"]", body))
        out.append((name, actions, gets))
    return out


class EngineResultKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sources = _go_sources()
        cls.action_fn = _action_to_functions(sources)
        cls.fn_keys = _function_keys(sources)
        cls.handlers = _python_handlers()

    def test_the_go_dispatch_switches_were_actually_parsed(self):
        # A parser that silently matched nothing would make this whole file a
        # no-op that passes forever.
        self.assertGreater(len(self.action_fn), 100)
        self.assertIn("market.trade", self.action_fn)
        self.assertIn("black_market.trade", self.action_fn)

    def test_the_python_handlers_were_actually_parsed(self):
        self.assertGreater(len(self.handlers), 50)

    def test_market_trade_really_returns_total_and_not_total_price(self):
        keys, functions = _keys_for("market.trade", self.action_fn, self.fn_keys)
        self.assertTrue(functions, "no Go handler resolved for market.trade")
        self.assertIn("total", keys)
        self.assertNotIn("total_price", keys)

    def test_no_python_handler_reads_a_key_the_engine_never_returns(self):
        problems = []
        for name, actions, gets in self.handlers:
            for action in sorted(actions):
                known, functions = _keys_for(action, self.action_fn, self.fn_keys)
                if not functions:
                    continue  # dispatched somewhere this parser does not model
                for key in sorted(gets):
                    if key in known or (name, key) in ALLOWED_MISSING:
                        continue
                    problems.append(f"{name} reads result['{key}'] but {action} never returns it")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_total_price_is_gone_from_the_whole_bot_module(self):
        self.assertNotIn("total_price", BOT.read_text(encoding="utf-8"))


class TradeReceiptWiringTests(unittest.TestCase):
    def setUp(self):
        self.bot = BOT.read_text(encoding="utf-8")

    def test_all_four_trade_handlers_use_the_shared_receipt(self):
        # market buy/sell and black market buy/sell. All four had the same bug;
        # only one was reported.
        self.assertEqual(self.bot.count("format_trade_receipt("), 4)

    def test_no_trade_message_hardcodes_the_currency(self):
        for line in self.bot.splitlines():
            if "format_trade_receipt(" in line:
                self.assertIn("currency_name=WORLD.currency_name", line)


class EquipmentOptionTests(unittest.TestCase):
    """Equip asked the player to type a database row id it never showed them."""

    def setUp(self):
        self.bot = BOT.read_text(encoding="utf-8")

    def test_every_equipment_id_action_has_a_live_option_provider(self):
        for action in ("equipment_equip", "equipment_unequip", "equipment_repair"):
            self.assertIn(
                f'register_hub_option_provider({action}, "equipment_id"',
                self.bot,
                action,
            )

    def test_bind_has_one_too(self):
        # Bind is the FIRST rung: without options it asks for a typed item id, so
        # nothing can ever become bound equipment, so Equip is permanently empty.
        # A player holding a Spirit-Iron Sword hit exactly that dead end.
        self.assertIn('register_hub_option_provider(equipment_bind, "item"', self.bot)

    def test_bind_offers_only_items_that_are_actually_equipment(self):
        start = self.bot.index("async def equipment_bind_hub_options")
        body = self.bot[start : start + 1600]
        self.assertIn("DB.get_inventory(", body)
        self.assertIn("EQUIPMENT_DEFINITIONS.get(", body)

    def test_every_empty_equipment_picker_explains_the_prerequisite(self):
        # "Equip has no available equipment id options right now" is true and
        # useless. Each empty picker now names what to do instead.
        for action, parameter in (
            ("equipment_bind", "item"),
            ("equipment_equip", "equipment_id"),
            ("equipment_unequip", "equipment_id"),
            ("equipment_repair", "equipment_id"),
        ):
            self.assertIn(
                f'register_hub_option_hint(\n    {action}, "{parameter}",',
                self.bot,
                f"{action}.{parameter}",
            )

    def test_equip_offers_only_unequipped_items(self):
        start = self.bot.index("async def equipment_equip_hub_options")
        body = self.bot[start : start + 400]
        self.assertIn("equipped=False", body)

    def test_unequip_offers_only_equipped_items(self):
        start = self.bot.index("async def equipment_unequip_hub_options")
        body = self.bot[start : start + 400]
        self.assertIn("equipped=True", body)

    def test_confirmations_name_the_item_rather_than_only_its_id(self):
        for verb in ("Equipped", "Unequipped", "Repaired"):
            pattern = rf'f"[^"]*{verb} \{{await _equipment_label\('
            self.assertRegex(self.bot, pattern, verb)


if __name__ == "__main__":
    unittest.main()
