"""Infrastructure Lift-and-Shift pillar.

Wraps the existing VM compute + Managed Disks + Landing Zone + HA + BCDR
builders. No new services — this pillar is a deliberate thin wrapper so the
proven lift-and-shift flow stays intact.

Exports `vm_count` + `la_gb` via `inputs["__exports__"]` so the Azure Security
pillar can pick them up when both pillars are active.
"""
from __future__ import annotations

import traceback
from typing import Dict, List, Optional

from ..analysis import aggregate_vm_count, build_compute_bom
from ..architecture import (
    MIGRATION_STRATEGIES,
    apply_ha_multiplier,
    build_bcdr_bom,
    build_ha_bom,
    strategy_guidance,
)
from ..constants import AZURE_REGIONS
from ..landing_zone import LANDING_ZONE_COMPONENTS, build_landing_zone_bom
from ..models import BomLine
from ..parsers import ai_parse_inventory, parse_inventory
from ..parsers.content import kind_from_name


PILLAR_METADATA = {
    "key": "infra_lift_shift",
    "label": "Infra Lift-and-Shift",
    "description": "One-to-one VM migration, landing zone, HA, BCDR, security on VMs.",
    "icon": "🖥",
    "needs_vm_extraction": True,
    "default_strategy": "iaas",
}


