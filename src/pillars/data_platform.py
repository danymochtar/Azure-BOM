"""Data Platform pillar.

Microsoft Fabric, Synapse Dedicated SQL Pool, Cosmos DB, Azure SQL Database,
ADLS Gen2, Azure Data Factory, Event Hubs.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models import BomLine
from ..pricing.retail import HOURS_PER_MONTH, RetailPricesClient
from .azure_security import _pick


PILLAR_METADATA = {
    "key": "data_platform",
    "label": "Data Platform",
    "description": "Fabric, Synapse, Azure SQL DB, Cosmos, ADLS Gen2, Data Factory, Event Hubs.",
    "icon": "🗄",
    "needs_vm_extraction": False,
    "default_strategy": None,
}


FABRIC_CAPACITIES: List[str] = [
    "F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256", "F512", "F1024", "F2048",
]


def recommend_fabric_sku(pbi_user_count: int) -> str:
    """Recommend a Fabric F-SKU based on Power BI user count.

    Rule-of-thumb ladder (Microsoft sizing guide 2026):
      < 25 users          → F2
      25-50 users         → F4
      50-200 users        → F8-F16 (pick F16 at the upper end)
      200-500 users       → F32
      500-2,000 users     → F64
      2,000-10,000 users  → F128
      > 10,000 users      → F256+
    """
    if pbi_user_count < 25:
        return "F2"
    if pbi_user_count < 50:
        return "F4"
    if pbi_user_count < 100:
        return "F8"
    if pbi_user_count < 200:
        return "F16"
    if pbi_user_count < 500:
        return "F32"
    if pbi_user_count < 2000:
        return "F64"
    if pbi_user_count < 10_000:
        return "F128"
    return "F256"

AZURE_SQL_DB_TIERS: Dict[str, str] = {
    "gp":   "General Purpose (serverless / provisioned vCore)",
    "bc":   "Business Critical (zone-redundant, Always On replicas)",
    "hs":   "Hyperscale (scale-out storage)",
}

SQL_MI_TIERS: Dict[str, str] = {
    "gp":   "Managed Instance General Purpose",
    "bc":   "Managed Instance Business Critical",
}

POSTGRES_TIERS: Dict[str, Dict] = {
    "burstable":       {"label": "Burstable (B-series)",         "meter_hint": "burstable"},
    "general_purpose": {"label": "General Purpose (D-series)",   "meter_hint": "general purpose"},
    "memory_optimized":{"label": "Memory Optimized (E-series)",  "meter_hint": "memory optimized"},
}

MYSQL_TIERS = POSTGRES_TIERS  # Same tier structure

REDIS_TIERS: Dict[str, Dict] = {
    "basic":            {"label": "Basic (C-series, dev/test)",    "meter_hint": "basic c"},
    "standard":         {"label": "Standard (C-series, HA pair)",  "meter_hint": "standard c"},
    "premium":          {"label": "Premium (P-series, clustering)","meter_hint": "premium p"},
    "enterprise":       {"label": "Enterprise (E-series)",          "meter_hint": "enterprise e"},
    "enterprise_flash": {"label": "Enterprise Flash (F-series)",    "meter_hint": "enterprise flash"},
}

AZURE_FILES_TIERS: Dict[str, Dict] = {
    "standard_lrs": {"label": "Standard LRS (transaction-optimized)", "meter_hint": "standard lrs"},
    "standard_zrs": {"label": "Standard ZRS",                         "meter_hint": "standard zrs"},
    "standard_grs": {"label": "Standard GRS",                         "meter_hint": "standard grs"},
    "premium_lrs":  {"label": "Premium LRS (low-latency, SSD)",       "meter_hint": "premium lrs"},
    "premium_zrs":  {"label": "Premium ZRS",                          "meter_hint": "premium zrs"},
}

ADLS_TIERS: Dict[str, str] = {
    "hot":  "Hot (frequent access)",
    "cool": "Cool (infrequent)",
    "cold": "Cold (rare access)",
}

ADLS_REDUNDANCY: Dict[str, str] = {
    "LRS": "LRS (local)",
    "ZRS": "ZRS (zone redundant)",
    "GRS": "GRS (geo redundant)",
    "RA-GRS": "RA-GRS (geo redundant, read access)",
}

EVENT_HUBS_TIERS: Dict[str, str] = {
    "standard": "Standard (throughput units)",
    "premium":  "Premium (processing units)",
    "dedicated": "Dedicated (capacity units)",
}

DATABRICKS_TIERS: Dict[str, Dict] = {
    "standard":     {"label": "Standard",                      "meter_hint": "standard"},
    "premium":      {"label": "Premium (RBAC, Unity Catalog)", "meter_hint": "premium"},
    "serverless_sql": {"label": "Serverless SQL Pro",          "meter_hint": "serverless sql"},
    "serverless_ml":{"label": "Serverless Jobs / ML",          "meter_hint": "serverless jobs"},
}

POWER_BI_CAPACITIES: Dict[str, str] = {
    "F2":  "Fabric F2 → PBI surface",
    "F8":  "Fabric F8",
    "F16": "Fabric F16 (P1 equivalent)",
    "F32": "Fabric F32 (P2 equivalent)",
    "F64": "Fabric F64 (P3 equivalent)",
    "P1":  "Power BI Premium P1 (legacy)",
    "P2":  "Power BI Premium P2 (legacy)",
    "P3":  "Power BI Premium P3 (legacy)",
    "A1":  "Power BI Embedded A1",
    "A2":  "Power BI Embedded A2",
    "A3":  "Power BI Embedded A3",
    "A4":  "Power BI Embedded A4",
    "A5":  "Power BI Embedded A5",
    "A6":  "Power BI Embedded A6",
}

ADX_SKUS: Dict[str, str] = {
    "dev_d11_v2":          "Dev (D11 v2, 2 vCPU)",
    "compute_d14_v2":      "Compute D14 v2 (16 vCPU)",
    "compute_d32d_v4":     "Compute D32d v4 (32 vCPU)",
    "storage_e16s_v4":     "Storage-opt E16s v4",
    "storage_e64s_v4":     "Storage-opt E64s v4",
}


# ---------------------------------------------------------------------------
# Line builders
# ---------------------------------------------------------------------------

def _fabric_line(client: RetailPricesClient, region: str, sku: str, app_name: str) -> Optional[BomLine]:
    recs = client.query(
        f"serviceName eq 'Microsoft Fabric' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, sku.lower())
    if not chosen:
        return None
    qty = HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Microsoft Fabric capacity {sku}",
        sku=sku, meter=chosen.meter_name,
        region=region, quantity=qty, unit="hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Microsoft Fabric",
        custom_name=f"{app_name}-Fabric-{sku}" if app_name else f"Fabric-{sku}",
    )


def _cosmos_line(client: RetailPricesClient, region: str, ru_per_second: int, app_name: str) -> Optional[BomLine]:
    if ru_per_second <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Azure Cosmos DB' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "provisioned throughput")
    if not chosen:
        return None
    units_of_100 = ru_per_second / 100.0
    qty = units_of_100 * HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Cosmos DB provisioned throughput ({ru_per_second:,} RU/s)",
        sku=f"{ru_per_second}RU", meter=chosen.meter_name,
        region=region, quantity=round(qty, 2), unit="100 RU/s-hour",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Cosmos DB",
        custom_name=f"{app_name}-CosmosDB" if app_name else "CosmosDB",
    )


def _synapse_line(client: RetailPricesClient, region: str, dwu: int, app_name: str) -> Optional[BomLine]:
    if dwu <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'SQL Data Warehouse' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, f"dw{dwu}c") or _pick(recs, "compute")
    if not chosen:
        return None
    qty = HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Synapse Dedicated SQL Pool (DW{dwu}c)",
        sku=f"DW{dwu}c", meter=chosen.meter_name,
        region=region, quantity=qty, unit="hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Synapse Analytics",
        custom_name=f"{app_name}-Synapse-DW{dwu}" if app_name else f"Synapse-DW{dwu}",
    )


# Azure Hybrid Benefit discount on Azure SQL DB compute by tier. Retail feed
# doesn't expose a dedicated "compute-only" meter for SQL DB — AHB is applied
# at billing. We approximate with industry-standard discount percentages:
#   GP: SQL license is ~55% of the total vCore cost
#   BC: SQL license is ~33% of the total
#   HS: SQL license is ~25% of the total
# When AHB is on, subtract that license fraction from the vCore line.
_SQL_DB_AHB_DISCOUNT: Dict[str, float] = {
    "gp": 0.55,
    "bc": 0.33,
    "hs": 0.25,
}


# SQL DB Reserved Capacity discount approximations (Oct-2025):
#   1Y commitment ≈ 22% off PAYG (GP / BC / HS provisioned)
#   3Y commitment ≈ 37% off
# Savings Plan for SQL DB not currently offered by MS — only RI.
# https://azure.microsoft.com/en-us/pricing/details/azure-sql-database/single/
_SQL_DB_RI_DISCOUNT = {"payg": 0.0, "sp_1y": 0.0, "sp_3y": 0.0,
                       "ri_1y": 0.22, "ri_3y": 0.37}
_SQL_DB_BILLING_TAG = {"payg": "", "sp_1y": "", "sp_3y": "",
                       "ri_1y": " [RI 1Y]", "ri_3y": " [RI 3Y]"}
_SQL_DB_BILLING_LABEL = {"payg": "PAYG", "sp_1y": "PAYG", "sp_3y": "PAYG",
                         "ri_1y": "RI 1Y", "ri_3y": "RI 3Y"}


def _azure_sql_db_lines(
    client: RetailPricesClient, region: str,
    tier_key: str, vcores: int, storage_gb: int, zone_redundant: bool, app_name: str,
    use_ahb_sql: bool = False,
    pricing_mode: str = "payg",
) -> List[BomLine]:
    if tier_key == "none" or vcores <= 0:
        return []
    cfg_label = AZURE_SQL_DB_TIERS.get(tier_key, tier_key)
    recs = client.query(
        f"serviceName eq 'SQL Database' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    tier_hints = {
        "gp": "general purpose",
        "bc": "business critical",
        "hs": "hyperscale",
    }
    hint = tier_hints.get(tier_key, tier_key)
    compute = _pick(
        [r for r in recs if hint in (r.meter_name + r.product_name).lower()
         and "vcore" in r.meter_name.lower()],
        hint,
    ) or _pick(recs, hint)
    out: List[BomLine] = []
    if compute:
        qty = vcores * HOURS_PER_MONTH
        # AHB SQL discount stacks on top of any RI discount — both are
        # multiplicative on the PAYG rate.
        ahb_disc = _SQL_DB_AHB_DISCOUNT.get(tier_key, 0.0) if use_ahb_sql else 0.0
        ri_disc = _SQL_DB_RI_DISCOUNT.get(pricing_mode, 0.0)
        effective_rate = compute.retail_price * (1.0 - ahb_disc) * (1.0 - ri_disc)
        ahb_tag = f" [AHB -{int(ahb_disc*100)}%]" if use_ahb_sql else ""
        ri_tag = _SQL_DB_BILLING_TAG.get(pricing_mode, "")
        zr_tag = " [ZR]" if zone_redundant else ""
        billing_lbl = _SQL_DB_BILLING_LABEL.get(pricing_mode, "PAYG")
        # Per-line assumption stamps each discount with its source rate
        # so reviewers can audit the math.
        notes: List[str] = []
        if ahb_disc > 0:
            notes.append(f"AHB SQL: -{int(ahb_disc*100)}% on {tier_key.upper()} tier")
        if ri_disc > 0:
            notes.append(
                f"SQL DB Reserved Capacity: -{int(ri_disc*100)}% "
                f"({pricing_mode.upper()}); approximation per MS published "
                f"discount ranges"
            )
        out.append(BomLine(
            category="Data + Analytics",
            resource=f"Azure SQL Database {cfg_label} × {vcores} vCores{zr_tag}{ahb_tag}{ri_tag}",
            sku=compute.sku_name or compute.product_name, meter=compute.meter_name,
            region=region, quantity=qty, unit="vCore-hours",
            unit_price=effective_rate,
            monthly_cost=round(effective_rate * qty, 2),
            currency=compute.currency_code,
            source="retail-prices" + (" + ri-approx" if ri_disc > 0 else ""),
            product_id=compute.product_id, sku_id=compute.sku_id, meter_id=compute.meter_id,
            service_name="Azure SQL Database",
            custom_name=f"{app_name}-SQLDB-{tier_key}" if app_name else f"SQLDB-{tier_key}",
            billing_term=billing_lbl,
            assumption=". ".join(notes),
        ))
    if storage_gb > 0:
        storage = _pick(
            [r for r in recs if "storage" in r.meter_name.lower() and hint in (r.product_name + r.meter_name).lower()],
            "storage",
        )
        if storage:
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"Azure SQL Database storage ({storage_gb} GB, {cfg_label})",
                sku=storage.sku_name or storage.product_name, meter=storage.meter_name,
                region=region, quantity=storage_gb, unit="GB",
                unit_price=storage.retail_price,
                monthly_cost=round(storage.retail_price * storage_gb, 2),
                currency=storage.currency_code,
                source="retail-prices",
                product_id=storage.product_id, sku_id=storage.sku_id, meter_id=storage.meter_id,
                service_name="Azure SQL Database",
                custom_name=f"{app_name}-SQLDB-{tier_key}-Storage" if app_name else f"SQLDB-{tier_key}-Storage",
            ))
    return out


def _adls_gen2_line(
    client: RetailPricesClient, region: str,
    storage_gb: int, tier_key: str, redundancy: str, app_name: str,
) -> Optional[BomLine]:
    if storage_gb <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Storage' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    hint_parts = [tier_key.lower(), redundancy.lower().replace("ra-", "")]
    # ADLS Gen2 priced via "Hierarchical Namespace" SKU. The right
    # billable meter is the per-GB STORAGE / DATA STORED charge —
    # NOT the per-operation meters (Read/Write/List/Delete Ops) which
    # are often $0 in the free tier and would silently zero out the
    # BOM. Require either "data stored" or "storage" in the meter
    # name, and skip $0 records when paid alternatives exist.
    candidates = []
    for r in recs:
        meter_l = (r.meter_name + " " + r.product_name).lower()
        if not all(h in meter_l for h in hint_parts):
            continue
        if "hierarchical" not in meter_l:
            continue
        if "operation" in meter_l or "transaction" in meter_l:
            # Skip per-operation meters; we want per-GB storage.
            continue
        candidates.append(r)
    chosen = None
    if candidates:
        non_zero = [r for r in candidates if r.retail_price > 0]
        pool = non_zero if non_zero else candidates
        chosen = min(pool, key=lambda r: r.retail_price)
    if not chosen:
        # Fallback: any storage GB meter matching the tier
        chosen = _pick(recs, tier_key.lower())
    if not chosen:
        return None
    return BomLine(
        category="Data + Analytics",
        resource=f"ADLS Gen2 ({tier_key}, {redundancy}, {storage_gb} GB)",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=storage_gb, unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * storage_gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Data Lake Storage Gen2",
        custom_name=f"{app_name}-ADLS-{tier_key}" if app_name else f"ADLS-{tier_key}",
    )


def _adf_lines(
    client: RetailPricesClient, region: str,
    pipeline_runs: int, diu_hours: float, ssis_ir_hours: float, app_name: str,
) -> List[BomLine]:
    out: List[BomLine] = []
    recs = client.query(
        f"serviceName eq 'Data Factory v2' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    if pipeline_runs > 0:
        chosen = _pick(recs, "orchestration")
        if chosen:
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"Azure Data Factory orchestration — {pipeline_runs:,} activity runs",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=pipeline_runs, unit="activity run",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * pipeline_runs, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Data Factory",
                custom_name=f"{app_name}-ADF-Runs" if app_name else "ADF-Runs",
            ))
    if diu_hours > 0:
        chosen = _pick(recs, "data movement")
        if chosen:
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"Azure Data Factory data movement — {diu_hours} DIU-hours",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=diu_hours, unit="DIU-hour",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * diu_hours, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Data Factory",
                custom_name=f"{app_name}-ADF-DIU" if app_name else "ADF-DIU",
            ))
    if ssis_ir_hours > 0:
        chosen = _pick(recs, "ssis")
        if chosen:
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"Azure Data Factory SSIS IR — {ssis_ir_hours} hours",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=ssis_ir_hours, unit="hours",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * ssis_ir_hours, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Data Factory",
                custom_name=f"{app_name}-ADF-SSIS" if app_name else "ADF-SSIS",
            ))
    return out


def _event_hubs_line(
    client: RetailPricesClient, region: str, tier_key: str, units: int, app_name: str,
) -> Optional[BomLine]:
    if tier_key == "none" or units <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Event Hubs' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    hint_map = {"standard": "throughput unit", "premium": "processing unit", "dedicated": "capacity unit"}
    hint = hint_map.get(tier_key, "unit")
    chosen = _pick(recs, hint)
    if not chosen:
        return None
    qty = units * HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Event Hubs {EVENT_HUBS_TIERS[tier_key]} × {units}",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=qty, unit=f"{hint}-hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Event Hubs",
        custom_name=f"{app_name}-EventHubs-{tier_key}" if app_name else f"EventHubs-{tier_key}",
    )


def _databricks_line(
    client: RetailPricesClient, region: str,
    tier_key: str, dbu_hours: float, app_name: str,
) -> Optional[BomLine]:
    if tier_key == "none" or dbu_hours <= 0:
        return None
    cfg = DATABRICKS_TIERS.get(tier_key)
    if not cfg:
        return None
    recs = client.query(
        f"serviceName eq 'Azure Databricks' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, cfg["meter_hint"])
    if not chosen:
        return None
    return BomLine(
        category="Data + Analytics",
        resource=f"Azure Databricks {cfg['label']} — {dbu_hours:,.0f} DBU-hours",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=dbu_hours, unit="DBU-hour",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * dbu_hours, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Databricks",
        custom_name=f"{app_name}-Databricks-{tier_key}" if app_name else f"Databricks-{tier_key}",
    )


def _synapse_serverless_line(
    client: RetailPricesClient, region: str,
    tb_scanned_per_month: float, app_name: str,
) -> Optional[BomLine]:
    if tb_scanned_per_month <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'SQL Data Warehouse' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "serverless") or _pick(recs, "on-demand")
    if not chosen:
        return None
    return BomLine(
        category="Data + Analytics",
        resource=f"Synapse Serverless SQL Pool — {tb_scanned_per_month:,.2f} TB scanned",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=tb_scanned_per_month, unit="TB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * tb_scanned_per_month, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Synapse Analytics",
        custom_name=f"{app_name}-Synapse-Serverless" if app_name else "Synapse-Serverless",
    )


def _power_bi_capacity_line(
    client: RetailPricesClient, region: str,
    sku: str, app_name: str,
) -> Optional[BomLine]:
    if sku == "none":
        return None
    # Fabric F-SKUs go through Fabric service; A/P go through Power BI Embedded.
    if sku.startswith("F"):
        return _fabric_line(client, region, sku, app_name)
    recs = client.query(
        f"serviceName eq 'Power BI Embedded' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, sku.lower())
    if not chosen:
        return None
    qty = HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Power BI Embedded capacity {sku}",
        sku=sku, meter=chosen.meter_name,
        region=region, quantity=qty, unit="hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Power BI Embedded",
        custom_name=f"{app_name}-PowerBI-{sku}" if app_name else f"PowerBI-{sku}",
    )


def _adx_line(
    client: RetailPricesClient, region: str,
    sku_key: str, instance_count: int, app_name: str,
) -> Optional[BomLine]:
    if sku_key == "none" or instance_count <= 0:
        return None
    label = ADX_SKUS.get(sku_key, sku_key)
    recs = client.query(
        f"serviceName eq 'Azure Data Explorer' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    hint = sku_key.replace("_", " ").lower()
    chosen = _pick(recs, hint) or _pick(recs, "engine")
    if not chosen:
        return None
    qty = instance_count * HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Azure Data Explorer {label} × {instance_count}",
        sku=sku_key, meter=chosen.meter_name,
        region=region, quantity=qty, unit="instance-hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Data Explorer",
        custom_name=f"{app_name}-ADX-{sku_key}" if app_name else f"ADX-{sku_key}",
    )


def _cosmos_serverless_line(
    client: RetailPricesClient, region: str,
    request_units_month: float, storage_gb: float, app_name: str,
) -> List[BomLine]:
    out: List[BomLine] = []
    recs = client.query(
        f"serviceName eq 'Azure Cosmos DB' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    if request_units_month > 0:
        chosen = _pick(recs, "serverless ru")
        if chosen:
            # Serverless is priced per million RU
            mru = request_units_month / 1_000_000.0
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"Cosmos DB Serverless — {request_units_month:,.0f} RU / mo",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=mru, unit="million RU",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * mru, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Cosmos DB",
                custom_name=f"{app_name}-CosmosDB-Serverless" if app_name else "CosmosDB-Serverless",
            ))
    if storage_gb > 0:
        chosen = _pick(recs, "data stored")
        if chosen:
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"Cosmos DB storage — {storage_gb:,.1f} GB",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=storage_gb, unit="GB",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * storage_gb, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Cosmos DB",
                custom_name=f"{app_name}-CosmosDB-Storage" if app_name else "CosmosDB-Storage",
            ))
    return out


# ---------------------------------------------------------------------------
# Azure SQL Managed Instance
# ---------------------------------------------------------------------------

# AHB for Managed Instance is typically ~40 % off compute for both tiers
_SQL_MI_AHB_DISCOUNT: Dict[str, float] = {"gp": 0.40, "bc": 0.40}


def _azure_sql_mi_lines(
    client: RetailPricesClient, region: str,
    tier_key: str, vcores: int, storage_gb: int, app_name: str,
    use_ahb_sql: bool = False,
) -> List[BomLine]:
    if tier_key == "none" or vcores <= 0:
        return []
    recs = client.query(
        f"serviceName eq 'SQL Managed Instance' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    hint = "general purpose" if tier_key == "gp" else "business critical"
    compute = _pick(
        [r for r in recs if hint in (r.meter_name + r.product_name).lower()
         and "vcore" in r.meter_name.lower()],
        hint,
    ) or _pick(recs, hint)
    out: List[BomLine] = []
    if compute:
        qty = vcores * HOURS_PER_MONTH
        discount = _SQL_MI_AHB_DISCOUNT.get(tier_key, 0.0) if use_ahb_sql else 0.0
        rate = compute.retail_price * (1.0 - discount)
        ahb_tag = f" [AHB -{int(discount*100)}%]" if use_ahb_sql else ""
        out.append(BomLine(
            category="Data + Analytics",
            resource=f"Azure SQL Managed Instance {SQL_MI_TIERS[tier_key]} × {vcores} vCores{ahb_tag}",
            sku=compute.sku_name or compute.product_name, meter=compute.meter_name,
            region=region, quantity=qty, unit="vCore-hours",
            unit_price=rate, monthly_cost=round(rate * qty, 2),
            currency=compute.currency_code,
            source="retail-prices",
            product_id=compute.product_id, sku_id=compute.sku_id, meter_id=compute.meter_id,
            service_name="Azure SQL Managed Instance",
            custom_name=f"{app_name}-SQLMI-{tier_key}" if app_name else f"SQLMI-{tier_key}",
        ))
    if storage_gb > 0:
        storage = _pick(
            [r for r in recs if "storage" in r.meter_name.lower()],
            "storage",
        )
        if storage:
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"Azure SQL MI storage ({storage_gb} GB)",
                sku=storage.sku_name or storage.product_name, meter=storage.meter_name,
                region=region, quantity=storage_gb, unit="GB",
                unit_price=storage.retail_price,
                monthly_cost=round(storage.retail_price * storage_gb, 2),
                currency=storage.currency_code,
                source="retail-prices",
                product_id=storage.product_id, sku_id=storage.sku_id, meter_id=storage.meter_id,
                service_name="Azure SQL Managed Instance",
                custom_name=f"{app_name}-SQLMI-{tier_key}-Storage" if app_name else f"SQLMI-{tier_key}-Storage",
            ))
    return out


# ---------------------------------------------------------------------------
# PostgreSQL / MySQL Flexible Server
# ---------------------------------------------------------------------------

def _flex_server_lines(
    client: RetailPricesClient, region: str,
    service_name: str, service_display: str, custom_prefix: str,
    tier_key: str, vcores: int, storage_gb: int, ha_enabled: bool,
    app_name: str,
) -> List[BomLine]:
    if tier_key == "none" or vcores <= 0:
        return []
    cfg = POSTGRES_TIERS.get(tier_key)  # same shape as MySQL
    if not cfg:
        return []
    recs = client.query(
        f"serviceName eq '{service_name}' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    compute = _pick(
        [r for r in recs if "flexible" in (r.meter_name + r.product_name).lower()
         and cfg["meter_hint"] in (r.meter_name + r.product_name).lower()],
        cfg["meter_hint"],
    ) or _pick(recs, cfg["meter_hint"])
    out: List[BomLine] = []
    if compute:
        # HA ≈ 2 × compute (primary + standby)
        mult = 2 if ha_enabled else 1
        qty = vcores * HOURS_PER_MONTH * mult
        ha_tag = " [HA]" if ha_enabled else ""
        out.append(BomLine(
            category="Data + Analytics",
            resource=f"{service_display} Flexible {cfg['label']} × {vcores} vCores{ha_tag}",
            sku=compute.sku_name or compute.product_name, meter=compute.meter_name,
            region=region, quantity=qty, unit="vCore-hours",
            unit_price=compute.retail_price,
            monthly_cost=round(compute.retail_price * qty, 2),
            currency=compute.currency_code,
            source="retail-prices",
            product_id=compute.product_id, sku_id=compute.sku_id, meter_id=compute.meter_id,
            service_name=service_display,
            custom_name=f"{app_name}-{custom_prefix}-{tier_key}" if app_name else f"{custom_prefix}-{tier_key}",
        ))
    if storage_gb > 0:
        storage = _pick(
            [r for r in recs if "storage" in r.meter_name.lower() and "flexible" in (r.product_name + r.meter_name).lower()],
            "storage",
        )
        if storage:
            mult = 2 if ha_enabled else 1
            out.append(BomLine(
                category="Data + Analytics",
                resource=f"{service_display} Flexible storage ({storage_gb} GB){' [HA]' if ha_enabled else ''}",
                sku=storage.sku_name or storage.product_name, meter=storage.meter_name,
                region=region, quantity=storage_gb * mult, unit="GB",
                unit_price=storage.retail_price,
                monthly_cost=round(storage.retail_price * storage_gb * mult, 2),
                currency=storage.currency_code,
                source="retail-prices",
                product_id=storage.product_id, sku_id=storage.sku_id, meter_id=storage.meter_id,
                service_name=service_display,
                custom_name=f"{app_name}-{custom_prefix}-{tier_key}-Storage" if app_name else f"{custom_prefix}-{tier_key}-Storage",
            ))
    return out


# ---------------------------------------------------------------------------
# Azure Cache for Redis
# ---------------------------------------------------------------------------

def _redis_line(
    client: RetailPricesClient, region: str,
    tier_key: str, sku: str, app_name: str,
) -> Optional[BomLine]:
    if tier_key == "none" or not sku or sku == "none":
        return None
    cfg = REDIS_TIERS.get(tier_key)
    if not cfg:
        return None
    recs = client.query(
        f"serviceName eq 'Azure Cache for Redis' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    candidates = [
        r for r in recs
        if cfg["meter_hint"] in (r.meter_name + r.product_name).lower()
        and sku.lower() in (r.meter_name + r.sku_name + r.product_name).lower()
    ]
    chosen = _pick(candidates, cfg["meter_hint"]) or _pick(recs, cfg["meter_hint"])
    if not chosen:
        return None
    qty = HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Azure Cache for Redis {cfg['label']} {sku}",
        sku=sku, meter=chosen.meter_name,
        region=region, quantity=qty, unit="hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Cache for Redis",
        custom_name=f"{app_name}-Redis-{sku}" if app_name else f"Redis-{sku}",
    )


# ---------------------------------------------------------------------------
# Azure Files (Standard + Premium)
# ---------------------------------------------------------------------------

def _azure_files_line(
    client: RetailPricesClient, region: str,
    tier_key: str, storage_gb: int, app_name: str,
) -> Optional[BomLine]:
    if tier_key == "none" or storage_gb <= 0:
        return None
    cfg = AZURE_FILES_TIERS.get(tier_key)
    if not cfg:
        return None
    recs = client.query(
        f"serviceName eq 'Storage' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = None
    for r in recs:
        blob = (r.meter_name + " " + r.product_name).lower()
        if "file" in blob and cfg["meter_hint"] in blob:
            if chosen is None or r.retail_price < chosen.retail_price:
                chosen = r
    if not chosen:
        chosen = _pick(recs, cfg["meter_hint"])
    if not chosen:
        return None
    return BomLine(
        category="Data + Analytics",
        resource=f"Azure Files {cfg['label']} — {storage_gb:,} GB",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=storage_gb, unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * storage_gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Files",
        custom_name=f"{app_name}-AzureFiles-{tier_key}" if app_name else f"AzureFiles-{tier_key}",
    )


# Back-compat
def build_data_platform_bom(
    client: RetailPricesClient, region: str,
    fabric_sku: Optional[str] = None, cosmos_ru_per_second: int = 0, synapse_dwu: int = 0,
    app_name: str = "",
) -> List[BomLine]:
    lines: List[BomLine] = []
    if fabric_sku:
        l = _fabric_line(client, region, fabric_sku, app_name)
        if l: lines.append(l)
    if cosmos_ru_per_second > 0:
        l = _cosmos_line(client, region, cosmos_ru_per_second, app_name)
        if l: lines.append(l)
    if synapse_dwu > 0:
        l = _synapse_line(client, region, synapse_dwu, app_name)
        if l: lines.append(l)
    return lines


# ---------------------------------------------------------------------------
# Pillar contract
# ---------------------------------------------------------------------------

def _sel_idx(options: list, saved, default_idx: int = 0) -> int:
    try:
        return options.index(saved)
    except (ValueError, TypeError):
        return default_idx


def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    # Pull suggested values from prefs (auto-simulate overlays them here).
    sql_pref = prefs.get("azure_sql_db", {}) or {}
    adls_pref = prefs.get("adls_gen2", {}) or {}
    adf_pref = prefs.get("adf", {}) or {}
    eh_pref = prefs.get("event_hubs", {}) or {}
    db_pref = prefs.get("databricks", {}) or {}
    ss_pref = prefs.get("synapse_serverless", {}) or {}
    pbi_pref = prefs.get("power_bi", {}) or {}
    adx_pref = prefs.get("adx", {}) or {}
    cosmos_sl_pref = prefs.get("cosmos_serverless", {}) or {}

    # --- Fabric-first: Power BI users drives capacity recommendation ---
    pbi_users = int(prefs.get("pbi_users", 0))
    pbi_users = st.number_input(
        "Existing Power BI users (drives Fabric F-SKU recommendation)",
        min_value=0, max_value=1_000_000,
        value=pbi_users, step=25, key="dp_pbi_users",
        help=(
            "Microsoft Fabric is the recommended default for data platform. "
            "The F-SKU sizes against reporting/interactive user concurrency. "
            "Enter your existing + planned Power BI users."
        ),
    )
    rec_sku = recommend_fabric_sku(pbi_users) if pbi_users > 0 else None
    if rec_sku:
        st.info(
            f"**Recommended Fabric SKU for {pbi_users:,} PBI users: `{rec_sku}`**. "
            "Fabric capacity scales non-linearly — over-sizing is common. "
            "Pick a smaller F-SKU to start if workload is bursty."
        )

    with st.expander("Microsoft Fabric / Cosmos / Synapse", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            _fabric_opts = ["(none)"] + FABRIC_CAPACITIES
            _saved_f = prefs.get("fabric_sku") or rec_sku or "(none)"
            fabric_sku = st.selectbox(
                "Fabric capacity", _fabric_opts, key="fabric_sku",
                index=_sel_idx(_fabric_opts, _saved_f),
            )
            fabric_sku = None if fabric_sku == "(none)" else fabric_sku
        with c2:
            cosmos_ru = st.number_input(
                "Cosmos DB RU/s", min_value=0,
                value=int(prefs.get("cosmos_ru_per_second", 0)), step=400,
                key="cosmos_ru",
            )
        with c3:
            synapse_dwu = st.number_input(
                "Synapse Dedicated SQL Pool DWU", min_value=0,
                value=int(prefs.get("synapse_dwu", 0)), step=100,
                key="synapse_dwu",
            )

    with st.expander("Azure SQL Database", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _sql_opts = ["none"] + list(AZURE_SQL_DB_TIERS.keys())
            sql_tier = st.selectbox(
                "Service tier", _sql_opts, key="sqldb_tier",
                index=_sel_idx(_sql_opts, sql_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else AZURE_SQL_DB_TIERS[k].split(" (")[0],
            )
        with c2:
            sql_vcores = st.number_input(
                "vCores", min_value=0, value=int(sql_pref.get("vcores", 0)),
                step=2, disabled=(sql_tier == "none"), key="sqldb_vcores",
            )
        with c3:
            sql_storage = st.number_input(
                "Storage GB", min_value=0, value=int(sql_pref.get("storage_gb", 0)),
                step=50, disabled=(sql_tier == "none"), key="sqldb_storage",
            )
        with c4:
            sql_zr = st.checkbox(
                "Zone-redundant", value=bool(sql_pref.get("zone_redundant", False)),
                disabled=(sql_tier == "none"), key="sqldb_zr",
            )

    with st.expander("ADLS Gen2 (Data Lake Storage)", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            _adls_opts = ["none"] + list(ADLS_TIERS.keys())
            adls_tier = st.selectbox(
                "Tier", _adls_opts, key="adls_tier",
                index=_sel_idx(_adls_opts, adls_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else ADLS_TIERS[k],
            )
        with c2:
            _red_opts = list(ADLS_REDUNDANCY.keys())
            adls_redundancy = st.selectbox(
                "Redundancy", _red_opts, key="adls_redundancy",
                index=_sel_idx(_red_opts, adls_pref.get("redundancy", "LRS")),
                format_func=lambda k: ADLS_REDUNDANCY[k],
                disabled=(adls_tier == "none"),
            )
        with c3:
            adls_gb = st.number_input(
                "Storage GB", min_value=0, value=int(adls_pref.get("storage_gb", 0)),
                step=1000, disabled=(adls_tier == "none"), key="adls_gb",
            )

    with st.expander("Azure Data Factory", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            adf_runs = st.number_input(
                "Activity runs / month", min_value=0,
                value=int(adf_pref.get("pipeline_runs", 0)), step=1000,
                key="adf_runs",
            )
        with c2:
            adf_diu = st.number_input(
                "Data movement DIU-hours / month", min_value=0.0,
                value=float(adf_pref.get("diu_hours", 0.0)), step=5.0,
                key="adf_diu",
            )
        with c3:
            adf_ssis = st.number_input(
                "SSIS IR hours / month", min_value=0.0,
                value=float(adf_pref.get("ssis_ir_hours", 0.0)), step=10.0,
                key="adf_ssis",
            )

    with st.expander("Event Hubs", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _eh_opts = ["none"] + list(EVENT_HUBS_TIERS.keys())
            eh_tier = st.selectbox(
                "Tier", _eh_opts, key="eh_tier",
                index=_sel_idx(_eh_opts, eh_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else EVENT_HUBS_TIERS[k],
            )
        with c2:
            eh_units = st.number_input(
                "Units", min_value=0, value=int(eh_pref.get("units", 0)),
                step=1, disabled=(eh_tier == "none"), key="eh_units",
            )

    with st.expander("Azure Databricks", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _db_opts = ["none"] + list(DATABRICKS_TIERS.keys())
            db_tier = st.selectbox(
                "Workspace tier", _db_opts, key="db_tier",
                index=_sel_idx(_db_opts, db_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else DATABRICKS_TIERS[k]["label"],
            )
        with c2:
            db_dbu = st.number_input(
                "DBU-hours / month", min_value=0.0,
                value=float(db_pref.get("dbu_hours", 0.0)), step=100.0,
                disabled=(db_tier == "none"), key="db_dbu",
                help="1 DBU ≈ 1 hour of a single-core worker on Standard. A small cluster of 4 nodes × 8hrs/day × 22 days ≈ 700 DBU.",
            )

    with st.expander("Synapse Serverless SQL (pay-per-TB)", expanded=False):
        synapse_serverless_tb = st.number_input(
            "TB scanned / month", min_value=0.0,
            value=float(ss_pref.get("tb_per_month", 0.0)), step=0.5,
            key="synapse_serverless_tb",
            help="Serverless SQL pool charges ~$5/TB scanned. Great for ad-hoc on ADLS.",
        )

    with st.expander("Microsoft Fabric / Power BI Embedded capacity", expanded=False):
        _pbi_opts = ["none"] + list(POWER_BI_CAPACITIES.keys())
        pbi_sku = st.selectbox(
            "Capacity SKU", _pbi_opts, key="pbi_sku",
            index=_sel_idx(_pbi_opts, pbi_pref.get("sku", "none")),
            format_func=lambda k: "None" if k == "none" else POWER_BI_CAPACITIES[k],
            help="F-SKUs route through Microsoft Fabric; P and A SKUs route through Power BI Embedded.",
        )

    with st.expander("Azure Data Explorer (Kusto)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _adx_opts = ["none"] + list(ADX_SKUS.keys())
            adx_sku = st.selectbox(
                "Cluster SKU", _adx_opts, key="adx_sku",
                index=_sel_idx(_adx_opts, adx_pref.get("sku", "none")),
                format_func=lambda k: "None" if k == "none" else ADX_SKUS[k],
            )
        with c2:
            adx_count = st.number_input(
                "Instance count", min_value=0,
                value=int(adx_pref.get("count", 0)), step=1,
                disabled=(adx_sku == "none"), key="adx_count",
            )

    with st.expander("Cosmos DB Serverless (per-RU billing)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            cosmos_sl_ru = st.number_input(
                "Request Units / month", min_value=0,
                value=int(cosmos_sl_pref.get("request_units_month", 0)),
                step=1_000_000, key="cosmos_sl_ru",
                help="Serverless: ~$0.25 per million RU consumed. Use provisioned above for sustained throughput.",
            )
        with c2:
            cosmos_sl_storage = st.number_input(
                "Cosmos storage GB", min_value=0.0,
                value=float(cosmos_sl_pref.get("storage_gb", 0.0)), step=10.0,
                key="cosmos_sl_storage",
            )

    # --- Azure SQL Managed Instance ---
    mi_pref = prefs.get("azure_sql_mi", {}) or {}
    with st.expander("Azure SQL Managed Instance", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            _mi_opts = ["none"] + list(SQL_MI_TIERS.keys())
            mi_tier = st.selectbox(
                "Tier", _mi_opts, key="mi_tier",
                index=_sel_idx(_mi_opts, mi_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else SQL_MI_TIERS[k],
            )
        with c2:
            mi_vcores = st.number_input(
                "vCores", min_value=0, value=int(mi_pref.get("vcores", 0)),
                step=4, disabled=(mi_tier == "none"), key="mi_vcores",
            )
        with c3:
            mi_storage = st.number_input(
                "Storage GB", min_value=0, value=int(mi_pref.get("storage_gb", 0)),
                step=50, disabled=(mi_tier == "none"), key="mi_storage",
            )

    # --- PostgreSQL Flexible ---
    pg_pref = prefs.get("postgres_flexible", {}) or {}
    with st.expander("Azure Database for PostgreSQL — Flexible Server", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _pg_opts = ["none"] + list(POSTGRES_TIERS.keys())
            pg_tier = st.selectbox(
                "Compute tier", _pg_opts, key="pg_tier",
                index=_sel_idx(_pg_opts, pg_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else POSTGRES_TIERS[k]["label"],
            )
        with c2:
            pg_vcores = st.number_input(
                "vCores", min_value=0, value=int(pg_pref.get("vcores", 0)),
                step=2, disabled=(pg_tier == "none"), key="pg_vcores",
            )
        with c3:
            pg_storage = st.number_input(
                "Storage GB", min_value=0, value=int(pg_pref.get("storage_gb", 0)),
                step=50, disabled=(pg_tier == "none"), key="pg_storage",
            )
        with c4:
            pg_ha = st.checkbox(
                "HA (zone-redundant)", value=bool(pg_pref.get("ha_enabled", False)),
                disabled=(pg_tier == "none"), key="pg_ha",
            )

    # --- MySQL Flexible ---
    my_pref = prefs.get("mysql_flexible", {}) or {}
    with st.expander("Azure Database for MySQL — Flexible Server", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _my_opts = ["none"] + list(MYSQL_TIERS.keys())
            my_tier = st.selectbox(
                "Compute tier", _my_opts, key="my_tier",
                index=_sel_idx(_my_opts, my_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else MYSQL_TIERS[k]["label"],
            )
        with c2:
            my_vcores = st.number_input(
                "vCores", min_value=0, value=int(my_pref.get("vcores", 0)),
                step=2, disabled=(my_tier == "none"), key="my_vcores",
            )
        with c3:
            my_storage = st.number_input(
                "Storage GB", min_value=0, value=int(my_pref.get("storage_gb", 0)),
                step=50, disabled=(my_tier == "none"), key="my_storage",
            )
        with c4:
            my_ha = st.checkbox(
                "HA (zone-redundant)", value=bool(my_pref.get("ha_enabled", False)),
                disabled=(my_tier == "none"), key="my_ha",
            )

    # --- Azure Cache for Redis ---
    redis_pref = prefs.get("redis_cache", {}) or {}
    with st.expander("Azure Cache for Redis", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _r_opts = ["none"] + list(REDIS_TIERS.keys())
            redis_tier = st.selectbox(
                "Tier", _r_opts, key="redis_tier",
                index=_sel_idx(_r_opts, redis_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else REDIS_TIERS[k]["label"],
            )
        with c2:
            redis_sku = st.text_input(
                "SKU / size (e.g. C1, P2, E10)",
                value=str(redis_pref.get("sku", "")),
                disabled=(redis_tier == "none"), key="redis_sku",
                help="Basic/Standard use C0-C6. Premium uses P1-P5. Enterprise uses E10/E20/.../E400.",
            )

    # --- Azure Files ---
    files_pref = prefs.get("azure_files", {}) or {}
    with st.expander("Azure Files (Standard / Premium)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _f_opts = ["none"] + list(AZURE_FILES_TIERS.keys())
            files_tier = st.selectbox(
                "Tier", _f_opts, key="files_tier",
                index=_sel_idx(_f_opts, files_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else AZURE_FILES_TIERS[k]["label"],
            )
        with c2:
            files_gb = st.number_input(
                "Provisioned GB", min_value=0, value=int(files_pref.get("storage_gb", 0)),
                step=100, disabled=(files_tier == "none"), key="files_gb",
            )

    return {
        "pbi_users": int(pbi_users),
        "fabric_recommended_sku": rec_sku,
        "fabric_sku": fabric_sku,
        "cosmos_ru_per_second": int(cosmos_ru),
        "synapse_dwu": int(synapse_dwu),
        "azure_sql_db": {
            "tier": sql_tier, "vcores": int(sql_vcores),
            "storage_gb": int(sql_storage), "zone_redundant": bool(sql_zr),
        },
        "adls_gen2": {
            "tier": adls_tier, "redundancy": adls_redundancy,
            "storage_gb": int(adls_gb),
        },
        "adf": {
            "pipeline_runs": int(adf_runs), "diu_hours": float(adf_diu), "ssis_ir_hours": float(adf_ssis),
        },
        "event_hubs": {"tier": eh_tier, "units": int(eh_units)},
        "databricks": {"tier": db_tier, "dbu_hours": float(db_dbu)},
        "synapse_serverless": {"tb_per_month": float(synapse_serverless_tb)},
        "power_bi": {"sku": pbi_sku},
        "adx": {"sku": adx_sku, "count": int(adx_count)},
        "cosmos_serverless": {
            "request_units_month": int(cosmos_sl_ru),
            "storage_gb": float(cosmos_sl_storage),
        },
        "azure_sql_mi": {
            "tier": mi_tier, "vcores": int(mi_vcores), "storage_gb": int(mi_storage),
        },
        "postgres_flexible": {
            "tier": pg_tier, "vcores": int(pg_vcores),
            "storage_gb": int(pg_storage), "ha_enabled": bool(pg_ha),
        },
        "mysql_flexible": {
            "tier": my_tier, "vcores": int(my_vcores),
            "storage_gb": int(my_storage), "ha_enabled": bool(my_ha),
        },
        "redis_cache": {"tier": redis_tier, "sku": redis_sku or "none"},
        "azure_files": {"tier": files_tier, "storage_gb": int(files_gb)},
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    lines: List[BomLine] = []

    if inputs.get("fabric_sku"):
        l = _fabric_line(client, region, inputs["fabric_sku"], app_name)
        if l:
            pbi_users = int(inputs.get("pbi_users", 0) or 0)
            rec_sku = inputs.get("fabric_recommended_sku")
            if pbi_users > 0 and rec_sku:
                l.assumption = (
                    f"Fabric {inputs['fabric_sku']} capacity × 730 h = ${l.monthly_cost:,.2f}/mo. "
                    f"Sizing hint: {pbi_users:,} Power BI users → recommended `{rec_sku}` "
                    f"(Microsoft Fabric sizing guide). Selected `{inputs['fabric_sku']}`."
                )
            lines.append(l)

    if inputs.get("cosmos_ru_per_second", 0) > 0:
        l = _cosmos_line(client, region, inputs["cosmos_ru_per_second"], app_name)
        if l: lines.append(l)

    if inputs.get("synapse_dwu", 0) > 0:
        l = _synapse_line(client, region, inputs["synapse_dwu"], app_name)
        if l: lines.append(l)

    sql = inputs.get("azure_sql_db", {})
    lines.extend(_azure_sql_db_lines(
        client, region,
        tier_key=sql.get("tier", "none"),
        vcores=sql.get("vcores", 0),
        storage_gb=sql.get("storage_gb", 0),
        zone_redundant=sql.get("zone_redundant", False),
        app_name=app_name,
        use_ahb_sql=bool(inputs.get("__use_ahb_sql__", False)),
        pricing_mode=pricing_mode,
    ))

    adls = inputs.get("adls_gen2", {})
    if adls.get("tier") and adls["tier"] != "none":
        l = _adls_gen2_line(
            client, region,
            storage_gb=adls.get("storage_gb", 0),
            tier_key=adls["tier"], redundancy=adls.get("redundancy", "LRS"),
            app_name=app_name,
        )
        if l: lines.append(l)

    adf = inputs.get("adf", {})
    lines.extend(_adf_lines(
        client, region,
        pipeline_runs=adf.get("pipeline_runs", 0),
        diu_hours=adf.get("diu_hours", 0.0),
        ssis_ir_hours=adf.get("ssis_ir_hours", 0.0),
        app_name=app_name,
    ))

    eh = inputs.get("event_hubs", {})
    if eh.get("tier") and eh["tier"] != "none":
        l = _event_hubs_line(client, region, eh["tier"], eh.get("units", 0), app_name)
        if l: lines.append(l)

    db = inputs.get("databricks", {})
    if db.get("tier") and db["tier"] != "none":
        l = _databricks_line(client, region, db["tier"], db.get("dbu_hours", 0.0), app_name)
        if l: lines.append(l)

    ss = inputs.get("synapse_serverless", {})
    if ss.get("tb_per_month", 0) > 0:
        l = _synapse_serverless_line(client, region, ss["tb_per_month"], app_name)
        if l: lines.append(l)

    pbi = inputs.get("power_bi", {})
    if pbi.get("sku") and pbi["sku"] != "none":
        l = _power_bi_capacity_line(client, region, pbi["sku"], app_name)
        if l: lines.append(l)

    adx = inputs.get("adx", {})
    if adx.get("sku") and adx["sku"] != "none":
        l = _adx_line(client, region, adx["sku"], adx.get("count", 0), app_name)
        if l: lines.append(l)

    cosmos_sl = inputs.get("cosmos_serverless", {})
    if cosmos_sl.get("request_units_month", 0) > 0 or cosmos_sl.get("storage_gb", 0) > 0:
        lines.extend(_cosmos_serverless_line(
            client, region,
            request_units_month=cosmos_sl.get("request_units_month", 0),
            storage_gb=cosmos_sl.get("storage_gb", 0.0),
            app_name=app_name,
        ))

    # Azure SQL Managed Instance (honors use_ahb_sql)
    mi = inputs.get("azure_sql_mi", {})
    lines.extend(_azure_sql_mi_lines(
        client, region,
        tier_key=mi.get("tier", "none"),
        vcores=mi.get("vcores", 0), storage_gb=mi.get("storage_gb", 0),
        app_name=app_name, use_ahb_sql=bool(inputs.get("__use_ahb_sql__", False)),
    ))

    # PostgreSQL Flexible
    pg = inputs.get("postgres_flexible", {})
    lines.extend(_flex_server_lines(
        client, region,
        service_name="Azure Database for PostgreSQL", service_display="Azure PostgreSQL",
        custom_prefix="Postgres",
        tier_key=pg.get("tier", "none"), vcores=pg.get("vcores", 0),
        storage_gb=pg.get("storage_gb", 0), ha_enabled=pg.get("ha_enabled", False),
        app_name=app_name,
    ))

    # MySQL Flexible
    my = inputs.get("mysql_flexible", {})
    lines.extend(_flex_server_lines(
        client, region,
        service_name="Azure Database for MySQL", service_display="Azure MySQL",
        custom_prefix="MySQL",
        tier_key=my.get("tier", "none"), vcores=my.get("vcores", 0),
        storage_gb=my.get("storage_gb", 0), ha_enabled=my.get("ha_enabled", False),
        app_name=app_name,
    ))

    # Redis Cache
    redis_cfg = inputs.get("redis_cache", {})
    if redis_cfg.get("tier") and redis_cfg["tier"] != "none" \
            and redis_cfg.get("sku") and redis_cfg["sku"] != "none":
        l = _redis_line(client, region, redis_cfg["tier"], redis_cfg["sku"], app_name)
        if l: lines.append(l)

    # Azure Files
    files_cfg = inputs.get("azure_files", {})
    if files_cfg.get("tier") and files_cfg["tier"] != "none" and files_cfg.get("storage_gb", 0) > 0:
        l = _azure_files_line(client, region, files_cfg["tier"], files_cfg["storage_gb"], app_name)
        if l: lines.append(l)

    return lines, []
