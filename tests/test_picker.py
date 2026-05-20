"""Centralised meter-picker contract tests.

Locks down the fix for the `min(records, key=retail_price)` bug that
let $0 free-tier meters silently win the picker race. Every duplicate
of this picker logic was bit-rotting into either the buggy or fixed
version — having one canonical helper + these tests prevents that.
"""
from __future__ import annotations

import pytest

from src.pricing.picker import (
    cheapest_nonzero,
    pick_by_substring,
    pick_by_substrings,
    pick_excluding,
)
from src.pricing.retail import PriceRecord


def _rec(meter: str, price: float, product: str = "") -> PriceRecord:
    return PriceRecord(
        product_name=product or meter,
        sku_name=meter,
        meter_name=meter,
        arm_sku_name="",
        arm_region_name="r",
        unit_price=price,
        retail_price=price,
        unit_of_measure="1 Hour",
        currency_code="USD",
        price_type="Consumption",
        service_name="Test",
        service_family="Test",
        product_id="",
        sku_id="",
        meter_id="",
        raw={},
    )


# ---- cheapest_nonzero -------------------------------------------------

def test_cheapest_nonzero_prefers_paid_over_free():
    records = [_rec("Free Tier", 0.0), _rec("Paid Tier", 0.20)]
    assert cheapest_nonzero(records).meter_name == "Paid Tier"


def test_cheapest_nonzero_picks_min_when_multiple_paid():
    records = [_rec("Expensive", 10.0), _rec("Cheap", 1.0), _rec("Medium", 5.0)]
    assert cheapest_nonzero(records).meter_name == "Cheap"


def test_cheapest_nonzero_falls_through_to_zero_when_all_zero():
    """Genuinely free service (e.g. Azure Arc management plane)
    should still return a record, not None."""
    records = [_rec("Free A", 0.0), _rec("Free B", 0.0)]
    chosen = cheapest_nonzero(records)
    assert chosen is not None
    assert chosen.retail_price == 0.0


def test_cheapest_nonzero_empty():
    assert cheapest_nonzero([]) is None


# ---- pick_by_substring ------------------------------------------------

def test_pick_by_substring_matches_meter_name():
    records = [
        _rec("Basic Deployment", 0.19),
        _rec("Standard Deployment", 0.29),
    ]
    assert pick_by_substring(records, "Basic").meter_name == "Basic Deployment"


def test_pick_by_substring_skips_zero_within_match():
    """The Bastion bug: 'Basic' matches both 'Basic Deployment' (paid)
    AND 'Basic Data Transfer Out — 5GB Free' ($0). The free meter must
    not win."""
    records = [
        _rec("Basic Deployment", 0.19),
        _rec("Basic Data Transfer Out — 5GB Free", 0.0),
        _rec("Basic Data Transfer Out", 0.087),
    ]
    chosen = pick_by_substring(records, "Basic")
    # Either of the two paid Basic meters is acceptable; the $0 free
    # tier must not.
    assert chosen.retail_price > 0


def test_pick_by_substring_falls_back_to_unfiltered_on_no_match():
    """When the substring is too specific, fall through to the cheapest
    nonzero across all records rather than returning None."""
    records = [_rec("Standard Tier", 0.10), _rec("Premium Tier", 0.50)]
    chosen = pick_by_substring(records, "nonexistent")
    assert chosen is not None
    assert chosen.meter_name == "Standard Tier"


# ---- pick_by_substrings (AND) ----------------------------------------

def test_pick_by_substrings_requires_all_tokens():
    """The Bastion fix: 'basic' alone matches both Deployment + Data
    Transfer Out. 'basic' AND 'deployment' anchors to the right one."""
    records = [
        _rec("Basic Deployment", 0.19),
        _rec("Basic Data Transfer Out — 5GB Free", 0.0),
        _rec("Basic Data Transfer Out", 0.087),
    ]
    chosen = pick_by_substrings(records, "basic", "deployment")
    assert chosen.meter_name == "Basic Deployment"


def test_pick_by_substrings_falls_through_when_strict_filter_misses():
    """If 'all' filter empties out, fall back to unfiltered cheapest
    so the BOM still emits something."""
    records = [_rec("Only Tier", 0.50)]
    chosen = pick_by_substrings(records, "nope", "definitely-not")
    assert chosen is not None


# ---- pick_excluding (parent/child disambiguation) ---------------------

def test_pick_excluding_anchors_to_parent_meter():
    """The App Gateway WAF v2 bug: 'waf v2 gateway' matches both base
    ($0.443/hr) AND CU child ($0.0144/hr). `cheapest_nonzero` then
    picks the CU — wrong. `pick_excluding('waf v2 gateway',
    'capacity unit')` anchors to the base."""
    records = [
        _rec("WAF v2 Application Gateway", 0.443),
        _rec("WAF v2 Application Gateway Capacity Units", 0.0144),
        _rec("WAF v2 Application Gateway Data Processed", 0.008),
    ]
    chosen = pick_excluding(
        records, "waf v2 application gateway", "capacity unit", "data",
    )
    assert chosen.meter_name == "WAF v2 Application Gateway"


def test_pick_excluding_falls_back_to_inclusion_only():
    """When exclusion list empties the candidate set, fall back to
    inclusion-only so we don't strand the line."""
    records = [
        _rec("WAF v2 Application Gateway", 0.443),
    ]
    # Exclude "gateway" which is in every record → fallback to
    # inclusion match
    chosen = pick_excluding(records, "waf v2", "gateway")
    assert chosen is not None
