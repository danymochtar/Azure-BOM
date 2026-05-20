"""Infra & App Modernization pillar.

App Service, AKS node pools, Azure Container Registry + stubs for Container
Apps, API Management, Front Door, Service Bus, AND developer tooling
(GitHub Enterprise / Advanced Security / Copilot Business / Copilot
Enterprise, Visual Studio Pro / Enterprise subscriptions, Azure DevOps
Basic / Test Plans / Parallel Jobs).

DevTools use static reference pricing (src/pricing/devtools_static.py)
because the Azure Retail Prices API does not reliably cover user-
licensing SKUs. Each DevTools line stamps "Static reference pricing
(2026-04)" into its Assumption column so reviewers can verify.

Reuses `vm_price` for AKS so the Billing Term (PAYG / SP / RI) flows
through on node compute.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models import BomLine
from ..pricing.devtools_static import DEVTOOLS_RATES, REFERENCE_DATE as _STATIC_REF_DATE
from ..pricing.retail import HOURS_PER_MONTH, RetailPricesClient
from .azure_security import _pick


PILLAR_METADATA = {
    "key": "infra_modernization",
    "label": "Infra & App Modernization",
    "description": "App Service, AKS, ACR, Container Apps, APIM, Front Door, Service Bus, GitHub + Copilot, Visual Studio, Azure DevOps.",
    "icon": "⚙",
    "needs_vm_extraction": False,
    "default_strategy": None,
}


APP_SERVICE_SKUS: Dict[str, str] = {
    "P0v3": "Premium v3 P0v3 (1 vCPU, 4 GB)",
    "P1v3": "Premium v3 P1v3 (2 vCPU, 8 GB)",
    "P2v3": "Premium v3 P2v3 (4 vCPU, 16 GB)",
    "P3v3": "Premium v3 P3v3 (8 vCPU, 32 GB)",
    "P1mv3": "Premium mv3 P1mv3 (2 vCPU, 16 GB memory-opt)",
    "P2mv3": "Premium mv3 P2mv3 (4 vCPU, 32 GB memory-opt)",
}

AKS_NODE_SKUS: Dict[str, str] = {
    "Standard_D2s_v5":  "D2s v5  (2 vCPU, 8 GB)",
    "Standard_D4s_v5":  "D4s v5  (4 vCPU, 16 GB)",
    "Standard_D8s_v5":  "D8s v5  (8 vCPU, 32 GB)",
    "Standard_D16s_v5": "D16s v5 (16 vCPU, 64 GB)",
    "Standard_E4s_v5":  "E4s v5  (4 vCPU, 32 GB mem-opt)",
    "Standard_E8s_v5":  "E8s v5  (8 vCPU, 64 GB mem-opt)",
}

ACR_SKUS: Dict[str, str] = {
    "Basic":    "Basic",
    "Standard": "Standard",
    "Premium":  "Premium (geo-rep, private link, content trust)",
}

APIM_TIERS: Dict[str, str] = {
    "consumption": "Consumption (serverless)",
    "basic_v2":    "Basic v2",
    "standard_v2": "Standard v2",
    "premium":     "Premium",
}

FRONT_DOOR_TIERS: Dict[str, str] = {
    "standard": "Front Door Standard",
    "premium":  "Front Door Premium",
}

SERVICE_BUS_TIERS: Dict[str, Dict] = {
    "basic":    {"label": "Basic (queues only, 1 msg/op)",  "meter_hint": "basic"},
    "standard": {"label": "Standard (topics + sessions)",   "meter_hint": "standard"},
    "premium":  {"label": "Premium (dedicated MUs)",        "meter_hint": "premium"},
}


# ---------------------------------------------------------------------------
# Line builders
# ---------------------------------------------------------------------------

# Reserved-instance / Savings-plan discount approximations for non-VM
# Azure compute. The Retail Prices API does carry Reservation meters
# for these services but the meter naming + reservationTerm handling
# is inconsistent across regions — the defensible approach for an
# assessment-stage estimate is a documented discount multiplier
# applied to the Consumption rate. Numbers reflect Microsoft published
# discount ranges (Oct-2025):
#
# App Service Reserved (Premium v3 / Isolated v2):
#   RI 1Y ≈ 30% off PAYG | RI 3Y ≈ 55% off
#   SP 1Y ≈ 15% off       | SP 3Y ≈ 40% off
# https://azure.microsoft.com/en-us/pricing/details/app-service/windows/
_APP_SVC_BILLING_DISCOUNT = {
    "payg": 0.0,  "sp_1y": 0.15, "sp_3y": 0.40,
    "ri_1y": 0.30, "ri_3y": 0.55,
}
_APP_SVC_BILLING_TAG = {
    "payg": "", "sp_1y": " [SP 1Y]", "sp_3y": " [SP 3Y]",
    "ri_1y": " [RI 1Y]", "ri_3y": " [RI 3Y]",
}
_APP_SVC_BILLING_LABEL = {
    "payg": "PAYG", "sp_1y": "SP 1Y", "sp_3y": "SP 3Y",
    "ri_1y": "RI 1Y", "ri_3y": "RI 3Y",
}


def _app_service_line(
    client: RetailPricesClient, region: str,
    plan_sku: str, count: int, os_is_windows: bool, pricing_mode: str, app_name: str,
) -> Optional[BomLine]:
    if plan_sku == "none" or count <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'Azure App Service' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    # Filter to the requested SKU + OS
    os_hint = "windows" if os_is_windows else "linux"
    candidates = [
        r for r in recs
        if plan_sku.lower() in (r.sku_name + r.meter_name).lower()
        and os_hint in (r.product_name + r.sku_name).lower()
    ]
    chosen = min(candidates, key=lambda r: r.retail_price) if candidates else _pick(recs, plan_sku.lower())
    if not chosen:
        return None
    qty = count * HOURS_PER_MONTH
    # Apply Reserved / Savings Plan discount when applicable.
    discount = _APP_SVC_BILLING_DISCOUNT.get(pricing_mode, 0.0)
    effective_rate = chosen.retail_price * (1.0 - discount)
    term_tag = _APP_SVC_BILLING_TAG.get(pricing_mode, "")
    billing_lbl = _APP_SVC_BILLING_LABEL.get(pricing_mode, "PAYG")
    discount_note = (
        f"App Service Reserved discount: -{int(discount*100)}% off PAYG "
        f"(${chosen.retail_price:.4f}/hr → ${effective_rate:.4f}/hr). "
        f"Approximation per MS published RI/SP ranges; exact reservation "
        f"meter pricing varies by plan."
        if discount > 0 else ""
    )
    return BomLine(
        category="App Modernization",
        resource=f"Azure App Service {plan_sku} × {count} ({'Windows' if os_is_windows else 'Linux'}){term_tag}",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=qty, unit="hours",
        unit_price=effective_rate,
        monthly_cost=round(effective_rate * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices" + (" + ri-approx" if discount > 0 else ""),
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure App Service",
        custom_name=f"{app_name}-AppService-{plan_sku}" if app_name else f"AppService-{plan_sku}",
        billing_term=billing_lbl,
        assumption=discount_note,
    )


def _aks_node_lines(
    client: RetailPricesClient, region: str,
    pool_name: str, node_sku: str, node_count: int, os_is_windows: bool,
    uptime_sla: bool, pricing_mode: str, app_name: str, use_ahb: bool = False,
) -> List[BomLine]:
    if node_sku == "none" or node_count <= 0:
        return []
    # Node VMs priced via vm_price (honors RI/SP + AHB)
    price = client.vm_price(
        node_sku, region, os_is_windows=os_is_windows,
        pricing_mode=pricing_mode, use_ahb=use_ahb,
    )
    out: List[BomLine] = []
    qty = node_count * HOURS_PER_MONTH
    pool_label = pool_name or "default"
    if price:
        out.append(BomLine(
            category="App Modernization",
            resource=f"AKS node pool `{pool_label}` — {node_sku} × {node_count}",
            sku=node_sku, meter=price.meter_name,
            region=region, quantity=qty, unit="hours",
            unit_price=price.retail_price,
            monthly_cost=round(price.retail_price * qty, 2),
            currency=price.currency_code,
            source="retail-prices",
            product_id=price.product_id, sku_id=price.sku_id, meter_id=price.meter_id,
            service_name="Azure Kubernetes Service",
            custom_name=f"{app_name}-AKS-{pool_label}" if app_name else f"AKS-{pool_label}",
        ))
    else:
        out.append(BomLine(
            category="App Modernization",
            resource=f"AKS node pool `{pool_label}` — {node_sku} × {node_count}",
            sku=node_sku, meter="(price not found)",
            region=region, quantity=qty, unit="hours",
            unit_price=0.0, monthly_cost=0.0,
            source="retail-prices-miss",
            service_name="Azure Kubernetes Service",
            custom_name=f"{app_name}-AKS-{pool_label}" if app_name else f"AKS-{pool_label}",
        ))

    # Uptime SLA uplift (fixed per cluster)
    if uptime_sla:
        aks_recs = client.query(
            f"serviceName eq 'Azure Kubernetes Service' and armRegionName eq '{region}' and priceType eq 'Consumption'"
        )
        chosen = _pick(aks_recs, "uptime sla")
        if chosen:
            out.append(BomLine(
                category="App Modernization",
                resource="AKS Uptime SLA uplift",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=HOURS_PER_MONTH, unit="hours",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * HOURS_PER_MONTH, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Kubernetes Service",
                custom_name=f"{app_name}-AKS-UptimeSLA" if app_name else "AKS-UptimeSLA",
            ))
    return out


def _acr_line(
    client: RetailPricesClient, region: str,
    sku: str, geo_replica_count: int, app_name: str,
) -> Optional[BomLine]:
    if sku == "none":
        return None
    recs = client.query(
        f"serviceName eq 'Container Registry' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, sku.lower())
    if not chosen:
        return None
    # ACR pricing is per day for the registry; geo-replication = replica_count × day rate
    replicas = max(1, geo_replica_count) if sku == "Premium" else 1
    qty = 30.0 * replicas
    return BomLine(
        category="App Modernization",
        resource=f"Azure Container Registry {sku}" + (f" × {replicas} regions" if replicas > 1 else ""),
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=qty, unit="registry-day",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Container Registry",
        custom_name=f"{app_name}-ACR-{sku}" if app_name else f"ACR-{sku}",
    )


def _container_apps_lines(
    client: RetailPricesClient, region: str,
    vcpu_seconds: float, mem_gib_seconds: float, request_millions: float, app_name: str,
) -> List[BomLine]:
    out: List[BomLine] = []
    if vcpu_seconds <= 0 and mem_gib_seconds <= 0 and request_millions <= 0:
        return out
    recs = client.query(
        f"serviceName eq 'Container Apps' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    if vcpu_seconds > 0:
        chosen = _pick(recs, "vcpu")
        if chosen:
            out.append(BomLine(
                category="App Modernization",
                resource=f"Container Apps vCPU — {vcpu_seconds:,.0f} vCPU-s/mo",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=vcpu_seconds, unit="vCPU-second",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * vcpu_seconds, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Container Apps",
                custom_name=f"{app_name}-ACA-vCPU" if app_name else "ACA-vCPU",
            ))
    if mem_gib_seconds > 0:
        chosen = _pick(recs, "memory")
        if chosen:
            out.append(BomLine(
                category="App Modernization",
                resource=f"Container Apps memory — {mem_gib_seconds:,.0f} GiB-s/mo",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=mem_gib_seconds, unit="GiB-second",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * mem_gib_seconds, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Container Apps",
                custom_name=f"{app_name}-ACA-Mem" if app_name else "ACA-Mem",
            ))
    if request_millions > 0:
        chosen = _pick(recs, "request")
        if chosen:
            out.append(BomLine(
                category="App Modernization",
                resource=f"Container Apps requests — {request_millions:,.1f}M/mo",
                sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
                region=region, quantity=request_millions, unit="million requests",
                unit_price=chosen.retail_price,
                monthly_cost=round(chosen.retail_price * request_millions, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
                service_name="Azure Container Apps",
                custom_name=f"{app_name}-ACA-Requests" if app_name else "ACA-Requests",
            ))
    return out


def _apim_line(
    client: RetailPricesClient, region: str, tier: str, units: int, app_name: str,
) -> Optional[BomLine]:
    if tier == "none" or units <= 0:
        return None
    recs = client.query(
        f"serviceName eq 'API Management' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, tier.replace("_", " "))
    if not chosen:
        return None
    qty = units * HOURS_PER_MONTH
    return BomLine(
        category="App Modernization",
        resource=f"API Management {APIM_TIERS[tier]} × {units}",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=qty, unit="unit-hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="API Management",
        custom_name=f"{app_name}-APIM-{tier}" if app_name else f"APIM-{tier}",
    )


def _devtools_line(rate_key: str, quantity: int, app_name: str) -> Optional[BomLine]:
    """Static-reference DevTools line (GitHub, Copilot, VS, Azure DevOps).

    No retail API call — per-seat/per-job licensing isn't in the
    Consumption feed. The Assumption column cites the reference date so
    reviewers know to verify against the current MS pricing page.
    """
    if quantity <= 0:
        return None
    rate = DEVTOOLS_RATES.get(rate_key)
    if rate is None:
        return None
    monthly = rate.per_unit_per_month_usd * quantity
    return BomLine(
        category="App Modernization",
        resource=f"{rate.label} × {quantity} {rate.unit.replace('/month','')}s",
        sku=rate_key,
        meter=f"Static reference ({_STATIC_REF_DATE})",
        region="global",
        quantity=float(quantity),
        unit=rate.unit,
        unit_price=rate.per_unit_per_month_usd,
        monthly_cost=round(monthly, 2),
        currency="USD",
        source="static-reference",
        service_name=rate.label,
        custom_name=f"{app_name}-DevTools-{rate_key}" if app_name else f"DevTools-{rate_key}",
        assumption=(
            f"Static reference pricing ({_STATIC_REF_DATE}); "
            f"${rate.per_unit_per_month_usd:.2f} per {rate.unit} × {quantity} "
            f"= ${monthly:,.2f}/mo. {rate.note}"
        ),
    )


def _service_bus_line(
    client: RetailPricesClient, region: str, tier_key: str, units: int, app_name: str,
) -> Optional[BomLine]:
    if tier_key == "none" or units <= 0:
        return None
    cfg = SERVICE_BUS_TIERS.get(tier_key)
    if not cfg:
        return None
    recs = client.query(
        f"serviceName eq 'Service Bus' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, cfg["meter_hint"])
    if not chosen:
        return None
    # Premium is per MU-hour; Basic/Standard are per namespace-hour.
    qty = units * HOURS_PER_MONTH
    return BomLine(
        category="App Modernization",
        resource=f"Azure Service Bus {cfg['label']} × {units}",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=qty, unit="hours",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price * qty, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Service Bus",
        custom_name=f"{app_name}-ServiceBus-{tier_key}" if app_name else f"ServiceBus-{tier_key}",
    )


def _front_door_line(
    client: RetailPricesClient, region: str, tier: str, routes: int, app_name: str,
) -> Optional[BomLine]:
    if tier == "none":
        return None
    recs = client.query(
        f"serviceName eq 'Azure Front Door' and priceType eq 'Consumption'"
    )
    chosen = _pick(recs, tier)
    if not chosen:
        return None
    return BomLine(
        category="App Modernization",
        resource=f"Azure Front Door {tier.capitalize()} ({routes} routes)",
        sku=chosen.sku_name or chosen.product_name, meter=chosen.meter_name,
        region=region, quantity=1, unit="profile-month",
        unit_price=chosen.retail_price,
        monthly_cost=round(chosen.retail_price, 2),
        currency=chosen.currency_code,
        source="retail-prices",
        product_id=chosen.product_id, sku_id=chosen.sku_id, meter_id=chosen.meter_id,
        service_name="Azure Front Door",
        custom_name=f"{app_name}-FrontDoor-{tier}" if app_name else f"FrontDoor-{tier}",
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
    asvc_pref = prefs.get("app_service", {}) or {}
    aks_pref = prefs.get("aks", {}) or {}
    acr_pref = prefs.get("acr", {}) or {}
    aca_pref = prefs.get("container_apps", {}) or {}
    apim_pref = prefs.get("apim", {}) or {}
    fd_pref = prefs.get("front_door", {}) or {}

    with st.expander("Azure App Service (Premium v3)", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            _app_opts = ["none"] + list(APP_SERVICE_SKUS)
            appsvc_sku = st.selectbox(
                "Plan SKU", _app_opts, key="appsvc_sku",
                index=_sel_idx(_app_opts, asvc_pref.get("sku", "none")),
                format_func=lambda k: "None" if k == "none" else APP_SERVICE_SKUS[k],
            )
        with c2:
            appsvc_count = st.number_input(
                "Instance count", min_value=0,
                value=int(asvc_pref.get("count", 0)), step=1,
                disabled=(appsvc_sku == "none"), key="appsvc_count",
            )
        with c3:
            _os_opts = ["Linux", "Windows"]
            _os_saved = "Windows" if asvc_pref.get("os_windows") else "Linux"
            appsvc_os = st.selectbox(
                "OS", _os_opts, index=_sel_idx(_os_opts, _os_saved),
                disabled=(appsvc_sku == "none"), key="appsvc_os",
            )

    with st.expander("AKS node pool", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            aks_pool_name = st.text_input(
                "Pool name", value=str(aks_pref.get("pool_name", "default")),
                key="aks_pool_name",
            )
        with c2:
            _aks_opts = ["none"] + list(AKS_NODE_SKUS)
            aks_sku = st.selectbox(
                "Node VM SKU", _aks_opts, key="aks_sku",
                index=_sel_idx(_aks_opts, aks_pref.get("sku", "none")),
                format_func=lambda k: "None" if k == "none" else AKS_NODE_SKUS[k],
            )
        with c3:
            aks_count = st.number_input(
                "Node count", min_value=0, value=int(aks_pref.get("count", 0)),
                step=1, disabled=(aks_sku == "none"), key="aks_count",
            )
        c4, c5 = st.columns(2)
        with c4:
            _aks_os_saved = "Windows" if aks_pref.get("os_windows") else "Linux"
            aks_os = st.selectbox(
                "Node OS", ["Linux", "Windows"],
                index=_sel_idx(["Linux", "Windows"], _aks_os_saved),
                disabled=(aks_sku == "none"), key="aks_os",
            )
        with c5:
            aks_uptime = st.checkbox(
                "Uptime SLA", value=bool(aks_pref.get("uptime_sla", False)),
                disabled=(aks_sku == "none"), key="aks_uptime",
            )

    with st.expander("Azure Container Registry", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _acr_opts = ["none"] + list(ACR_SKUS)
            acr_sku = st.selectbox(
                "SKU", _acr_opts, key="acr_sku",
                index=_sel_idx(_acr_opts, acr_pref.get("sku", "none")),
                format_func=lambda k: "None" if k == "none" else ACR_SKUS[k],
            )
        with c2:
            acr_geo = st.number_input(
                "Geo-replica regions (Premium only)", min_value=1,
                value=int(acr_pref.get("geo_replica_count", 1)), step=1,
                disabled=(acr_sku != "Premium"), key="acr_geo",
            )

    with st.expander("Azure Container Apps", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            aca_vcpu = st.number_input(
                "vCPU-seconds / month", min_value=0.0,
                value=float(aca_pref.get("vcpu_seconds", 0.0)), step=100_000.0,
                key="aca_vcpu",
            )
        with c2:
            aca_mem = st.number_input(
                "Memory GiB-seconds / month", min_value=0.0,
                value=float(aca_pref.get("mem_gib_seconds", 0.0)), step=100_000.0,
                key="aca_mem",
            )
        with c3:
            aca_req = st.number_input(
                "Requests (millions / month)", min_value=0.0,
                value=float(aca_pref.get("request_millions", 0.0)), step=1.0,
                key="aca_req",
            )

    with st.expander("API Management", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _apim_opts = ["none"] + list(APIM_TIERS)
            apim_tier = st.selectbox(
                "Tier", _apim_opts, key="apim_tier",
                index=_sel_idx(_apim_opts, apim_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else APIM_TIERS[k],
            )
        with c2:
            apim_units = st.number_input(
                "Units", min_value=0, value=int(apim_pref.get("units", 0)),
                step=1, disabled=(apim_tier == "none"), key="apim_units",
            )

    with st.expander("Azure Front Door", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _fd_opts = ["none"] + list(FRONT_DOOR_TIERS)
            fd_tier = st.selectbox(
                "Tier", _fd_opts, key="fd_tier",
                index=_sel_idx(_fd_opts, fd_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else FRONT_DOOR_TIERS[k],
            )
        with c2:
            fd_routes = st.number_input(
                "Routes (info)", min_value=0, value=int(fd_pref.get("routes", 0)),
                step=1, disabled=(fd_tier == "none"), key="fd_routes",
            )

    sb_pref = prefs.get("service_bus", {}) or {}
    with st.expander("Azure Service Bus", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _sb_opts = ["none"] + list(SERVICE_BUS_TIERS)
            sb_tier = st.selectbox(
                "Tier", _sb_opts, key="sb_tier",
                index=_sel_idx(_sb_opts, sb_pref.get("tier", "none")),
                format_func=lambda k: "None" if k == "none" else SERVICE_BUS_TIERS[k]["label"],
            )
        with c2:
            sb_units = st.number_input(
                "Namespaces / MUs", min_value=0, value=int(sb_pref.get("units", 0)),
                step=1, disabled=(sb_tier == "none"), key="sb_units",
                help="Basic/Standard: count of namespaces. Premium: messaging units (1/2/4).",
            )

    # --- Developer tooling (static reference pricing) ---
    devtools_pref = prefs.get("devtools", {}) or {}
    with st.expander("GitHub — Enterprise + Advanced Security + Copilot", expanded=False):
        st.caption(
            f"Static reference pricing ({_STATIC_REF_DATE}) — not from the "
            "Retail Prices API. Verify against https://github.com/pricing."
        )
        c1, c2 = st.columns(2)
        gh_ent = c1.number_input(
            "GitHub Enterprise Cloud — users", min_value=0,
            value=int(devtools_pref.get("github_enterprise", 0)),
            step=1, key="gh_ent_users",
        )
        gh_ghas = c2.number_input(
            "GitHub Advanced Security — active committers", min_value=0,
            value=int(devtools_pref.get("github_advanced_security", 0)),
            step=1, key="gh_ghas_users",
        )
        c3, c4 = st.columns(2)
        gh_copilot_biz = c3.number_input(
            "GitHub Copilot Business — users", min_value=0,
            value=int(devtools_pref.get("github_copilot_business", 0)),
            step=1, key="gh_copilot_biz",
        )
        gh_copilot_ent = c4.number_input(
            "GitHub Copilot Enterprise — users", min_value=0,
            value=int(devtools_pref.get("github_copilot_enterprise", 0)),
            step=1, key="gh_copilot_ent",
        )

    with st.expander("Visual Studio subscriptions", expanded=False):
        st.caption(
            f"Static reference pricing ({_STATIC_REF_DATE}). Monthly cloud-"
            "subscription rate; annual and standard-perpetual options exist "
            "at different rates."
        )
        c1, c2 = st.columns(2)
        vs_pro = c1.number_input(
            "Visual Studio Professional — users", min_value=0,
            value=int(devtools_pref.get("vs_professional", 0)),
            step=1, key="vs_pro_users",
        )
        vs_ent = c2.number_input(
            "Visual Studio Enterprise — users", min_value=0,
            value=int(devtools_pref.get("vs_enterprise", 0)),
            step=1, key="vs_ent_users",
        )

    with st.expander("Azure DevOps Services", expanded=False):
        st.caption(
            f"Static reference pricing ({_STATIC_REF_DATE}). First 5 users + "
            "all VS subscribers are free on Basic."
        )
        c1, c2 = st.columns(2)
        azdo_basic = c1.number_input(
            "Basic — users beyond free 5", min_value=0,
            value=int(devtools_pref.get("azdo_basic", 0)),
            step=1, key="azdo_basic_users",
        )
        azdo_test = c2.number_input(
            "Basic + Test Plans — users", min_value=0,
            value=int(devtools_pref.get("azdo_basic_test", 0)),
            step=1, key="azdo_test_users",
        )
        c3, c4 = st.columns(2)
        azdo_hosted = c3.number_input(
            "Hosted parallel jobs", min_value=0,
            value=int(devtools_pref.get("azdo_hosted_pipeline", 0)),
            step=1, key="azdo_hosted",
        )
        azdo_selfhosted = c4.number_input(
            "Self-hosted parallel jobs", min_value=0,
            value=int(devtools_pref.get("azdo_selfhosted_pipeline", 0)),
            step=1, key="azdo_selfhosted",
        )

    return {
        "app_service": {
            "sku": appsvc_sku, "count": int(appsvc_count),
            "os_windows": (appsvc_os == "Windows") if appsvc_sku != "none" else False,
        },
        "aks": {
            "pool_name": aks_pool_name, "sku": aks_sku, "count": int(aks_count),
            "os_windows": (aks_os == "Windows") if aks_sku != "none" else False,
            "uptime_sla": bool(aks_uptime),
        },
        "acr": {"sku": acr_sku, "geo_replica_count": int(acr_geo)},
        "container_apps": {
            "vcpu_seconds": float(aca_vcpu), "mem_gib_seconds": float(aca_mem),
            "request_millions": float(aca_req),
        },
        "apim": {"tier": apim_tier, "units": int(apim_units)},
        "front_door": {"tier": fd_tier, "routes": int(fd_routes)},
        "service_bus": {"tier": sb_tier, "units": int(sb_units)},
        "devtools": {
            "github_enterprise":        int(gh_ent),
            "github_advanced_security": int(gh_ghas),
            "github_copilot_business":  int(gh_copilot_biz),
            "github_copilot_enterprise": int(gh_copilot_ent),
            "vs_professional":          int(vs_pro),
            "vs_enterprise":            int(vs_ent),
            "azdo_basic":               int(azdo_basic),
            "azdo_basic_test":          int(azdo_test),
            "azdo_hosted_pipeline":     int(azdo_hosted),
            "azdo_selfhosted_pipeline": int(azdo_selfhosted),
        },
    }


def build_bom(client, region: str, inputs: dict, app_name: str, pricing_mode: str):
    lines: List[BomLine] = []

    asvc = inputs.get("app_service", {})
    l = _app_service_line(
        client, region,
        plan_sku=asvc.get("sku", "none"), count=asvc.get("count", 0),
        os_is_windows=asvc.get("os_windows", False),
        pricing_mode=pricing_mode, app_name=app_name,
    )
    if l: lines.append(l)

    aks = inputs.get("aks", {})
    lines.extend(_aks_node_lines(
        client, region,
        pool_name=aks.get("pool_name", "default"),
        node_sku=aks.get("sku", "none"), node_count=aks.get("count", 0),
        os_is_windows=aks.get("os_windows", False),
        uptime_sla=aks.get("uptime_sla", False),
        pricing_mode=pricing_mode, app_name=app_name,
        use_ahb=bool(inputs.get("__use_ahb__", False)),
    ))

    acr = inputs.get("acr", {})
    l = _acr_line(
        client, region,
        sku=acr.get("sku", "none"), geo_replica_count=acr.get("geo_replica_count", 1),
        app_name=app_name,
    )
    if l: lines.append(l)

    aca = inputs.get("container_apps", {})
    lines.extend(_container_apps_lines(
        client, region,
        vcpu_seconds=aca.get("vcpu_seconds", 0.0),
        mem_gib_seconds=aca.get("mem_gib_seconds", 0.0),
        request_millions=aca.get("request_millions", 0.0),
        app_name=app_name,
    ))

    apim = inputs.get("apim", {})
    l = _apim_line(
        client, region,
        tier=apim.get("tier", "none"), units=apim.get("units", 0), app_name=app_name,
    )
    if l: lines.append(l)

    fd = inputs.get("front_door", {})
    l = _front_door_line(
        client, region,
        tier=fd.get("tier", "none"), routes=fd.get("routes", 0), app_name=app_name,
    )
    if l: lines.append(l)

    sb = inputs.get("service_bus", {})
    l = _service_bus_line(
        client, region,
        tier_key=sb.get("tier", "none"), units=sb.get("units", 0), app_name=app_name,
    )
    if l: lines.append(l)

    # Static-reference DevTools lines (GitHub / Copilot / VS / Azure DevOps)
    dt = inputs.get("devtools", {}) or {}
    for rate_key in (
        "github_enterprise", "github_advanced_security",
        "github_copilot_business", "github_copilot_enterprise",
        "vs_professional", "vs_enterprise",
        "azdo_basic", "azdo_basic_test",
        "azdo_hosted_pipeline", "azdo_selfhosted_pipeline",
    ):
        qty = int(dt.get(rate_key, 0))
        if qty > 0:
            l = _devtools_line(rate_key, qty, app_name)
            if l: lines.append(l)

    return lines, []
