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


def _contains_all(*substrs: str):
    def picker(records: List[PriceRecord]) -> Optional[PriceRecord]:
        subs = [s.lower() for s in substrs]
        filtered = [
            r for r in records
            if all(s in r.meter_name.lower() for s in subs)
        ]
        return _cheapest(filtered) or _cheapest(records)
    return picker


LANDING_ZONE_COMPONENTS: List[LzComponent] = [
    LzComponent(
        key="public_ip",
        category="Networking",
        resource="Public IP Address (Standard, static)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Virtual Network' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=lambda records: _cheapest([
            r for r in records
            if "standard" in (r.meter_name + r.sku_name).lower()
            and "static" in (r.meter_name + r.sku_name).lower()
            and "ip" in r.meter_name.lower()
        ]) or _cheapest(records),
        notes="Standard static Public IP for ingress / NAT gateway. Quantity = 1 IP × 730h.",
    ),
    LzComponent(
        key="expressroute_circuit",
        category="Networking",
        resource="ExpressRoute circuit (1 Gbps, metered)",
        default_enabled=False,
        quantity=1.0,
        unit="circuit/month",
        # ExpressRoute circuits carry armRegionName='Global' in the retail feed,
        # so we deliberately omit the region filter.
        build_filter=lambda region: (
            "serviceName eq 'ExpressRoute' and priceType eq 'Consumption'"
        ),
        pick=_contains_all("1 gbps", "circuit"),
        notes=(
            "Private connectivity from on-prem to Azure. Default tier: 1 Gbps, "
            "metered. For unlimited data plans edit the meter filter. "
            "Outbound data transfer and ISP cross-connect costs are billed separately."
        ),
    ),
    LzComponent(
        key="expressroute_gateway",
        category="Networking",
        resource="ExpressRoute Gateway (ErGw1AZ, zone-redundant)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'VPN Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("ErGw1AZ"),
        notes=(
            "ExpressRoute Gateway attached to the hub VNet. ErGw1AZ is the "
            "zone-redundant entry SKU (~1 Gbps). Use ErGw2AZ / ErGw3AZ for "
            "higher throughput (~2 / ~10 Gbps)."
        ),
    ),
    LzComponent(
        key="bandwidth_egress",
        category="Networking",
        resource="Bandwidth egress (outbound, standard tier)",
        default_enabled=True,
        quantity=200.0,
        unit="GB",
        # Bandwidth is zone-priced in the retail feed; armRegionName is
        # typically empty/"Global", so we deliberately don't filter by region.
        build_filter=lambda region: (
            "serviceName eq 'Bandwidth' and priceType eq 'Consumption'"
        ),
        pick=lambda records: (
            _cheapest([
                r for r in records
                if "data transfer out" in r.meter_name.lower()
                and "standard" in (r.meter_name + r.product_name).lower()
                and "free" not in r.meter_name.lower()
            ])
            or _cheapest([
                r for r in records
                if "data transfer out" in r.meter_name.lower()
                and "free" not in r.meter_name.lower()
            ])
            or _cheapest(records)
        ),
        notes=(
            "Outbound data transfer (egress). First 100 GB/month are free per "
            "subscription — set the quantity to the expected monthly egress "
            "ABOVE the free tier. Typical for small/mid workloads: 100-500 GB/mo."
        ),
    ),
    LzComponent(
        key="app_gateway_waf",
        category="Networking",
        resource="Application Gateway WAF v2 (1 instance)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Application Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains_all("waf v2", "gateway"),
        notes=(
            "Azure Application Gateway v2 with Web Application Firewall (WAF). "
            "Layer-7 reverse proxy + OWASP rules. Capacity Units are billed "
            "separately based on throughput; 1 CU baseline assumed here."
        ),
    ),
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
        resource="Log Analytics (PAYG ingestion)",
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
        resource="Recovery Services Vault / Azure Backup",
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
        # Rewrite the resource label to reflect the ACTUAL quantity when the
        # caller overrode the default (backup %, LA MB/day/VM, bandwidth GB).
        resource_label = comp.resource
        if comp.key in ("log_analytics", "recovery_vault", "bandwidth_egress"):
            resource_label = f"{comp.resource} (~{qty:,.1f} {comp.unit}/mo)"
        lines.append(
            BomLine(
                category=comp.category,
                resource=resource_label,
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
