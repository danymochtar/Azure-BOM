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

from src.constants import AZURE_REGIONS, CURRENCIES, DEFAULT_REGION, DEFAULT_CURRENCY, region_label
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
# Grouped into three expanders so the sidebar fits one screen.
with st.sidebar:
    st.header("Configuration")

    # ===== 🛠 Workload =====
    with st.expander("🛠 Workload", expanded=True):
        app_name = st.text_input(
            "Application / workload name",
            value=_prefs.get("app_name", ""),
            placeholder="e.g. ERPSuite, FraudAI",
            help="Tagged into 'Custom name' for every BOM line.",
        )
        _default_region = _prefs.get("region") or DEFAULT_REGION
        # Geo-grouped labels via format_func so the 49-entry list is scannable.
        # Source-of-truth value remains the ARM code (e.g. 'malaysiawest').
        region = st.selectbox(
            "Primary region",
            AZURE_REGIONS,
            index=AZURE_REGIONS.index(_default_region) if _default_region in AZURE_REGIONS else AZURE_REGIONS.index(DEFAULT_REGION),
            format_func=region_label,
        )
        _default_currency = _prefs.get("currency") or DEFAULT_CURRENCY
        currency = st.selectbox(
            "Currency",
            CURRENCIES,
            index=CURRENCIES.index(_default_currency) if _default_currency in CURRENCIES else CURRENCIES.index(DEFAULT_CURRENCY),
        )

    # ===== 💰 Pricing =====
    with st.expander("💰 Pricing", expanded=True):
        _bt_keys = list(BILLING_TERMS.keys())
        _saved_bt = _prefs.get("pricing_mode", "payg")
        _bt_idx = _bt_keys.index(_saved_bt) if _saved_bt in _bt_keys else 0
        pricing_mode = st.radio(
            "Billing term",
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
            "**Cost-optimisation guide:** \n"
            "• Steady 24/7 → RI 3Y (50-60% off)  \n"
            "• Uncertain → SP 1Y / RI 1Y (30-40% off)  \n"
            "• Spiky / experimental → PAYG"
        )

        st.markdown("**Azure Hybrid Benefit (BYOL + SA)**")
        use_ahb_windows = st.checkbox(
            "Windows Server AHB",
            value=bool(_prefs.get("use_ahb_windows", False)),
            help=(
                "Applies to all Windows VMs + AKS Windows nodes + GPU VMs "
                "running Windows. Swaps the Windows-VM price for the Linux-VM "
                "price of the same SKU (~30-40% off a Windows VM). Requires "
                "existing Windows Server licenses with active SA."
            ),
        )
        use_ahb_sql = st.checkbox(
            "SQL Server AHB (Azure SQL DB)",
            value=bool(_prefs.get("use_ahb_sql", False)),
            help=(
                "Tier-based discount on Azure SQL DB compute: ~55% GP, "
                "~33% BC, ~25% Hyperscale. Requires SQL Server licenses + SA."
            ),
        )

        # Pricing diagnostic lives here — it's a pricing-side tool, not config.
        with st.expander("🔍 Diagnose billing-term availability", expanded=False):
            st.caption(
                "Check whether the Azure Retail Prices API actually returns "
                "SP / RI meters for a given VM SKU in your region. If any row "
                "shows '(not found)', the app silently falls back to PAYG."
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

    # ===== ⚙ Advanced =====
    with st.expander("⚙ Advanced", expanded=False):
        st.markdown("**AI key**")
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
            "**Data egress:** when AI features are enabled, a preview of "
            "your uploaded file (sheet names, headers, sample rows — or full "
            "content for files ≤500 rows) is sent to Anthropic for "
            "classification and extraction. Remove sensitive data (passwords, "
            "PII) before upload. Anthropic does not train on API inputs."
        )

        st.markdown("**📥 Pre-assessment templates**")
        st.caption(
            "Optional Excel templates, one per pillar. The infra template is "
            "not strict — any doc format (RVTools, Azure Migrate, PDF, etc.) "
            "still works."
        )
        from src.output.templates import TEMPLATE_REGISTRY
        _tpl_labels = [t[0] for t in TEMPLATE_REGISTRY]
        _tpl_choice = st.selectbox(
            "Template", _tpl_labels, key="tpl_choice",
            label_visibility="collapsed",
        )
        _tpl_match = next(
            ((label, fname, builder) for (label, fname, builder)
             in TEMPLATE_REGISTRY if label == _tpl_choice),
            None,
        )
        if _tpl_match:
            _lbl, _fname, _builder = _tpl_match
            try:
                _blob = _builder()
                st.download_button(
                    label=f"⬇ Download `{_fname}`",
                    data=_blob,
                    file_name=_fname,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"tpl_dl_{_fname}",
                    use_container_width=True,
                )
            except Exception as te:
                st.warning(f"Could not build {_lbl}: {te}")

        st.markdown("**Browser storage**")
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

