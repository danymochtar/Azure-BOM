"""Azure BOM Generator — Streamlit app.

Run:
    streamlit run app.py
"""
from __future__ import annotations

import io
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analysis import build_compute_bom, aggregate_vm_count
from src.constants import AZURE_REGIONS, CURRENCIES, DEFAULT_REGION, DEFAULT_CURRENCY
from src.landing_zone import (
    LANDING_ZONE_COMPONENTS,
    DEFENDER_PLANS,
    build_landing_zone_bom,
    build_defender_bom,
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
    "Upload an on-prem inventory (RVTools, Excel, or CSV), add a basic landing "
    "zone and Defender for Cloud, and export an Azure BOM with live retail pricing."
)

# ---------------- Sidebar controls ----------------
with st.sidebar:
    st.header("Configuration")
    region = st.selectbox("Azure region", AZURE_REGIONS, index=AZURE_REGIONS.index(DEFAULT_REGION))
    currency = st.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(DEFAULT_CURRENCY))

    st.subheader("Sizing")
    headroom = st.slider("Sizing headroom (multiplier on source specs)", 1.0, 2.0, 1.3, step=0.05)
    disk_tier = st.selectbox("Default disk tier", ["Premium SSD", "Standard SSD", "Standard HDD"])
    os_mode = st.radio("OS handling", ["as-detected", "Linux", "Windows"], horizontal=True)
    include_off = st.checkbox("Include powered-off VMs (RVTools)", value=False)

    st.subheader("AI parser")
    st.caption(
        "Let Claude read any inventory format — arbitrary column names, "
        "custom layouts, mixed units. Falls back to the heuristic parser if off."
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
        help="Get one at console.anthropic.com. Can also be set via ANTHROPIC_API_KEY in .streamlit/secrets.toml.",
    )

    st.subheader("Landing zone")
    lz_selected = []
    lz_overrides = {}
    for comp in LANDING_ZONE_COMPONENTS:
        on = st.checkbox(f"{comp.resource}", value=comp.default_enabled, key=f"lz_{comp.key}")
        if on:
            lz_selected.append(comp.key)
            with st.expander(f"Adjust: {comp.resource}", expanded=False):
                lz_overrides[comp.key] = st.number_input(
                    f"Quantity ({comp.unit})",
                    min_value=0.0,
                    value=float(comp.quantity),
                    key=f"lz_qty_{comp.key}",
                )
                st.caption(comp.notes)

    st.subheader("Defender for Cloud")
    defender_selected = []
    defender_counts = {}
    for plan in DEFENDER_PLANS:
        on = st.checkbox(f"{plan.resource}", value=plan.default_enabled, key=f"df_{plan.key}")
        if on:
            defender_selected.append(plan.key)
            if plan.count_source == "manual":
                defender_counts[plan.key] = st.number_input(
                    f"{plan.resource} — {plan.unit}",
                    min_value=0.0,
                    value=0.0,
                    key=f"df_qty_{plan.key}",
                )
            st.caption(plan.notes)

# ---------------- Main: upload ----------------
st.subheader("1. Upload inventory")
col_u1, col_u2 = st.columns([2, 1])
with col_u1:
    uploaded = st.file_uploader(
        "RVTools .xlsx, generic Excel (.xlsx/.xls), or CSV",
        type=["xlsx", "xls", "csv"],
    )
with col_u2:
    st.write("Or try a sample:")
    use_sample = st.button("Load sample inventory")

items = []
fmt = None
ai_mapping = None


def _parse_with_fallback(data: bytes, filename: str):
    """Try AI parser (if enabled + key present); fall back to heuristic on failure."""
    if use_ai and anthropic_key:
        try:
            with st.spinner("Asking Claude to map your inventory columns..."):
                ai_items, mapping = ai_parse_inventory(
                    data, filename, anthropic_key, include_powered_off=include_off
                )
            st.success(
                f"AI parsed {len(ai_items)} items from sheet `{mapping.sheet_name}`. "
                f"Mapped: name=`{mapping.name_col}`, vCPU=`{mapping.vcpu_col}`, "
                f"memory=`{mapping.memory_col}` ({mapping.memory_unit}), "
                f"storage=`{mapping.storage_col}` ({mapping.storage_unit})."
            )
            if mapping.notes:
                st.info(f"AI notes: {mapping.notes}")
            return ai_items, "ai", mapping
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
        items, fmt, ai_mapping = _parse_with_fallback(uploaded.read(), uploaded.name)
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
    if ai_mapping is not None:
        with st.expander("AI column mapping", expanded=False):
            st.json(ai_mapping.model_dump())
    inv_df = pd.DataFrame([{
        "Name": i.name,
        "vCPU": i.vcpu,
        "Memory (GB)": i.memory_gb,
        "Storage (GB)": i.storage_gb,
        "OS": i.os,
        "Power": i.powerstate,
        "Environment": i.environment,
    } for i in items])
    st.dataframe(inv_df, use_container_width=True, hide_index=True)

    st.subheader("3. Generate BOM")
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
            lz_lines = build_landing_zone_bom(
                client=client,
                region=region,
                enabled_keys=lz_selected,
                quantity_overrides=lz_overrides,
            )
            vm_count = aggregate_vm_count(items)
            df_lines = build_defender_bom(
                client=client,
                region=region,
                enabled_keys=defender_selected,
                vm_count=vm_count,
                manual_counts=defender_counts,
            )
            all_lines = compute_lines + lz_lines + df_lines

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
        "so we link to the per-product calculator pages for the resources in "
        "your BOM. Combine with the JSON above to reproduce the estimate."
    )
    for link in build_pricing_calculator_links(lines):
        st.markdown(f"- [{link['label']}]({link['url']})")

else:
    st.info("Upload an inventory file (or load the sample) and click **Generate Azure BOM**.")
