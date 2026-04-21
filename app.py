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
from src.pillars.auto_simulate import simulate as auto_simulate
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
    st.caption(
        "**Azure cost-optimization guidance:** \n"
        "• **Steady-state 24/7** workloads → RI 3Y (50-60% savings).  \n"
        "• **Always-on but product direction uncertain** → SP 1Y or RI 1Y (30-40% savings).  \n"
        "• **Spiky / seasonal** workloads → Pay-as-you-go.  \n"
        "• **Experimental / short-term** → Pay-as-you-go."
    )

    st.subheader("Azure Hybrid Benefit (BYOL with Software Assurance)")
    use_ahb_windows = st.checkbox(
        "Windows Server AHB (bring your own license)",
        value=bool(_prefs.get("use_ahb_windows", False)),
        help=(
            "Applies to all Windows VMs + AKS Windows nodes + GPU VMs running Windows. "
            "Swaps the Windows-VM price for the Linux-VM price of the same SKU "
            "(~30-40% off a Windows VM). Requires existing Windows Server licenses "
            "with active SA."
        ),
    )
    use_ahb_sql = st.checkbox(
        "SQL Server AHB for Azure SQL Database",
        value=bool(_prefs.get("use_ahb_sql", False)),
        help=(
            "Applies a tier-based discount on Azure SQL DB compute: "
            "~55% off General Purpose vCores, ~33% off Business Critical, "
            "~25% off Hyperscale. Requires existing SQL Server licenses with SA."
        ),
    )

    # ------------- Pricing diagnostic -------------
    with st.expander("🔍 Diagnose billing-term availability", expanded=False):
        st.caption(
            "Check whether the Azure Retail Prices API actually returns SP / RI "
            "meters for a given VM SKU in your region. If any row shows "
            "'(not found)', the app silently falls back to PAYG for that SKU."
        )
        diag_sku = st.text_input(
            "ARM VM SKU to probe", value="Standard_D4s_v5",
            key="diag_sku",
            help="e.g. Standard_D4s_v5, Standard_E16s_v5, Standard_NC24ads_A100_v4",
        )
        diag_os = st.selectbox("OS for the probe", ["Linux", "Windows"], key="diag_os")
        if st.button("Probe retail prices for this SKU", key="diag_probe"):
            try:
                from src.pricing.retail import RetailPricesClient as _RPC, BILLING_TERMS as _BT
                _c = _RPC(currency=currency)
                diag_win = diag_os == "Windows"
                rows = []
                for mode_key, cfg in _BT.items():
                    rec = _c.vm_price(diag_sku, region, os_is_windows=diag_win,
                                      pricing_mode=mode_key, use_ahb=False)
                    if rec is None:
                        rows.append({
                            "Mode": cfg["label"], "Status": "NOT FOUND",
                            "Per-hour rate": "—", "Meter": "—",
                            "Monthly (×730)": "$0.00",
                        })
                    else:
                        # "Consumption" means we fell back to PAYG, NOT what user asked for
                        actual = rec.price_type or "Consumption"
                        status = "OK" if actual != "Consumption" or mode_key == "payg" else "FALLBACK → PAYG"
                        rows.append({
                            "Mode": cfg["label"], "Status": status,
                            "Per-hour rate": f"${rec.retail_price:.4f}",
                            "Meter": rec.meter_name[:60],
                            "Monthly (×730)": f"${rec.retail_price * 730:.2f}",
                        })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                if _c.fallbacks_used:
                    st.info(f"Regional fallbacks used during probe: {sorted(_c.fallbacks_used)}")
                if getattr(_c, "_last_error", None):
                    st.warning(_c._last_error)
            except Exception as e:
                st.error(f"Probe failed: {type(e).__name__}: {e}")

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

    with st.expander("📥 Pre-assessment templates", expanded=False):
        st.caption(
            "Optional Excel templates, one per pillar. The infra template is "
            "**not strict** — you can upload any doc (RVTools, Azure Migrate, "
            "PDF, etc.) and the app will extract what's there. Use templates "
            "if you want a clean starting point."
        )
        from src.output.templates import TEMPLATE_REGISTRY
        for label, fname, builder in TEMPLATE_REGISTRY:
            try:
                blob = builder()
            except Exception as te:
                st.warning(f"Could not build {label}: {te}")
                continue
            st.download_button(
                label=f"⬇ {label}",
                data=blob,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"tpl_{fname}",
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
st.subheader("1. Upload workload description(s)")
uploaded_files = st.file_uploader(
    "Upload ONE or MORE files. Mix and match — inventory + DB list + "
    "licensing notes, or a single RVTools export. Supported: Excel/CSV "
    "(RVTools, Azure Migrate, infra list), PDF (design doc, RFP), image "
    "(screenshots), Word (.docx), plain text/markdown.",
    type=ALL_SUPPORTED_EXTS,
    accept_multiple_files=True,
    help=(
        "PDFs and images are read natively by Claude (no OCR preprocessing). "
        "DOCX includes tables. Uploading multiple files is useful when "
        "inventory, DB editions, and AHB / licensing details live in "
        "separate docs — the app merges them into one assessment."
    ),
)

if not uploaded_files:
    st.info(
        "Upload at least one file. Supports VM inventories (RVTools, Azure "
        "Migrate, infra lists), SIEM/SOC design docs, AI use-cases, data-"
        "platform specs, or mixed architecture documents. Multi-file upload "
        "is supported — drop in additional docs to fill gaps."
    )
    st.stop()

# Read + cache each file's bytes once
uploads: list = []
for f in uploaded_files:
    fb = f.read()
    uploads.append({"name": f.name, "bytes": fb})

# ---------------- Stage A: classify each file ----------------
st.subheader("2. Workload classification")
if not anthropic_key:
    st.error(
        "Anthropic API key required for classification. Paste one in the "
        "sidebar or set ANTHROPIC_API_KEY via .streamlit/secrets.toml."
    )
    st.stop()

# Classify each uploaded file; cache in session_state by (file-hash, name).
profiles_per_file: list = []
for u in uploads:
    key = f"profile::{hash(u['bytes'])}::{u['name']}"
    if key in st.session_state:
        prof = st.session_state[key]
    else:
        with st.spinner(f"Classifying `{u['name']}` (Haiku)…"):
            try:
                prof = classify(u["bytes"], u["name"], anthropic_key)
                st.session_state[key] = prof
            except Exception as e:
                st.error(
                    f"Classification failed for `{u['name']}`: "
                    f"{type(e).__name__}: {e}"
                )
                with st.expander(f"Traceback — {u['name']}", expanded=False):
                    st.code(traceback.format_exc())
                prof = None
    profiles_per_file.append({"file": u, "profile": prof})

# Aggregate: pick the highest-confidence non-unknown workload_type as the
# "primary" profile; union the suggested_components across all files.
_valid = [p for p in profiles_per_file if p["profile"] and p["profile"].workload_type != "unknown"]
if _valid:
    primary = max(_valid, key=lambda p: p["profile"].confidence)
    profile = primary["profile"]
    # Union suggested_components from every classified file
    all_suggested = set()
    for p in _valid:
        all_suggested.update(getattr(p["profile"], "suggested_components", []) or [])
    profile.suggested_components = sorted(all_suggested)
    # OR together needs_vm_extraction
    profile.needs_vm_extraction = any(
        p["profile"].needs_vm_extraction for p in _valid
    )
else:
    profile = profiles_per_file[0]["profile"] if profiles_per_file and profiles_per_file[0]["profile"] else None

if profile is None:
    st.stop()

# The Anthropic API key is consumed inside pillar render_inputs for VM
# extraction — stash it in session state for them.
st.session_state["_anthropic_key"] = anthropic_key

# For back-compat with existing pillar render_inputs that take a single
# (upload_bytes, upload_name). For lift-shift we pass the FIRST uploaded
# file — the pillar then loops over ALL uploads and concatenates its
# extracted items via the merge logic in infra_lift_shift.render_inputs.
# For non-VM pillars auto-simulate also iterates through `uploads` if present.
upload_bytes = uploads[0]["bytes"]
upload_name = uploads[0]["name"]
st.session_state["_all_uploads"] = uploads   # used by lift-shift merge loop

PILLAR_META = all_metadata()

# Per-file status summary
with st.expander(f"📎 Files processed ({len(uploads)})", expanded=True):
    for row in profiles_per_file:
        p = row["profile"]
        fname = row["file"]["name"]
        if p is None:
            st.markdown(f"- ❌ `{fname}` — classification failed")
        else:
            mapped = PILLAR_META.get(p.workload_type, {}).get("label", p.workload_type)
            st.markdown(
                f"- ✅ `{fname}` — **{mapped}** · confidence {p.confidence:.0%} · {p.summary or '(no summary)'}"
            )

col_c1, col_c2, col_c3 = st.columns([2, 1, 1])
_detected_label = (
    PILLAR_META[profile.workload_type]["label"]
    if profile.workload_type in PILLAR_META
    else profile.workload_type.replace("_", " ").title()
)
col_c1.markdown(f"**Primary detected pillar:** {_detected_label}")
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

# Auto-simulate toggle: ask Sonnet to read the upload and pre-fill pillar
# inputs with assumed values + follow-up questions. Skipped for lift-shift
# (which runs its own Sonnet extractor for VM specs).
auto_sim_default = any(
    not PILLAR_META[pk]["needs_vm_extraction"] for pk in active_pillars
)
auto_sim_enabled = st.checkbox(
    "🔮 Auto-simulate inputs from the document (Sonnet reads the doc, pre-fills "
    "each pillar's inputs, and lists its assumptions + follow-up questions)",
    value=auto_sim_default,
    help=(
        "Uses ~2-5K Sonnet tokens per pillar. Cached per (file, pillar, your "
        "answers) so toggling widgets won't re-hit the API. Skip for pure VM "
        "lift-and-shift — that pillar reads the file on its own."
    ),
)

# ---------------- Stage B: per-pillar inputs ----------------
st.subheader("3. Pillar inputs")

pillar_inputs: dict = {}
for pk in active_pillars:
    md = PILLAR_META[pk]
    with st.expander(f"{md['icon']} {md['label']} — {md['description']}", expanded=True):
        merged_prefs = dict(_prefs)

        if auto_sim_enabled and not md["needs_vm_extraction"] and anthropic_key:
            # Load previous answers the user may have typed for this pillar
            answers_key = f"_sim_answers::{pk}"
            prior_answers = st.session_state.get(answers_key, {})

            sim_cache_key = (
                f"_sim::{hash(upload_bytes)}::{upload_name}::{pk}::"
                f"{sorted((prior_answers or {}).items())}"
            )
            if sim_cache_key in st.session_state:
                sim = st.session_state[sim_cache_key]
            else:
                try:
                    with st.spinner(f"Auto-simulating inputs for {md['label']}…"):
                        sim = auto_simulate(
                            pillar=pk,
                            data=upload_bytes,
                            filename=upload_name,
                            api_key=anthropic_key,
                            prior_answers=prior_answers,
                        )
                    st.session_state[sim_cache_key] = sim
                except Exception as e:
                    st.warning(
                        f"Auto-simulate failed for {md['label']} "
                        f"({type(e).__name__}: {e}). Falling back to defaults."
                    )
                    with st.expander("Traceback", expanded=False):
                        st.code(traceback.format_exc())
                    sim = None

            if sim is not None:
                # Overlay Sonnet's suggestions onto the prefs passed to render_inputs
                merged_prefs.update(sim.suggested_inputs)
                merged_prefs["ai_openai_usage"] = sim.suggested_inputs.get("openai_usage", {})
                merged_prefs["ai_foundry_usage"] = sim.suggested_inputs.get("foundry_usage", {})

                cols_head = st.columns([3, 1])
                cols_head[0].markdown(f"**Auto-simulated from the document** (confidence {sim.confidence:.0%})")
                if cols_head[1].button("Re-run simulate", key=f"rerun_{pk}"):
                    st.session_state.pop(sim_cache_key, None)
                    st.rerun()

                if sim.assumptions:
                    with st.expander("🧮 Assumptions used", expanded=False):
                        for a in sim.assumptions:
                            st.markdown(f"- {a}")

                if sim.open_questions:
                    with st.expander("❓ Follow-up questions (answer to tighten the estimate)", expanded=True):
                        new_answers = dict(prior_answers)
                        for i, q in enumerate(sim.open_questions):
                            new_answers[q] = st.text_input(
                                q, value=prior_answers.get(q, ""),
                                key=f"sim_q_{pk}_{i}",
                            )
                        if st.button("Refine with answers", key=f"refine_{pk}"):
                            st.session_state[answers_key] = new_answers
                            # Invalidate the cached simulation so the next render re-runs with answers
                            for k in list(st.session_state):
                                if k.startswith(f"_sim::") and f"::{pk}::" in k:
                                    st.session_state.pop(k, None)
                            st.rerun()

        pillar_inputs[pk] = get_pillar(pk).render_inputs(
            st, merged_prefs, app_name, region, upload_bytes, upload_name, profile,
        )

# Cross-pillar wiring: share lift-shift vm_count + la_gb with azure_security.
if "infra_lift_shift" in pillar_inputs and "azure_security" in pillar_inputs:
    exports = pillar_inputs["infra_lift_shift"].get("__exports__", {})
    pillar_inputs["azure_security"].setdefault("vm_count", exports.get("vm_count", 0))
    pillar_inputs["azure_security"].setdefault("la_gb", exports.get("la_gb", 0.0))

# AHB injection: flow the Windows / SQL hybrid-benefit flags to every pillar
# that prices VM compute or Azure SQL DB. Lives under `__use_ahb__` /
# `__use_ahb_sql__` to avoid colliding with user-facing pillar fields.
for pk, pin in pillar_inputs.items():
    if not isinstance(pin, dict):
        continue
    pin["__use_ahb__"] = bool(use_ahb_windows)
    pin["__use_ahb_sql__"] = bool(use_ahb_sql)

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
        pair_strs = [
            f"• `{sku}` ({BILLING_TERMS.get(mode, {}).get('label', mode)})"
            for sku, mode in sorted(client.term_fallbacks)
        ]
        n = len(pair_strs)
        st.warning(
            f"**{n} SKU(s) have no Reserved Instance / Savings Plan meter in "
            f"`{region}`** — those lines were priced at Pay-as-you-go instead, "
            "and the BOM label for those lines does NOT show a SP/RI tag (so "
            "you can spot them). List:\n\n" + "\n".join(pair_strs) +
            "\n\n_Tip: open the sidebar's **🔍 Diagnose billing-term availability** "
            "tool to probe your exact SKU + region._"
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
            "use_ahb_windows": use_ahb_windows,
            "use_ahb_sql": use_ahb_sql,
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

    # Collect assessment-wide assumptions: missing-key-info defaults,
    # missing-DB-info stub, and any file-level merge notes.
    global_assumptions: list = []
    missing_inv = st.session_state.get("_missing_inventory_assumptions") or []
    global_assumptions.extend(missing_inv)
    if st.session_state.get("_db_info_missing"):
        global_assumptions.append(
            "DB info not detected in uploads — assumed no separate DB license "
            "required beyond what's bundled with the VM OS. Upload a DB "
            "editions + versions list to tighten."
        )
    _upl = st.session_state.get("_all_uploads") or []
    if len(_upl) > 1:
        global_assumptions.append(
            "Multi-file intake: "
            + " + ".join(f"`{u['name']}`" for u in _upl)
            + " merged into a single inventory (dedup by VM name)."
        )

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        excel_bytes = build_excel_bom(
            lines, mapping_rows, region, currency,
            app_name=saved_app, global_assumptions=global_assumptions,
        )
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
