"""Azure BOM Generator — Streamlit app.

Run:
    streamlit run app.py
"""
from __future__ import annotations

import traceback
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analysis import build_compute_bom, aggregate_vm_count
from src.architecture import (
    MIGRATION_STRATEGIES,
    SECURITY_TIERS,
    apply_ha_multiplier,
    build_bcdr_bom,
    build_ha_bom,
    build_security_tier_bom,
    strategy_guidance,
)
from src.constants import AZURE_REGIONS, CURRENCIES, DEFAULT_REGION, DEFAULT_CURRENCY
from src.landing_zone import (
    LANDING_ZONE_COMPONENTS,
    build_landing_zone_bom,
)
from src.output import (
    build_excel_bom,
    build_pricing_calculator_import,
    build_pricing_calculator_links,
)
from src.parsers import parse_inventory, ai_parse_inventory
from src.pricing.retail import RetailPricesClient


st.set_page_config(page_title="Azure BOM Generator", page_icon="AZ", layout="wide")

st.title("Azure BOM Generator")
st.caption(
    "Upload an on-prem inventory (any format). Choose a migration strategy "
    "and which architecture pillars to include. Export an Azure BOM with "
    "live retail pricing."
)

# ---------------- Sidebar controls ----------------
with st.sidebar:
    st.header("Configuration")
    currency = st.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(DEFAULT_CURRENCY))

    st.subheader("Regions")
    region = st.selectbox(
        "Primary region",
        AZURE_REGIONS,
        index=AZURE_REGIONS.index(DEFAULT_REGION),
        help="Where production workloads run.",
    )

    st.subheader("Migration strategy")
    strategy_key = st.radio(
        "Target architecture",
        list(MIGRATION_STRATEGIES.keys()),
        format_func=lambda k: MIGRATION_STRATEGIES[k]["label"],
        index=0,
    )
    st.caption(MIGRATION_STRATEGIES[strategy_key]["description"])

    st.subheader("Include in BOM")
    include_lz = st.checkbox("Landing Zone", value=True)
    include_ha = st.checkbox(
        "High Availability (2× compute, Standard Load Balancer; zone-redundant PaaS)",
        value=False,
    )
    include_bcdr = st.checkbox(
        "BCDR (Azure Site Recovery + replicated backup)",
        value=False,
    )
    security_tier = st.radio(
        "Security tier",
        list(SECURITY_TIERS.keys()),
        format_func=lambda k: SECURITY_TIERS[k]["label"],
        index=1,
    )
    st.caption(SECURITY_TIERS[security_tier]["description"])

    # Secondary region only makes sense with HA or BCDR
    secondary_region = None
    if include_ha or include_bcdr:
        secondary_options = ["(none — single region)"] + [r for r in AZURE_REGIONS if r != region]
        sec_pick = st.selectbox(
            "Secondary region (DR / failover target)",
            secondary_options,
            index=0,
            help="Used for BCDR replication pricing and failover annotation.",
        )
        if not sec_pick.startswith("(none"):
            secondary_region = sec_pick

    st.subheader("Landing zone components")
    st.caption(
        "Pick exactly what to include. External connectivity is typically "
        "**Public IP** OR **ExpressRoute** (or both if you need internet egress "
        "plus private on-prem connectivity). Greyed out if the 'Landing Zone' "
        "master toggle is off."
    )
    lz_selected = []
    lz_overrides = {}
    for comp in LANDING_ZONE_COMPONENTS:
        default_on = comp.default_enabled and include_lz
        on = st.checkbox(
            f"{comp.resource}",
            value=default_on,
            disabled=not include_lz,
            key=f"lz_{comp.key}",
        )
        if on and include_lz:
            lz_selected.append(comp.key)
            # recovery_vault and log_analytics are driven dynamically below
            if comp.key in ("recovery_vault", "log_analytics"):
                continue
            with st.expander(f"Quantity ({comp.unit})", expanded=False):
                lz_overrides[comp.key] = st.number_input(
                    f"Qty in {comp.unit}",
                    min_value=0.0,
                    value=float(comp.quantity),
                    key=f"lz_qty_{comp.key}",
                    label_visibility="collapsed",
                )
                st.caption(comp.notes)

    st.subheader("Dynamic sizing")
    backup_pct = st.slider(
        "Azure Backup — % of total provisioned storage",
        min_value=0,
        max_value=200,
        value=40,
        step=5,
        help=(
            "Total backed-up GB = this percent × sum of all VM disks. "
            "40% is a typical starting point for daily incrementals + "
            "30-day retention; raise for longer retention (e.g. GFS with "
            "12-month retention can reach 100-150%)."
        ),
    )
    la_mb_per_vm_per_day = st.number_input(
        "Log Analytics — MB/day per VM",
        min_value=0,
        max_value=5000,
        value=200,
        step=50,
        help=(
            "Monthly Log Analytics GB = this × VM count × 30 ÷ 1024.\n\n"
            "Baselines:\n"
            "• ~100-200 MB/day: Azure Monitor Agent with perf counters + "
            "basic Windows/syslog events.\n"
            "• +200-500 MB/day: Defender for Servers P2 security events.\n"
            "• +500 MB-1 GB/day: full Sentinel onboarding with UEBA."
        ),
    )

    st.divider()
    st.subheader("Sizing")
    headroom = st.slider("Sizing headroom (multiplier on source specs)", 1.0, 2.0, 1.3, step=0.05)
    disk_tier = st.selectbox("Default disk tier", ["Premium SSD", "Standard SSD", "Standard HDD"])
    os_mode = st.radio("OS handling", ["as-detected", "Linux", "Windows"], horizontal=True)
    include_off = st.checkbox("Include powered-off VMs (RVTools)", value=False)

    st.subheader("AI parser")
    st.caption(
        "Let Claude read any inventory format — arbitrary column names, "
        "pivoted layouts, free-text specs, mixed units."
    )
    use_ai = st.checkbox("Use AI parser (Claude Opus 4.7)", value=True)
    _default_key = ""
    try:
        _default_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        pass
    anthropic_key = st.text_input(
        "Anthropic API key",
        type="password",
        value=_default_key,
        help="Get one at console.anthropic.com or set ANTHROPIC_API_KEY in .streamlit/secrets.toml.",
    )

