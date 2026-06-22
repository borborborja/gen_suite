"""Approximate LLM prices (USD per 1M tokens) for the spending control. Matched by substring so
both ``gemini-2.5-flash`` (Google direct) and ``google/gemini-2.5-flash`` (OpenRouter) resolve to the
same price. These are estimates the operator can refine; cost is computed and stored per job."""
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
_DEFAULT = (0.50, 1.50)  # unknown model → conservative estimate


def _rate(model: str | None) -> tuple[float, float]:
    m = (model or "").lower()
    for key, pin, pout in PRICES:
        if key in m:
            return pin, pout
    return _DEFAULT


def cost_cents(model: str | None, prompt_tokens: int, completion_tokens: int) -> int:
    """USD cents for a call's token usage (rounded up to the cent)."""
    pin, pout = _rate(model)
    dollars = (prompt_tokens / 1_000_000) * pin + (completion_tokens / 1_000_000) * pout
    cents = dollars * 100
    return int(cents) + (1 if cents - int(cents) > 0 else 0)
