"""Data Platform cost model — Microsoft Fabric, Synapse, Cosmos DB.

Fabric capacity SKUs are fixed-rate (pay-by-the-hour for a reserved
capacity unit). Cosmos DB is RU/s × hours. Synapse Dedicated Pool is
DWU × hours.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..architecture.security_tier import _pick
from ..models import BomLine
from ..pricing.retail import RetailPricesClient, HOURS_PER_MONTH


# Fabric capacity SKUs, ordered smallest to largest.
FABRIC_CAPACITIES: List[str] = [
    "F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256", "F512", "F1024", "F2048",
]


def _fabric_line(
    client: RetailPricesClient, region: str, sku: str, app_name: str
) -> Optional[BomLine]:
    recs = client.query(
        f"serviceName eq 'Microsoft Fabric' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, sku.lower())
    if not chosen:
        return None
    qty = HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Microsoft Fabric capacity {sku}",
        sku=sku,
        meter=chosen.meter_name,
        region=region,
        quantity=qty,
        unit="hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
        service_name="Microsoft Fabric",
        custom_name=f"{app_name}-Fabric-{sku}" if app_name else f"Fabric-{sku}",
    )


def _cosmos_line(
    client: RetailPricesClient, region: str, ru_per_second: int, app_name: str
) -> Optional[BomLine]:
    if ru_per_second <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Azure Cosmos DB' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "provisioned throughput")
    if not chosen:
        return None
    # Cosmos meters price per 100 RU/s per hour.
    units_of_100 = ru_per_second / 100.0
    qty = units_of_100 * HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Cosmos DB provisioned throughput ({ru_per_second:,} RU/s)",
        sku=f"{ru_per_second}RU",
        meter=chosen.meter_name,
        region=region,
        quantity=round(qty, 2),
        unit="100 RU/s-hour",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
        service_name="Azure Cosmos DB",
        custom_name=f"{app_name}-CosmosDB" if app_name else "CosmosDB",
    )


def _synapse_line(
    client: RetailPricesClient, region: str, dwu: int, app_name: str
) -> Optional[BomLine]:
    if dwu <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'SQL Data Warehouse' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, f"dw{dwu}c")
    if not chosen:
        chosen = _pick(recs, "compute")
    if not chosen:
        return None
    qty = HOURS_PER_MONTH
    return BomLine(
        category="Data + Analytics",
        resource=f"Synapse Dedicated SQL Pool (DW{dwu}c)",
        sku=f"DW{dwu}c",
        meter=chosen.meter_name,
        region=region,
        quantity=qty,
        unit="hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id,
        sku_id=chosen.sku_id,
        meter_id=chosen.meter_id,
        service_name="Azure Synapse Analytics",
        custom_name=f"{app_name}-Synapse-DW{dwu}" if app_name else f"Synapse-DW{dwu}",
    )


def build_data_platform_bom(
    client: RetailPricesClient,
    region: str,
    fabric_sku: Optional[str] = None,
    cosmos_ru_per_second: int = 0,
    synapse_dwu: int = 0,
    app_name: str = "",
) -> List[BomLine]:
    lines: List[BomLine] = []
    if fabric_sku:
        l = _fabric_line(client, region, fabric_sku, app_name)
        if l:
            lines.append(l)
    if cosmos_ru_per_second > 0:
        l = _cosmos_line(client, region, cosmos_ru_per_second, app_name)
        if l:
            lines.append(l)
    if synapse_dwu > 0:
        l = _synapse_line(client, region, synapse_dwu, app_name)
        if l:
            lines.append(l)
    return lines
