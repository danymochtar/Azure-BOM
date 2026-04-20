"""Individual security components + optional tier presets.

Each component is toggleable independently (checkbox UI). Tier presets remain
as convenience bundles but are no longer the primary entry point.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..landing_zone.defender import (
    DEFENDER_PLANS,
    build_defender_bom,
)
from ..models import BomLine
from ..pricing.retail import RetailPricesClient, PriceRecord, HOURS_PER_MONTH


# ---------------------------------------------------------------------------
# Component catalog (used by the checkbox UI)
# ---------------------------------------------------------------------------
# Each entry:
#   label          — human label shown next to the checkbox
#   default        — default checkbox state
#   scales_with    — "vms" | "manual" | "la_gb"
#   defender_plan  — optional key from DEFENDER_PLANS (servers_p2, sql_on_vms, ...)
#   unit_prompt    — placeholder for manual-count inputs
#   notes          — caption shown under the checkbox
SECURITY_COMPONENTS: Dict[str, Dict] = {
    "cspm": {
        "label": "Defender CSPM (enhanced posture management)",
        "default": True,
        "scales_with": "vms",
        "notes": "Per-resource posture + secure score + regulatory compliance.",
    },
    "servers_p2": {
        "label": "Defender for Servers Plan 2",
        "default": True,
        "scales_with": "vms",
        "defender_plan": "servers_p2",
        "notes": "Per protected VM. Includes EDR integration, FIM, JIT access.",
    },
    "sql_on_vms": {
        "label": "Defender for SQL servers on machines",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "sql_on_vms",
        "unit_prompt": "Total SQL vCores across VMs",
        "notes": "Per SQL vCore on IaaS. Skip if DB runs on Azure SQL PaaS.",
    },
    "storage": {
        "label": "Defender for Storage",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "storage",
        "unit_prompt": "Storage accounts",
        "notes": "Per storage account + malware scanning GB.",
    },
    "appsvc": {
        "label": "Defender for App Service",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "appsvc",
        "unit_prompt": "App Service Plan vCores",
        "notes": "Per App Service Plan vCore.",
    },
    "keyvault": {
        "label": "Defender for Key Vault",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "keyvault",
        "unit_prompt": "Vault transactions (per 10K)",
        "notes": "Per 10K vault transactions.",
    },
    "containers": {
        "label": "Defender for Containers",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "containers",
        "unit_prompt": "AKS node vCores",
        "notes": "Per AKS node vCore.",
    },
    "sentinel": {
        "label": "Microsoft Sentinel (PAYG ingestion)",
        "default": False,
        "scales_with": "la_gb",
        "notes": "Priced per GB on top of Log Analytics ingestion.",
    },
}


# ---------------------------------------------------------------------------
# Presets (optional quick-select; not used by the default UI)
# ---------------------------------------------------------------------------
SECURITY_TIERS: Dict[str, Dict] = {
    "none": {
        "label": "None",
        "description": "Compute only, no cloud-security telemetry.",
        "components": [],
    },
    "basic": {
        "label": "Basic (Defender CSPM only)",
        "description": "Foundational security posture.",
        "components": ["cspm"],
    },
    "full": {
        "label": "Full suite (all workload Defender plans + Sentinel)",
        "description": "Every Defender plan + Sentinel.",
        "components": [
            "cspm", "servers_p2", "sql_on_vms", "storage",
            "appsvc", "keyvault", "containers", "sentinel",
        ],
    },
}


def defender_plans_for_tier(tier: str) -> List[str]:
    return [
        SECURITY_COMPONENTS[k]["defender_plan"]
        for k in SECURITY_TIERS.get(tier, {}).get("components", [])
        if "defender_plan" in SECURITY_COMPONENTS.get(k, {})
    ]


# ---------------------------------------------------------------------------
# Helpers for lines the DEFENDER_PLANS list doesn't already cover
# ---------------------------------------------------------------------------

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
    """Defender CSPM (enhanced) — per billable resource/hour."""
    if vm_count <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Microsoft Defender for Cloud' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "defender cspm") or _pick(recs, "cspm")
    if not chosen:
        return None
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


# ---------------------------------------------------------------------------
# Primary entry point (checkbox-driven)
# ---------------------------------------------------------------------------

def build_security_bom(
    client: RetailPricesClient,
    region: str,
    enabled_keys: List[str],
    vm_count: int,
    la_gb: float = 0.0,
    manual_counts: Optional[Dict[str, float]] = None,
) -> List[BomLine]:
    """Build security lines from an explicit list of enabled component keys.

    `manual_counts` is keyed by the Defender plan key (e.g. 'sql_on_vms') —
    matches the UI's per-component quantity inputs.
    """
    manual_counts = manual_counts or {}
    lines: List[BomLine] = []

    # Collect Defender plans from enabled components and build via existing builder
    defender_enabled = [
        SECURITY_COMPONENTS[k]["defender_plan"]
        for k in enabled_keys
        if SECURITY_COMPONENTS.get(k, {}).get("defender_plan")
    ]
    if defender_enabled:
        lines.extend(
            build_defender_bom(
                client=client,
                region=region,
                enabled_keys=defender_enabled,
                vm_count=vm_count,
                manual_counts=manual_counts,
            )
        )

    # CSPM (not part of DEFENDER_PLANS)
    if "cspm" in enabled_keys:
        cspm = _cspm_line(client, region, vm_count)
        if cspm:
            lines.append(cspm)

    # Sentinel
    if "sentinel" in enabled_keys:
        sn = _sentinel_line(client, region, la_gb)
        if sn:
            lines.append(sn)

    return lines


# ---------------------------------------------------------------------------
# Backwards-compat wrapper (still callable, but use build_security_bom directly)
# ---------------------------------------------------------------------------

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
    keys = SECURITY_TIERS.get(tier, {}).get("components", [])
    return build_security_bom(
        client=client,
        region=region,
        enabled_keys=keys,
        vm_count=vm_count,
        la_gb=sentinel_gb or log_analytics_gb or 0.0,
        manual_counts=defender_manual_counts or {},
    )