# Two input modes, side-by-side: file upload OR paste content directly.
# Both feed the same downstream `uploads` list — classify each piece,
# extract per pillar, merge into one assessment.
tab_upload, tab_paste = st.tabs(["📁 Upload files", "📝 Paste content"])

with tab_upload:
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
            "separate docs — the app merges them into one assessment. "
            "Tip: screenshots can be dragged directly from the OS screenshot "
            "tool into this uploader."
        ),
    ) or []

with tab_paste:
    st.caption(
        "Paste text directly — useful for email bodies, meeting transcripts, "
        "Slack/Teams chat summaries, or freeform requirements that aren't in "
        "a file. The assessment treats this as an additional document."
    )
    pasted_text = st.text_area(
        "Paste content here",
        height=240,
        placeholder=(
            "Examples:\n"
            "• Email thread: \"From: customer@…\\nSubject: Azure migration\\n"
            "We have 50 Linux VMs running PostgreSQL…\"\n"
            "• Meeting notes: \"2025-11-12 — sizing call: target is 1,000 "
            "concurrent users for the new RAG chatbot, ~5K queries/day…\"\n"
            "• Requirements: \"Need Defender for Cloud P2 on all servers, "
            "Sentinel ingestion 30 GB/day, 90-day retention…\""
        ),
        key="pasted_text_input",
        label_visibility="collapsed",
    )
    paste_label = st.text_input(
        "Optional label (defaults to `pasted-content.txt`)",
        value="",
        placeholder="e.g. customer-email.txt, meeting-2025-11-12.md",
        key="pasted_text_label",
    )

    # Clipboard image paste — third-party component talks to the browser's
    # navigator.clipboard.read() API. User clicks the button, grants
    # permission once per origin, and the image lands here as a PIL Image.
    st.markdown("**Or paste a screenshot from the clipboard**")
    pasted_image = None
    try:
        from streamlit_paste_button import paste_image_button
        _paste_result = paste_image_button(
            label="📋 Paste image from clipboard",
            key="paste_img_btn",
            errors="ignore",
        )
        if _paste_result and _paste_result.image_data is not None:
            pasted_image = _paste_result.image_data
    except ImportError:
        st.caption(
            "⚠ `streamlit-paste-button` not installed — add it to "
            "`requirements.txt` to enable clipboard image paste. "
            "Meanwhile, use the **📁 Upload files** tab and drag the "
            "screenshot from your OS screenshot tool."
        )

# Merge file + pasted content into one stream. Pasted text becomes a
# synthetic upload so it flows through classification + extraction the
# same way as any other text/markdown file.
uploads: list = []
for f in uploaded_files:
    fb = f.read()
    uploads.append({"name": f.name, "bytes": fb})

if (pasted_text or "").strip():
    _name = (paste_label or "").strip() or "pasted-content.txt"
    # If the user typed a name without an extension, default to .txt so the
    # parser treats it as plain text/markdown.
    if "." not in _name.rsplit("/", 1)[-1]:
        _name += ".txt"
    uploads.append({"name": _name, "bytes": pasted_text.encode("utf-8")})

# Pasted image (clipboard → PIL Image via streamlit-paste-button) → PNG
# bytes → synthetic upload. The classifier + extractor already handle
# image content natively (Claude reads images directly).
if pasted_image is not None:
    import io as _io
    import time as _time
    _buf = _io.BytesIO()
    pasted_image.save(_buf, format="PNG")
    uploads.append({
        "name": f"pasted-screenshot-{int(_time.time())}.png",
        "bytes": _buf.getvalue(),
    })

if not uploads:
    st.info(
        "Upload a file OR paste content above to get started. Supports VM "
        "inventories (RVTools, Azure Migrate, infra lists), SIEM/SOC design "
        "docs, AI use-cases, data-platform specs, mixed architecture "
        "documents, or freeform paste (email, meeting transcript, "
        "requirements). Multi-source input is supported — combine an "
        "inventory file with a pasted email to fill gaps."
    )
    st.stop()

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

# Per-file status summary — only worth expanding by default when there's
# more than one upload (single-file = a one-line summary; collapsing
# reclaims vertical space for the pillar inputs below).
with st.expander(f"📎 Files processed ({len(uploads)})", expanded=len(uploads) > 1):
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

