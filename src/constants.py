"""Shared constants (Azure region list, etc.)."""

from typing import Optional

# Subset of commonly used Azure commercial regions; add more as needed.
AZURE_REGIONS = [
    # Americas
    "eastus", "eastus2", "westus", "westus2", "westus3", "centralus",
    "southcentralus", "northcentralus", "westcentralus",
    "canadacentral", "canadaeast", "brazilsouth", "mexicocentral",
    "chilecentral",
    # Europe
    "northeurope", "westeurope", "uksouth", "ukwest",
    "francecentral", "francesouth",
    "germanywestcentral", "germanynorth",
    "switzerlandnorth", "switzerlandwest",
    "norwayeast", "norwaywest",
    "swedencentral", "swedensouth",
    "polandcentral", "italynorth", "spaincentral",
    # Middle East & Africa
    "uaenorth", "uaecentral",
    "qatarcentral", "israelcentral",
    "southafricanorth", "southafricawest",
    # Asia-Pacific — include announced regions; retail API returns no prices
    # for regions not yet live and the UI surfaces that gracefully.
    "southeastasia", "eastasia",
    "japaneast", "japanwest",
    "australiaeast", "australiasoutheast", "australiacentral", "australiacentral2",
    "koreacentral", "koreasouth",
    "centralindia", "southindia", "westindia",
    "jioindiacentral", "jioindiawest",
    "malaysiawest",
    "indonesiacentral",
    "newzealandnorth",
    "taiwannorth",
]

CURRENCIES = ["USD", "EUR", "GBP", "AUD", "JPY", "CAD", "BRL", "INR", "SGD"]

DEFAULT_REGION = "malaysiawest"
DEFAULT_CURRENCY = "USD"


