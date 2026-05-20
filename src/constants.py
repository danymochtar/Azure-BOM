"""Shared constants (Azure region list, etc.)."""

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