_detected_label = (
    PILLAR_META[profile.workload_type]["label"]
    if profile.workload_type in PILLAR_META
    else profile.workload_type.replace("_", " ").title()
)
st.markdown(
    f"**Primary pillar:** {_detected_label}  "
    f"· **Confidence** {profile.confidence:.0%}  "
    f"· **Complexity** {profile.complexity}"
)
if profile.summary:
    st.caption(profile.summary)
if profile.signals:
    with st.expander("Why this classification? (signals)", expanded=False):
        for s in profile.signals:
            st.markdown(f"- {s}")

# Pre-tick rules:
# - single detected pillar  → tick that pillar
# - mixed                   → derive ticks from the classifier's
#                             `suggested_components` (mapped to pillars)
#                             instead of blindly ticking all 7
# - unknown / other         → tick whatever the user last had, else empty
_COMPONENT_TO_PILLAR = {
    # lift-shift / LZ
    "vm_sizing": "infra_lift_shift", "managed_disks": "infra_lift_shift",
    "public_ip": "infra_lift_shift", "firewall": "infra_lift_shift",
    "bastion": "infra_lift_shift", "vpn_gw": "infra_lift_shift",
    "expressroute_circuit": "infra_lift_shift",
    "expressroute_gateway": "infra_lift_shift",
    "app_gateway_waf": "infra_lift_shift",
    "bandwidth_egress": "infra_lift_shift",
    "log_analytics": "infra_lift_shift", "key_vault": "infra_lift_shift",
    "recovery_vault": "infra_lift_shift",
    "ha": "infra_lift_shift", "bcdr": "infra_lift_shift",
    "nat_gateway": "infra_lift_shift",
    # modernization
    "app_service": "infra_modernization", "aks": "infra_modernization",
    "container_apps": "infra_modernization", "api_management": "infra_modernization",
    "front_door": "infra_modernization", "acr": "infra_modernization",
    "service_bus": "infra_modernization",
    "github_enterprise": "infra_modernization",
    "github_advanced_security": "infra_modernization",
    "github_copilot_business": "infra_modernization",
    "github_copilot_enterprise": "infra_modernization",
    "visual_studio_pro": "infra_modernization",
    "visual_studio_enterprise": "infra_modernization",
    "azdo_basic": "infra_modernization", "azdo_basic_test": "infra_modernization",
    "azdo_hosted_pipeline": "infra_modernization",
    "azdo_selfhosted_pipeline": "infra_modernization",
    # data
    "fabric": "data_platform", "synapse": "data_platform",
    "cosmos_db": "data_platform", "azure_sql_db": "data_platform",
    "sql_mi": "data_platform", "adls_gen2": "data_platform",
    "adf": "data_platform", "event_hubs": "data_platform",
    "databricks": "data_platform", "power_bi": "data_platform",
    "postgres_flexible": "data_platform", "mysql_flexible": "data_platform",
    "redis_cache": "data_platform", "azure_files": "data_platform",
    # AI
    "azure_openai": "ai_application", "ai_search": "ai_application",
    "ml_workspace": "ai_application", "gpu_vm": "ai_application",
    "cognitive_services": "ai_application", "fine_tuning": "ai_application",
    # security
    "defender_cspm": "azure_security", "defender_servers_p2": "azure_security",
    "sentinel": "azure_security", "waf": "azure_security",
    "private_link": "azure_security", "purview": "azure_security",
    "pim": "azure_security",
    "ddos_ip_protection": "azure_security",
    "ddos_network_protection": "azure_security",
    # hybrid
    "azure_arc": "hybrid_multicloud", "defender_multicloud": "hybrid_multicloud",
    "arc_sql_payg": "hybrid_multicloud",
    "arc_winserver_payg": "hybrid_multicloud",
    "arc_k8s": "hybrid_multicloud", "arc_la_ingestion": "hybrid_multicloud",
    # M365 & others
    "m365_backup": "m365_and_others", "m365_archive": "m365_and_others",
    "sharepoint_premium": "m365_and_others",
    "copilot_studio_pack_25k": "m365_and_others",
    "copilot_studio_payg": "m365_and_others",
    "other_marketplace": "m365_and_others",
}

