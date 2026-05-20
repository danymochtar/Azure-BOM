"""Azure Security pillar.

Owns the full security catalog: Defender suite (via defender_plan keys),
Microsoft Sentinel, CSPM, and new components (WAF managed rules, Private Link,
Purview, PIM).

Also absorbs the SIEM cost model (Log Analytics commitment tiers + Sentinel
ingestion + retention) so an assessment focused purely on SOC/SIEM can run
standalone without a VM list.

`SECURITY_COMPONENTS` lives HERE (moved from src/architecture/security_tier.py);
`security_tier.py` keeps a re-export shim for back-compat.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..landing_zone.defender import build_defender_bom
from ..models import BomLine
from ..pricing.retail import HOURS_PER_MONTH, PriceRecord, RetailPricesClient


# ---------------------------------------------------------------------------
# Component catalog
# ---------------------------------------------------------------------------
SECURITY_COMPONENTS: Dict[str, Dict] = {
    "cspm": {
        "label": "Defender CSPM (enhanced posture management)",
        "default": True,
        "scales_with": "vms",
        "notes": "Per-resource posture + secure score + regulatory compliance.",
    },
    "servers_p2": {
        "label": "Defender for Servers Plan 2",
        "default": True,
        "scales_with": "vms",
        "defender_plan": "servers_p2",
        "notes": "Per protected VM. Includes EDR integration, FIM, JIT access.",
    },
    "sql_on_vms": {
        "label": "Defender for SQL servers on machines",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "sql_on_vms",
        "unit_prompt": "Total SQL vCores across VMs",
        "notes": "Per SQL vCore on IaaS. Skip if DB runs on Azure SQL PaaS.",
    },
    "storage": {
        "label": "Defender for Storage",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "storage",
        "unit_prompt": "Storage accounts",
        "notes": "Per storage account + malware scanning GB.",
    },
    "appsvc": {
        "label": "Defender for App Service",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "appsvc",
        "unit_prompt": "App Service Plan vCores",
        "notes": "Per App Service Plan vCore.",
    },
    "keyvault": {
        "label": "Defender for Key Vault",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "keyvault",
        "unit_prompt": "Vault transactions (per 10K)",
        "notes": "Per 10K vault transactions.",
    },
    "containers": {
        "label": "Defender for Containers",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "containers",
        "unit_prompt": "AKS node vCores",
        "notes": "Per AKS node vCore.",
    },
    "cosmos": {
        "label": "Defender for Cosmos DB",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "cosmos",
        "unit_prompt": "Cosmos 100-RU/s blocks",
        "notes": "Per 100 RU/s provisioned throughput, normalised.",
    },
    "oss_db": {
        "label": "Defender for Open-Source RDBMS (PG/MySQL/MariaDB)",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "oss_db",
        "unit_prompt": "Flexible Server vCores",
        "notes": "Per vCore — covers PostgreSQL/MySQL/MariaDB Flexible.",
    },
    "ai": {
        "label": "Defender for AI workloads",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "ai",
        "unit_prompt": "Azure OpenAI deployments",
        "notes": "Per OpenAI / Foundry deployment. Prompt-injection + DLP detection.",
    },
    "apis": {
        "label": "Defender for APIs",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "apis",
        "unit_prompt": "APIs protected",
        "notes": "Per API exposed via API Management.",
    },
    "rm": {
        "label": "Defender for Resource Manager",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "rm",
        "unit_prompt": "Subscriptions",
        "notes": "Per subscription. Detects suspicious ARM-level operations.",
    },
    "dns": {
        "label": "Defender for DNS",
        "default": False,
        "scales_with": "manual",
        "defender_plan": "dns",
        "unit_prompt": "Subscriptions",
        "notes": "Per subscription. Detects data exfil / C2 via DNS tunnelling.",
    },
    "sentinel": {
        "label": "Microsoft Sentinel (PAYG ingestion)",
        "default": False,
        "scales_with": "la_gb",
        "notes": "Priced per GB on top of Log Analytics ingestion.",
    },
    # New (pillar additions)
    "waf": {
        "label": "Azure WAF managed rules policy",
        "default": False,
        "scales_with": "manual",
        "unit_prompt": "Policies",
        "notes": "Managed-rules cost on top of App Gateway / Front Door WAF.",
    },
    "private_link": {
        "label": "Private Link endpoints",
        "default": False,
        "scales_with": "manual",
        "unit_prompt": "Endpoints",
        "notes": "Per endpoint-hour + processed data (not modelled in detail).",
    },
    "purview": {
        "label": "Microsoft Purview (Data Governance)",
        "default": False,
        "scales_with": "manual",
        "unit_prompt": "Capacity units",
        "notes": "Data catalog + lineage + classification.",
    },
    "pim": {
        "label": "Entra ID P2 / Privileged Identity Management",
        "default": False,
        "scales_with": "manual",
        "unit_prompt": "Eligible users",
        "notes": "Per user-month for PIM-eligible identities.",
    },
    "ddos_ip_protection": {
        "label": "Azure DDoS IP Protection (per public IP)",
        "default": False,
        "scales_with": "manual",
        "unit_prompt": "Protected public IPs",
        "notes": "~$199/mo per protected IP. Cheaper than Network Protection for small footprints.",
    },
    "ddos_network_protection": {
        "label": "Azure DDoS Network Protection (flat, up to 100 resources)",
        "default": False,
        "scales_with": "manual",
        "unit_prompt": "Protection plans (usually 1)",
        "notes": "Flat ~$2,944/mo. Covers up to 100 public IPs across the tenant; extra IPs charged separately.",
    },
}


SECURITY_TIERS: Dict[str, Dict] = {
    "none":  {"label": "None",  "description": "Compute only, no security telemetry.", "components": []},
    "basic": {"label": "Basic (Defender CSPM only)", "description": "Foundational posture.", "components": ["cspm"]},
    "full":  {
        "label": "Full suite (all workload Defender plans + Sentinel)",
        "description": "Every Defender plan + Sentinel.",
        "components": ["cspm", "servers_p2", "sql_on_vms", "storage", "appsvc", "keyvault", "containers", "sentinel"],
    },
}


SIEM_COMMITMENT_TIERS: Dict[str, Dict] = {
    "payg":  {"label": "Pay-as-you-go",       "gb_floor": 0,    "meter_hint": "pay-as-you-go"},
    "100":   {"label": "100 GB/day",          "gb_floor": 100,  "meter_hint": "100 gb commitment"},
    "200":   {"label": "200 GB/day",          "gb_floor": 200,  "meter_hint": "200 gb commitment"},
    "400":   {"label": "400 GB/day",          "gb_floor": 400,  "meter_hint": "400 gb commitment"},
    "500":   {"label": "500 GB/day",          "gb_floor": 500,  "meter_hint": "500 gb commitment"},
    "1000":  {"label": "1 TB/day",            "gb_floor": 1000, "meter_hint": "1000 gb commitment"},
    "2000":  {"label": "2 TB/day",            "gb_floor": 2000, "meter_hint": "2000 gb commitment"},
    "5000":  {"label": "5 TB/day",            "gb_floor": 5000, "meter_hint": "5000 gb commitment"},
}


# ---------------------------------------------------------------------------
# Pillar metadata
# ---------------------------------------------------------------------------
PILLAR_METADATA = {
    "key": "azure_security",
    "label": "Azure Security (Sentinel + advanced)",
    "description": (
        "Standalone advanced security: Microsoft Sentinel ingestion, "
        "WAF managed rules, Private Link, Purview, PIM. Defender for "
        "Cloud is included automatically in EVERY assessment as a "
        "CAF-mandatory baseline — only enable this pillar when you "
        "need Sentinel or non-Defender advanced capabilities."
    ),
    "icon": "🛡",
    "needs_vm_extraction": False,
    "default_strategy": None,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

from ..pricing.picker import pick_by_substring as _pick


def _cspm_line(client: RetailPricesClient, region: str, vm_count: int) -> Optional[BomLine]:
    if vm_count <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Microsoft Defender for Cloud' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "defender cspm") or _pick(recs, "cspm")
    if not chosen:
        return None
    unit_is_hour = "hour" in (chosen.unit_of_measure or "").lower()
    qty = vm_count * (HOURS_PER_MONTH if unit_is_hour else 1)
    unit = "resource-hours" if unit_is_hour else chosen.unit_of_measure or "unit"
    return BomLine(
        category="Security",
        resource=f"Defender CSPM — {vm_count} resource(s)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=qty, unit=unit,
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
    )


def _sentinel_line(client: RetailPricesClient, region: str, gb: float) -> Optional[BomLine]:
    if gb <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Sentinel' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    if not recs:
        recs = client.query(
            f"serviceName eq 'Azure Sentinel' and armRegionName eq '{region}' and priceType eq 'Consumption'"
        )
    chosen = _pick(recs, "pay-as-you-go")
    if not chosen:
        return None
    return BomLine(
        category="Security",
        resource=f"Microsoft Sentinel (PAYG ingestion, {gb} GB/mo)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=gb, unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
    )


def _la_commitment_line(
    client: RetailPricesClient, region: str, tier_key: str, gb_per_month: float
) -> Optional[BomLine]:
    cfg = SIEM_COMMITMENT_TIERS[tier_key]
    recs = client.query(
        f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, cfg["meter_hint"])
    if not chosen:
        return None
    return BomLine(
        category="Management",
        resource=f"Log Analytics ({cfg['label']} ingestion)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=30.0 if tier_key != "payg" else gb_per_month,
        unit="day" if tier_key != "payg" else "GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * (30.0 if tier_key != "payg" else gb_per_month), 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Monitor",
        custom_name=f"Sentinel-LogAnalytics-{cfg['label']}",
    )


def _la_retention_line(client: RetailPricesClient, region: str, retention_gb: float) -> Optional[BomLine]:
    if retention_gb <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, "data retention")
    if not chosen:
        return None
    return BomLine(
        category="Management",
        resource=f"Log Analytics retention (~{retention_gb:.0f} GB-month)",
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=retention_gb, unit="GB",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * retention_gb, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Monitor",
        custom_name="Sentinel-LogAnalytics-Retention",
    )


def _manual_flat_line(
    client: RetailPricesClient,
    region: str,
    service_name_filter: str,
    meter_hint: str,
    qty: float,
    unit_label: str,
    category: str,
    resource: str,
    service_display: str,
    custom_name: str,
) -> Optional[BomLine]:
    """Generic helper for flat per-unit lines (WAF, Private Link, Purview, PIM)."""
    if qty <= 0:
        return None
    recs = client.query(
        f"serviceName eq '{service_name_filter}' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, meter_hint)
    if not chosen:
        return None
    return BomLine(
        category=category,
        resource=resource,
        sku=chosen.sku_name or chosen.product_name,
        meter=chosen.meter_name,
        region=region,
        quantity=qty, unit=unit_label,
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name=service_display,
        custom_name=custom_name,
    )


# ---------------------------------------------------------------------------
# BOM builders
# ---------------------------------------------------------------------------

def build_security_bom(
    client: RetailPricesClient,
    region: str,
    enabled_keys: List[str],
    vm_count: int,
    la_gb: float = 0.0,
    manual_counts: Optional[Dict[str, float]] = None,
    app_name: str = "",
) -> List[BomLine]:
    """Build security lines from an explicit list of enabled component keys."""
    manual_counts = manual_counts or {}
    lines: List[BomLine] = []
    app_prefix = f"{app_name}-" if app_name else ""

    # Defender plans via existing builder
    defender_enabled = [
        SECURITY_COMPONENTS[k]["defender_plan"]
        for k in enabled_keys
        if SECURITY_COMPONENTS.get(k, {}).get("defender_plan")
    ]
    if defender_enabled:
        lines.extend(
            build_defender_bom(
                client=client, region=region,
                enabled_keys=defender_enabled,
                vm_count=vm_count, manual_counts=manual_counts,
            )
        )

    if "cspm" in enabled_keys:
        cspm = _cspm_line(client, region, vm_count)
        if cspm:
            cspm.custom_name = f"{app_prefix}Defender-CSPM"
            lines.append(cspm)

    if "sentinel" in enabled_keys:
        sn = _sentinel_line(client, region, la_gb)
        if sn:
            sn.custom_name = f"{app_prefix}Sentinel"
            lines.append(sn)

    # New components
    if "waf" in enabled_keys:
        qty = manual_counts.get("waf", 1) or 1
        l = _manual_flat_line(
            client, region,
            service_name_filter="Application Gateway",
            meter_hint="managed rule",
            qty=qty, unit_label="policy-month",
            category="Security",
            resource=f"Azure WAF managed rules × {qty}",
            service_display="Application Gateway",
            custom_name=f"{app_prefix}WAF",
        )
        if l:
            lines.append(l)

    if "private_link" in enabled_keys:
        qty = manual_counts.get("private_link", 1) or 1
        l = _manual_flat_line(
            client, region,
            service_name_filter="Private Link",
            meter_hint="endpoint",
            qty=qty * HOURS_PER_MONTH, unit_label="endpoint-hours",
            category="Security",
            resource=f"Private Link endpoints × {int(qty)}",
            service_display="Private Link",
            custom_name=f"{app_prefix}PrivateLink",
        )
        if l:
            lines.append(l)

    if "purview" in enabled_keys:
        qty = manual_counts.get("purview", 1) or 1
        l = _manual_flat_line(
            client, region,
            service_name_filter="Microsoft Purview",
            meter_hint="capacity",
            qty=qty * HOURS_PER_MONTH, unit_label="capacity-hours",
            category="Security",
            resource=f"Microsoft Purview × {int(qty)} CU",
            service_display="Microsoft Purview",
            custom_name=f"{app_prefix}Purview",
        )
        if l:
            lines.append(l)

    if "pim" in enabled_keys:
        qty = manual_counts.get("pim", 0)
        l = _manual_flat_line(
            client, region,
            service_name_filter="Azure Active Directory",
            meter_hint="premium p2",
            qty=qty, unit_label="user-month",
            category="Security",
            resource=f"Entra ID P2 / PIM × {int(qty)} users",
            service_display="Microsoft Entra ID",
            custom_name=f"{app_prefix}EntraP2-PIM",
        )
        if l:
            lines.append(l)

    if "ddos_ip_protection" in enabled_keys:
        qty = manual_counts.get("ddos_ip_protection", 0) or 0
        l = _manual_flat_line(
            client, region,
            service_name_filter="DDoS Protection",
            meter_hint="ip protection",
            qty=qty, unit_label="ip-month",
            category="Security",
            resource=f"Azure DDoS IP Protection × {int(qty)} public IPs",
            service_display="Azure DDoS Protection",
            custom_name=f"{app_prefix}DDoS-IP",
        )
        if l:
            lines.append(l)

    if "ddos_network_protection" in enabled_keys:
        qty = manual_counts.get("ddos_network_protection", 1) or 1
        l = _manual_flat_line(
            client, region,
            service_name_filter="DDoS Protection",
            meter_hint="network protection",
            qty=qty, unit_label="plan-month",
            category="Security",
            resource=f"Azure DDoS Network Protection × {int(qty)} plan",
            service_display="Azure DDoS Protection",
            custom_name=f"{app_prefix}DDoS-Network",
        )
        if l:
            lines.append(l)

    return lines


def build_siem_bom(
    client: RetailPricesClient,
    region: str,
    gb_per_day: float,
    retention_days_over_free: int = 0,
    commitment_tier: str = "payg",
    defender_vm_count: int = 0,
    include_cspm: bool = True,
    app_name: str = "",
) -> List[BomLine]:
    """SIEM/SOC-only flow: Log Analytics ingestion + Sentinel + retention +
    optional Defender (CSPM always; Servers P2 only when defender_vm_count > 0).
    """
    lines: List[BomLine] = []
    app_prefix = f"{app_name}-" if app_name else ""
    gb_per_month = round(gb_per_day * 30, 2)

    la_line = _la_commitment_line(client, region, commitment_tier, gb_per_month)
    if la_line:
        la_line.custom_name = f"{app_prefix}{la_line.custom_name}"
        lines.append(la_line)

    sn = _sentinel_line(client, region, gb_per_month)
    if sn:
        sn.custom_name = f"{app_prefix}Sentinel"
        lines.append(sn)

    retention_gb = round(gb_per_month * (retention_days_over_free / 30.0), 2)
    ret = _la_retention_line(client, region, retention_gb)
    if ret:
        ret.custom_name = f"{app_prefix}{ret.custom_name}"
        lines.append(ret)

    if include_cspm or defender_vm_count > 0:
        plans = []
        if defender_vm_count > 0:
            plans.append("servers_p2")
        if plans:
            lines.extend(
                build_defender_bom(
                    client=client, region=region,
                    enabled_keys=plans, vm_count=defender_vm_count,
                )
            )
        if include_cspm:
            cspm = _cspm_line(client, region, max(defender_vm_count, 1))
            if cspm:
                cspm.custom_name = f"{app_prefix}Defender-CSPM"
                lines.append(cspm)

    return lines


def defender_plans_for_tier(tier: str) -> List[str]:
    return [
        SECURITY_COMPONENTS[k]["defender_plan"]
        for k in SECURITY_TIERS.get(tier, {}).get("components", [])
        if "defender_plan" in SECURITY_COMPONENTS.get(k, {})
    ]


# ---------------------------------------------------------------------------
# Pillar contract
# ---------------------------------------------------------------------------

def render_inputs(st, prefs: dict, app_name: str, region: str,
                  upload_bytes: bytes, upload_name: str, profile) -> dict:
    """Render the security pillar's input widgets and return a plain dict."""
    # prefs may include auto-simulate overlays: `enabled`, `manual_counts`,
    # `siem_gb_per_day`, `siem_retention_days`, `siem_tier`, `target_scope`.
    # Fall back to legacy key `sec_enabled` for backward compat.
    sugg_enabled = set(prefs.get("enabled") or prefs.get("sec_enabled") or [])
    sugg_manual = prefs.get("manual_counts", {}) or {}
    default_siem_gb = float(prefs.get("siem_gb_per_day", 0.0))
    default_siem_retention = int(prefs.get("siem_retention_days", 0))
    default_siem_tier = prefs.get("siem_tier", "payg")

    enabled: List[str] = []
    manual_counts: Dict[str, float] = {}

    st.caption("Tick the security services to include. Each component is independent.")
    for key, meta in SECURITY_COMPONENTS.items():
        default_on = (key in sugg_enabled) if sugg_enabled else meta["default"]
        on = st.checkbox(meta["label"], value=default_on, key=f"sec_{key}")
        if on:
            enabled.append(key)
            if meta.get("scales_with") == "manual":
                defender_key = meta.get("defender_plan", key)
                manual_counts[defender_key] = st.number_input(
                    meta.get("unit_prompt", "Count"),
                    min_value=0.0,
                    value=float(sugg_manual.get(defender_key, 0.0)),
                    key=f"sec_qty_{key}",
                )

    st.markdown("**SIEM inputs** (fill only if Sentinel or a SIEM/SOC assessment is in scope)")
    col1, col2, col3 = st.columns(3)
    with col1:
        siem_gb_per_day = st.number_input(
            "Log ingestion (GB/day)", min_value=0.0, value=default_siem_gb, step=5.0,
            help="1 EPS ≈ 2-3 MB/day → 10K EPS ≈ 25 GB/day.",
        )
    with col2:
        siem_retention_days = st.number_input(
            "Retention beyond free 90 days", min_value=0, value=default_siem_retention, step=30,
        )
    with col3:
        _tier_keys = list(SIEM_COMMITMENT_TIERS.keys())
        try:
            _tier_idx = _tier_keys.index(default_siem_tier)
        except ValueError:
            _tier_idx = 0
        siem_tier = st.selectbox(
            "Commitment tier", _tier_keys, index=_tier_idx,
            format_func=lambda k: SIEM_COMMITMENT_TIERS[k]["label"],
        )

    st.markdown("**Standalone security scope**")
    target_scope = st.number_input(
        "# of resources under governance (used when no VM inventory is ticked)",
        min_value=0,
        value=int(prefs.get("target_scope") or prefs.get("security_target_scope", 0)),
        step=10,
        help="Drives CSPM / Defender resource counts when this pillar runs without a lift-shift inventory.",
    )

    return {
        "enabled": enabled,
        "manual_counts": manual_counts,
        "siem_gb_per_day": float(siem_gb_per_day),
        "siem_retention_days": int(siem_retention_days),
        "siem_tier": siem_tier,
        "target_scope": int(target_scope),
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    """Produce the BOM lines for this pillar.

    Input contract:
      inputs["enabled"]           list of SECURITY_COMPONENTS keys
      inputs["manual_counts"]     dict keyed by defender_plan or component key
      inputs["siem_gb_per_day"]   float (>0 triggers Sentinel ingestion path)
      inputs["siem_retention_days"] int
      inputs["siem_tier"]         key of SIEM_COMMITMENT_TIERS
      inputs["target_scope"]      int (# resources when running standalone)
      inputs.get("vm_count")      int (inherited from lift-shift pillar if active)
      inputs.get("la_gb")         float (inherited from lift-shift pillar if active)
    """
    lines: List[BomLine] = []
    vm_count = int(inputs.get("vm_count") or 0) or int(inputs.get("target_scope", 0))
    la_gb_from_ls = float(inputs.get("la_gb") or 0.0)

    # SIEM flow (Sentinel + LA commitment + retention + Defender servers if VMs exist)
    gb_per_day = float(inputs.get("siem_gb_per_day") or 0.0)
    if gb_per_day > 0:
        lines.extend(
            build_siem_bom(
                client=client, region=region,
                gb_per_day=gb_per_day,
                retention_days_over_free=int(inputs.get("siem_retention_days", 0)),
                commitment_tier=inputs.get("siem_tier", "payg"),
                defender_vm_count=vm_count,
                include_cspm=("cspm" in inputs.get("enabled", [])),
                app_name=app_name,
            )
        )

    # General security components — Sentinel uses la_gb from lift-shift if SIEM flow wasn't invoked
    la_gb_for_sentinel = round(gb_per_day * 30, 2) if gb_per_day > 0 else la_gb_from_ls
    lines.extend(
        build_security_bom(
            client=client, region=region,
            enabled_keys=inputs.get("enabled", []),
            vm_count=vm_count, la_gb=la_gb_for_sentinel,
            manual_counts=inputs.get("manual_counts", {}),
            app_name=app_name,
        )
    )

    # De-dupe: if SIEM flow produced a Sentinel line and the component 'sentinel'
    # is also enabled, strip the duplicate.
    seen_meters = set()
    deduped: List[BomLine] = []
    for l in lines:
        key = (l.category, l.resource, l.meter)
        if key in seen_meters:
            continue
        seen_meters.add(key)
        deduped.append(l)
    return deduped, []
