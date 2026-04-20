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

def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    with st.expander("Microsoft Fabric / Cosmos / Synapse", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            fabric_sku = st.selectbox("Fabric capacity", ["(none)"] + FABRIC_CAPACITIES, index=0)
            fabric_sku = None if fabric_sku == "(none)" else fabric_sku
        with c2:
            cosmos_ru = st.number_input("Cosmos DB RU/s", min_value=0, value=0, step=400)
        with c3:
            synapse_dwu = st.number_input("Synapse Dedicated SQL Pool DWU", min_value=0, value=0, step=100)

    with st.expander("Azure SQL Database", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            sql_tier = st.selectbox(
                "Service tier",
                ["none"] + list(AZURE_SQL_DB_TIERS.keys()),
                format_func=lambda k: "None" if k == "none" else AZURE_SQL_DB_TIERS[k].split(" (")[0],
            )
        with c2:
            sql_vcores = st.number_input("vCores", min_value=0, value=0, step=2, disabled=(sql_tier == "none"))
        with c3:
            sql_storage = st.number_input("Storage GB", min_value=0, value=0, step=50, disabled=(sql_tier == "none"))
        with c4:
            sql_zr = st.checkbox("Zone-redundant", value=False, disabled=(sql_tier == "none"))

    with st.expander("ADLS Gen2 (Data Lake Storage)", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            adls_tier = st.selectbox(
                "Tier",
                ["none"] + list(ADLS_TIERS.keys()),
                format_func=lambda k: "None" if k == "none" else ADLS_TIERS[k],
            )
        with c2:
            adls_redundancy = st.selectbox(
                "Redundancy",
                list(ADLS_REDUNDANCY.keys()),
                format_func=lambda k: ADLS_REDUNDANCY[k],
                disabled=(adls_tier == "none"),
            )
        with c3:
            adls_gb = st.number_input("Storage GB", min_value=0, value=0, step=1000, disabled=(adls_tier == "none"))

    with st.expander("Azure Data Factory", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            adf_runs = st.number_input("Activity runs / month", min_value=0, value=0, step=1000)
        with c2:
            adf_diu = st.number_input("Data movement DIU-hours / month", min_value=0.0, value=0.0, step=5.0)
        with c3:
            adf_ssis = st.number_input("SSIS IR hours / month", min_value=0.0, value=0.0, step=10.0)

    with st.expander("Event Hubs", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            eh_tier = st.selectbox(
                "Tier",
                ["none"] + list(EVENT_HUBS_TIERS.keys()),
                format_func=lambda k: "None" if k == "none" else EVENT_HUBS_TIERS[k],
            )
        with c2:
            eh_units = st.number_input("Units", min_value=0, value=0, step=1, disabled=(eh_tier == "none"))

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

    return lines, []
