"""Static-reference pricing for Landing Zone components.

Used as a fallback when the Retail Prices API returns no records (or the
picker can't match a meter) for a given component. Without this table,
the BOM would emit `(price not found)` rows with $0 monthly — useless for
budget conversations. With it, every component (except resources that
genuinely have no charge) always lands with a non-zero cost line; the
Cost Assumptions sheet stamps the reference date so reviewers can verify
against the live Microsoft pricing page.

All rates captured 2026-04 from the Azure public pricing pages
(https://azure.microsoft.com/pricing/details/<service>/). Refresh by
re-reading the relevant pricing page when MS publishes a change.

The dict is keyed by `LzComponent.key`. Each entry uses the same
`StaticRate` dataclass as devtools_static.py so the export layer can
treat both with one code path.
"""
from __future__ import annotations

from .devtools_static import StaticRate, REFERENCE_DATE  # noqa: F401  (re-exported)


# ---------------------------------------------------------------------------
# Per-component reference rates.
#
# Rates are expressed in the unit the LzComponent advertises (per-hour,
# per-GB, per-instance, per-IP, etc.). For per-hour resources the LZ
# builder multiplies by 730 via `comp.quantity = HOURS_PER_MONTH`, so a
# per-hour rate here yields a correct monthly figure with no additional
# scaling.
# ---------------------------------------------------------------------------

