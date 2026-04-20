"""High-availability add-on: Standard Load Balancer + compute multiplier.

When HA is enabled we assume each workload runs as a redundant pair across
availability zones. That means:
- Compute and disk quantities are multiplied by the HA factor (default 2)
- One Standard Load Balancer is added per deployment (regional)
"""
from __future__ import annotations

from copy import deepcopy
from typing import List

from ..models import BomLine
from ..pricing.retail import RetailPricesClient, HOURS_PER_MONTH


def apply_ha_multiplier(lines: List[BomLine], factor: int = 2) -> List[BomLine]:
    """Scale Compute + Storage lines by `factor`. Non-compute lines untouched."""
    if factor <= 1:
        return lines
    out: List[BomLine] = []
    for l in lines:
        if l.category in ("Compute", "Storage"):
            new = deepcopy(l)
            new.quantity = l.quantity * factor
            new.monthly_cost = round(l.monthly_cost * factor, 2)
            new.resource = f"{l.resource} [HAx{factor}]"
            out.append(new)
        else:
            out.append(l)
    return out


def build_ha_bom(client: RetailPricesClient, region: str) -> List[BomLine]:
    """Return HA infrastructure lines (Load Balancer, etc.)."""
    lines: List[BomLine] = []

    # Standard Load Balancer: flat per-hour + rule-based components.
    lb_records = client.query(
        f"serviceName eq 'Load Balancer' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    std = [r for r in lb_records if "standard" in (r.meter_name + r.sku_name).lower()]
    std = [r for r in std if r.unit_of_measure and "hour" in r.unit_of_measure.lower()]
    if std:
        chosen = min(std, key=lambda r: r.retail_price)
        monthly = chosen.retail_price * HOURS_PER_MONTH
        lines.append(
            BomLine(
                category="Networking",
                resource="Standard Load Balancer (HA)",
                sku=chosen.sku_name or chosen.product_name,
                meter=chosen.meter_name,
                region=region,
                quantity=HOURS_PER_MONTH,
                unit="hours",
                unit_price=chosen.retail_price,
                monthly_cost=round(monthly, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id,
                sku_id=chosen.sku_id,
                meter_id=chosen.meter_id,
            )
        )
    return lines