_saved_active = _prefs.get("active_pillars") or []
if profile.workload_type == "mixed":
    derived = {
        _COMPONENT_TO_PILLAR[c]
        for c in (profile.suggested_components or [])
        if c in _COMPONENT_TO_PILLAR
    }
    _pre_ticked = [p for p in PILLAR_ORDER if p in derived] or list(PILLAR_ORDER)
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
    "🔮 Auto-simulate inputs from the document",
    value=auto_sim_default,
    help=(
        "Sonnet reads the doc, pre-fills each pillar's inputs, and lists its "
        "assumptions + follow-up questions. Uses ~2-5K Sonnet tokens per "
        "pillar. Cached per (file, pillar, your answers) so toggling widgets "
        "won't re-hit the API. Skip for pure VM lift-and-shift — that pillar "
        "reads the file on its own."
    ),
)

# ---------------- Stage B: per-pillar inputs ----------------
st.subheader("3. Pillar inputs")

pillar_inputs: dict = {}
# Default expansion rule: only the primary detected pillar is open. If the
# upload classified as `mixed` / `unknown`, fall back to the first active
# pillar in the multi-select. Everything else is collapsed; users click to
# review. After Generate, the per-pillar subtotal is appended to the label.
_primary_pk = profile.workload_type if profile.workload_type in PILLAR_META else (
    active_pillars[0] if active_pillars else None
)
_pillar_totals = st.session_state.get("_pillar_totals", {})