LZ_STATIC_RATES: dict = {
    # ----- Connectivity -----
    "public_ip": StaticRate(
        label="Public IP Address (Standard, static)",
        unit="IP-hour",
        per_unit_per_month_usd=0.005,
        note="Standard static IP, allocated + associated.",
    ),
    "expressroute_circuit": StaticRate(
        label="ExpressRoute circuit (1 Gbps, metered)",
        unit="circuit/month",
        per_unit_per_month_usd=300.00,
        note="1 Gbps metered tier; unlimited data is a step-up.",
    ),
    "expressroute_gateway": StaticRate(
        label="ExpressRoute Gateway (ErGw1AZ)",
        unit="gateway-hour",
        per_unit_per_month_usd=0.51,
        note="ErGw1AZ zone-redundant entry SKU.",
    ),
    "nat_gateway": StaticRate(
        label="NAT Gateway (Standard)",
        unit="gateway-hour",
        per_unit_per_month_usd=0.045,
        note="Per-gateway hourly charge; data processed billed separately.",
    ),
    "nat_gateway_data": StaticRate(
        label="NAT Gateway data processed",
        unit="GB",
        per_unit_per_month_usd=0.045,
        note="Per-GB data processed via the gateway.",
    ),
    "bandwidth_egress": StaticRate(
        label="Bandwidth egress (tier 1 anchor)",
        unit="GB",
        per_unit_per_month_usd=0.087,
        note="Tier-1 outbound data rate; the tiered helper extrapolates the curve.",
    ),
    "vnet_peering_egress": StaticRate(
        label="VNet peering — outbound data",
        unit="GB",
        per_unit_per_month_usd=0.01,
        note="Intra-region VNet peering data transfer.",
    ),
    "firewall": StaticRate(
        label="Azure Firewall (Standard)",
        unit="firewall-hour",
        per_unit_per_month_usd=1.25,
        note="Standard tier deployment hour.",
    ),
    "firewall_premium": StaticRate(
        label="Azure Firewall (Premium — IDPS/TLS)",
        unit="firewall-hour",
        per_unit_per_month_usd=2.50,
        note="Premium tier deployment hour (~2x Standard).",
    ),
    "firewall_data": StaticRate(
        label="Azure Firewall data processed",
        unit="GB",
        per_unit_per_month_usd=0.016,
        note="Per-GB on top of the firewall deployment hour.",
    ),
    "bastion": StaticRate(
        label="Azure Bastion (Basic)",
        unit="host-hour",
        per_unit_per_month_usd=0.19,
        note="Basic tier; Standard / Premium step-ups.",
    ),
    # VPN Gateway — kept for back-compat; the SKU-aware lookup below is
    # what the builder uses when `vpn_gw_sku` is passed in
    # `sku_overrides`. Default landing rate is VpnGw1 (non-AZ) for the
    # "default install" line.
    "vpn_gw": StaticRate(
        label="VPN Gateway (VpnGw1)",
        unit="gateway-hour",
        per_unit_per_month_usd=0.190,
        note="VpnGw1 SKU; zone-redundant ~30% more.",
    ),
    "app_gateway_waf": StaticRate(
        label="Application Gateway WAF v2 (instance)",
        unit="gateway-hour",
        per_unit_per_month_usd=0.443,
        note="WAF v2 base instance hour.",
    ),
    "app_gateway_waf_cu": StaticRate(
        label="Application Gateway WAF v2 — Capacity Units",
        unit="CU-hour",
        per_unit_per_month_usd=0.0144,
        note="Per-CU hourly; scales with throughput.",
    ),
    "front_door_standard": StaticRate(
        label="Azure Front Door — Standard (base fee)",
        unit="profile/month",
        per_unit_per_month_usd=35.00,
        note="Standard profile base fee. Egress + requests are additional.",
    ),
    "front_door_premium": StaticRate(
        label="Azure Front Door — Premium (base fee)",
        unit="profile/month",
        per_unit_per_month_usd=330.00,
        note="Premium profile base fee. Adds managed WAF + Private Link origin.",
    ),
    "private_endpoint": StaticRate(
        label="Private Endpoint",
        unit="endpoint-hour",
        per_unit_per_month_usd=0.01,
        note="Per-endpoint hourly. Data processed billed separately.",
    ),
    "private_dns_resolver": StaticRate(
        label="Private DNS Resolver",
        unit="endpoint-hour",
        per_unit_per_month_usd=0.10,
        note="Per inbound/outbound endpoint hour. Queries billed separately.",
    ),

    # ----- Identity -----
    "entra_domain_services": StaticRate(
        label="Microsoft Entra Domain Services (Standard)",
        unit="forest-hour",
        per_unit_per_month_usd=0.20,
        note="Standard SKU; Enterprise/Premium are step-ups.",
    ),

    # ----- Management & Observability -----
    "log_analytics": StaticRate(
        label="Log Analytics (PAYG ingestion)",
        unit="GB",
        per_unit_per_month_usd=2.76,
        note="Pay-as-you-go ingestion. Commitment tiers from 100 GB/day cheaper.",
    ),
    "app_insights": StaticRate(
        label="Application Insights (data ingested)",
        unit="GB",
        per_unit_per_month_usd=2.76,
        note="Same workspace billing as Log Analytics ingestion.",
    ),
    "automation_account": StaticRate(
        label="Automation Account (process automation)",
        unit="minute",
        per_unit_per_month_usd=0.002,
        note="First 500 min/month free. Per-minute beyond.",
    ),
    "recovery_vault": StaticRate(
        label="Azure Backup storage (LRS)",
        unit="GB",
        per_unit_per_month_usd=0.10,
        note="LRS per-GB stored; GRS ~2x.",
    ),
    "recovery_vault_instances": StaticRate(
        label="Azure Backup — protected instances",
        unit="instance/month",
        per_unit_per_month_usd=5.00,
        note="Flat per-instance fee (up to 50 GB; higher tiers add).",
    ),
    "site_recovery": StaticRate(
        label="Azure Site Recovery — protected instances",
        unit="instance/month",
        per_unit_per_month_usd=25.00,
        note="Per-VM replication fee. Storage + egress are additional.",
    ),
    "network_watcher_flow_logs": StaticRate(
        label="NSG Flow Logs (collection)",
        unit="GB",
        per_unit_per_month_usd=0.50,
        note="Per-GB collected. Traffic Analytics processing extra (via LA).",
    ),

    # ----- Security -----
    "key_vault": StaticRate(
        label="Key Vault (Standard operations)",
        unit="10K ops",
        per_unit_per_month_usd=0.30,
        note="Standard vault operations; HSM keys priced separately.",
    ),
    "ddos_ip_protection": StaticRate(
        label="DDoS Protection — IP Protection",
        unit="IP/month",
        per_unit_per_month_usd=200.00,
        note="Per-public-IP DDoS mitigation.",
    ),
    "ddos_network_protection": StaticRate(
        label="DDoS Protection — Network Protection",
        unit="plan/month",
        per_unit_per_month_usd=2944.00,
        note="Flat per-subscription DDoS plan; covers up to 100 protected IPs.",
    ),

    # ----- Shared Platform -----
    "acr_standard": StaticRate(
        label="Azure Container Registry — Standard",
        unit="registry/month",
        per_unit_per_month_usd=20.00,
        note="Standard tier; 100 GB included storage.",
    ),
    "acr_premium": StaticRate(
        label="Azure Container Registry — Premium",
        unit="registry/month",
        per_unit_per_month_usd=50.00,
        note="Premium tier; geo-replicas are additional line items.",
    ),
}