def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    """Render all VM-flow widgets and run the extraction. Returns a dict that
    build_bom() consumes. Also exposes `__exports__` so the security pillar
    can inherit vm_count + la_gb when both are active.
    """
    # ----- Strategy / sizing -----
    strategy_keys = list(MIGRATION_STRATEGIES.keys())
    saved_strategy = prefs.get("strategy_key", "iaas")
    strategy_idx = strategy_keys.index(saved_strategy) if saved_strategy in strategy_keys else 0

    c1, c2 = st.columns(2)
    with c1:
        strategy_key = st.radio(
            "Migration strategy",
            strategy_keys,
            format_func=lambda k: MIGRATION_STRATEGIES[k]["label"],
            index=strategy_idx,
        )
        headroom = st.slider("Sizing headroom", 1.0, 2.0, float(prefs.get("headroom", 1.3)), step=0.05)
        disk_opts = ["Premium SSD", "Standard SSD", "Standard HDD"]
        saved_disk = prefs.get("disk_tier", "Premium SSD")
        disk_tier = st.selectbox(
            "Default disk tier", disk_opts,
            index=disk_opts.index(saved_disk) if saved_disk in disk_opts else 0,
        )
    with c2:
        os_opts = ["as-detected", "Linux", "Windows"]
        saved_os = prefs.get("os_mode", "as-detected")
        os_mode = st.radio(
            "OS handling", os_opts,
            index=os_opts.index(saved_os) if saved_os in os_opts else 0,
            horizontal=True,
        )
        include_off = st.checkbox("Include powered-off VMs", value=False)
        include_lz = st.checkbox("Landing Zone", value=bool(prefs.get("include_lz", True)))
        include_ha = st.checkbox("High Availability", value=bool(prefs.get("include_ha", False)))
        include_bcdr = st.checkbox("BCDR", value=bool(prefs.get("include_bcdr", False)))

    # Secondary region (only when HA or BCDR)
    secondary_region: Optional[str] = None
    if include_ha or include_bcdr:
        secondary_options = ["(none — single region)"] + [r for r in AZURE_REGIONS if r != region]
        sec_pick = st.selectbox("Secondary region (DR / failover)", secondary_options, index=0)
        if not sec_pick.startswith("(none"):
            secondary_region = sec_pick

    # ----- Extraction (cached) -----
    api_key = st.session_state.get("_anthropic_key", "")
    items: List = []
    mode = "failed"
    spec = None

    ext_key = (
        f"ext::{hash(upload_bytes)}::{upload_name}::{strategy_key}::"
        f"{include_ha}::{include_off}"
    )
    if ext_key in st.session_state:
        items, mode, spec = st.session_state[ext_key]
        st.success(f"Using cached extraction ({len(items)} VM(s), `{mode}` mode).")
        if mode == "direct" and getattr(spec, "summary", None):
            st.caption(spec.summary)
    else:
        if not api_key:
            st.warning("Anthropic API key required for VM extraction. Paste one in the sidebar.")
        else:
            with st.spinner("Extracting inventory (Sonnet)…"):
                try:
                    items, mode, spec = ai_parse_inventory(
                        upload_bytes, upload_name, api_key,
                        include_powered_off=include_off,
                        strategy_hint=strategy_guidance(strategy_key, ha_enabled=include_ha),
                    )
                    st.session_state[ext_key] = (items, mode, spec)
                    st.success(f"Extracted {len(items)} VM(s) in `{mode}` mode.")
                    if mode == "direct" and getattr(spec, "summary", None):
                        st.caption(spec.summary)
                except Exception as e:
                    st.warning(f"AI extraction failed ({type(e).__name__}: {e}).")
                    with st.expander("Traceback", expanded=False):
                        st.code(traceback.format_exc())
                    if kind_from_name(upload_name) == "spreadsheet":
                        try:
                            items, _ = parse_inventory(upload_bytes, upload_name, include_powered_off=include_off)
                            mode, spec = "heuristic", None
                            st.info(f"Heuristic parser recovered {len(items)} item(s).")
                        except Exception as e2:
                            st.error(f"Heuristic parser also failed: {e2}")
                    else:
                        st.error("No heuristic fallback for this file type — only AI extraction supports PDF/image/DOCX/text.")

    if items:
        import pandas as pd
        inv_df = pd.DataFrame([{
            "Name": i.name, "vCPU": i.vcpu, "Memory (GB)": i.memory_gb,
            "Storage (GB)": i.storage_gb, "OS": i.os, "Power": i.powerstate,
            "Notes": i.notes,
        } for i in items])
        st.dataframe(inv_df, use_container_width=True, hide_index=True)

    # ----- LZ component checkboxes -----
    lz_selected: List[str] = []
    backup_pct = int(prefs.get("backup_pct", 40))
    la_mb_per_vm_per_day = int(prefs.get("la_mb_per_vm_per_day", 200))
    bandwidth_gb = int(prefs.get("bandwidth_gb", 200))
    waf_capacity_units = int(prefs.get("waf_capacity_units", 2))
    firewall_gb = int(prefs.get("firewall_gb_processed", 0))
    if include_lz:
        with st.expander("Landing zone components (tick what to include)", expanded=True):
            st.caption(
                "Public IP vs ExpressRoute is typically an either/or for external "
                "connectivity."
            )
            saved_lz = set(prefs.get("lz_selected", []))
            for comp in LANDING_ZONE_COMPONENTS:
                default_on = (comp.key in saved_lz) if saved_lz else comp.default_enabled
                on = st.checkbox(comp.resource, value=default_on, key=f"ls_lz_{comp.key}")
                if on:
                    lz_selected.append(comp.key)
            backup_pct = st.slider("Azure Backup — % of total disk", 0, 200, backup_pct, 5)
            la_mb_per_vm_per_day = st.number_input(
                "Log Analytics — MB/day per VM",
                min_value=0, max_value=5000, value=la_mb_per_vm_per_day, step=50,
            )
            bandwidth_gb = st.number_input(
                "Bandwidth egress — TOTAL GB/month (first 100 GB free; tiered)",
                min_value=0, max_value=10_000_000, value=bandwidth_gb, step=50,
                help=(
                    "Total outbound data transfer per month. Azure applies "
                    "tiered pricing: 0-10 TB full rate, 10-50 TB ~5% off, "
                    "50-150 TB ~20% off, 150-500 TB ~45% off, 500+ TB ~55% off."
                ),
            )
            waf_capacity_units = st.number_input(
                "App Gateway WAF v2 — Capacity Units (avg)",
                min_value=0, max_value=125, value=int(prefs.get("waf_capacity_units", 2)), step=1,
                help=(
                    "Azure Pricing Calculator bills WAF v2 on base instance "
                    "hours PLUS Capacity Units. Typical: 2-4 CU."
                ),
            )
            firewall_gb = st.number_input(
                "Azure Firewall — data processed (GB/month)",
                min_value=0, max_value=10_000_000,
                value=int(prefs.get("firewall_gb_processed", 0)), step=100,
                help=(
                    "Per-GB processed charge on top of the deployment hour. "
                    "Leave 0 if the firewall only handles hub idle traffic."
                ),
            )

    # ----- Exports for cross-pillar wiring -----
    vm_count = aggregate_vm_count(items) if items else 0
    la_gb = round(la_mb_per_vm_per_day * vm_count * 30 / 1024.0, 2)
    total_storage = round(sum(i.storage_gb for i in items), 2) if items else 0.0

    return {
        "items": items, "mode": mode, "spec": spec,
        "strategy_key": strategy_key, "headroom": headroom,
        "disk_tier": disk_tier, "os_mode": os_mode,
        "include_off": include_off,
        "include_lz": include_lz,
        "include_ha": include_ha,
        "include_bcdr": include_bcdr,
        "secondary_region": secondary_region,
        "lz_selected": lz_selected,
        "backup_pct": backup_pct,
        "la_mb_per_vm_per_day": la_mb_per_vm_per_day,
        "bandwidth_gb": bandwidth_gb,
        "waf_capacity_units": waf_capacity_units,
        "firewall_gb_processed": firewall_gb,
        "__exports__": {
            "vm_count": vm_count,
            "la_gb": la_gb,
            "total_storage": total_storage,
        },
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    items = inputs.get("items") or []
    if not items:
        return [], []

    compute_lines, mapping_rows = build_compute_bom(
        items=items, client=client, region=region,
        headroom=inputs.get("headroom", 1.3),
        disk_tier=inputs.get("disk_tier", "Premium SSD"),
        os_override=inputs.get("os_mode", "as-detected"),
        app_name=app_name, pricing_mode=pricing_mode,
        use_ahb=bool(inputs.get("__use_ahb__", False)),
    )

    all_lines: List[BomLine] = []
    if inputs.get("include_ha"):
        compute_lines = apply_ha_multiplier(compute_lines, factor=2)
        all_lines.extend(build_ha_bom(client=client, region=region))
    all_lines.extend(compute_lines)

    # Dynamic quantities for LZ overrides
    total_storage = inputs["__exports__"]["total_storage"]
    vm_count = inputs["__exports__"]["vm_count"]
    backup_gb = round(total_storage * inputs.get("backup_pct", 40) / 100.0, 2)
    la_gb = inputs["__exports__"]["la_gb"]
    lz_overrides: Dict[str, float] = {
        "recovery_vault": backup_gb,
        "log_analytics": la_gb,
        "bandwidth_egress": float(inputs.get("bandwidth_gb", 200)),
        # Azure Backup per-VM protected-instance fee — VM count from inventory
        "recovery_vault_instances": float(vm_count),
        # App Gateway WAF v2 Capacity Units × 730 h
        "app_gateway_waf_cu": float(inputs.get("waf_capacity_units", 2)) * 730.0,
        # Azure Firewall per-GB processed
        "firewall_data": float(inputs.get("firewall_gb_processed", 0)),
    }

    # Auto-enable derived LZ components that pair with primary toggles:
    #   recovery_vault    → also adds recovery_vault_instances
    #   app_gateway_waf   → also adds app_gateway_waf_cu (if CU input > 0)
    #   firewall          → also adds firewall_data (if GB input > 0)
    auto_enabled = list(inputs.get("lz_selected") or [])
    if "recovery_vault" in auto_enabled and "recovery_vault_instances" not in auto_enabled and vm_count > 0:
        auto_enabled.append("recovery_vault_instances")
    if "app_gateway_waf" in auto_enabled and inputs.get("waf_capacity_units", 0) > 0 \
            and "app_gateway_waf_cu" not in auto_enabled:
        auto_enabled.append("app_gateway_waf_cu")
    if "firewall" in auto_enabled and inputs.get("firewall_gb_processed", 0) > 0 \
            and "firewall_data" not in auto_enabled:
        auto_enabled.append("firewall_data")

    if inputs.get("include_lz") and auto_enabled:
        all_lines.extend(
            build_landing_zone_bom(
                client=client, region=region,
                enabled_keys=auto_enabled,
                quantity_overrides=lz_overrides,
            )
        )

    if inputs.get("include_bcdr"):
        all_lines.extend(
            build_bcdr_bom(
                client=client, region=region, items=items,
                secondary_region=inputs.get("secondary_region"),
            )
        )

    return all_lines, mapping_rows