for pk in active_pillars:
    md = PILLAR_META[pk]
    _total = _pillar_totals.get(pk)
    if _total is not None:
        _label = f"{md['icon']} {md['label']} — **${_total:,.2f}/mo**"
    else:
        _label = f"{md['icon']} {md['label']}"
    with st.expander(_label, expanded=(pk == _primary_pk)):
        # Description as inline caption — keeps the expander header tight.
        st.caption(md["description"])
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
                # Throttle multi-pillar auto-simulate so we don't burst-
                # fire the 30K input-tokens-per-minute Tier-1 rate limit
                # when the user has 3+ pillars active. Sleep 2s between
                # back-to-back live API calls (cache hits skip the sleep).
                import time as _time
                _last_sim_call = st.session_state.get("_last_sim_call_ts", 0)
                _elapsed = _time.monotonic() - _last_sim_call
                if _last_sim_call and _elapsed < 2.0:
                    _time.sleep(2.0 - _elapsed)

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
                    st.session_state["_last_sim_call_ts"] = _time.monotonic()
                except Exception as e:
                    # Friendlier message for the two most common failures.
                    err_kind = type(e).__name__
                    if err_kind == "RateLimitError":
                        st.warning(
                            f"⏱ Auto-simulate for **{md['label']}** hit the "
                            "Anthropic input-tokens-per-minute rate limit even "
                            "after a 60s retry. Falling back to defaults — try "
                            "re-running with fewer pillars ticked, or upgrade "
                            "the API tier at console.anthropic.com/settings/limits."
                        )
                    elif err_kind == "BadRequestError" and "prompt is too long" in str(e):
                        st.warning(
                            f"📄 Auto-simulate for **{md['label']}** couldn't fit "
                            "the upload into the model's context window. Falling "
                            "back to defaults — try uploading a smaller summary "
                            "doc instead of the full inventory."
                        )
                    else:
                        st.warning(
                            f"Auto-simulate failed for {md['label']} "
                            f"({err_kind}: {e}). Falling back to defaults."
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

# Auto-fire Generate on the first render of a fresh (upload × pillars × auto-sim)
# combo so non-lift-shift pillars produce a "v1 BOM" immediately from the
# doc-derived assumptions — user fine-tunes via the widgets above and clicks
# Re-generate to refresh. Without this, those pillars look empty until the
# user discovers the Generate button.
_auto_gen_key = (
    f"_auto_gen::{hash(tuple(u['bytes'] for u in uploads))}::"
    f"{','.join(sorted(active_pillars))}"
)
_should_auto_generate = (
    auto_sim_enabled
    and bool(anthropic_key)
    and _auto_gen_key not in st.session_state
)
if _should_auto_generate:
    st.session_state[_auto_gen_key] = True
    st.info(
        "✨ Auto-generating a first-pass BOM from doc-derived assumptions. "
        "Adjust the inputs above and click **🔁 Re-generate** to refresh."
    )

_already_have_bom = "bom_lines" in st.session_state
_btn_label = "🔁 Re-generate BOM" if _already_have_bom else "📊 Generate BOM"
_btn_clicked = st.button(_btn_label, type="primary")

if _btn_clicked or _should_auto_generate:
    with st.spinner("Pricing via Azure Retail Prices API…"):
        client = RetailPricesClient(currency=currency)
        all_lines: list = []
        mapping_rows: list = []
        pillar_totals: dict = {}
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
            pillar_totals[pk] = round(sum(l.monthly_cost for l in pl_lines), 2)

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
        st.session_state["_pillar_totals"] = pillar_totals

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
            "lz_preset": ls_in.get("lz_preset", "Standard"),
            "sec_enabled": sec_in.get("enabled", []),
            "backup_pct": ls_in.get("backup_pct", 40),
            "la_mb_per_vm_per_day": ls_in.get("la_mb_per_vm_per_day", 200),
            "bandwidth_gb": ls_in.get("bandwidth_gb", 200),
            "headroom": ls_in.get("headroom", 1.0),
            "disk_tier": ls_in.get("disk_tier", "Premium SSD"),
            "os_mode": ls_in.get("os_mode", "as-detected"),
            "security_target_scope": sec_in.get("target_scope", 0),
            "private_endpoint_count": ls_in.get("private_endpoint_count", 0),
            "vnet_peering_gb": ls_in.get("vnet_peering_gb", 0),
            "nat_gateway_gb": ls_in.get("nat_gateway_gb", 0),
            "automation_minutes": ls_in.get("automation_minutes", 0),
            "app_insights_gb": ls_in.get("app_insights_gb", 10.0),
            "flow_logs_gb": ls_in.get("flow_logs_gb", 10.0),
            "waf_capacity_units": ls_in.get("waf_capacity_units", 2),
            "firewall_gb_processed": ls_in.get("firewall_gb_processed", 0),
        })
        # Persist the full result so a browser refresh can restore it
        storage.save_last_bom({
            "lines": [l.to_row() for l in all_lines],
            "mapping_rows": mapping_rows,
            "region": region,
            "currency": currency,
            "app_name": app_name,
            "pillar_totals": pillar_totals,
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
            st.session_state["_pillar_totals"] = _last.get("pillar_totals", {}) or {}
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

    # ----- Top-of-results summary: metrics + download CTAs -----
    # Surfaces totals + Excel / JSON downloads immediately under the
    # heading so reviewers don't have to scroll past the chart + dataframe
    # to find them.
    m1, m2, m3 = st.columns(3)
    m1.metric("Line items", len(df))
    m2.metric(f"Monthly ({currency})", f"{total_monthly:,.2f}")
    m3.metric(f"Annual ({currency})", f"{total_monthly * 12:,.2f}")

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

    excel_bytes = build_excel_bom(
        lines, mapping_rows, region, currency,
        app_name=saved_app, global_assumptions=global_assumptions,
    )
    pc_json = build_pricing_calculator_import(lines, region, currency)
    _fname = (saved_app or "assessment").replace(" ", "-")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.download_button(
            "📥 Download Excel (Pricing Calculator template)",
            excel_bytes,
            file_name=f"azure-{_fname}-{region}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
    with col_d2:
        st.download_button(
            "📥 Download Pricing Calculator import (JSON)",
            pc_json,
            file_name=f"azure-{_fname}-{region}.json",
            mime="application/json",
            use_container_width=True,
        )

    if not df.empty:
        by_cat = (
            df.groupby("category", as_index=False)["monthly_cost"].sum()
            .sort_values("monthly_cost", ascending=False)
        )
        st.bar_chart(by_cat, x="category", y="monthly_cost")

    with st.expander("All line items", expanded=True):
        show_ids = st.checkbox(
            "Show retail meter IDs (product/sku/meter)",
            value=False, key="show_meter_ids",
            help="Off by default — these are useful for auditing against the Retail Prices API.",
        )
        _hide_cols = (
            [] if show_ids
            else ["product_id", "sku_id", "meter_id", "source"]
        )
        _display_df = df.drop(columns=[c for c in _hide_cols if c in df.columns])
        # Surface `resource_count` right after `resource` and rename it
        # to "#" so reviewers see the instance count next to the SKU
        # instead of having to parse "x4" out of the resource string.
        if "resource_count" in _display_df.columns:
            cols = list(_display_df.columns)
            cols.remove("resource_count")
            insert_at = cols.index("resource") + 1 if "resource" in cols else 0
            cols.insert(insert_at, "resource_count")
            _display_df = _display_df[cols].rename(columns={"resource_count": "#"})
        st.dataframe(_display_df, use_container_width=True, hide_index=True)

    # Token spend breakdown — per-process Claude API usage + USD cost
    usage_list = usage_tracker.get_usage()
    if usage_list:
        st.subheader("6. AI token spend (this session)")
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