# ---------------------------------------------------------------------------
# VPN Gateway — SKU ladder
# ---------------------------------------------------------------------------
# Generation-2 VPN Gateway SKUs by aggregate throughput / S2S tunnels / P2S
# connections. AZ variants are zone-redundant deployments (~30% premium).
# Static rates captured 2026-04 from
# https://azure.microsoft.com/en-us/pricing/details/vpn-gateway/ .
#
# Sizing guide (MS Learn):
#   Basic       100 Mbps  / 10 S2S   / 128 P2S    — legacy, no BGP/IKEv2
#   VpnGw1      650 Mbps  / 30 S2S   / 250 P2S    — entry production
#   VpnGw2      1   Gbps  / 30 S2S   / 500 P2S    — mid-tier
#   VpnGw3      1.25 Gbps / 30 S2S   / 1000 P2S   — large hub
#   VpnGw4      5   Gbps  / 100 S2S  / 5000 P2S   — enterprise
#   VpnGw5      10  Gbps  / 100 S2S  / 10000 P2S  — top SKU
VPN_GATEWAY_SKUS: dict = {
    "Basic":     {"label": "VPN Gateway Basic",        "rate_usd_hr": 0.036, "throughput": "100 Mbps",  "s2s": 10,  "p2s": 128,  "az": False, "note": "Legacy. No BGP, no IKEv2, no ER coexistence. Not for production."},
    "VpnGw1":    {"label": "VPN Gateway VpnGw1",       "rate_usd_hr": 0.190, "throughput": "650 Mbps",  "s2s": 30,  "p2s": 250,  "az": False, "note": "Entry production tier; BGP + IKEv2 + P2S OpenVPN."},
    "VpnGw1AZ":  {"label": "VPN Gateway VpnGw1AZ",     "rate_usd_hr": 0.250, "throughput": "650 Mbps",  "s2s": 30,  "p2s": 250,  "az": True,  "note": "VpnGw1 with zone redundancy. Production default."},
    "VpnGw2":    {"label": "VPN Gateway VpnGw2",       "rate_usd_hr": 0.500, "throughput": "1 Gbps",    "s2s": 30,  "p2s": 500,  "az": False, "note": "1 Gbps aggregate; mid-tier hub."},
    "VpnGw2AZ":  {"label": "VPN Gateway VpnGw2AZ",     "rate_usd_hr": 0.650, "throughput": "1 Gbps",    "s2s": 30,  "p2s": 500,  "az": True,  "note": "VpnGw2 with zone redundancy."},
    "VpnGw3":    {"label": "VPN Gateway VpnGw3",       "rate_usd_hr": 1.280, "throughput": "1.25 Gbps", "s2s": 30,  "p2s": 1000, "az": False, "note": "Large hub; co-existence with ExpressRoute."},
    "VpnGw3AZ":  {"label": "VPN Gateway VpnGw3AZ",     "rate_usd_hr": 1.665, "throughput": "1.25 Gbps", "s2s": 30,  "p2s": 1000, "az": True,  "note": "VpnGw3 with zone redundancy. Enterprise hub default."},
    "VpnGw4":    {"label": "VPN Gateway VpnGw4",       "rate_usd_hr": 3.370, "throughput": "5 Gbps",    "s2s": 100, "p2s": 5000, "az": False, "note": "5 Gbps, 100 S2S tunnels."},
    "VpnGw4AZ":  {"label": "VPN Gateway VpnGw4AZ",     "rate_usd_hr": 4.380, "throughput": "5 Gbps",    "s2s": 100, "p2s": 5000, "az": True,  "note": "VpnGw4 with zone redundancy."},
    "VpnGw5":    {"label": "VPN Gateway VpnGw5",       "rate_usd_hr": 5.760, "throughput": "10 Gbps",   "s2s": 100, "p2s": 10000,"az": False, "note": "Top SKU; 10 Gbps aggregate."},
    "VpnGw5AZ":  {"label": "VPN Gateway VpnGw5AZ",     "rate_usd_hr": 7.490, "throughput": "10 Gbps",   "s2s": 100, "p2s": 10000,"az": True,  "note": "VpnGw5 with zone redundancy."},
}


def vpn_sku_to_static_rate(sku: str) -> StaticRate:
    """Translate a VPN SKU string into a StaticRate row the builder can
    feed into the BOM. Falls back to VpnGw1 when the SKU is unknown."""
    spec = VPN_GATEWAY_SKUS.get(sku) or VPN_GATEWAY_SKUS["VpnGw1"]
    return StaticRate(
        label=spec["label"],
        unit="gateway-hour",
        per_unit_per_month_usd=spec["rate_usd_hr"],
        note=(
            f'{spec["throughput"]} aggregate; {spec["s2s"]} S2S tunnels; '
            f'{spec["p2s"]} P2S connections. {spec["note"]}'
        ),
    )


# Preset → recommended VPN SKU. CAF guidance + community sizing
# heuristics: small hubs land on VpnGw1AZ (zone-redundant, BGP);
# growing standard footprints upgrade to VpnGw2AZ as concurrent S2S
# tunnels + P2S users grow; enterprise multi-region designs need
# VpnGw3AZ for ExpressRoute coexistence on the same gateway.
PRESET_VPN_SKU: dict = {
    "Basic":      "VpnGw1",       # cheapest functional; no AZ
    "Foundation": "VpnGw1AZ",     # zone-redundant prod baseline
    "Standard":   "VpnGw2AZ",     # 1 Gbps, more S2S headroom
    "Enterprise": "VpnGw3AZ",     # 1.25 Gbps + ER coexistence
}


def recommend_vpn_sku(preset: str = "Foundation") -> str:
    return PRESET_VPN_SKU.get(preset, "VpnGw1AZ")
