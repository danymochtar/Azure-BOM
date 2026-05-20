"""Tiny LLM helper — runs a Claude API call through a tier-cascade so a
transient `OverloadedError` (HTTP 529) or upstream 5xx on the cheapest
model gracefully falls through to the next-tier model instead of failing
the whole assessment.

Default cascades:
    Haiku  → Sonnet → Opus      (used by the classifier)
    Sonnet → Opus               (used by auto_simulate + ai_inventory)

`OverloadedError` is an Anthropic-side load signal (the requested model
is unavailable RIGHT NOW), so retrying the same model is pointless —
bumping to a less-loaded tier is the right move. We deliberately do
NOT auto-fallback on `RateLimitError` (429): that's the caller's org
quota and a different model on the same org probably won't help; the
calling code should already handle 429 with a longer wait + retry.

Each successful call is reported to `usage_tracker` under the model
that actually served the response, so cost accounting stays accurate
even when a fallback fires.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional

import anthropic

from . import usage_tracker


HAIKU_TO_OPUS = (
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
)

SONNET_TO_OPUS = (
    "claude-sonnet-4-6",
    "claude-opus-4-7",
)


def call_with_cascade(
    process_label: str,
    cascade: Iterable[str],
    invoke: Callable[[str], Any],
) -> Any:
    """Try each model in `cascade` until one succeeds; fall through to
    the next on `OverloadedError` or any 5xx. `invoke(model)` is the
    actual API call (a closure capturing prompt + parse settings).

    The chosen model is stamped into the `usage_tracker` label so cost
    breakdowns reflect what was actually billed.
    """
    cascade_list: List[str] = list(cascade)
    last_err: Optional[BaseException] = None
    for idx, model in enumerate(cascade_list):
        try:
            response = invoke(model)
        except anthropic.APIStatusError as e:
            # Only fall through on overload / 5xx — leave rate-limit (429),
            # auth (401/403), bad-request (400) to the caller.
            status = getattr(e, "status_code", None)
            if status in (502, 503, 504, 529):
                last_err = e
                continue
            raise
        except anthropic.APIConnectionError as e:
            # Network blip — try the next tier in case it routes differently.
            last_err = e
            continue

        # Success — stamp the model that actually served the response.
        label = process_label if idx == 0 else f"{process_label} → {model} (fallback)"
        usage_tracker.record(label, model, getattr(response, "usage", None))
        return response

    # Every tier in the cascade failed. Surface the most recent error so
    # the caller's `except` block sees the original SDK exception type.
    if last_err is not None:
        raise last_err
    raise RuntimeError(f"call_with_cascade: empty cascade for {process_label!r}")
