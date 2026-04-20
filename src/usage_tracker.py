"""Per-call Claude token usage + USD cost tracking.

Streamlit app drops a fresh list into session_state at startup; each AI
call appends its usage here. Results panel renders a breakdown at the
bottom: process, model, input/cache/output tokens, and USD cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

try:
    import streamlit as st
    _HAVE_STREAMLIT = True
except Exception:
    st = None  # type: ignore
    _HAVE_STREAMLIT = False


# Cache-write and cache-read multipliers are the same for Opus/Sonnet/Haiku
# (1.25x base for 5-minute writes, 0.1x base for reads). We bake the per-M
# dollar figures in directly so we don't need to do multiplier math at runtime.
MODEL_PRICING_PER_1M: Dict[str, Dict[str, float]] = {
    # Opus 4.7: $5 in / $25 out
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25,  "cache_read": 0.50},
    # Opus 4.6: same as 4.7
    "claude-opus-4-6":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25,  "cache_read": 0.50},
    # Sonnet 4.6: $3 in / $15 out
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30},
    # Haiku 4.5: $1 in / $5 out
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00,  "cache_write": 1.25,  "cache_read": 0.10},
}


@dataclass
class Usage:
    process: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    usd_cost: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _compute_cost(model: str, in_tok: int, out_tok: int, cr_tok: int, cc_tok: int) -> float:
    p = MODEL_PRICING_PER_1M.get(model)
    if not p:
        return 0.0
    return (
        in_tok * p["input"] / 1_000_000
        + out_tok * p["output"] / 1_000_000
        + cc_tok * p["cache_write"] / 1_000_000
        + cr_tok * p["cache_read"] / 1_000_000
    )


def record(process: str, model: str, response_usage) -> Optional[Usage]:
    """Append a Usage entry to Streamlit session_state from a Claude SDK
    `response.usage` object. Safe to call outside a Streamlit runtime —
    falls through silently.
    """
    try:
        in_tok = int(getattr(response_usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(response_usage, "output_tokens", 0) or 0)
        cr_tok = int(getattr(response_usage, "cache_read_input_tokens", 0) or 0)
        cc_tok = int(getattr(response_usage, "cache_creation_input_tokens", 0) or 0)
    except Exception:
        return None

    usd = _compute_cost(model, in_tok, out_tok, cr_tok, cc_tok)
    entry = Usage(
        process=process,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cr_tok,
        cache_creation_tokens=cc_tok,
        usd_cost=round(usd, 6),
    )

    if _HAVE_STREAMLIT and st is not None:
        try:
            bucket = st.session_state.setdefault("_token_usage", [])
            bucket.append(entry)
        except Exception:
            pass
    return entry


def get_usage() -> List[Usage]:
    if not _HAVE_STREAMLIT:
        return []
    try:
        return list(st.session_state.get("_token_usage", []))
    except Exception:
        return []


def clear_usage() -> None:
    if not _HAVE_STREAMLIT:
        return
    try:
        st.session_state["_token_usage"] = []
    except Exception:
        pass
