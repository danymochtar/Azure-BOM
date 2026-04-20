"""Build compute + storage BOM lines from parsed inventory."""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..models import BomLine, InventoryItem
from ..mapper import recommend_vm, recommend_disk
from ..mapper.sizer import os_is_windows
from ..pricing.retail import RetailPricesClient, HOURS_PER_MONTH


def aggregate_vm_count(items: List[InventoryItem]) -> int:
    return sum(1 for i in items if "off" not in (i.powerstate or "").lower())


def build_compute_bom(
    items: List[InventoryItem],
    client: RetailPricesClient,
    region: str,
    headroom: float = 1.3,
    disk_tier: str = "Premium SSD",
    os_override: str = "as-detected",
) -> Tuple[List[BomLine], List[dict]]:
    """Return (bom lines, mapping_rows) where mapping_rows is a per-VM record
    showing source specs -> target Azure SKUs for UI display.
    """
    lines: List[BomLine] = []
    mapping_rows: List[dict] = []

    # Group identical VM sizings to reduce API calls and make the BOM legible
    vm_groups: Dict[Tuple[str, bool], Dict] = {}
    disk_groups: Dict[str, Dict] = {}

    for item in items:
        sku = recommend_vm(item, headroom=headroom)
        win = os_is_windows(item.os) if os_override == "as-detected" else (os_override == "Windows")
        key = (sku.arm_name, win)
        vm_groups.setdefault(key, {"sku": sku, "count": 0, "names": []})
        vm_groups[key]["count"] += 1
        vm_groups[key]["names"].append(item.name)

        disk = recommend_disk(item.storage_gb, tier=disk_tier) if item.storage_gb > 0 else None
        if disk:
            disk_groups.setdefault(disk.sku, {"disk": disk, "count": 0})
            disk_groups[disk.sku]["count"] += 1

        mapping_rows.append({
            "VM": item.name,
            "Source vCPU": item.vcpu,
            "Source Memory (GB)": item.memory_gb,
            "Source Storage (GB)": item.storage_gb,
            "OS": item.os,
            "Target Azure VM": sku.display,
            "ARM SKU": sku.arm_name,
            "Target Disk": disk.sku if disk else "-",
            "Target Disk Size (GiB)": disk.size_gib if disk else 0,
            "Windows Licensing": "Yes" if win else "No",
        })

    # Price VMs
    for (arm_name, win), grp in vm_groups.items():
        sku = grp["sku"]
        price = client.vm_price(arm_name, region, os_is_windows=win)
        if not price:
            lines.append(BomLine(
                category="Compute",
                resource=f"Virtual Machine - {sku.display}",
                sku=arm_name,
                meter="(price not found)",
                region=region,
                quantity=grp["count"] * HOURS_PER_MONTH,
                unit="hours",
                unit_price=0.0,
                monthly_cost=0.0,
                source="retail-prices-miss",
            ))
            continue
        qty_hours = grp["count"] * HOURS_PER_MONTH
        lines.append(BomLine(
            category="Compute",
            resource=f"Virtual Machine - {sku.display} {'(Windows)' if win else '(Linux)'} x{grp['count']}",
            sku=arm_name,
            meter=price.meter_name,
            region=region,
            quantity=qty_hours,
            unit="hours",
            unit_price=price.retail_price,
            monthly_cost=round(price.retail_price * qty_hours, 2),
            currency=price.currency_code,
            source="retail-prices",
            product_id=price.product_id,
            sku_id=price.sku_id,
            meter_id=price.meter_id,
        ))

    # Price disks
    for sku_name, grp in disk_groups.items():
        disk = grp["disk"]
        price = client.disk_price(disk.meter_name, region)
        if not price:
            lines.append(BomLine(
                category="Storage",
                resource=f"Managed Disk - {disk.tier} {disk.sku} x{grp['count']}",
                sku=disk.sku,
                meter=disk.meter_name,
                region=region,
                quantity=grp["count"],
                unit="disk/month",
                unit_price=0.0,
                monthly_cost=0.0,
                source="retail-prices-miss",
            ))
            continue
        lines.append(BomLine(
            category="Storage",
            resource=f"Managed Disk - {disk.tier} {disk.sku} x{grp['count']}",
            sku=disk.sku,
            meter=price.meter_name,
            region=region,
            quantity=grp["count"],
            unit="disk/month",
            unit_price=price.retail_price,
            monthly_cost=round(price.retail_price * grp["count"], 2),
            currency=price.currency_code,
            source="retail-prices",
            product_id=price.product_id,
            sku_id=price.sku_id,
            meter_id=price.meter_id,
        ))

    return lines, mapping_rows
