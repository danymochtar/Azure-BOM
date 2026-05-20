"""Centralised meter-picker helpers used by every BOM builder.

Why this module exists: 4+ pillars / LZ modules were defining their
own `_pick` / `_cheapest` functions with subtle differences. The
critical bug pattern is that `min(records, key=retail_price)` happily
picks $0 free-tier meters ("Storage Mirroring Free Data Stored",
"1 vCore - Free", "Basic Data Transfer Out — 5GB Free") and silently
zeroes the BOM line. Every duplicate has been bit-rotted into either
the buggy or the fixed version at different times.

This module is the single source of truth. The contract:

- `cheapest_nonzero(records)` — return the cheapest record whose
  `retail_price > 0`. Falls through to the cheapest $0 record only
  when EVERY candidate is $0 (legitimately free service).

- `pick_by_substring(records, substr)` — keep records whose meter or
  product name contains `substr`, then apply `cheapest_nonzero`.
  Falls through to the unfiltered set on no substring match.

- `pick_by_substrings(records, *substrs)` — all substrings must
  match (AND semantics).

- `pick_excluding(records, must_include, *exclude_tokens)` — match
  records containing `must_include` AND lacking any of
  `exclude_tokens`. Useful for picking the base meter when there's
  a parent + child meter pair (App Gateway WAF v2 base vs CU,
  Storage data-stored vs operations).

All four return `Optional[PriceRecord]`. Callers should handle None
by falling back to a static rate.
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from .retail import PriceRecord


def cheapest_nonzero(records: Iterable[PriceRecord]) -> Optional[PriceRecord]:
    """Cheapest record, preferring non-zero retail_price.

    Why "preferring": free-tier meters live alongside paid meters in
    the Retail Prices API. A naive `min(records, key=retail_price)`
    always picks the free one, silently zeroing the BOM. We want the
    cheapest meter that ACTUALLY BILLS something — only fall through
    to $0 when there's literally nothing else (rare).
    """
    records = list(records)
    if not records:
        return None
    non_zero = [r for r in records if r.retail_price > 0]
    pool = non_zero if non_zero else records
    return min(pool, key=lambda r: r.retail_price)


def pick_by_substring(
    records: Iterable[PriceRecord], substr: str,
) -> Optional[PriceRecord]:
    """Filter records whose meter_name OR product_name contains
    `substr` (case-insensitive), then return the cheapest non-zero.
    Falls back to the unfiltered set when no match — caller's
    substring may be too specific for this region's meter naming."""
    s = substr.lower()
    records = list(records)
    matches = [
        r for r in records
        if s in r.meter_name.lower() or s in r.product_name.lower()
    ]
    if not matches:
        matches = records
    return cheapest_nonzero(matches)


def pick_by_substrings(
    records: Iterable[PriceRecord], *substrs: str,
) -> Optional[PriceRecord]:
    """All substrings must appear in the meter_name (case-insensitive).
    Stricter than `pick_by_substring` — use when you have multiple
    disambiguating tokens (e.g. "waf v2" + "gateway")."""
    subs = [s.lower() for s in substrs]
    records = list(records)
    matches = [
        r for r in records
        if all(s in r.meter_name.lower() for s in subs)
    ]
    if not matches:
        matches = records
    return cheapest_nonzero(matches)


def pick_excluding(
    records: Iterable[PriceRecord],
    must_include: str,
    *exclude_tokens: str,
) -> Optional[PriceRecord]:
    """Match records containing `must_include` AND lacking any of
    `exclude_tokens` in their meter_name. Used to pick a parent
    meter while excluding the related child meters — e.g. App
    Gateway WAF v2 has a base "WAF v2 Gateway" meter and a child
    "WAF v2 Gateway Capacity Units" meter; picking by "waf v2 gateway"
    alone matches both and `cheapest_nonzero` picks the CU (cheaper).
    Excluding "capacity unit" / "data" anchors to the base.
    """
    inc = must_include.lower()
    excl = [t.lower() for t in exclude_tokens]
    records = list(records)
    matches = [
        r for r in records
        if inc in r.meter_name.lower()
        and not any(e in r.meter_name.lower() for e in excl)
    ]
    if not matches:
        # Fall back to inclusion-only — the exclusion may have been
        # too aggressive for this region's meter set.
        matches = [r for r in records if inc in r.meter_name.lower()]
    return cheapest_nonzero(matches)