# ---------------------------------------------------------------------------
# Compute mode — global policy that biases SKU / tier selection across every
# pillar that picks compute resources (VM sizer, App Service, AKS, Azure SQL
# DB, Postgres / MySQL Flexible, Cosmos, Redis, etc.).
# ---------------------------------------------------------------------------
# Rationale (per Microsoft Learn pricing + best-practice docs):
#
# - 💰 **saving** — bias toward Burstable / Basic / Serverless tiers that
#   trade peak performance for ~30-60% lower steady-state cost.
#   Suitable for non-prod (UAT, dev, test, staging, SIT, QA, sandbox,
#   training), low-utilisation prod, and anything that can tolerate
#   credit-burst throttling (B-series VM CPU credits; SQL serverless
#   auto-pause; App Service Basic/F1).
#     • VMs: Burstable B-series (B1s..B20ms) — credits when idle, burst
#       when busy. Cap 20 vCPU / 80 GB / 4 GB per vCPU.
#     • App Service: B1 / B2 / B3 (Basic). Free F1 for dev only.
#     • AKS: D-series workers, no uptime SLA, single-zone.
#     • Azure SQL DB: General Purpose Serverless (auto-pause on idle).
#     • Postgres/MySQL Flexible: Burstable tier (B1ms / B2s).
#     • Cosmos DB: Serverless (pay-per-RU, no capacity reservation).
#     • Redis: Basic C0..C2 (no replica).
#
# - ⚖ **normal** — current default; production-grade general-purpose tiers
#   without paying for advanced HA / IOPS / memory bandwidth.
#     • VMs: Dsv5 (general purpose) + Esv5 when memory-heavy (ratio ≥ 6).
#     • App Service: P0v3 / P1v3 (Premium v3).
#     • AKS: D-series + uptime SLA.
#     • Azure SQL DB: General Purpose 2-4 vCore provisioned.
#     • Postgres/MySQL Flexible: General Purpose D-series.
#     • Cosmos DB: Provisioned throughput autoscale.
#     • Redis: Standard C2..C4 (with replica).
#
# - 🚀 **high_perf** — performance-first; bias toward Memory-optimised /
#   Compute-optimised / Premium / Business-Critical tiers. Suitable for
#   sustained-high-CPU workloads, latency-sensitive databases, OLAP, ML
#   training/inference at scale.
#     • VMs: Esv5 (memory) / Fsv2 (compute) — auto-simulate may upgrade
#       to ND/NC GPU series when doc mentions ML/training/inference.
#     • App Service: P2v3 / P3v3 / P1mv3+ (Premium memory-optimised).
#       Isolated v2 / ASEv3 for regulated workloads.
#     • AKS: D/E + GPU node pools + zone-redundant + uptime SLA.
#     • Azure SQL DB: Business Critical / Hyperscale 8+ vCore.
#     • Postgres/MySQL Flexible: Memory Optimised E-series + HA.
#     • Cosmos DB: Provisioned throughput + multi-region writes.
#     • Redis: Premium P1+ or Enterprise E10+ (geo-replication).
#
# References:
#   https://learn.microsoft.com/en-us/azure/virtual-machines/sizes
#   https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans
#   https://learn.microsoft.com/en-us/azure/azure-sql/database/service-tiers-sql-database-vcore
#   https://learn.microsoft.com/en-us/azure/cosmos-db/throughput-serverless
#   https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-compute
#
COMPUTE_MODES: dict = {
    "saving": {
        "label": "💰 Saving — Burstable / Basic / Serverless (~30-60% off)",
        "short": "saving",
        "vm_family_pref": ("burstable", "general", "memory"),
        "app_service_tier_pref": ("B1", "B2", "B3"),
        "sql_db_tier_pref": "gp_serverless",
        "pg_mysql_tier_pref": "burstable",
        "cosmos_tier_pref": "serverless",
        "redis_tier_pref": "basic",
        "aks_uptime_sla": False,
    },
    "normal": {
        "label": "⚖ Normal — Production-grade general purpose (default)",
        "short": "normal",
        "vm_family_pref": ("general", "memory", "burstable"),
        "app_service_tier_pref": ("P0v3", "P1v3"),
        "sql_db_tier_pref": "gp_provisioned",
        "pg_mysql_tier_pref": "general_purpose",
        "cosmos_tier_pref": "provisioned_autoscale",
        "redis_tier_pref": "standard",
        "aks_uptime_sla": True,
    },
    "high_perf": {
        "label": "🚀 High Performance — Premium / Memory-Optimised / GPU-ready",
        "short": "high_perf",
        "vm_family_pref": ("memory", "compute_optimized", "general"),
        "app_service_tier_pref": ("P2v3", "P3v3", "P1mv3"),
        "sql_db_tier_pref": "business_critical",
        "pg_mysql_tier_pref": "memory_optimized",
        "cosmos_tier_pref": "provisioned_multiregion",
        "redis_tier_pref": "premium",
        "aks_uptime_sla": True,
    },
}
DEFAULT_COMPUTE_MODE = "normal"


# When a service has no retail price in the primary region, query these
# fallback regions in order. Used by RetailPricesClient.query().
REGION_FALLBACKS: dict = {
    "malaysiawest": ["southeastasia"],
    # Other newly-announced regions can map here as they come up.
}


