"""
price_format.py

Single source of truth for turning a raw PKR number into natural
crore/lakh phrasing.

WHY THIS EXISTS:
Every property price and rent figure in the DB is a plain PKR integer
(45000000, 68000000, ...). Nothing converted that to crore/lakh before —
the code just handed the raw number to an LLM and let it phrase it in
Roman-Urdu. The LLM did the crore conversion in its head and got it wrong
by 10x, consistently: 1 crore = 10,000,000, but the model kept dividing by
1,000,000 (the "million" grouping it's seen far more of in training) —
so 45,000,000 PKR (4.5 crore) kept coming out as "45 crore" on calls.
Same 10x error, every time, for every crore-range property.

FIX: do the conversion deterministically in Python — the one thing an LLM
should never be asked to do reliably is unit-system arithmetic — and hand
the LLM/TTS layer the already-correct string. Callers should never again
pass a raw price number into an LLM prompt or a spoken response without
running it through this module first.
"""

from decimal import Decimal
from typing import Optional, Union

Number = Union[int, float, Decimal]

CRORE = 10_000_000   # 1,00,00,000
LAKH = 100_000        # 1,00,000


def format_pkr(amount: Optional[Number], *, no_price_text: str = "price on request") -> str:
    """
    Converts a raw PKR figure into natural crore/lakh phrasing, e.g.:
        45000000  -> "4.5 crore"
        45500000  -> "4 crore 55 lakh"
        250000    -> "2.5 lakh"
        95000     -> "PKR 95,000"
        None      -> "price on request"

    This is the ONLY function that should ever convert a price for
    display or for inclusion in an LLM prompt. Never let an LLM compute
    the crore/lakh conversion itself — see module docstring.
    """
    if amount is None:
        return no_price_text

    amount = float(amount)
    if amount < 0:
        return no_price_text

    if amount >= CRORE:
        crore_part = int(amount // CRORE)
        remainder = amount - (crore_part * CRORE)
        lakh_part = round(remainder / LAKH)
        if lakh_part >= 100:  # rounding pushed a full crore over
            crore_part += 1
            lakh_part = 0
        if lakh_part == 0:
            return f"{crore_part} crore"
        return f"{crore_part} crore {lakh_part} lakh"

    if amount >= LAKH:
        lakh = amount / LAKH
        if lakh == int(lakh):
            return f"{int(lakh)} lakh"
        return f"{lakh:.1f} lakh"

    return f"PKR {amount:,.0f}"


def format_pkr_with_raw(amount: Optional[Number], *, no_price_text: str = "price on request") -> str:
    """
    Same as format_pkr(), but also appends the exact raw PKR figure in
    parentheses — e.g. "4.5 crore (PKR 45,000,000)". Use this when the
    string is going into an LLM prompt as grounding context: it gives the
    LLM the correct natural phrasing to speak AND the exact figure for any
    arithmetic it needs to do (like "how much over budget"), without ever
    requiring it to convert units itself.
    """
    if amount is None:
        return no_price_text
    natural = format_pkr(amount, no_price_text=no_price_text)
    return f"{natural} (PKR {float(amount):,.0f})"


def format_pkr_delta(amount: Optional[Number]) -> str:
    """
    Formats a difference (e.g. "over budget by X") the same way — reuses
    format_pkr so a 1.2-crore overage is spoken as "1.2 crore over budget",
    not a 10x-wrong or unnatural raw-digit figure.
    """
    if amount is None:
        return "0"
    return format_pkr(abs(amount))


if __name__ == "__main__":
    tests = [45_000_000, 68_000_000, 95_000_000, 52_000_000, 45_500_000,
              8_500_000, 250_000, 95_000, 110_000, 0, None]
    for t in tests:
        print(f"{t!r:>15} -> {format_pkr(t)!r:20}  |  {format_pkr_with_raw(t)!r}")