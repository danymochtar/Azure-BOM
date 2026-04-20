"""Tiered Azure outbound bandwidth (egress) pricing.

Azure bills bandwidth egress in volume tiers rather than at a single flat
rate. Our retail-feed lookup returns the FIRST-TIER rate, so multiplying it
by the full monthly GB over-states cost for any workload past ~10 TB/month
and under-states for the free-allowance (first 100 GB).

This helper applies the canonical MS tier table:

    0 – 100 GB         free
    100 GB – 10 TB     ~$0.087 / GB
    10 – 50 TB         ~$0.083 / GB
    50 – 150 TB        ~$0.070 / GB
    150 – 500 TB       ~$0.050 / GB
    500 TB +           ~$0.037 / GB

Usage:

    fallback = retail_price_per_gb    # from Retail Prices API
    monthly, weighted_rate, breakdown = compute_tiered_egress(
        total_gb_month=10_000,
        first_tier_rate=fallback,
    )

The first-tier rate seeds the 100 GB – 10 TB band; subsequent bands scale
down against it with a fixed discount ladder (matching MS's published
ratios). That way we don't hard-code absolute dollar figures — we track
whatever the live retail feed reports for tier 1 and preserve the tiering
shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

# Free allowance every subscription gets before billing kicks in.
FREE_GB_PER_MONTH: float = 100.0

# Tier structure. Each tuple: (ceiling_gb_from_start_of_tier_1, discount_factor).
# `ceiling_gb` is the upper bound of the tier relative to the END of the free
# allowance (so the first paid GB counts as tier-1 GB #1). `discount_factor`
# is relative to the first-tier retail rate; published rates are roughly:
#   tier 1 (0-10 TB)         : 1.00  × base
#   tier 2 (10-50 TB)        : 0.954 × base  (~$0.083 / $0.087)
#   tier 3 (50-150 TB)       : 0.805 × base  (~$0.070 / $0.087)
#   tier 4 (150-500 TB)      : 0.575 × base  (~$0.050 / $0.087)
#   tier 5 (500+ TB)         : 0.425 × base  (~$0.037 / $0.087)
#
# Factors may shift ±2 % month-to-month as MS adjusts pricing; the shape of
# the curve is stable.
_TIERS: List[Tuple[float, float]] = [
    (10_000.0,           1.000),   # first 10 TB  after free
    (50_000.0,           0.954),   # next 40 TB   (10-50 TB)
    (150_000.0,          0.805),   # next 100 TB  (50-150 TB)
    (500_000.0,          0.575),   # next 350 TB  (150-500 TB)
    (float("inf"),       0.425),   # 500+ TB
]


@dataclass
class EgressTierBreakdown:
    """Per-tier accounting. `gb` is how many GB billed in this tier;
    `rate` is the effective $/GB charged for it."""
    label: str
    gb: float
    rate: float
    cost: float


def compute_tiered_egress(
    total_gb_month: float,
    first_tier_rate: float,
    free_gb: float = FREE_GB_PER_MONTH,
) -> Tuple[float, float, List[EgressTierBreakdown]]:
    """Return (monthly_cost, weighted_per_gb_rate, breakdown).

    - `total_gb_month`: the total EGRESS the user expects for the month
      (INCLUDING the free allowance — we subtract it internally).
    - `first_tier_rate`: the per-GB rate from the Retail Prices API for
      the lowest-paid band.
    - `free_gb`: free allowance (default 100 GB); set 0 to disable.
    """
    if total_gb_month <= 0 or first_tier_rate <= 0:
        return 0.0, 0.0, []

    billable_gb = max(0.0, total_gb_month - free_gb)
    if billable_gb == 0:
        label = f"0 – {int(free_gb)} GB (free allowance)"
        return 0.0, 0.0, [EgressTierBreakdown(label, total_gb_month, 0.0, 0.0)]

    remaining = billable_gb
    total_cost = 0.0
    breakdown: List[EgressTierBreakdown] = []

    lower = 0.0
    for upper_gb, factor in _TIERS:
        if remaining <= 0:
            break
        tier_capacity = upper_gb - lower
        tier_gb = min(remaining, tier_capacity)
        tier_rate = first_tier_rate * factor
        tier_cost = tier_gb * tier_rate
        total_cost += tier_cost
        breakdown.append(
            EgressTierBreakdown(
                label=_tier_label(lower, upper_gb),
                gb=tier_gb, rate=tier_rate, cost=tier_cost,
            )
        )
        remaining -= tier_gb
        lower = upper_gb

    weighted = total_cost / billable_gb if billable_gb else 0.0
    return round(total_cost, 4), round(weighted, 6), breakdown


def _tier_label(lower_gb: float, upper_gb: float) -> str:
    def fmt(x: float) -> str:
        if x == float("inf"):
            return "∞"
        if x >= 1_000_000:
            return f"{x / 1_000_000:.0f} PB"
        if x >= 1_000:
            return f"{x / 1_000:.0f} TB"
        return f"{x:.0f} GB"
    return f"{fmt(lower_gb)} – {fmt(upper_gb)}"


def format_breakdown_caption(breakdown: List[EgressTierBreakdown]) -> str:
    """Short multi-line summary for a Streamlit caption."""
    if not breakdown:
        return ""
    lines = []
    for b in breakdown:
        if b.rate == 0:
            lines.append(f"• **{b.label}** — {b.gb:,.0f} GB free")
        else:
            lines.append(
                f"• **{b.label}** — {b.gb:,.0f} GB × ${b.rate:.4f} = "
                f"${b.cost:,.2f}"
            )
    return "\n".join(lines)
