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

AZURE_SQL_DB_TIERS: Dict[str, str] = {
    "gp":   "General Purpose (serverless / provisioned vCore)",
    "bc":   "Business Critical (zone-redundant, Always On replicas)",
    "hs":   "Hyperscale (scale-out storage)",
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


def _azure_sql_db_lines(
    client: RetailPricesClient, region: str,
    tier_key: str, vcores: int, storage_gb: int, zone_redundant: bool, app_name: str,
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
        out.append(BomLine(
            category="Data + Analytics",
            resource=f"Azure SQL Database {cfg_label} × {vcores} vCores" + (" [ZR]" if zone_redundant else ""),
            sku=compute.sku_name or compute.product_name, meter=compute.meter_name,
            region=region, quantity=qty, unit="vCore-hours",
            unit_price=compute.retail_price,
            monthly_cost=round(compute.retail_price * qty, 2),
            currency=compute.currency_code,
            source="retail-prices",
            product_id=compute.product_id, sku_id=compute.sku_id, meter_id=compute.meter_id,
            service_name="Azure SQL Database",
            custom_name=f"{app_name}-SQLDB-{tier_key}" if app_name else f"SQLDB-{tier_key}",
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
    chosen = None
    for r in recs:
        meter_l = (r.meter_name + " " + r.product_name).lower()
        if all(h in meter_l for h in hint_parts) and "hierarchical" in meter_l:
            if chosen is None or r.retail_price < chosen.retail_price:
                chosen = r
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

    with st.expander("Microsoft Fabric / Cosmos / Synapse", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            _fabric_opts = ["(none)"] + FABRIC_CAPACITIES
            _saved_f = prefs.get("fabric_sku") or "(none)"
            fabric_sku = st.selectbox("Fabric capacity", _fabric_opts,
                                       index=_sel_idx(_fabric_opts, _saved_f))
            fabric_sku = None if fabric_sku == "(none)" else fabric_sku
        with c2:
            cosmos_ru = st.number_input("Cosmos DB RU/s", min_value=0,
                                          value=int(prefs.get("cosmos_ru_per_second", 0)), step=400)
        with c3:
            synapse_dwu = st.number_input("Synapse Dedicated SQL Pool DWU", min_value=0,
                                            value=int(prefs.get("synapse_dwu", 0)), step=100)

    with st.expander("Azure SQL Database", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _sql_opts = ["none"] + list(AZURE_SQL_DB_TIERS.keys())
            sql_tier = st.selectbox(
                "Service tier", _sql_opts,
                index=_sel_idx(_sql_opts, sql_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else AZURE_SQL_DB_TIERS[k].split(" (")[0],
            )
        with c2:
            sql_vcores = st.number_input("vCores", min_value=0, value=int(sql_pref.get("vcores", 0)),
                                          step=2, disabled=(sql_tier == "none"))
        with c3:
            sql_storage = st.number_input("Storage GB", min_value=0, value=int(sql_pref.get("storage_gb", 0)),
                                            step=50, disabled=(sql_tier == "none"))
        with c4:
            sql_zr = st.checkbox("Zone-redundant", value=bool(sql_pref.get("zone_redundant", False)),
                                  disabled=(sql_tier == "none"))

    with st.expander("ADLS Gen2 (Data Lake Storage)", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            _adls_opts = ["none"] + list(ADLS_TIERS.keys())
            adls_tier = st.selectbox(
                "Tier", _adls_opts,
                index=_sel_idx(_adls_opts, adls_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else ADLS_TIERS[k],
            )
        with c2:
            _red_opts = list(ADLS_REDUNDANCY.keys())
            adls_redundancy = st.selectbox(
                "Redundancy", _red_opts,
                index=_sel_idx(_red_opts, adls_pref.get("redundancy", "LRS")),
                format_func=lambda k: ADLS_REDUNDANCY[k],
                disabled=(adls_tier == "none"),
            )
        with c3:
            adls_gb = st.number_input("Storage GB", min_value=0, value=int(adls_pref.get("storage_gb", 0)),
                                        step=1000, disabled=(adls_tier == "none"))

    with st.expander("Azure Data Factory", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            adf_runs = st.number_input("Activity runs / month", min_value=0,
                                         value=int(adf_pref.get("pipeline_runs", 0)), step=1000)
        with c2:
            adf_diu = st.number_input("Data movement DIU-hours / month", min_value=0.0,
                                        value=float(adf_pref.get("diu_hours", 0.0)), step=5.0)
        with c3:
            adf_ssis = st.number_input("SSIS IR hours / month", min_value=0.0,
                                         value=float(adf_pref.get("ssis_ir_hours", 0.0)), step=10.0)

    with st.expander("Event Hubs", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _eh_opts = ["none"] + list(EVENT_HUBS_TIERS.keys())
            eh_tier = st.selectbox(
                "Tier", _eh_opts,
                index=_sel_idx(_eh_opts, eh_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else EVENT_HUBS_TIERS[k],
            )
        with c2:
            eh_units = st.number_input("Units", min_value=0, value=int(eh_pref.get("units", 0)),
                                        step=1, disabled=(eh_tier == "none"))

    with st.expander("Azure Databricks", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _db_opts = ["none"] + list(DATABRICKS_TIERS.keys())
            db_tier = st.selectbox(
                "Workspace tier", _db_opts,
                index=_sel_idx(_db_opts, db_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else DATABRICKS_TIERS[k]["label"],
            )
        with c2:
            db_dbu = st.number_input(
                "DBU-hours / month", min_value=0.0,
                value=float(db_pref.get("dbu_hours", 0.0)), step=100.0,
                disabled=(db_tier == "none"),
                help="1 DBU ≈ 1 hour of a single-core worker on Standard. A small cluster of 4 nodes × 8hrs/day × 22 days ≈ 700 DBU.",
            )

    with st.expander("Synapse Serverless SQL (pay-per-TB)", expanded=False):
        synapse_serverless_tb = st.number_input(
            "TB scanned / month", min_value=0.0,
            value=float(ss_pref.get("tb_per_month", 0.0)), step=0.5,
            help="Serverless SQL pool charges ~$5/TB scanned. Great for ad-hoc on ADLS.",
        )

    with st.expander("Microsoft Fabric / Power BI Embedded capacity", expanded=False):
        _pbi_opts = ["none"] + list(POWER_BI_CAPACITIES.keys())
        pbi_sku = st.selectbox(
            "Capacity SKU", _pbi_opts,
            index=_sel_idx(_pbi_opts, pbi_pref.get("sku", "none")),
            format_func=lambda k: "None" if k == "none" else POWER_BI_CAPACITIES[k],
            help="F-SKUs route through Microsoft Fabric; P and A SKUs route through Power BI Embedded.",
        )

    with st.expander("Azure Data Explorer (Kusto)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _adx_opts = ["none"] + list(ADX_SKUS.keys())
            adx_sku = st.selectbox(
                "Cluster SKU", _adx_opts,
                index=_sel_idx(_adx_opts, adx_pref.get("sku", "none")),
                format_func=lambda k: "None" if k == "none" else ADX_SKUS[k],
            )
        with c2:
            adx_count = st.number_input("Instance count", min_value=0,
                                         value=int(adx_pref.get("count", 0)), step=1,
                                         disabled=(adx_sku == "none"))

    with st.expander("Cosmos DB Serverless (per-RU billing)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            cosmos_sl_ru = st.number_input(
                "Request Units / month", min_value=0,
                value=int(cosmos_sl_pref.get("request_units_month", 0)),
                step=1_000_000,
                help="Serverless: ~$0.25 per million RU consumed. Use provisioned above for sustained throughput.",
            )
        with c2:
            cosmos_sl_storage = st.number_input(
                "Cosmos storage GB", min_value=0.0,
                value=float(cosmos_sl_pref.get("storage_gb", 0.0)), step=10.0,
            )

    return {
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
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    lines: List[BomLine] = []

    if inputs.get("fabric_sku"):
        l = _fabric_line(client, region, inputs["fabric_sku"], app_name)
        if l: lines.append(l)

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

    return lines, []
