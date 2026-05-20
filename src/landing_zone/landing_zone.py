"""Azure Cloud Adoption Framework (CAF) landing-zone catalogue, priced
via the Retail Prices API.

Components are tagged with a `category` that mirrors CAF's platform-
subscription split: **Connectivity** (hub networking, edge), **Identity**
(Entra Domain Services, B2C), **Management & Observability** (Log
Analytics, Monitor, Automation, Backup, Site Recovery, Network Watcher),
**Security** (Key Vault; Defender + Sentinel live in the `azure_security`
pillar and are not duplicated here), and **Shared Platform** (Container
Registry, DNS resolver).

Three presets (`LZ_PRESETS`) give the reviewer a one-click "CAF
Foundation / Standard / Enterprise" scaffolding — they pre-tick a
matching subset of component keys. The UI still renders every checkbox
so individual toggles stack on top of the preset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from ..models import BomLine
from ..pricing.retail import RetailPricesClient, PriceRecord, HOURS_PER_MONTH
from ..pricing.lz_static import LZ_STATIC_RATES, REFERENCE_DATE as _LZ_STATIC_DATE


@dataclass
class LzComponent:
    key: str
    category: str
    resource: str
    default_enabled: bool
    quantity: float                  # in "unit"
    unit: str                        # "hours", "GB", "million ops", etc.
    build_filter: Callable[[str], str]
    # Optional post-filter to pick the right record from multiple returned
    pick: Optional[Callable[[List[PriceRecord]], Optional[PriceRecord]]] = None
    notes: str = ""
    # When True, this line is auto-added by the bill builder whenever its
    # parent component is selected AND the relevant quantity input > 0.
    # Hidden from the LZ checkbox grid so users don't see "two checkboxes
    # for the same service". App Gateway v2 CU hours, Azure Firewall data
    # processed, NAT Gateway data processed all bill alongside their parent
    # — exposing them as separate ticks is confusing.
    derived: bool = False


def _cheapest(records: List[PriceRecord]) -> Optional[PriceRecord]:
    """Cheapest record by retail_price, but prefer non-zero — a $0 record
    is almost always a free-tier meter (e.g. 'Free Data Analysis',
    'Basic Data Transfer Out — 5GB Free') and silently kills the BOM
    line. Only fall through to $0 records when EVERYTHING is $0."""
    if not records:
        return None
    non_zero = [r for r in records if r.retail_price > 0]
    if non_zero:
        return min(non_zero, key=lambda r: r.retail_price)
    return min(records, key=lambda r: r.retail_price)


def _contains(meter_substr: str):
    def picker(records: List[PriceRecord]) -> Optional[PriceRecord]:
        s = meter_substr.lower()
        filtered = [r for r in records if s in r.meter_name.lower()]
        return _cheapest(filtered) or _cheapest(records)
    return picker


def _contains_all(*substrs: str):
    def picker(records: List[PriceRecord]) -> Optional[PriceRecord]:
        subs = [s.lower() for s in substrs]
        filtered = [
            r for r in records
            if all(s in r.meter_name.lower() for s in subs)
        ]
        return _cheapest(filtered) or _cheapest(records)
    return picker


LANDING_ZONE_COMPONENTS: List[LzComponent] = [
    LzComponent(
        key="public_ip",
        category="Connectivity",
        resource="Public IP Address (Standard, static)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Virtual Network' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=lambda records: _cheapest([
            r for r in records
            if "standard" in (r.meter_name + r.sku_name).lower()
            and "static" in (r.meter_name + r.sku_name).lower()
            and "ip" in r.meter_name.lower()
        ]) or _cheapest(records),
        notes="Standard static Public IP for ingress / NAT gateway. Quantity = 1 IP × 730h.",
    ),
    LzComponent(
        key="expressroute_circuit",
        category="Connectivity",
        resource="ExpressRoute circuit (1 Gbps, metered)",
        default_enabled=False,
        quantity=1.0,
        unit="circuit/month",
        # ExpressRoute circuits carry armRegionName='Global' in the retail feed,
        # so we deliberately omit the region filter.
        build_filter=lambda region: (
            "serviceName eq 'ExpressRoute' and priceType eq 'Consumption'"
        ),
        pick=_contains_all("1 gbps", "circuit"),
        notes=(
            "Private connectivity from on-prem to Azure. Default tier: 1 Gbps, "
            "metered. For unlimited data plans edit the meter filter. "
            "Outbound data transfer and ISP cross-connect costs are billed separately."
        ),
    ),
    LzComponent(
        key="nat_gateway",
        category="Connectivity",
        resource="NAT Gateway (Standard)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'NAT Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("standard"),
        notes=(
            "Per-gateway hourly charge for outbound SNAT. "
            "Data processing is billed separately by Azure (~$0.045/GB) — "
            "currently modelled as the per-hour fixture only."
        ),
    ),
    LzComponent(
        key="expressroute_gateway",
        category="Connectivity",
        resource="ExpressRoute Gateway (ErGw1AZ, zone-redundant)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'VPN Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("ErGw1AZ"),
        notes=(
            "ExpressRoute Gateway attached to the hub VNet. ErGw1AZ is the "
            "zone-redundant entry SKU (~1 Gbps). Use ErGw2AZ / ErGw3AZ for "
            "higher throughput (~2 / ~10 Gbps)."
        ),
    ),
    LzComponent(
        key="bandwidth_egress",
        category="Connectivity",
        resource="Bandwidth egress (outbound, standard tier)",
        default_enabled=True,
        quantity=200.0,
        unit="GB",
        # Bandwidth is zone-priced in the retail feed; armRegionName is
        # typically empty/"Global", so we deliberately don't filter by region.
        build_filter=lambda region: (
            "serviceName eq 'Bandwidth' and priceType eq 'Consumption'"
        ),
        pick=lambda records: (
            _cheapest([
                r for r in records
                if "data transfer out" in r.meter_name.lower()
                and "standard" in (r.meter_name + r.product_name).lower()
                and "free" not in r.meter_name.lower()
            ])
            or _cheapest([
                r for r in records
                if "data transfer out" in r.meter_name.lower()
                and "free" not in r.meter_name.lower()
            ])
            or _cheapest(records)
        ),
        notes=(
            "Outbound data transfer (egress). First 100 GB/month are free per "
            "subscription — set the quantity to the expected monthly egress "
            "ABOVE the free tier. Typical for small/mid workloads: 100-500 GB/mo."
        ),
    ),
    LzComponent(
        key="app_gateway_waf",
        category="Connectivity",
        resource="Application Gateway WAF v2 (base + CU)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Application Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains_all("waf v2", "gateway"),
        notes=(
            "Azure App Gateway v2 (WAF). Layer-7 reverse proxy + OWASP rules. "
            "Azure bills this on TWO meters: base gateway-hours + Capacity-Unit "
            "hours. Ticking this enables both; CU count comes from the "
            "Quantities input below (default 2 CU)."
        ),
    ),
    LzComponent(
        key="firewall",
        category="Connectivity",
        resource="Azure Firewall (Standard)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Firewall' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Standard Deployment"),
        notes="1 hub firewall (Standard tier). Premium adds IDPS/TLS — ~2x cost.",
    ),
    LzComponent(
        key="bastion",
        category="Connectivity",
        resource="Azure Bastion (Basic)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Bastion' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        # "Basic" alone matches both 'Basic Deployment' (hourly, $0.19/hr)
        # and 'Basic Data Transfer Out — 5GB Free' ($0). We want the
        # hourly deployment meter, so match on both substrings.
        pick=_contains_all("basic", "deployment"),
        notes="Basic tier, 1 instance. Add data out separately if needed.",
    ),
    LzComponent(
        key="vpn_gw",
        category="Connectivity",
        resource="VPN Gateway (VpnGw1)",
        default_enabled=True,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'VPN Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("VpnGw1"),
        notes="Single VpnGw1 in hub. Zone-redundant SKU costs ~15% more.",
    ),
    LzComponent(
        key="log_analytics",
        category="Management & Observability",
        resource="Log Analytics (PAYG ingestion)",
        default_enabled=True,
        quantity=50.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Log Analytics' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Pay-as-you-go Data Ingestion"),
        notes="Default 50 GB/mo ingestion. Increase for production workloads.",
    ),
    LzComponent(
        key="key_vault",
        category="Security",
        resource="Key Vault (Standard, 10k ops)",
        default_enabled=True,
        quantity=1.0,  # 1 × 10K ops/month — realistic baseline
        unit="10K ops",
        build_filter=lambda region: (
            f"serviceName eq 'Key Vault' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("Operations"),
        notes="Standard vault, approx 10k ops/month. HSM-backed keys are separate.",
    ),
    LzComponent(
        key="recovery_vault",
        category="Management & Observability",
        resource="Azure Backup storage (LRS/GRS)",
        default_enabled=False,
        quantity=50.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Backup' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        # Pick the cheapest "data stored" meter — matches the GB quantity
        pick=_contains("data stored"),
        notes=(
            "Per-GB backup storage. Default is LRS/GRS retail — tune the "
            "quantity via the Backup % slider. For the per-VM protected "
            "instance fee, tick 'Azure Backup — protected instances' below."
        ),
    ),
    LzComponent(
        key="recovery_vault_instances",
        category="Management & Observability",
        resource="Azure Backup — protected instances",
        default_enabled=False,
        quantity=1.0,
        unit="instance/month",
        build_filter=lambda region: (
            f"serviceName eq 'Backup' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("protected instances"),
        notes=(
            "Flat per-VM fee (~$5/VM up to 50 GB, ~$10 up to 500 GB, then "
            "additional blocks). Quantity = # VMs protected. Auto-populated "
            "from the lift-shift inventory when both pillars are active."
        ),
    ),
    LzComponent(
        key="app_gateway_waf_cu",
        category="Connectivity",
        resource="Application Gateway WAF v2 — Capacity Units",
        default_enabled=False,
        quantity=2.0 * HOURS_PER_MONTH,   # 2 CU × 730 h default
        unit="CU-hours",
        build_filter=lambda region: (
            f"serviceName eq 'Application Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains_all("waf v2", "capacity unit"),
        notes=(
            "Auto-billed alongside `app_gateway_waf` — Azure pricing model "
            "couples base gateway-hours with CU-hours; you can't deploy v2 "
            "without CU. CU count is the 'App Gateway WAF v2 — Capacity Units' "
            "input under Quantities."
        ),
        # Hidden from the LZ checkbox grid — added automatically by the
        # bill builder when `app_gateway_waf` is on AND CU > 0.
        derived=True,
    ),
    LzComponent(
        key="firewall_data",
        category="Connectivity",
        resource="Azure Firewall — data processed",
        default_enabled=False,
        quantity=0.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Firewall' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains_all("standard", "data processed"),
        notes=(
            "Auto-billed alongside `firewall` / `firewall_premium`. Per-GB "
            "processed charge on top of the firewall deployment hour. "
            "GB count comes from the 'Azure Firewall — data processed' "
            "input under Quantities."
        ),
        derived=True,
    ),
    # -----------------------------------------------------------------------
    # CAF additions — Connectivity (edge + DDoS + private networking)
    # -----------------------------------------------------------------------
    LzComponent(
        key="ddos_ip_protection",
        category="Security",
        resource="DDoS Protection — IP Protection (per public IP)",
        default_enabled=False,
        quantity=1.0,
        unit="IP/month",
        build_filter=lambda region: (
            "serviceName eq 'DDoS Protection' and priceType eq 'Consumption'"
        ),
        pick=_contains("ip protection"),
        notes=(
            "Per-public-IP DDoS mitigation. Cheaper entry tier than the "
            "Network Protection plan. Quantity = number of protected public IPs."
        ),
    ),
    LzComponent(
        key="ddos_network_protection",
        category="Security",
        resource="DDoS Protection — Network Protection (subscription plan)",
        default_enabled=False,
        quantity=1.0,
        unit="plan/month",
        build_filter=lambda region: (
            "serviceName eq 'DDoS Protection' and priceType eq 'Consumption'"
        ),
        pick=_contains("network protection"),
        notes=(
            "Flat per-subscription DDoS plan covering up to 100 public IPs. "
            "Required for enterprise-scale LZ hubs with internet exposure."
        ),
    ),
    LzComponent(
        key="front_door_standard",
        category="Connectivity",
        resource="Azure Front Door — Standard (base fee)",
        default_enabled=False,
        quantity=1.0,
        unit="profile/month",
        build_filter=lambda region: (
            "serviceName eq 'Azure Front Door' and priceType eq 'Consumption'"
        ),
        pick=_contains_all("standard", "base"),
        notes="Edge CDN + WAF. Request + egress charges are extra.",
    ),
    LzComponent(
        key="front_door_premium",
        category="Connectivity",
        resource="Azure Front Door — Premium (base fee)",
        default_enabled=False,
        quantity=1.0,
        unit="profile/month",
        build_filter=lambda region: (
            "serviceName eq 'Azure Front Door' and priceType eq 'Consumption'"
        ),
        pick=_contains_all("premium", "base"),
        notes="Adds managed WAF + Private Link origin + bot protection.",
    ),
    LzComponent(
        key="private_dns_resolver",
        category="Connectivity",
        resource="Private DNS Resolver (endpoint hours)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,   # 1 endpoint × 730 h; UI scales per endpoint count
        unit="endpoint-hours",
        build_filter=lambda region: (
            f"serviceName eq 'Azure DNS' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("resolver"),
        notes=(
            "Hub DNS resolver for hybrid name-resolution. Typical deployment "
            "= 2 endpoints (1 inbound + 1 outbound) × 730 h."
        ),
    ),
    LzComponent(
        key="private_endpoint",
        category="Connectivity",
        resource="Private Endpoint (hourly × count)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,   # 1 endpoint × 730 h; UI scales via count input
        unit="endpoint-hours",
        build_filter=lambda region: (
            f"serviceName eq 'Virtual Network' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("private endpoint"),
        notes=(
            "Per-endpoint hourly charge for Private Link ingress. Data "
            "processed is billed separately. Quantity auto-scales with the "
            "'Private Endpoint count' input."
        ),
    ),
    LzComponent(
        key="vnet_peering_egress",
        category="Connectivity",
        resource="VNet peering — outbound data (GB)",
        default_enabled=False,
        quantity=0.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Virtual Network' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("peering"),
        notes=(
            "Per-GB charge on inter-VNet peered traffic. Usually negligible "
            "unless hub-spoke egress is high; tune on the LZ sliders."
        ),
    ),
    LzComponent(
        key="nat_gateway_data",
        category="Connectivity",
        resource="NAT Gateway — data processed (GB)",
        default_enabled=False,
        quantity=0.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'NAT Gateway' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("data processed"),
        notes=(
            "Auto-billed alongside `nat_gateway`. ~$0.045/GB processed on top "
            "of the NAT Gateway hourly charge. GB count comes from the "
            "'NAT Gateway data processed' input under Quantities."
        ),
        derived=True,
    ),
    LzComponent(
        key="firewall_premium",
        category="Connectivity",
        resource="Azure Firewall (Premium — IDPS/TLS)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Firewall' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains_all("premium", "deployment"),
        notes=(
            "Premium tier adds IDPS + TLS inspection + URL filtering (~2× "
            "Standard). Tick this OR `firewall` — not both."
        ),
    ),
    # -----------------------------------------------------------------------
    # CAF additions — Management & Observability
    # -----------------------------------------------------------------------
    LzComponent(
        key="automation_account",
        category="Management & Observability",
        resource="Automation Account (process automation)",
        default_enabled=False,
        quantity=0.0,    # users provide expected runbook minutes/month
        unit="minutes",
        build_filter=lambda region: (
            f"serviceName eq 'Automation' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("runbook"),
        notes=(
            "Per-minute runbook execution charge. First 500 min/month free. "
            "Typical hub runs 2-5k min/month for patch + compliance jobs."
        ),
    ),
    LzComponent(
        key="site_recovery",
        category="Management & Observability",
        resource="Azure Site Recovery (protected instances)",
        default_enabled=False,
        quantity=0.0,    # auto-populated from lift-shift vm_count
        unit="instance/month",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Site Recovery' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("protected instance"),
        notes=(
            "Per-VM ASR replication fee (~$25/VM/mo). Storage + egress are "
            "billed separately. Quantity auto-populates from the lift-shift "
            "inventory when that pillar is active."
        ),
    ),
    LzComponent(
        key="app_insights",
        category="Management & Observability",
        resource="Application Insights (data ingested)",
        default_enabled=False,
        quantity=10.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Application Insights' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("data"),
        notes=(
            "App telemetry ingestion. First 5 GB/mo free per workspace. "
            "Default 10 GB/mo for a small app; scale with active-user count."
        ),
    ),
    LzComponent(
        key="network_watcher_flow_logs",
        category="Management & Observability",
        resource="Network Watcher — NSG flow logs",
        default_enabled=False,
        quantity=10.0,
        unit="GB",
        build_filter=lambda region: (
            f"serviceName eq 'Network Watcher' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("flow log"),
        notes=(
            "Per-GB flow-log collection. Traffic Analytics processing is "
            "additional (via Log Analytics). 10 GB default = small env."
        ),
    ),
    # -----------------------------------------------------------------------
    # CAF additions — Identity
    # -----------------------------------------------------------------------
    LzComponent(
        key="entra_domain_services",
        category="Identity",
        resource="Microsoft Entra Domain Services (Standard)",
        default_enabled=False,
        quantity=HOURS_PER_MONTH,
        unit="hours",
        build_filter=lambda region: (
            f"serviceName eq 'Azure Active Directory Domain Services' "
            f"and armRegionName eq '{region}' and priceType eq 'Consumption'"
        ),
        pick=_contains("standard"),
        notes=(
            "Managed domain for legacy workloads that need Kerberos / NTLM / "
            "LDAP. Standard SKU; Enterprise/Premium are step-ups."
        ),
    ),
    # -----------------------------------------------------------------------
    # CAF additions — Shared Platform
    # -----------------------------------------------------------------------
    LzComponent(
        key="acr_standard",
        category="Shared Platform",
        resource="Azure Container Registry — Standard",
        default_enabled=False,
        quantity=1.0,
        unit="registry/month",
        build_filter=lambda region: (
            f"serviceName eq 'Container Registry' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("standard"),
        notes="Typical dev/test registry; 100 GB included storage.",
    ),
    LzComponent(
        key="acr_premium",
        category="Shared Platform",
        resource="Azure Container Registry — Premium (geo-replication)",
        default_enabled=False,
        quantity=1.0,
        unit="registry/month",
        build_filter=lambda region: (
            f"serviceName eq 'Container Registry' and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        ),
        pick=_contains("premium"),
        notes=(
            "Required for geo-replication, content trust, private link. "
            "Geo-replica regions are additional line items (not modelled here)."
        ),
    ),
]


# ---------------------------------------------------------------------------
# CAF presets — one-click "Basic / Foundation / Standard / Enterprise"
# scaffolding. Each preset is a superset of the previous one (except Basic,
# which is a minimal web-only stack: public IP + App Gateway). Users can
# still toggle individual checkboxes on top of a preset — the UI seeds
# session state from the preset, then renders the widgets so manual
# overrides win.
# ---------------------------------------------------------------------------

# Minimal "single web app" landing zone: ingress IP + App Gateway (WAF v2)
# + VPN Gateway for hybrid connectivity from on-prem. No firewall, no
# bastion, no Log Analytics. Useful for a single-app cost line-up that
# still needs site-to-site VPN back to corporate. CU billing for the App
# Gateway is auto-attached by the bill builder (it's a `derived` line).
_LZ_BASIC = {
    "public_ip", "app_gateway_waf", "vpn_gw",
}

_LZ_FOUNDATION = {
    "public_ip", "firewall", "bastion", "nat_gateway",
    "log_analytics", "key_vault", "recovery_vault",
    "recovery_vault_instances", "bandwidth_egress",
    "vpn_gw",  # mandatory in every preset — hybrid connectivity assumed
}

_LZ_STANDARD = _LZ_FOUNDATION | {
    "private_endpoint", "front_door_standard", "ddos_ip_protection",
    "app_insights", "automation_account", "network_watcher_flow_logs",
    "site_recovery", "acr_standard",
}

_LZ_ENTERPRISE = _LZ_STANDARD | {
    "ddos_network_protection", "front_door_premium", "firewall_premium",
    "private_dns_resolver", "vnet_peering_egress",
    "expressroute_circuit", "expressroute_gateway",
    "entra_domain_services", "acr_premium",
    "app_gateway_waf", "vpn_gw",
    # `app_gateway_waf_cu`, `firewall_data`, `nat_gateway_data` are derived
    # and auto-attached by the builder when their parent is on AND the
    # relevant Quantities input is > 0. Don't list them here.
}

LZ_PRESETS: dict = {
    "None":        set(),
    "Basic":       set(_LZ_BASIC),
    "Foundation":  set(_LZ_FOUNDATION),
    "Standard":    set(_LZ_STANDARD),
    "Enterprise":  set(_LZ_ENTERPRISE),
}


# Display order for category-grouped UI rendering
LZ_CATEGORY_ORDER: List[str] = [
    "Connectivity",
    "Identity",
    "Management & Observability",
    "Security",
    "Shared Platform",
]


def build_landing_zone_bom(
    client: RetailPricesClient,
    region: str,
    enabled_keys: List[str],
    quantity_overrides: Optional[dict] = None,
    sku_overrides: Optional[dict] = None,
) -> List[BomLine]:
    """Build LZ BOM. `sku_overrides` lets callers swap the meter pick
    for SKU-driven components — currently used by `vpn_gw` (VpnGw1 /
    VpnGw1AZ / VpnGw2AZ / VpnGw3AZ / etc.) but generalisable. Keyed by
    `comp.key`; value is the SKU string."""
    overrides = quantity_overrides or {}
    skus = sku_overrides or {}
    lines: List[BomLine] = []
    for comp in LANDING_ZONE_COMPONENTS:
        if comp.key not in enabled_keys:
            continue
        qty = float(overrides.get(comp.key, comp.quantity))
        # Quantity = 0 → user explicitly opted out via a slider (e.g. set
        # firewall_gb_processed = 0 or private_endpoint_count = 0). Don't
        # emit a $0 line for it.
        if qty <= 0:
            continue
        # SKU override path: for components where the user (or preset)
        # picks a specific SKU tier, replace the component's default
        # picker with one filtered by the chosen SKU label. Currently
        # only `vpn_gw` uses this — the SKU comes from
        # `sku_overrides["vpn_gw"]` and defaults to "VpnGw1AZ".
        _sku_pick = skus.get(comp.key)
        if comp.key == "vpn_gw" and _sku_pick:
            _sku_lower = _sku_pick.lower()
            local_pick = lambda recs: _cheapest([
                r for r in recs if _sku_lower in r.meter_name.lower()
            ]) or None
            records = client.query(comp.build_filter(region), max_pages=5)
            chosen = local_pick(records)
        else:
            records = client.query(comp.build_filter(region), max_pages=5)
            chosen = (comp.pick(records) if comp.pick else _cheapest(records))
        # Layer-2 defense: if the picker landed on a $0 record (free-tier
        # meter that snuck through), treat it as "not found" so the static
        # fallback below kicks in. A real BOM line should never be $0.
        if chosen is not None and chosen.retail_price <= 0:
            chosen = None
        if not chosen:
            # Retail feed (and any regional fallback) returned nothing — fall
            # back to the dated static reference table so the BOM still shows
            # a non-zero monthly cost. The Assumption column flags the
            # substitution so reviewers can verify against the live MS
            # pricing page.
            static = LZ_STATIC_RATES.get(comp.key)
            # SKU-aware static fallback for `vpn_gw` — the default
            # `vpn_gw` entry in LZ_STATIC_RATES is VpnGw1; swap to the
            # SKU the user / preset chose.
            if comp.key == "vpn_gw" and _sku_pick:
                from ..pricing.lz_static import vpn_sku_to_static_rate
                static = vpn_sku_to_static_rate(_sku_pick)
            if static:
                monthly = static.per_unit_per_month_usd * qty
                lines.append(
                    BomLine(
                        category=comp.category,
                        resource=f"{comp.resource} (static reference)",
                        sku=comp.key,
                        meter=f"Static reference ({_LZ_STATIC_DATE})",
                        region=region,
                        quantity=qty,
                        unit=comp.unit,
                        unit_price=static.per_unit_per_month_usd,
                        monthly_cost=round(monthly, 2),
                        currency="USD",
                        source="static-reference",
                        service_name=static.label,
                        assumption=(
                            f"Static reference pricing ({_LZ_STATIC_DATE}); "
                            f"retail meter not available for `{region}`. "
                            f"${static.per_unit_per_month_usd:.4f} × "
                            f"{qty:,.2f} {static.unit} = ${monthly:,.2f}/mo. "
                            f"{static.note}"
                        ),
                    )
                )
                continue
            # No static reference either — emit a clearly-labelled zero so
            # the row at least appears (only resources that have NO public
            # rate should land here).
            lines.append(
                BomLine(
                    category=comp.category,
                    resource=comp.resource,
                    sku="N/A",
                    meter="(price not found)",
                    region=region,
                    quantity=qty,
                    unit=comp.unit,
                    unit_price=0.0,
                    monthly_cost=0.0,
                    source="retail-prices-miss",
                )
            )
            continue
        # Tiered bandwidth egress (MS tier table) — use the tiered helper
        # instead of flat-rate × GB. The retail feed returns the tier-1 price;
        # compute_tiered_egress extrapolates the rest of the curve.
        if comp.key == "bandwidth_egress":
            from ..pricing.bandwidth import compute_tiered_egress
            tiered_total, weighted_rate, _bd = compute_tiered_egress(
                total_gb_month=qty,
                first_tier_rate=chosen.retail_price,
            )
            lines.append(
                BomLine(
                    category=comp.category,
                    resource=(
                        f"{comp.resource} (~{qty:,.0f} GB/mo, tiered — "
                        f"eff. ${weighted_rate:.4f}/GB)"
                    ),
                    sku=chosen.sku_name or chosen.product_name,
                    meter=chosen.meter_name + " (tier-weighted)",
                    region=region,
                    quantity=qty,
                    unit=comp.unit,
                    unit_price=weighted_rate,
                    monthly_cost=round(tiered_total, 2),
                    currency=chosen.currency_code,
                    source="retail-prices",
                    product_id=chosen.product_id,
                    sku_id=chosen.sku_id,
                    meter_id=chosen.meter_id,
                )
            )
            continue

        monthly = chosen.retail_price * qty
        # Rewrite the resource label to reflect the ACTUAL quantity when the
        # caller overrode the default (backup %, LA MB/day/VM, bandwidth GB).
        resource_label = comp.resource
        if comp.key in ("log_analytics", "recovery_vault", "bandwidth_egress"):
            resource_label = f"{comp.resource} (~{qty:,.1f} {comp.unit}/mo)"
        lines.append(
            BomLine(
                category=comp.category,
                resource=resource_label,
                sku=chosen.sku_name or chosen.product_name,
                meter=chosen.meter_name,
                region=region,
                quantity=qty,
                unit=comp.unit,
                unit_price=chosen.retail_price,
                monthly_cost=round(monthly, 2),
                currency=chosen.currency_code,
                source="retail-prices",
                product_id=chosen.product_id,
                sku_id=chosen.sku_id,
                meter_id=chosen.meter_id,
            )
        )
    return lines
