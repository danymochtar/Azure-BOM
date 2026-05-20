"""Build compute + storage BOM lines from parsed inventory."""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..models import BomLine, InventoryItem
from ..mapper import (
    recommend_vm, recommend_disk, recommend_disk_tier,
    is_non_prod, is_sql_server, env_tag, role_tag,
)
from ..mapper.sizer import os_is_windows
from ..mapper.vm_catalog import VM_CATALOG, VmSku
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

# Friendly label written into BomLine.billing_term (its own column in
# the Results table). When the retail feed falls back to PAYG for an
# SKU that lacked SP/RI in the region, we stamp "PAYG (fallback)" so
# reviewers can spot the silent downgrade.
_BILLING_LABEL: dict = {
    "payg":  "PAYG",
    "sp_1y": "SP 1Y",
    "sp_3y": "SP 3Y",
    "ri_1y": "RI 1Y",
    "ri_3y": "RI 3Y",
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


def _general_purpose_alternative(burstable: VmSku) -> VmSku | None:
    """Find the smallest D-series (general-purpose) SKU that matches or
    exceeds the Burstable pick's vCPU + memory. Used by the cost-compare
    safety net so we never pay more for B-series than we would for D
    in the rare region where the published rates invert (typically
    happens in Phase-2 / newly-announced regions where the B-series
    meter hasn't been priced down yet)."""
    candidates = [
        s for s in VM_CATALOG
        if s.family == "general"
        and s.vcpu >= burstable.vcpu
        and s.memory_gb >= burstable.memory_gb
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (s.vcpu, s.memory_gb))
    return candidates[0]


def _cheaper_of(
    client: RetailPricesClient,
    primary: VmSku,
    region: str,
    win: bool,
    pricing_mode: str,
    use_ahb: bool,
):
    """Return (chosen_sku, chosen_price, swapped). If the primary pick is
    Burstable AND its equivalent D-series is cheaper in this region for
    the same OS + billing term, swap to D. Both prices are still fetched
    so the caller sees the actual numbers."""
    primary_price = client.vm_price(
        primary.arm_name, region, os_is_windows=win,
        pricing_mode=pricing_mode, use_ahb=use_ahb,
    )
    if primary.family != "burstable":
        return primary, primary_price, False

    alt = _general_purpose_alternative(primary)
    if alt is None:
        return primary, primary_price, False
    alt_price = client.vm_price(
        alt.arm_name, region, os_is_windows=win,
        pricing_mode=pricing_mode, use_ahb=use_ahb,
    )
    # Swap only when we have valid prices on both sides AND D is strictly
    # cheaper. Equal rates → stay on Burstable (user explicitly chose the
    # saving mode for a reason).
    if (
        primary_price and alt_price
        and alt_price.retail_price > 0
        and primary_price.retail_price > 0
        and alt_price.retail_price < primary_price.retail_price
    ):
        return alt, alt_price, True
    return primary, primary_price, False


def build_compute_bom(
    items: List[InventoryItem],
    client: RetailPricesClient,
    region: str,
    headroom: float = 1.0,
    disk_tier: str = "Standard SSD",
    os_override: str = "as-detected",
    app_name: str = "",
    pricing_mode: str = "payg",
    use_ahb: bool = False,
    compute_mode: str = "normal",
    auto_disk_tier: bool = True,
    non_prod_payg: bool = True,
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
        # Per-VM billing term: non-prod VMs default to PAYG even when
        # global pricing_mode is RI/SP (UAT / dev / staging usually
        # doesn't run 24×7 — committing to RI is wasteful). Triggered
        # by `non_prod_payg=True` + `is_non_prod(item)`.
        item_billing = (
            "payg"
            if (non_prod_payg and pricing_mode != "payg" and is_non_prod(item))
            else pricing_mode
        )
        # Group by (SKU, OS, billing, environment, role) so functionally
        # distinct VMs don't silently merge. Two D4s v5 Linux VMs named
        # "Server 1 PROD App" and "Server 3 UAT App" used to collapse
        # into one "D4s v5 (Linux) ×2" line — confusing for review.
        # Now they emit as two lines: "(prod, app) ×1" and "(uat, app)
        # ×1", and a "DB (PROD)" Windows VM stays separate from app
        # servers at the same SKU.
        item_env = env_tag(item)
        item_role = role_tag(item)
        key = (sku.arm_name, win, item_billing, item_env, item_role)
        vm_groups.setdefault(key, {
            "sku": sku, "count": 0, "names": [],
            "billing": item_billing, "env": item_env, "role": item_role,
            "vcpu_total": 0, "is_sql": False,
        })
        vm_groups[key]["count"] += 1
        vm_groups[key]["names"].append(item.name)
        vm_groups[key]["vcpu_total"] += sku.vcpu
        if is_sql_server(item):
            vm_groups[key]["is_sql"] = True

        # Per-VM disk-tier auto-recommendation. DB / SQL / Oracle /
        # Mongo / etc. → Premium SSD; backup / archive / file / log →
        # Standard HDD; everything else inherits the global default
        # (`disk_tier`). When `auto_disk_tier=False` every disk uses
        # the global default — matches the legacy "single tier for
        # everything" behaviour.
        item_tier = (
            recommend_disk_tier(item, default=disk_tier)
            if auto_disk_tier else disk_tier
        )
        disk = recommend_disk(item.storage_gb, tier=item_tier) if item.storage_gb > 0 else None
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
    for (arm_name, win, group_billing, group_env, group_role), grp in vm_groups.items():
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
        # Cost-compare safety net: if the sizer picked Burstable but the
        # equivalent D-series is actually cheaper in this region for this
        # OS + billing term, swap to D. `swapped=True` stamps an
        # assumption note so reviewers see the substitution rationale.
        sku, price, swapped = _cheaper_of(
            client, sku, region, win, group_billing, use_ahb,
        )
        arm_name = sku.arm_name
        # License tag — included in every compute row so the BOM makes the
        # licensing basis explicit (Azure cost standard). CRITICAL: read the
        # actual PriceRecord.price_type, not the GROUP's requested
        # `group_billing`, so when vm_price fell back to PAYG (no SP/RI in
        # this region for this SKU) the tag reflects reality.
        actual_price_type = getattr(price, "price_type", "") if price else ""
        if actual_price_type in ("SavingsPlan", "Reservation"):
            term_tag = _BILLING_TAG.get(group_billing, "")
            billing_label = _BILLING_LABEL.get(group_billing, "PAYG")
        else:
            term_tag = ""   # fell back to PAYG; no SP/RI discount applied
            # If the user asked for SP/RI but the SKU has no such meter
            # in this region, mark the line "PAYG (fallback)" so the
            # billing column makes the silent downgrade visible.
            billing_label = (
                "PAYG"
                if group_billing == "payg"
                else f"PAYG (fallback from {_BILLING_LABEL.get(group_billing, group_billing)})"
            )
        ahb_tag = " [AHB]" if (use_ahb and win) else ""
        license_tag = f"{term_tag}{ahb_tag}"
        os_label = "Windows" + (" / AHB BYOL" if use_ahb and win else "") if win else "Linux"
        if not price:
            lines.append(BomLine(
                category="Compute",
                resource=f"Virtual Machine - {sku.display} ({os_label}, {group_env}/{group_role}){license_tag}",
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
                billing_term=billing_label,
            ))
            continue
        qty_hours = grp["count"] * HOURS_PER_MONTH
        swap_note = (
            f"Cost-compare: Burstable was more expensive than D-series at "
            f"this size in `{region}`; swapped to {sku.display} for savings."
            if swapped else ""
        )
        lines.append(BomLine(
            category="Compute",
            resource=f"Virtual Machine - {sku.display} ({os_label}, {group_env}/{group_role}){license_tag}",
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
            assumption=swap_note,
            billing_term=billing_label,
        ))

        # SQL Server VM license line — only when VM is detected as
        # SQL Server AND `use_ahb_sql` is OFF (AHB-SQL means customer
        # is bringing their own SQL Server license via Software
        # Assurance). Rate captured Oct-2025 from Microsoft Learn
        # "SQL Server on Azure VMs" pricing page; Standard edition
        # baseline. Reviewers can upgrade to Enterprise via the
        # Cost Assumptions sheet if needed.
        if grp.get("is_sql") and not use_ahb_sql:
            sql_license_per_core_hr = 0.4080   # SQL Server Standard PAYG
            sql_cores = grp["vcpu_total"]
            sql_hours = HOURS_PER_MONTH
            sql_monthly = sql_license_per_core_hr * sql_cores * sql_hours
            lines.append(BomLine(
                category="Compute",
                resource=f"SQL Server VM license (Standard) — {grp['count']}× {sku.display} ({group_env}/{group_role})",
                sku="SQL_Server_Std_PAYG",
                meter="SQL Server Standard — PAYG per core-hour",
                region=region,
                quantity=sql_cores * sql_hours,
                unit="core-hours",
                unit_price=sql_license_per_core_hr,
                monthly_cost=round(sql_monthly, 2),
                currency=price.currency_code if price else "USD",
                source="static-reference",
                service_name="SQL Server on Azure VMs",
                custom_name=f"{vm_custom}-SQLSvr-Std",
                resource_count=int(grp["count"]),
                billing_term="PAYG (SQL license)",
                assumption=(
                    f"SQL Server VM license auto-added: detected `sql`/`mssql` "
                    f"in {grp['count']} VM name(s). Standard edition PAYG "
                    f"(~$0.408/core-hr × {sql_cores} cores). Toggle "
                    f"`SQL Server AHB` ON in Assessment context to BYOL "
                    f"(zero this line) if customer has SA. Upgrade to "
                    f"Enterprise (~$1.539/core-hr) for mission-critical."
                ),
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
