"""Microsoft Defender for Cloud plan pricing.

Plans are priced per-resource per-month. We compute counts from the inventory
where relevant (Servers Plan 2 = VM count). Other plans default to 0 unless
the caller overrides with a count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..models import BomLine
from ..pricing.retail import RetailPricesClient, PriceRecord


@dataclass
class DefenderPlan:
    key: str
    resource: str
    default_enabled: bool
    unit: str                         # "Server/month", "Vault/month", ...
    build_filter: Callable[[str], str]
    pick_substring: str               # meter-name substring to disambiguate
    count_source: str                 # "vms" | "manual"
    notes: str = ""


def _filter(service: str, region: str) -> str:
    return (
        f"serviceName eq '{service}' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )


DEFENDER_PLANS: List[DefenderPlan] = [
    DefenderPlan(
        key="servers_p2",
        resource="Defender for Servers Plan 2",
        default_enabled=True,
        unit="Server/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="Servers",
        count_source="vms",
        notes="Per protected server (Linux/Windows). P2 includes EDR integration, FIM, JIT.",
    ),
    DefenderPlan(
        key="sql_on_vms",
        resource="Defender for SQL servers on machines",
        default_enabled=False,
        unit="vCore/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="SQL on Machines",
        count_source="manual",
        notes="Per SQL VM vCore. Count manually if you have SQL on IaaS.",
    ),
    DefenderPlan(
        key="storage",
        resource="Defender for Storage",
        default_enabled=False,
        unit="Storage account/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="Storage",
        count_source="manual",
        notes="Per storage account + GB malware scanning.",
    ),
    DefenderPlan(
        key="appsvc",
        resource="Defender for App Service",
        default_enabled=False,
        unit="vCore/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="App Service",
        count_source="manual",
        notes="Per App Service plan vCore.",
    ),
    DefenderPlan(
        key="keyvault",
        resource="Defender for Key Vault",
        default_enabled=False,
        unit="10K transactions",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="Key Vault",
        count_source="manual",
        notes="Per 10K vault transactions.",
    ),
    DefenderPlan(
        key="containers",
        resource="Defender for Containers",
        default_enabled=False,
        unit="vCore/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="Containers",
        count_source="manual",
        notes="Per AKS node vCore.",
    ),
    DefenderPlan(
        key="cosmos",
        resource="Defender for Cosmos DB",
        default_enabled=False,
        unit="100 RU/s-month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="Cosmos DB",
        count_source="manual",
        notes="Per 100 RU/s provisioned throughput, normalised. Counts manual RU/s blocks.",
    ),
    DefenderPlan(
        key="oss_db",
        resource="Defender for Open-Source Relational Databases",
        default_enabled=False,
        unit="vCore/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="Relational Databases",
        count_source="manual",
        notes="Per server vCore — covers PostgreSQL/MySQL/MariaDB Flexible Servers.",
    ),
    DefenderPlan(
        key="ai",
        resource="Defender for AI workloads",
        default_enabled=False,
        unit="OpenAI resource/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="AI",
        count_source="manual",
        notes="Per Azure OpenAI / Foundry deployment. New (2025) — prompt injection + DLP.",
    ),
    DefenderPlan(
        key="apis",
        resource="Defender for APIs",
        default_enabled=False,
        unit="API/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="APIs",
        count_source="manual",
        notes="Per API protected — for APIs exposed through API Management.",
    ),
    DefenderPlan(
        key="rm",
        resource="Defender for Resource Manager",
        default_enabled=False,
        unit="subscription/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="Resource Manager",
        count_source="manual",
        notes="Per subscription. Detects suspicious ARM-level operations.",
    ),
    DefenderPlan(
        key="dns",
        resource="Defender for DNS",
        default_enabled=False,
        unit="subscription/month",
        build_filter=lambda r: _filter("Microsoft Defender for Cloud", r),
        pick_substring="DNS",
        count_source="manual",
        notes="Per subscription. Detects data exfil + C2 via DNS tunnelling.",
    ),
]


from ..pricing.picker import pick_by_substring as _pick


def build_defender_bom(
    client: RetailPricesClient,
    region: str,
    enabled_keys: List[str],
    vm_count: int,
    manual_counts: Optional[Dict[str, float]] = None,
) -> List[BomLine]:
    manual_counts = manual_counts or {}
    lines: List[BomLine] = []
    for plan in DEFENDER_PLANS:
        if plan.key not in enabled_keys:
            continue
        qty = float(vm_count if plan.count_source == "vms" else manual_counts.get(plan.key, 0))
        if qty <= 0:
            continue
        records = client.query(plan.build_filter(region), max_pages=5)
        chosen = _pick(records, plan.pick_substring)
        if not chosen:
            lines.append(
                BomLine(
                    category="Security",
                    resource=plan.resource,
                    sku="N/A",
                    meter="(price not found)",
                    region=region,
                    quantity=qty,
                    unit=plan.unit,
                    unit_price=0.0,
                    monthly_cost=0.0,
                    source="retail-prices-miss",
                )
            )
            continue
        lines.append(
            BomLine(
                category="Security",
                resource=plan.resource,
                sku=chosen.sku_name or chosen.product_name,
                meter=chosen.meter_name,
                region=region,
                quantity=qty,
                unit=plan.unit,
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * qty, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id,
                sku_id=chosen.sku_id,
                meter_id=chosen.meter_id,
            )
        )
    return lines