# Human-readable labels for the region selectbox. The flat ARM codes
# (e.g. `malaysiawest`) are still the source-of-truth keys passed to the
# Retail Prices API — these labels just make the dropdown scannable.
# Format: "<geo prefix> <city/country>" so users can search-as-you-type
# either way ("malaysia" → matches; "🌏" → groups APAC entries).
REGION_LABELS: dict = {
    # Americas (🌎)
    "eastus":             "🌎 Americas — East US (Virginia)",
    "eastus2":            "🌎 Americas — East US 2 (Virginia)",
    "westus":             "🌎 Americas — West US (California)",
    "westus2":            "🌎 Americas — West US 2 (Washington)",
    "westus3":            "🌎 Americas — West US 3 (Arizona)",
    "centralus":          "🌎 Americas — Central US (Iowa)",
    "southcentralus":     "🌎 Americas — South Central US (Texas)",
    "northcentralus":     "🌎 Americas — North Central US (Illinois)",
    "westcentralus":      "🌎 Americas — West Central US (Wyoming)",
    "canadacentral":      "🌎 Americas — Canada Central (Toronto)",
    "canadaeast":         "🌎 Americas — Canada East (Quebec)",
    "brazilsouth":        "🌎 Americas — Brazil South (São Paulo)",
    "mexicocentral":      "🌎 Americas — Mexico Central",
    "chilecentral":       "🌎 Americas — Chile Central",

    # Europe (🇪🇺)
    "northeurope":        "🇪🇺 Europe — North Europe (Ireland)",
    "westeurope":         "🇪🇺 Europe — West Europe (Netherlands)",
    "uksouth":            "🇪🇺 Europe — UK South (London)",
    "ukwest":             "🇪🇺 Europe — UK West (Cardiff)",
    "francecentral":      "🇪🇺 Europe — France Central (Paris)",
    "francesouth":        "🇪🇺 Europe — France South (Marseille)",
    "germanywestcentral": "🇪🇺 Europe — Germany West Central (Frankfurt)",
    "germanynorth":       "🇪🇺 Europe — Germany North (Berlin)",
    "switzerlandnorth":   "🇪🇺 Europe — Switzerland North (Zurich)",
    "switzerlandwest":    "🇪🇺 Europe — Switzerland West (Geneva)",
    "norwayeast":         "🇪🇺 Europe — Norway East (Oslo)",
    "norwaywest":         "🇪🇺 Europe — Norway West (Stavanger)",
    "swedencentral":      "🇪🇺 Europe — Sweden Central (Gävle)",
    "swedensouth":        "🇪🇺 Europe — Sweden South (Malmö)",
    "polandcentral":      "🇪🇺 Europe — Poland Central (Warsaw)",
    "italynorth":         "🇪🇺 Europe — Italy North (Milan)",
    "spaincentral":       "🇪🇺 Europe — Spain Central (Madrid)",

    # Middle East & Africa (🌍)
    "uaenorth":           "🌍 MEA — UAE North (Dubai)",
    "uaecentral":         "🌍 MEA — UAE Central (Abu Dhabi)",
    "qatarcentral":       "🌍 MEA — Qatar Central (Doha)",
    "israelcentral":      "🌍 MEA — Israel Central",
    "southafricanorth":   "🌍 MEA — South Africa North (Johannesburg)",
    "southafricawest":    "🌍 MEA — South Africa West (Cape Town)",

    # Asia-Pacific (🌏)
    "southeastasia":      "🌏 APAC — Southeast Asia (Singapore)",
    "eastasia":           "🌏 APAC — East Asia (Hong Kong)",
    "japaneast":          "🌏 APAC — Japan East (Tokyo)",
    "japanwest":          "🌏 APAC — Japan West (Osaka)",
    "australiaeast":      "🌏 APAC — Australia East (NSW)",
    "australiasoutheast": "🌏 APAC — Australia Southeast (Victoria)",
    "australiacentral":   "🌏 APAC — Australia Central (Canberra)",
    "australiacentral2":  "🌏 APAC — Australia Central 2 (Canberra)",
    "koreacentral":       "🌏 APAC — Korea Central (Seoul)",
    "koreasouth":         "🌏 APAC — Korea South (Busan)",
    "centralindia":       "🌏 APAC — Central India (Pune)",
    "southindia":         "🌏 APAC — South India (Chennai)",
    "westindia":          "🌏 APAC — West India (Mumbai)",
    "jioindiacentral":    "🌏 APAC — Jio India Central",
    "jioindiawest":       "🌏 APAC — Jio India West",
    "malaysiawest":       "🌏 APAC — Malaysia West (Kuala Lumpur)",
    "indonesiacentral":   "🌏 APAC — Indonesia Central (Jakarta)",
    "newzealandnorth":    "🌏 APAC — New Zealand North (Auckland)",
    "taiwannorth":        "🌏 APAC — Taiwan North (Taipei)",
}


def region_label(arm_code: str) -> str:
    """Return the human-readable label for a region; falls back to the raw
    ARM code if we don't have a label mapping (e.g. a freshly announced
    region that hasn't been added to REGION_LABELS yet)."""
    return REGION_LABELS.get(arm_code, arm_code)


