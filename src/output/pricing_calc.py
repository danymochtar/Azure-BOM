"""Build artifacts that line up with the Azure Pricing Calculator.

Two outputs:

1) `build_pricing_calculator_import` — a JSON payload with every line item
   and its Retail Prices API identifiers (productId, skuId, meterId). The
   Azure Pricing Calculator's "Save/Share" feature stores estimates with
   these exact identifiers. While Microsoft doesn't publish a stable
   third-party import endpoint, this JSON gives you a deterministic record
   you can:
     - re-import by scripting your own estimate addition, OR
     - paste into a shared template, OR
     - keep as an audit trail next to the Excel BOM.

2) `build_pricing_calculator_links` — category-level deep links to the
   Azure Pricing Calculator product pages (e.g. Virtual Machines). These
   open the calculator pre-scoped to the right product so a user can
   quickly configure the same SKU without hunting the catalog.
"""
from __future__ import annotations

import json
from typing import Dict, List

from ..models import BomLine


CALCULATOR_BASE = "https://azure.microsoft.com/en-us/pricing/calculator/"

# Map BOM category / resource kind -> calculator product anchor
PRODUCT_ANCHORS: Dict[str, str] = {
    "Virtual Machines": "virtual-machines",
    "Managed Disks": "managed-disks",
    "Azure Firewall": "azure-firewall",
    "Azure Bastion": "azure-bastion",
    "VPN Gateway": "vpn-gateway",
    "Log Analytics": "monitor",
    "Key Vault": "key-vault",
    "Azure Backup": "backup",
    "Microsoft Defender for Cloud": "defender",
}


def build_pricing_calculator_import(
    lines: List[BomLine],
    region: str,
    currency: str,
) -> bytes:
    """Return JSON bytes describing every line item with calculator-compatible IDs."""
    payload = {
        "schemaVersion": "1",
        "generator": "azure-bom-generator",
        "region": region,
        "currency": currency,
        "totalMonthlyCost": round(sum(l.monthly_cost for l in lines), 2),
        "items": [
            {
                "category": l.category,
                "resource": l.resource,
                "sku": l.sku,
                "meter": l.meter,
                "region": l.region,
                "quantity": l.quantity,
                "unit": l.unit,
                "unitPrice": l.unit_price,
                "monthlyCost": l.monthly_cost,
                "productId": l.product_id,
                "skuId": l.sku_id,
                "meterId": l.meter_id,
                "source": l.source,
            }
            for l in lines
        ],
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def build_pricing_calculator_links(lines: List[BomLine]) -> List[Dict[str, str]]:
    """Return a list of {label, url} records for the categories present in BOM."""
    categories_present = set()
    for l in lines:
        rn = l.resource.lower()
        if "virtual machine" in rn:
            categories_present.add("Virtual Machines")
        elif "managed disk" in rn:
            categories_present.add("Managed Disks")
        elif "firewall" in rn:
            categories_present.add("Azure Firewall")
        elif "bastion" in rn:
            categories_present.add("Azure Bastion")
        elif "vpn" in rn:
            categories_present.add("VPN Gateway")
        elif "log analytics" in rn:
            categories_present.add("Log Analytics")
        elif "key vault" in rn:
            categories_present.add("Key Vault")
        elif "backup" in rn or "recovery services" in rn:
            categories_present.add("Azure Backup")
        elif "defender" in rn:
            categories_present.add("Microsoft Defender for Cloud")

    out: List[Dict[str, str]] = []
    for cat in sorted(categories_present):
        anchor = PRODUCT_ANCHORS.get(cat)
        if not anchor:
            continue
        out.append({
            "label": cat,
            "url": f"{CALCULATOR_BASE}#{anchor}",
        })
    # Always include the main calculator entry point
    out.append({"label": "Azure Pricing Calculator (all products)", "url": CALCULATOR_BASE})
    return out
