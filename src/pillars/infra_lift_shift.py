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
from ..constants import AZURE_REGIONS, paired_region, region_label
from ..landing_zone import (
    LANDING_ZONE_COMPONENTS,
    LZ_PRESETS,
    LZ_CATEGORY_ORDER,
    build_landing_zone_bom,
)
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
        # Sizing policy: default is 1:1 exact match; if the source spec
        # doesn't line up with a catalog SKU the mapper falls back to the
        # smallest / cheapest SKU whose capacity still covers the workload.
        # Users can opt-in to a safety margin for noisy on-prem sizing data.
        headroom_mode = st.radio(
            "Sizing policy",
            ["1:1 (exact match, else nearest cost-optimized)", "Add safety margin"],
            index=0 if float(prefs.get("headroom", 1.0)) <= 1.001 else 1,
            horizontal=False,
            help=(
                "1:1 matches the source spec to an Azure SKU with the same "
                "vCPU + memory when possible (e.g. 4 vCPU / 16 GB → D4s v5). "
                "If no exact match exists, the mapper picks the SMALLEST "
                "SKU whose capacity still covers the spec (cheapest fit). "
                "Pick 'Add safety margin' only when the source inventory "
                "under-reports utilisation."
            ),
        )
        if headroom_mode.startswith("1:1"):
            headroom = 1.0
            st.caption("Policy: 1:1 exact match → else nearest cost-optimized SKU.")
        else:
            headroom = st.slider(
                "Safety margin (multiplier applied to vCPU + memory)",
                1.05, 2.0,
                max(1.05, float(prefs.get("headroom", 1.3))),
                step=0.05,
                help="1.1 = +10% padding; 1.3 = +30%; etc.",
            )
        disk_opts = ["Premium SSD", "Standard SSD", "Standard HDD"]
        # Default flipped from Premium SSD → Standard SSD to match
        # Microsoft Learn's 2025 baseline ("Standard SSD is the
        # recommended starting tier for most VM workloads"). DB / OLTP
        # VMs auto-promote to Premium SSD; backup / archive / log
        # VMs auto-demote to Standard HDD via `recommend_disk_tier`.
        saved_disk = prefs.get("disk_tier", "Standard SSD")
        disk_tier = st.selectbox(
            "Default disk tier (fallback when name doesn't hint at workload)",
            disk_opts,
            index=disk_opts.index(saved_disk) if saved_disk in disk_opts else 1,
            help=(
                "Microsoft Learn disk-tier guidance:\n\n"
                "  • **Premium SSD** — production DB, OLTP, low-latency "
                "apps (SQL/Oracle/Postgres/MongoDB), SAP HANA. Auto-"
                "picked for VMs whose name contains DB tokens.\n"
                "  • **Standard SSD** — recommended baseline for web "
                "/ app servers, batch, dev / test with reliability "
                "needs. Default when no workload hint is detectable.\n"
                "  • **Standard HDD** — backup, archive, file shares "
                "with infrequent access, log retention. Auto-picked "
                "for VMs whose name contains backup/archive/file/log "
                "tokens.\n\n"
                "Per-VM auto-routing can be disabled below if you want "
                "every disk on the same tier."
            ),
        )
        auto_disk_tier = st.checkbox(
            "🤖 Auto-route disk tier per VM (DB→Premium, archive→HDD)",
            value=bool(prefs.get("auto_disk_tier", True)),
            help=(
                "When on, VMs are routed to a tier based on naming "
                "hints — DB / OLTP names go to Premium SSD; backup / "
                "archive / file / log names go to Standard HDD; "
                "everything else uses the default tier above. Turn off "
                "to force every disk onto the default tier (legacy "
                "behaviour)."
            ),
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
        # Compute-mode (Saving / Normal / High-Performance) is now a
        # global sidebar setting in 💰 Pricing — it biases the VM family
        # preference across all pillars. The lift-shift sizer reads it
        # from session_state below. See `constants.COMPUTE_MODES`.

    # Secondary region (only when HA or BCDR). Default to the Azure-
    # recommended paired region for the primary so the user doesn't have
    # to look up the official DR pair manually. Falls back to "(none)"
    # if Microsoft hasn't published a pair for the primary region.
    secondary_region: Optional[str] = None
    if include_ha or include_bcdr:
        suggested = paired_region(region)
        secondary_options = ["(none — single region)"] + [r for r in AZURE_REGIONS if r != region]
        _default_idx = 0
        if suggested and suggested in secondary_options:
            _default_idx = secondary_options.index(suggested)
            st.caption(
                f"💡 Azure-recommended DR pair for `{region}` → "
                f"**{region_label(suggested)}** "
                f"(auto-selected; you can override below)."
            )
        else:
            st.caption(
                f"⚠ Microsoft has no published BCDR pair for `{region}`. "
                "Pick a same-geography region manually for cross-region replication."
            )
        sec_pick = st.selectbox(
            "Secondary region (DR / failover)",
            secondary_options,
            index=_default_idx,
            format_func=lambda r: r if r.startswith("(none") else region_label(r),
        )
        if not sec_pick.startswith("(none"):
            secondary_region = sec_pick

    # ----- Extraction (cached) -----
    # Runs the AI extractor across EVERY uploaded file and concatenates the
    # extracted inventory items (dedup by name). Merges pillar-facing notes
    # into a single per-file status list shown to the user.
    api_key = st.session_state.get("_anthropic_key", "")
    items: List = []
    mode = "failed"
    spec = None

    all_uploads = st.session_state.get("_all_uploads") or [
        {"name": upload_name, "bytes": upload_bytes}
    ]

    per_file_status: List[str] = []
    dedup_names: set = set()

    if not api_key:
        st.warning("Anthropic API key required for VM extraction. Paste one in the sidebar.")
    else:
        for u in all_uploads:
            ub, un = u["bytes"], u["name"]
            ext_key = (
                f"ext::{hash(ub)}::{un}::{strategy_key}::"
                f"{include_ha}::{include_off}"
            )
            if ext_key in st.session_state:
                f_items, f_mode, f_spec = st.session_state[ext_key]
                per_file_status.append(
                    f"✅ `{un}` — cached · {len(f_items)} VM(s) · mode `{f_mode}`"
                )
            else:
                try:
                    with st.spinner(f"Extracting inventory from `{un}` (Sonnet)…"):
                        f_items, f_mode, f_spec = ai_parse_inventory(
                            ub, un, api_key,
                            include_powered_off=include_off,
                            strategy_hint=strategy_guidance(strategy_key, ha_enabled=include_ha),
                        )
                    st.session_state[ext_key] = (f_items, f_mode, f_spec)
                    per_file_status.append(
                        f"✅ `{un}` — extracted {len(f_items)} VM(s) · mode `{f_mode}`"
                    )
                except Exception as e:
                    per_file_status.append(
                        f"⚠️ `{un}` — AI extraction failed ({type(e).__name__}: {e})"
                    )
                    f_items, f_mode, f_spec = [], "failed", None
                    if kind_from_name(un) == "spreadsheet":
                        try:
                            f_items, _ = parse_inventory(
                                ub, un, include_powered_off=include_off,
                            )
                            f_mode = "heuristic"
                            per_file_status.append(
                                f"↪ `{un}` — heuristic parser recovered {len(f_items)} item(s)"
                            )
                        except Exception as e2:
                            per_file_status.append(
                                f"❌ `{un}` — heuristic parser failed ({e2})"
                            )

            # Dedup: concatenate items by name, skipping repeats so two uploads
            # of the same inventory don't double-count.
            for it in f_items:
                nm = (it.name or "").strip().lower()
                if nm and nm in dedup_names:
                    continue
                if nm:
                    dedup_names.add(nm)
                items.append(it)

            # Remember the FIRST successful mode/spec for the summary caption
            if mode == "failed" and f_mode != "failed":
                mode, spec = f_mode, f_spec

        if len(all_uploads) > 1 or any("⚠️" in s or "❌" in s for s in per_file_status):
            with st.expander(f"Per-file extraction status ({len(all_uploads)} file(s))", expanded=True):
                for s in per_file_status:
                    st.markdown(f"- {s}")

        if items:
            st.success(f"Merged inventory: {len(items)} unique VM(s) across {len(all_uploads)} file(s) · primary mode `{mode}`.")
            if mode == "direct" and getattr(spec, "summary", None):
                st.caption(spec.summary)

    # ----- Validate key-info on the merged inventory -----
    # Surfaces missing vcpu/memory/storage/os per VM and offers two actions:
    # (1) upload another doc, (2) accept baseline assumptions. Accepted
    # assumptions are stashed in session_state so the exporter can stamp
    # them onto the Cost Assumptions sheet.
    from ..parsers import validate_inventory, apply_assumptions_for_missing
    val = validate_inventory(items) if items else None
    st.session_state["_validation_result"] = val
    missing_assumptions = st.session_state.get("_missing_inventory_assumptions", [])

    if val and not val.ok:
        with st.container():
            st.warning(
                f"**{len(val.missing_by_vm)} VM(s) missing required fields** — "
                f"the cost engine needs vCPU, memory, storage, and OS per VM. "
                f"By field: "
                + ", ".join(f"{k}={v}" for k, v in val.by_field.items() if v > 0)
                + "."
            )
            with st.expander("Which VMs are missing what?", expanded=True):
                for vm, fields in val.missing_by_vm.items():
                    st.markdown(f"- `{vm}` → missing `{', '.join(fields)}`")
            ca1, ca2 = st.columns(2)
            ca1.info(
                "Option A — upload another doc containing the missing fields "
                "(add it to the uploader at the top of the page). The app will "
                "merge the new data automatically."
            )
            if ca2.button(
                "Option B — continue with documented baseline assumptions",
                key="ls_accept_baseline",
                help=(
                    "Fills missing fields with the Azure lowest-cost defaults "
                    "(2 vCPU, 4 GB RAM, 64 GB disk, Linux OS) and stamps each "
                    "substitution in the Cost Assumptions sheet so reviewers "
                    "know which lines have guessed inputs."
                ),
            ):
                new_assumptions = apply_assumptions_for_missing(items, val)
                st.session_state["_missing_inventory_assumptions"] = new_assumptions
                st.rerun()
    elif missing_assumptions:
        with st.expander(
            f"✅ Baseline assumptions applied to {len(missing_assumptions)} VM(s)",
            expanded=False,
        ):
            for a in missing_assumptions:
                st.caption(f"• {a}")

    if val and not val.has_db_info and items:
        st.info(
            "**DB info not detected.** Continuing with the assumption "
            "**'no separate DB license required beyond what's in the VM OS "
            "bundle'** — stamped in the Cost Assumptions sheet. Upload a DB "
            "list (SQL Server / Postgres / MySQL editions + versions) to "
            "tighten the estimate."
        )
        st.session_state["_db_info_missing"] = True
    else:
        st.session_state["_db_info_missing"] = False

    if items:
        import pandas as pd
        inv_df = pd.DataFrame([{
            "Name": i.name, "vCPU": i.vcpu, "Memory (GB)": i.memory_gb,
            "Storage (GB)": i.storage_gb, "OS": i.os, "Power": i.powerstate,
            "Notes": i.notes,
        } for i in items])
        st.dataframe(inv_df, use_container_width=True, hide_index=True)

    # ----- LZ component checkboxes (CAF-grouped + preset) -----
    lz_selected: List[str] = []
    backup_pct = int(prefs.get("backup_pct", 40))
    la_mb_per_vm_per_day = int(prefs.get("la_mb_per_vm_per_day", 200))
    bandwidth_gb = int(prefs.get("bandwidth_gb", 200))
    waf_capacity_units = int(prefs.get("waf_capacity_units", 2))
    firewall_gb = int(prefs.get("firewall_gb_processed", 0))
    private_endpoint_count = int(prefs.get("private_endpoint_count", 0))
    vnet_peering_gb = int(prefs.get("vnet_peering_gb", 0))
    nat_gateway_gb = int(prefs.get("nat_gateway_gb", 0))
    automation_minutes = int(prefs.get("automation_minutes", 0))
    app_insights_gb = float(prefs.get("app_insights_gb", 10.0))
    flow_logs_gb = float(prefs.get("flow_logs_gb", 10.0))
    if include_lz:
        with st.expander("Landing zone components (tick what to include)", expanded=True):
            st.caption(
                "Organised by CAF platform domain (Connectivity / Identity / "
                "Management / Security / Shared Platform). Pick a preset to "
                "pre-tick a canonical set, then refine individual boxes."
            )

            # ---- Preset selector ----
            _preset_keys = list(LZ_PRESETS.keys())
            saved_preset = prefs.get("lz_preset", "Standard")
            if saved_preset not in _preset_keys:
                saved_preset = "Standard"
            preset_choice = st.radio(
                "CAF preset",
                _preset_keys,
                index=_preset_keys.index(saved_preset),
                horizontal=True,
                key="ls_lz_preset",
                help=(
                    "**Foundation** — minimum viable hub (firewall, bastion, "
                    "public IP, log analytics, key vault, backup, egress). "
                    "**Standard** — CAF default; adds edge (Front Door, Private "
                    "Endpoints, DDoS IP), observability (App Insights, "
                    "Automation, Flow Logs, Site Recovery), ACR Standard. "
                    "**Enterprise** — full platform subs (Premium tiers, "
                    "ExpressRoute, Entra Domain Services, geo-ACR, WAF, VPN)."
                ),
            )

            # Detect a preset switch and re-seed the checkbox session state.
            # `_lz_last_preset` persists across reruns so we only seed when
            # the user actually changes the radio (otherwise manual toggles
            # would be lost every rerun).
            last_preset = st.session_state.get("_lz_last_preset")
            preset_changed = last_preset != preset_choice
            st.session_state["_lz_last_preset"] = preset_choice

            saved_lz = set(prefs.get("lz_selected", []))
            preset_set = LZ_PRESETS.get(preset_choice, set())

            def _initial_state(comp_key: str) -> bool:
                if preset_changed or last_preset is None:
                    # Preset drives initial tick state when user switches
                    # presets, OR on the very first render when no prior
                    # state exists.
                    return comp_key in preset_set if preset_choice != "None" else (comp_key in saved_lz)
                # No preset change → respect user's manual session state
                # if present; else fall back to the saved preferences.
                return st.session_state.get(f"ls_lz_{comp_key}", comp_key in saved_lz)

            # ---- Category-grouped checkbox grid ----
            # Skip `derived` components (e.g. App Gateway CU-hours, Firewall
            # data-processed GB, NAT data-processed GB) — they bill alongside
            # their parent and shouldn't appear as separate checkboxes. The
            # bill builder auto-adds them based on quantity inputs.
            comps_by_cat: dict = {}
            for comp in LANDING_ZONE_COMPONENTS:
                if comp.derived:
                    continue
                comps_by_cat.setdefault(comp.category, []).append(comp)

            for cat in LZ_CATEGORY_ORDER:
                cat_comps = comps_by_cat.get(cat, [])
                if not cat_comps:
                    continue
                st.markdown(f"**{cat}**")
                cols = st.columns(2)
                for i, comp in enumerate(cat_comps):
                    widget_key = f"ls_lz_{comp.key}"
                    if preset_changed:
                        st.session_state[widget_key] = (comp.key in preset_set)
                    on = cols[i % 2].checkbox(
                        comp.resource,
                        value=_initial_state(comp.key),
                        key=widget_key,
                        help=comp.notes or None,
                    )
                    if on:
                        lz_selected.append(comp.key)

            # ---- VPN Gateway SKU picker ----
            # The single `vpn_gw` checkbox covers Generation-2 SKUs
            # (VpnGw1 .. VpnGw5 + AZ variants) — let the user pick
            # which tier. Preset drives the default: Basic → VpnGw1,
            # Foundation → VpnGw1AZ, Standard → VpnGw2AZ, Enterprise
            # → VpnGw3AZ. Throughput / S2S / P2S limits per SKU are
            # documented in `lz_static.VPN_GATEWAY_SKUS`.
            vpn_gw_sku = None
            if "vpn_gw" in lz_selected:
                from ..pricing.lz_static import VPN_GATEWAY_SKUS, recommend_vpn_sku
                _vpn_keys = list(VPN_GATEWAY_SKUS.keys())
                _vpn_default = recommend_vpn_sku(preset_choice)
                _saved_vpn = prefs.get("vpn_gw_sku") or _vpn_default
                _vpn_idx = _vpn_keys.index(_saved_vpn) if _saved_vpn in _vpn_keys else _vpn_keys.index(_vpn_default)
                vpn_gw_sku = st.selectbox(
                    f"VPN Gateway SKU (preset `{preset_choice}` recommends `{_vpn_default}`)",
                    _vpn_keys,
                    index=_vpn_idx,
                    key="ls_vpn_gw_sku",
                    format_func=lambda k: (
                        f"{k} — {VPN_GATEWAY_SKUS[k]['throughput']} | "
                        f"{VPN_GATEWAY_SKUS[k]['s2s']} S2S / "
                        f"{VPN_GATEWAY_SKUS[k]['p2s']} P2S"
                        + (" (zone-redundant)" if VPN_GATEWAY_SKUS[k]['az'] else "")
                    ),
                    help=(
                        "Throughput drives SKU selection. Basic = legacy "
                        "(no BGP / IKEv2 — not for prod). AZ = zone-"
                        "redundant deployment, ~30% premium. VpnGw3+ "
                        "required for ExpressRoute coexistence on the "
                        "same gateway."
                    ),
                )
                st.caption(VPN_GATEWAY_SKUS[vpn_gw_sku]["note"])

            # ---- Derived-quantity sliders / inputs ----
            # Each input is gated on its corresponding LZ component being
            # ticked above. Untie that and the input disappears — no point
            # asking for "Private Endpoint count" when private_endpoint
            # isn't selected. Backup % is the only always-visible quantity
            # since it's about VM disk backup, not an LZ line item.
            _lz_set = set(lz_selected)
            st.markdown("**Quantities**")
            c1, c2 = st.columns(2)
            with c1:
                # Backup % is the storage multiplier driving the Recovery
                # Vault GB line. Only relevant when Recovery Vault is on
                # — otherwise hide it (matches the pattern used by every
                # other quantity input).
                if "recovery_vault" in _lz_set:
                    backup_pct = st.slider(
                        "Azure Backup — % of total disk",
                        0, 200, backup_pct, 5,
                        help=(
                            "Recovery Vault stores N% of total VM disk "
                            "as backup. Default 40% covers ~1 month of "
                            "incremental backups for typical change "
                            "rates (5-10% daily delta × 30 days)."
                        ),
                    )
                if "log_analytics" in _lz_set:
                    la_mb_per_vm_per_day = st.number_input(
                        "Log Analytics — MB/day per VM",
                        min_value=0, max_value=5000, value=la_mb_per_vm_per_day, step=50,
                    )
                if "bandwidth_egress" in _lz_set:
                    bandwidth_gb = st.number_input(
                        "Bandwidth egress — TOTAL GB/month (first 100 GB free; tiered)",
                        min_value=0, max_value=10_000_000, value=bandwidth_gb, step=50,
                        help=(
                            "Total outbound data transfer per month. Azure applies "
                            "tiered pricing: 0-10 TB full rate, 10-50 TB ~5% off, "
                            "50-150 TB ~20% off, 150-500 TB ~45% off, 500+ TB ~55% off."
                        ),
                    )
                if "app_gateway_waf" in _lz_set or "app_gateway_waf_cu" in _lz_set:
                    # CU recommendation per Microsoft Learn sizing guide:
                    #   1 CU ≈ 2.22 Mbps throughput
                    #   1 CU ≈ 2,500 persistent TCP connections
                    #   1 CU ≈ 50 TLS handshakes per second
                    # Actual CU billed = max(of the three constraints). The
                    # default (2 CU avg) covers light production:
                    # ~4.4 Mbps avg / ~5K open connections / ~100 TLS/sec.
                    _cm = str(prefs.get("__compute_mode__", "normal"))
                    _cu_default = {"saving": 2, "normal": 2, "high_perf": 8}.get(_cm, 2)
                    waf_capacity_units = st.number_input(
                        "App Gateway WAF v2 — Capacity Units (avg)",
                        min_value=0, max_value=125,
                        value=int(prefs.get("waf_capacity_units", _cu_default)),
                        step=1,
                        help=(
                            "**How CU is sized** (Microsoft Learn):\n"
                            "  • 1 CU ≈ 2.22 Mbps throughput\n"
                            "  • 1 CU ≈ 2,500 persistent connections\n"
                            "  • 1 CU ≈ 50 TLS handshakes/sec\n"
                            "Billed CU = max(of those three constraints).\n\n"
                            "**Common picks:**\n"
                            "  • 2 CU — light prod (~100 RPS, ~5K connections)\n"
                            "  • 4-8 CU — medium prod (500-2K RPS)\n"
                            "  • 10-30 CU — heavy prod (2-10K RPS)\n"
                            "  • 30-125 CU — enterprise (>10K RPS)\n\n"
                            f"Compute mode `{_cm}` suggests **{_cu_default} CU** "
                            "as the baseline."
                        ),
                    )
                if "firewall_data" in _lz_set or "firewall" in _lz_set or "firewall_premium" in _lz_set:
                    firewall_gb = st.number_input(
                        "Azure Firewall — data processed (GB/month)",
                        min_value=0, max_value=10_000_000,
                        value=int(prefs.get("firewall_gb_processed", 0)), step=100,
                        help=(
                            "Per-GB processed charge on top of the deployment hour. "
                            "Leave 0 if the firewall only handles hub idle traffic."
                        ),
                    )
            with c2:
                if "private_endpoint" in _lz_set:
                    private_endpoint_count = st.number_input(
                        "Private Endpoints — count",
                        min_value=0, max_value=2000, value=private_endpoint_count, step=1,
                        help="Each endpoint is billed at the per-hour rate × 730.",
                    )
                if "vnet_peering_egress" in _lz_set:
                    vnet_peering_gb = st.number_input(
                        "VNet peering — outbound GB/month",
                        min_value=0, max_value=10_000_000, value=vnet_peering_gb, step=50,
                    )
                if "nat_gateway_data" in _lz_set or "nat_gateway" in _lz_set:
                    nat_gateway_gb = st.number_input(
                        "NAT Gateway — data processed GB/month",
                        min_value=0, max_value=10_000_000, value=nat_gateway_gb, step=50,
                    )
                if "automation_account" in _lz_set:
                    automation_minutes = st.number_input(
                        "Automation — runbook minutes/month (first 500 free)",
                        min_value=0, max_value=200_000, value=automation_minutes, step=100,
                    )
                if "app_insights" in _lz_set:
                    app_insights_gb = st.number_input(
                        "Application Insights — GB ingested/month (5 GB free)",
                        min_value=0.0, max_value=50_000.0, value=float(app_insights_gb), step=1.0,
                    )
                if "network_watcher_flow_logs" in _lz_set:
                    flow_logs_gb = st.number_input(
                        "NSG Flow Logs — GB collected/month",
                        min_value=0.0, max_value=50_000.0, value=float(flow_logs_gb), step=1.0,
                    )

    # ----- Exports for cross-pillar wiring -----
    vm_count = aggregate_vm_count(items) if items else 0
    la_gb = round(la_mb_per_vm_per_day * vm_count * 30 / 1024.0, 2)
    total_storage = round(sum(i.storage_gb for i in items), 2) if items else 0.0

    return {
        "items": items, "mode": mode, "spec": spec,
        "strategy_key": strategy_key, "headroom": headroom,
        "disk_tier": disk_tier, "os_mode": os_mode,
        "auto_disk_tier": auto_disk_tier,
        "include_off": include_off,
        "include_lz": include_lz,
        "include_ha": include_ha,
        "include_bcdr": include_bcdr,
        "secondary_region": secondary_region,
        "lz_selected": lz_selected,
        "lz_preset": preset_choice if include_lz else prefs.get("lz_preset", "Standard"),
        "vpn_gw_sku": vpn_gw_sku or prefs.get("vpn_gw_sku"),
        "backup_pct": backup_pct,
        "la_mb_per_vm_per_day": la_mb_per_vm_per_day,
        "bandwidth_gb": bandwidth_gb,
        "waf_capacity_units": waf_capacity_units,
        "firewall_gb_processed": firewall_gb,
        "private_endpoint_count": private_endpoint_count,
        "vnet_peering_gb": vnet_peering_gb,
        "nat_gateway_gb": nat_gateway_gb,
        "automation_minutes": automation_minutes,
        "app_insights_gb": float(app_insights_gb),
        "flow_logs_gb": float(flow_logs_gb),
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
        headroom=inputs.get("headroom", 1.0),
        disk_tier=inputs.get("disk_tier", "Standard SSD"),
        auto_disk_tier=bool(inputs.get("auto_disk_tier", True)),
        os_override=inputs.get("os_mode", "as-detected"),
        app_name=app_name, pricing_mode=pricing_mode,
        use_ahb=bool(inputs.get("__use_ahb__", False)),
        compute_mode=str(inputs.get("__compute_mode__", "normal")),
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
        # CAF additions with user-supplied quantities:
        "private_endpoint": float(inputs.get("private_endpoint_count", 0)) * 730.0,
        "vnet_peering_egress": float(inputs.get("vnet_peering_gb", 0)),
        "nat_gateway_data": float(inputs.get("nat_gateway_gb", 0)),
        "automation_account": float(inputs.get("automation_minutes", 0)),
        "app_insights": float(inputs.get("app_insights_gb", 0.0)),
        "network_watcher_flow_logs": float(inputs.get("flow_logs_gb", 0.0)),
        # Site Recovery per-VM instance fee — auto from inventory
        "site_recovery": float(vm_count),
    }

    # Auto-enable derived LZ components that pair with primary toggles:
    #   recovery_vault     → also adds recovery_vault_instances (per-VM fee)
    #   app_gateway_waf    → also adds app_gateway_waf_cu (if CU input > 0)
    #   firewall           → also adds firewall_data (if GB input > 0)
    #   private_endpoint   → only added when the count input > 0
    #   site_recovery      → only added when there are VMs to protect
    auto_enabled = list(inputs.get("lz_selected") or [])
    if "recovery_vault" in auto_enabled and "recovery_vault_instances" not in auto_enabled and vm_count > 0:
        auto_enabled.append("recovery_vault_instances")
    # `app_gateway_waf_cu`, `firewall_data`, `nat_gateway_data` are `derived`
    # components — no separate checkbox. They auto-attach to their parent
    # whenever the parent is ticked AND the relevant Quantities input > 0.
    if "app_gateway_waf" in auto_enabled and inputs.get("waf_capacity_units", 0) > 0 \
            and "app_gateway_waf_cu" not in auto_enabled:
        auto_enabled.append("app_gateway_waf_cu")
    if ("firewall" in auto_enabled or "firewall_premium" in auto_enabled) \
            and inputs.get("firewall_gb_processed", 0) > 0 \
            and "firewall_data" not in auto_enabled:
        auto_enabled.append("firewall_data")
    if "nat_gateway" in auto_enabled and inputs.get("nat_gateway_gb", 0) > 0 \
            and "nat_gateway_data" not in auto_enabled:
        auto_enabled.append("nat_gateway_data")
    # Don't cost a $0 per-endpoint-hour line when the count is 0
    if "private_endpoint" in auto_enabled and inputs.get("private_endpoint_count", 0) <= 0:
        auto_enabled = [k for k in auto_enabled if k != "private_endpoint"]
    if "site_recovery" in auto_enabled and vm_count <= 0:
        auto_enabled = [k for k in auto_enabled if k != "site_recovery"]

    if inputs.get("include_lz") and auto_enabled:
        # Preset-driven SKU recommendations — currently VPN Gateway
        # (preset → VpnGw1 / VpnGw1AZ / VpnGw2AZ / VpnGw3AZ). User can
        # override via the `vpn_gw_sku` widget in render_inputs which
        # writes to `inputs["vpn_gw_sku"]`.
        from ..pricing.lz_static import recommend_vpn_sku
        _preset = inputs.get("lz_preset", "Foundation")
        _vpn_sku = inputs.get("vpn_gw_sku") or recommend_vpn_sku(_preset)
        all_lines.extend(
            build_landing_zone_bom(
                client=client, region=region,
                enabled_keys=auto_enabled,
                quantity_overrides=lz_overrides,
                sku_overrides={"vpn_gw": _vpn_sku},
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
