"""Derive a default assumption string for any BomLine that doesn't already
have one. Used by the Excel export's Cost Assumptions sheet.

The default string lists rate × quantity × any discount / licensing tags
the resource string already advertises. Builders with richer context
(e.g. tier-based AHB %, bandwidth tier breakdown, static-pricing fallback)
should populate `BomLine.assumption` directly — this helper ONLY fills
blanks.
"""
from __future__ import annotations

import re
from typing import List

from ..models import BomLine


# Tags that can appear in the Description column; mapped to assumption phrases.
_TAG_PHRASES: List[tuple] = [
    ("[AHB]",         "Azure Hybrid Benefit applied (Windows VM priced from Linux meter — customer brings Windows Server license with SA)"),
    ("[SP 1Y]",       "Savings Plan 1-year rate from Consumption meter's savingsPlan array"),
    ("[SP 3Y]",       "Savings Plan 3-year rate from Consumption meter's savingsPlan array"),
    ("[RI 1Y]",       "Reserved Instance 1-year rate: prepaid total / (730 × 12) per-hour amortization"),
    ("[RI 3Y]",       "Reserved Instance 3-year rate: prepaid total / (730 × 36) per-hour amortization"),
    ("[HAx2]",        "High Availability: compute and disks multiplied × 2 (primary + standby pair)"),
    ("[ZR]",          "Zone-redundant tier"),
    ("(AHB Windows)", "Windows VM billed at Linux rate — AHB applied"),
    ("tier-weighted", "Bandwidth tiered (first 100 GB free; 0-10 TB, 10-50 TB, 50-150 TB, 150-500 TB, 500 TB+ bands per Azure public rate card)"),
    ("(AHB -",        "SQL AHB tier-based discount (GP 55% / BC 33% / HS 25%)"),
]


def _tag_phrases(resource: str, meter: str) -> List[str]:
    text = f"{resource} {meter}"
    phrases: List[str] = []
    for tag, phrase in _TAG_PHRASES:
        if tag in text:
            phrases.append(phrase)
    return phrases


def derive_assumption(line: BomLine) -> str:
    """Return the assumption string for a BomLine. Preserves any
    builder-supplied value; otherwise synthesizes from rate × qty + tags."""
    if line.assumption:
        return line.assumption

    parts: List[str] = []

    # Base rate × quantity math
    if line.unit_price and line.quantity:
        parts.append(
            f"${line.unit_price:.4f} × {line.quantity:,.2f} {line.unit} "
            f"= ${line.monthly_cost:,.2f}"
        )
    elif line.monthly_cost == 0 and line.source == "retail-prices-free":
        parts.append("Free (Azure Arc management plane has no charge)")
    elif line.monthly_cost == 0 and line.source == "retail-prices-miss":
        parts.append(
            "Price not found in retail feed for this meter+region — "
            "verify against the Azure Pricing Calculator and re-run after "
            "Microsoft publishes the meter"
        )

    # License / discount / tiering tags
    parts.extend(_tag_phrases(line.resource, line.meter))

    # Meter identity for traceability
    if line.meter and line.meter != "(price not found)":
        parts.append(f"meter: {line.meter}")

    return "; ".join(parts) if parts else "(no additional context)"


def assumption_row(line: BomLine, app_name: str = "") -> dict:
    """Flat dict matching the Cost Assumptions sheet's column schema."""
    return {
        "Service category": line.category,
        "Service name":     line.service_name or line.category,
        "Type":             line.sku or line.meter or "",
        "Custom name":      line.custom_name,
        "Resource":         line.resource,
        "Assumption":       derive_assumption(line),
        "Unit price":       round(float(line.unit_price or 0), 6),
        "Quantity":         round(float(line.quantity or 0), 4),
        "Unit":             line.unit,
        "Monthly cost":     round(float(line.monthly_cost or 0), 2),
    }