# ---------------- Main: upload ----------------
st.subheader("1. Upload inventory")
col_u1, col_u2 = st.columns([2, 1])
with col_u1:
    uploaded = st.file_uploader(
        "RVTools .xlsx, Excel (.xlsx/.xls), or CSV — any layout.",
        type=["xlsx", "xls", "csv"],
    )
with col_u2:
    st.write("Or try a sample:")
    use_sample = st.button("Load sample inventory")

items = []
fmt = None
ai_spec = None


def _parse_with_fallback(data: bytes, filename: str):
    """Try AI parser (if enabled + key present); fall back to heuristic on failure."""
    if use_ai and anthropic_key:
        try:
            with st.spinner("Claude is reading your inventory..."):
                ai_items, mode, spec = ai_parse_inventory(
                    data,
                    filename,
                    anthropic_key,
                    include_powered_off=include_off,
                    strategy_hint=strategy_guidance(strategy_key, ha_enabled=include_ha),
                )
            if mode == "direct":
                st.success(
                    f"AI extracted {len(ai_items)} servers directly. {spec.summary}"
                )
            else:
                st.success(
                    f"AI mapped and parsed {len(ai_items)} items from sheet "
                    f"`{spec.sheet_name}`."
                )
                if spec.notes:
                    st.info(f"AI notes: {spec.notes}")
            return ai_items, "ai", spec
        except Exception as e:
            st.warning(
                f"AI parser failed ({type(e).__name__}: {e}). "
                "Falling back to heuristic parser."
            )
            with st.expander("AI parser traceback", expanded=False):
                st.code(traceback.format_exc())
    its, f = parse_inventory(data, filename, include_powered_off=include_off)
    return its, f, None


if uploaded is not None:
    try:
        items, fmt, ai_spec = _parse_with_fallback(uploaded.read(), uploaded.name)
        if fmt != "ai":
            st.success(f"Parsed {len(items)} items from `{uploaded.name}` (format: {fmt}).")
    except Exception as e:
        st.error(f"Failed to parse file: {e}")

if use_sample:
    sample_path = Path(__file__).parent / "samples" / "sample_inventory.csv"
    if sample_path.exists():
        items, fmt = parse_inventory(sample_path.read_bytes(), sample_path.name)
        st.success(f"Loaded sample: {len(items)} items.")
    else:
        st.warning("Sample file not found.")

