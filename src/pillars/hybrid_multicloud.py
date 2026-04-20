"""Hybrid & Multi-cloud pillar.

Cost-models Azure Arc-connected resources running on on-premises, AWS, GCP,
or edge hardware — plus Defender for Cloud plans applied across clouds.

Inputs:
- Arc-enabled servers per environment (on-prem / AWS / GCP / edge)
- Arc-enabled SQL Server licensing (BYOL / Standard PAYG / Enterprise PAYG)
- Windows Server via Arc licensing (BYOL / Standard PAYG / Datacenter PAYG)
- Defender plans to extend to non-Azure resources
- Log Analytics ingestion MB/day per Arc server

Line items produced:
- Azure Arc-enabled Servers management plane (FREE, documentation line)
- Defender for Servers P2 × non-Azure server count
- Defender CSPM multicloud (per protected resource)
- Defender for SQL on machines × Arc SQL vCores (optional)
- Defender for Containers (Arc-enabled K8s / EKS / GKE) × vCores (optional)
- Arc-SQL Server PAYG licensing × cores (if PAYG)
- Windows Server via Arc PAYG × cores (if PAYG)
- Log Analytics ingestion for Arc servers

Defender plans cost the SAME per-resource regardless of where the resource
runs (Azure VM, Arc on-prem VM, AWS EC2, GCP Compute Engine). We reuse
`build_defender_bom` with the aggregated non-Azure server count.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..landing_zone.defender import build_defender_bom
from ..models import BomLine
from ..pricing.retail import HOURS_PER_MONTH, RetailPricesClient
from .azure_security import _cspm_line, _pick, _sentinel_line


PILLAR_METADATA = {
    "key": "hybrid_multicloud",
    "label": "Hybrid & Multi-cloud",
    "description": "Azure Arc, Defender for Cloud across on-prem / AWS / GCP, Arc-enabled SQL and Windows Server licensing.",
    "icon": "🔗",
    "needs_vm_extraction": False,
    "default_strategy": None,
}


# Where the servers live — used for labelling + Defender counts
ENV_CATEGORIES: Dict[str, str] = {
    "onprem": "On-premises datacenter / private cloud",
    "aws":    "AWS EC2 instances (connected via Arc)",
    "gcp":    "GCP Compute Engine (connected via Arc)",
    "edge":   "Edge / branch / manufacturing sites",
}


# Arc-enabled SQL Server licensing choices
ARC_SQL_LICENSING: Dict[str, Dict] = {
    "byol":       {"label": "Bring-your-own license (BYOL) — no Arc license charge", "meter_hint": None},
    "std_payg":   {"label": "SQL Server Standard — Arc PAYG (per core)",              "meter_hint": "sql server standard arc"},
    "ent_payg":   {"label": "SQL Server Enterprise — Arc PAYG (per core)",            "meter_hint": "sql server enterprise arc"},
    "ent_core":   {"label": "SQL Server Enterprise Core — Arc PAYG",                  "meter_hint": "sql server enterprise core arc"},
}


# Windows Server via Arc PAYG (announced 2024 GA — billed per core/month)
WIN_SERVER_LICENSING: Dict[str, Dict] = {
    "byol":     {"label": "Bring-your-own Windows Server license (SA required)", "meter_hint": None},
    "std_payg": {"label": "Windows Server Standard — Arc PAYG (per core)",       "meter_hint": "windows server standard arc"},
    "dc_payg":  {"label": "Windows Server Datacenter — Arc PAYG (per core)",     "meter_hint": "windows server datacenter arc"},
}


# Defender plans applicable to multicloud / Arc resources. Keys align with
# DEFENDER_PLANS in landing_zone.defender so build_defender_bom picks them up.
MULTICLOUD_DEFENDER_PLANS: Dict[str, Dict] = {
    "servers_p2":  {"label": "Defender for Servers Plan 2", "scales_with": "servers",   "defender_plan": "servers_p2"},
    "cspm":        {"label": "Defender CSPM (enhanced) across clouds", "scales_with": "servers",   "defender_plan": None},
    "sql_on_vms":  {"label": "Defender for SQL on Arc machines",        "scales_with": "manual",    "defender_plan": "sql_on_vms"},
    "storage":     {"label": "Defender for Storage (Azure + S3 + GCS)", "scales_with": "manual",    "defender_plan": "storage"},
    "containers":  {"label": "Defender for Containers (Arc K8s / EKS / GKE)", "scales_with": "manual", "defender_plan": "containers"},
}


# ---------------------------------------------------------------------------
# Line builders
# ---------------------------------------------------------------------------

def _arc_docs_line(region: str, total_arc_servers: int, app_name: str) -> BomLine:
    """Zero-cost documentation line so the Arc management plane shows up in
    the BOM. Users often forget that Arc management itself is free."""
    return BomLine(
        category="Hybrid",
        resource=(
            f"Azure Arc-enabled Servers management — {total_arc_servers} resource(s) "
            f"(management plane is FREE; billed services are listed separately)"
        ),
        sku="Arc-Servers-Management",
        meter="Management plane",
        region=region,
        quantity=total_arc_servers, unit="resource",
        unit_price=0.0, monthly_cost=0.0, currency="USD",
        source="retail-prices-free",
        service_name="Azure Arc",
        custom_name=f"{app_name}-Arc-Mgmt" if app_name else "Arc-Mgmt",
    )


def _arc_license_line(
    client: RetailPricesClient,
    region: str,
    service_family_hint: str,     # "SQL Server" or "Windows Server"
    meter_hint: str,
    cores: int,
    category: str,
    resource_label: str,
    service_display: str,
    custom_name: str,
) -> Optional[BomLine]:
    """Generic per-core licensing line for Arc-enabled products (SQL, Windows)."""
    if cores <= 0 or not meter_hint:
        return None
    # Licensing meters can live under several serviceName values depending on
    # how the retail feed is organised. Try a few in order of specificity.
    service_names = [
        service_family_hint,
        "Azure Arc",
        "Virtual Machines Licenses",
        "SQL Database",
    ]
    chosen = None
    for svc in service_names:
        recs = client.query(
            f"serviceName eq '{svc}' and armRegionName eq '{region}' and priceType eq 'Consumption'"
        )
        chosen = _pick([r for r in recs if "arc" in r.meter_name.lower()], meter_hint)
        if chosen and chosen.retail_price > 0:
            break
    if not chosen:
        # Graceful miss line so the customer sees the component was selected
        return BomLine(
            category=category, resource=f"{resource_label} × {cores} cores",
            sku=meter_hint, meter="(price not found in region — check licensing availability)",
            region=region, quantity=cores * HOURS_PER_MONTH, unit="core-hour",
            unit_price=0.0, monthly_cost=0.0, currency="USD",
            source="retail-prices-miss",
            service_name=service_display,
            custom_name=custom_name,
        )
    # Most Arc licensing meters are per core-hour
    unit_is_hour = "hour" in (chosen.unit_of_measure or "").lower()
    qty = cores * (HOURS_PER_MONTH if unit_is_hour else 1)
    unit = "core-hours" if unit_is_hour else (chosen.unit_of_measure or "core-month")
    return BomLine(
        category=category,
        resource=f"{resource_label} × {cores} cores",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=qty, unit=unit,
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name=service_display,
        custom_name=custom_name,
    )


def _la_ingestion_line(
    client: RetailPricesClient, region: str, la_gb: float, app_name: str,
) -> Optional[BomLine]:
    if la_gb <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "pay-as-you-go data ingestion")
    if not chosen:
        return None
    return BomLine(
        category="Management",
        resource=f"Log Analytics ingestion — Arc servers (~{la_gb} GB/mo)",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=la_gb, unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * la_gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Monitor",
        custom_name=f"{app_name}-Arc-LogAnalytics" if app_name else "Arc-LogAnalytics",
    )


# ---------------------------------------------------------------------------
# Pillar contract
# ---------------------------------------------------------------------------

def _sel_idx(options: list, saved, default_idx: int = 0) -> int:
    try:
        return options.index(saved)
    except (ValueError, TypeError):
        return default_idx


def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    st.caption(
        "Models Azure Arc-connected resources across on-premises, AWS, GCP, and "
        "edge. The Arc management plane is **free**; Defender, Azure Monitor, "
        "and Arc licensing are billed."
    )

    sugg_servers = prefs.get("arc_servers", {}) or {}
    sugg_sql = prefs.get("arc_sql", {}) or {}
    sugg_win = prefs.get("arc_winserver", {}) or {}
    sugg_def = set(prefs.get("multicloud_defender") or [])
    sugg_def_manual = prefs.get("multicloud_defender_counts", {}) or {}

    with st.expander("Arc-enabled servers by environment", expanded=True):
        servers: Dict[str, int] = {}
        for env_key, env_label in ENV_CATEGORIES.items():
            servers[env_key] = int(st.number_input(
                env_label, min_value=0,
                value=int(sugg_servers.get(env_key, 0)), step=1,
                key=f"arc_env_{env_key}",
            ))
        total_servers = sum(servers.values())
        st.caption(f"**Total Arc-enabled servers: {total_servers}**")

    with st.expander("Azure Arc-enabled SQL Server licensing", expanded=False):
        c1, c2 = st.columns([2, 1])
        with c1:
            _sql_opts = list(ARC_SQL_LICENSING.keys())
            sql_lic = st.selectbox(
                "Licensing choice", _sql_opts,
                index=_sel_idx(_sql_opts, sugg_sql.get("licensing", "byol")),
                format_func=lambda k: ARC_SQL_LICENSING[k]["label"],
            )
        with c2:
            sql_cores = st.number_input(
                "Total SQL cores", min_value=0,
                value=int(sugg_sql.get("cores", 0)), step=2,
                disabled=(sql_lic == "byol"),
                help="Minimum 4 cores per SQL instance for most licensing tiers.",
            )

    with st.expander("Windows Server via Arc licensing (pay-as-you-go)", expanded=False):
        c1, c2 = st.columns([2, 1])
        with c1:
            _win_opts = list(WIN_SERVER_LICENSING.keys())
            win_lic = st.selectbox(
                "Licensing choice", _win_opts,
                index=_sel_idx(_win_opts, sugg_win.get("licensing", "byol")),
                format_func=lambda k: WIN_SERVER_LICENSING[k]["label"],
                help=(
                    "Announced GA mid-2024 / refined 2025: monthly per-core "
                    "Windows Server licensing via Arc for on-prem and edge. "
                    "BYOL (with Software Assurance) remains free."
                ),
            )
        with c2:
            win_cores = st.number_input(
                "Total Windows cores", min_value=0,
                value=int(sugg_win.get("cores", 0)), step=8,
                disabled=(win_lic == "byol"),
                help="Minimum 8 cores per server under Microsoft core licensing rules.",
            )

    with st.expander("Defender for Cloud plans (applied across Azure + on-prem + AWS + GCP)", expanded=False):
        st.caption(
            "Defender plans cost the same per-resource regardless of where the "
            "resource runs. Ticking a plan applies it to the Arc server total + "
            "any manual counts below."
        )
        enabled_defender: List[str] = []
        manual_counts: Dict[str, float] = {}
        for key, meta in MULTICLOUD_DEFENDER_PLANS.items():
            default_on = (key in sugg_def) if sugg_def else (key in ("servers_p2", "cspm"))
            on = st.checkbox(meta["label"], value=default_on, key=f"mc_def_{key}")
            if on:
                enabled_defender.append(key)
                if meta["scales_with"] == "manual":
                    dplan = meta.get("defender_plan", key)
                    manual_counts[dplan] = float(st.number_input(
                        f"{meta['label']} — count",
                        min_value=0.0, value=float(sugg_def_manual.get(dplan, 0.0)),
                        step=1.0, key=f"mc_def_qty_{key}",
                    ))

    la_mb_per_server = int(st.number_input(
        "Log Analytics ingestion per Arc server (MB/day)",
        min_value=0, max_value=5000,
        value=int(prefs.get("arc_la_mb_per_server_per_day", 200)),
        step=50,
        help=(
            "Monthly GB = MB/day × server count × 30 ÷ 1024. 200 MB/day is a "
            "typical baseline for Azure Monitor Agent + baseline events; +500 MB "
            "with Defender P2, +1 GB with Sentinel correlation."
        ),
    ))

    return {
        "arc_servers": servers,
        "arc_sql": {"licensing": sql_lic, "cores": int(sql_cores)},
        "arc_winserver": {"licensing": win_lic, "cores": int(win_cores)},
        "multicloud_defender": enabled_defender,
        "multicloud_defender_counts": manual_counts,
        "arc_la_mb_per_server_per_day": la_mb_per_server,
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    lines: List[BomLine] = []

    servers: Dict[str, int] = inputs.get("arc_servers", {}) or {}
    total_arc_servers = int(sum(servers.values()))

    # 1. Arc management plane — free, but surface it so the BOM is complete
    if total_arc_servers > 0:
        lines.append(_arc_docs_line(region, total_arc_servers, app_name))

    # 2. Defender plans applied to Arc resources
    defender_enabled = inputs.get("multicloud_defender", []) or []
    defender_plan_keys = [
        MULTICLOUD_DEFENDER_PLANS[k]["defender_plan"]
        for k in defender_enabled
        if MULTICLOUD_DEFENDER_PLANS.get(k, {}).get("defender_plan")
    ]
    if defender_plan_keys and total_arc_servers > 0:
        lines.extend(
            build_defender_bom(
                client=client, region=region,
                enabled_keys=defender_plan_keys,
                vm_count=total_arc_servers,
                manual_counts=inputs.get("multicloud_defender_counts", {}),
            )
        )
        # Tag Defender lines so custom_name reflects the hybrid origin
        for l in lines:
            if l.category == "Security" and (app_name + "-Arc") not in l.custom_name:
                l.custom_name = f"{app_name}-Arc-{l.sku}" if app_name else f"Arc-{l.sku}"

    # 3. Defender CSPM multicloud (per-resource)
    if "cspm" in defender_enabled and total_arc_servers > 0:
        cspm = _cspm_line(client, region, total_arc_servers)
        if cspm:
            cspm.custom_name = f"{app_name}-Arc-Defender-CSPM" if app_name else "Arc-Defender-CSPM"
            cspm.resource = (
                f"Defender CSPM (multicloud) — {total_arc_servers} Arc resource(s) "
                "across Azure + on-prem + AWS + GCP"
            )
            lines.append(cspm)

    # 4. Arc-enabled SQL Server licensing
    sql = inputs.get("arc_sql", {}) or {}
    sql_lic = sql.get("licensing", "byol")
    sql_cores = int(sql.get("cores", 0))
    if sql_lic != "byol" and sql_cores > 0:
        meter_hint = ARC_SQL_LICENSING[sql_lic]["meter_hint"]
        l = _arc_license_line(
            client, region,
            service_family_hint="SQL Server",
            meter_hint=meter_hint,
            cores=sql_cores,
            category="Hybrid",
            resource_label=f"Arc-SQL {ARC_SQL_LICENSING[sql_lic]['label'].split(' — ')[0]}",
            service_display="Azure Arc-enabled SQL Server",
            custom_name=f"{app_name}-Arc-SQL-{sql_lic}" if app_name else f"Arc-SQL-{sql_lic}",
        )
        if l:
            lines.append(l)

    # 5. Windows Server via Arc licensing
    win = inputs.get("arc_winserver", {}) or {}
    win_lic = win.get("licensing", "byol")
    win_cores = int(win.get("cores", 0))
    if win_lic != "byol" and win_cores > 0:
        meter_hint = WIN_SERVER_LICENSING[win_lic]["meter_hint"]
        l = _arc_license_line(
            client, region,
            service_family_hint="Azure Arc",
            meter_hint=meter_hint,
            cores=win_cores,
            category="Hybrid",
            resource_label=f"Arc-WindowsServer {WIN_SERVER_LICENSING[win_lic]['label'].split(' — ')[0]}",
            service_display="Windows Server via Azure Arc",
            custom_name=f"{app_name}-Arc-WinSvr-{win_lic}" if app_name else f"Arc-WinSvr-{win_lic}",
        )
        if l:
            lines.append(l)

    # 6. Log Analytics ingestion for Arc servers
    mb_per_server_per_day = int(inputs.get("arc_la_mb_per_server_per_day", 200))
    la_gb = round(mb_per_server_per_day * total_arc_servers * 30 / 1024.0, 2)
    l = _la_ingestion_line(client, region, la_gb, app_name)
    if l:
        lines.append(l)

    return lines, []
