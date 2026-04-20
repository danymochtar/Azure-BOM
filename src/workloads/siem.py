"""SIEM / SOC cost model (Sentinel + Log Analytics + Defender).

Inputs the user provides (no VM extraction needed):
- GB/day of log ingestion
- Retention days beyond the free 90
- Commitment tier (PAYG or 100/200/500/1000/2000 GB/day bundles)

Line items produced:
- Microsoft Sentinel ingestion (priced by tier)
- Log Analytics retention (archive beyond the included 90 days)
- Defender for Cloud plans (CSPM + Servers if VM count > 0)
"""
from __future__ import annotations

from typing import List, Optional

from ..architecture.security_tier import _pick, _sentinel_line
from ..landing_zone.defender import build_defender_bom
from ..models import BomLine
from ..pricing.retail import RetailPricesClient, PriceRecord


# PAYG + commitment tier labels. The Retail feed uses meter names like
# "100 GB Commitment Tier", "200 GB Commitment Tier", etc.
SIEM_COMMITMENT_TIERS: dict = {
    "payg":  {"label": "Pay-as-you-go",       "gb_floor": 0,    "meter_hint": "pay-as-you-go"},
    "100":   {"label": "100 GB/day",          "gb_floor": 100,  "meter_hint": "100 gb commitment"},
    "200":   {"label": "200 GB/day",          "gb_floor": 200,  "meter_hint": "200 gb commitment"},
    "400":   {"label": "400 GB/day",          "gb_floor": 400,  "meter_hint": "400 gb commitment"},
    "500":   {"label": "500 GB/day",          "gb_floor": 500,  "meter_hint": "500 gb commitment"},
    "1000":  {"label": "1 TB/day",            "gb_floor": 1000, "meter_hint": "1000 gb commitment"},
    "2000":  {"label": "2 TB/day",            "gb_floor": 2000, "meter_hint": "2000 gb commitment"},
    "5000":  {"label": "5 TB/day",            "gb_floor": 5000, "meter_hint": "5000 gb commitment"},
}


def _la_commitment_line(
    client: RetailPricesClient, region: str, tier_key: str, gb_per_month: float
) -> Optional[BomLine]:
    """Log Analytics commitment tier line (priced per day, quoted per month)."""
    cfg = SIEM_COMMITMENT_TIERS[tier_key]
    recs = client.query(
        f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    hint = cfg["meter_hint"]
    chosen = _pick(recs, hint)
    if not chosen:
        return None
    # Commitment meters price per day. Quantity = days in month (30).
    return BomLine(
        category="Management",
        resource=f"Log Analytics ({cfg['label']} ingestion)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=30.0 if tier_key != "payg" else gb_per_month,
        unit="day" if tier_key != "payg" else "GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(
            chosen.retail_price * (30.0 if tier_key != "payg" else gb_per_month), 2
        ),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
        service_name="Azure Monitor",
        custom_name=f"Sentinel-LogAnalytics-{cfg['label']}",
    )


def _la_retention_line(
    client: RetailPricesClient, region: str, retention_gb: float
) -> Optional[BomLine]:
    if retention_gb <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "data retention")
    if not chosen:
        return None
    return BomLine(
        category="Management",
        resource=f"Log Analytics retention (~{retention_gb:.0f} GB-month)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=retention_gb,
        unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * retention_gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
        service_name="Azure Monitor",
        custom_name="Sentinel-LogAnalytics-Retention",
    )


def build_siem_bom(
    client: RetailPricesClient,
    region: str,
    gb_per_day: float,
    retention_days_over_free: int = 0,
    commitment_tier: str = "payg",
    defender_vm_count: int = 0,
    include_cspm: bool = True,
    app_name: str = "",
) -> List[BomLine]:
    """Build a SIEM/SOC assessment BOM."""
    lines: List[BomLine] = []
    gb_per_month = round(gb_per_day * 30, 2)

    # Ingestion line (Log Analytics commitment or PAYG)
    la_line = _la_commitment_line(client, region, commitment_tier, gb_per_month)
    if la_line:
        la_line.custom_name = f"{app_name}-{la_line.custom_name}" if app_name else la_line.custom_name
        lines.append(la_line)

    # Sentinel ingestion (priced on top of LA)
    sn = _sentinel_line(client, region, gb_per_month)
    if sn:
        sn.custom_name = f"{app_name}-Sentinel" if app_name else "Sentinel"
        lines.append(sn)

    # Retention beyond the included 90 days
    retention_gb = round(gb_per_month * (retention_days_over_free / 30.0), 2)
    ret = _la_retention_line(client, region, retention_gb)
    if ret:
        ret.custom_name = f"{app_name}-{ret.custom_name}" if app_name else ret.custom_name
        lines.append(ret)

    # Optional Defender for Servers if there are protected machines the
    # SIEM is monitoring; always include CSPM for posture if requested.
    if include_cspm or defender_vm_count > 0:
        plans = []
        if defender_vm_count > 0:
            plans.append("servers_p2")
        if plans:
            lines.extend(
                build_defender_bom(
                    client=client,
                    region=region,
                    enabled_keys=plans,
                    vm_count=defender_vm_count,
                )
            )
        if include_cspm:
            from ..architecture.security_tier import _cspm_line
            cspm = _cspm_line(client, region, max(defender_vm_count, 1))
            if cspm:
                cspm.custom_name = f"{app_name}-Defender-CSPM" if app_name else "Defender-CSPM"
                lines.append(cspm)

    return lines
