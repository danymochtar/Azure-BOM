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
    "vpn_gw": StaticRate(
        label="VPN Gateway (VpnGw1)",
        unit="gateway-hour",
        per_unit_per_month_usd=0.142,
        note="VpnGw1 SKU; zone-redundant ~15% more.",
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
