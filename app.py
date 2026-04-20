"""Azure Cost Assessment — Streamlit app.

Flow:
  1. Upload any document/spreadsheet/use-case describing the target workload.
  2. Haiku classifier decides whether this is VM inventory, SIEM/SOC, AI/ML,
     data platform, app modernization, or mixed — and recommends components.
  3. Only the relevant pricing calculators run. Expensive Opus extraction
     happens ONLY when the workload is a VM inventory.

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
    SECURITY_COMPONENTS,
    apply_ha_multiplier,
    build_bcdr_bom,
    build_ha_bom,
    build_security_bom,
    strategy_guidance,
)
from src.constants import AZURE_REGIONS, CURRENCIES, DEFAULT_REGION, DEFAULT_CURRENCY
from src.landing_zone import LANDING_ZONE_COMPONENTS, build_landing_zone_bom
from src.output import (
    build_excel_bom,
    build_pricing_calculator_import,
    build_pricing_calculator_links,
)
from src.parsers import parse_inventory, ai_parse_inventory, classify
from src.pricing.retail import RetailPricesClient
from src.workloads import (
    AZURE_OPENAI_MODELS,
    FABRIC_CAPACITIES,
    SIEM_COMMITMENT_TIERS,
    build_ai_ml_bom,
    build_data_platform_bom,
    build_siem_bom,
)


st.set_page_config(page_title="Azure Cost Assessment", page_icon="$", layout="wide")

st.title("Azure Cost Assessment")
st.caption(
    "Upload anything — VM inventory, SIEM design, AI use-case, data-platform "
    "spec, or a mixed architecture doc. The workload is classified first and "
    "only the relevant pricing models run. Output matches Microsoft's Azure "
    "Pricing Calculator export template."
)

# ---------------- Sidebar: configuration + AI key ----------------
with st.sidebar:
    st.header("Configuration")
    app_name = st.text_input(
        "Application / workload name",
        value="",
        placeholder="e.g. ERPSuite, FraudAI",
        help="Tagged into 'Custom name' for every BOM line.",
    )
    region = st.selectbox(
        "Primary region",
        AZURE_REGIONS,
        index=AZURE_REGIONS.index(DEFAULT_REGION),
    )
    currency = st.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(DEFAULT_CURRENCY))

    st.subheader("AI key")
    _default_key = ""
    try:
        _default_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        pass
    anthropic_key = st.text_input(
        "Anthropic API key",
        type="password",
        value=_default_key,
        help=(
            "Required for classification + AI extraction. Keys are held in "
            "session memory only, never written to disk."
        ),
    )

    st.warning(
        "**Data egress notice:** when AI features are enabled, a preview of "
        "your uploaded file (sheet names, headers, sample rows — or full "
        "content for files ≤500 rows) is sent to Anthropic for classification "
        "and extraction. Remove sensitive data (passwords, PII) before upload. "
        "Anthropic does not train on API inputs per their terms."
    )

# ---------------- Upload ----------------
st.subheader("1. Upload workload description")
uploaded = st.file_uploader(
    "RVTools .xlsx, generic Excel/CSV, use-case doc — any format. "
    "The classifier decides what to run.",
    type=["xlsx", "xls", "csv"],
)

if uploaded is None:
    st.info(
        "Upload a file to begin. Supports VM inventories (RVTools, infra lists), "
        "SIEM/SOC design docs, AI use-cases, data-platform specs, or mixed "
        "architecture documents."
    )
    st.stop()

upload_bytes = uploaded.read()
upload_name = uploaded.name

# ---------------- Stage A: classify ----------------
st.subheader("2. Workload classification")
if not anthropic_key:
    st.error(
        "Anthropic API key required for classification. Paste one in the "
        "sidebar or set ANTHROPIC_API_KEY via .streamlit/secrets.toml."
    )
    st.stop()

cache_key = f"profile::{hash(upload_bytes)}::{upload_name}"
if cache_key in st.session_state:
    profile = st.session_state[cache_key]
else:
    with st.spinner("Classifying workload (Haiku)…"):
        try:
            profile = classify(upload_bytes, upload_name, anthropic_key)
            st.session_state[cache_key] = profile
        except Exception as e:
            st.error(f"Classification failed: {type(e).__name__}: {e}")
            with st.expander("Traceback", expanded=False):
                st.code(traceback.format_exc())
            st.stop()

WORKLOAD_LABELS = {
    "vm_inventory": "VM Inventory (lift-and-shift or app modernization)",
    "app_modernization": "Application Modernization (PaaS-leaning)",
    "siem_soc": "SIEM / SOC (Sentinel + Log Analytics)",
    "data_platform": "Data Platform (Fabric / Synapse / Cosmos)",
    "ai_ml": "AI / ML (Azure OpenAI / ML workspace)",
    "mixed": "Mixed (multiple categories)",
    "unknown": "Unknown",
}

col_c1, col_c2, col_c3 = st.columns([2, 1, 1])
col_c1.markdown(f"**Detected:** {WORKLOAD_LABELS.get(profile.workload_type, profile.workload_type)}")
col_c2.metric("Confidence", f"{profile.confidence:.0%}")
col_c3.metric("Complexity", profile.complexity)
if profile.summary:
    st.caption(profile.summary)
if profile.signals:
    with st.expander("Why this classification? (signals)", expanded=False):
        for s in profile.signals:
            st.markdown(f"- {s}")

# Allow manual override
override_options = list(WORKLOAD_LABELS.keys())
override_idx = override_options.index(profile.workload_type) if profile.workload_type in override_options else override_options.index("unknown")
chosen_type = st.selectbox(
    "Override workload type (optional)",
    override_options,
    index=override_idx,
    format_func=lambda k: WORKLOAD_LABELS[k],
)

# ---------------- Stage B: workload-specific inputs ----------------
st.subheader("3. Workload inputs")

bom_sections: list = []
mapping_rows: list = []
secondary_region = None
include_ha = False
include_bcdr = False
include_lz = False

# --- VM-inventory / app-mod / mixed: full flow ---
run_vm_flow = chosen_type in ("vm_inventory", "app_modernization", "mixed") or profile.needs_vm_extraction
run_siem_flow = chosen_type == "siem_soc" or ("sentinel" in profile.suggested_components)
run_ai_flow = chosen_type == "ai_ml" or ("azure_openai" in profile.suggested_components)
run_data_flow = chosen_type == "data_platform" or any(
    c in profile.suggested_components for c in ("fabric", "synapse", "cosmos_db")
)

items: list = []

if run_vm_flow:
    st.markdown("#### VM inventory")
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        strategy_key = st.radio(
            "Migration strategy",
            list(MIGRATION_STRATEGIES.keys()),
            format_func=lambda k: MIGRATION_STRATEGIES[k]["label"],
            index=0 if chosen_type == "vm_inventory" else 1,
        )
        headroom = st.slider("Sizing headroom", 1.0, 2.0, 1.3, step=0.05)
        disk_tier = st.selectbox("Default disk tier", ["Premium SSD", "Standard SSD", "Standard HDD"])
    with col_v2:
        os_mode = st.radio("OS handling", ["as-detected", "Linux", "Windows"], horizontal=True)
        include_off = st.checkbox("Include powered-off VMs", value=False)
        include_lz = st.checkbox("Landing Zone", value=True)
        include_ha = st.checkbox("High Availability", value=False)
        include_bcdr = st.checkbox("BCDR", value=False)

    if include_ha or include_bcdr:
        secondary_options = ["(none — single region)"] + [r for r in AZURE_REGIONS if r != region]
        sec_pick = st.selectbox("Secondary region (DR / failover)", secondary_options, index=0)
        if not sec_pick.startswith("(none"):
            secondary_region = sec_pick

    with st.spinner("Extracting inventory (Opus)…"):
        try:
            items, mode, spec = ai_parse_inventory(
                upload_bytes,
                upload_name,
                anthropic_key,
                include_powered_off=include_off,
                strategy_hint=strategy_guidance(strategy_key, ha_enabled=include_ha),
            )
            st.success(f"Extracted {len(items)} VM(s) in `{mode}` mode.")
            if mode == "direct" and getattr(spec, "summary", None):
                st.caption(spec.summary)
        except Exception as e:
            st.warning(f"AI extraction failed ({type(e).__name__}: {e}). Falling back to heuristic parser.")
            with st.expander("Traceback", expanded=False):
                st.code(traceback.format_exc())
            try:
                items, _ = parse_inventory(upload_bytes, upload_name, include_powered_off=include_off)
            except Exception as e2:
                st.error(f"Heuristic parser also failed: {e2}")
                items = []

    if items:
        inv_df = pd.DataFrame([{
            "Name": i.name, "vCPU": i.vcpu, "Memory (GB)": i.memory_gb,
            "Storage (GB)": i.storage_gb, "OS": i.os, "Power": i.powerstate,
            "Notes": i.notes,
        } for i in items])
        st.dataframe(inv_df, use_container_width=True, hide_index=True)

# --- Landing-zone detailed checkboxes (shown when LZ is on) ---
lz_selected: list = []
lz_overrides: dict = {}
backup_pct = 40
la_mb_per_vm_per_day = 200
if run_vm_flow and include_lz:
    with st.expander("Landing zone components (tick what to include)", expanded=True):
        st.caption(
            "Public IP vs ExpressRoute is typically an either/or for external "
            "connectivity."
        )
        for comp in LANDING_ZONE_COMPONENTS:
            on = st.checkbox(comp.resource, value=comp.default_enabled, key=f"lz_{comp.key}")
            if on:
                lz_selected.append(comp.key)
        backup_pct = st.slider("Azure Backup — % of total disk", 0, 200, 40, 5)
        la_mb_per_vm_per_day = st.number_input(
            "Log Analytics — MB/day per VM", min_value=0, max_value=5000, value=200, step=50
        )

# --- Security checkboxes (shown for VM + SIEM) ---
sec_enabled: list = []
sec_manual_counts: dict = {}
if run_vm_flow or run_siem_flow:
    with st.expander("Security components", expanded=run_siem_flow):
        for key, meta in SECURITY_COMPONENTS.items():
            on = st.checkbox(meta["label"], value=meta["default"], key=f"sec_{key}")
            if on:
                sec_enabled.append(key)
                if meta.get("scales_with") == "manual":
                    defender_key = meta.get("defender_plan", key)
                    sec_manual_counts[defender_key] = st.number_input(
                        meta.get("unit_prompt", "Count"), min_value=0.0, value=0.0, key=f"sec_qty_{key}"
                    )

# --- SIEM-only inputs ---
siem_gb_per_day = 0
siem_retention_days = 0
siem_tier = "payg"
if run_siem_flow:
    st.markdown("#### SIEM / SOC inputs")
    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        siem_gb_per_day = st.number_input(
            "Log ingestion (GB/day)", min_value=0.0, value=25.0, step=5.0,
            help="Total across all log sources. 1 EPS ≈ 2-3 MB/day → 10K EPS ≈ 25 GB/day.",
        )
    with col_s2:
        siem_retention_days = st.number_input(
            "Retention beyond free 90 days", min_value=0, value=275, step=30,
            help="Extra retention in days. 275 = ~1 year total.",
        )
    with col_s3:
        siem_tier = st.selectbox(
            "Commitment tier",
            list(SIEM_COMMITMENT_TIERS.keys()),
            format_func=lambda k: SIEM_COMMITMENT_TIERS[k]["label"],
        )

# --- AI/ML inputs ---
ai_usage: dict = {}
if run_ai_flow:
    st.markdown("#### Azure OpenAI token volume")
    st.caption("Enter daily token volume per model (in 1K-token units). Leave 0 to skip.")
    for mk, model in AZURE_OPENAI_MODELS.items():
        col_m1, col_m2, col_m3 = st.columns([2, 1, 1])
        col_m1.markdown(f"**{model.label}** (`{mk}`)")
        in_1k = col_m2.number_input(
            f"Input 1K/day", min_value=0.0, value=0.0, step=100.0, key=f"ai_in_{mk}",
            label_visibility="collapsed", placeholder="Input 1K/day",
        )
        out_1k = col_m3.number_input(
            f"Output 1K/day", min_value=0.0, value=0.0, step=50.0, key=f"ai_out_{mk}",
            label_visibility="collapsed", placeholder="Output 1K/day",
        )
        if in_1k > 0 or out_1k > 0:
            ai_usage[mk] = {"input_1k_per_day": in_1k, "output_1k_per_day": out_1k}

# --- Data platform inputs ---
fabric_sku = None
cosmos_ru = 0
synapse_dwu = 0
if run_data_flow:
    st.markdown("#### Data Platform")
    col_d1, col_d2, col_d3 = st.columns(3)
    with col_d1:
        fabric_pick = st.selectbox("Microsoft Fabric capacity", ["(none)"] + FABRIC_CAPACITIES, index=0)
        fabric_sku = None if fabric_pick == "(none)" else fabric_pick
    with col_d2:
        cosmos_ru = st.number_input("Cosmos DB throughput (RU/s)", min_value=0, value=0, step=400)
    with col_d3:
        synapse_dwu = st.number_input("Synapse Dedicated SQL Pool (DWU)", min_value=0, value=0, step=100)

# ---------------- Stage C: generate BOM ----------------
st.subheader("4. Generate assessment")
if st.button("Run Azure Cost Assessment", type="primary"):
    with st.spinner("Pricing via Azure Retail Prices API…"):
        client = RetailPricesClient(currency=currency)
        all_lines: list = []

        # VM flow
        if run_vm_flow and items:
            compute_lines, mapping_rows = build_compute_bom(
                items=items, client=client, region=region,
                headroom=headroom, disk_tier=disk_tier, os_override=os_mode,
                app_name=app_name,
            )
            if include_ha:
                compute_lines = apply_ha_multiplier(compute_lines, factor=2)
                all_lines.extend(build_ha_bom(client=client, region=region))
            all_lines.extend(compute_lines)

            vm_count = aggregate_vm_count(items)
            total_storage = round(sum(i.storage_gb for i in items), 2)
            backup_gb = round(total_storage * backup_pct / 100.0, 2)
            la_gb = round(la_mb_per_vm_per_day * vm_count * 30 / 1024.0, 2)
            lz_overrides["recovery_vault"] = backup_gb
            lz_overrides["log_analytics"] = la_gb

            if include_lz and lz_selected:
                all_lines.extend(
                    build_landing_zone_bom(
                        client=client, region=region,
                        enabled_keys=lz_selected, quantity_overrides=lz_overrides,
                    )
                )
            if include_bcdr:
                all_lines.extend(
                    build_bcdr_bom(
                        client=client, region=region, items=items,
                        secondary_region=secondary_region,
                    )
                )
            all_lines.extend(
                build_security_bom(
                    client=client, region=region,
                    enabled_keys=sec_enabled, vm_count=vm_count,
                    la_gb=la_gb, manual_counts=sec_manual_counts,
                )
            )

        # SIEM-only flow (skip VM extraction entirely)
        if run_siem_flow and not run_vm_flow and siem_gb_per_day > 0:
            all_lines.extend(
                build_siem_bom(
                    client=client, region=region,
                    gb_per_day=siem_gb_per_day,
                    retention_days_over_free=int(siem_retention_days),
                    commitment_tier=siem_tier,
                    include_cspm=("cspm" in sec_enabled),
                    app_name=app_name,
                )
            )

        # AI flow
        if run_ai_flow and ai_usage:
            all_lines.extend(
                build_ai_ml_bom(client=client, region=region, usage=ai_usage, app_name=app_name)
            )

        # Data platform flow
        if run_data_flow and (fabric_sku or cosmos_ru > 0 or synapse_dwu > 0):
            all_lines.extend(
                build_data_platform_bom(
                    client=client, region=region,
                    fabric_sku=fabric_sku,
                    cosmos_ru_per_second=int(cosmos_ru),
                    synapse_dwu=int(synapse_dwu),
                    app_name=app_name,
                )
            )

    if getattr(client, "_last_error", None):
        st.warning(f"Retail Prices API had issues: {client._last_error}")

    if not all_lines:
        st.warning("No BOM lines produced. Check inputs above and re-run.")
    else:
        st.session_state["bom_lines"] = all_lines
        st.session_state["mapping_rows"] = mapping_rows
        st.session_state["region"] = region
        st.session_state["currency"] = currency
        st.session_state["app_name"] = app_name

# ---------------- Results ----------------
if "bom_lines" in st.session_state:
    lines = st.session_state["bom_lines"]
    mapping_rows = st.session_state["mapping_rows"]
    region = st.session_state["region"]
    currency = st.session_state["currency"]
    saved_app = st.session_state.get("app_name", "")

    st.subheader("5. Results")
    df = pd.DataFrame([l.to_row() for l in lines])
    total_monthly = float(df["monthly_cost"].sum()) if not df.empty else 0.0

    m1, m2, m3 = st.columns(3)
    m1.metric("Line items", len(df))
    m2.metric(f"Monthly ({currency})", f"{total_monthly:,.2f}")
    m3.metric(f"Annual ({currency})", f"{total_monthly * 12:,.2f}")

    if not df.empty:
        by_cat = (
            df.groupby("category", as_index=False)["monthly_cost"].sum()
            .sort_values("monthly_cost", ascending=False)
        )
        st.bar_chart(by_cat, x="category", y="monthly_cost")

    with st.expander("All line items", expanded=True):
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("6. Download")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        excel_bytes = build_excel_bom(lines, mapping_rows, region, currency, app_name=saved_app)
        _fname = (saved_app or "assessment").replace(" ", "-")
        st.download_button(
            "Download Excel (Azure Pricing Calculator template)",
            excel_bytes,
            file_name=f"azure-{_fname}-{region}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col_d2:
        pc_json = build_pricing_calculator_import(lines, region, currency)
        st.download_button(
            "Download Pricing Calculator import (JSON)",
            pc_json,
            file_name=f"azure-{_fname}-{region}.json",
            mime="application/json",
        )

    st.subheader("7. Azure Pricing Calculator links")
    for link in build_pricing_calculator_links(lines):
        st.markdown(f"- [{link['label']}]({link['url']})")
