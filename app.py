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

import hmac
import traceback
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from src.constants import AZURE_REGIONS, CURRENCIES, DEFAULT_REGION, DEFAULT_CURRENCY
from src.output import (
    build_excel_bom,
    build_pricing_calculator_import,
)
from src.parsers import classify
from src.parsers.content import ALL_SUPPORTED_EXTS
from src.pillars import PILLAR_ORDER, PILLARS, all_metadata, get_pillar
from src.pricing.retail import BILLING_TERMS, RetailPricesClient
from src import storage, usage_tracker


st.set_page_config(page_title="Azure Cost Assessment", page_icon="$", layout="wide")


# ---------------- Access control ----------------
# Hardcoded shared credentials (deliberately — this app is deployed for a
# specific audience on a private Streamlit Cloud URL). If you fork, change these.
_AUTH_USER = "admin"
_AUTH_PASS = "noventiqazure"


def _require_login() -> None:
    """Render a login form and st.stop() until the correct creds are entered."""
    if st.session_state.get("_authed"):
        return
    # Persistent login flag from localStorage (opt-in, client-trustable only)
    if storage.load_auth_ok():
        st.session_state["_authed"] = True
        return

    st.title("Azure Cost Assessment — Sign in")
    with st.form("_login_form"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        remember = st.checkbox("Remember me on this browser", value=True)
        submit = st.form_submit_button("Sign in", type="primary")
    if submit:
        ok_user = hmac.compare_digest(u or "", _AUTH_USER)
        ok_pass = hmac.compare_digest(p or "", _AUTH_PASS)
        if ok_user and ok_pass:
            st.session_state["_authed"] = True
            if remember:
                storage.save_auth_ok()
            st.rerun()
        else:
            st.error("Invalid credentials.")
    st.stop()


_require_login()


st.title("Azure Cost Assessment")
st.caption(
    "Upload anything — VM inventory, SIEM design, AI use-case, data-platform "
    "spec, or a mixed architecture doc. The workload is classified first and "
    "only the relevant pricing models run. Output matches Microsoft's Azure "
    "Pricing Calculator export template."
)

# ---------------- Load persisted preferences (browser localStorage) ----------------
_prefs = storage.load_prefs()
_saved_api_key = storage.load_api_key()

# ---------------- Sidebar: configuration + AI key ----------------
with st.sidebar:
    st.header("Configuration")
    app_name = st.text_input(
        "Application / workload name",
        value=_prefs.get("app_name", ""),
        placeholder="e.g. ERPSuite, FraudAI",
        help="Tagged into 'Custom name' for every BOM line.",
    )
    _default_region = _prefs.get("region") or DEFAULT_REGION
    region = st.selectbox(
        "Primary region",
        AZURE_REGIONS,
        index=AZURE_REGIONS.index(_default_region) if _default_region in AZURE_REGIONS else AZURE_REGIONS.index(DEFAULT_REGION),
    )
    _default_currency = _prefs.get("currency") or DEFAULT_CURRENCY
    currency = st.selectbox(
        "Currency",
        CURRENCIES,
        index=CURRENCIES.index(_default_currency) if _default_currency in CURRENCIES else CURRENCIES.index(DEFAULT_CURRENCY),
    )

    st.subheader("Billing term")
    _bt_keys = list(BILLING_TERMS.keys())
    _saved_bt = _prefs.get("pricing_mode", "payg")
    _bt_idx = _bt_keys.index(_saved_bt) if _saved_bt in _bt_keys else 0
    pricing_mode = st.radio(
        "How compute is billed",
        _bt_keys,
        format_func=lambda k: BILLING_TERMS[k]["label"],
        index=_bt_idx,
        help=(
            "Applied to VM compute only. RI = upfront prepay for 1 or 3 "
            "years (biggest discount, least flexible). Savings Plan = "
            "1/3-year hourly commitment (discount, more flexible — covers "
            "any VM family). When a SKU has no RI/SP price in the region, "
            "the line silently falls back to PAYG and a notice appears "
            "after Generate."
        ),
    )

    st.subheader("AI key")
    _secrets_key = ""
    try:
        _secrets_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        pass
    anthropic_key = st.text_input(
        "Anthropic API key",
        type="password",
        value=_saved_api_key or _secrets_key,
        help="Required for classification + AI extraction.",
    )
    remember_key = st.checkbox(
        "Remember key in browser (convenience)",
        value=bool(_saved_api_key),
        help=(
            "Stores the key in this browser's localStorage so you don't have to "
            "paste it again. DO NOT enable on shared/public machines — the key "
            "is readable via DevTools and any browser extension with page access."
        ),
    )
    if remember_key and anthropic_key and anthropic_key != _saved_api_key:
        storage.save_api_key(anthropic_key)
    elif not remember_key and _saved_api_key:
        storage.clear_api_key()

    st.warning(
        "**Data egress notice:** when AI features are enabled, a preview of "
        "your uploaded file (sheet names, headers, sample rows — or full "
        "content for files ≤500 rows) is sent to Anthropic for classification "
        "and extraction. Remove sensitive data (passwords, PII) before upload. "
        "Anthropic does not train on API inputs per their terms."
    )

    with st.expander("Browser storage", expanded=False):
        st.caption(
            "Non-sensitive preferences (region, strategy, selected components, "
            "sizing sliders) are auto-saved to your browser so refreshes and "
            "redeploys don't lose your configuration."
        )
        col_s1, col_s2 = st.columns(2)
        if col_s1.button("Clear saved prefs"):
            storage.clear_prefs()
            st.rerun()
        if col_s2.button("Clear everything", help="Prefs + API key + last result + login"):
            storage.clear_all()
            st.session_state.pop("_authed", None)
            st.rerun()
        if st.button("Sign out"):
            storage.clear_auth_ok()
            st.session_state.pop("_authed", None)
            st.rerun()

# ---------------- Upload ----------------
st.subheader("1. Upload workload description")
uploaded = st.file_uploader(
    "Any format: Excel/CSV (RVTools, infra list), PDF (design doc, RFP), "
    "image (PNG/JPG/GIF/WebP — screenshots of spec tables or diagrams work), "
    "Word (.docx), or plain text/markdown. The classifier decides what to run.",
    type=ALL_SUPPORTED_EXTS,
    help=(
        "PDFs and images are read natively by Claude (no OCR preprocessing). "
        "DOCX is text-extracted including tables. Spreadsheets keep the "
        "fast mapping-based parser for large files."
    ),
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

# The Anthropic API key is consumed inside pillar render_inputs for VM
# extraction — stash it in session state for them.
st.session_state["_anthropic_key"] = anthropic_key

PILLAR_META = all_metadata()

col_c1, col_c2, col_c3 = st.columns([2, 1, 1])
_detected_label = (
    PILLAR_META[profile.workload_type]["label"]
    if profile.workload_type in PILLAR_META
    else profile.workload_type.replace("_", " ").title()
)
col_c1.markdown(f"**Detected pillar:** {_detected_label}")
col_c2.metric("Confidence", f"{profile.confidence:.0%}")
col_c3.metric("Complexity", profile.complexity)
if profile.summary:
    st.caption(profile.summary)
if profile.signals:
    with st.expander("Why this classification? (signals)", expanded=False):
        for s in profile.signals:
            st.markdown(f"- {s}")

# Pre-tick rules:
# - single detected pillar → tick that pillar
# - mixed → tick all five
# - unknown / other → tick whatever the user last had (from prefs), else empty
_saved_active = _prefs.get("active_pillars") or []
if profile.workload_type == "mixed":
    _pre_ticked = list(PILLAR_ORDER)
elif profile.workload_type in PILLAR_META:
    _pre_ticked = [profile.workload_type]
elif _saved_active:
    _pre_ticked = [p for p in _saved_active if p in PILLAR_META]
else:
    _pre_ticked = []

active_pillars: list = st.multiselect(
    "Pillars to cost (detected pre-ticked; add more for a combined assessment)",
    options=PILLAR_ORDER,
    default=_pre_ticked,
    format_func=lambda k: f"{PILLAR_META[k]['icon']} {PILLAR_META[k]['label']}",
)

if not active_pillars:
    st.info("Pick at least one pillar to run the assessment.")
    st.stop()

# ---------------- Stage B: per-pillar inputs ----------------
st.subheader("3. Pillar inputs")

pillar_inputs: dict = {}
for pk in active_pillars:
    md = PILLAR_META[pk]
    with st.expander(f"{md['icon']} {md['label']} — {md['description']}", expanded=True):
        pillar_inputs[pk] = get_pillar(pk).render_inputs(
            st, _prefs, app_name, region, upload_bytes, upload_name, profile,
        )

# Cross-pillar wiring: share lift-shift vm_count + la_gb with azure_security.
if "infra_lift_shift" in pillar_inputs and "azure_security" in pillar_inputs:
    exports = pillar_inputs["infra_lift_shift"].get("__exports__", {})
    pillar_inputs["azure_security"].setdefault("vm_count", exports.get("vm_count", 0))
    pillar_inputs["azure_security"].setdefault("la_gb", exports.get("la_gb", 0.0))

# ---------------- Stage C: generate BOM (fan-out across active pillars) ----------------
st.subheader("4. Generate assessment")
if st.button("Run Azure Cost Assessment", type="primary"):
    with st.spinner("Pricing via Azure Retail Prices API…"):
        client = RetailPricesClient(currency=currency)
        all_lines: list = []
        mapping_rows: list = []
        for pk in active_pillars:
            try:
                pl_lines, pl_mapping = get_pillar(pk).build_bom(
                    client=client, region=region,
                    inputs=pillar_inputs.get(pk, {}),
                    app_name=app_name, pricing_mode=pricing_mode,
                )
            except Exception as e:
                st.error(f"{PILLAR_META[pk]['label']} build failed: {type(e).__name__}: {e}")
                with st.expander(f"Traceback — {pk}", expanded=False):
                    st.code(traceback.format_exc())
                continue
            all_lines.extend(pl_lines)
            mapping_rows.extend(pl_mapping)

    if getattr(client, "fallbacks_used", None):
        pairs = ", ".join(f"{p}→{f}" for p, f in sorted(client.fallbacks_used))
        st.info(
            f"Some services had no retail prices in your primary region. "
            f"Prices for those lines came from the fallback region(s): {pairs}. "
            "Deploy-to-region remains your primary selection; only the pricing "
            "lookup was redirected."
        )
    if getattr(client, "term_fallbacks", None):
        skus = ", ".join(
            f"{sku} ({BILLING_TERMS.get(mode, {}).get('label', mode)})"
            for sku, mode in sorted(client.term_fallbacks)
        )
        st.info(
            f"RI/Savings Plan not available for some SKUs — those lines fell "
            f"back to Pay-as-you-go: {skus}."
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

        # Auto-save preferences to browser localStorage. Pull pillar-specific
        # widget state out of pillar_inputs where the lift-shift / security
        # pillars stashed it so refreshes don't lose anything.
        ls_in = pillar_inputs.get("infra_lift_shift", {})
        sec_in = pillar_inputs.get("azure_security", {})
        storage.save_prefs({
            "app_name": app_name,
            "region": region,
            "currency": currency,
            "pricing_mode": pricing_mode,
            "active_pillars": active_pillars,
            "strategy_key": ls_in.get("strategy_key", "iaas"),
            "include_lz": ls_in.get("include_lz", True),
            "include_ha": ls_in.get("include_ha", False),
            "include_bcdr": ls_in.get("include_bcdr", False),
            "lz_selected": ls_in.get("lz_selected", []),
            "sec_enabled": sec_in.get("enabled", []),
            "backup_pct": ls_in.get("backup_pct", 40),
            "la_mb_per_vm_per_day": ls_in.get("la_mb_per_vm_per_day", 200),
            "bandwidth_gb": ls_in.get("bandwidth_gb", 200),
            "headroom": ls_in.get("headroom", 1.3),
            "disk_tier": ls_in.get("disk_tier", "Premium SSD"),
            "os_mode": ls_in.get("os_mode", "as-detected"),
            "security_target_scope": sec_in.get("target_scope", 0),
        })
        # Persist the full result so a browser refresh can restore it
        storage.save_last_bom({
            "lines": [l.to_row() for l in all_lines],
            "mapping_rows": mapping_rows,
            "region": region,
            "currency": currency,
            "app_name": app_name,
        })

# ---------------- Results ----------------
# Offer to restore the last assessment if nothing is in session state yet
if "bom_lines" not in st.session_state:
    _last = storage.load_last_bom()
    if _last and _last.get("lines"):
        from src.models import BomLine
        st.info(
            f"Last saved assessment available ({len(_last['lines'])} line items, "
            f"region {_last.get('region')}, app `{_last.get('app_name') or '—'}`)."
        )
        if st.button("Restore last assessment from browser storage"):
            st.session_state["bom_lines"] = [BomLine(**row) for row in _last["lines"]]
            st.session_state["mapping_rows"] = _last.get("mapping_rows", [])
            st.session_state["region"] = _last.get("region", "")
            st.session_state["currency"] = _last.get("currency", "USD")
            st.session_state["app_name"] = _last.get("app_name", "")
            st.rerun()

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

    # Token spend breakdown — per-process Claude API usage + USD cost
    usage_list = usage_tracker.get_usage()
    if usage_list:
        st.subheader("7. AI token spend (this session)")
        st.caption(
            "Per-process breakdown of Claude API usage. Input tokens = fresh "
            "prompt bytes (full-price). Cache read / write = prompt-caching "
            "savings. USD cost = retail API pricing."
        )
        usage_df = pd.DataFrame([{
            "Process": u.process,
            "Model": u.model,
            "Input tokens": u.input_tokens,
            "Cache read tokens": u.cache_read_tokens,
            "Cache create tokens": u.cache_creation_tokens,
            "Output tokens": u.output_tokens,
            "USD cost": round(u.usd_cost, 6),
        } for u in usage_list])
        # Append a totals row
        totals = pd.DataFrame([{
            "Process": "TOTAL",
            "Model": "",
            "Input tokens": int(usage_df["Input tokens"].sum()),
            "Cache read tokens": int(usage_df["Cache read tokens"].sum()),
            "Cache create tokens": int(usage_df["Cache create tokens"].sum()),
            "Output tokens": int(usage_df["Output tokens"].sum()),
            "USD cost": round(float(usage_df["USD cost"].sum()), 6),
        }])
        st.dataframe(pd.concat([usage_df, totals], ignore_index=True), use_container_width=True, hide_index=True)
        total_usd = float(usage_df["USD cost"].sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Calls", len(usage_df))
        c2.metric("Total tokens", int(
            usage_df["Input tokens"].sum()
            + usage_df["Cache read tokens"].sum()
            + usage_df["Cache create tokens"].sum()
            + usage_df["Output tokens"].sum()
        ))
        c3.metric("Total AI cost (USD)", f"${total_usd:.4f}")
