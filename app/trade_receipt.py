"""Player-facing receipt for an authoritative market or black-market trade.

Why this module exists
----------------------
A player bought a Spirit-Iron Sword and the bot said:

    🪙 Bought **Spirit-Iron Sword x1** for **0** stones.

The wallet had, in fact, been debited correctly. The Go engine returns the
charge under the key ``total``; all four Python trade handlers asked for
``total_price``, a key the engine has never returned, and every one of them used
``result.get("total_price", 0)``. The default turned a missing key into a
confident, specific, wrong number - and it did so on both buy *and* sell, in both
the open market and the black market. Only the buy case was ever reported,
because "you were paid 0" reads as a balance bug while "you paid 0" reads as free
money.

So the rule this module exists to enforce: **a price that is not present is
reported as missing, never as zero.** A receipt is a statement about someone's
money. It is better to say "the engine did not report a price" - which is
obviously a bug and gets reported - than to print a plausible number that is a
lie.

Kept free of ``discord`` imports so it can be unit tested without the library.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

DEFAULT_CURRENCY_LABEL = "stones"

#: Keys the Go engine actually returns from ``market.trade`` and
#: ``black_market.trade``. Written down here because the bug above was purely a
#: name mismatch across the language boundary, invisible to both compilers.
ENGINE_TOTAL_KEY = "total"
ENGINE_UNIT_KEY = "unit_price"
ENGINE_CURRENCY_KEY = "currency_id"
ENGINE_BALANCE_KEY = "balance"
ENGINE_DETECTED_KEY = "detected"


def _as_number(value: Any) -> int | float | None:
    """Return a number, or None. A non-numeric value is missing, not zero."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(text) if text.lstrip("-").isdigit() else float(text)
    except (TypeError, ValueError):
        return None


def _money(value: int | float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def format_trade_receipt(
    *,
    icon: str,
    verb: str,
    item_label: str,
    quantity: int,
    result: Mapping[str, Any],
    currency_name: Callable[[str], str] | None = None,
) -> str:
    """Render one trade confirmation from the engine's own result payload.

    ``verb`` is "Bought" or "Sold". ``currency_name`` maps a currency id to its
    display name; when it is absent or raises, the generic label is used rather
    than the raw id.
    """
    quantity = max(1, int(quantity))

    currency_id = str(result.get(ENGINE_CURRENCY_KEY) or "").strip()
    label = DEFAULT_CURRENCY_LABEL
    if currency_id and currency_name is not None:
        try:
            resolved = str(currency_name(currency_id) or "").strip()
        except Exception:  # noqa: BLE001 - a naming lookup must never break a receipt
            resolved = ""
        label = resolved or DEFAULT_CURRENCY_LABEL

    total = _as_number(result.get(ENGINE_TOTAL_KEY))
    if total is None:
        # Deliberately not "for 0". See the module docstring.
        head = (
            f"{icon} {verb} **{item_label} x{quantity}** — "
            f"⚠️ the engine did not report a price. Check your balance with "
            f"**/economy → Wallet** and report this."
        )
    else:
        head = f"{icon} {verb} **{item_label} x{quantity}** for **{_money(total)} {label}**."

    parts = [head]

    unit = _as_number(result.get(ENGINE_UNIT_KEY))
    if unit is not None and quantity > 1:
        parts.append(f"Unit price **{_money(unit)} {label}**.")

    balance = _as_number(result.get(ENGINE_BALANCE_KEY))
    if balance is not None:
        parts.append(f"Balance now **{_money(balance)} {label}**.")

    if result.get(ENGINE_DETECTED_KEY):
        # The engine already decided this and it changes the player's standing;
        # saying nothing was its own quiet lie.
        parts.append("🚨 The transaction was **detected**.")

    return " ".join(parts)
