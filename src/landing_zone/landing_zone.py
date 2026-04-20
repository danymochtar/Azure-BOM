"""Basic enterprise-scale landing zone components priced via Retail Prices API.

These are minimums for a hub-spoke style LZ. Customers typically resize based
on throughput, log volume, and user counts — adjust quantities in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from ..models import BomLine
from ..pricing.retail import RetailPricesClient, PriceRecord, HOURS_PER_MONTH


@dataclass
class LzComponent:
    key: str
    category: str
    resource: str
    default_enabled: bool
    quantity: float                  # in "unit"
    unit: str                        # "hours", "GB", "million ops", etc.
    build_filter: Callable[[str], str]
    # Optional post-filter to pick the right record from multiple returned
    pick: Optional[Callable[[List[PriceRecord]], Optional[PriceRecord]]] = None
    notes: str = ""


def _cheapest(records: List[PriceRecord]) -> Optional[PriceRecord]:
    return min(records, key=lambda r: r.retail_price) if records else None


def _contains(meter_substr: str):
    def picker(records: List[PriceRecord]) -> Optional[PriceRecord]:
        s = meter_substr.lower()
        filtered = [r for r in records if s in r.meter_name.lower()]
        return _cheapest(filtered) or _cheapest(records)
    return picker


LANDING_ZONE_COMPONENTS: List[LzComponent] = [
    LzComponent(
        key="firewall",
        category="Networking",
        resource="Azure Firewall (Standard)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Firewall' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Standard Deployment"),
        notes="1 hub firewall (Standard tier). Premium adds IDPS/TLS — ~2x cost.",
    ),
    LzComponent(
        key="bastion",
        category="Networking",
        resource="Azure Bastion (Basic)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Bastion' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Basic"),
        notes="Basic tier, 1 instance. Add data out separately if needed.",
    ),
    LzComponent(
        key="vpn_gw",
        category="Networking",
        resource="VPN Gateway (VpnGw1)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'VPN Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("VpnGw1"),
        notes="Single VpnGw1 in hub. Zone-redundant SKU costs ~15% more.",
    ),
    LzComponent(
        key="log_analytics",
        category="Management",
        resource="Log Analytics (Pay-as-you-go, 50 GB/mo)",
        default_enabled=True,
        quantity=50.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Pay-as-you-go Data Ingestion"),
        notes="Default 50 GB/mo ingestion. Increase for production workloads.",
    ),
    LzComponent(
        key="key_vault",
        category="Security",
        resource="Key Vault (Standard, 10k ops)",
        default_enabled=True,
        quantity=0.01,  # 10k operations in millions (Retail price unit varies)
        unit="10K ops",
        build_filter=lambda region: (
            f"serviceName eq 'Key Vault' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Operations"),
        notes="Standard vault, approx 10k ops/month. HSM-backed keys are separate.",
    ),
    LzComponent(
        key="recovery_vault",
        category="Management",
        resource="Recovery Services Vault / Azure Backup (50 GB protected)",
        default_enabled=False,
        quantity=50.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Backup' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Protected Instances"),
        notes="Enable for VM backup. Tune GB & RPO per workload.",
    ),
]


def build_landing_zone_bom(
    client: RetailPricesClient,
    region: str,
    enabled_keys: List[str],
    quantity_overrides: Optional[dict] = None,
) -> List[BomLine]:
    overrides = quantity_overrides or {}
    lines: List[BomLine] = []
    for comp in LANDING_ZONE_COMPONENTS:
        if comp.key not in enabled_keys:
            continue
        qty = float(overrides.get(comp.key, comp.quantity))
        records = client.query(comp.build_filter(region), max_pages=5)
        chosen = (comp.pick(records) if comp.pick else _cheapest(records))
        if not chosen:
            lines.append(
                BomLine(
                    category=comp.category,
                    resource=comp.resource,
                    sku="N/A",
                    meter="(price not found)",
                    region=region,
                    quantity=qty,
                    unit=comp.unit,
                    unit_price=0.0,
                    monthly_cost=0.0,
                    source="retail-prices-miss",
                )
            )
            continue
        monthly = chosen.retail_price * qty
        lines.append(
            BomLine(
                category=comp.category,
                resource=comp.resource,
                sku=chosen.sku_name or chosen.product_name,
                meter=chosen.meter_name,
                region=region,
                quantity=qty,
                unit=comp.unit,
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
