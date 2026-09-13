"""LEGACY estimates for importing old Batch usage only; not current verified tariffs.

New calls use exact operator-confirmed profiles and micro-USD arithmetic in admission.py.
Do not use this substring catalog for admission, current-price advice or invoice reconciliation.
Historical rows are deliberately not recalculated (no authorized backfill).
"""
from __future__ import annotations

# model substring → (input $/1M, output $/1M)
PRICES: list[tuple[str, float, float]] = [
    ("gemini-2.5-pro", 1.25, 10.0),
    ("gemini-2.5-flash-lite", 0.10, 0.40),
    ("gemini-2.5-flash", 0.30, 2.50),
    ("gemini-1.5-pro", 1.25, 5.0),
    ("gemini-1.5-flash", 0.075, 0.30),
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.0),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("claude-haiku", 0.80, 4.0),
    ("claude-sonnet", 3.0, 15.0),
    ("claude-opus", 15.0, 75.0),
    ("jina-embeddings", 0.02, 0.02),
    ("text-embedding-3", 0.02, 0.02),
]
def _rate(model: str | None) -> tuple[float, float] | None:
    m = (model or "").lower()
    for key, pin, pout in PRICES:
        if key in m:
            return pin, pout
    return None


def has_known_price(model: str | None) -> bool:
    """Whether the legacy approximation exists; does NOT authorize paid work."""
    return _rate(model) is not None


def cost_cents(model: str | None, prompt_tokens: int, completion_tokens: int) -> int:
    """USD cents for a call's token usage (rounded up to the cent)."""
    rates = _rate(model)
    if rates is None:
        raise ValueError("unknown model price")
    pin, pout = rates
    dollars = (prompt_tokens / 1_000_000) * pin + (completion_tokens / 1_000_000) * pout
    cents = dollars * 100
    return int(cents) + (1 if cents - int(cents) > 0 else 0)
