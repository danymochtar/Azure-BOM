"""Business continuity / disaster recovery add-on.

Adds:
- Azure Site Recovery — per protected instance (VM)
- Backup storage (GRS) — ~40% of provisioned storage for snapshots/retention

Azure Backup per-VM protected instance charge is already accounted for by the
Recovery Services Vault line in the landing zone; this module focuses on the
DR replication side of BCDR.
"""
from __future__ import annotations

from typing import List, Optional

from ..models import BomLine, InventoryItem
from ..pricing.retail import RetailPricesClient, PriceRecord


from ..pricing.picker import pick_by_substring as _pick


def build_bcdr_bom(
    client: RetailPricesClient,
    region: str,
    items: List[InventoryItem],
    snapshot_ratio: float = 0.4,
    storage_redundancy: str = "GRS",
    secondary_region: Optional[str] = None,
) -> List[BomLine]:
    """Return BCDR lines: Site Recovery + replicated backup storage.

    `secondary_region`, when provided, is used as the replication target:
    the ASR per-instance fee is priced in the secondary region (where the
    replica runs) and the resource label calls out the DR pair.
    """
    lines: List[BomLine] = []
    asr_region = secondary_region or region
    pair_label = f"{region} → {secondary_region}" if secondary_region else region
    vm_count = len(items)
    total_storage_gb = round(sum(i.storage_gb for i in items), 2)
    backup_gb = round(total_storage_gb * snapshot_ratio, 2)

    # --- Azure Site Recovery (per protected instance, priced in replica region) ---
    asr_records = client.query(
        f"serviceName eq 'Azure Site Recovery' and armRegionName eq '{asr_region}' "
        f"and priceType eq 'Consumption'"
    )
    asr = _pick(asr_records, "replicated instance")
    resource_label = (
        f"Azure Site Recovery — {vm_count} protected instance(s) [{pair_label}]"
    )
    if asr and vm_count > 0:
        lines.append(
            BomLine(
                category="BCDR",
                resource=resource_label,
                sku=asr.sku_name or asr.product_name,
                meter=asr.meter_name,
                region=asr_region,
                quantity=vm_count,
                unit="instance/month",
                unit_price=asr.retail_price,
                monthly_cost=round(asr.retail_price * vm_count, 2),
                currency=asr.currency_code,
                source="retail-prices",
                product_id=asr.product_id,
                sku_id=asr.sku_id,
                meter_id=asr.meter_id,
            )
        )
    elif vm_count > 0:
        lines.append(
            BomLine(
                category="BCDR",
                resource=resource_label,
                sku="N/A",
                meter="(price not found)",
                region=asr_region,
                quantity=vm_count,
                unit="instance/month",
                unit_price=0.0,
                monthly_cost=0.0,
                source="retail-prices-miss",
            )
        )

    # --- Replicated backup storage (GRS / LRS) priced in primary region ---
    if backup_gb > 0:
        storage_records = client.query(
            f"serviceName eq 'Backup' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        )
        sub = "grs" if storage_redundancy.upper() == "GRS" else "lrs"
        chosen = _pick(storage_records, f"{sub} data stored")
        if chosen:
            lines.append(
                BomLine(
                    category="BCDR",
                    resource=f"Backup Storage ({storage_redundancy}, ~{backup_gb} GB) [{region}]",
                    sku=chosen.sku_name or chosen.product_name,
                    meter=chosen.meter_name,
                    region=region,
                    quantity=backup_gb,
                    unit="GB",
                    unit_price=chosen.retail_price,
                    monthly_cost=round(chosen.retail_price * backup_gb, 2),
                    currency=chosen.currency_code,
                    source="retail-prices",
                    product_id=chosen.product_id,
                    sku_id=chosen.sku_id,
                    meter_id=chosen.meter_id,
                )
            )
    return lines
