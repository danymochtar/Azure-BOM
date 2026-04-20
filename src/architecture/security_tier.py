"""Security tier presets that drive Defender + Monitor + Sentinel pricing."""
from __future__ import annotations

from typing import Dict, List, Optional

from ..landing_zone.defender import (
    DEFENDER_PLANS,
    build_defender_bom,
)
from ..models import BomLine
from ..pricing.retail import RetailPricesClient, PriceRecord


SECURITY_TIERS: Dict[str, Dict] = {
    "none": {
        "label": "None",
        "description": "Compute only, no cloud-security telemetry.",
        "defender_plans": [],
        "add_sentinel": False,
        "add_cspm": False,
        "log_analytics_gb": 0.0,
    },
    "basic": {
        "label": "Basic (Defender CSPM + Azure Monitor)",
        "description": "Foundational security posture: Defender CSPM across the estate, basic Log Analytics + Azure Monitor.",
        "defender_plans": [],
        "add_sentinel": False,
        "add_cspm": True,
        "log_analytics_gb": 25.0,
    },
    "full": {
        "label": "Full suite (Defender everywhere + Log Analytics + Azure Monitor + Sentinel)",
        "description": "Defender for Servers P2 + SQL + Storage + App Service + Key Vault + Containers, Sentinel, and larger Log Analytics ingestion.",
        "defender_plans": ["servers_p2", "sql_on_vms", "storage", "appsvc", "keyvault", "containers"],
        "add_sentinel": True,
        "add_cspm": True,
        "log_analytics_gb": 150.0,
    },
}


def defender_plans_for_tier(tier: str) -> List[str]:
    return SECURITY_TIERS.get(tier, {}).get("defender_plans", [])


def _pick(records: List[PriceRecord], substr: str) -> Optional[PriceRecord]:
    sub = substr.lower()
    matches = [
        r for r in records
        if sub in r.meter_name.lower() or sub in r.product_name.lower()
    ]
    if not matches:
        matches = records
    return min(matches, key=lambda r: r.retail_price) if matches else None


def _cspm_line(client: RetailPricesClient, region: str, vm_count: int) -> Optional[BomLine]:
    """Defender CSPM (enhanced) — priced per billable resource/hour."""
    if vm_count <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Microsoft Defender for Cloud' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "defender cspm")
    if not chosen:
        chosen = _pick(recs, "cspm")
    if not chosen:
        return None
    # CSPM meters are usually per resource/hour
    from ..pricing.retail import HOURS_PER_MONTH
    unit_is_hour = "hour" in (chosen.unit_of_measure or "").lower()
    qty = vm_count * (HOURS_PER_MONTH if unit_is_hour else 1)
    unit = "resource-hours" if unit_is_hour else chosen.unit_of_measure or "unit"
    return BomLine(
        category="Security",
        resource=f"Defender CSPM — {vm_count} resource(s)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=qty,
        unit=unit,
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
    )


def _sentinel_line(client: RetailPricesClient, region: str, gb: float) -> Optional[BomLine]:
    if gb <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Sentinel' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    if not recs:
        recs = client.query(
            f"serviceName eq 'Azure Sentinel' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        )
    chosen = _pick(recs, "pay-as-you-go")
    if not chosen:
        return None
    return BomLine(
        category="Security",
        resource=f"Microsoft Sentinel (PAYG ingestion, {gb} GB/mo)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=gb,
        unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
    )


def _log_analytics_line(client: RetailPricesClient, region: str, gb: float) -> Optional[BomLine]:
    if gb <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "pay-as-you-go data ingestion")
    if not chosen:
        return None
    return BomLine(
        category="Management",
        resource=f"Log Analytics (PAYG ingestion, {gb} GB/mo)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=gb,
        unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
    )


def build_security_tier_bom(
    client: RetailPricesClient,
    region: str,
    tier: str,
    vm_count: int,
    sentinel_gb: Optional[float] = None,
    log_analytics_gb: Optional[float] = None,
    defender_manual_counts: Optional[Dict[str, float]] = None,
    add_log_analytics: bool = True,
) -> List[BomLine]:
    """Build security-tier lines for the chosen tier.

    `add_log_analytics=False` suppresses the Log Analytics ingestion line —
    use this when the Landing Zone already includes Log Analytics, to avoid
    double-billing.
    """
    cfg = SECURITY_TIERS.get(tier, SECURITY_TIERS["none"])
    lines: List[BomLine] = []

    # Defender plans included in this tier
    if cfg["defender_plans"]:
        lines.extend(
            build_defender_bom(
                client=client,
                region=region,
                enabled_keys=cfg["defender_plans"],
                vm_count=vm_count,
                manual_counts=defender_manual_counts or {},
            )
        )

    # CSPM (basic + full)
    if cfg["add_cspm"]:
        cspm = _cspm_line(client, region, vm_count)
        if cspm:
            lines.append(cspm)

    # Log Analytics (only if LZ isn't already carrying it)
    la_gb = log_analytics_gb if log_analytics_gb is not None else cfg["log_analytics_gb"]
    if add_log_analytics and la_gb > 0:
        la = _log_analytics_line(client, region, la_gb)
        if la:
            lines.append(la)

    # Sentinel (full only) — priced on the same ingestion volume
    if cfg["add_sentinel"]:
        s_gb = sentinel_gb if sentinel_gb is not None else la_gb
        sn = _sentinel_line(client, region, s_gb)
        if sn:
            lines.append(sn)

    return lines