# ---------------------------------------------------------------------------
# Azure paired regions for BCDR cross-region replication
# ---------------------------------------------------------------------------
# Microsoft designates "regional pairs" so that platform updates roll out
# sequentially (never both at once) and so that services like geo-replicated
# storage / Recovery Services / Azure SQL geo-replication can use the pair
# without the customer choosing manually. Source:
#   https://learn.microsoft.com/en-us/azure/reliability/cross-region-replication-azure
#   https://learn.microsoft.com/en-us/azure/reliability/regions-paired
#
# Newer "Azure Regions Phase 2" regions (Malaysia West, Mexico Central,
# Indonesia Central, Taiwan North, Italy North, Spain Central, etc.) do
# NOT have an official fixed pair — Microsoft instead recommends choosing
# a same-geography region manually. For those we list the most sensible
# in-geo recommendation (matches Microsoft's published guidance where
# available) and flag the suggestion as `recommended` (no asymmetric
# automatic services like geo-redundant storage).
#
# Format: `primary → paired_secondary`. Bidirectional pairs appear twice.
# `None` means no recommended pair (Microsoft hasn't published one).
AZURE_PAIRED_REGIONS: dict = {
    # Americas — official pairs
    "eastus":             "westus",
    "westus":             "eastus",
    "eastus2":            "centralus",
    "centralus":          "eastus2",
    "southcentralus":     "northcentralus",
    "northcentralus":     "southcentralus",
    "westus2":            "westcentralus",
    "westcentralus":      "westus2",
    "westus3":            "eastus",          # Phase 2; recommended pair
    "canadacentral":      "canadaeast",
    "canadaeast":         "canadacentral",
    "brazilsouth":        "southcentralus",  # asymmetric (BR→SCUS only)
    "mexicocentral":      "southcentralus",  # Phase 2; recommended
    "chilecentral":       "brazilsouth",     # Phase 2; recommended

    # Europe — official pairs
    "northeurope":        "westeurope",
    "westeurope":         "northeurope",
    "uksouth":            "ukwest",
    "ukwest":             "uksouth",
    "francecentral":      "francesouth",
    "francesouth":        "francecentral",
    "germanywestcentral": "germanynorth",
    "germanynorth":       "germanywestcentral",
    "norwayeast":         "norwaywest",
    "norwaywest":         "norwayeast",
    "switzerlandnorth":   "switzerlandwest",
    "switzerlandwest":    "switzerlandnorth",
    "swedencentral":      "swedensouth",
    "swedensouth":        "swedencentral",
    # Phase 2 — no fixed Microsoft pair; in-geo recommendations:
    "polandcentral":      "swedencentral",
    "italynorth":         "westeurope",
    "spaincentral":       "francecentral",

    # Middle East & Africa
    "uaenorth":           "uaecentral",
    "uaecentral":         "uaenorth",
    "qatarcentral":       "uaenorth",        # Phase 2; recommended
    "israelcentral":      "italynorth",      # Phase 2; recommended in-geo
    "southafricanorth":   "southafricawest",
    "southafricawest":    "southafricanorth",

    # Asia-Pacific — official pairs
    "southeastasia":      "eastasia",
    "eastasia":           "southeastasia",
    "japaneast":          "japanwest",
    "japanwest":          "japaneast",
    "australiaeast":      "australiasoutheast",
    "australiasoutheast": "australiaeast",
    "australiacentral":   "australiacentral2",
    "australiacentral2":  "australiacentral",
    "koreacentral":       "koreasouth",
    "koreasouth":         "koreacentral",
    "centralindia":       "southindia",
    "southindia":         "centralindia",
    "westindia":          "southindia",      # asymmetric (W→S only)
    "jioindiacentral":    "jioindiawest",
    "jioindiawest":       "jioindiacentral",
    # Phase 2 APAC — recommended in-geo pairs:
    "malaysiawest":       "southeastasia",
    "indonesiacentral":   "southeastasia",
    "taiwannorth":        "eastasia",
    "newzealandnorth":    "australiaeast",
}


def paired_region(arm_code: str) -> Optional[str]:
    """Return the Azure-recommended DR pair for a region, or None if
    Microsoft hasn't published a pair AND we don't have a sensible
    in-geography recommendation either. Used by the lift-shift BCDR
    flow to auto-default the secondary-region picker so users don't
    have to look up the official pair manually."""
    return AZURE_PAIRED_REGIONS.get(arm_code)