if items:
    st.subheader("2. Parsed inventory")
    if ai_spec is not None:
        with st.expander("AI extraction details", expanded=False):
            st.json(ai_spec.model_dump())
    inv_df = pd.DataFrame([{
        "Name": i.name,
        "vCPU": i.vcpu,
        "Memory (GB)": i.memory_gb,
        "Storage (GB)": i.storage_gb,
        "OS": i.os,
        "Power": i.powerstate,
        "Environment": i.environment,
        "Notes": i.notes,
    } for i in items])
    st.dataframe(inv_df, use_container_width=True, hide_index=True)

    st.subheader("3. Generate BOM")
    region_pair = region if not secondary_region else f"{region} → {secondary_region}"
    _total_storage = sum(i.storage_gb for i in items)
    _vm_count = sum(1 for i in items if "off" not in (i.powerstate or "").lower())
    _backup_gb_preview = round(_total_storage * backup_pct / 100.0, 2)
    _la_gb_preview = round(la_mb_per_vm_per_day * _vm_count * 30 / 1024.0, 2)
    st.caption(
        f"Region(s): **{region_pair}** · "
        f"Strategy: **{MIGRATION_STRATEGIES[strategy_key]['label']}** · "
        f"LZ: **{'on' if include_lz else 'off'}** · "
        f"HA: **{'on' if include_ha else 'off'}** · "
        f"BCDR: **{'on' if include_bcdr else 'off'}** · "
        f"Security: **{SECURITY_TIERS[security_tier]['label']}**"
    )
    st.caption(
        f"Dynamic sizing · Backup: **{_backup_gb_preview:,.1f} GB** "
        f"({backup_pct}% of {_total_storage:,.1f} GB) · "
        f"Log Analytics: **{_la_gb_preview:,.1f} GB/mo** "
        f"({la_mb_per_vm_per_day} MB/day × {_vm_count} VMs × 30)"
    )
    if st.button("Generate Azure BOM", type="primary"):
        with st.spinner("Calling Azure Retail Prices API and building BOM..."):
            client = RetailPricesClient(currency=currency)

            compute_lines, mapping_rows = build_compute_bom(
                items=items,
                client=client,
                region=region,
                headroom=headroom,
                disk_tier=disk_tier,
                os_override=os_mode,
            )

            # HA — duplicate compute/storage lines and add Load Balancer
            ha_extra: list = []
            if include_ha:
                compute_lines = apply_ha_multiplier(compute_lines, factor=2)
                ha_extra = build_ha_bom(client=client, region=region)

            # Compute dynamic quantities from inventory + user inputs
            vm_count = aggregate_vm_count(items)
            total_storage_gb = round(sum(i.storage_gb for i in items), 2)
            backup_gb = round(total_storage_gb * backup_pct / 100.0, 2)
            la_gb = round(
                la_mb_per_vm_per_day * vm_count * 30 / 1024.0, 2
            )
            lz_overrides.setdefault("recovery_vault", backup_gb)
            lz_overrides.setdefault("log_analytics", la_gb)

            # Landing zone
            lz_lines: list = []
            if include_lz and lz_selected:
                lz_lines = build_landing_zone_bom(
                    client=client,
                    region=region,
                    enabled_keys=lz_selected,
                    quantity_overrides=lz_overrides,
                )

            # BCDR
            bcdr_lines: list = []
            if include_bcdr:
                bcdr_lines = build_bcdr_bom(
                    client=client,
                    region=region,
                    items=items,
                    secondary_region=secondary_region,
                )

            # Security tier. Only add its own Log Analytics line if the LZ
            # isn't already carrying log_analytics (otherwise double-billing).
            lz_has_la = include_lz and "log_analytics" in lz_selected
            sec_lines = build_security_tier_bom(
                client=client,
                region=region,
                tier=security_tier,
                vm_count=vm_count,
                log_analytics_gb=la_gb,
                sentinel_gb=la_gb,
                add_log_analytics=not lz_has_la,
            )

            all_lines = compute_lines + ha_extra + lz_lines + bcdr_lines + sec_lines

        if client._last_error:
            st.warning(
                "One or more calls to the Azure Retail Prices API failed. "
                "Affected line items show `(price not found)` and $0. "
                f"Last error: {client._last_error}"
            )

        if not all_lines:
            st.warning("No BOM lines produced. Check inventory and retail price lookups.")
        else:
            st.session_state["bom_lines"] = all_lines
            st.session_state["mapping_rows"] = mapping_rows
            st.session_state["region"] = region
            st.session_state["currency"] = currency

# ---------------- Results ----------------
if "bom_lines" in st.session_state:
    lines = st.session_state["bom_lines"]
    mapping_rows = st.session_state["mapping_rows"]
    region = st.session_state["region"]
    currency = st.session_state["currency"]

    st.subheader("4. BOM result")

    df = pd.DataFrame([l.to_row() for l in lines])
    total_monthly = float(df["monthly_cost"].sum())
    total_annual = total_monthly * 12

    m1, m2, m3 = st.columns(3)
    m1.metric("Line items", len(df))
    m2.metric(f"Monthly ({currency})", f"{total_monthly:,.2f}")
    m3.metric(f"Annual ({currency})", f"{total_annual:,.2f}")

    by_cat = (
        df.groupby("category", as_index=False)["monthly_cost"].sum()
        .sort_values("monthly_cost", ascending=False)
    )
    st.bar_chart(by_cat, x="category", y="monthly_cost")

    with st.expander("VM mapping (source -> target)", expanded=False):
        if mapping_rows:
            st.dataframe(pd.DataFrame(mapping_rows), use_container_width=True, hide_index=True)

    with st.expander("All line items", expanded=True):
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("5. Download")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        excel_bytes = build_excel_bom(lines, mapping_rows, region, currency)
        st.download_button(
            "Download Excel BOM",
            excel_bytes,
            file_name=f"azure-bom-{region}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col_d2:
        pc_json = build_pricing_calculator_import(lines, region, currency)
        st.download_button(
            "Download Pricing Calculator import (JSON)",
            pc_json,
            file_name=f"azure-pricing-calc-{region}.json",
            mime="application/json",
        )

    st.subheader("6. Azure Pricing Calculator links")
    st.caption(
        "Microsoft's calculator requires sign-in to save a shareable estimate, "
        "so we link to per-product calculator pages for the resources in your "
        "BOM. Combine with the JSON above to reproduce the estimate."
    )
    for link in build_pricing_calculator_links(lines):
        st.markdown(f"- [{link['label']}]({link['url']})")

else:
    st.info("Upload an inventory file (or load the sample) and click **Generate Azure BOM**.")
