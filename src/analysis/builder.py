"""Build compute + storage BOM lines from parsed inventory."""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..models import BomLine, InventoryItem
from ..mapper import recommend_vm, recommend_disk
from ..mapper.sizer import os_is_windows
from ..pricing.retail import RetailPricesClient, HOURS_PER_MONTH


def aggregate_vm_count(items: List[InventoryItem]) -> int:
    return sum(1 for i in items if "off" not in (i.powerstate or "").lower())


_BILLING_TAG: dict = {
    "payg": "",
    "sp_1y": " [SP 1Y]",
    "sp_3y": " [SP 3Y]",
    "ri_1y": " [RI 1Y]",
    "ri_3y": " [RI 3Y]",
}

# Map what the PriceRecord.price_type says back to a tag. When vm_price
# falls back to PAYG (because the SKU lacks an SP/RI record in the region),
# the returned record's price_type is "Consumption" — we must NOT tag it as
# SP/RI in that case, or users will see a discount label with no discount.
_PRICE_TYPE_TAG: dict = {
    "Consumption":  "",
    "SavingsPlan":  None,   # use the user's chosen pricing_mode tag
    "Reservation":  None,   # same
}


def build_compute_bom(
    items: List[InventoryItem],
    client: RetailPricesClient,
    region: str,
    headroom: float = 1.0,
    disk_tier: str = "Premium SSD",
    os_override: str = "as-detected",
    app_name: str = "",
    pricing_mode: str = "payg",
    use_ahb: bool = False,
    compute_mode: str = "normal",
) -> Tuple[List[BomLine], List[dict]]:
    """Return (bom lines, mapping_rows) where mapping_rows is a per-VM record
    showing source specs -> target Azure SKUs for UI display.

    `compute_mode` (one of `saving` / `normal` / `high_perf`) flows
    through to the sizer's family preferences — see `mapper.sizer.recommend_vm`
    docstring + `constants.COMPUTE_MODES` for the full mapping.
    """
    lines: List[BomLine] = []
    mapping_rows: List[dict] = []

    # Group identical VM sizings to reduce API calls and make the BOM legible
    vm_groups: Dict[Tuple[str, bool], Dict] = {}
    disk_groups: Dict[str, Dict] = {}

    for item in items:
        sku = recommend_vm(item, headroom=headroom, compute_mode=compute_mode)
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
        # Custom name = concatenated VM names (short) or "<app>-<sku>" fallback
        names = grp["names"]
        if len(names) == 1:
            vm_custom = names[0]
        elif len(names) <= 3:
            vm_custom = ", ".join(names)
        else:
            vm_custom = f"{names[0]}+{len(names)-1}-more"
        if app_name:
            vm_custom = f"{app_name}-{vm_custom}"
        price = client.vm_price(
            arm_name, region, os_is_windows=win,
            pricing_mode=pricing_mode, use_ahb=use_ahb,
        )
        # License tag — included in every compute row so the BOM makes the
        # licensing basis explicit (Azure cost standard). CRITICAL: read the
        # actual PriceRecord.price_type, not the user's requested
        # `pricing_mode`, so when vm_price fell back to PAYG (no SP/RI in
        # this region for this SKU) the tag reflects reality.
        actual_price_type = getattr(price, "price_type", "") if price else ""
        if actual_price_type in ("SavingsPlan", "Reservation"):
            term_tag = _BILLING_TAG.get(pricing_mode, "")
        else:
            term_tag = ""   # fell back to PAYG; no SP/RI discount applied
        ahb_tag = " [AHB]" if (use_ahb and win) else ""
        license_tag = f"{term_tag}{ahb_tag}"
        os_label = "Windows" + (" / AHB BYOL" if use_ahb and win else "") if win else "Linux"
        if not price:
            lines.append(BomLine(
                category="Compute",
                resource=f"Virtual Machine - {sku.display} ({os_label}){license_tag}",
                sku=arm_name,
                meter="(price not found)",
                region=region,
                quantity=grp["count"] * HOURS_PER_MONTH,
                unit="hours",
                unit_price=0.0,
                monthly_cost=0.0,
                source="retail-prices-miss",
                service_name="Virtual Machines",
                custom_name=vm_custom,
                resource_count=int(grp["count"]),
            ))
            continue
        qty_hours = grp["count"] * HOURS_PER_MONTH
        lines.append(BomLine(
            category="Compute",
            resource=f"Virtual Machine - {sku.display} ({os_label}){license_tag}",
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
            service_name="Virtual Machines",
            custom_name=vm_custom,
            resource_count=int(grp["count"]),
        ))

    # Price disks
    for sku_name, grp in disk_groups.items():
        disk = grp["disk"]
        disk_custom = f"{app_name}-Data-{disk.sku}" if app_name else f"Data-{disk.sku}"
        price = client.disk_price(disk.meter_name, region)
        if not price:
            lines.append(BomLine(
                category="Storage",
                resource=f"Managed Disk - {disk.tier} {disk.sku}",
                sku=disk.sku,
                meter=disk.meter_name,
                region=region,
                quantity=grp["count"],
                unit="disk/month",
                unit_price=0.0,
                monthly_cost=0.0,
                source="retail-prices-miss",
                service_name="Managed Disks",
                custom_name=disk_custom,
                resource_count=int(grp["count"]),
            ))
            continue
        lines.append(BomLine(
            category="Storage",
            resource=f"Managed Disk - {disk.tier} {disk.sku}",
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
            service_name="Managed Disks",
            custom_name=disk_custom,
            resource_count=int(grp["count"]),
        ))

    return lines, mapping_rows
